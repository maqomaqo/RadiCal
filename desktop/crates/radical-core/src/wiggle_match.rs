//! Wiggle matching -- OxCal's D_Sequence and V_Sequence: a series of dates
//! separated by KNOWN gaps (tree rings, varves, any sampled sequence), solved
//! in closed form. Port of `services/wiggle_match.py`.
//!
//! # Why this is its own module
//!
//! It shares none of the chain solver's machinery. There are no transfer
//! matrices, no message passing, no span density and no boundaries, because
//! there is only ONE free parameter: the calendar age at the series reference
//! point. Every member's age follows from it and its own measured gap.
//!
//! # The maths
//!
//! Writing t for the calendar age (cal BP) at the reference point and g_i for
//! member i's `ring_offset`,
//!
//! ```text
//! p(t)  proportional to  product over i of  L_i(t + g_i)
//! ```
//!
//! with each L_i the ordinary calibration likelihood -- curve error, Delta-R
//! and any TPQ/TAQ limiter included, exactly as the chain solver builds it.
//! Evaluate every member on a shared grid shifted by its own gap, sum the logs,
//! exponentiate. O(n*M), deterministic, and exact up to the grid step.
//!
//! Consequences worth stating rather than leaving to be rediscovered:
//!
//! - There is no sampler and nothing to converge, so none of the
//!   qualifications the durations carry about Monte Carlo error apply here.
//! - The gaps are HARD. A D_Sequence asserts the ring counts are right; if they
//!   are not, the model has no way to say so except through the agreement
//!   index. That is why the combination index is a required output and not a
//!   nicety.
//! - NO OUTLIER MODEL. An outlier model gives one date room to sit apart from
//!   the rest, and a wiggle match has no room to give -- every member is the
//!   same age plus a fixed number. A flagged member is REFUSED rather than
//!   matched as though it were not flagged. That is a statement about this
//!   app, not about OxCal, which does allow Outlier() inside a D_Sequence.
//!
//! # The sign convention, which is the thing most likely to be wrong
//!
//! Rings count forward in time; cal BP counts backward. A flipped sign here
//! does not produce nonsense -- it produces a tight range in the wrong place,
//! which is indistinguishable from a good answer without an independent check.
//!
//! So, once: `ring_offset` is years BEFORE the reference member. It is 0 AT
//! the reference and INCREASES INTO THE PAST, so a member's calendar age is the
//! reference age PLUS its offset: t_i = t + g_i.
//!
//! The reference is the point g = 0, whether or not a member sits exactly
//! there; `reference_sample_id` on the element records which member the entry
//! helper measured from, for labelling.
//!
//! # Why this builds its own grid
//!
//! The chain solver pads the dates' support by max(800, 6 * support), because
//! BOUNDARIES genuinely range far beyond the outermost date. A wiggle match
//! has no boundaries at all, so at a fixed point budget that padding is paid
//! for in resolution. So the grid here is the series' own support plus a
//! modest FIXED pad -- fixed because the only reason to reach past the support
//! is to keep a reported range off the window edge -- found in two passes,
//! coarse then fine.

use crate::Error;
use crate::agreement;
use crate::exact_chain::summary::{Posterior, summarize};
use crate::exact_chain::tables::{ElementBounds, date_likelihood};
use crate::exact_chain::{MIN_GRID_STEP_YEARS, SUPPORT_THRESHOLD, convolve_on_grid};
use crate::measurement::{Calibrator, Measurement};
use crate::model_tree::{Element, Limits, WiggleGroup, inherited_bounds};
use crate::numeric::{arange, py_max, py_min, sum};
use crate::rng::Generator;
use crate::stratigraphy::measurement_bounds;

/// Points the coarse first pass is allowed. Its only job is to find where the
/// product has mass, so it can be coarse; the answer comes from the second.
pub const COARSE_GRID_POINTS: f64 = 4000.0;
/// Calendar years kept either side of the located support in the fine pass.
/// FIXED rather than proportional to the support: there is no boundary to
/// accommodate, and the only reason to reach beyond the support at all is so a
/// reported range is not clipped at the window edge.
pub const FINE_PAD_YEARS: f64 = 200.0;
/// Points the fine pass is allowed before the step is coarsened. Only reached
/// by a series whose support is thousands of years wide, i.e. one that has not
/// resolved -- and there a fine step would be reporting resolution the answer
/// does not have.
pub const MAX_FINE_GRID_POINTS: f64 = 60000.0;
/// A gap uncertainty is truncated at this many sigma when its kernel is built.
pub const GAP_KERNEL_SIGMAS: f64 = 5.0;

