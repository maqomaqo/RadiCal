//! Agreement indices -- how well each modelled date still fits the measurement
//! it came from, and how well the model as a whole does. Port of
//! `services/agreement.py`.
//!
//! A Bayesian model moves every date, which means one that does not belong --
//! residual charcoal, an intrusive fragment, a lab error -- gets dragged into
//! line with its neighbours and reported with a confident range. The agreement
//! index is the standard warning (Bronk Ramsey 1995): it compares each date's
//! MODELLED posterior against its own likelihood, so a date the model had to
//! move a long way scores low however tidy its final range looks.
//!
//! # The definition
//!
//! For one date with likelihood L (a density in calendar years) and posterior
//! p:
//!
//! ```text
//! A_i = 100 * integral(p * L) / integral(L^2)
//!     = 100 * E_posterior[ l(t_i) ],     l = L / integral(L^2)
//! ```
//!
//! the posterior expectation of the date's NORMALISED likelihood l. A date the
//! model leaves alone scores 100; one pushed off its likelihood scores low; and
//! a date the model SHARPENS onto the peak of its own likelihood scores above
//! 100, which is why values over 100 are normal and not a bug. Below about 60
//! is the conventional threshold for taking a second look.
//!
//! Amodel is the same expectation taken over the JOINT posterior, every date at
//! once, with the usual 1/sqrt(n) exponent:
//!
//! ```text
//! A_model = 100 * ( E_posterior[ product of l_i(t_i) ] ) ^ (1/sqrt(n))
//! ```
//!
//! At n = 1 that IS A_i, and with a model that leaves the dates independent it
//! returns A_overall; what separates the two is the correlation the model's
//! structure puts between the dates -- which is why Amodel drops below
//! Aoverall exactly when constraints are doing work. It is not a function of
//! the per-date indices and cannot be reached from them.
//!
//! A_overall combines the per-date indices as if they were independent:
//!
//! ```text
//! A_overall = 100 * (product of A_i/100) ^ (1/sqrt(n))
//! ```
//!
//! Both are EXPECTATIONS, and are computed as such: the likelihood is evaluated
//! exactly at each posterior point -- a joint draw, or a node of a tabulated
//! posterior -- and only integral(L^2), a one-dimensional integral of a known
//! function, is done on a grid, at COMPARISON_STEP_YEARS. The index is a ratio
//! of integrals and the right value is the one a finer grid converges to;
//! OxCal's own index moves with its calculation resolution (the Hillside
//! fixture's Aoverall is 100.4 at its default five years, 105.4 at one), and
//! at one year this module reproduces it. Against OxCal at its default five
//! years it reads about 0.7 per date higher, which is OxCal's coarser
//! approximation.
//!
//! # Outlier models
//!
//! A date under an outlier model is compared against the likelihood term that
//! model puts in Bayes' theorem, AS THE MODEL IS SPECIFIED: the measurement
//! fits either the date itself or the date moved by a shift delta, the second
//! with the outlier PRIOR p,
//!
//! ```text
//! L(t, delta) = (1 - p) * L(t) + p * L(t + delta),   delta ~ k_s
//! ```
//!
//! with k_s the model's offset kernel at scale s (Student-t for General, a
//! one-sided exponential for Charcoal). delta is a parameter of the model,
//! with a posterior of its own. The solvers integrate it out -- L becomes
//! (1 - p) * L + p * (L smeared by k_s) -- but that is a way of computing, and
//! a diagnostic should not change with how the posterior was computed. So the
//! expectation runs over delta as well, which has a closed form given t and s:
//!
//! ```text
//! E[l] = [ (1 - p) L(t) + p ((1 - q) B(t) + q D(t) / B(t)) ] / Q
//! B = L * k_s,  D = L^2 * k_s  (convolutions),  q = p B / ((1 - p) L + p B)
//! Q = ((1 - p)^2 + p^2) integral(L^2) + 2 p (1 - p) integral(L B)
//! ```
//!
//! q is the chance the state is the outlier branch. In that branch delta sits
//! wherever puts t + delta back on the likelihood, so the branch always fits --
//! but it carries weight p, fixed, so a date explained ONLY as an outlier scores
//! about 100 p / (1 - p)^2, near 5 at the usual 0.05. Weighting by the
//! posterior outlier probability instead would let the model rescue a moved
//! date after the fact and make the index confirm itself. This is also what
//! OxCal does.
//!
//! The scale s is shared by every date under the same outlier model and has
//! its own posterior. The sampler samples it, and each joint draw carries its
//! own s, so the expectation is over (t_i, s) together. The exact solver
//! instead mixes s per date, and a date it solved is scored with the kernel
//! weighed the same way.
//!
//! An outlier model also adds a term to n, in both overall indices. OxCal gives
//! each Outlier_Model a scale parameter of its own -- the U(0,4) under General
//! -- and lists it in the results table with A = 100, which it then counts: n
//! is the dates PLUS one per outlier model in use. The term contributes nothing
//! to the product but widens the 1/sqrt(n) exponent.
//!
//! # What it does NOT do
//!
//! It detects a date the MODEL HAD TO MOVE, which is not the same as a date
//! that does not belong. On a single unconstrained phase, a date 600
//! radiocarbon years from its four neighbours scores 100.5, because the phase
//! simply widens and nothing is forced anywhere; put the same date where a
//! constraint bites and it collapses to zero. This is a diagnostic for conflict
//! between the data and the model's STRUCTURE -- finding a date that is merely
//! wrong needs an outlier model instead.
//!
//! The element's TPQ/TAQ bounds are deliberately not applied to the
//! likelihood, though the solvers' own likelihood tables carry them: a bound is
//! part of the model, and judging a date against a likelihood already truncated
//! by it would hide exactly the conflict with the bound this exists to show.

