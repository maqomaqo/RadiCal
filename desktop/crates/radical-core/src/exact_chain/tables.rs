//! Each date's likelihood on the solver's calendar grid -- its calibration,
//! its inbuilt-age allowance and its outlier model folded in -- and the grids
//! themselves.

use std::collections::HashMap;

use crate::exact_chain::{
    BOUNDARY_WINDOW_FACTOR, GROUPS_AT_FULL_RESOLUTION, MAX_GRID_POINTS, MIN_BOUNDARY_PAD_YEARS,
    MIN_GRID_STEP_YEARS, REFINE_MARGIN_FRACTION, REFINE_MARGIN_STEPS, REFINE_MIN_GAIN,
    REFINE_TAIL_MASS, REFINED_MIN_STEP_YEARS, REPORTING_STEP_YEARS, SUPPORT_THRESHOLD,
};
use crate::measurement::{Calibrator, Measurement};
use crate::model_tree::Limits;
use crate::numeric::{arange, cumsum, linspace, py_max, py_min, searchsorted_left, sum};
use crate::stratigraphy::measurement_bounds;
use crate::{Error, py_pow};

/// OxCal's "General" outlier model: a Student-t offset on the calendar age,
/// scaled by 10^u with u uniform over four decades (Bronk Ramsey 2009).
pub const OUTLIER_DF: f64 = 5.0;
pub const OUTLIER_LOG_SCALE_MAX: f64 = 4.0;
pub const OUTLIER_SCALE_NODES: usize = 24;
pub const OUTLIER_KERNEL_HALF_WIDTH_YEARS: f64 = 20000.0;
/// The charcoal/residual model instead assumes a one-sided gap: the dated
/// material formed some years BEFORE the context it was found in, never after.
/// OxCal's Charcoal_Outlier is
///
/// ```text
/// Outlier_Model("Charcoal", Exp(1,-10,0), U(0,3), "t")
/// ```
///
/// -- an exponential gap truncated at 10 scale lengths, with the SCALE itself
/// drawn as 10^u over three decades, as the General model draws its own over
/// four. Integrating that scale is not a refinement: at a single fixed scale
/// only 12% of the offset mass lands within 20 years against OxCal's 55%, so a
/// flagged date drifts onto its neighbours instead of pulling the group towards
/// itself. On the charcoal fixture, integrating it moved the modelled dates
/// from 16.1 to 9.9 years rms of OxCal's answer.
pub const CHARCOAL_LOG_SCALE_MAX: f64 = 3.0;
pub const CHARCOAL_KERNEL_SPAN_TAUS: f64 = 10.0;
pub const OUTLIER_MODELS: [&str; 2] = ["general", "charcoal"];
pub const DEFAULT_OUTLIER_MODEL: &str = "general";

/// Inbuilt age (feature spec M1): how much older than its event a sample's
/// material may be. Charcoal from heartwood dates the wood, not the fire. A
/// date's allowance is `inbuilt_years` and `inbuilt_shape`:
///
/// ```text
/// uniform      any offset from 0 to inbuilt_years, equally likely, never more
///              -- a published MAXIMUM inbuilt age, as a published source can give.
/// exponential  offsets with mean inbuilt_years, small ones likeliest, a long
///              tail and no ceiling -- a TYPICAL age with no maximum known.
/// ```
///
/// The offset touches only its own date, so it is integrated out of that
/// date's likelihood here, as the outlier kernels are, and both solvers, the
/// direct cross-check and the agreement index see the same term.
pub const INBUILT_SHAPES: [&str; 2] = ["uniform", "exponential"];
/// How far an exponential allowance is followed, in means: 10 keeps all but
/// 0.005% of it.
pub const INBUILT_EXPONENTIAL_REACH: f64 = 10.0;

fn inbuilt_shape(m: &Measurement) -> &str {
    crate::measurement::or_str(&m.inbuilt_shape, "uniform")
}

