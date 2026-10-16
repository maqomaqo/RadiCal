//! Calibrating a radiocarbon measurement against a curve: the likelihood,
//! the posterior over the curve's grid, and the ways that posterior is
//! reported. A port of `backend/app/services/calibration.py`.

use std::sync::OnceLock;

use crate::numeric::{argmax, argsort_stable, cumsum, diff, interp, interp_one, sum};
use crate::spline::CubicSpline;
use crate::{Error, py_pow};

// --------------------------------------------------------------------------
// The coverages every HPD region in this app is reported at
// --------------------------------------------------------------------------

/// "1 sigma" means the two-sided normal coverage of one standard deviation --
/// erf(1/sqrt(2)) = 0.682689..., quoted as 68.3%. Likewise 95.4% and 99.7%.
/// These are the levels OxCal reports against and the ones this app's labels
/// claim.
///
/// Exact rather than typed short, because the confidence is the threshold the
/// HPD set is accumulated to (see [`hpd_ranges_from_distribution`]) and so a
/// truncated value is a different reported range: over thirty IntCal20
/// calibrations from 500-10,000 BP, a two-sigma level of 0.95 differed from the
/// exact coverage in 27 of 30 cases, by up to 10 years. Python computes these
/// as `erf(k / sqrt(2))`; they are written here as the doubles that gives.
pub const SIGMA_1: f64 = 0.682_689_492_137_085_9; // reported as 68.3%
pub const SIGMA_2: f64 = 0.954_499_736_103_641_6; // reported as 95.4%
pub const SIGMA_3: f64 = 0.997_300_203_936_739_8; // reported as 99.7%

/// The levels the results page offers, in order. One decimal place, correctly
/// ROUNDED: 68.27% rounds to 68.3%. `frontend/src/utils/hpd.ts` carries the
/// same three pairs.
pub const REPORTING_CONFIDENCE_LEVELS: [f64; 3] = [SIGMA_1, SIGMA_2, SIGMA_3];

/// The label each reporting level is quoted under.
pub fn confidence_label(level: f64) -> Option<&'static str> {
    match level {
        SIGMA_1 => Some("68.3%"),
        SIGMA_2 => Some("95.4%"),
        SIGMA_3 => Some("99.7%"),
        _ => None,
    }
}

/// Libby's mean life of 14C, in years: a conventional radiocarbon age r is
/// defined as -8033 ln F14C, so F14C = exp(-r / 8033).
pub const LIBBY_MEAN_LIFE: f64 = 8033.0;

/// Fraction modern for a conventional radiocarbon age.
pub fn f14c(age_bp: f64) -> f64 {
    (-age_bp / LIBBY_MEAN_LIFE).exp()
}

/// How far from the curve, in standard deviations of the F14C difference,
/// observed OxCal 4.4 behaviour treats a measurement's likelihood as zero:
/// exp(-z^2/2) < 1e-6. `sqrt(-2 ln 1e-6)`, written as the double that is.
pub const OXCAL_LIKELIHOOD_CUTOFF_Z: f64 = 5.256_521_769_756_932;

/// How a likelihood is formed.
///
/// Python keeps this in a context variable (`matching_oxcal`), because the
/// likelihood is reached from the solvers, the agreement indices and the
/// wiggle match alike, and every one of them has to see the same setting for
/// the run to describe one model. Here it is passed explicitly instead, which
/// makes the same guarantee visible at each call: whatever runs a model holds
/// one `Mode` and hands it down.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum Mode {
    /// Linear interpolation between curve points, nothing cut.
    #[default]
    Standard,
    /// "Match OxCal 4.4" on an analysis: likelihoods formed as observed OxCal
    /// 4.4 behaviour forms them, the curve interpolated cubically between its
    /// points and the likelihood cut to zero beyond
    /// [`OXCAL_LIKELIHOOD_CUTOFF_Z`].
    MatchOxcal,
}

/// The calendar-year edges of the cell each point in `ages` stands for: the
/// midpoints between neighbours, with the two end cells closed at the end
/// points themselves. The differences of this are the width each point
/// represents, which is what turns a probability MASS per point into a
/// density.
///
/// One definition, shared by the curve's grid weights, the HPD ranking and
/// the point estimates, so a mass and its density can never disagree about
/// how wide a cell is.
pub fn cell_edges(ages: &[f64]) -> Vec<f64> {
    if ages.len() < 2 {
        return ages.to_vec();
    }
    let mut edges = Vec::with_capacity(ages.len() + 1);
    edges.push(ages[0]);
    edges.extend(ages.windows(2).map(|w| (w[0] + w[1]) / 2.0));
    edges.push(ages[ages.len() - 1]);
    edges
}

