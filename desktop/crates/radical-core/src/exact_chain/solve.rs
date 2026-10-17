//! The solve: two passes over calendar grids, the outlier scale weighed
//! between them, the model's evidence, and everything reported from the
//! marginals and the joint draws.

use std::collections::{BTreeMap, HashMap};

use crate::exact_chain::messages::{
    Messages, draw_dates, exp_rescaled, finite_peak, messages, messages_with_span_prior,
};
use crate::exact_chain::ordered::{
    OrderedGroup, log_date_factor, mask_allowed, ordered_log_transfer,
};
use crate::exact_chain::prior::{boundary_pair_term, pair_limits, span_prior_correction};
use crate::exact_chain::summary::{
    Posterior, edge_mass, outlier_probability, straddling, summarize,
};
use crate::exact_chain::tables::{
    DateTables, ElementBounds, OUTLIER_SCALE_NODES, build_grid, date_tables, flat_date_tables,
    point_budget, refined_grid, regular_grid, reporting_aligned_step, scale_nodes,
};
use crate::exact_chain::tails::Tails;
use crate::exact_chain::{Chain, MIN_GRID_STEP_YEARS, REFINED_EDGE_MASS, REPORTING_STEP_YEARS};
use crate::intervals::{self, Block, DEFAULT_DRAWS, Draws};
use crate::matrix::Matrix;
use crate::measurement::{Calibrator, Measurement};
use crate::model_tree::{Element, inherited_bounds};
use crate::numeric::{interp, interp_one, linspace, py_max, py_min, python_sum, sum};
use crate::rng::Generator;
use crate::{Error, py_pow, pyfmt};

/// What a solve is asked for beyond the model.
#[derive(Debug, Clone, PartialEq)]
pub struct SolveOptions {
    /// Draws kept per posterior, for code that wants samples.
    pub num_samples: usize,
    pub seed: u64,
    /// OxCal's UniformSpanPrior, on by default there too; see
    /// [`span_prior_correction`].
    pub uniform_span_prior: bool,
    /// Report durations and differences (the `intervals` block) off the joint
    /// draws.
    pub estimate_durations: bool,
    /// Joint draws taken whatever was asked for, since Amodel needs them.
    pub interval_draws: usize,
    /// The calendar step, in years, the HPD ranges and any durations are
    /// reported at.
    pub resolution: f64,
}

impl Default for SolveOptions {
    fn default() -> Self {
        Self {
            num_samples: 4000,
            seed: 42,
            uniform_span_prior: true,
            estimate_durations: false,
            interval_draws: DEFAULT_DRAWS,
            resolution: REPORTING_STEP_YEARS,
        }
    }
}

/// The grid a solve was reported on.
#[derive(Debug, Clone, PartialEq)]
pub struct GridInfo {
    pub lower: f64,
    pub upper: f64,
    pub step: f64,
    /// The first pass's step, when the second pass was kept.
    pub first_pass_step: Option<f64>,
}

/// One outlier model's shared offset scale, weighed from the data, in log10
/// years as OxCal reports its own U(0,n) posterior.
#[derive(Debug, Clone, PartialEq)]
pub struct OutlierScale {
    pub log10_mean: f64,
    pub log10_hpd_68: (f64, f64),
    pub years_mean: f64,
}

/// p(data | model) in a form that compares between models, or why the model
/// has none.
#[derive(Debug, Clone, PartialEq)]
pub enum Evidence {
    Unavailable(String),
    Available {
        log: f64,
        log10: f64,
        lower: f64,
        upper: f64,
        step: f64,
        approximate: bool,
        /// A short key for the radiocarbon readings alone: models compared by
        /// evidence must explain the same data.
        readings: String,
    },
}

/// A Boundary's reported posterior.
#[derive(Debug, Clone, PartialEq)]
pub struct BoundaryResult {
    pub name: String,
    pub posterior: Posterior,
}

/// Exact marginal posteriors for every date and every Boundary, in the shape
/// the sampler returns, with the joint draws taken from the message-passed
/// posterior.
#[derive(Debug, Clone, PartialEq)]
pub struct Solution {
    /// Every date's summary: the chain's groups in order, then the tails.
    pub measurements: Vec<(String, Posterior)>,
    /// Every Boundary with any mass, in chain order.
    pub boundaries: Vec<(String, BoundaryResult)>,
    /// The dates' joint draws, {date id: calendar ages}, one per draw.
    pub joint_draws: Draws,
    /// The Boundaries in the same states.
    pub joint_boundaries: Draws,
    pub intervals: Option<Block>,
    /// The model's evidence on this grid, up to a constant that depends only
    /// on the dates and the grid: for comparing solves of the same dates on
    /// the same grid.
    pub log_evidence: f64,
    pub uniform_span_prior: bool,
    pub grid: GridInfo,
    pub edge_mass: f64,
    /// Each outlier model's weighed scale, sorted by model.
    pub outlier_scale: Option<Vec<(String, OutlierScale)>>,
    /// One scale per joint draw for each outlier model, drawn from its
    /// weighed posterior.
    pub joint_scales: Option<HashMap<String, Vec<f64>>>,
    pub evidence: Option<Evidence>,
}

impl Solution {
    pub fn measurement(&self, id: &str) -> Option<&Posterior> {
        self.measurements
            .iter()
            .find(|(d, _)| d == id)
            .map(|(_, p)| p)
    }