/// How much younger, in years, a date's event may be than its material.
pub fn inbuilt_reach(m: &Measurement) -> f64 {
    let years = m.inbuilt_years();
    if years <= 0.0 {
        return 0.0;
    }
    if inbuilt_shape(m) == "exponential" {
        return INBUILT_EXPONENTIAL_REACH * years;
    }
    years
}

/// A date's inbuilt-age allowance as a convolution kernel on a grid of `step`
/// years, or None for a date with none.
///
/// The event is the material's age MINUS the offset, so an event at t is
/// supported by the material's likelihood at t + s:
///
/// ```text
/// L_event(t) = integral k(s) L(t + s) ds,    s >= 0
/// ```
///
/// which puts the kernel's mass at NEGATIVE lag in [`convolve_on_grid`]'s
/// convention -- the side the Charcoal outlier kernel uses, for the same
/// reason. Each lag cell takes the allowance's probability over that cell
/// exactly (from its CDF), so an allowance that is not a whole number of
/// steps, or is shorter than one, still has the right mean.
pub fn inbuilt_kernel(step: f64, m: &Measurement) -> Result<Option<Vec<f64>>, Error> {
    let years = m.inbuilt_years();
    if years <= 0.0 {
        return Ok(None);
    }
    let shape = inbuilt_shape(m);
    if !INBUILT_SHAPES.contains(&shape) {
        return Err(Error::Model(format!(
            "sample '{}' has unknown inbuilt-age shape '{shape}'; expected one of {}",
            m.name_or_id(),
            INBUILT_SHAPES.join(", ")
        )));
    }
    let reach = inbuilt_reach(m);
    let lags = arange(0.0, reach + step, step);
    let weights: Vec<f64> = lags
        .iter()
        .map(|&lag| {
            let lo = (lag - step / 2.0).max(0.0).min(reach);
            let hi = (lag + step / 2.0).max(0.0).min(reach);
            if shape == "uniform" {
                (hi - lo) / years
            } else {
                (-lo / years).exp() - (-hi / years).exp()
            }
        })
        .collect();
    let used = weights
        .iter()
        .rposition(|&w| w > 0.0)
        .expect("an allowance has weight somewhere")
        + 1;
    let weights = &weights[..used];
    let total = sum(weights);
    let mut kernel: Vec<f64> = weights.iter().rev().map(|w| w / total).collect();
    kernel.extend(std::iter::repeat_n(0.0, used - 1));
    Ok(Some(kernel))
}

/// The measurement's own likelihood at each calendar age: the age of the
/// MATERIAL, with its reservoir offset applied.
fn calibration_likelihood(
    points: &[f64],
    m: &Measurement,
    cal: &Calibrator,
) -> Result<Vec<f64>, Error> {
    cal.likelihood(points, m)
}

/// The likelihood of the EVENT a date stands for, at each age on a regular
/// `grid`: its calibration, averaged over its inbuilt-age allowance where it
/// has one ([`inbuilt_kernel`]).
///
/// The material may be older than anything on the grid, so the calibration
/// is evaluated past the grid's old end by the allowance's reach before it is
/// smoothed, and only then cut back to the grid.
pub fn event_likelihood(
    grid: &[f64],
    m: &Measurement,
    cal: &Calibrator,
) -> Result<Vec<f64>, Error> {
    let step = if grid.len() > 1 {
        grid[1] - grid[0]
    } else {
        1.0
    };
    let Some(kernel) = inbuilt_kernel(step, m)? else {
        return calibration_likelihood(grid, m, cal);
    };
    let extra = (inbuilt_reach(m) / step).ceil() as usize + 1;
    let wide: Vec<f64> = (0..grid.len() + extra)
        .map(|i| grid[0] + step * i as f64)
        .collect();
    let mut smoothed = convolve_on_grid(&calibration_likelihood(&wide, m, cal)?, &kernel);
    smoothed.truncate(grid.len());
    Ok(smoothed)
}

