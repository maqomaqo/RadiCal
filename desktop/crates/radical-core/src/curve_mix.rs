//! Curves made by mixing two others, for samples whose carbon came from more
//! than one reservoir. A port of `backend/app/services/curve_mix.py`.
//!
//! The archaeological case is a human or animal with a partly marine diet.
//! Their bone is neither terrestrial nor marine carbon: IntCal20 ignores a
//! reservoir offset of several hundred years, Marine20 applies all of one they
//! only partly experienced, and both are wrong by centuries in opposite
//! directions.
//!
//! OxCal's answer is `Mix_Curves(name, A, B, percent, error)`, and this is the
//! same construction:
//!
//! ```text
//! c14_mix(t)   = p * A(t) + (1 - p) * B(t)
//! sigma_mix(t) = sqrt( p^2 sigma_A(t)^2
//!                    + (1-p)^2 sigma_B(t)^2
//!                    + ((A(t) - B(t)) * sigma_p)^2 )
//! ```
//!
//! The third term under the root earns its keep: a dietary fraction is
//! estimated from stable isotopes with real uncertainty, and that uncertainty
//! matters most where the two curves are furthest apart, because that is where
//! being wrong about the proportion moves the answer furthest. Without it a
//! mixed curve would state a confident age from an admittedly uncertain diet
//! -- the same argument a measurement's Delta-R error makes for a reservoir
//! offset.
//!
//! A mixed curve is stored as its recipe rather than as data, so it stays
//! honest about its parents and they count as in use for as long as it exists.

use crate::{CalibrationCurve, Error};

/// Combine two curves in a fixed proportion.
///
/// - `percent_a`: how much of the carbon came from A, 0-100. OxCal states this
///   as a percentage of the FIRST curve, and so does this.
/// - `percent_a_error`: 1-sigma uncertainty on that percentage.
/// - `name`: for the resulting curve's own label.
///
/// The result is defined on the union of the two grids, restricted to their
/// overlap. Union rather than either alone because the curves are not sampled
/// alike -- IntCal20 steps 1 year where Marine20 steps 10 -- and the coarser
/// grid would throw away detail the mix can carry.
pub fn mix(
    curve_a: &CalibrationCurve,
    curve_b: &CalibrationCurve,
    percent_a: f64,
    percent_a_error: f64,
    name: &str,
) -> Result<CalibrationCurve, Error> {
    if !(0.0..=100.0).contains(&percent_a) {
        return Err(Error::MixProportion);
    }
    if percent_a_error < 0.0 {
        return Err(Error::MixProportionError);
    }
    if curve_a.is_empty() || curve_b.is_empty() {
        return Err(Error::MixEmptyCurve);
    }
    let min = |v: &[f64]| v.iter().copied().fold(f64::INFINITY, f64::min);
    let max = |v: &[f64]| v.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let lo = min(curve_a.cal_ages()).max(min(curve_b.cal_ages()));
    let hi = max(curve_a.cal_ages()).min(max(curve_b.cal_ages()));
    if hi <= lo {
        return Err(Error::MixNoOverlap);
    }

    // numpy.union1d: sorted, each value once.
    let mut grid: Vec<f64> = curve_a
        .cal_ages()
        .iter()
        .chain(curve_b.cal_ages())
        .copied()
        .collect();
    grid.sort_by(f64::total_cmp);
    grid.dedup();
    grid.retain(|&t| t >= lo && t <= hi);

    let (a_c14, a_sigma) = curve_a.interpolate(&grid)?;
    let (b_c14, b_sigma) = curve_b.interpolate(&grid)?;

    let p = percent_a / 100.0;
    let sigma_p = percent_a_error / 100.0;
    let q = 1.0 - p;

    let c14 = a_c14
        .iter()
        .zip(&b_c14)
        .map(|(a, b)| p * a + q * b)
        .collect();
    let sigma = (0..grid.len())
        .map(|i| {
            let (sa, sb, gap) = (
                p * a_sigma[i],
                q * b_sigma[i],
                (a_c14[i] - b_c14[i]) * sigma_p,
            );
            (sa * sa + sb * sb + gap * gap).sqrt()
        })
        .collect();
    Ok(CalibrationCurve::new(name, grid, c14, sigma))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn curves_that_do_not_overlap_cannot_be_mixed() {
        let a = CalibrationCurve::new("a", vec![0.0, 10.0], vec![1.0, 2.0], vec![1.0, 1.0]);
        let b = CalibrationCurve::new("b", vec![20.0, 30.0], vec![1.0, 2.0], vec![1.0, 1.0]);
        assert_eq!(
            mix(&a, &b, 50.0, 0.0, "m").unwrap_err(),
            Error::MixNoOverlap
        );
        assert_eq!(
            mix(&a, &a, -1.0, 0.0, "m").unwrap_err(),
            Error::MixProportion
        );
        assert_eq!(
            mix(&a, &a, 50.0, -1.0, "m").unwrap_err(),
            Error::MixProportionError
        );
    }
}
