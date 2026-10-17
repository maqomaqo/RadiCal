//! The prior's terms over pairs of Boundaries: a group's span factor, the
//! UniformSpanPrior correction over the outermost pair, and the limits factor
//! it carries.

use std::collections::HashMap;

use crate::exact_chain::tables::ElementBounds;
use crate::exact_chain::{BOUNDARY_TIE_WEIGHT, LIMITS_ROUNDING_YEARS, SUPPORT_THRESHOLD};
use crate::matrix::Matrix;
use crate::numeric::{py_max, py_min};
use crate::special::gammaln;

/// `numpy.logaddexp`: log(exp(x) + exp(y)) without overflow, as NumPy forms it.
pub fn logaddexp(x: f64, y: f64) -> f64 {
    if x == y {
        // Handles infinities of the same sign without warnings.
        return x + std::f64::consts::LN_2;
    }
    let tmp = x - y;
    if tmp > 0.0 {
        x + (-tmp).exp().ln_1p()
    } else if tmp <= 0.0 {
        y + tmp.exp().ln_1p()
    } else {
        // NaNs
        tmp
    }
}

/// A group's span factor, in the log, indexed [younger, older]: -K log(span)
/// where the older boundary is older, log BOUNDARY_TIE_WEIGHT where the two
/// share a cell, -inf where they cross.
///
/// Two boundaries may share a cell at half weight. In the continuum their
/// meeting is a set of measure zero, and on a grid the sum over ordered pairs
/// is the integral over a triangle, whose diagonal the trapezoid rule weighs
/// by a half -- the same rule as a date on a boundary (the interval integral).
/// Keeping them a cell apart instead drops that edge altogether, an error of
/// first order in the step that grows with the number of boundaries: on a
/// test sequence of ten, the modelled Boundaries moved 0.035 in the
/// probability of being older than a given year between a 1.25-year step and
/// the converged answer, 0.0065 with the half. Counting the tie whole is as
/// wrong the other way. The span correction counts arrangements the same way
/// ([`log_interior_placements`]), so ties add nothing it does not divide out
/// -- the reason they were once forbidden.
///
/// The span factor at a shared cell is the tie weight alone: the dates'
/// likelihoods there stand in for span^(-K) prod F.
pub fn boundary_pair_term(grid: &[f64], num_dates: usize) -> Matrix {
    let m = grid.len();
    // Python's -K is an integer, so -0 is 0, not -0.0.
    let k = -(num_dates as i64) as f64;
    let mut term = Matrix::from_fn(m, m, |y, o| {
        let span = grid[o] - grid[y];
        if span > 0.0 {
            k * span.ln()
        } else {
            f64::NEG_INFINITY
        }
    });
    let tie = BOUNDARY_TIE_WEIGHT.ln();
    for i in 0..m {
        term.set(i, i, tie);
    }
    term
}