/// One date's likelihood on `grid`: its calibration, with its reservoir
/// offset applied and its inbuilt-age allowance integrated out
/// ([`event_likelihood`]), and its TPQ/TAQ limiters imposed as a hard
/// truncation. The limiters bound the EVENT, so they cut after the
/// allowance, not before.
pub fn date_likelihood(
    grid: &[f64],
    m: &Measurement,
    bounds: Limits,
    cal: &Calibrator,
) -> Result<Vec<f64>, Error> {
    let mut lik = event_likelihood(grid, m, cal)?;
    let (taq, tpq) = bounds;
    for (l, &t) in lik.iter_mut().zip(grid) {
        if taq.is_some_and(|taq| t < taq) || tpq.is_some_and(|tpq| t > tpq) {
            *l = 0.0;
        }
    }
    Ok(lik)
}

/// The offset scales an outlier model is allowed, as log10(years).
///
/// The second argument of OxCal's Outlier_Model: U(0,4) for General, U(0,3)
/// for Charcoal. Uniform on the LOG scale, so every decade is equally likely a
/// priori, which puts a third of the charcoal prior at ten years or less.
pub fn scale_nodes(model: &str) -> Vec<f64> {
    let top = if model == "charcoal" {
        CHARCOAL_LOG_SCALE_MAX
    } else {
        OUTLIER_LOG_SCALE_MAX
    };
    linspace(0.0, top, OUTLIER_SCALE_NODES)
}

/// One scale's kernel, normalised, on a lag axis that does not depend on the
/// scale -- so kernels for different scales are directly comparable and can be
/// summed or selected between. Index order matches the grid, so a lag of +1
/// cell means the material is one step OLDER than the context.
pub fn outlier_kernel_at_scale(
    step: f64,
    half_width: f64,
    model: &str,
    log_scale: f64,
) -> Vec<f64> {
    let tau = py_pow(10.0, log_scale);
    let kernel: Vec<f64> = if model == "charcoal" {
        // One-sided, and the side matters. The MATERIAL is older than the
        // CONTEXT, so the context's calendar age is YOUNGER -- lower cal BP, a
        // lower grid index -- and the kernel carries its mass at negative lag.
        // At positive lag it would say the context predates the wood found in
        // it, making a residual date look MORE consistent rather than less.
        //
        // The lag axis runs to the WIDEST scale's reach so every scale shares
        // it, but each is truncated at its own 10 scale lengths (OxCal's
        // Exp(1,-10,0)).
        let widest = CHARCOAL_KERNEL_SPAN_TAUS * py_pow(10.0, CHARCOAL_LOG_SCALE_MAX);
        let lags = arange(0.0, widest + step, step);
        let reach = CHARCOAL_KERNEL_SPAN_TAUS * tau;
        let mut kernel: Vec<f64> = lags
            .iter()
            .rev()
            .map(|&lag| {
                if lag <= reach {
                    (-lag / tau).exp()
                } else {
                    0.0
                }
            })
            .collect();
        kernel.extend(std::iter::repeat_n(0.0, lags.len() - 1));
        kernel
    } else {
        arange(-half_width, half_width + step, step)
            .into_iter()
            .map(|lag| {
                let x = lag / tau;
                py_pow(1.0 + x * x / OUTLIER_DF, -(OUTLIER_DF + 1.0) / 2.0)
            })
            .collect()
    };
    let total = sum(&kernel);
    if total > 0.0 {
        kernel.iter().map(|k| k / total).collect()
    } else {
        kernel
    }
}