/// A member: the date, its gap, the gap's uncertainty.
type Member<'a> = (&'a Measurement, f64, f64);

/// Where this member puts the REFERENCE age, in cal BP: its own likelihood's
/// support shifted back by its gap.
fn member_support(m: &Measurement, offset: f64, cal: &Calibrator) -> Result<(f64, f64), Error> {
    let (ages, prob) = cal.calibrate(m)?;
    let peak = prob.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let threshold = peak * SUPPORT_THRESHOLD;
    Ok(
        match (
            prob.iter().position(|&p| p >= threshold),
            prob.iter().rposition(|&p| p >= threshold),
        ) {
            (Some(a), Some(b)) => (ages[a] - offset, ages[b] - offset),
            _ => (ages[0] - offset, ages[ages.len() - 1] - offset),
        },
    )
}

/// The calendar range every member's curve can be read over.
fn curve_limits(members: &[Member], cal: &Calibrator) -> Result<(f64, f64), Error> {
    let (mut lo, mut hi) = (f64::NEG_INFINITY, f64::INFINITY);
    for (m, _, _) in members {
        let ages = cal.curve_of(m)?.cal_ages();
        lo = py_max(lo, ages[0]);
        hi = py_min(hi, ages[ages.len() - 1]);
    }
    Ok((lo, hi))
}

/// A gap's own uncertainty as a convolution kernel over calendar years -- what
/// makes a V_Sequence a V_Sequence.
///
/// Marginalising G_i ~ N(g_i, sigma_i) turns member i's shifted likelihood
/// into that likelihood convolved with this, so a D_Sequence is exactly the
/// sigma = 0 case of the same code path rather than a separate one.
pub fn gap_kernel(step: f64, sigma: f64) -> Vec<f64> {
    let reach = py_max(step, GAP_KERNEL_SIGMAS * sigma);
    let kernel: Vec<f64> = arange(-reach, reach + step, step)
        .into_iter()
        .map(|lag| {
            let z = lag / sigma;
            (-0.5 * (z * z)).exp()
        })
        .collect();
    let total = sum(&kernel);
    if total > 0.0 {
        kernel.iter().map(|k| k / total).collect()
    } else {
        kernel
    }
}

fn shifted_likelihood(
    grid: &[f64],
    member: &Member,
    bounds: Limits,
    cal: &Calibrator,
) -> Result<Vec<f64>, Error> {
    let (m, offset, _) = *member;
    let shifted: Vec<f64> = grid.iter().map(|g| g + offset).collect();
    date_likelihood(&shifted, m, bounds, cal)
}

/// Each member's log likelihood as a function of the REFERENCE age on `grid`,
/// scaled to peak at zero; None when some member has no support on it.
///
/// Each member is evaluated at grid + its own gap, which is the whole of the
/// model: shift, multiply. Where a gap carries an uncertainty the shifted
/// likelihood is smeared by it first ([`gap_kernel`]), which is the only
/// difference between a D_Sequence and a V_Sequence.
fn log_terms(
    grid: &[f64],
    members: &[Member],
    bounds: &[Limits],
    cal: &Calibrator,
) -> Result<Option<Vec<Vec<f64>>>, Error> {
    let step = grid[1] - grid[0];
    let mut terms = Vec::with_capacity(members.len());
    for (member, &b) in members.iter().zip(bounds) {
        let mut shifted = shifted_likelihood(grid, member, b, cal)?;
        if member.2 > 0.0 {
            shifted = convolve_on_grid(&shifted, &gap_kernel(step, member.2));
        }
        let peak = shifted.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        if peak <= 0.0 {
            // Nowhere on this window can this member be. None rather than an
            // error: the caller decides, and the coarse pass reaching here
            // simply means the window is in the wrong place.
            return Ok(None);
        }
        terms.push(shifted.iter().map(|s| (s / peak).ln()).collect());
    }
    Ok(Some(terms))
}