/// One range of an HPD region, with its own share of the whole distribution.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct HpdRange {
    pub min: f64,
    pub max: f64,
    /// 0-100, a share of the ENTIRE input distribution.
    pub probability_percent: f64,
}

/// Highest Posterior Density (HPD) ranges from a discretized calendar-age
/// distribution, in OxCal/CALIB reporting style.
///
/// Calibration curves are non-monotonic, so one measurement can map to
/// several disjoint calendar-age ranges. Reporting only the min/max envelope
/// would imply probability mass in the near-zero-density gap between them, so
/// each range is returned with its own share of the total, e.g. 3300-3360 at
/// 13.5% and 3100-3270 at 81.9%. The shares sum to slightly MORE than the
/// level asked for, because the set is built from whole grid points and the
/// last one taken overshoots.
///
/// Shared by [`hpd_ranges`] (curve grid points) and the solvers' posterior
/// extraction (histogram bins) -- both are (age, weight) series.
///
/// - `ages`: calendar ages, sorted ascending.
/// - `weights`: probability weights aligned with `ages`, need not already be
///   normalized to sum to 1.
/// - `confidence`: target coverage -- [`SIGMA_1`], [`SIGMA_2`] or
///   [`SIGMA_3`], the levels this app reports at.
/// - `widths`: optionally the calendar width each point stands for. When
///   given, points are RANKED by density (weight / width) while MASS (weight)
///   is what accumulates to the level -- the two coincide only on a uniform
///   grid. Required for correctness on the curve's own grid: IntCal20 steps
///   from 1 to 5 years at 5000 cal BP, and ranking by mass there lets a 5-year
///   cell in the tail outrank a 1-year cell of four times the density, so the
///   region came back with a different shape either side of the step.
///   `frontend/src/utils/hpd.ts` makes the same distinction.
///
/// Returns the ranges ordered oldest-first (largest `max` first), each with
/// `min < max` in years and its share of the entire input distribution.
pub fn hpd_ranges_from_distribution(
    ages: &[f64],
    weights: &[f64],
    confidence: f64,
    widths: Option<&[f64]>,
) -> Result<Vec<HpdRange>, Error> {
    if ages.is_empty() || weights.is_empty() {
        return Err(Error::EmptyDistribution);
    }
    let total = sum(weights);
    if total <= 0.0 || !total.is_finite() {
        return Err(Error::DegenerateDistribution);
    }
    let norm: Vec<f64> = weights.iter().map(|w| w / total).collect();

    // The standard HPD construction: take the highest-DENSITY points first,
    // regardless of where they fall on the age axis, until the level is met.
    let rank: Vec<f64> = match widths {
        None => norm.clone(),
        Some(widths) => {
            if widths.len() != norm.len() {
                return Err(Error::MisalignedWidths);
            }
            norm.iter()
                .zip(widths)
                .map(|(&w, &d)| if d > 0.0 { w / d } else { 0.0 })
                .collect()
        }
    };
    let negated: Vec<f64> = rank.iter().map(|r| -r).collect();
    let sorted = argsort_stable(&negated);
    let cumulative = cumsum(&sorted.iter().map(|&i| norm[i]).collect::<Vec<_>>());
    let mut selected: Vec<usize> = match cumulative.iter().position(|&c| c >= confidence) {
        Some(last) => sorted[..=last].to_vec(),
        // Even the full distribution falls short of the requested confidence,
        // which a few NaN/zero weights can cause.
        None => (0..ages.len()).collect(),
    };
    selected.sort_unstable();

    // Ages are sorted ascending, so each contiguous run of indices is one
    // disjoint range in the HPD set.
    let mut segments = Vec::new();
    let (mut start, mut prev) = (selected[0], selected[0]);
    for &i in &selected[1..] {
        if i == prev + 1 {
            prev = i;
            continue;
        }
        segments.push((start, prev));
        start = i;
        prev = i;
    }
    segments.push((start, prev));

    let mut ranges: Vec<HpdRange> = segments
        .into_iter()
        .map(|(s, e)| HpdRange {
            min: ages[s],
            max: ages[e],
            probability_percent: sum(&norm[s..=e]) * 100.0,
        })
        .collect();
    // Oldest first, matching conventional radiocarbon reporting order.
    ranges.sort_by(|a, b| b.max.total_cmp(&a.max));
    Ok(ranges)
}