/// The offset a flagged date is allowed, as a convolution kernel over
/// CALENDAR years, summed over the model's scale prior.
///
/// Two models, because the two ways a date goes wrong are not the same shape:
///
/// "general" -- OxCal's `Outlier_Model("General", T(5), U(0,4), "t")` (Bronk
/// Ramsey 2009): a Student-t offset scaled by 10^u over four decades.
/// Symmetric and very heavy-tailed, so a flagged date says almost nothing
/// about where it sits. The model for a date wrong in an unknown way.
///
/// "charcoal" -- OxCal's `Outlier_Model("Charcoal", Exp(1,-10,0), U(0,3),
/// "t")`: a ONE-SIDED exponential gap, the material having formed some years
/// BEFORE the context and never after, its scale drawn over three decades.
/// This is old wood and reworked material, and the known direction is worth a
/// lot: on an injected residual date it recovered the Start boundary to AD
/// 1292 against the general model's 1201 (clean value 1339), and put the
/// outlier probability at 90% rather than 66%.
///
/// Both are summed over their scale, which APPROXIMATES OxCal: OxCal shares
/// one scale across every date using the model and lets the data constrain
/// it, where summing marginalises it independently per date. The sampler
/// samples the shared scale properly.
pub fn outlier_kernel(step: f64, half_width: f64, model: &str) -> Vec<f64> {
    let mut total: Option<Vec<f64>> = None;
    for log_scale in scale_nodes(model) {
        let component = outlier_kernel_at_scale(step, half_width, model, log_scale);
        total = Some(match total {
            None => component,
            Some(t) => t.iter().zip(&component).map(|(a, b)| a + b).collect(),
        });
    }
    let total = total.expect("a model has scale nodes");
    let norm = sum(&total);
    total.iter().map(|t| t / norm).collect()
}

/// The offset kernel with the scale mixed over `weights`, one per node of
/// [`scale_nodes`]: its posterior once weighed, where [`outlier_kernel`]
/// mixes over the prior.
fn weighted_kernel(step: f64, half_width: f64, model: &str, weights: &[f64]) -> Vec<f64> {
    let mut kernel: Option<Vec<f64>> = None;
    for (&w, log_scale) in weights.iter().zip(scale_nodes(model)) {
        let row = outlier_kernel_at_scale(step, half_width, model, log_scale);
        kernel = Some(match kernel {
            None => row.iter().map(|r| w * r).collect(),
            Some(k) => k.iter().zip(&row).map(|(a, r)| a + w * r).collect(),
        });
    }
    let kernel = kernel.expect("a model has scale nodes");
    let norm = sum(&kernel);
    kernel.iter().map(|k| k / norm).collect()
}

/// `values` smeared by `kernel`, returned on the same grid.
///
/// The kernel is trimmed to the lags that can reach a grid point from a grid
/// point. That is exact rather than an economy -- a longer lag contributes to
/// no on-grid output -- and it matters because these kernels run to
/// 10,000-40,000 years while the grid is a few thousand. Trimming happens AFTER
/// normalisation, so mass the kernel throws off the grid stays thrown off; see
/// [`with_outlier_component`] for why that is the point.
///
/// Python convolves by FFT (`scipy.signal.fftconvolve`) and clips the
/// rounding noise that leaves below zero. This is the direct sum the FFT
/// approximates: equal to it within that noise, about 10^-16 of the largest
/// value, and exact in the tails where the FFT's answer is noise.
pub fn convolve_on_grid(values: &[f64], kernel: &[f64]) -> Vec<f64> {
    let n = values.len();
    if n == 0 {
        return Vec::new();
    }
    let centre = (kernel.len() - 1) / 2;
    let reach = n - 1;
    let start = centre.saturating_sub(reach);
    let stop = (centre + reach + 1).min(kernel.len());
    let trimmed = &kernel[start..stop];
    let offset = centre.min(reach);
    // out[k] = full[offset + k] = sum over i of values[i] * trimmed[offset + k - i],
    // accumulated in increasing i -- the order the plain sum takes -- but
    // value by value, so the inner loop runs along the output and vectorises.
    // Threads take disjoint stretches of the output.
    let threads = std::thread::available_parallelism()
        .map_or(1, usize::from)
        .min((n * trimmed.len()).div_ceil(1 << 22))
        .max(1);
    let per = n.div_ceil(threads);
    let mut out = vec![0.0; n];
    std::thread::scope(|scope| {
        for (chunk, part) in out.chunks_mut(per).enumerate() {
            let first = chunk * per;
            scope.spawn(move || {
                for (i, &v) in values.iter().enumerate() {
                    if v == 0.0 {
                        continue;
                    }
                    // Outputs k with 0 <= offset + k - i < trimmed.len().
                    let k_lo = (i + 1).saturating_sub(offset + 1).max(first);
                    let k_hi = (i + trimmed.len())
                        .saturating_sub(offset)
                        .min(first + part.len());
                    if k_lo >= k_hi {
                        continue;
                    }
                    let lag0 = offset + k_lo - i;
                    let taps = &trimmed[lag0..lag0 + (k_hi - k_lo)];
                    for (o, &t) in part[k_lo - first..k_hi - first].iter_mut().zip(taps) {
                        *o += v * t;
                    }
                }
            });
        }
    });
    for o in &mut out {
        *o = o.max(0.0);
    }
    out
}