/// The rows of `terms` added one after another, as `np.sum(terms, axis=0)`.
fn summed(terms: &[&Vec<f64>], len: usize) -> Vec<f64> {
    let mut out = match terms.first() {
        Some(first) => (*first).clone(),
        None => return vec![0.0; len],
    };
    for t in &terms[1..] {
        for (o, v) in out.iter_mut().zip(t.iter()) {
            *o += v;
        }
    }
    out
}

/// log of the product over members, as a function of the REFERENCE age.
fn log_product(
    grid: &[f64],
    members: &[Member],
    bounds: &[Limits],
    cal: &Calibrator,
) -> Result<Vec<f64>, Error> {
    Ok(match log_terms(grid, members, bounds, cal)? {
        None => vec![f64::NEG_INFINITY; grid.len()],
        Some(terms) => summed(&terms.iter().collect::<Vec<_>>(), grid.len()),
    })
}

fn density(log_weights: &[f64]) -> Vec<f64> {
    let peak = log_weights
        .iter()
        .copied()
        .filter(|v| v.is_finite())
        .fold(f64::NEG_INFINITY, f64::max);
    log_weights
        .iter()
        .map(|&v| if v.is_finite() { (v - peak).exp() } else { 0.0 })
        .collect()
}

/// Member `index`'s own age, t_i = t + G_i, as a density on grid + g_i.
///
/// With a COUNTED gap (sigma 0) G_i is g_i exactly, and this is the
/// reference's density moved bodily -- one distribution translated.
///
/// With a MEASURED gap it is not. Marginalising every other gap as the
/// reference does, then the reference age itself, leaves
///
/// ```text
/// p(t_i)  proportional to  L_i(t_i) * (R_-i conv N(0, sigma_i))(t_i - g_i)
/// ```
///
/// where R_-i is the product of every OTHER member's smeared term. The
/// member's own measurement stays sharp and what the rest of the series says
/// about it is blurred by how well its position in the series is known. So
/// the member gets a width of its own: usually wider than the reference, but
/// narrower where its own measurement sits on a steep stretch of curve and
/// pins it harder than the series pins gap 0.
fn member_density(
    grid: &[f64],
    index: usize,
    members: &[Member],
    terms: &[Vec<f64>],
    bounds: &[Limits],
    reference: &[f64],
    cal: &Calibrator,
) -> Result<Vec<f64>, Error> {
    let error = members[index].2;
    if error <= 0.0 {
        return Ok(reference.to_vec());
    }
    let step = grid[1] - grid[0];
    let others: Vec<&Vec<f64>> = terms
        .iter()
        .enumerate()
        .filter(|(k, _)| *k != index)
        .map(|(_, t)| t)
        .collect();
    let rest = if others.is_empty() {
        vec![1.0; grid.len()]
    } else {
        density(&summed(&others, grid.len()))
    };
    let smeared_rest = convolve_on_grid(&rest, &gap_kernel(step, error));
    let own = shifted_likelihood(grid, &members[index], bounds[index], cal)?;
    let out: Vec<f64> = own.iter().zip(&smeared_rest).map(|(a, b)| a * b).collect();
    // The two integrate to the same mass analytically; only a window that
    // clips the smear could empty this, and the translated reference is the
    // honest fallback there rather than an exception.
    Ok(if sum(&out) > 0.0 {
        out
    } else {
        reference.to_vec()
    })
}

fn refused(message: &str) -> Error {
    Error::Model(message.to_owned())
}

const NO_FIT: &str = "no calendar age fits every member of this series at the gaps given. \
     Either the gaps are wrong, or one of the dates does not belong to the series";