/// The HPD region of a calibrated posterior as one or more disjoint
/// calendar-age ranges, each with its own share of the total probability --
/// standard radiocarbon reporting practice, needed because calibration curves
/// are not monotonic. Python's `CalibrationCurve.get_hpd_ranges`.
///
/// The percentages are shares of the ENTIRE distribution, so they typically
/// sum to slightly more than the level asked for: the set is built from whole
/// grid points, as in OxCal.
///
/// `probs` are MASSES (what [`CalibrationCurve::calibrate`] returns) on a grid
/// that is not uniformly spaced, so the ranking is by density -- see
/// [`hpd_ranges_from_distribution`]'s `widths`.
pub fn hpd_ranges(
    cal_ages: &[f64],
    probs: &[f64],
    confidence: f64,
) -> Result<Vec<HpdRange>, Error> {
    let widths = (cal_ages.len() > 1).then(|| diff(&cell_edges(cal_ages)));
    hpd_ranges_from_distribution(cal_ages, probs, confidence, widths.as_deref())
}

/// The overall HPD envelope: the min/max across all disjoint ranges combined,
/// for callers that only need a single span. Because radiocarbon posteriors
/// are frequently multimodal, that span claims probability mass in the gap
/// between peaks -- use [`hpd_ranges`] for the per-range breakdown. Python's
/// `get_hpd_interval`.
pub fn hpd_interval(cal_ages: &[f64], probs: &[f64], confidence: f64) -> Result<(f64, f64), Error> {
    let ranges = hpd_ranges(cal_ages, probs, confidence)?;
    let lo = ranges.iter().map(|r| r.min).fold(f64::INFINITY, f64::min);
    let hi = ranges
        .iter()
        .map(|r| r.max)
        .fold(f64::NEG_INFINITY, f64::max);
    Ok((lo, hi))
}

/// Point estimates of a posterior.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PointEstimate {
    pub mean: f64,
    pub median: f64,
    pub mode: f64,
    pub std: f64,
}

/// Mean, median, mode and standard deviation of a posterior given as masses
/// at `cal_ages`. Python's `get_point_estimate`.
///
/// `probs` are masses on a non-uniform grid. The median is where the
/// cumulative mass crosses a half, read off the CELL EDGES so it is
/// interpolated within a cell rather than snapped to the grid point that first
/// exceeds it. The mode is the highest DENSITY, not the heaviest cell: a
/// 20-year cell holds twenty times the mass of a 1-year cell of equal density.
pub fn point_estimate(cal_ages: &[f64], probs: &[f64]) -> Result<PointEstimate, Error> {
    let total = sum(probs);
    if cal_ages.is_empty() || cal_ages.len() != probs.len() || total == 0.0 {
        // numpy.average's refusal.
        return Err(Error::ZeroWeights);
    }
    let weighted: Vec<f64> = cal_ages.iter().zip(probs).map(|(a, p)| a * p).collect();
    let mean = sum(&weighted) / total;

    let (median, mode) = if cal_ages.len() > 1 {
        let edges = cell_edges(cal_ages);
        let mut cdf = vec![0.0];
        cdf.extend(cumsum(probs).iter().map(|c| c / total));
        let median = interp_one(0.5, &cdf, &edges);
        let density: Vec<f64> = probs.iter().zip(diff(&edges)).map(|(p, w)| p / w).collect();
        (median, cal_ages[argmax(&density).unwrap()])
    } else {
        (cal_ages[0], cal_ages[0])
    };

    let squared: Vec<f64> = cal_ages
        .iter()
        .zip(probs)
        .map(|(a, p)| (a - mean) * (a - mean) * p)
        .collect();
    let std = (sum(&squared) / total).sqrt();
    Ok(PointEstimate {
        mean,
        median,
        mode,
        std,
    })
}

/// How many (age, density) pairs a display series is cut to.
pub const DISPLAY_MAX_POINTS: usize = 200;
/// The fraction of the peak below which a display series is trimmed.
pub const DISPLAY_MASS_THRESHOLD: f64 = 1e-4;