/// (mixture, outlier component) for one date, both normalised on `grid`.
/// `scale_weights`, one per node of [`scale_nodes`], mixes the offset over the
/// scale's posterior rather than its prior.
///
/// Integrating out a Bernoulli outlier indicator is exact and costs the
/// solver nothing, because the indicator touches only this date's own
/// likelihood:
///
/// ```text
/// L_mix(t) = (1 - p) * L_inlier(t)  +  p * L_outlier(t)
/// ```
///
/// Every date enters the chain through its likelihood alone, so the message
/// passing is untouched -- the outlier model is a property of a date, not of
/// the inference.
pub fn with_outlier_component(
    grid: &[f64],
    inlier: &[f64],
    prior: f64,
    model: &str,
    scale_weights: Option<&[f64]>,
) -> (Vec<f64>, Vec<f64>) {
    let step = grid[1] - grid[0];
    let half_width = py_max(
        OUTLIER_KERNEL_HALF_WIDTH_YEARS,
        grid[grid.len() - 1] - grid[0],
    );
    let kernel = match scale_weights {
        None => outlier_kernel(step, half_width, model),
        Some(w) => weighted_kernel(step, half_width, model, w),
    };
    let broad = convolve_on_grid(inlier, &kernel);
    // NOT renormalised over the grid. The convolution of two normalised
    // densities is already proper on the real line, and most of its mass lies
    // off this grid -- which is exactly the statement that a flagged date's
    // calendar age could be anywhere. Rescaling what remains to one would
    // inflate the component by the reciprocal of the retained mass and make
    // the outlier probability depend on the grid width: it put the fifteen
    // sound dates of the outlier fixture at 9-14% against OxCal's 0.6-1.5%.
    // The mixture is therefore sub-unit here, the missing mass being the
    // outlier possibility the window does not cover.
    let mixture = inlier
        .iter()
        .zip(&broad)
        .map(|(i, b)| (1.0 - prior) * i + prior * b)
        .collect();
    (mixture, broad)
}

/// One array per date, by id.
pub type ByDate = HashMap<String, Vec<f64>>;

/// Each element's inherited (taq, tpq), by element id.
pub type ElementBounds = HashMap<String, Limits>;

/// A date's own limiters combined with its element's.
pub fn effective_date_bounds(m: &Measurement, element_bounds: &ElementBounds) -> Limits {
    let element = m
        .element_id
        .as_ref()
        .and_then(|e| element_bounds.get(e))
        .copied()
        .unwrap_or((None, None));
    measurement_bounds(m, element)
}

/// A date's (inlier likelihood, outlier component, outlier prior), for
/// reporting its outlier probability.
pub type OutlierPart = (Vec<f64>, Vec<f64>, f64);

/// Every date's likelihood on a grid, its cumulative integral, and the parts
/// needed to report an outlier probability.
#[derive(Debug, Clone, Default)]
pub struct DateTables {
    pub likelihood: HashMap<String, Vec<f64>>,
    pub cumulative: HashMap<String, Vec<f64>>,
    pub outlier_parts: HashMap<String, OutlierPart>,
}