use std::cell::RefCell;
use std::collections::{BTreeSet, HashMap};

use crate::exact_chain::summary::Posterior;
use crate::exact_chain::tables::OUTLIER_KERNEL_HALF_WIDTH_YEARS;
use crate::exact_chain::{
    convolve_on_grid, event_likelihood, inbuilt_reach, outlier_kernel, outlier_kernel_at_scale,
};
use crate::intervals::Draws;
use crate::measurement::{Calibrator, Measurement};
use crate::numeric::{interp_bounded, mean, py_max, py_min, sum, unique};
use crate::{Error, py_pow};

/// Step integral(L^2) and the outlier component are tabulated at. Calibration
/// curves are annual at their finest, so a likelihood has structure at one
/// year and a coarser grid mis-states the integral.
pub const COMPARISON_STEP_YEARS: f64 = 1.0;
/// Likelihood below this fraction of its peak is outside the date's support.
pub const SUPPORT_THRESHOLD: f64 = 1e-6;
/// How far either side of everything being compared an outlier component is
/// tabulated. Its tails carry mass for thousands of years at the widest
/// scales, but at a density that adds nothing measurable to integral(L^2) --
/// the prior squared times a density squared.
pub const OUTLIER_MARGIN_YEARS: f64 = 500.0;
/// Conventional threshold for a date worth re-examining (Bronk Ramsey 1995).
pub const POOR_AGREEMENT_BELOW: f64 = 60.0;
/// What OxCal quotes a combination of no dates against; see
/// [`combination_threshold`].
pub const EMPTY_COMBINATION_THRESHOLD: f64 = 60.0;
/// Floor applied before taking logs in the overall index. A date pushed clean
/// off its likelihood scores a true zero, and dropping those would remove
/// precisely the worst dates and let a broken model report a healthy overall.
pub const MINIMUM_FOR_OVERALL: f64 = 0.01;
/// Normalised likelihood given to a posterior point the date's own likelihood
/// puts no mass at, where a true zero would take the joint product to -inf and
/// lose every other draw with it.
const UNSUPPORTED: f64 = 1e-300;

/// The date's own calibration likelihood at `grid` -- what it would say with
/// no model at all. Unnormalised; any array of calendar ages will do.
pub fn unmodelled_likelihood(
    m: &Measurement,
    grid: &[f64],
    cal: &Calibrator,
) -> Result<Vec<f64>, Error> {
    cal.likelihood(grid, m)
}

/// The offset kernel at one scale, or marginalised over the scale prior when
/// `log_scale` is None -- the same kernels the solvers build their likelihood
/// tables from, so the comparison is against the term they actually used.
fn kernel(model: &str, log_scale: Option<f64>) -> Vec<f64> {
    match log_scale {
        None => outlier_kernel(
            COMPARISON_STEP_YEARS,
            OUTLIER_KERNEL_HALF_WIDTH_YEARS,
            model,
        ),
        Some(s) => outlier_kernel_at_scale(
            COMPARISON_STEP_YEARS,
            OUTLIER_KERNEL_HALF_WIDTH_YEARS,
            model,
            s,
        ),
    }
}