/// A radiocarbon calibration curve: calendar age against radiocarbon age and
/// its 1-sigma error, ascending in calendar age.
#[derive(Debug)]
pub struct CalibrationCurve {
    pub name: String,
    cal_ages: Vec<f64>,
    c14_ages: Vec<f64>,
    c14_errors: Vec<f64>,
    grid_weights: Vec<f64>,
    /// The cubic splines through the curve, built on first use.
    cubic: OnceLock<Result<(CubicSpline, CubicSpline), Error>>,
}

impl Clone for CalibrationCurve {
    fn clone(&self) -> Self {
        Self::new(
            &self.name,
            self.cal_ages.clone(),
            self.c14_ages.clone(),
            self.c14_errors.clone(),
        )
    }
}

impl CalibrationCurve {
    /// A curve from its three columns, which must be the same length and
    /// ascending in calendar age. An empty curve is allowed, as in Python,
    /// and refuses to calibrate.
    ///
    /// # Panics
    /// If the columns differ in length.
    pub fn new(name: &str, cal_ages: Vec<f64>, c14_ages: Vec<f64>, c14_errors: Vec<f64>) -> Self {
        assert!(
            cal_ages.len() == c14_ages.len() && cal_ages.len() == c14_errors.len(),
            "a curve's columns must be the same length"
        );
        // The calendar-year width each curve grid point stands for. Curves
        // like IntCal20 are NOT uniformly spaced -- 1-year steps near the
        // present, 20+ further back -- so plain-summing the likelihood would
        // bias the posterior toward densely-sampled stretches of curve.
        let grid_weights = match cal_ages.len() {
            0 => Vec::new(),
            1 => vec![1.0],
            _ => diff(&cell_edges(&cal_ages)),
        };
        Self {
            name: name.to_owned(),
            cal_ages,
            c14_ages,
            c14_errors,
            grid_weights,
            cubic: OnceLock::new(),
        }
    }

    pub fn cal_ages(&self) -> &[f64] {
        &self.cal_ages
    }

    pub fn c14_ages(&self) -> &[f64] {
        &self.c14_ages
    }

    pub fn c14_errors(&self) -> &[f64] {
        &self.c14_errors
    }

    pub fn len(&self) -> usize {
        self.cal_ages.len()
    }

    pub fn is_empty(&self) -> bool {
        self.cal_ages.is_empty()
    }

    /// The calendar-year width each grid point stands for.
    pub fn grid_weights(&self) -> &[f64] {
        &self.grid_weights
    }

    /// Calibrate a radiocarbon age to a calendar age by the probability
    /// method: at each curve point, evaluate the likelihood (in F14C, with
    /// curve and sample errors in quadrature; see [`Self::likelihood`]),
    /// weight by the calendar-year interval the point represents, and
    /// normalise. Returns the posterior MASS at each of [`Self::cal_ages`].
    ///
    /// The likelihood includes the 1/sigma normalisation term, not just the
    /// exponential. Curve error varies substantially with age, so omitting it
    /// biases the posterior toward high-curve-error regions -- the fix CALIB
    /// (Stuiver & Reimer 1993) and later OxCal made; see Bronk Ramsey (2001)
    /// eqs. 1 and 2.
    ///
    /// A local marine reservoir offset (Delta-R) shifts the age the curve
    /// predicts, since the curve models only the global average: a marine
    /// sample of calendar age t is expected to measure curve(t) + delta_r. Its
    /// uncertainty joins the quadrature sum, so a poorly known offset widens
    /// the calibrated range rather than only moving it.
    pub fn calibrate(
        &self,
        c14_age: f64,
        c14_error: f64,
        delta_r: Option<f64>,
        delta_r_error: Option<f64>,
        mode: Mode,
    ) -> Result<Vec<f64>, Error> {
        if self.is_empty() {
            return Err(Error::EmptyCurve);
        }
        let likelihood = self.likelihood(
            &self.cal_ages,
            c14_age,
            c14_error,
            delta_r,
            delta_r_error,
            mode,
        )?;
        // Weighted by local grid spacing before normalising, so the curve
        // file's calendar-year density does not distort the posterior shape.
        let weighted: Vec<f64> = likelihood
            .iter()
            .zip(&self.grid_weights)
            .map(|(l, w)| l * w)
            .collect();
        let total = sum(&weighted);
        if total <= 0.0 || !total.is_finite() {
            return Err(Error::DegeneratePosterior);
        }
        Ok(weighted.iter().map(|w| w / total).collect())
    }