    pub fn measurement_mut(&mut self, id: &str) -> Option<&mut Posterior> {
        self.measurements
            .iter_mut()
            .find(|(d, _)| d == id)
            .map(|(_, p)| p)
    }
}

/// {outlier model: weight per node of [`scale_nodes`]}, sorted by model.
pub type ScaleWeights = BTreeMap<String, Vec<f64>>;

/// What a solver of this shape answers on one grid. [`two_pass`] drives it.
pub(crate) trait GridSolve {
    /// The densities of Boundaries that between them bound every date -- all
    /// a first pass needs to place the second grid.
    fn boundaries(&self, grid: &[f64]) -> Result<Vec<Vec<f64>>, Error>;
    /// The log evidence alone. `prior_only` solves the same model with no data
    /// (flat tables), the limits factor still taken from the real dates, and
    /// `masses` receives the dates' log integrals either way.
    fn evidence(
        &self,
        grid: &[f64],
        scale_weights: Option<&ScaleWeights>,
        prior_only: bool,
        masses: Option<&mut HashMap<String, f64>>,
    ) -> Result<f64, Error>;
    /// The whole solve, with the densities it reported.
    fn full(
        &self,
        grid: &[f64],
        scale_weights: Option<&ScaleWeights>,
    ) -> Result<(Solution, Vec<Vec<f64>>), Error>;
}

/// Exact marginal posteriors for every date and every boundary in `chain`.
///
/// Returns the same shape the sampler does, so callers need not know which
/// produced it -- including `posterior_samples`, drawn from each exact marginal
/// by inverse CDF so plotting and export code is unchanged.
///
/// Those draws are from the MARGINALS and carry no joint correlation between
/// dates. Nothing reads joint structure off them: Amodel and the durations
/// both need it, and both read the separate joint draws this takes from the
/// message-passed posterior.
///
/// Solved twice where it pays: the Boundaries alone on a grid padded blind for
/// their tails, then everything on one covering only where they found
/// posterior mass, at a finer step ([`refined_grid`]). `grid` reports the one
/// used, with `first_pass_step` when it was refined.
pub fn solve(
    measurements: &[Measurement],
    root: &Element,
    chain: &Chain,
    options: &SolveOptions,
    cal: &Calibrator,
) -> Result<Solution, Error> {
    let element_bounds = inherited_bounds(root);
    let solver = ChainSolver {
        measurements,
        root,
        chain,
        element_bounds: &element_bounds,
        options,
        cal,
    };
    two_pass(
        &solver,
        measurements,
        &element_bounds,
        chain.groups.len(),
        options.seed,
        Some(chain),
        cal,
    )
}

/// The evidence's grid is the prior's own region, whatever its width, so it
/// can be far coarser than the posterior's. On the test models a 1.25-yr step
/// came within 0.005 in the natural log of a grid four times finer where the
/// limits sit clear of the dates, and within 0.05 where they cut into the
/// posterior. 800 points cost a sixth of 2,000. Past 2.5 yr, a region over
/// 2,000 years wide, the figure is flagged as approximate rather than withheld.
pub const EVIDENCE_MAX_GRID_POINTS: f64 = 800.0;
pub const EVIDENCE_COARSE_STEP_YEARS: f64 = 2.5;

/// Why the model has no evidence, or None when it has one.
fn evidence_unavailable(outer: &Chain, element_bounds: &ElementBounds) -> Option<String> {
    if !outer.older_tail.is_empty()
        || !outer.younger_tail.is_empty()
        || !outer.older_tail_blocks.is_empty()
        || !outer.younger_tail_blocks.is_empty()
    {
        return Some(
            "a date sits outside every Boundary, so nothing bounds where it may fall".into(),
        );
    }
    let first = &outer.boundary_ids[0];
    let last = &outer.boundary_ids[outer.boundary_ids.len() - 1];
    // A Boundary has no limiters of its own in the editor; it takes those of
    // the Sequence or Phase it sits in (inherited_bounds).
    if element_bounds.get(first).is_none_or(|b| b.1.is_none()) {
        return Some(format!(
            "the oldest Boundary, '{}', has no TPQ from the Sequence that holds it, so the \
             model has no bounded prior",
            outer.boundary_name(first)
        ));
    }
    if element_bounds.get(last).is_none_or(|b| b.0.is_none()) {
        return Some(format!(
            "the youngest Boundary, '{}', has no TAQ from the Sequence that holds it, so the \
             model has no bounded prior",
            outer.boundary_name(last)
        ));
    }
    None
}

/// A short key for the radiocarbon readings alone: models compared by
/// evidence must explain the same data. The first 16 hex digits of the SHA-1
/// of the sorted (age, error) pairs, rounded to 0.001 and written as Python's
/// `json.dumps` writes them.
fn readings_key(measurements: &[Measurement]) -> String {
    let round3 = |x: f64| crate::numeric::round_ndigits(x, 3);
    let mut readings: Vec<(f64, f64)> = measurements
        .iter()
        .map(|m| (round3(m.age_bp), round3(m.error_1sigma)))
        .collect();
    readings.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.total_cmp(&b.1)));
    let json = format!(
        "[{}]",
        readings
            .iter()
            .map(|(a, e)| format!("[{}, {}]", pyfmt::repr(*a), pyfmt::repr(*e)))
            .collect::<Vec<_>>()
            .join(", ")
    );
    let digest = crate::sha1::sha1_hex(json.as_bytes());
    digest[..16].to_owned()
}