/// Where the date's likelihood has any meaningful mass, in cal BP.
fn support(m: &Measurement, cal: &Calibrator) -> Result<(f64, f64), Error> {
    let (ages, prob) = cal.calibrate(m)?;
    let peak = prob.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let threshold = peak * SUPPORT_THRESHOLD;
    match (
        prob.iter().position(|&p| p >= threshold),
        prob.iter().rposition(|&p| p >= threshold),
    ) {
        (Some(a), Some(b)) => Ok((ages[a], ages[b])),
        _ => Ok((ages[0], ages[ages.len() - 1])),
    }
}

/// (B, D, Q) at one scale: the calibration and its square smeared by the
/// offset kernel, and the normalising integral -- see the module docs.
struct Component {
    broad: Option<Vec<f64>>,
    smeared_square: Option<Vec<f64>>,
    total: f64,
}

/// One date's normalised likelihood l = L / integral(L^2), ready to evaluate
/// at posterior points -- per outlier scale where the date carries a model
/// whose scale was sampled.
///
/// The calibration part of L is evaluated EXACTLY at each point; only the
/// outlier component, a convolution, is read off a table, and it is smooth.
struct Normalised<'a> {
    measurement: &'a Measurement,
    cal: &'a Calibrator<'a>,
    prior: f64,
    supplied: Option<(Vec<f64>, Vec<f64>)>,
    grid: Vec<f64>,
    event: Option<Vec<f64>>,
    mass: f64,
    calibration: Vec<f64>,
    by_scale: RefCell<HashMap<Option<u64>, std::rc::Rc<Component>>>,
}

impl<'a> Normalised<'a> {
    fn new(
        measurement: &'a Measurement,
        points: &[f64],
        unmodelled: Option<&[[f64; 2]]>,
        cal: &'a Calibrator<'a>,
    ) -> Result<Self, Error> {
        let prior = if unmodelled.is_some() {
            0.0
        } else {
            measurement.outlier_prior()
        };
        let supplied = unmodelled.map(|u| {
            (
                u.iter().map(|p| p[0]).collect::<Vec<f64>>(),
                u.iter().map(|p| p[1]).collect::<Vec<f64>>(),
            )
        });
        let (lo, hi) = match &supplied {
            Some((x, _)) => (
                x.iter().copied().fold(f64::INFINITY, f64::min),
                x.iter().copied().fold(f64::NEG_INFINITY, f64::max),
            ),
            None => support(measurement, cal)?,
        };
        let pad = if prior > 0.0 {
            OUTLIER_MARGIN_YEARS
        } else {
            0.0
        };
        // An inbuilt-age allowance lets the event sit younger than its
        // material, so the comparison reaches that much further that way.
        let reach = if supplied.is_none() {
            inbuilt_reach(measurement)
        } else {
            0.0
        };
        let points_min = points.iter().copied().fold(f64::INFINITY, f64::min);
        let points_max = points.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let lo = py_min(lo - reach, points_min) - pad;
        let hi = py_max(hi, points_max) + pad;
        let step = COMPARISON_STEP_YEARS;
        let start = (lo / step).floor() * step;
        let count = ((hi - start) / step).ceil() as usize + 1;
        let grid: Vec<f64> = (0..count).map(|i| start + step * i as f64).collect();
        let mut table = Self {
            measurement,
            cal,
            prior,
            supplied,
            grid,
            event: None,
            mass: 0.0,
            calibration: Vec::new(),
            by_scale: RefCell::new(HashMap::new()),
        };
        // The likelihood the solvers used is the EVENT's: with an allowance,
        // the calibration averaged over it. A convolution needs a regular
        // grid, so it is tabulated here and read between nodes, as the outlier
        // component is.
        if reach > 0.0 {
            table.event = Some(event_likelihood(&table.grid, measurement, cal)?);
        }
        let calibration = table.calibration_at(&table.grid)?;
        // Normalised to a DENSITY, not to unit sum over the grid: the index is
        // a ratio of integrals, and the outlier component below is a density
        // too.
        table.mass = sum(&calibration) * step;
        table.calibration = if table.mass > 0.0 {
            calibration.iter().map(|c| c / table.mass).collect()
        } else {
            calibration
        };
        Ok(table)
    }

    fn calibration_at(&self, t: &[f64]) -> Result<Vec<f64>, Error> {
        if let Some((x, y)) = &self.supplied {
            return Ok(interp_bounded(t, x, y, 0.0, 0.0));
        }
        if let Some(event) = &self.event {
            return Ok(interp_bounded(t, &self.grid, event, 0.0, 0.0));
        }
        unmodelled_likelihood(self.measurement, t, self.cal)
    }