/// Every date's likelihood on `grid`, its cumulative integral, and the parts
/// needed to report an outlier probability.
///
/// Each likelihood is normalised to integrate to one over the grid, so every
/// interval integral lands in [0, 1] and long products of them cannot
/// underflow. A date carrying an outlier prior has its Bernoulli indicator
/// integrated out here ([`with_outlier_component`]), which is exact and leaves
/// the rest of the inference untouched -- the outlier model is a property of a
/// date, not of how the model is solved.
///
/// Shared with the sampler: both solvers need the identical table for their
/// answers to be comparable, and a second copy of the normalisation,
/// truncation and outlier mixture would drift.
///
/// `scale_weights`, {outlier model: weight per node of [`scale_nodes`]}, mixes
/// each flagged date's offset over its model's scale weighed from the data
/// instead of over the prior. The sampler draws the scale itself and never
/// passes it.
///
/// `log_masses`, if given, receives each date's log integral before the
/// normalisation, which the model's evidence needs put back.
pub fn date_tables(
    grid: &[f64],
    measurements: &[Measurement],
    element_bounds: &ElementBounds,
    scale_weights: Option<&std::collections::BTreeMap<String, Vec<f64>>>,
    mut log_masses: Option<&mut HashMap<String, f64>>,
    cal: &Calibrator,
) -> Result<DateTables, Error> {
    let dt = grid[1] - grid[0];
    let mut tables = DateTables::default();
    for m in measurements {
        let mut lik = date_likelihood(grid, m, effective_date_bounds(m, element_bounds), cal)?;
        let total = sum(&lik) * dt;
        if total <= 0.0 || !total.is_finite() {
            return Err(Error::Model(format!(
                "sample '{}' has no probability inside its allowed calendar range",
                m.name_or_id()
            )));
        }
        if let Some(masses) = log_masses.as_deref_mut() {
            masses.insert(m.id.clone(), total.ln());
        }
        for l in &mut lik {
            *l /= total;
        }
        let prior = m.outlier_prior();
        if prior > 0.0 {
            let model_name = m.outlier_model();
            if !OUTLIER_MODELS.contains(&model_name) {
                return Err(Error::Model(format!(
                    "sample '{}' has unknown outlier model '{model_name}'; expected one of {}",
                    m.name_or_id(),
                    OUTLIER_MODELS.join(", ")
                )));
            }
            let weights = scale_weights
                .and_then(|w| w.get(model_name))
                .map(Vec::as_slice);
            let (mixture, broad) = with_outlier_component(grid, &lik, prior, model_name, weights);
            tables
                .outlier_parts
                .insert(m.id.clone(), (lik, broad, prior));
            lik = mixture;
        }
        tables.cumulative.insert(
            m.id.clone(),
            cumsum(&lik).into_iter().map(|c| c * dt).collect(),
        );
        tables.likelihood.insert(m.id.clone(), lik);
    }
    Ok(tables)
}

/// [`date_tables`]' likelihoods and cumulative integrals for a model with no
/// data: every date equally likely anywhere its TPQ/TAQ limiters allow,
/// normalised as `date_tables` does. What the model asserts before it sees a
/// date, which the evidence is measured against. `log_masses` receives each
/// date's log allowed length.
pub fn flat_date_tables(
    grid: &[f64],
    measurements: &[Measurement],
    element_bounds: &ElementBounds,
    mut log_masses: Option<&mut HashMap<String, f64>>,
) -> Result<(ByDate, ByDate), Error> {
    let dt = grid[1] - grid[0];
    let mut likelihood = HashMap::new();
    let mut cumulative = HashMap::new();
    for m in measurements {
        let (taq, tpq) = effective_date_bounds(m, element_bounds);
        let allowed: Vec<f64> = grid
            .iter()
            .map(|&t| {
                if taq.is_some_and(|taq| t < taq) || tpq.is_some_and(|tpq| t > tpq) {
                    0.0
                } else {
                    1.0
                }
            })
            .collect();
        let total = sum(&allowed) * dt;
        if total <= 0.0 {
            return Err(Error::Model(format!(
                "sample '{}' has no allowed calendar range",
                m.name_or_id()
            )));
        }
        if let Some(masses) = log_masses.as_deref_mut() {
            masses.insert(m.id.clone(), total.ln());
        }
        let flat: Vec<f64> = allowed.iter().map(|a| a / total).collect();
        cumulative.insert(
            m.id.clone(),
            cumsum(&flat).into_iter().map(|c| c * dt).collect(),
        );
        likelihood.insert(m.id.clone(), flat);
    }
    Ok((likelihood, cumulative))
}