/// The model's marginal likelihood, p(data | model), in a form that compares
/// between models.
///
/// The solver's own log_evidence is up to a constant that depends on the grid
/// (its dates are normalised over it and its prior is not), so it compares
/// nothing between runs. This one is the data's weight over the prior's
/// weight, both from the same message passing on the same grid:
///
/// ```text
/// p(data | model) = sum of (likelihoods x prior) / sum of (prior)
/// ```
///
/// the second with every date flat, and each date's normalisation put back on
/// both sides. For that to be a probability the prior has to be proper, which
/// is what bounds on the outer Boundaries are for, and the grid is exactly the
/// region those bounds leave -- so it does not depend on where the posterior
/// happens to lie. A model with outlier dates is taken with each date's offset
/// scale mixed over its prior, independently.
fn model_evidence(
    solver: &dyn GridSolve,
    measurements: &[Measurement],
    element_bounds: &ElementBounds,
    outer: &Chain,
    num_groups: usize,
) -> Evidence {
    if let Some(reason) = evidence_unavailable(outer, element_bounds) {
        return Evidence::Unavailable(reason);
    }
    let lower = element_bounds[&outer.boundary_ids[outer.boundary_ids.len() - 1]]
        .0
        .expect("checked");
    let upper = element_bounds[&outer.boundary_ids[0]].1.expect("checked");
    if upper <= lower {
        return Evidence::Unavailable(
            "the outer Boundaries' limits leave no calendar range".into(),
        );
    }
    let step = reporting_aligned_step(py_max(
        MIN_GRID_STEP_YEARS / 4.0,
        (upper - lower) / py_min(EVIDENCE_MAX_GRID_POINTS, point_budget(num_groups)),
    ));
    let grid = regular_grid(lower, upper, step);
    let mut data_masses = HashMap::new();
    let mut prior_masses = HashMap::new();
    let with_data = solver.evidence(&grid, None, false, Some(&mut data_masses));
    let without = with_data.and_then(|with_data| {
        solver
            .evidence(&grid, None, true, Some(&mut prior_masses))
            .map(|w| (with_data, w))
    });
    let (with_data, without) = match without {
        Ok(pair) => pair,
        Err(error) => return Evidence::Unavailable(error.to_string()),
    };
    if !(with_data.is_finite() && without.is_finite()) {
        return Evidence::Unavailable("the data have no probability inside the limits".into());
    }
    let in_order = |masses: &HashMap<String, f64>| {
        python_sum(
            measurements
                .iter()
                .filter_map(|m| masses.get(&m.id).copied()),
        )
    };
    let log_evidence = with_data + in_order(&data_masses) - without - in_order(&prior_masses);
    Evidence::Available {
        log: log_evidence,
        log10: log_evidence / 10.0_f64.ln(),
        lower,
        upper,
        step,
        approximate: step > EVIDENCE_COARSE_STEP_YEARS,
        readings: readings_key(measurements),
    }
}

/// The two passes for any solver of this shape: the first on a grid padded
/// blind for the Boundaries' tails, solving them alone; the second on a grid
/// covering only where they found posterior mass, at the finer step the same
/// point budget then allows, kept only if nothing runs off its edge, else the
/// first pass stands. Dates outside every Boundary are not bound by them, but
/// the second grid covers every date's own likelihood support too, so it holds
/// them.
///
/// Between the passes, where any date carries an outlier model, each model's
/// shared offset scale is weighed from the data over the second grid's window
/// ([`weigh_scales`]), and the answer is solved with each flagged date's offset
/// mixed over that posterior rather than the prior.
pub(crate) fn two_pass(
    solver: &dyn GridSolve,
    measurements: &[Measurement],
    element_bounds: &ElementBounds,
    num_groups: usize,
    seed: u64,
    outer: Option<&Chain>,
    cal: &Calibrator,
) -> Result<Solution, Error> {
    let grid = build_grid(measurements, element_bounds, num_groups, cal)?;
    let boundary_densities = solver.boundaries(&grid)?;
    let refined = refined_grid(
        &grid,
        &boundary_densities,
        measurements,
        element_bounds,
        num_groups,
        cal,
    )?;
    let mut models: Vec<String> = measurements
        .iter()
        .filter(|m| m.outlier_prior() > 0.0)
        .map(|m| m.outlier_model().to_owned())
        .collect();
    models.sort();
    models.dedup();
    let weights = if models.is_empty() {
        None
    } else {
        let window = refined.as_deref().unwrap_or(&grid);
        Some(weigh_scales(
            |coarse, trial| solver.evidence(coarse, Some(trial), false, None),
            &models,
            window[0],
            window[window.len() - 1],
        )?)
    };
    let mut results = None;
    if let Some(refined) = &refined {
        let (mut second, _densities) = solver.full(refined, weights.as_ref())?;
        if second.edge_mass <= REFINED_EDGE_MASS {
            second.grid.first_pass_step = Some(grid[1] - grid[0]);
            results = Some(second);
        }
    }
    let mut results = match results {
        Some(r) => r,
        None => solver.full(&grid, weights.as_ref())?.0,
    };
    if let Some(weights) = &weights {
        report_scales(&mut results, weights, seed);
    }
    if let Some(outer) = outer {
        results.evidence = Some(model_evidence(
            solver,
            measurements,
            element_bounds,
            outer,
            num_groups,
        ));
    }
    Ok(results)
}