/// log of the number of ways `num_interior` ordered boundaries fit inside a
/// span of `span_cells` grid cells, each TIE between neighbours -- two
/// boundaries in one cell, an interior one on an end included -- counting a
/// half (BOUNDARY_TIE_WEIGHT). -inf for a negative span.
///
/// The exact discrete version of the volume the UniformSpanPrior correction
/// divides out, and a COUNT rather than a power -- see
/// [`span_prior_correction`] for why a power is wrong on a grid. Sorting the
/// arrangements by how many of the k + 1 gaps between neighbours are empty, z
/// of them:
///
/// ```text
/// W(j, k) = sum over z of  C(k + 1, z) 2^-z C(j - 1, k - z)     j >= 1
/// W(0, k) = 2^-k
/// ```
///
/// W(0, k) is every boundary in one cell, k + 1 ties, counted at twice their
/// weight: the outer pair's own tie is the end of the trapezoid over the span,
/// and dividing by it would undo that half.
///
/// Counted with half-weight ties because that is how the solvers place
/// boundaries ([`boundary_pair_term`]): the count has to be of the same
/// arrangements the posterior sums over, or the correction leaves the excess
/// in. Strictly separated boundaries -- C(j - 1, k) -- converge on the
/// continuum's j^k / k! only to first order in the step, and that error
/// reached the answers. Half-weight ties make it second order.
///
/// Shared with the sampler so the two solvers cannot drift apart. Defined only
/// up to a constant factor, like the correction itself: the 1/num_interior! of
/// the continuum expression is absorbed into the posterior's normalisation.
pub fn log_interior_placements(span_cells: &[f64], num_interior: usize) -> Vec<f64> {
    let k = num_interior;
    let kf = k as f64;
    let mut out: Vec<f64> = span_cells
        .iter()
        .map(|&j| {
            if j == 0.0 {
                -kf * 2.0_f64.ln()
            } else {
                f64::NEG_INFINITY
            }
        })
        .collect();
    let some: Vec<usize> = (0..span_cells.len())
        .filter(|&i| span_cells[i] >= 1.0)
        .collect();
    if some.is_empty() {
        return out;
    }
    let tie = BOUNDARY_TIE_WEIGHT.ln();
    let mut total: Vec<f64> = Vec::new();
    for z in 0..=k {
        let m = (k - z) as f64; // gaps left to fill
        let zf = z as f64;
        let constant = gammaln(kf + 2.0) - gammaln(zf + 1.0) - gammaln(kf + 2.0 - zf) + zf * tie;
        let term: Vec<f64> = some
            .iter()
            .map(|&i| {
                let js = span_cells[i];
                if js - 1.0 >= m {
                    constant + gammaln(js) - gammaln(m + 1.0) - gammaln(js - m)
                } else {
                    f64::NEG_INFINITY
                }
            })
            .collect();
        total = if z == 0 {
            term
        } else {
            total
                .iter()
                .zip(&term)
                .map(|(&a, &b)| logaddexp(a, b))
                .collect()
        };
    }
    for (&i, v) in some.iter().zip(total) {
        out[i] = v;
    }
    out
}

/// (oldest, youngest) calendar age a boundary may take, in cal BP, as
/// infinities where nothing constrains it.
///
/// The llim/ulim of Bronk Ramsey 2008 A.30-A.31. Only REAL constraints count --
/// a TPQ or TAQ on the Boundary or on an element enclosing it. The solver's
/// grid is not used even though it bounds the answer numerically: it is padded
/// by six times the dates' own width for the boundaries' benefit, so treating
/// its edges as model statements makes the factor an artefact of that padding.
/// Measured on a single unconstrained phase, the effect halved with each
/// doubling of the window (+1.20 years at 600, +0.13 at 4,800), heading for
/// nothing.
///
/// An unconstrained boundary is unbounded, so g is infinite, 1/g is constant in
/// the span, and it drops out of the posterior -- which is why an
/// unconstrained model gets no limits factor rather than a small arbitrary one.
pub fn boundary_limits(element_bounds: &ElementBounds, boundary_id: &str) -> (f64, f64) {
    let (taq, tpq) = element_bounds
        .get(boundary_id)
        .copied()
        .unwrap_or((None, None));
    (
        tpq.unwrap_or(f64::INFINITY),
        taq.unwrap_or(f64::NEG_INFINITY),
    )
}