    /// p(measurement | calendar age) at each of `cal_ages`, formed in F14C.
    ///
    /// A laboratory counts 14C, so it is the fraction modern that carries
    /// Gaussian error; a conventional age is -8033 ln F14C, and its symmetric
    /// error is a linearisation that stretches the old tail and squeezes the
    /// young one. Comparing measurement and curve in F14C removes that, as
    /// OxCal does by default. Through the Holocene the difference is a
    /// fraction of a year; it grows for dates near the curve's old end, where
    /// the BP error is far from symmetric, and for near-modern dates.
    ///
    /// The curve's age and the reservoir offset are combined in radiocarbon
    /// years first -- Delta-R is defined as an offset in years -- and the
    /// expected age and its error are then carried to F14C:
    ///
    /// ```text
    /// F_c = f14c(curve(t) + delta_r),  s_c = F_c sqrt(sc^2 + sdr^2) / 8033
    /// F_m = f14c(c14_age),             s_m = F_m c14_error / 8033
    /// L(t) = N(F_m; F_c, s_c^2 + s_m^2) * F_m / 8033
    /// ```
    ///
    /// The 1/sigma term is kept because the combined error varies along the
    /// curve (Bronk Ramsey 2001 eqs. 1-2). The trailing F_m / 8033 is the
    /// Jacobian back to the measured age: a constant, so no posterior moves,
    /// but it keeps L a density over the reported BP age, which is the scale
    /// model evidence (p(data | model)) is quoted on.
    ///
    /// Zero beyond the curve's ends, where [`Self::interpolate`] clamps rather
    /// than extrapolating and the curve says nothing. Under
    /// [`Mode::MatchOxcal`] the curve is interpolated cubically and the
    /// likelihood cut beyond [`OXCAL_LIKELIHOOD_CUTOFF_Z`] as well.
    pub fn likelihood(
        &self,
        cal_ages: &[f64],
        c14_age: f64,
        c14_error: f64,
        delta_r: Option<f64>,
        delta_r_error: Option<f64>,
        mode: Mode,
    ) -> Result<Vec<f64>, Error> {
        let matching = mode == Mode::MatchOxcal;
        let (curve_c14, curve_error) = if matching {
            self.interpolate_cubic(cal_ages)?
        } else {
            self.interpolate(cal_ages)?
        };
        let (first, last) = (self.cal_ages[0], self.cal_ages[self.len() - 1]);
        let delta_r = delta_r.unwrap_or(0.0);
        // Python floats squared with `**`, where the curve's arrays are
        // squared exactly; see py_pow.
        let delta_r_variance = py_pow(delta_r_error.unwrap_or(0.0), 2.0);
        let measured = f14c(c14_age);
        let measured_error = measured * c14_error / LIBBY_MEAN_LIFE;
        let measured_variance = py_pow(measured_error, 2.0);
        Ok(cal_ages
            .iter()
            .zip(curve_c14.iter().zip(&curve_error))
            .map(|(&t, (&c14, &err))| {
                let expected = f14c(c14 + delta_r);
                let expected_error =
                    expected * (err * err + delta_r_variance).sqrt() / LIBBY_MEAN_LIFE;
                let sigma = (expected_error * expected_error + measured_variance).sqrt();
                let z = (measured - expected) / sigma;
                let lik = (-0.5 * (z * z)).exp() / sigma * measured / LIBBY_MEAN_LIFE;
                let inside =
                    t >= first && t <= last && (!matching || z.abs() <= OXCAL_LIKELIHOOD_CUTOFF_Z);
                if inside { lik } else { 0.0 }
            })
            .collect())
    }

    /// Linearly interpolate the curve's radiocarbon age and its 1-sigma curve
    /// error at arbitrary (non-grid) calendar ages. Values outside the curve's
    /// range are clamped to the nearest edge value rather than extrapolated.
    pub fn interpolate(&self, cal_ages: &[f64]) -> Result<(Vec<f64>, Vec<f64>), Error> {
        if self.is_empty() {
            return Err(Error::EmptyCurve);
        }
        Ok((
            interp(cal_ages, &self.cal_ages, &self.c14_ages),
            interp(cal_ages, &self.cal_ages, &self.c14_errors),
        ))
    }