/// The shared outlier scale is weighed on this many points across the second
/// grid's window. 200 across it matched 300 to 0.04 years in the dates and
/// 0.0001 in the outlier probabilities on the test models.
pub const SCALE_WEIGHING_POINTS: usize = 200;
/// Every second scale node is solved for and the log evidence interpolated
/// between them: the scale's posterior is smooth in the log, and on the test
/// models 12 nodes matched 24 within 0.3 years and 0.0003 in outlier
/// probability, at half the cost. The last node is always solved too.
pub const SCALE_WEIGHING_STRIDE: usize = 2;
/// With several outlier models each is weighed in turn, the others held at
/// their weights so far, twice round. On the charcoal fixture's two the joint
/// weights are within 0.001 total variation of the product of each model's
/// own.
pub const SCALE_WEIGHING_SWEEPS: usize = 2;

/// {outlier model: posterior weight per node of [`scale_nodes`]}: the shared
/// offset scale weighed from the data.
///
/// OxCal's Outlier_Model carries ONE scale for all the dates using it, and the
/// data constrain it; mixing each date's offset over the scale's prior on its
/// own, as [`date_tables`] does by default, is a different model. The exact
/// answer mixes whole solves, one per scale, by their evidence -- 24 solves per
/// model, 576 for two. Instead `evidence(grid, weights)`, the log evidence on a
/// coarse grid over [lower, upper] with each model's offset at the given
/// weights, is taken at each node, and the solve that follows mixes each
/// flagged date's offset over the posterior those give. That keeps what the
/// data say about the scale and drops only the link a common scale makes
/// between the dates.
pub fn weigh_scales(
    evidence: impl Fn(&[f64], &ScaleWeights) -> Result<f64, Error>,
    models: &[String],
    lower: f64,
    upper: f64,
) -> Result<ScaleWeights, Error> {
    let grid = linspace(lower, upper, SCALE_WEIGHING_POINTS);
    let count = OUTLIER_SCALE_NODES;
    let mut solved_at: Vec<usize> = (0..count).step_by(SCALE_WEIGHING_STRIDE).collect();
    if !solved_at.contains(&(count - 1)) {
        solved_at.push(count - 1);
    }
    let solved_x: Vec<f64> = solved_at.iter().map(|&n| n as f64).collect();
    let mut weights: ScaleWeights = models
        .iter()
        .map(|m| (m.clone(), vec![1.0 / count as f64; count]))
        .collect();
    let sweeps = if models.len() == 1 {
        1
    } else {
        SCALE_WEIGHING_SWEEPS
    };
    for _ in 0..sweeps {
        for model in models {
            let mut log_evidence = Vec::with_capacity(solved_at.len());
            for &node in &solved_at {
                let mut alone = vec![0.0; count];
                alone[node] = 1.0;
                let mut trial = weights.clone();
                trial.insert(model.clone(), alone);
                log_evidence.push(evidence(&grid, &trial)?);
            }
            let finite: Vec<f64> = log_evidence
                .iter()
                .copied()
                .filter(|v| v.is_finite())
                .collect();
            if finite.is_empty() {
                continue; // nothing to weigh by: keep the prior
            }
            let floor = finite.iter().copied().fold(f64::INFINITY, f64::min) - 50.0;
            let filled: Vec<f64> = log_evidence
                .iter()
                .map(|&v| if v.is_finite() { v } else { floor })
                .collect();
            let every = interp(
                &(0..count).map(|n| n as f64).collect::<Vec<_>>(),
                &solved_x,
                &filled,
            );
            let peak = every.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            let posterior: Vec<f64> = every.iter().map(|v| (v - peak).exp()).collect();
            let total = sum(&posterior);
            weights.insert(model.clone(), posterior.iter().map(|p| p / total).collect());
        }
    }
    Ok(weights)
}

/// Each outlier model's weighed scale in `results`, in the sampler's shape:
/// `outlier_scale`, its posterior mean and 68% range in log10 years, and
/// `joint_scales`, one scale per joint draw drawn from it, so the agreement
/// indices score a flagged date under a scale as they do a sampled run's.
/// Seeded apart from everything else in the results.
fn report_scales(results: &mut Solution, weights: &ScaleWeights, seed: u64) {
    let mut rng = Generator::new(seed + 2);
    let count = results.joint_draws.values().next().map_or(0, Vec::len);
    let mut scales = Vec::new();
    let mut joint = HashMap::new();
    for (model, weight) in weights {
        let nodes = scale_nodes(model);
        let mean = sum(&nodes
            .iter()
            .zip(weight)
            .map(|(n, w)| n * w)
            .collect::<Vec<_>>());
        let cdf = crate::numeric::cumsum(weight);
        scales.push((
            model.clone(),
            OutlierScale {
                log10_mean: mean,
                log10_hpd_68: (
                    interp_one(0.16, &cdf, &nodes),
                    interp_one(0.84, &cdf, &nodes),
                ),
                years_mean: py_pow(10.0, mean),
            },
        ));
        let total = sum(weight);
        let p: Vec<f64> = weight.iter().map(|w| w / total).collect();
        let drawn: Vec<f64> = (0..count).map(|_| nodes[rng.choice(&p)]).collect();
        joint.insert(model.clone(), drawn);
    }
    results.outlier_scale = Some(scales);
    results.joint_scales = Some(joint);
}

