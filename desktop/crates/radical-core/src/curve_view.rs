//! Serving a piece of a calibration curve to be looked at. Port of
//! `services/curve_view.py`.
//!
//! A curve is up to 9,501 points spanning 55,000 years, and browsing one is
//! about seeing the wiggles -- so reducing a window to a drawable number of
//! points must not erase the features being examined.
//!
//! Hence decimation is min/max per bucket rather than every Nth point. Stride
//! sampling removes exactly the extremes that make a curve wiggly: the
//! Hallstatt plateau's reversals, the sharp drop at its start, the short
//! excursions that split a calibrated date into two ranges. Keeping the lowest
//! and highest radiocarbon age per bucket preserves the envelope at any zoom,
//! at two points per bucket instead of one.
//!
//! Local slope is computed on the FULL grid beforehand and carried through, so
//! a hover readout in a decimated view reports the curve's real gradient
//! rather than one measured between two surviving points that were never
//! adjacent.

use std::collections::BTreeSet;

use crate::calibration::CalibrationCurve;
use crate::numeric::{interp, linspace, searchsorted_left};
use crate::{Error, pyfmt};

/// Enough to draw a curve on a wide screen without the payload getting silly.
pub const DEFAULT_MAX_POINTS: usize = 800;

/// A comparison window narrower than this is widened, so two curves with no
/// shared grid point in a tight window still draw a line.
pub const GRID_MINIMUM_SPAN: f64 = 1.0;
pub const MAX_MAX_POINTS: usize = 5000;

/// How far either side of a point the slope is measured over, in calendar
/// years.
///
/// A point-to-point gradient is unusable: on IntCal20's 1-year grid it is
/// dominated by year-to-year scatter and swings between +4 and -3 across a
/// stretch that is flat at any scale a person cares about. 50 separates a
/// plateau from ordinary curve threefold (mean slope 0.31 against 0.91)
/// without 25's scatter or 100's smearing of the plateau into its
/// surroundings.
pub const SLOPE_HALF_SPAN_YEARS: f64 = 50.0;

/// `numpy.clip`: the larger of `x` and `lo`, then the smaller of that and `hi`.
fn clip(x: f64, lo: f64, hi: f64) -> f64 {
    let raised = if x < lo { lo } else { x };
    if raised > hi { hi } else { raised }
}

fn max_points_clipped(max_points: usize) -> usize {
    max_points.clamp(2, MAX_MAX_POINTS)
}

/// d(14C age)/d(calendar year) at each requested calendar age, in radiocarbon
/// years per calendar year, measured over a fixed calendar span rather than
/// between adjacent grid points.
///
/// A fixed span rather than a grid step, so the value means the same thing
/// everywhere: the grid is 1 year near the present and 20 beyond 25,000 cal
/// BP. Around 1 the curve tracks calendar time; near 0 a measurement maps to
/// a wide span of calendar years; below 0 the curve runs backwards and one
/// measurement splits into disjoint ranges.
pub fn slope(curve: &CalibrationCurve, at: &[f64]) -> Vec<f64> {
    let cal = curve.cal_ages();
    let (cal_min, cal_max) = (cal[0], cal[cal.len() - 1]);
    // Clamped at the curve's ends with the denominator taken from the span
    // actually used, so an edge point reports a one-sided slope, not a halved
    // one.
    let lo: Vec<f64> = at
        .iter()
        .map(|&a| clip(a - SLOPE_HALF_SPAN_YEARS, cal_min, cal_max))
        .collect();
    let hi: Vec<f64> = at
        .iter()
        .map(|&a| clip(a + SLOPE_HALF_SPAN_YEARS, cal_min, cal_max))
        .collect();
    let younger = interp(&lo, cal, curve.c14_ages());
    let older = interp(&hi, cal, curve.c14_ages());
    (0..at.len())
        .map(|i| {
            let span = hi[i] - lo[i];
            if span > 0.0 {
                (older[i] - younger[i]) / span
            } else {
                0.0
            }
        })
        .collect()
}