    /// As [`Self::interpolate`], with a cubic spline through the curve's
    /// points in place of straight lines, for matching OxCal. Identical at the
    /// points themselves; between them it follows the curve's wiggles rather
    /// than cutting across them. Clamped beyond the ends, as `interpolate` is.
    pub fn interpolate_cubic(&self, cal_ages: &[f64]) -> Result<(Vec<f64>, Vec<f64>), Error> {
        if self.is_empty() {
            return Err(Error::EmptyCurve);
        }
        let (c14_spline, error_spline) = self
            .cubic
            .get_or_init(|| {
                Ok((
                    CubicSpline::not_a_knot(&self.cal_ages, &self.c14_ages)?,
                    CubicSpline::not_a_knot(&self.cal_ages, &self.c14_errors)?,
                ))
            })
            .as_ref()
            .map_err(Clone::clone)?;
        let (first, last) = (self.cal_ages[0], self.cal_ages[self.len() - 1]);
        let at: Vec<f64> = cal_ages.iter().map(|t| t.clamp(first, last)).collect();
        Ok((
            at.iter().map(|&t| c14_spline.eval(t)).collect(),
            // A spline through positive errors can overshoot below zero
            // between two small ones.
            at.iter().map(|&t| error_spline.eval(t).max(1e-9)).collect(),
        ))
    }

    /// A compact (cal_age, probability DENSITY) series for the frontend to
    /// plot. Python's `get_display_distribution`.
    ///
    /// The full curve runs to thousands of grid points across 0-55,000 cal
    /// BP, but a single sample's posterior occupies a narrow window of it. So
    /// this converts the posterior MASSES [`Self::calibrate`] returns into a
    /// density by dividing out each grid point's calendar-year width, finds
    /// the contiguous window where that density exceeds `mass_threshold` of
    /// the peak, and downsamples only that window to `max_points`.
    ///
    /// A DENSITY, not masses, because the curves are not uniformly spaced (1,
    /// 5, 10 and 20-year steps in different ranges), so a mass array has
    /// different units in different parts of the curve while the series is
    /// plotted against a calendar axis. It is also what the plot's y-axis is
    /// labelled, and what the exact solver produces for the modelled
    /// posterior drawn over it.
    ///
    /// The returned window integrates to slightly less than 1, by whatever the
    /// trimmed tails held: it is a view of a density, not a renormalised
    /// distribution. Ordered by calendar age; [`DISPLAY_MAX_POINTS`] and
    /// [`DISPLAY_MASS_THRESHOLD`] are the defaults the app uses.
    pub fn display_distribution(
        &self,
        cal_ages: &[f64],
        probs: &[f64],
        max_points: usize,
        mass_threshold: f64,
    ) -> Vec<[f64; 2]> {
        if cal_ages.is_empty() || probs.is_empty() {
            return Vec::new();
        }
        // Masses -> density, guarded against a zero-width cell.
        let density: Vec<f64> = if self.grid_weights.len() == probs.len() {
            probs
                .iter()
                .zip(&self.grid_weights)
                .map(|(&p, &w)| if w > 0.0 { p / w } else { 0.0 })
                .collect()
        } else {
            probs.to_vec()
        };
        let peak = density.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        if peak <= 0.0 {
            return Vec::new();
        }
        let cut = peak * mass_threshold;
        let Some(first) = density.iter().position(|&p| p >= cut) else {
            return Vec::new();
        };
        let last = density.iter().rposition(|&p| p >= cut).unwrap();

        let pad = 3;
        let lo = first.saturating_sub(pad);
        let hi = (cal_ages.len() - 1).min(last + pad);
        let ages = &cal_ages[lo..=hi];
        let window = &density[lo..=hi];
        if ages.len() > max_points {
            crate::numeric::linspace(0.0, (ages.len() - 1) as f64, max_points)
                .into_iter()
                .map(|i| {
                    let i = i as usize;
                    [ages[i], window[i]]
                })
                .collect()
        } else {
            ages.iter().zip(window).map(|(&a, &p)| [a, p]).collect()
        }
    }