/// Where each boundary may sit: inside its own TPQ/TAQ, if it has them.
pub(crate) fn allowed_windows(
    grid: &[f64],
    element_bounds: &ElementBounds,
    boundary_ids: &[String],
) -> HashMap<String, Vec<bool>> {
    boundary_ids
        .iter()
        .map(|bid| {
            let (taq, tpq) = element_bounds.get(bid).copied().unwrap_or((None, None));
            let ok = grid
                .iter()
                .map(|&t| !(taq.is_some_and(|a| t < a) || tpq.is_some_and(|p| t > p)))
                .collect();
            (bid.clone(), ok)
        })
        .collect()
}

/// T_i over one group, dates integrated out, in the log and indexed [younger,
/// older]. Its two boundaries may share a cell, at half weight; -inf where
/// either boundary may not sit.
pub(crate) fn group_log_transfer(
    grid: &[f64],
    date_ids: &[String],
    cumulative: &HashMap<String, Vec<f64>>,
    likelihood: &HashMap<String, Vec<f64>>,
    older_ok: &[bool],
    younger_ok: &[bool],
) -> Matrix {
    let dt = grid[1] - grid[0];
    let mut term = boundary_pair_term(grid, date_ids.len());
    for date_id in date_ids {
        let factor = log_date_factor(&cumulative[date_id], &likelihood[date_id], dt);
        term.zip_inplace(&factor, |a, b| a + b);
    }
    mask_allowed(&mut term, older_ok, younger_ok);
    term
}

/// Every group's log transfer, in order, and {group index: ordered group} for
/// the groups whose dates are ordered inside them.
pub(crate) fn chain_log_transfers(
    grid: &[f64],
    chain: &Chain,
    cumulative: &HashMap<String, Vec<f64>>,
    likelihood: &HashMap<String, Vec<f64>>,
    allowed: &HashMap<String, Vec<bool>>,
) -> (Vec<Matrix>, BTreeMap<usize, OrderedGroup>) {
    let mut log_t = Vec::new();
    let mut ordered = BTreeMap::new();
    for (index, date_ids) in chain.groups.iter().enumerate() {
        let older_ok = &allowed[&chain.boundary_ids[index]];
        let younger_ok = &allowed[&chain.boundary_ids[index + 1]];
        if chain.ordered(index) {
            let group = OrderedGroup::new(&chain.blocks[index], grid, cumulative, likelihood);
            log_t.push(ordered_log_transfer(
                &group, grid, older_ok, younger_ok, likelihood,
            ));
            ordered.insert(index, group);
        } else {
            log_t.push(group_log_transfer(
                grid, date_ids, cumulative, likelihood, older_ok, younger_ok,
            ));
        }
    }
    (log_t, ordered)
}

/// The date marginals for a chain that may hold ordered groups, in the chain's
/// order. An ordered group's dates come from its own chain over its cuts,
/// given everything outside it: the pair weight, and whatever of its log
/// transfer is not the sum over its cuts -- the span factor, the tie, and the
/// span correction where one group carries it.
pub(crate) fn chain_date_marginals(
    chain: &Chain,
    ordered: &mut BTreeMap<usize, OrderedGroup>,
    log_t: &[Matrix],
    pair_weight: &dyn Fn(usize) -> Matrix,
    cumulative: &HashMap<String, Vec<f64>>,
    likelihood: &HashMap<String, Vec<f64>>,
    dt: f64,
) -> Vec<(String, Vec<f64>)> {
    let plain: Vec<Vec<String>> = chain
        .groups
        .iter()
        .enumerate()
        .map(|(i, g)| {
            if ordered.contains_key(&i) {
                Vec::new()
            } else {
                g.clone()
            }
        })
        .collect();
    let mut found: HashMap<String, Vec<f64>> =
        date_marginals(&plain, log_t, pair_weight, cumulative, likelihood, dt)
            .into_iter()
            .collect();
    for (&index, group) in ordered.iter_mut() {
        let mut log_pair = pair_weight(index);
        log_pair.map_inplace(f64::ln);
        let m = log_pair.rows();
        // log_pair + (log_T - log_inner), NaN (-inf less -inf) to -inf.
        let mut end = Matrix::zeros(m, m);
        for y in 0..m {
            for o in 0..m {
                let v = log_pair.get(y, o) + (log_t[index].get(y, o) - group.log_inner.get(y, o));
                end.set(y, o, if v.is_nan() { f64::NEG_INFINITY } else { v });
            }
        }
        // The ends in one cell are the tie, weighed on its own.
        let log_tie: Vec<f64> = log_pair
            .diagonal()
            .iter()
            .zip(log_t[index].diagonal())
            .map(|(p, t)| p + t)
            .collect();
        for i in 0..m {
            end.set(i, i, f64::NEG_INFINITY);
        }
        group.solve(&end, Some(log_tie));
        found.extend(group.marginals(cumulative, likelihood));
    }
    chain
        .groups
        .iter()
        .flatten()
        .map(|d| {
            (
                d.clone(),
                found.remove(d).expect("every date has a marginal"),
            )
        })
        .collect()
}