/// The limits factor as a function of the span alone, for any `spans`; see
/// [`log_limits_factor`] for the derivation and the four terms.
///
/// Split out so the sampler can ask for a 1-D row indexed by span without
/// allocating the M x M matrix the exact solver needs, while both share one
/// definition of the terms and their sign.
pub fn log_limits_factor_of_span(
    spans: &[f64],
    oldest_limits: (f64, f64),
    youngest_limits: (f64, f64),
) -> Option<Vec<f64>> {
    let (hi_old, lo_old) = oldest_limits;
    let (hi_yng, lo_yng) = youngest_limits;
    let first = hi_old.is_finite() && lo_yng.is_finite();
    let second = lo_old.is_finite() && hi_yng.is_finite();
    if !first && !second {
        return None; // nothing depends on the span
    }
    let constants: Vec<f64> = [hi_old - lo_old, hi_yng - lo_yng]
        .into_iter()
        .filter(|c| c.is_finite())
        .collect();
    Some(
        spans
            .iter()
            .map(|&span| {
                let mut g: Option<f64> = None;
                let mut take = |v: f64| {
                    g = Some(match g {
                        None => v,
                        Some(current) => current.min(v),
                    });
                };
                if first {
                    take((hi_old - lo_yng) - span);
                }
                if second {
                    take(span - (lo_old - hi_yng));
                }
                for &c in &constants {
                    take(c);
                }
                let g = g.expect("at least one term");
                // g <= 0 means the derived limits contradict each other at
                // this span, which happens when the dates' supports do not
                // overlap and containment already forbids a span that short.
                // The factor counts configurations to normalise a prior, so a
                // non-positive count says something about the LIMITS, not the
                // model, and must not veto the state: weight 1 there, not zero.
                //
                // "Non-positive" has to allow for rounding. The span and the
                // limits are both differences of grid ages, so where they are
                // equal g is zero only in exact arithmetic. On a step binary
                // cannot represent -- the 5/3 or 5/6 years the aligned step
                // often picks -- it comes out as +/-1e-13, and the positive half
                // of that weighted one span by 1e13. On an eleven-date phase
                // whose outermost dates do not overlap, that span then carried
                // the posterior.
                if g > LIMITS_ROUNDING_YEARS {
                    -g.ln()
                } else {
                    0.0
                }
            })
            .collect(),
    )
}

/// How far the enclosing pair of boundaries is pinned by the dates between
/// them: (youngest the OLDER boundary may be, oldest the YOUNGER boundary may
/// be), cal BP.
///
/// The llim/ulim of Bronk Ramsey 2008 A.31, derived as OxCal derives them: each
/// date is bounded by its own likelihood's extent, and each boundary then by
/// t_start < t_i < t_end. So the older boundary cannot be younger than the
/// youngest end of the OLDEST date's support, and symmetrically for the
/// younger.
///
/// The INLIER likelihood, not the outlier mixture: a flagged date's broad
/// component runs for thousands of years, which says where its event might be
/// if the sample is residual, not what the parameter is confined to.
pub fn containment_limits(
    grid: &[f64],
    inliers: &HashMap<String, Vec<f64>>,
    date_ids: &[String],
) -> (f64, f64) {
    let (mut youngest_of_oldest, mut oldest_of_youngest) = (f64::NEG_INFINITY, f64::INFINITY);
    for date_id in date_ids {
        let Some(density) = inliers.get(date_id) else {
            continue;
        };
        let peak = density.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let threshold = peak * SUPPORT_THRESHOLD;
        let (Some(first), Some(last)) = (
            density.iter().position(|&d| d >= threshold),
            density.iter().rposition(|&d| d >= threshold),
        ) else {
            continue;
        };
        youngest_of_oldest = py_max(youngest_of_oldest, grid[first]);
        oldest_of_youngest = py_min(oldest_of_youngest, grid[last]);
    }
    (youngest_of_oldest, oldest_of_youngest)
}

/// The four limits A.31 needs for one pair of boundaries: each one's own
/// explicit TPQ/TAQ where it has them, tightened by what the dates between
/// them imply.
pub fn pair_limits(
    element_bounds: &ElementBounds,
    oldest_id: &str,
    youngest_id: &str,
    grid: &[f64],
    inliers: &HashMap<String, Vec<f64>>,
    date_ids: &[String],
) -> ((f64, f64), (f64, f64)) {
    let (young_bound, old_bound) = containment_limits(grid, inliers, date_ids);
    let oldest = boundary_limits(element_bounds, oldest_id);
    let youngest = boundary_limits(element_bounds, youngest_id);
    (
        (oldest.0, py_max(oldest.1, young_bound)),
        (py_min(youngest.0, old_bound), youngest.1),
    )
}