    fn component(&self, log_scale: Option<f64>) -> std::rc::Rc<Component> {
        let key = log_scale.map(f64::to_bits);
        if let Some(found) = self.by_scale.borrow().get(&key) {
            return found.clone();
        }
        let step = COMPARISON_STEP_YEARS;
        let squared: Vec<f64> = self.calibration.iter().map(|c| c * c).collect();
        let square = sum(&squared) * step;
        let p = self.prior;
        let component = if p > 0.0 {
            let k = kernel(self.measurement.outlier_model(), log_scale);
            let broad = convolve_on_grid(&self.calibration, &k);
            let smeared_square = convolve_on_grid(&squared, &k);
            let products: Vec<f64> = self
                .calibration
                .iter()
                .zip(&broad)
                .map(|(c, b)| c * b)
                .collect();
            let cross = sum(&products) * step;
            let total =
                (py_pow(1.0 - p, 2.0) + py_pow(p, 2.0)) * square + 2.0 * p * (1.0 - p) * cross;
            Component {
                broad: Some(broad),
                smeared_square: Some(smeared_square),
                total,
            }
        } else {
            Component {
                broad: None,
                smeared_square: None,
                total: square,
            }
        };
        let component = std::rc::Rc::new(component);
        self.by_scale.borrow_mut().insert(key, component.clone());
        component
    }

    fn usable(&self) -> bool {
        self.mass > 0.0
    }

    /// The normalised likelihood's expectation at the calendar ages `t`, under
    /// one outlier scale -- over the outlier shift as well, where there is one.
    fn at(&self, t: &[f64], log_scale: Option<f64>) -> Result<Vec<f64>, Error> {
        let component = self.component(log_scale);
        let total = component.total;
        if total <= 0.0 {
            return Ok(vec![0.0; t.len()]);
        }
        let p = self.prior;
        let own: Vec<f64> = self
            .calibration_at(t)?
            .iter()
            .map(|c| c / self.mass)
            .collect();
        if p <= 0.0 {
            return Ok(own.iter().map(|o| o / total).collect());
        }
        let b = interp_bounded(
            t,
            &self.grid,
            component.broad.as_ref().expect("an outlier component"),
            0.0,
            0.0,
        );
        let d = interp_bounded(
            t,
            &self.grid,
            component
                .smeared_square
                .as_ref()
                .expect("an outlier component"),
            0.0,
            0.0,
        );
        Ok((0..t.len())
            .map(|i| {
                let mixture = (1.0 - p) * own[i] + p * b[i];
                // p * [(1 - q) B + q D / B] with q = p B / mixture, written so
                // that neither B nor the mixture is ever divided by where it
                // is zero.
                let shifted = if mixture > 0.0 {
                    (p * (1.0 - p) * own[i] * b[i] + p * p * d[i]) / mixture.max(1e-300)
                } else {
                    0.0
                };
                ((1.0 - p) * own[i] + shifted) / total
            })
            .collect())
    }

    /// l at each point, each under its own scale when there are several.
    fn over(&self, t: &[f64], log_scales: Option<&[f64]>) -> Result<Vec<f64>, Error> {
        let Some(scales) = log_scales.filter(|_| self.prior > 0.0) else {
            return self.at(t, None);
        };
        let mut out = vec![0.0; t.len()];
        for scale in unique(scales) {
            let chosen: Vec<usize> = (0..t.len()).filter(|&i| scales[i] == scale).collect();
            let points: Vec<f64> = chosen.iter().map(|&i| t[i]).collect();
            for (&i, v) in chosen.iter().zip(self.at(&points, Some(scale))?) {
                out[i] = v;
            }
        }
        Ok(out)
    }
}

fn scored(
    m: &Measurement,
    points: &[f64],
    weights: &[f64],
    log_scales: Option<&[f64]>,
    unmodelled: Option<&[[f64; 2]]>,
    cal: &Calibrator,
) -> Result<Option<f64>, Error> {
    let keep: Vec<usize> = (0..weights.len()).filter(|&i| weights[i] > 0.0).collect();
    if keep.is_empty() {
        return Ok(None);
    }
    let kept: Vec<f64> = keep.iter().map(|&i| weights[i]).collect();
    let total = sum(&kept);
    let weights: Vec<f64> = kept.iter().map(|w| w / total).collect();
    let points: Vec<f64> = keep.iter().map(|&i| points[i]).collect();
    let scales: Option<Vec<f64>> = log_scales.map(|s| keep.iter().map(|&i| s[i]).collect());
    let table = Normalised::new(m, &points, unmodelled, cal)?;
    if !table.usable() {
        return Ok(None);
    }
    let values = table.over(&points, scales.as_deref())?;
    let products: Vec<f64> = weights.iter().zip(&values).map(|(w, v)| w * v).collect();
    Ok(Some(100.0 * sum(&products)))
}