/// {date id: unnormalised marginal density} for every date in `groups`, in
/// their order, from each group's log transfer and `pair_weight(k)`, the
/// weight of everything outside group k indexed [younger, older].
pub(crate) fn date_marginals(
    groups: &[Vec<String>],
    log_t: &[Matrix],
    pair_weight: &dyn Fn(usize) -> Matrix,
    cumulative: &HashMap<String, Vec<f64>>,
    likelihood: &HashMap<String, Vec<f64>>,
    dt: f64,
) -> Vec<(String, Vec<f64>)> {
    let mut densities = Vec::new();
    for (index, date_ids) in groups.iter().enumerate() {
        if date_ids.is_empty() {
            continue;
        }
        // Everything about this group's two boundaries, the weight outside the
        // group and the transfer inside it, in the log and once per group: on
        // the span-prior path the outside is two M x M matrix products, and
        // every date in the group shares it.
        let mut log_pair = pair_weight(index);
        log_pair.map_inplace(f64::ln);
        log_pair.zip_inplace(&log_t[index], |a, b| a + b);
        for date_id in date_ids {
            // This date's own factor divided back out, leaving it free: its
            // integral where the boundaries are apart, its likelihood where
            // they share a cell. Where the date has no likelihood that factor
            // is -inf, and so is log_T, which was summed from the same factor;
            // -inf minus -inf is NaN, which fmax turns back into -inf.
            let mut w = log_date_factor(&cumulative[date_id], &likelihood[date_id], dt);
            w.zip_inplace(&log_pair, |f, p| {
                let v = p - f;
                if v.is_nan() { f64::NEG_INFINITY } else { v }
            });
            let peak = w.max();
            if peak.is_finite() {
                w.map_inplace(|v| (v - peak).exp());
            } else {
                w.map_inplace(|_| 0.0);
            }
            // The weight with the date ON the younger boundary (row t: the
            // younger boundary at t, any older one) and ON the older (column
            // t). W is zero wherever the older boundary is younger, so a whole
            // row or column sum is exactly that -- the diagonal, both
            // boundaries at t, included in each.
            let on_younger = w.row_sums();
            let on_older = w.column_sums();
            let on_both = w.diagonal();
            // A date sitting on one of its boundaries counts HALF, as it does
            // in the interval integral T was built from and in the sampler.
            // Counted whole, the date's marginal was not the marginal of the
            // joint the boundaries come from: modelled dates moved up to 3.7
            // years on the test models.
            //
            // With both boundaries at t the date can only be at t too, and the
            // whole of that weight is its: the half on each side would take it
            // twice, so it is put back once -- divided by the step. Everywhere
            // else W is the pair's weight over the date's interval integral,
            // so L * W is a density; on the diagonal the date's own factor was
            // its likelihood, so L * W there is the tie's whole weight, a mass
            // in one cell, and a density needs it over the cell's width.
            let s = straddling(&on_younger, &on_older);
            let density = likelihood[date_id]
                .iter()
                .enumerate()
                .map(|(t, l)| {
                    let enclosed = s[t] - 0.5 * (on_younger[t] + on_older[t]) + on_both[t] / dt;
                    l * enclosed.max(0.0)
                })
                .collect();
            densities.push((date_id.clone(), density));
        }
    }
    densities
}

/// Grid indices of joint draws: the Boundaries' and the dates'.
pub(crate) type JointIndices = (Vec<(String, Vec<usize>)>, HashMap<String, Vec<usize>>);

/// What [`results`] needs of one solve on one grid.
pub(crate) struct Marginals<'a> {
    pub grid: &'a [f64],
    pub root: &'a Element,
    pub measurements: &'a [Measurement],
    pub date_densities: Vec<(String, Vec<f64>)>,
    pub boundary_densities: Vec<(String, Vec<f64>)>,
    pub boundary_names: &'a crate::exact_chain::Names,
    pub tables: &'a DateTables,
    pub log_evidence: f64,
    pub span_prior_applied: bool,
}

/// A solve's results from its marginals, in the shape the sampler returns:
/// every date's then every Boundary's summary, in the order given, and the
/// joint draws `draw_joint(rng, count)` takes. (results, densities).
pub(crate) fn results(
    m: Marginals<'_>,
    options: &SolveOptions,
    draw_joint: &dyn Fn(&mut Generator, usize) -> Result<JointIndices, Error>,
) -> Result<(Solution, Vec<Vec<f64>>), Error> {
    let grid = m.grid;
    let dt = grid[1] - grid[0];
    let mut rng = Generator::new(options.seed);
    let mut densities = Vec::new();
    let mut measurements = Vec::new();
    for (date_id, density) in m.date_densities {
        let mut summary = summarize(
            grid,
            &density,
            &mut rng,
            options.num_samples,
            options.resolution,
        )?;
        if let Some((_inlier, broad, prior)) = m.tables.outlier_parts.get(&date_id) {
            summary.outlier_probability = Some(outlier_probability(
                &density,
                &m.tables.likelihood[&date_id],
                broad,
                *prior,
            ));
        }
        densities.push(density);
        measurements.push((date_id, summary));
    }
    let mut boundaries = Vec::new();
    for (bid, density) in m.boundary_densities {
        if sum(&density) <= 0.0 {
            continue;
        }
        let posterior = summarize(
            grid,
            &density,
            &mut rng,
            options.num_samples,
            options.resolution,
        )?;
        densities.push(density);
        boundaries.push((
            bid.clone(),
            BoundaryResult {
                name: crate::exact_chain::name_of(m.boundary_names, &bid)
                    .unwrap_or("Boundary")
                    .to_owned(),
                posterior,
            },
        ));
    }

    // Joint draws: taken always, because Amodel -- the whole model's agreement
    // index -- is a function of every date at once and is always reported.
    // The dates' draws are returned for the agreement indices to read, rather
    // than scored here: the diagnostic needs each date's posterior outlier
    // probability as well, which is a finished result. Durations read the SAME
    // draws when they were asked for, so the sections cannot describe
    // different states of the model.
    //
    // Seeded apart from the generator above, so every other number here stays
    // bit-for-bit identical to what it was before any of this was collected.
    let mut joint_rng = Generator::new(options.seed + 1);
    let (boundary_draws, date_draws) = draw_joint(&mut joint_rng, options.interval_draws)?;
    let at = |idx: &[usize]| idx.iter().map(|&i| grid[i]).collect::<Vec<f64>>();
    let joint_boundaries: Draws = boundary_draws
        .iter()
        .map(|(b, idx)| (b.clone(), at(idx)))
        .collect();
    let joint_draws: Draws = date_draws
        .iter()
        .map(|(d, idx)| (d.clone(), at(idx)))
        .collect();
    let intervals = options.estimate_durations.then(|| {
        let mut joint = joint_boundaries.clone();
        joint.extend(joint_draws.iter().map(|(k, v)| (k.clone(), v.clone())));
        intervals::report(
            Some(m.root),
            m.measurements,
            &joint,
            true,
            true,
            Some(options.resolution),
        )
    });
    let edge = edge_mass(&densities, grid.len());
    Ok((
        Solution {
            measurements,
            boundaries,
            joint_draws,
            joint_boundaries,
            intervals,
            log_evidence: m.log_evidence,
            uniform_span_prior: m.span_prior_applied,
            grid: GridInfo {
                lower: grid[0],
                upper: grid[grid.len() - 1],
                step: dt,
                first_pass_step: None,
            },
            edge_mass: edge,
            outlier_scale: None,
            joint_scales: None,
            evidence: None,
        },
        densities,
    ))
}

