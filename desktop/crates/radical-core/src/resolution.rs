//! The expected temporal resolution of a calibration curve: how tightly a date
//! from a given calendar age can be known, before anyone measures anything. A
//! port of `uncertainty_at` and `profile` from
//! `backend/app/services/resolution.py`; the whole-curve cache stays with
//! whatever serves the profiles.
//!
//! The method is Svetlik et al. (2019), Radiocarbon 61(6):1729-1740, as
//! implemented by Demjan in res14c. For each radiocarbon age a sample of that
//! period could plausibly return:
//!
//! 1. scale the laboratory uncertainty with age, `u(a) = u0 * exp(a / 2*8033)`,
//!    because counting statistics degrade as there is less 14C left to count;
//! 2. calibrate that measurement against the curve;
//! 3. take the width of its 95.45% HPD envelope -- first to last point of the
//!    set, gaps included -- as the resolution that measurement achieved.
//!
//! Then for each calendar year, average those widths over the radiocarbon ages
//! a sample of that true age could actually yield, weighting each by how
//! likely it is to yield it. That gives a mean, and a 5th-95th percentile band
//! for how lucky or unlucky a single date can be.
//!
//! Written from the published method and this app's own calibration code, not
//! translated from res14c. It reproduces the method's OUTPUT: over 1,000-4,000
//! cal BP it agrees with res14c's published figures to about 0.3%, including
//! the sharp 33-year trough at 2,750 cal BP that a subtly wrong implementation
//! would smear away.
//!
//! Affordable at request time despite the obvious formulation being a dense
//! 50,000 x 55,000 problem: every calendar age more than 5 sigma from a
//! measurement contributes nothing and is skipped -- found by a banded search
//! rather than by testing them all (see `reach_bands`) -- and the calculation
//! runs on a uniform 1-year grid, where `calibrate`'s trapezoidal weights are
//! constant and so cannot change which points the HPD selects.

use crate::numeric::{
    arange, argsort_quicksort, argsort_stable, cumsum, interp, interp_one, linspace,
    searchsorted_left, searchsorted_right, sum,
};
use crate::{CalibrationCurve, Error, SIGMA_2};

/// Twice the mean life of 14C, in years -- the constant in the uncertainty
/// scaling law. Libby's 5568-year half-life gives a mean life of 8033 years,
/// the convention conventional radiocarbon ages are reported on.
pub const MEAN_LIFE_DOUBLED: f64 = 2.0 * 8033.0;

/// The laboratory uncertainty assumed when none is given: 25 years on a
/// modern sample, what a commercial date typically carries.
///
/// The reference implementation defaults to 15, about the best a good AMS lab
/// offers, which is right for reproducing its published figures and wrong for
/// "can I date this period well enough" -- it would quote a precision most
/// samples will not get. 15 stays one click away.
pub const DEFAULT_U0: f64 = 25.0;

/// 2 sigma, the level every other HPD region here is reported at, shared by
/// construction rather than by a matching literal.
pub const CONFIDENCE: f64 = SIGMA_2;

/// Beyond this many combined sigma a measurement contributes nothing to a
/// calendar age, and masking it out is what makes this computable in a
/// request.
pub const SIGMA_REACH: f64 = 5.0;

/// Calendar-year step of the internal grid. One year: the curves are given at
/// one-year resolution over the Holocene, and the whole quantity being
/// measured is a width in years.
pub const GRID_STEP: f64 = 1.0;

/// Expected resolution across a span of calendar time. Where no measurement
/// reaches a reported year its statistics are NaN.
#[derive(Debug, Clone, PartialEq)]
pub struct ResolutionProfile {
    /// cal BP, ascending.
    pub cal_ages: Vec<f64>,
    /// Mean 95.45% envelope width, years.
    pub mean: Vec<f64>,
    /// 5th percentile (an unusually lucky date).
    pub p05: Vec<f64>,
    /// 95th percentile (an unusually unlucky one).
    pub p95: Vec<f64>,
    /// Laboratory sigma used, years.
    pub uncertainty: Vec<f64>,
    pub u0: f64,
}

/// The laboratory uncertainty expected for a measurement of this age.
pub fn uncertainty_at(c14_age: f64, u0: f64) -> f64 {
    u0 * (c14_age / MEAN_LIFE_DOUBLED).exp()
}