/// `numpy.gradient` of a 1-D array at unit spacing: central differences
/// inside, one-sided at the ends.
fn gradient(x: &[f64]) -> Vec<f64> {
    let n = x.len();
    (0..n)
        .map(|i| {
            if i == 0 {
                x[1] - x[0]
            } else if i == n - 1 {
                x[n - 1] - x[n - 2]
            } else {
                (x[i + 1] - x[i - 1]) / 2.0
            }
        })
        .collect()
}

/// A_i for one date from a TABULATED posterior, `modelled` as [cal_age,
/// density] on a regular grid -- the expectation of the normalised likelihood
/// over the posterior's nodes.
///
/// `unmodelled` holds the comparison against a likelihood from elsewhere, in
/// the same form, which is how the formula is checked against OxCal's exports
/// without our own calibration muddying the test. `log_scale` fixes the
/// outlier scale for a date under an outlier model; left out, the scale is
/// marginalised over its prior.
///
/// None rather than a number when there is nothing to compare.
pub fn agreement_index(
    m: &Measurement,
    modelled: &[[f64; 2]],
    unmodelled: Option<&[[f64; 2]]>,
    log_scale: Option<f64>,
    cal: &Calibrator,
) -> Result<Option<f64>, Error> {
    if modelled.len() < 2 {
        return Ok(None);
    }
    let ages: Vec<f64> = modelled.iter().map(|p| p[0]).collect();
    // Density times the width each node stands for, so an irregular grid is
    // weighted correctly; on a regular one this is just the density.
    let widths = gradient(&ages);
    let weights: Vec<f64> = modelled
        .iter()
        .zip(&widths)
        .map(|(p, w)| p[1].max(0.0) * w.abs())
        .collect();
    let scales = log_scale.map(|s| vec![s; modelled.len()]);
    scored(m, &ages, &weights, scales.as_deref(), unmodelled, cal)
}

/// A_i for one date from a run's JOINT draws of it, each with the outlier
/// scale the same state held when the date's outlier model was sampled.
pub fn date_agreement(
    m: &Measurement,
    draws: &[f64],
    log_scales: Option<&[f64]>,
    cal: &Calibrator,
) -> Result<Option<f64>, Error> {
    if draws.is_empty() {
        return Ok(None);
    }
    scored(m, draws, &vec![1.0; draws.len()], log_scales, None, cal)
}

/// How many outlier-model scale parameters OxCal would count in n: one per
/// distinct outlier model any date is under.
pub fn outlier_scale_terms(measurements: &[Measurement]) -> usize {
    measurements
        .iter()
        .filter(|m| m.outlier_prior() > 0.0)
        .map(Measurement::outlier_model)
        .collect::<BTreeSet<_>>()
        .len()
}

/// The whole model's agreement, combining the per-date indices.
///
/// `extra_terms` counts parameters that score exactly 100 and so add to n
/// without touching the product -- [`outlier_scale_terms`], for a model with
/// an outlier model in it.
///
/// Computed in log space, since the product runs over every date and would
/// otherwise underflow on a large model. The 1/sqrt(n) exponent multiplies any
/// systematic per-date bias by sqrt(n).
pub fn overall_agreement(indices: &[Option<f64>], extra_terms: usize) -> Option<f64> {
    let usable: Vec<f64> = indices.iter().flatten().copied().collect();
    if usable.is_empty() {
        return None;
    }
    let logs: Vec<f64> = usable
        .iter()
        .map(|&a| (a.max(MINIMUM_FOR_OVERALL) / 100.0).ln())
        .collect();
    Some(100.0 * (sum(&logs) / ((usable.len() + extra_terms) as f64).sqrt()).exp())
}