/// Reduce a run of indices to at most `max_points`, keeping the lowest and
/// highest radiocarbon age in each bucket, in calendar order. The first and
/// last always survive, so the drawn curve starts and ends where the window
/// does.
pub fn decimate(indices: &[usize], c14: &[f64], max_points: usize) -> Vec<usize> {
    if indices.len() <= max_points {
        return indices.to_vec();
    }
    // Two points per bucket, so the bucket count is half the budget.
    let buckets = (max_points / 2).max(1);
    let edges: Vec<usize> = linspace(0.0, indices.len() as f64, buckets + 1)
        .into_iter()
        .map(|e| e as usize)
        .collect();
    let mut keep = BTreeSet::from([indices[0], indices[indices.len() - 1]]);
    for w in edges.windows(2) {
        let (start, end) = (w[0], w[1]);
        if end <= start {
            continue;
        }
        let chunk = &indices[start..end];
        let (mut lowest, mut highest) = (0, 0);
        for (k, &i) in chunk.iter().enumerate() {
            if c14[i] < c14[chunk[lowest]] {
                lowest = k;
            }
            if c14[i] > c14[chunk[highest]] {
                highest = k;
            }
        }
        keep.insert(chunk[lowest]);
        keep.insert(chunk[highest]);
    }
    keep.into_iter().collect()
}

/// A drawable slice of a curve.
#[derive(Debug, Clone, PartialEq)]
pub struct Window {
    pub cal_from: f64,
    pub cal_to: f64,
    /// `[cal BP, 14C age, 1-sigma, slope]` rows in calendar order.
    pub points: Vec<[f64; 4]>,
    /// How many points the window held before decimation -- how a caller
    /// knows whether it has the whole curve or a summary of it.
    pub points_in_window: usize,
    pub decimated: bool,
    pub curve_cal_min: f64,
    pub curve_cal_max: f64,
}

/// The calendar window `cal_from`..`cal_to` in cal BP, clamped to what the
/// curve covers (either `None` meaning to that end of the curve), decimated to
/// at most `max_points` (itself held to 2..=5000).
pub fn window(
    curve: &CalibrationCurve,
    cal_from: Option<f64>,
    cal_to: Option<f64>,
    max_points: usize,
) -> Result<Window, Error> {
    let cal = curve.cal_ages();
    if cal.is_empty() {
        return Err(Error::EmptyCurve);
    }
    let (cal_min, cal_max) = (cal[0], cal[cal.len() - 1]);
    let mut lo = cal_from.unwrap_or(cal_min);
    let mut hi = cal_to.unwrap_or(cal_max);
    if hi < lo {
        std::mem::swap(&mut lo, &mut hi);
    }
    let lo = if cal_min > lo { cal_min } else { lo };
    let hi = if cal_max < hi { cal_max } else { hi };

    let mut selected: Vec<usize> = (0..cal.len())
        .filter(|&i| cal[i] >= lo && cal[i] <= hi)
        .collect();
    if selected.len() < 2 {
        // A window narrower than the curve's grid spacing holds no points or
        // one, and neither draws a line. Widen to the points bracketing it.
        let (left, right) = match selected.first() {
            None => {
                let right = searchsorted_left(cal, lo);
                (right.saturating_sub(1), right)
            }
            Some(&first) => (first.saturating_sub(1), first + 1),
        };
        let right = right.min(cal.len() - 1);
        selected = (left..=right).collect();
    }

    let kept = decimate(&selected, curve.c14_ages(), max_points_clipped(max_points));
    // Measured against the whole curve, not the window, so an edge point is
    // not described by a slope that stops where the view does.
    let at: Vec<f64> = kept.iter().map(|&i| cal[i]).collect();
    let slopes = slope(curve, &at);
    let points = kept
        .iter()
        .zip(&slopes)
        .map(|(&i, &s)| [cal[i], curve.c14_ages()[i], curve.c14_errors()[i], s])
        .collect();

    Ok(Window {
        cal_from: cal[selected[0]],
        cal_to: cal[selected[selected.len() - 1]],
        points,
        points_in_window: selected.len(),
        decimated: kept.len() < selected.len(),
        curve_cal_min: cal_min,
        curve_cal_max: cal_max,
    })
}