/// How far outside the requested window the calculation has to reach.
///
/// A measurement whose calendar distribution runs past the edge of the window
/// would have its HPD envelope clipped there, and would report a resolution
/// better than it really achieves -- the error being worst exactly where
/// resolution is worst, since those are the distributions that spread
/// furthest. Reaching [`SIGMA_REACH`] combined sigma beyond the window covers
/// every measurement that could contribute, converted to calendar years at
/// the roughly one-to-one rate the curve runs at. The floor keeps a narrow
/// window on a flat stretch honest, where the local rate is far below one.
///
/// That one-to-one conversion is the approximation. A curve running shallower
/// than one spreads a distribution further in calendar years than in
/// radiocarbon years, by roughly the inverse of its slope. On the three
/// bundled curves the floor absorbs this -- a windowed profile matches a
/// whole-curve one to about one part in a billion -- but a curve holding a
/// shallow constant slope for well over a thousand years reaches one part in
/// 10^5 at a window's outermost point (measured in Python's
/// test_resolution.py, on a synthetic curve built to provoke it). Worth
/// knowing before trusting this on a curve unlike the real ones.
fn padding(curve_sigma_max: f64, u_max: f64) -> f64 {
    let reach = SIGMA_REACH * curve_sigma_max.hypot(u_max);
    // Python's max(500.0, reach): the first unless the second is larger.
    if reach > 500.0 { reach } else { 500.0 }
}

// --------------------------------------------------------------------------
// Finding the stretches of curve a measurement can have come from
// --------------------------------------------------------------------------
//
// The obvious loop tests every calendar year against every radiocarbon age,
// which is 50,000 x 55,000 comparisons for a whole curve. A measurement only
// reaches SIGMA_REACH combined sigma, so the candidates are the grid points
// whose curve value is within that of it -- a contiguous slice of the grid
// sorted by radiocarbon age.
//
// Sorted in BANDS of curve error rather than as one list, because the reach
// depends on that error and a single global maximum is set by the
// late-glacial end of the curve, where it is fifty times the Holocene value.
// One list would hand every Holocene measurement thousands of candidates it
// must then reject; banded, it gets hundreds. The bands only have to bracket
// the reach -- the exact test still runs on what they return -- so their
// edges are arbitrary.
const BAND_EDGES: [f64; 5] = [20.0, 40.0, 80.0, 160.0, 320.0];

struct Band {
    /// Grid indices, sorted by radiocarbon age.
    member: Vec<usize>,
    /// Their radiocarbon ages, ascending.
    c14: Vec<f64>,
    sigma_max: f64,
}

/// Grid indices grouped by curve error, each sorted by radiocarbon age.
fn reach_bands(c14: &[f64], sigma: &[f64]) -> Vec<Band> {
    let mut bands = Vec::new();
    let mut lower = f64::NEG_INFINITY;
    for upper in BAND_EDGES.into_iter().chain([f64::INFINITY]) {
        let member: Vec<usize> = (0..sigma.len())
            .filter(|&i| sigma[i] > lower && sigma[i] <= upper)
            .collect();
        lower = upper;
        if member.is_empty() {
            continue;
        }
        let band_c14: Vec<f64> = member.iter().map(|&i| c14[i]).collect();
        let order = argsort_stable(&band_c14);
        let member: Vec<usize> = order.iter().map(|&k| member[k]).collect();
        let sigma_max = member
            .iter()
            .map(|&i| sigma[i])
            .fold(f64::NEG_INFINITY, f64::max);
        bands.push(Band {
            c14: member.iter().map(|&i| c14[i]).collect(),
            member,
            sigma_max,
        });
    }
    bands
}

/// Grid points a measurement of `age` could plausibly have come from.
///
/// A superset of the answer -- each band is taken at its own worst error --
/// returned in ascending grid order, since the HPD construction downstream
/// reads the calendar ages as a sorted series.
fn candidates(bands: &[Band], age: f64, u: f64) -> Vec<usize> {
    let mut found = Vec::new();
    for band in bands {
        let reach = SIGMA_REACH * band.sigma_max.hypot(u);
        let first = searchsorted_left(&band.c14, age - reach);
        let last = searchsorted_right(&band.c14, age + reach);
        if last > first {
            found.extend_from_slice(&band.member[first..last]);
        }
    }
    found.sort_unstable();
    found
}

/// How far the 95.45% HPD set spans, gaps included.
///
/// `hpd_ranges_from_distribution`'s selection without its ranges: this runs
/// once per radiocarbon age -- tens of thousands of times for one profile --
/// and everything that function builds between the first and last point is
/// thrown away here. The selection is identical, normalising before
/// accumulating as it does, so the width is too.
///
/// Python sorts here with NumPy's default, unstable sort, and on a one-year
/// grid through a curve given in whole years there are exact ties wherever
/// two years share a radiocarbon age and error. Which of two tied years the
/// set takes at its boundary can move its far end by decades, so the sort is
/// NumPy's own (see `argsort_quicksort`).
fn hpd_width(ages: &[f64], weights: &[f64], total: f64) -> f64 {
    let normalised: Vec<f64> = weights.iter().map(|w| w / total).collect();
    let negated: Vec<f64> = normalised.iter().map(|w| -w).collect();
    let order = argsort_quicksort(&negated);
    let cumulative = cumsum(&order.iter().map(|&i| normalised[i]).collect::<Vec<_>>());
    // The first point at which the level is met; if it never is -- which a
    // few zero weights can cause -- the whole distribution is the set.
    let reached = searchsorted_left(&cumulative, CONFIDENCE);
    let selected = if reached < cumulative.len() {
        &order[..=reached]
    } else {
        &order[..]
    };
    let (lo, hi) = selected
        .iter()
        .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), &i| {
            (lo.min(ages[i]), hi.max(ages[i]))
        });
    hi - lo
}