/// log of the factor 1/g from Bronk Ramsey 2008 A.31 (= 2009a eq 29), as a
/// matrix indexed [youngest boundary, oldest boundary]. None when g does not
/// depend on the span, in which case the factor is a constant and contributes
/// nothing.
///
/// The paper is written in AD, where the phase runs from t_a up to t_m with
/// llim_a <= t_a <= ulim_a and llim_m <= t_m <= ulim_m. In cal BP everything
/// reverses, so writing (hi, lo) for the oldest and youngest age each boundary
/// may take, A.31 becomes
///
/// ```text
/// g = min( (hi_old - lo_yng) - span,        <- needs a TPQ on the oldest
///          span - (lo_old - hi_yng),        <-   and a TAQ on the youngest
///          hi_old - lo_old,                 <- the oldest's own range
///          hi_yng - lo_yng )                <- the youngest's own range
/// ```
///
/// WHICH TERM IS SMALLEST is the character of this factor and why its sign is
/// easy to get backwards: the first falls with the span, so 1/g rises and
/// favours a LONGER one, while the second rises with it and favours a SHORTER.
/// The last two are constants that only cap it.
///
/// Only the first two depend on the span, and each needs both of its limits to
/// be real; with either infinite that term cannot be the minimum. If neither
/// survives, the factor is constant and None is returned.
pub fn log_limits_factor(
    grid: &[f64],
    oldest_limits: (f64, f64),
    youngest_limits: (f64, f64),
) -> Option<Matrix> {
    let m = grid.len();
    let spans: Vec<f64> = (0..m * m).map(|i| grid[i % m] - grid[i / m]).collect();
    log_limits_factor_of_span(&spans, oldest_limits, youngest_limits)
        .map(|v| Matrix::from_rows(m, m, v))
}