/// The reference age's calendar grid: the series' own support plus
/// FINE_PAD_YEARS, at MIN_GRID_STEP_YEARS, located by a coarse first pass.
///
/// The coarse pass runs over the UNION of what each member alone allows,
/// because the intersection can be empty on a series whose gaps are wrong --
/// and "these ring counts cannot be right" is the answer that case deserves,
/// not an exception from an empty range.
fn build_grid(members: &[Member], bounds: &[Limits], cal: &Calibrator) -> Result<Vec<f64>, Error> {
    let offsets: Vec<f64> = members.iter().map(|m| m.1).collect();
    let supports = members
        .iter()
        .map(|(m, offset, _)| member_support(m, *offset, cal))
        .collect::<Result<Vec<_>, _>>()?;
    let lo = supports
        .iter()
        .map(|s| s.0)
        .reduce(py_min)
        .expect("members");
    let hi = supports
        .iter()
        .map(|s| s.1)
        .reduce(py_max)
        .expect("members");

    // Every member is read at grid + its own gap, so the window the CURVE has
    // to cover is wider than the reference's own by the series length.
    // Getting this wrong truncates the oldest member's likelihood silently --
    // the curve clamps rather than raising -- so it is enforced here rather
    // than assumed.
    let (curve_lo, curve_hi) = curve_limits(members, cal)?;
    let lo = py_max(
        lo,
        curve_lo - offsets.iter().copied().reduce(py_min).expect("members"),
    );
    let hi = py_min(
        hi,
        curve_hi - offsets.iter().copied().reduce(py_max).expect("members"),
    );
    if hi <= lo {
        return Err(refused(
            "the series reaches outside the calibration curve: with these gaps there is no \
             calendar age at which every member can be read off it",
        ));
    }

    let coarse_step = py_max(MIN_GRID_STEP_YEARS, (hi - lo) / COARSE_GRID_POINTS);
    let coarse = arange(lo, hi + coarse_step, coarse_step);
    let weights = density(&log_product(&coarse, members, bounds, cal)?);
    let peak = weights.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    if peak <= 0.0 {
        return Err(refused(NO_FIT));
    }
    let threshold = peak * SUPPORT_THRESHOLD;
    let first = weights
        .iter()
        .position(|&w| w >= threshold)
        .expect("the peak");
    let last = weights
        .iter()
        .rposition(|&w| w >= threshold)
        .expect("the peak");
    let fine_lo = py_max(lo, coarse[first] - FINE_PAD_YEARS);
    let fine_hi = py_min(hi, coarse[last] + FINE_PAD_YEARS);
    let step = py_max(
        MIN_GRID_STEP_YEARS,
        (fine_hi - fine_lo) / MAX_FINE_GRID_POINTS,
    );
    let mut grid = arange(fine_lo, fine_hi + step, step);
    if grid.len() < 2 {
        // A support narrower than one grid step. Widen rather than fail: the
        // answer is real and simply very tight.
        grid = arange(fine_lo - step, fine_hi + 2.0 * step, step);
    }
    Ok(grid)
}

/// One member as reported beside the series.
#[derive(Debug, Clone, PartialEq)]
pub struct MemberRecord {
    pub id: String,
    pub name: String,
    pub ring_offset: f64,
    /// None for a counted gap.
    pub ring_offset_error: Option<f64>,
    pub agreement_index: Option<f64>,
}

/// One wiggle-matched series, solved.
#[derive(Debug, Clone, PartialEq)]
pub struct WiggleSolution {
    /// The age at ring_offset 0.
    pub reference: Posterior,
    /// Each member's posterior, in the series' order, youngest first.
    pub measurements: Vec<(String, Posterior)>,
    /// The overall index over all members.
    pub combination_agreement: Option<f64>,
    /// The pass mark it is quoted against.
    pub combination_threshold: f64,
    pub members: Vec<MemberRecord>,
    pub gap_units: String,
    pub reference_sample_id: Option<String>,
    /// The outermost gap minus the innermost.
    pub series_span: f64,
    /// (lower, upper, step).
    pub grid: (f64, f64, f64),
}