/// Expected resolution across a window of calendar time.
///
/// - `cal_from`, `cal_to`: the window in cal BP; the whole curve if omitted.
/// - `u0`: laboratory uncertainty at 0 BP, scaled with age from there
///   ([`DEFAULT_U0`] is the app's default).
/// - `stride`: report every `stride` calendar years. The calculation itself is
///   always at one-year resolution; this only thins what comes back.
pub fn profile(
    curve: &CalibrationCurve,
    cal_from: Option<f64>,
    cal_to: Option<f64>,
    u0: f64,
    stride: usize,
) -> Result<ResolutionProfile, Error> {
    if curve.is_empty() {
        return Err(Error::EmptyCurve);
    }
    if u0 <= 0.0 {
        return Err(Error::NonPositiveUncertainty);
    }
    if stride == 0 {
        return Err(Error::ZeroStride);
    }
    let (cal_ages, c14_ages, c14_errors) = (curve.cal_ages(), curve.c14_ages(), curve.c14_errors());

    let curve_min = cal_ages.iter().copied().fold(f64::INFINITY, f64::min);
    let curve_max = cal_ages.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    // Python's max(x, curve_min) and min(x, curve_max): the first argument
    // unless the second beats it.
    let mut lo = cal_from.map_or(curve_min, |f| if curve_min > f { curve_min } else { f });
    let mut hi = cal_to.map_or(curve_max, |t| if curve_max < t { curve_max } else { t });
    if hi < lo {
        (lo, hi) = (hi, lo);
    }
    if hi - lo < GRID_STEP {
        hi = (lo + GRID_STEP).min(curve_max);
    }

    // How far the working grid has to reach beyond what is reported.
    let reported_c14_max = interp_one(hi, cal_ages, c14_ages);
    let u_max = uncertainty_at(reported_c14_max, u0);
    let sigma_max = interp(&linspace(lo, hi, 64), cal_ages, c14_errors)
        .into_iter()
        .fold(f64::NEG_INFINITY, f64::max);
    let pad = padding(sigma_max, u_max);

    let grid_lo = if lo - pad > curve_min {
        lo - pad
    } else {
        curve_min
    };
    let grid_hi = if hi + pad < curve_max {
        hi + pad
    } else {
        curve_max
    };
    let cal = arange(grid_lo, grid_hi + GRID_STEP, GRID_STEP);
    let c14 = interp(&cal, cal_ages, c14_ages);
    let sigma = interp(&cal, cal_ages, c14_errors);

    // The calendar ages actually reported.
    let in_window: Vec<usize> = (0..cal.len())
        .filter(|&i| cal[i] >= lo && cal[i] <= hi)
        .collect();
    let out_index: Vec<usize> = in_window.iter().copied().step_by(stride).collect();
    if out_index.is_empty() {
        return Err(Error::EmptyWindow);
    }
    let out_cal: Vec<f64> = out_index.iter().map(|&i| cal[i]).collect();

    // Every radiocarbon age a sample from this window could plausibly give.
    let window_c14 = in_window.iter().map(|&i| c14[i]);
    let window_min = window_c14.clone().fold(f64::INFINITY, f64::min);
    let window_max = window_c14.fold(f64::NEG_INFINITY, f64::max);
    let u_window_max = uncertainty_at(window_max, u0);
    let reach = SIGMA_REACH * sigma_max.hypot(u_window_max);
    let first_age = (window_min - reach).floor();
    let ages = arange(
        if first_age > 0.0 { first_age } else { 0.0 },
        (window_max + reach).ceil() + 1.0,
        1.0,
    );

    // Sparse: for each (measurement, reported year) pair that matters, the
    // width that measurement achieved and how likely the year is to have
    // produced it. Dense would be a 50,000 x 11,000 matrix, almost all zero.
    let mut year_id: Vec<usize> = Vec::new();
    let mut weight: Vec<f64> = Vec::new();
    let mut width: Vec<f64> = Vec::new();

    // Which reported year each grid point is, if any. A measurement's
    // contribution is then gathered straight from its own points.
    let mut slot: Vec<Option<usize>> = vec![None; cal.len()];
    for (k, &i) in out_index.iter().enumerate() {
        slot[i] = Some(k);
    }

    let bands = reach_bands(&c14, &sigma);
    let sigma_sq: Vec<f64> = sigma.iter().map(|s| s * s).collect();

    for &age in &ages {
        let u = u0 * (age / MEAN_LIFE_DOUBLED).exp();
        let candidate = candidates(&bands, age, u);
        if candidate.is_empty() {
            continue;
        }
        let mut near = Vec::with_capacity(candidate.len());
        let mut likelihood = Vec::with_capacity(candidate.len());
        for &i in &candidate {
            let variance = u * u + sigma_sq[i];
            let delta = age - c14[i];
            if delta.abs() <= SIGMA_REACH * variance.sqrt() {
                near.push(i);
                likelihood.push((-(delta * delta) / (2.0 * variance)).exp() / variance.sqrt());
            }
        }
        if near.is_empty() {
            continue;
        }
        let total = sum(&likelihood);
        if !total.is_finite() || total <= 0.0 {
            continue;
        }

        // Width of the 95.45% HPD set, gaps included -- the app's own HPD
        // construction, and on a uniform grid selecting by density and by
        // mass are the same selection.
        let near_ages: Vec<f64> = near.iter().map(|&i| cal[i]).collect();
        let w = hpd_width(&near_ages, &likelihood, total);

        // Which reported years this measurement says anything about.
        for (&i, &l) in near.iter().zip(&likelihood) {
            if let (Some(k), true) = (slot[i], l > 0.0) {
                year_id.push(k);
                weight.push(l);
                width.push(w);
            }
        }
    }

    if year_id.is_empty() {
        return Err(Error::NothingMapsIntoWindow);
    }

    let n_out = out_cal.len();
    let mut mean = vec![f64::NAN; n_out];
    let mut p05 = vec![f64::NAN; n_out];
    let mut p95 = vec![f64::NAN; n_out];
    let mut mean_u = vec![f64::NAN; n_out];

    // Grouped by reported year, then the weighted mean and percentiles read
    // off the sorted widths. Exact rather than binned, since a histogram would
    // floor the precision of the numbers being reported. A stable sort by
    // (year, width), as numpy.lexsort is.
    let mut order: Vec<usize> = (0..year_id.len()).collect();
    order.sort_by(|&a, &b| {
        year_id[a]
            .cmp(&year_id[b])
            .then(width[a].total_cmp(&width[b]))
    });
    let year_id: Vec<usize> = order.iter().map(|&i| year_id[i]).collect();
    let weight: Vec<f64> = order.iter().map(|&i| weight[i]).collect();
    let width: Vec<f64> = order.iter().map(|&i| width[i]).collect();

    let mut start = 0;
    for i in 0..n_out {
        let end = start + year_id[start..].partition_point(|&y| y <= i);
        let (w, r) = (&weight[start..end], &width[start..end]);
        start = end;
        if w.is_empty() {
            continue;
        }
        let total = sum(w);
        if total <= 0.0 {
            continue;
        }
        let products: Vec<f64> = w.iter().zip(r).map(|(w, r)| w * r).collect();
        mean[i] = sum(&products) / total;
        let cumulative: Vec<f64> = cumsum(w).iter().map(|c| c / total).collect();
        let last = r.len() - 1;
        p05[i] = r[searchsorted_left(&cumulative, 0.05).min(last)];
        p95[i] = r[searchsorted_left(&cumulative, 0.95).min(last)];
        mean_u[i] = uncertainty_at(c14[out_index[i]], u0);
    }

    Ok(ResolutionProfile {
        cal_ages: out_cal,
        mean,
        p05,
        p95,
        uncertainty: mean_u,
        u0,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refuses_what_python_refuses() {
        let curve = crate::get_curve("intcal20").unwrap();
        assert_eq!(
            profile(curve, Some(1000.0), Some(1100.0), 0.0, 5),
            Err(Error::NonPositiveUncertainty)
        );
        let empty = CalibrationCurve::new("e", vec![], vec![], vec![]);
        assert_eq!(profile(&empty, None, None, 25.0, 5), Err(Error::EmptyCurve));
    }

    #[test]
    fn a_reversed_window_is_the_same_window() {
        let curve = crate::get_curve("intcal20").unwrap();
        let forward = profile(curve, Some(2400.0), Some(2450.0), 25.0, 10).unwrap();
        let reversed = profile(curve, Some(2450.0), Some(2400.0), 25.0, 10).unwrap();
        assert_eq!(forward.cal_ages, reversed.cal_ages);
        assert_eq!(forward.mean, reversed.mean);
    }
}