/// (youngest, oldest) calendar age any date's likelihood reaches, cal BP, and
/// the (youngest, oldest) the curves in use cover.
pub fn dates_extent(
    measurements: &[Measurement],
    element_bounds: &ElementBounds,
    cal: &Calibrator,
) -> Result<(f64, f64, f64, f64), Error> {
    let (mut curve_lo, mut curve_hi) = (f64::INFINITY, f64::NEG_INFINITY);
    let (mut lo, mut hi) = (f64::INFINITY, f64::NEG_INFINITY);
    for m in measurements {
        let curve = cal.curve_of(m)?;
        let ages = curve.cal_ages();
        curve_lo = py_min(curve_lo, ages[0]);
        curve_hi = py_max(curve_hi, ages[ages.len() - 1]);
        let (cal_ages, prob) = cal.calibrate(m)?;
        let peak = prob.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let threshold = peak * SUPPORT_THRESHOLD;
        if let (Some(first), Some(last)) = (
            prob.iter().position(|&p| p >= threshold),
            prob.iter().rposition(|&p| p >= threshold),
        ) {
            // An inbuilt-age allowance lets the event be younger than its
            // material, so the grid reaches that much further on the young
            // side.
            lo = py_min(lo, cal_ages[first] - inbuilt_reach(m));
            hi = py_max(hi, cal_ages[last]);
        }
        let (taq, tpq) = effective_date_bounds(m, element_bounds);
        if let Some(taq) = taq {
            lo = py_max(lo, taq);
        }
        if let Some(tpq) = tpq {
            hi = py_min(hi, tpq);
        }
    }
    if !lo.is_finite() || !hi.is_finite() || hi <= lo {
        return Err(Error::Model(
            "the dates have no overlapping calendar range to solve over".into(),
        ));
    }
    Ok((lo, hi, curve_lo, curve_hi))
}

/// Grid points the memory budget allows for a chain of `num_groups` groups:
/// the full count up to GROUPS_AT_FULL_RESOLUTION, fewer beyond.
pub fn point_budget(num_groups: usize) -> f64 {
    MAX_GRID_POINTS / py_max(1.0, (num_groups as f64 / GROUPS_AT_FULL_RESOLUTION).sqrt())
}

/// `lo + step * arange(count)` with count from the span, not from the end
/// point: an explicit count rather than `np.arange(lo, hi + step, step)`,
/// whose length depends on floating-point rounding at the end point.
fn grid_over(lo: f64, hi: f64, step: f64) -> Vec<f64> {
    let count = ((hi - lo) / step - 1e-9).ceil() as usize + 1;
    (0..count).map(|i| lo + step * i as f64).collect()
}

/// A calendar grid wide enough for the dates and the boundaries either side of
/// them, at the finest step the memory budget allows.
///
/// One M x M array of doubles is 32 MB at M = 2000 and a transfer matrix is
/// held per group, so the point count scales down as the model grows: a long
/// sequence gets a coarser calendar step rather than an out-of-memory kill.
///
/// This is the FIRST pass's grid, padded blind for the boundaries' tails; the
/// solve then narrows it to where the posterior is ([`refined_grid`]).
pub fn build_grid(
    measurements: &[Measurement],
    element_bounds: &ElementBounds,
    num_groups: usize,
    cal: &Calibrator,
) -> Result<Vec<f64>, Error> {
    let (lo, hi, curve_lo, curve_hi) = dates_extent(measurements, element_bounds, cal)?;
    let pad = py_max(MIN_BOUNDARY_PAD_YEARS, BOUNDARY_WINDOW_FACTOR * (hi - lo));
    let lo = py_max(curve_lo, lo - pad);
    let hi = py_min(curve_hi, hi + pad);
    let budget = point_budget(num_groups);
    let step = reporting_aligned_step(py_max(MIN_GRID_STEP_YEARS, (hi - lo) / budget));
    Ok(grid_over(lo, hi, step))
}