/// One solve of a chain on one grid, at the stage asked for.
struct ChainSolver<'a> {
    measurements: &'a [Measurement],
    root: &'a Element,
    chain: &'a Chain,
    element_bounds: &'a ElementBounds,
    options: &'a SolveOptions,
    cal: &'a Calibrator<'a>,
}

/// How far a solve on one grid goes.
enum Stage<'m> {
    Boundaries,
    Evidence {
        prior_only: bool,
        masses: Option<&'m mut HashMap<String, f64>>,
    },
    Full,
}

enum Reached {
    Boundaries(Vec<Vec<f64>>),
    Evidence(f64),
    Full(Box<(Solution, Vec<Vec<f64>>)>),
}

impl ChainSolver<'_> {
    fn on_grid(
        &self,
        grid: &[f64],
        scale_weights: Option<&ScaleWeights>,
        stage: Stage<'_>,
    ) -> Result<Reached, Error> {
        let chain = self.chain;
        let dt = grid[1] - grid[0];
        let (evidence_only, boundaries_only) = match &stage {
            Stage::Boundaries => (false, true),
            Stage::Evidence { .. } => (true, false),
            Stage::Full => (false, false),
        };
        let (prior_only, mut masses) = match stage {
            Stage::Evidence { prior_only, masses } => (prior_only, masses),
            _ => (false, None),
        };
        let mut tables = date_tables(
            grid,
            self.measurements,
            self.element_bounds,
            scale_weights,
            if prior_only {
                None
            } else {
                masses.as_deref_mut()
            },
            self.cal,
        )?;
        let all_dates: Vec<String> = chain.groups.iter().flatten().cloned().collect();
        let inliers: HashMap<String, Vec<f64>> = all_dates
            .iter()
            .map(|d| {
                let v = match tables.outlier_parts.get(d) {
                    Some(part) => part.0.clone(),
                    None => tables.likelihood[d].clone(),
                };
                (d.clone(), v)
            })
            .collect();
        if prior_only {
            let (likelihood, cumulative) = flat_date_tables(
                grid,
                self.measurements,
                self.element_bounds,
                masses,
            )?;
            tables.likelihood = likelihood;
            tables.cumulative = cumulative;
        }
        let allowed = allowed_windows(grid, self.element_bounds, &chain.boundary_ids);
        let (mut log_t, mut ordered) = chain_log_transfers(
            grid,
            chain,
            &tables.cumulative,
            &tables.likelihood,
            &allowed,
        );

        // Dates outside every Boundary: one weight each on the first and last
        // Boundary. Not enclosed by the outer pair, so not part of the limits
        // factor below either, as in the sampler.
        let mut tails = Tails::new(chain, grid, &tables.cumulative, &tables.likelihood);
        let first = &chain.boundary_ids[0];
        let last = &chain.boundary_ids[chain.boundary_ids.len() - 1];
        let limits = pair_limits(self.element_bounds, first, last, grid, &inliers, &all_dates);
        let log_correction = span_prior_correction(
            grid,
            chain.boundary_ids.len(),
            self.options.uniform_span_prior,
            Some(limits),
        );
        let span_prior_applied = log_correction.is_some();
        let mut correction_offset = 0.0;
        let msgs: Messages = match log_correction {
            Some(correction) if chain.groups.len() == 1 => {
                // With one group the correction's boundaries ARE this group's,
                // so it folds into the transfer matrix and costs nothing. Worth
                // the special case: it is the app's default shape, and the
                // limits factor applies to it where the (n-2) factor does not,
                // so without this every single-phase model would take the
                // O(M^3) path.
                log_t[0].zip_inplace(&correction, |a, c| {
                    a + if c.is_finite() { c } else { f64::NEG_INFINITY }
                });
                messages(chain, &allowed, &log_t, &tails.end_weights)
            }
            Some(correction) => {
                correction_offset = finite_peak(correction.data());
                let scaled = Matrix::from_rows(
                    correction.rows(),
                    correction.cols(),
                    exp_rescaled(correction.data()),
                );
                messages_with_span_prior(chain, &allowed, &log_t, scaled, &tails.end_weights)
            }
            None => messages(chain, &allowed, &log_t, &tails.end_weights),
        };
        // The model's evidence, p(data | model), in the log: the chain's total
        // weight with every factor the rescaling took out put back. Up to a
        // constant that depends only on the dates and the grid -- each
        // likelihood is normalised over the grid and the boundaries' prior is
        // flat over it -- so it compares solves of the SAME dates on the SAME
        // grid, such as one per value of a shared outlier scale, not models
        // with different windows.
        let log_evidence = msgs.log_mass()
            + correction_offset
            + tails.log_scale
            + python_sum(log_t.iter().map(|t| finite_peak(t.data())));
        if evidence_only {
            return Ok(Reached::Evidence(log_evidence));
        }
        let n = chain.boundary_ids.len();
        if boundaries_only {
            return Ok(Reached::Boundaries(
                (0..n)
                    .map(|i| msgs.boundary_density(i))
                    .filter(|d| sum(d) > 0.0)
                    .collect(),
            ));
        }

        let boundary_densities: Vec<(String, Vec<f64>)> = chain
            .boundary_ids
            .iter()
            .enumerate()
            .map(|(i, b)| (b.clone(), msgs.boundary_density(i)))
            .collect();
        let pair_weight = |k: usize| msgs.pair_weight(k);
        let mut date_densities = chain_date_marginals(
            chain,
            &mut ordered,
            &log_t,
            &pair_weight,
            &tables.cumulative,
            &tables.likelihood,
            dt,
        );
        date_densities.extend(tails.marginals(
            &boundary_densities[0].1,
            &boundary_densities[n - 1].1,
            &tables.cumulative,
            &tables.likelihood,
        ));

        let draw_joint = |rng: &mut Generator, count: usize| -> Result<JointIndices, Error> {
            let boundary_draws = msgs.draw_boundaries(&log_t, rng, count)?;
            let mut date_draws = chain_draw_dates(
                rng,
                chain,
                &ordered,
                &tables.cumulative,
                &tables.likelihood,
                &boundary_draws,
                dt,
            )?;
            date_draws.extend(tails.draw(
                rng,
                &boundary_draws[0],
                &boundary_draws[n - 1],
                &tables.cumulative,
                &tables.likelihood,
                dt,
            )?);
            Ok((
                chain
                    .boundary_ids
                    .iter()
                    .cloned()
                    .zip(boundary_draws)
                    .collect(),
                date_draws,
            ))
        };
        let solved = results(
            Marginals {
                grid,
                root: self.root,
                measurements: self.measurements,
                date_densities,
                boundary_densities,
                boundary_names: &chain.boundary_names,
                tables: &tables,
                log_evidence,
                span_prior_applied,
            },
            self.options,
            &draw_joint,
        )?;
        Ok(Reached::Full(Box::new(solved)))
    }
}