/// The pass mark a combination index over `n` dates is quoted against:
/// A_n = 100 / sqrt(2n), the threshold in OxCal's documentation and the one
/// observed OxCal 4.4 behaviour prints beside Acomb ("Acomb=...(An=...)"). It
/// falls as n grows, since the index's 1/sqrt(n) exponent lets a long series
/// score lower than a short one for the same quality of fit. With no dates
/// OxCal falls back to 60, the per-date threshold.
pub fn combination_threshold(n: usize) -> f64 {
    if n == 0 {
        return EMPTY_COMBINATION_THRESHOLD;
    }
    100.0 / (2.0 * n as f64).sqrt()
}

fn scales_for<'s>(
    m: &Measurement,
    scales: Option<&'s HashMap<String, Vec<f64>>>,
) -> Option<&'s [f64]> {
    let scales = scales.filter(|s| !s.is_empty())?;
    if m.outlier_prior() <= 0.0 {
        return None;
    }
    scales.get(m.outlier_model()).map(Vec::as_slice)
}

/// A_model: the agreement index of the whole model, from its JOINT posterior.
///
/// `draws` maps a date's id to the calendar ages it took across the run's
/// joint states -- one state per draw, every date in it at once, which is what
/// carries the correlation the model imposed. `scales` maps an outlier model
/// to the log10 scale each of those states held, where the solver sampled it;
/// without it a flagged date's scale is marginalised over its prior.
///
/// Dates missing from `draws`, or with no likelihood to speak of, are left out
/// of both the product and n, exactly as A_overall leaves out a date whose
/// index could not be computed. None when no date could be included.
pub fn model_agreement(
    measurements: &[Measurement],
    draws: &Draws,
    scales: Option<&HashMap<String, Vec<f64>>>,
    cal: &Calibrator,
) -> Result<Option<f64>, Error> {
    let mut log_joint: Option<Vec<f64>> = None;
    let mut counted = 0usize;
    for m in measurements {
        let Some(drawn) = draws.get(&m.id).filter(|d| !d.is_empty()) else {
            continue;
        };
        let table = Normalised::new(m, drawn, None, cal)?;
        if !table.usable() {
            continue;
        }
        let values = table.over(drawn, scales_for(m, scales))?;
        // In logs: the product runs over every date, and at eight dates it is
        // already at 1e-14. A draw the likelihood gives no mass to contributes
        // nothing to the expectation -- the model put the date where its own
        // measurement says it cannot be.
        let term: Vec<f64> = values.iter().map(|v| v.max(UNSUPPORTED).ln()).collect();
        log_joint = Some(match log_joint {
            None => term,
            Some(joint) => joint.iter().zip(&term).map(|(a, b)| a + b).collect(),
        });
        counted += 1;
    }
    let Some(log_joint) = log_joint.filter(|_| counted > 0) else {
        return Ok(None);
    };
    // log of the MEAN, not the mean of the logs: the index is an expectation,
    // and averaging in log space would report the geometric mean instead.
    let peak = log_joint.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let shifted: Vec<f64> = log_joint.iter().map(|v| (v - peak).exp()).collect();
    let log_expectation = peak + mean(&shifted).ln();
    // The outlier models' scale parameters count towards n here as they do in
    // A_overall, and for the same reason: OxCal counts them.
    let n = counted + outlier_scale_terms(measurements);
    Ok(Some(100.0 * (log_expectation / (n as f64).sqrt()).exp()))
}

/// Add `agreement_index` to each per-date result in place and return the
/// overall index.
///
/// A date whose outlier scale was SAMPLED is scored off the joint draws, each
/// under the scale its state held -- the only place that pairing survives.
/// Every other date is scored off its `probability_distribution`, the full
/// marginal, which carries no Monte Carlo error beyond the run's own.
pub fn attach(
    results: &mut [(String, Posterior)],
    measurements: &[Measurement],
    draws: Option<&Draws>,
    scales: Option<&HashMap<String, Vec<f64>>>,
    cal: &Calibrator,
) -> Result<Option<f64>, Error> {
    let mut indices = Vec::new();
    for m in measurements {
        let Some((_, entry)) = results.iter_mut().find(|(id, _)| *id == m.id) else {
            continue;
        };
        let drawn = draws.and_then(|d| d.get(&m.id));
        let paired = scales_for(m, scales);
        let index = match (drawn, paired) {
            (Some(drawn), Some(paired)) if paired.len() == drawn.len() => {
                date_agreement(m, drawn, Some(paired), cal)?
            }
            _ => agreement_index(m, &entry.probability_distribution, None, None, cal)?,
        };
        entry.agreement_index = index;
        indices.push(index);
    }
    Ok(overall_agreement(
        &indices,
        outlier_scale_terms(measurements),
    ))
}