    /// Load a curve from the text of a `.14c` file.
    ///
    /// The format is CSV with at least three columns, comma or whitespace
    /// separated: calendar age (years BP), radiocarbon age (years BP) and its
    /// 1-sigma error. Lines starting with `#` are comments, and a line that
    /// does not parse as at least three numbers is skipped, as Python skips it
    /// with a warning. A curve given out of calendar order is sorted.
    pub fn from_14c(text: &str, name: &str) -> Result<Self, Error> {
        let (mut cal, mut c14, mut err) = (Vec::new(), Vec::new(), Vec::new());
        // Python reads the file with universal newlines: \r, \n and \r\n
        // all end a line.
        for line in text.split(['\r', '\n']) {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let parts: Result<Vec<f64>, _> = if line.contains(',') {
                line.split(',').map(|p| p.trim().parse::<f64>()).collect()
            } else {
                line.split_whitespace().map(str::parse::<f64>).collect()
            };
            match parts {
                Ok(parts) if parts.len() >= 3 => {
                    cal.push(parts[0]);
                    c14.push(parts[1]);
                    err.push(parts[2]);
                }
                _ => continue,
            }
        }
        if cal.is_empty() {
            return Err(Error::NoCurveData);
        }
        if !cal.windows(2).all(|w| w[1] > w[0]) {
            let order = crate::numeric::argsort_quicksort(&cal);
            let pick = |v: &[f64]| order.iter().map(|&i| v[i]).collect::<Vec<_>>();
            (cal, c14, err) = (pick(&cal), pick(&c14), pick(&err));
        }
        Ok(Self::new(name, cal, c14, err))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_sigma_levels_are_erf_of_k_over_root_two() {
        // repr(erf(k / sqrt(2))) in Python, for k = 1, 2, 3. The same-event
        // parity test holds them to the corpus's own doubles as well.
        let parse = |s: &str| s.parse::<f64>().unwrap().to_bits();
        assert_eq!(SIGMA_1.to_bits(), parse("0.6826894921370859"));
        assert_eq!(SIGMA_2.to_bits(), parse("0.9544997361036416"));
        assert_eq!(SIGMA_3.to_bits(), parse("0.9973002039367398"));
        assert_eq!(confidence_label(SIGMA_2), Some("95.4%"));
        assert_eq!(confidence_label(0.95), None);
    }

    #[test]
    fn the_cutoff_is_where_the_gaussian_falls_to_a_millionth() {
        assert!(((-2.0 * 1e-6_f64.ln()).sqrt() - OXCAL_LIKELIHOOD_CUTOFF_Z).abs() < 1e-15);
        // repr(float(np.sqrt(-2.0 * np.log(1e-6)))) in Python.
        assert_eq!(
            OXCAL_LIKELIHOOD_CUTOFF_Z.to_bits(),
            "5.256521769756932".parse::<f64>().unwrap().to_bits()
        );
    }

    #[test]
    fn a_curve_file_is_read_whichever_way_it_is_written() {
        let text = "# header\r\n\r\n2,30,5\r\n1 20 4 -24.1\n0,10\n3 40 5 words\nbad,line,here\n";
        let curve = CalibrationCurve::from_14c(text, "t").unwrap();
        assert_eq!(curve.cal_ages(), [1.0, 2.0]);
        assert_eq!(curve.c14_ages(), [20.0, 30.0]);
        assert_eq!(curve.c14_errors(), [4.0, 5.0]);
        assert!(matches!(
            CalibrationCurve::from_14c("# nothing\n", "t"),
            Err(Error::NoCurveData)
        ));
    }

    #[test]
    fn an_empty_curve_refuses() {
        let curve = CalibrationCurve::new("e", vec![], vec![], vec![]);
        assert_eq!(
            curve.calibrate(1000.0, 30.0, None, None, Mode::Standard),
            Err(Error::EmptyCurve)
        );
    }

    #[test]
    fn hpd_refusals() {
        assert_eq!(
            hpd_ranges_from_distribution(&[], &[], SIGMA_2, None),
            Err(Error::EmptyDistribution)
        );
        assert_eq!(
            hpd_ranges_from_distribution(&[1.0], &[0.0], SIGMA_2, None),
            Err(Error::DegenerateDistribution)
        );
        assert_eq!(
            hpd_ranges_from_distribution(&[1.0, 2.0], &[1.0, 1.0], SIGMA_2, Some(&[1.0])),
            Err(Error::MisalignedWidths)
        );
    }
}