impl GridSolve for ChainSolver<'_> {
    fn boundaries(&self, grid: &[f64]) -> Result<Vec<Vec<f64>>, Error> {
        match self.on_grid(grid, None, Stage::Boundaries)? {
            Reached::Boundaries(d) => Ok(d),
            _ => unreachable!("asked for the Boundaries"),
        }
    }

    fn evidence(
        &self,
        grid: &[f64],
        scale_weights: Option<&ScaleWeights>,
        prior_only: bool,
        masses: Option<&mut HashMap<String, f64>>,
    ) -> Result<f64, Error> {
        match self.on_grid(grid, scale_weights, Stage::Evidence { prior_only, masses })? {
            Reached::Evidence(e) => Ok(e),
            _ => unreachable!("asked for the evidence"),
        }
    }

    fn full(
        &self,
        grid: &[f64],
        scale_weights: Option<&ScaleWeights>,
    ) -> Result<(Solution, Vec<Vec<f64>>), Error> {
        match self.on_grid(grid, scale_weights, Stage::Full)? {
            Reached::Full(solved) => Ok(*solved),
            _ => unreachable!("asked for the whole solve"),
        }
    }
}

/// The dates' draws for a chain that may hold ordered groups, whose dates come
/// from the group given its two Boundaries drawn.
pub(crate) fn chain_draw_dates(
    rng: &mut Generator,
    chain: &Chain,
    ordered: &BTreeMap<usize, OrderedGroup>,
    cumulative: &HashMap<String, Vec<f64>>,
    likelihood: &HashMap<String, Vec<f64>>,
    boundary_draws: &[Vec<usize>],
    step: f64,
) -> Result<HashMap<String, Vec<usize>>, Error> {
    let plain: Vec<Vec<String>> = chain
        .groups
        .iter()
        .enumerate()
        .map(|(i, g)| {
            if ordered.contains_key(&i) {
                Vec::new()
            } else {
                g.clone()
            }
        })
        .collect();
    let mut out = draw_dates(rng, &plain, cumulative, likelihood, boundary_draws, step);
    for (&index, group) in ordered {
        out.extend(group.draw(
            rng,
            &boundary_draws[index],
            &boundary_draws[index + 1],
            cumulative,
            likelihood,
        )?);
    }
    Ok(out)
}