/// Solve one wiggle-matched series.
///
/// Each member's posterior is the reference's shifted by its own gap -- one
/// distribution translated rather than n results, which every caller that
/// renders them has to say -- except where the gap was measured rather than
/// counted, which gives that member a width of its own.
///
/// `resolution` is the calendar step the HPD ranges are reported at, as in the
/// chain solver.
pub fn solve(
    measurements: &[Measurement],
    group: &WiggleGroup,
    root: Option<&Element>,
    num_samples: usize,
    seed: u64,
    resolution: f64,
    cal: &Calibrator,
) -> Result<WiggleSolution, Error> {
    let element_bounds: ElementBounds = root.map(inherited_bounds).unwrap_or_default();
    let mut members: Vec<Member> = Vec::new();
    for (date_id, offset, error) in &group.members {
        let Some(m) = measurements.iter().find(|m| m.id == *date_id) else {
            return Err(Error::Model(format!(
                "the wiggle match names a date ('{date_id}') that is not in this analysis"
            )));
        };
        members.push((m, *offset, *error));
    }
    if members.is_empty() {
        return Err(refused(
            "the wiggle match has no dates in it, so there is nothing to match",
        ));
    }

    // Held here as well as at every edge (validate_ordinates), because this is
    // the function that would otherwise drop the setting: the likelihoods
    // below are the plain calibration, where an outlier model lives one level
    // up in the chain solver's date tables. A member flagged as a possible
    // outlier would be matched as though it were not, and nothing on the page
    // would say so.
    let mut flagged: Vec<&str> = members
        .iter()
        .filter(|(m, _, _)| m.outlier_prior() > 0.0)
        .map(|(m, _, _)| m.label())
        .collect();
    if !flagged.is_empty() {
        let count = flagged.len();
        flagged.sort_unstable();
        flagged.truncate(4);
        return Err(Error::Model(format!(
            "{} carr{} an outlier model, which a wiggle match cannot apply: its gaps are \
             asserted, so there is nothing for a date to be an outlier from. A member that does \
             not belong shows up in the agreement index instead",
            flagged.join(", "),
            if count == 1 { "ies" } else { "y" }
        )));
    }

    let bounds: Vec<Limits> = members
        .iter()
        .map(|(m, _, _)| {
            let element = m
                .element_id
                .as_ref()
                .and_then(|e| element_bounds.get(e))
                .copied()
                .unwrap_or((None, None));
            measurement_bounds(m, element)
        })
        .collect();

    let grid = build_grid(&members, &bounds, cal)?;
    let terms = log_terms(&grid, &members, &bounds, cal)?;
    let reference_density = match &terms {
        Some(t) => density(&summed(&t.iter().collect::<Vec<_>>(), grid.len())),
        None => vec![0.0; grid.len()],
    };
    if sum(&reference_density) <= 0.0 {
        return Err(refused(NO_FIT));
    }
    let terms = terms.expect("a density with mass came from the terms");

    let mut rng = Generator::new(seed);
    let reference = summarize(&grid, &reference_density, &mut rng, num_samples, resolution)?;

    let mut results = Vec::new();
    let mut records = Vec::new();
    let mut indices = Vec::new();
    for (index, member) in members.iter().enumerate() {
        let (m, offset, error) = *member;
        let shifted_grid: Vec<f64> = grid.iter().map(|g| g + offset).collect();
        // For a counted gap, the SAME density on a grid moved bodily by this
        // member's gap. Not a separate inference: summarize normalises and
        // trims identically whatever the shift, so a member's plotted curve is
        // the reference's translated, which is exactly the claim the model
        // makes. A measured gap widens its member; see member_density.
        let own = member_density(
            &grid,
            index,
            &members,
            &terms,
            &bounds,
            &reference_density,
            cal,
        )?;
        let mut summary = summarize(&shifted_grid, &own, &mut rng, num_samples, resolution)?;
        let agreement =
            agreement::agreement_index(m, &summary.probability_distribution, None, None, cal)?;
        summary.agreement_index = agreement;
        results.push((m.id.clone(), summary));
        indices.push(agreement);
        records.push(MemberRecord {
            id: m.id.clone(),
            name: m.label().to_owned(),
            ring_offset: offset,
            ring_offset_error: (error != 0.0).then_some(error),
            agreement_index: agreement,
        });
    }

    let offsets: Vec<f64> = members.iter().map(|m| m.1).collect();
    Ok(WiggleSolution {
        reference,
        measurements: results,
        combination_agreement: agreement::overall_agreement(&indices, 0),
        // OxCal's A_n, with n counting every member.
        combination_threshold: agreement::combination_threshold(members.len()),
        members: records,
        gap_units: group.gap_units.clone(),
        reference_sample_id: group.reference_sample_id.clone(),
        series_span: offsets.iter().copied().reduce(py_max).expect("members")
            - offsets.iter().copied().reduce(py_min).expect("members"),
        grid: (grid[0], grid[grid.len() - 1], grid[1] - grid[0]),
    })
}