/// The second pass's grid, or None when it would not be meaningfully finer.
///
/// The first pass pads the dates by six times their own width for the
/// boundaries' tails, blind, so on a long model most of its points lie where
/// no posterior is: a twenty-phase sequence over 1,500 years was solved at a
/// 15-year step across 14,800 years. Having solved it once, the tails are
/// known. This grid covers every first-pass posterior in `densities` out to
/// REFINE_TAIL_MASS in either tail and every date's own likelihood support,
/// with a margin, at the finest step the same point budget allows. The solve
/// passes the Boundaries' alone: every date lies between the outermost two,
/// so no date's tail reaches past theirs.
///
/// The dates' supports are included so each date's likelihood is normalised,
/// and its outlier component convolved, over all of it, exactly as on the
/// first grid; only the empty padding goes.
pub fn refined_grid(
    grid: &[f64],
    densities: &[Vec<f64>],
    measurements: &[Measurement],
    element_bounds: &ElementBounds,
    num_groups: usize,
    cal: &Calibrator,
) -> Result<Option<Vec<f64>>, Error> {
    let m = grid.len();
    let (mut lo, mut hi) = (f64::INFINITY, f64::NEG_INFINITY);
    for density in densities {
        let total = sum(density);
        if total <= 0.0 {
            continue;
        }
        let cdf: Vec<f64> = cumsum(density).into_iter().map(|c| c / total).collect();
        lo = py_min(
            lo,
            grid[(m - 1).min(searchsorted_left(&cdf, REFINE_TAIL_MASS))],
        );
        hi = py_max(
            hi,
            grid[(m - 1).min(searchsorted_left(&cdf, 1.0 - REFINE_TAIL_MASS))],
        );
    }
    let (dates_lo, dates_hi, _, _) = dates_extent(measurements, element_bounds, cal)?;
    let (lo, hi) = (py_min(lo, dates_lo), py_max(hi, dates_hi));
    if !lo.is_finite() || !hi.is_finite() || hi <= lo {
        return Ok(None);
    }
    let first_step = grid[1] - grid[0];
    let margin = py_max(
        REFINE_MARGIN_STEPS * first_step,
        REFINE_MARGIN_FRACTION * (hi - lo),
    );
    let (lo, hi) = (
        py_max(grid[0], lo - margin),
        py_min(grid[m - 1], hi + margin),
    );
    let step = reporting_aligned_step(py_max(
        REFINED_MIN_STEP_YEARS,
        (hi - lo) / point_budget(num_groups),
    ));
    if step * REFINE_MIN_GAIN > first_step {
        return Ok(None);
    }
    Ok(Some(grid_over(lo, hi, step)))
}

/// The smallest calendar step >= `step` that tiles REPORTING_STEP_YEARS
/// exactly -- a divisor of it below, a multiple of it above.
///
/// The raw step is (range / point budget), 1.015 years on a typical model,
/// and a 5-year reporting bin then holds four grid cells or five depending
/// on where it falls. That is not cosmetic: the mass per bin swung between
/// 0.81 and 1.04 of its true value on a smooth posterior, and an HPD region
/// built by ranking bins was picking them on that sawtooth. The reported
/// ranges now integrate the density exactly, which removes the error whatever
/// the step; aligning the step as well keeps the internal cells and the
/// reported bins nested, so a bin is always a whole number of cells.
pub fn reporting_aligned_step(step: f64) -> f64 {
    let reporting = REPORTING_STEP_YEARS;
    if step >= reporting {
        return (step / reporting - 1e-9).ceil() * reporting;
    }
    reporting / (reporting / step + 1e-9).floor()
}

/// [`grid_over`], for the evidence's grid.
pub(crate) fn regular_grid(lo: f64, hi: f64, step: f64) -> Vec<f64> {
    grid_over(lo, hi, step)
}