/// OxCal's UniformSpanPrior factor over the outermost pair, as a log matrix
/// indexed [youngest boundary, oldest boundary], or None when it would do
/// nothing and the cheap chain can be used.
///
/// ```text
/// 1 / W(span in cells, n - 2)
/// ```
///
/// Placing n boundaries in order across a span leaves the n-2 interior ones
/// uniform within it, so the induced prior carries a factor for that freedom
/// and favours long sequences purely for having more room to arrange them in.
/// Dividing it out makes the span prior independent of how finely the sequence
/// is subdivided -- Nicholls and Jones (2001), and OxCal's default.
///
/// WHY A COUNT AND NOT (b_first - b_last)^-(n-2). The continuum factor is
/// span^(n-2), but the model this solver integrates is discrete, and what has
/// to be divided out is the number of ways the grid lets k ordered interior
/// boundaries sit across a span of j cells -- W(j, k) of
/// [`log_interior_placements`], which counts a tie between neighbours at the
/// half the transfer matrices give it -- not (j*step)^k. The power is wrong
/// most at SMALL j, where the span prior concentrates its mass. Measured with
/// the likelihoods switched off, so the reported posterior IS the prior and the
/// answer is known in closed form, the total-variation distance of the oldest
/// boundary's marginal went from 0.39-1.56% to 0.00% at n = 3-5.
///
/// THE LIMITS FACTOR. OxCal's UniformSpanPrior also carries 1/g of Bronk Ramsey
/// 2008 A.31 (2009a eq 29-30), and it is not negligible: running OxCal with the
/// option off on eight overlapping dates in one phase gives a span of 31.83
/// against 26.26 with it on, so the term is worth 18% of the span, and it
/// NARROWS. Its ON/OFF ratio is about the same on every model tried -- 0.825
/// there, 0.847 on the `ordered` fixture, 0.792 on the charcoal one.
///
/// ```text
/// g = min( (ulim_m - llim_a) - (t_m - t_a),
///          (t_m - t_a) - (llim_m - ulim_a),
///          ulim_a - llim_a,
///          ulim_m - llim_m )
/// ```
///
/// Which of the four is smallest decides which WAY it biases the span, which
/// is the whole subtlety. Read llim/ulim as the wide computational window and
/// the first term wins, giving 1/(window - span), which WIDENS -- and cannot
/// be what OxCal does. With per-boundary limits tight enough for the second
/// term to win, 1/g goes as 1/(span + c), the right sign and roughly the right
/// size. See [`log_limits_factor`] for the expression and [`pair_limits`] for
/// where the limits come from; two things that do NOT work are using the
/// solver's grid edges in place of a missing constraint (which makes the
/// factor an artefact of the grid's padding) and treating an unconstrained
/// boundary as unbounded (the DATES bound the boundaries enclosing them, which
/// is how OxCal derives these and what it prints).
///
/// THE REMAINING SPAN DIFFERENCE, AND WHY IT IS KEPT. On one phase of eight
/// overlapping dates the span is 28.5 against OxCal's 26.3, and 54.4 against
/// 48.9 on four, with the dates themselves agreeing to 0.1-0.2 years in sd.
/// The difference sits in the two Boundaries and appears only with the span
/// prior on. It comes from how OxCal runs, not from the model it states.
/// Observed OxCal 4.4 behaviour: after the fourth of its ten groups of passes,
/// provided convergence is already above 0.5, OxCal pulls every parameter's
/// hard limits in to the range it has sampled so far, once. The limits factor
/// above reads those limits live, so from then on its offset term drops from
/// roughly span + 123 years to roughly span + 45, and the factor narrows the
/// span. This solver does NOT do that, deliberately: it would make the prior
/// depend on the run's own early samples, and so on run length, resolution and
/// the random stream; what this computes is the posterior of the model as
/// OxCal documents it.
///
/// At n = 2 the exponent is zero, so a single Phase between two Boundaries --
/// the app's default shape -- is untouched by the interior-boundary term, and
/// the O(M^2) chain is kept unless the limits factor applies.
///
/// It is not optional: on the fixture's two-group model the group separation is
/// 13.5 years against OxCal's 13.2 with it and 26.7 without, and the dates 0.76
/// years rms against 6.67. It couples the two ends of the chain, so the solve
/// stops factorising and goes from O(M^2) to O(M^3) per group -- worth paying.
pub fn span_prior_correction(
    grid: &[f64],
    num_boundaries: usize,
    enabled: bool,
    limits: Option<((f64, f64), (f64, f64))>,
) -> Option<Matrix> {
    if !enabled {
        return None;
    }
    let m = grid.len();
    // The LOG of the factor, so both parts enter with one sign and -inf means
    // this pair of boundaries is impossible.
    let mut total = Matrix::zeros(m, m); // [youngest, oldest]
    let mut has_limits = false;
    let exponent = num_boundaries as i64 - 2;

    if exponent > 0 {
        // 1 / (ways the interior boundaries fit) -- A.29, 2001 eq 6. A
        // function of the span in cells alone, so evaluated once per span and
        // gathered.
        let index: Vec<f64> = (0..m).map(|i| i as f64).collect();
        let by_span = log_interior_placements(&index, exponent as usize);
        for y in 0..m {
            for o in 0..m {
                let value = if o >= y {
                    let count = by_span[o - y];
                    if count.is_finite() {
                        0.0 - count
                    } else {
                        f64::NEG_INFINITY
                    }
                } else {
                    f64::NEG_INFINITY
                };
                total.set(y, o, value);
            }
        }
    }

    if let Some((oldest_limits, youngest_limits)) = limits {
        // 1 / g -- A.31, 2001 eq 7. Applies at ANY number of boundaries, which
        // is why this can return something at two; None when nothing
        // constrains them, the usual case.
        if let Some(factor) = log_limits_factor(grid, oldest_limits, youngest_limits) {
            total.zip_inplace(&factor, |t, f| {
                if f.is_finite() && t.is_finite() {
                    t + f
                } else {
                    f64::NEG_INFINITY
                }
            });
            has_limits = true;
        }
    }

    if exponent <= 0 && !has_limits {
        return None; // nothing to apply
    }
    if !total.data().iter().any(|v| v.is_finite()) {
        return None;
    }
    Some(total)
}