/// Two curves on one grid, with their difference.
#[derive(Debug, Clone, PartialEq)]
pub struct Comparison {
    pub cal_from: f64,
    pub cal_to: f64,
    /// `[cal BP, A, sigma_A, B, sigma_B, A - B, sigma of the difference]`.
    pub points: Vec<[f64; 7]>,
    pub points_in_window: usize,
    pub decimated: bool,
    pub overlap_cal_min: f64,
    pub overlap_cal_max: f64,
}

/// Two curves on one grid, with their difference.
///
/// The difference is what the comparison is for. Two curves drawn over each
/// other look alike at any zoom fitting a millennium on screen, where A - B is
/// legible immediately. Decimation keeps the extremes of the DIFFERENCE, that
/// being the series examined; the curves are reported at whichever points
/// survive. The difference's sigma is the two curve errors in quadrature.
pub fn compare(
    curve_a: &CalibrationCurve,
    curve_b: &CalibrationCurve,
    cal_from: Option<f64>,
    cal_to: Option<f64>,
    max_points: usize,
) -> Result<Comparison, Error> {
    let (a, b) = (curve_a.cal_ages(), curve_b.cal_ages());
    if a.is_empty() || b.is_empty() {
        return Err(Error::EmptyCurve);
    }
    let lo_curve = a[0].max(b[0]);
    let hi_curve = a[a.len() - 1].min(b[b.len() - 1]);
    if hi_curve <= lo_curve {
        return Err(Error::CurvesDoNotOverlap);
    }
    // Python's max(x, y) and min(x, y): the first unless the second is
    // strictly beyond it.
    let mut lo = cal_from.map_or(lo_curve, |f| if lo_curve > f { lo_curve } else { f });
    let mut hi = cal_to.map_or(hi_curve, |t| if hi_curve < t { hi_curve } else { t });
    if hi < lo {
        std::mem::swap(&mut lo, &mut hi);
    }

    let mut grid: Vec<f64> = a
        .iter()
        .chain(b)
        .copied()
        .filter(|&g| g >= lo && g <= hi)
        .collect();
    grid.sort_by(f64::total_cmp);
    grid.dedup();
    if grid.len() < 2 {
        let top = if lo + GRID_MINIMUM_SPAN > hi {
            lo + GRID_MINIMUM_SPAN
        } else {
            hi
        };
        grid = linspace(lo, top, 2);
    }

    let (a_c14, a_sigma) = curve_a.interpolate(&grid)?;
    let (b_c14, b_sigma) = curve_b.interpolate(&grid)?;
    let difference: Vec<f64> = a_c14.iter().zip(&b_c14).map(|(x, y)| x - y).collect();
    let indices: Vec<usize> = (0..grid.len()).collect();
    let kept = decimate(&indices, &difference, max_points_clipped(max_points));

    Ok(Comparison {
        cal_from: grid[0],
        cal_to: grid[grid.len() - 1],
        points: kept
            .iter()
            .map(|&i| {
                [
                    grid[i],
                    a_c14[i],
                    a_sigma[i],
                    b_c14[i],
                    b_sigma[i],
                    difference[i],
                    a_sigma[i].hypot(b_sigma[i]),
                ]
            })
            .collect(),
        points_in_window: grid.len(),
        decimated: kept.len() < grid.len(),
        overlap_cal_min: lo_curve,
        overlap_cal_max: hi_curve,
    })
}

/// The visible window as CSV, in .14c column order with the local gradient
/// alongside, so a stretch of curve can leave as data and not only a picture.
pub fn to_csv(window: &Window, curve_name: &str) -> String {
    let mut out = format!("# {curve_name}\n# CAL BP,14C age,Sigma,Slope (14C yr / cal yr)\n");
    for [cal, c14, sigma, slope] in &window.points {
        out.push_str(&format!(
            "{},{},{},{slope:.4}\n",
            pyfmt::g(*cal, 6),
            pyfmt::g(*c14, 6),
            pyfmt::g(*sigma, 6)
        ));
    }
    out
}
