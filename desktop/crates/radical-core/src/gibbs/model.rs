//! The posterior, flattened into what the sampler needs: per-date likelihood
//! tables, per-group span terms, and for every parameter its hard bounds and
//! the parameters constraining it. Port of `gibbs.Model`.

use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

use crate::Error;
use crate::exact_chain::prior::{log_interior_placements, log_limits_factor_of_span, pair_limits};
use crate::exact_chain::tables::{
    ElementBounds, OUTLIER_KERNEL_HALF_WIDTH_YEARS, OutlierPart, build_grid, convolve_on_grid,
    date_tables, effective_date_bounds, outlier_kernel_at_scale, scale_nodes,
};
use crate::measurement::{Calibrator, Measurement};
use crate::model_tree::{Element, ModelPlan, inherited_bounds, plan_model};
use crate::numeric::{argmax, cumsum, find_peaks, linspace, mean, searchsorted_left, sum};
use crate::rng::Generator;

use super::{
    DEPOSITION_K_NODES, Deposition, EQUALISED_EDGE, LOBE_HELD_FRACTION, LOBE_MIN_MASS,
    MAX_PEAK_GAPS, PEAK_JUMP_FRACTION, PEAK_JUMP_JITTER_YEARS, PEAK_PROMINENCE, START_STRATEGIES,
    State, WIDE_MOVE_FRACTION, WIDE_MOVE_YEARS, log_deposition_density,
};

/// A calibration lobe: (peak cell, share of the row's total, first cell, one
/// past the last).
pub type Lobe = (usize, f64, usize, usize);

/// A date's wished lobes, first choice first, each with the cell in it the
/// chain would like (None for the peak).
pub type Wishes = HashMap<usize, Vec<(Lobe, Option<i64>)>>;

/// Python's `int(round(x))`: ties to even.
fn round_even(x: f64) -> i64 {
    x.round_ties_even() as i64
}

/// (row, scale node, beta's bits): a tempered likelihood table's key.
type TemperedKey = (usize, Option<usize>, u64);
/// A likelihood row and its cumulative sum.
type Tables = (Vec<f64>, Vec<f64>);

/// The sampler's model of one analysis.
#[derive(Debug)]
pub struct Model {
    pub measurements: Vec<Measurement>,
    pub plan: ModelPlan,
    pub element_bounds: ElementBounds,
    pub uniform_span_prior: bool,
    pub grid: Vec<f64>,
    pub dt: f64,
    pub m: usize,

    /// Every parameter: dates, then undated levels, then boundaries.
    pub params: Vec<String>,
    pub param_row: HashMap<String, usize>,
    pub num_dates: usize,
    pub num_drawable: usize,
    pub date_rows: Vec<usize>,
    pub level_rows: Vec<usize>,
    pub boundary_rows: Vec<usize>,

    /// Per drawable row: the likelihood table, its log, and its cumulative.
    pub likelihood: Vec<Vec<f64>>,
    pub log_likelihood: Vec<Vec<f64>>,
    pub cdf: Vec<Vec<f64>>,
    pub outlier_parts: HashMap<String, OutlierPart>,

    /// The outlier models in use, sorted, with each one's scale nodes and
    /// flagged date rows.
    pub outlier_models: Vec<String>,
    pub scale_grid: Vec<Vec<f64>>,
    pub scaled_dates: Vec<Vec<usize>>,
    /// Per parameter row, the outlier model it uses.
    pub model_of: Vec<Option<usize>>,
    /// Per flagged date row: the mixture likelihood at each scale node, its
    /// log, cumulative and the outlier component's responsibility.
    pub likelihood_at: HashMap<usize, Vec<Vec<f64>>>,
    pub log_likelihood_at: HashMap<usize, Vec<Vec<f64>>>,
    pub cdf_at: HashMap<usize, Vec<Vec<f64>>>,
    pub responsibility_at: HashMap<usize, Vec<Vec<f64>>>,

    pub depositions: Vec<Deposition>,
    pub level_names: HashMap<usize, String>,
    /// Per level row, (sequence, position in it).
    pub deposition_of: HashMap<usize, (usize, usize)>,

    pub below: Vec<Vec<usize>>,
    pub above: Vec<Vec<usize>>,
    pub group_dates: Vec<(usize, usize, Vec<usize>)>,
    pub window: Vec<(i64, i64)>,
    pub span_priors: Vec<(usize, usize, usize)>,
    pub limits_tables: HashMap<(usize, usize), Vec<f64>>,
    pub span_prior_rows: Vec<Vec<f64>>,
    pub log_span_of_cells: Vec<f64>,
    pub boundary_groups: HashMap<usize, Vec<(usize, bool)>>,
    pub boundary_priors: HashMap<usize, Vec<(usize, bool)>>,
    edge_younger: Vec<usize>,
    edge_older: Vec<usize>,
    /// Every tied pair, (younger, older): ordering edges carrying a half,
    /// date ties and neighbouring boundaries alike.
    pub tie_pairs: BTreeSet<(usize, usize)>,
    pub tied_with: Vec<Vec<usize>>,
    pub edge_tied_with: Vec<Vec<usize>>,
    pub date_tied_with: Vec<Vec<usize>>,
    tie_younger: Vec<usize>,
    tie_older: Vec<usize>,
    date_tie_pairs: Vec<(usize, usize)>,
    younger_dates: HashMap<usize, HashSet<usize>>,
    pub blocks: Vec<Vec<usize>>,

    cluster_weights: RefCell<HashMap<Vec<usize>, f64>>,
    tempered: RefCell<HashMap<TemperedKey, Tables>>,
    peak_gaps_cache: RefCell<HashMap<Vec<usize>, Vec<i64>>>,
    lobes_cache: RefCell<HashMap<usize, Vec<Lobe>>>,
}

impl Clone for Model {
    fn clone(&self) -> Self {
        Self {
            measurements: self.measurements.clone(),
            plan: self.plan.clone(),
            element_bounds: self.element_bounds.clone(),
            uniform_span_prior: self.uniform_span_prior,
            grid: self.grid.clone(),
            dt: self.dt,
            m: self.m,
            params: self.params.clone(),
            param_row: self.param_row.clone(),
            num_dates: self.num_dates,
            num_drawable: self.num_drawable,
            date_rows: self.date_rows.clone(),
            level_rows: self.level_rows.clone(),
            boundary_rows: self.boundary_rows.clone(),
            likelihood: self.likelihood.clone(),
            log_likelihood: self.log_likelihood.clone(),
            cdf: self.cdf.clone(),
            outlier_parts: self.outlier_parts.clone(),
            outlier_models: self.outlier_models.clone(),
            scale_grid: self.scale_grid.clone(),
            scaled_dates: self.scaled_dates.clone(),
            model_of: self.model_of.clone(),
            likelihood_at: self.likelihood_at.clone(),
            log_likelihood_at: self.log_likelihood_at.clone(),
            cdf_at: self.cdf_at.clone(),
            responsibility_at: self.responsibility_at.clone(),
            depositions: self.depositions.clone(),
            level_names: self.level_names.clone(),
            deposition_of: self.deposition_of.clone(),
            below: self.below.clone(),
            above: self.above.clone(),
            group_dates: self.group_dates.clone(),
            window: self.window.clone(),
            span_priors: self.span_priors.clone(),
            limits_tables: self.limits_tables.clone(),
            span_prior_rows: self.span_prior_rows.clone(),
            log_span_of_cells: self.log_span_of_cells.clone(),
            boundary_groups: self.boundary_groups.clone(),
            boundary_priors: self.boundary_priors.clone(),
            edge_younger: self.edge_younger.clone(),
            edge_older: self.edge_older.clone(),
            tie_pairs: self.tie_pairs.clone(),
            tied_with: self.tied_with.clone(),
            edge_tied_with: self.edge_tied_with.clone(),
            date_tied_with: self.date_tied_with.clone(),
            tie_younger: self.tie_younger.clone(),
            tie_older: self.tie_older.clone(),
            date_tie_pairs: self.date_tie_pairs.clone(),
            younger_dates: self.younger_dates.clone(),
            blocks: self.blocks.clone(),
            // Caches start empty in a copy; they refill as they are read.
            cluster_weights: RefCell::default(),
            tempered: RefCell::default(),
            peak_gaps_cache: RefCell::default(),
            lobes_cache: RefCell::default(),
        }
    }
}

impl Model {
    /// Built from plan_model's output, which is already general -- it emits
    /// ordering and span groups for an arbitrary tree.
    ///
    /// `grid` None builds the sampler's own, at the exact solver's step for
    /// ONE group: the sampler holds nothing bigger than a row, so it does not
    /// coarsen as the model grows. Callers comparing the two solvers pass the
    /// exact solver's grid in.
    pub fn new(
        measurements: &[Measurement],
        root: &Element,
        grid: Option<Vec<f64>>,
        uniform_span_prior: bool,
        cal: &Calibrator,
    ) -> Result<Self, Error> {
        let plan = plan_model(root, measurements);
        let element_bounds = inherited_bounds(root);
        let grid = match grid {
            Some(g) => g,
            None => build_grid(measurements, &element_bounds, 1, cal)?,
        };
        let dt = grid[1] - grid[0];
        let m = grid.len();
        let tables = date_tables(&grid, measurements, &element_bounds, None, None, cal)?;

        let date_ids: Vec<String> = measurements.iter().map(|x| x.id.clone()).collect();

        // Deposition sequences: their undated levels become parameters with a
        // flat likelihood, drawn like any date.
        let mut level_ids: Vec<String> = Vec::new();
        let mut level_names_by_id: HashMap<String, String> = HashMap::new();
        let mut deposition_specs = Vec::new();
        for group in &plan.deposition_groups {
            let depths: Vec<f64> = group.levels.iter().map(|(_, d, _)| *d).collect();
            let depth_gaps: Vec<f64> = depths.windows(2).map(|w| (w[1] - w[0]).abs()).collect();
            if depth_gaps.iter().any(|&g| g <= 0.0) {
                return Err(Error::Model(format!(
                    "the deposition sequence '{}' has two levels at the same depth. The model \
                     prices the interval between successive depths, and a zero interval has no rate",
                    group.name
                )));
            }
            for (pid, depth, dated) in &group.levels {
                if *dated {
                    continue;
                }
                level_ids.push(pid.clone());
                let name = group
                    .level_names
                    .get(pid)
                    .filter(|n| !n.is_empty())
                    .cloned()
                    .unwrap_or_else(|| {
                        format!("{} {}", crate::pyfmt::g(*depth, 6), group.depth_units)
                    });
                level_names_by_id.insert(pid.clone(), name);
            }
            deposition_specs.push((group, depth_gaps));
        }

        let boundary_ids: Vec<String> =
            plan.boundary_names.iter().map(|(b, _)| b.clone()).collect();
        let mut params: Vec<String> = date_ids.clone();
        params.extend(level_ids.iter().cloned());
        params.extend(boundary_ids.iter().cloned());
        let param_row: HashMap<String, usize> = params
            .iter()
            .enumerate()
            .map(|(i, p)| (p.clone(), i))
            .collect();
        let num_dates = date_ids.len();
        let num_drawable = num_dates + level_ids.len();
        let date_rows: Vec<usize> = (0..num_dates).collect();
        let level_rows: Vec<usize> = (num_dates..num_drawable).collect();
        let boundary_rows: Vec<usize> = (num_drawable..params.len()).collect();

        let flat = vec![1.0 / (m as f64 * dt); m];
        let mut likelihood: Vec<Vec<f64>> = Vec::with_capacity(num_drawable);
        for id in &date_ids {
            likelihood.push(tables.likelihood[id].clone());
        }
        for _ in &level_ids {
            likelihood.push(flat.clone());
        }
        let log_likelihood = likelihood
            .iter()
            .map(|row| row.iter().map(|&l| l.max(1e-300).ln()).collect())
            .collect();
        let cdf = likelihood.iter().map(|row| cumsum(row)).collect();

        let depositions: Vec<Deposition> = deposition_specs
            .iter()
            .map(|(group, depth_gaps)| Deposition {
                element_id: group.element_id.clone(),
                name: group.name.clone(),
                ids: group
                    .levels
                    .iter()
                    .map(|(pid, _, _)| param_row[pid])
                    .collect(),
                depth_gaps: depth_gaps.clone(),
                fixed_k: group.k,
                k_nodes: match group.k {
                    Some(k) => vec![k],
                    None => linspace(group.log_k_min, group.log_k_max, DEPOSITION_K_NODES)
                        .into_iter()
                        .map(|e| 10.0_f64.powf(std::hint::black_box(e)))
                        .collect(),
                },
                log_k_min: group.log_k_min,
                log_k_max: group.log_k_max,
                depth_units: group.depth_units.clone(),
            })
            .collect();
        let mut deposition_of = HashMap::new();
        for (d, deposition) in depositions.iter().enumerate() {
            for (position, &row) in deposition.ids.iter().enumerate() {
                deposition_of.insert(row, (d, position));
            }
        }
        let level_names = level_names_by_id
            .into_iter()
            .map(|(id, n)| (param_row[&id], n))
            .collect();

        let mut model = Self {
            measurements: measurements.to_vec(),
            plan,
            element_bounds,
            uniform_span_prior,
            grid,
            dt,
            m,
            params,
            param_row,
            num_dates,
            num_drawable,
            date_rows,
            level_rows,
            boundary_rows,
            likelihood,
            log_likelihood,
            cdf,
            outlier_parts: tables.outlier_parts,
            outlier_models: Vec::new(),
            scale_grid: Vec::new(),
            scaled_dates: Vec::new(),
            model_of: Vec::new(),
            likelihood_at: HashMap::new(),
            log_likelihood_at: HashMap::new(),
            cdf_at: HashMap::new(),
            responsibility_at: HashMap::new(),
            depositions,
            level_names,
            deposition_of,
            below: Vec::new(),
            above: Vec::new(),
            group_dates: Vec::new(),
            window: Vec::new(),
            span_priors: Vec::new(),
            limits_tables: HashMap::new(),
            span_prior_rows: Vec::new(),
            log_span_of_cells: Vec::new(),
            boundary_groups: HashMap::new(),
            boundary_priors: HashMap::new(),
            edge_younger: Vec::new(),
            edge_older: Vec::new(),
            tie_pairs: BTreeSet::new(),
            tied_with: Vec::new(),
            edge_tied_with: Vec::new(),
            date_tied_with: Vec::new(),
            tie_younger: Vec::new(),
            tie_older: Vec::new(),
            date_tie_pairs: Vec::new(),
            younger_dates: HashMap::new(),
            blocks: Vec::new(),
            cluster_weights: RefCell::default(),
            tempered: RefCell::default(),
            peak_gaps_cache: RefCell::default(),
            lobes_cache: RefCell::default(),
        };
        model.build_outlier_tables();
        model.build_constraints();
        model.build_blocks();
        Ok(model)
    }

    pub fn is_boundary(&self, row: usize) -> bool {
        row >= self.num_drawable
    }

    pub fn is_drawable(&self, row: usize) -> bool {
        row < self.num_drawable
    }

    /// The id of a parameter row.
    pub fn id(&self, row: usize) -> &str {
        &self.params[row]
    }

    // -- the shared outlier scale ------------------------------------------

    /// For every flagged date, its likelihood at each candidate offset SCALE,
    /// plus one shared scale per outlier model, drawn like anything else.
    /// OxCal's Outlier_Model carries ONE scale for all its dates; exact_chain
    /// marginalises it per date, which is a different model.
    fn build_outlier_tables(&mut self) {
        let mut names: BTreeSet<String> = BTreeSet::new();
        for x in &self.measurements {
            if self.outlier_parts.contains_key(&x.id) {
                names.insert(x.outlier_model().to_owned());
            }
        }
        self.outlier_models = names.into_iter().collect();
        self.scale_grid = self.outlier_models.iter().map(|n| scale_nodes(n)).collect();
        self.scaled_dates = vec![Vec::new(); self.outlier_models.len()];
        self.model_of = vec![None; self.params.len()];
        for (row, x) in self.measurements.iter().enumerate() {
            if self.outlier_parts.contains_key(&x.id) {
                let model = self
                    .outlier_models
                    .iter()
                    .position(|n| n == x.outlier_model())
                    .unwrap();
                self.scaled_dates[model].push(row);
                self.model_of[row] = Some(model);
            }
        }
        let half_width = OUTLIER_KERNEL_HALF_WIDTH_YEARS.max(self.grid[self.m - 1] - self.grid[0]);
        for (model, name) in self.outlier_models.iter().enumerate() {
            for &row in &self.scaled_dates[model] {
                let (inlier, _broad, prior) = &self.outlier_parts[&self.params[row]];
                let mut mixtures = Vec::with_capacity(self.scale_grid[model].len());
                let mut responsibility = Vec::with_capacity(self.scale_grid[model].len());
                for &log_scale in &self.scale_grid[model] {
                    let kernel = outlier_kernel_at_scale(self.dt, half_width, name, log_scale);
                    let broad = convolve_on_grid(inlier, &kernel);
                    let mixture: Vec<f64> = inlier
                        .iter()
                        .zip(&broad)
                        .map(|(i, b)| (1.0 - prior) * i + prior * b)
                        .collect();
                    responsibility.push(
                        mixture
                            .iter()
                            .zip(&broad)
                            .map(|(&mx, &b)| {
                                if mx > 0.0 {
                                    prior * b / mx.max(1e-300)
                                } else {
                                    0.0
                                }
                            })
                            .collect(),
                    );
                    mixtures.push(mixture);
                }
                let logs = mixtures
                    .iter()
                    .map(|row: &Vec<f64>| row.iter().map(|&v| v.max(1e-300).ln()).collect())
                    .collect();
                let cdfs = mixtures.iter().map(|row| cumsum(row)).collect();
                self.likelihood_at.insert(row, mixtures);
                self.log_likelihood_at.insert(row, logs);
                self.cdf_at.insert(row, cdfs);
                self.responsibility_at.insert(row, responsibility);
            }
        }
    }

    /// The node this date's outlier model sits at, or None if it has none.
    pub fn scale_of(&self, row: usize, state: &State) -> Option<usize> {
        self.model_of[row].map(|model| state.scale[model])
    }

    /// (density, cumulative) of a drawable row's likelihood at power `beta`,
    /// cached per power.
    fn likelihood_tables(
        &self,
        row: usize,
        node: Option<usize>,
        beta: f64,
    ) -> (Vec<f64>, Vec<f64>) {
        let density = match node {
            None => &self.likelihood[row],
            Some(n) => &self.likelihood_at[&row][n],
        };
        if beta == 1.0 {
            let cdf = match node {
                None => &self.cdf[row],
                Some(n) => &self.cdf_at[&row][n],
            };
            return (density.clone(), cdf.clone());
        }
        let key = (row, node, beta.to_bits());
        if let Some(found) = self.tempered.borrow().get(&key) {
            return found.clone();
        }
        let powered: Vec<f64> = density.iter().map(|&d| d.powf(beta)).collect();
        let cumulative = cumsum(&powered);
        self.tempered
            .borrow_mut()
            .insert(key, (powered.clone(), cumulative.clone()));
        (powered, cumulative)
    }

    /// The dates' log likelihood at their current positions, untempered:
    /// what a swap between tempered copies is decided on.
    pub fn log_likelihood_sum(&self, state: &State) -> f64 {
        let mut total = 0.0;
        for row in 0..self.num_drawable {
            let cell = state.index[row] as usize;
            total += match self.model_of[row] {
                None => self.log_likelihood[row][cell],
                Some(model) => self.log_likelihood_at[&row][state.scale[model]][cell],
            };
        }
        total
    }

    /// Exact draw of one outlier model's shared scale from its full
    /// conditional: the product of its dates' likelihoods at their current
    /// positions over a uniform prior on the log scale.
    pub fn draw_scale(&self, model: usize, state: &State, u: f64, beta: f64) -> usize {
        let dates = &self.scaled_dates[model];
        let nodes = self.scale_grid[model].len();
        if dates.is_empty() {
            return state.scale[model];
        }
        let mut log_weights = vec![0.0; nodes];
        for &row in dates {
            let table = &self.log_likelihood_at[&row];
            let cell = state.index[row] as usize;
            for (node, w) in log_weights.iter_mut().enumerate() {
                *w += beta * table[node][cell];
            }
        }
        let peak = log_weights
            .iter()
            .copied()
            .fold(f64::NEG_INFINITY, f64::max);
        let weights: Vec<f64> = log_weights.iter().map(|w| (w - peak).exp()).collect();
        let total = sum(&weights);
        if !total.is_finite() || total <= 0.0 {
            return state.scale[model];
        }
        let cdf: Vec<f64> = cumsum(&weights).into_iter().map(|c| c / total).collect();
        searchsorted_left(&cdf, u).min(nodes - 1)
    }

    // -- deposition ---------------------------------------------------------

    fn deposition_gaps(&self, deposition: &Deposition, state: &State) -> Vec<f64> {
        let ages: Vec<f64> = deposition
            .ids
            .iter()
            .map(|&r| self.grid[state.index[r] as usize])
            .collect();
        ages.windows(2).map(|w| -(w[1] - w[0])).collect()
    }

    fn alpha_of(&self, d: usize, state: &State) -> Vec<f64> {
        let deposition = &self.depositions[d];
        let node = state.rate[d].min(deposition.k_nodes.len() - 1);
        deposition.shape_parameters(deposition.k_nodes[node])
    }

    /// The deposition prior as a function of where `row` sits, over the grid
    /// cells `candidates`, or None if it is in no sequence.
    fn log_deposition_terms(
        &self,
        row: usize,
        state: &State,
        candidates: &[usize],
    ) -> Option<Vec<f64>> {
        let &(d, position) = self.deposition_of.get(&row)?;
        let deposition = &self.depositions[d];
        let alpha = self.alpha_of(d, state);
        let ids = &deposition.ids;
        let ages: Vec<f64> = candidates.iter().map(|&c| self.grid[c]).collect();
        let mut log_dens = vec![0.0; candidates.len()];
        let term = |gap: f64, exponent: f64| -> f64 {
            if gap > 0.0 {
                exponent * gap.ln()
            } else {
                f64::NEG_INFINITY
            }
        };
        if position > 0 {
            let older = self.grid[state.index[ids[position - 1]] as usize];
            for (l, a) in log_dens.iter_mut().zip(&ages) {
                *l += term(older - a, alpha[position - 1] - 1.0);
            }
        }
        if position < ids.len() - 1 {
            let younger = self.grid[state.index[ids[position + 1]] as usize];
            for (l, a) in log_dens.iter_mut().zip(&ages) {
                *l += term(a - younger, alpha[position] - 1.0);
            }
        }
        if position == 0 || position == ids.len() - 1 {
            // Written so the span is positive when the ordering holds, rather
            // than an absolute value that would weigh a crossed candidate.
            let other_row = if position == 0 {
                ids[ids.len() - 1]
            } else {
                ids[0]
            };
            let other = self.grid[state.index[other_row] as usize];
            let big_a = sum(&alpha);
            for (l, a) in log_dens.iter_mut().zip(&ages) {
                let total = if position == 0 { a - other } else { other - a };
                *l += if total > 0.0 {
                    -(big_a - 1.0) * total.ln()
                } else {
                    f64::NEG_INFINITY
                };
            }
        }
        Some(log_dens)
    }

    /// Exact draw of one sequence's rigidity from its full conditional, over
    /// a log-uniform prior on k.
    pub fn draw_deposition_rate(&self, d: usize, state: &State, u: f64) -> usize {
        let deposition = &self.depositions[d];
        if !deposition.variable() {
            return 0;
        }
        let gaps = self.deposition_gaps(deposition, state);
        if gaps.iter().any(|&g| g <= 0.0) {
            return state.rate[d];
        }
        let log_gaps: Vec<f64> = gaps.iter().map(|g| g.ln()).collect();
        let log_total = sum(&gaps).ln();
        let log_weights: Vec<f64> = deposition
            .k_nodes
            .iter()
            .map(|&k| {
                let alpha: Vec<f64> = deposition.depth_gaps.iter().map(|dz| k * dz).collect();
                let big_a = sum(&alpha);
                let gammas: Vec<f64> = alpha.iter().map(|&a| crate::special::gammaln(a)).collect();
                let logs: Vec<f64> = alpha
                    .iter()
                    .zip(&log_gaps)
                    .map(|(a, lg)| (a - 1.0) * lg)
                    .collect();
                crate::special::gammaln(big_a) - sum(&gammas) + sum(&logs)
                    - (big_a - 1.0) * log_total
            })
            .collect();
        let finite_max = log_weights
            .iter()
            .copied()
            .filter(|w| w.is_finite())
            .fold(f64::NEG_INFINITY, f64::max);
        if finite_max == f64::NEG_INFINITY {
            return state.rate[d];
        }
        let weights: Vec<f64> = log_weights
            .iter()
            .map(|&w| {
                if w.is_finite() {
                    (w - finite_max).exp()
                } else {
                    0.0
                }
            })
            .collect();
        let total = sum(&weights);
        if !total.is_finite() || total <= 0.0 {
            return state.rate[d];
        }
        let cdf: Vec<f64> = cumsum(&weights).into_iter().map(|c| c / total).collect();
        searchsorted_left(&cdf, u).min(deposition.k_nodes.len() - 1)
    }

    // -- structure ----------------------------------------------------------

    /// Index range this parameter's own TPQ/TAQ limiters allow.
    fn own_window(&self, row: usize) -> (i64, i64) {
        let (taq, tpq) = if row < self.num_dates {
            effective_date_bounds(&self.measurements[row], &self.element_bounds)
        } else {
            self.element_bounds
                .get(&self.params[row])
                .copied()
                .unwrap_or((None, None))
        };
        let mut lo = 0_i64;
        let mut hi = self.m as i64 - 1;
        if let Some(taq) = taq {
            lo = lo.max(searchsorted_left(&self.grid, taq) as i64);
        }
        if let Some(tpq) = tpq {
            hi = hi.min(crate::numeric::searchsorted_right(&self.grid, tpq) as i64 - 1);
        }
        (lo, hi)
    }

    /// For each parameter: which others must be older than it, and which
    /// younger; then everything derived from that.
    fn build_constraints(&mut self) {
        let n = self.params.len();
        self.below = vec![Vec::new(); n];
        self.above = vec![Vec::new(); n];
        let add = |below: &mut Vec<Vec<usize>>,
                   above: &mut Vec<Vec<usize>>,
                   younger: Option<usize>,
                   older: Option<usize>| {
            if let (Some(y), Some(o)) = (younger, older) {
                below[y].push(o);
                above[o].push(y);
            }
        };
        let row_of = |id: &str| self.param_row.get(id).copied();
        let (mut below, mut above) = (
            std::mem::take(&mut self.below),
            std::mem::take(&mut self.above),
        );
        for (older_terms, younger_terms) in &self.plan.ordering {
            for (_, older) in older_terms {
                for (_, younger) in younger_terms {
                    add(&mut below, &mut above, row_of(younger), row_of(older));
                }
            }
        }
        // Containment: a group's dates sit between its two boundaries; its
        // contained levels too, without joining the span term.
        let mut group_dates = Vec::new();
        for group in &self.plan.span_groups {
            let older = row_of(&group.older_boundary_id);
            let younger = row_of(&group.younger_boundary_id);
            let dates: Vec<usize> = group
                .date_ids
                .iter()
                .filter_map(|d| row_of(d).filter(|&r| self.is_drawable(r)))
                .collect();
            for &d in &dates {
                add(&mut below, &mut above, Some(d), older);
                add(&mut below, &mut above, younger, Some(d));
            }
            for contained in &group.contained_ids {
                let Some(c) = row_of(contained).filter(|&r| self.is_drawable(r)) else {
                    continue;
                };
                add(&mut below, &mut above, Some(c), older);
                add(&mut below, &mut above, younger, Some(c));
            }
            if let (Some(o), Some(y)) = (older, younger) {
                group_dates.push((o, y, dates));
            }
        }
        self.below = below;
        self.above = above;
        self.group_dates = group_dates;

        self.window = (0..n).map(|r| self.own_window(r)).collect();
        self.span_priors = if self.uniform_span_prior {
            self.compute_span_priors()
        } else {
            Vec::new()
        };
        self.limits_tables = self.limits_factor_tables();
        self.span_prior_rows = self.compute_span_prior_rows();

        // Every span is a whole number of cells, so -K log(span) is a gather.
        self.log_span_of_cells = (0..self.m).map(|i| (i as f64 * self.dt).ln()).collect();
        // Both boundaries of a group in one cell, each date tied to both a
        // quarter: the span that leaves the group at its limit is a quarter
        // of a cell.
        self.log_span_of_cells[0] = (self.dt / 4.0).ln();

        let mut boundary_groups: HashMap<usize, Vec<(usize, bool)>> = self
            .boundary_rows
            .iter()
            .map(|&b| (b, Vec::new()))
            .collect();
        for (gi, (older, younger, dates)) in self.group_dates.iter().enumerate() {
            if dates.is_empty() {
                continue;
            }
            boundary_groups.entry(*older).or_default().push((gi, true));
            boundary_groups
                .entry(*younger)
                .or_default()
                .push((gi, false));
        }
        self.boundary_groups = boundary_groups;
        let mut boundary_priors: HashMap<usize, Vec<(usize, bool)>> = self
            .boundary_rows
            .iter()
            .map(|&b| (b, Vec::new()))
            .collect();
        for (pi, &(oldest, youngest, _)) in self.span_priors.iter().enumerate() {
            boundary_priors.entry(oldest).or_default().push((pi, true));
            boundary_priors
                .entry(youngest)
                .or_default()
                .push((pi, false));
        }
        self.boundary_priors = boundary_priors;

        let pairs: Vec<(usize, usize)> = (0..n)
            .flat_map(|p| self.below[p].iter().map(move |&o| (p, o)))
            .collect();
        self.edge_younger = pairs.iter().map(|&(y, _)| y).collect();
        self.edge_older = pairs.iter().map(|&(_, o)| o).collect();

        // The ties that carry a half weight: each ordering edge once, with a
        // date at one end at least and no deposition level at either -- and
        // each pair of neighbouring boundaries.
        let mut tie_pairs: BTreeSet<(usize, usize)> = BTreeSet::new();
        for &(younger, older) in &pairs {
            if (self.is_boundary(younger) && self.is_boundary(older))
                || self.deposition_of.contains_key(&younger)
                || self.deposition_of.contains_key(&older)
            {
                continue;
            }
            tie_pairs.insert((younger, older));
        }
        tie_pairs.extend(self.neighbouring_boundaries());
        self.tie_pairs = tie_pairs.clone();
        self.tied_with = vec![Vec::new(); n];
        for &(y, o) in &tie_pairs {
            self.tied_with[y].push(o);
            self.tied_with[o].push(y);
        }
        // Ties between two DATES are weighted per cluster (cluster_weight);
        // every other keeps its half per edge.
        let is_plain_date = |r: usize| r < self.num_dates && !self.deposition_of.contains_key(&r);
        let date_ties: Vec<(usize, usize)> = tie_pairs
            .iter()
            .copied()
            .filter(|&(y, o)| is_plain_date(y) && is_plain_date(o))
            .collect();
        let edge_ties: Vec<(usize, usize)> = tie_pairs
            .iter()
            .copied()
            .filter(|pair| !date_ties.contains(pair))
            .collect();
        self.date_tied_with = vec![Vec::new(); n];
        self.edge_tied_with = vec![Vec::new(); n];
        for &(y, o) in &date_ties {
            self.date_tied_with[y].push(o);
            self.date_tied_with[o].push(y);
        }
        for &(y, o) in &edge_ties {
            self.edge_tied_with[y].push(o);
            self.edge_tied_with[o].push(y);
        }
        self.tie_younger = edge_ties.iter().map(|&(y, _)| y).collect();
        self.tie_older = edge_ties.iter().map(|&(_, o)| o).collect();
        if !date_ties.is_empty() {
            let reachable = self.younger_reachable();
            self.younger_dates = (0..self.num_dates)
                .filter(|&d| is_plain_date(d))
                .map(|d| {
                    (
                        d,
                        reachable[d]
                            .iter()
                            .copied()
                            .filter(|&r| is_plain_date(r))
                            .collect(),
                    )
                })
                .collect();
        }
        self.date_tie_pairs = date_ties;
    }

    /// The weight of a set of dates sitting in ONE cell, joined by ordering
    /// ties: the number of orders the model allows them (their linear
    /// extensions) over k!. Two ordered dates get 1/2, as the trapezoid does;
    /// a date older than two others in one cell gets 1/3, where a half per
    /// tie gave 1/4.
    fn cluster_weight(&self, members: &BTreeSet<usize>) -> f64 {
        let key: Vec<usize> = members.iter().copied().collect();
        if let Some(&w) = self.cluster_weights.borrow().get(&key) {
            return w;
        }
        let k = key.len();
        let weight = if k < 2 {
            1.0
        } else {
            let position: HashMap<usize, usize> =
                key.iter().enumerate().map(|(i, &d)| (d, i)).collect();
            let mut older = vec![0_u64; k];
            for &d in &key {
                for younger in self.younger_dates.get(&d).into_iter().flatten() {
                    if let Some(&p) = position.get(younger) {
                        older[p] |= 1 << position[&d];
                    }
                }
            }
            let mut ways = vec![0_u128; 1 << k];
            ways[0] = 1;
            for placed in 0..(1_usize << k) {
                if ways[placed] == 0 {
                    continue;
                }
                for (i, &needs) in older.iter().enumerate() {
                    if placed >> i & 1 == 0 && needs & !(placed as u64) == 0 {
                        ways[placed | 1 << i] += ways[placed];
                    }
                }
            }
            let factorial: u128 = (1..=k as u128).product();
            ways[(1 << k) - 1] as f64 / factorial as f64
        };
        self.cluster_weights.borrow_mut().insert(key, weight);
        weight
    }

    /// The dates in `cell` joined to `start` through date ties.
    fn cluster(
        &self,
        start: &[usize],
        cell: i64,
        index: &[i64],
        leave_out: Option<usize>,
    ) -> BTreeSet<usize> {
        let mut found = BTreeSet::new();
        let mut stack: Vec<usize> = start.to_vec();
        while let Some(d) = stack.pop() {
            if found.contains(&d) || Some(d) == leave_out || index[d] != cell {
                continue;
            }
            found.insert(d);
            stack.extend(&self.date_tied_with[d]);
        }
        found
    }

    /// What placing `row` in `cell` multiplies the joint's cluster weights
    /// by: its cluster there with it, over the clusters its tied neighbours
    /// there form without it.
    fn date_tie_factor(&self, row: usize, cell: i64, index: &[i64]) -> f64 {
        let neighbours: Vec<usize> = self.date_tied_with[row]
            .iter()
            .copied()
            .filter(|&o| index[o] == cell)
            .collect();
        if neighbours.is_empty() {
            return 1.0;
        }
        let mut without = 1.0;
        let mut joined = BTreeSet::new();
        for neighbour in neighbours {
            if joined.contains(&neighbour) {
                continue;
            }
            let part = self.cluster(&[neighbour], cell, index, Some(row));
            joined.extend(part.iter().copied());
            without *= self.cluster_weight(&part);
        }
        joined.insert(row);
        self.cluster_weight(&joined) / without
    }

    /// log of the product of cluster weights over every cluster of tied dates.
    fn log_cluster_weights(&self, index: &[i64]) -> f64 {
        let mut total = 0.0;
        let mut seen = HashSet::new();
        for &(younger, older) in &self.date_tie_pairs {
            if index[younger] != index[older] || seen.contains(&younger) {
                continue;
            }
            let members = self.cluster(&[younger], index[younger], index, None);
            seen.extend(members.iter().copied());
            total += self.cluster_weight(&members).ln();
        }
        total
    }

    /// The whole span-prior factor per prior, in the log, as a row indexed by
    /// the span in CELLS: -log C(cells - 1, n_interior) plus the limits factor
    /// where there is one, and -inf where the interior boundaries do not fit.
    fn compute_span_prior_rows(&self) -> Vec<Vec<f64>> {
        let cells: Vec<f64> = (0..self.m).map(|i| i as f64).collect();
        self.span_priors
            .iter()
            .map(|&(oldest, youngest, exponent)| {
                let log_count = log_interior_placements(&cells, exponent);
                let mut row: Vec<f64> = log_count
                    .iter()
                    .map(|&c| if c.is_finite() { -c } else { f64::NEG_INFINITY })
                    .collect();
                if let Some(limits) = self.limits_tables.get(&(oldest, youngest)) {
                    for (r, l) in row.iter_mut().zip(limits) {
                        *r += l;
                    }
                }
                row
            })
            .collect()
    }

    /// Narrow each parameter's window to `ranges` and recompute the span
    /// prior's limits factor from the Boundaries' narrowed windows -- what
    /// observed OxCal 4.4 behaviour does once, partway through a run.
    pub fn shrink_limits(&mut self, ranges: &HashMap<usize, (i64, i64)>) {
        for (&row, &(lo, hi)) in ranges {
            let (own_lo, own_hi) = self.window[row];
            self.window[row] = (own_lo.max(lo), own_hi.min(hi));
        }
        let spans: Vec<f64> = (0..self.m).map(|i| i as f64 * self.dt).collect();
        let mut tables = HashMap::new();
        for &(oldest, youngest, _) in &self.span_priors {
            let o = self.window[oldest];
            let y = self.window[youngest];
            let factor = log_limits_factor_of_span(
                &spans,
                (self.grid[o.1 as usize], self.grid[o.0 as usize]),
                (self.grid[y.1 as usize], self.grid[y.0 as usize]),
            );
            if let Some(f) = factor {
                tables.insert((oldest, youngest), f);
            }
        }
        self.limits_tables = tables;
        self.span_prior_rows = self.compute_span_prior_rows();
    }

    /// (younger, older) for every pair of boundaries with no boundary between
    /// them in the ordering: the pairs whose meeting in one cell is a tie.
    fn neighbouring_boundaries(&self) -> BTreeSet<(usize, usize)> {
        let reachable = self.younger_reachable();
        let below: HashMap<usize, HashSet<usize>> = self
            .boundary_rows
            .iter()
            .map(|&b| {
                (
                    b,
                    reachable[b]
                        .iter()
                        .copied()
                        .filter(|&r| self.is_boundary(r))
                        .collect(),
                )
            })
            .collect();
        let mut pairs = BTreeSet::new();
        for &older in &self.boundary_rows {
            for &younger in &below[&older] {
                if !below[&older]
                    .iter()
                    .any(|between| below[between].contains(&younger))
                {
                    pairs.insert((younger, older));
                }
            }
        }
        pairs
    }

    /// For every parameter, everything the ordering forces to be YOUNGER
    /// than it, transitively.
    pub fn younger_reachable(&self) -> Vec<HashSet<usize>> {
        fn walk(
            model: &Model,
            row: usize,
            reachable: &mut Vec<Option<HashSet<usize>>>,
        ) -> HashSet<usize> {
            if let Some(found) = &reachable[row] {
                return found.clone();
            }
            reachable[row] = Some(HashSet::new());
            let mut found = HashSet::new();
            for &younger in &model.above[row] {
                found.insert(younger);
                found.extend(walk(model, younger, reachable));
            }
            reachable[row] = Some(found.clone());
            found
        }
        let mut reachable = vec![None; self.params.len()];
        for row in 0..self.params.len() {
            walk(self, row, &mut reachable);
        }
        reachable
            .into_iter()
            .map(Option::unwrap_or_default)
            .collect()
    }

    /// The UniformSpanPrior corrections, as (oldest boundary, youngest
    /// boundary, exponent) -- one per connected component of the boundary
    /// ordering with a unique outermost pair.
    fn compute_span_priors(&self) -> Vec<(usize, usize, usize)> {
        if self.boundary_rows.len() < 2 {
            return Vec::new();
        }
        let reachable = self.younger_reachable();
        let younger_boundaries: HashMap<usize, HashSet<usize>> = self
            .boundary_rows
            .iter()
            .map(|&b| {
                (
                    b,
                    reachable[b]
                        .iter()
                        .copied()
                        .filter(|&r| self.is_boundary(r))
                        .collect(),
                )
            })
            .collect();
        let mut parent: HashMap<usize, usize> =
            self.boundary_rows.iter().map(|&b| (b, b)).collect();
        fn find(parent: &mut HashMap<usize, usize>, mut b: usize) -> usize {
            while parent[&b] != b {
                let grand = parent[&parent[&b]];
                parent.insert(b, grand);
                b = grand;
            }
            b
        }
        for &b in &self.boundary_rows {
            let mut others: Vec<usize> = younger_boundaries[&b].iter().copied().collect();
            others.sort_unstable();
            for other in others {
                let (root_other, root_b) = (find(&mut parent, other), find(&mut parent, b));
                parent.insert(root_other, root_b);
            }
        }
        let mut components: Vec<(usize, Vec<usize>)> = Vec::new();
        for &b in &self.boundary_rows {
            let root = find(&mut parent, b);
            match components.iter_mut().find(|(r, _)| *r == root) {
                Some((_, members)) => members.push(b),
                None => components.push((root, vec![b])),
            }
        }
        let mut priors = Vec::new();
        for (_, members) in components {
            if members.len() < 2 {
                continue;
            }
            let exponent = members.len() - 2;
            let rest: HashSet<usize> = members.iter().copied().collect();
            let oldest: Vec<usize> = members
                .iter()
                .copied()
                .filter(|b| {
                    rest.iter()
                        .all(|o| o == b || younger_boundaries[b].contains(o))
                })
                .collect();
            let youngest: Vec<usize> = members
                .iter()
                .copied()
                .filter(|b| {
                    rest.iter()
                        .all(|o| o == b || younger_boundaries[o].contains(b))
                })
                .collect();
            if oldest.len() == 1 && youngest.len() == 1 && oldest[0] != youngest[0] {
                priors.push((oldest[0], youngest[0], exponent));
            }
        }
        priors
    }

    /// -log g of Bronk Ramsey 2008 A.31 per span prior, over the span in
    /// cells, from each boundary's limits tightened by the dates it encloses.
    fn limits_factor_tables(&self) -> HashMap<(usize, usize), Vec<f64>> {
        let spans: Vec<f64> = (0..self.m).map(|i| i as f64 * self.dt).collect();
        let reachable = self.younger_reachable();
        let inliers: HashMap<String, Vec<f64>> = (0..self.num_dates)
            .map(|d| {
                let id = &self.params[d];
                let row = match self.outlier_parts.get(id) {
                    Some((inlier, _, _)) => inlier.clone(),
                    None => self.likelihood[d].clone(),
                };
                (id.clone(), row)
            })
            .collect();
        let mut tables = HashMap::new();
        for &(oldest, youngest, _) in &self.span_priors {
            let enclosed: Vec<String> = (0..self.num_dates)
                .filter(|&d| reachable[oldest].contains(&d) && reachable[d].contains(&youngest))
                .map(|d| self.params[d].clone())
                .collect();
            let (oldest_limits, youngest_limits) = pair_limits(
                &self.element_bounds,
                &self.params[oldest],
                &self.params[youngest],
                &self.grid,
                &inliers,
                &enclosed,
            );
            if let Some(f) = log_limits_factor_of_span(&spans, oldest_limits, youngest_limits) {
                tables.insert((oldest, youngest), f);
            }
        }
        tables
    }

    /// Drawable parameters grouped so no two in a block constrain each other,
    /// by greedy colouring of the conflict graph, highest degree first.
    fn build_blocks(&mut self) {
        let drawable: Vec<usize> = (0..self.num_drawable).collect();
        let mut conflicts: Vec<BTreeSet<usize>> = vec![BTreeSet::new(); self.num_drawable];
        for &d in &drawable {
            for &other in self.below[d].iter().chain(&self.above[d]) {
                if other < self.num_drawable {
                    conflicts[d].insert(other);
                    conflicts[other].insert(d);
                }
            }
        }
        let mut order = drawable.clone();
        order.sort_by_key(|&d| std::cmp::Reverse(conflicts[d].len()));
        let mut colour: Vec<Option<usize>> = vec![None; self.num_drawable];
        let mut by_colour: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
        for d in order {
            let taken: HashSet<usize> = conflicts[d].iter().filter_map(|&n| colour[n]).collect();
            let c = (0..=self.num_drawable)
                .find(|c| !taken.contains(c))
                .unwrap();
            colour[d] = Some(c);
            by_colour.entry(c).or_default().push(d);
        }
        self.blocks = by_colour.into_values().collect();
    }

    // -- conditionals ---------------------------------------------------------

    /// The index range `row` may currently occupy, constraints included. A
    /// parameter may share a cell with what it is ordered against, at the
    /// half weight a tie carries.
    pub fn allowed(&self, row: usize, state: &State) -> (i64, i64) {
        let (mut lo, mut hi) = self.window[row];
        for &older in &self.below[row] {
            hi = hi.min(state.index[older]);
        }
        for &younger in &self.above[row] {
            lo = lo.max(state.index[younger]);
        }
        (lo, hi)
    }

    /// The weight a date carries for sitting exactly ON something it is
    /// ordered against: a half per tied edge, and its cluster weight with
    /// tied dates in its cell.
    pub fn endpoint_weight(&self, row: usize, state: &State) -> f64 {
        let index = state.index[row];
        let mut weight = 1.0;
        for &other in &self.edge_tied_with[row] {
            if state.index[other] == index {
                weight *= 0.5;
            }
        }
        weight * self.date_tie_factor(row, index, &state.index)
    }

    /// Exact draw from a date's full conditional: its likelihood over the
    /// allowed interval, each end at the weight its ties give it.
    pub fn draw_date(&self, row: usize, state: &State, u: f64, beta: f64) -> i64 {
        let (lo, hi) = self.allowed(row, state);
        if hi <= lo {
            return if hi >= 0 {
                lo.max(hi.min(state.index[row]))
            } else {
                state.index[row]
            };
        }
        if self.deposition_of.contains_key(&row) {
            return self.draw_deposition_level(row, state, u, lo, hi, beta);
        }
        let node = self.scale_of(row, state);
        let (density, cdf) = self.likelihood_tables(row, node, beta);
        let (mut low_weight, mut high_weight) = (1.0, 1.0);
        for &other in &self.edge_tied_with[row] {
            let at = state.index[other];
            if at == lo {
                low_weight *= 0.5;
            } else if at == hi {
                high_weight *= 0.5;
            }
        }
        if !self.date_tied_with[row].is_empty() {
            low_weight *= self.date_tie_factor(row, lo, &state.index);
            high_weight *= self.date_tie_factor(row, hi, &state.index);
        }
        let (l, h) = (lo as usize, hi as usize);
        let interior = if h >= l + 2 { cdf[h - 1] - cdf[l] } else { 0.0 };
        let low_end = low_weight * density[l];
        let high_end = high_weight * density[h];
        let total = interior + low_end + high_end;
        if total <= 0.0 {
            // No likelihood anywhere it may be: uniform keeps the chain moving.
            return (lo as f64 + u * (hi - lo) as f64) as i64;
        }
        let target = u * total;
        if target < low_end {
            return lo;
        }
        if target < low_end + interior {
            let base = cdf[l];
            let picked = searchsorted_left(&cdf, base + (target - low_end)) as i64;
            return picked.max(lo + 1).min(hi - 1);
        }
        hi
    }

    /// Exact draw from a deposition level's full conditional: its likelihood
    /// times the deposition prior's neighbour terms, built over [lo, hi]. No
    /// endpoint weighting: a level is charged no span term.
    fn draw_deposition_level(
        &self,
        row: usize,
        state: &State,
        u: f64,
        lo: i64,
        hi: i64,
        beta: f64,
    ) -> i64 {
        let candidates: Vec<usize> = (lo as usize..=hi as usize).collect();
        let density = match self.scale_of(row, state) {
            None => &self.likelihood[row],
            Some(n) => &self.likelihood_at[&row][n],
        };
        let mut log_dens: Vec<f64> = candidates
            .iter()
            .map(|&c| beta * density[c].max(1e-300).ln())
            .collect();
        if let Some(terms) = self.log_deposition_terms(row, state, &candidates) {
            for (l, t) in log_dens.iter_mut().zip(terms) {
                *l += t;
            }
        }
        let finite_max = log_dens
            .iter()
            .copied()
            .filter(|v| v.is_finite())
            .fold(f64::NEG_INFINITY, f64::max);
        if finite_max == f64::NEG_INFINITY {
            return state.index[row];
        }
        let weights: Vec<f64> = log_dens
            .iter()
            .map(|&v| {
                if v.is_finite() {
                    (v - finite_max).exp()
                } else {
                    0.0
                }
            })
            .collect();
        let total = sum(&weights);
        if total <= 0.0 {
            return state.index[row];
        }
        let cdf = cumsum(&weights);
        lo + (searchsorted_left(&cdf, u * total) as i64).min(hi - lo)
    }

    /// Exact draw from a boundary's full conditional: the span factors of the
    /// groups it bounds and the span priors it is an end of, over its allowed
    /// interval, with the tie half-weights where something sits on a
    /// candidate.
    pub fn draw_boundary(&self, row: usize, state: &State, u: f64) -> i64 {
        let (lo, hi) = self.allowed(row, state);
        if hi <= lo {
            return state.index[row];
        }
        let width = (hi - lo + 1) as usize;
        let mut log_dens = vec![0.0; width];
        let gather =
            |table: &[f64], cells: i64| table[cells.clamp(0, table.len() as i64 - 1) as usize];
        for &(gi, is_older) in self.boundary_groups.get(&row).into_iter().flatten() {
            let (older, younger, dates) = &self.group_dates[gi];
            let k = dates.len() as f64;
            for (offset, l) in log_dens.iter_mut().enumerate() {
                let candidate = lo + offset as i64;
                let cells = if is_older {
                    candidate - state.index[*younger]
                } else {
                    state.index[*older] - candidate
                };
                *l += if cells >= 0 {
                    -k * gather(&self.log_span_of_cells, cells)
                } else {
                    f64::NEG_INFINITY
                };
            }
        }
        for &other in &self.tied_with[row] {
            let index = state.index[other];
            if lo <= index && index <= hi {
                log_dens[(index - lo) as usize] -= std::f64::consts::LN_2;
            }
        }
        for &(pi, is_oldest) in self.boundary_priors.get(&row).into_iter().flatten() {
            let (oldest, youngest, _) = self.span_priors[pi];
            for (offset, l) in log_dens.iter_mut().enumerate() {
                let candidate = lo + offset as i64;
                let cells = if is_oldest {
                    candidate - state.index[youngest]
                } else {
                    state.index[oldest] - candidate
                };
                *l += if cells >= 0 && cells < self.m as i64 {
                    gather(&self.span_prior_rows[pi], cells)
                } else {
                    f64::NEG_INFINITY
                };
            }
        }
        let finite_max = log_dens
            .iter()
            .copied()
            .filter(|v| v.is_finite())
            .fold(f64::NEG_INFINITY, f64::max);
        if finite_max == f64::NEG_INFINITY {
            return state.index[row];
        }
        let dens: Vec<f64> = log_dens
            .iter()
            .map(|&v| {
                if v.is_finite() {
                    (v - finite_max).exp()
                } else {
                    0.0
                }
            })
            .collect();
        let total = sum(&dens);
        if total <= 0.0 {
            return state.index[row];
        }
        let cdf = cumsum(&dens);
        lo + (searchsorted_left(&cdf, u * total) as i64).min(hi - lo)
    }

    /// The full log posterior at power `beta` on the likelihood: -inf when any
    /// hard constraint is violated.
    pub fn log_posterior(&self, state: &State, beta: f64) -> f64 {
        let index = &state.index;
        for (row, &(lo, hi)) in self.window.iter().enumerate() {
            if index[row] < lo || index[row] > hi {
                return f64::NEG_INFINITY;
            }
        }
        if self
            .edge_younger
            .iter()
            .zip(&self.edge_older)
            .any(|(&y, &o)| index[y] > index[o])
        {
            return f64::NEG_INFINITY;
        }
        let mut total = beta * self.log_likelihood_sum(state);
        let halves = self
            .tie_younger
            .iter()
            .zip(&self.tie_older)
            .filter(|&(&y, &o)| index[y] == index[o])
            .count();
        total -= halves as f64 * std::f64::consts::LN_2;
        if self
            .date_tie_pairs
            .iter()
            .any(|&(y, o)| index[y] == index[o])
        {
            total += self.log_cluster_weights(index);
        }
        for (d, deposition) in self.depositions.iter().enumerate() {
            let contribution = log_deposition_density(
                &self.deposition_gaps(deposition, state),
                &self.alpha_of(d, state),
            );
            if !contribution.is_finite() {
                return f64::NEG_INFINITY;
            }
            total += contribution;
        }
        for (older, younger, dates) in &self.group_dates {
            if dates.is_empty() {
                continue;
            }
            let cells = index[*older] - index[*younger];
            if cells < 0 {
                return f64::NEG_INFINITY;
            }
            total -= dates.len() as f64 * self.log_span_of_cells[cells as usize];
        }
        for (pi, &(oldest, youngest, _)) in self.span_priors.iter().enumerate() {
            let cells = index[oldest] - index[youngest];
            if cells < 0 || cells >= self.m as i64 {
                return f64::NEG_INFINITY;
            }
            let value = self.span_prior_rows[pi][cells as usize];
            if !value.is_finite() {
                return f64::NEG_INFINITY;
            }
            total += value;
        }
        total
    }

    // -- starting states -------------------------------------------------------

    /// A state satisfying every hard constraint, by walking the ordering from
    /// oldest to youngest and giving each parameter the cell it prefers that
    /// still leaves room for everything younger. Where it prefers to be is
    /// its likelihood mode, or with `quantile` that fraction of the way
    /// through its likelihood, or a lobe `wishes` names.
    pub fn initial_state(
        &self,
        quantile: Option<f64>,
        lobe: Option<&(usize, usize, String, u64)>,
        wishes: Option<Wishes>,
    ) -> Result<State, Error> {
        let n = self.params.len();
        // Longest chain of "must be older than" above each parameter.
        let mut depth: Vec<Option<usize>> = vec![None; n];
        fn compute_depth(
            model: &Model,
            row: usize,
            seen: &mut Vec<usize>,
            depth: &mut Vec<Option<usize>>,
        ) -> Result<usize, Error> {
            if let Some(d) = depth[row] {
                return Ok(d);
            }
            if seen.contains(&row) {
                return Err(Error::Model("the model structure is circular".into()));
            }
            seen.push(row);
            let mut value = 0;
            for &older in &model.below[row] {
                value = value.max(1 + compute_depth(model, older, seen, depth)?);
            }
            seen.pop();
            depth[row] = Some(value);
            Ok(value)
        }
        for row in 0..n {
            compute_depth(self, row, &mut Vec::new(), &mut depth)?;
        }

        let wishes = match wishes {
            Some(w) => w,
            None => match lobe {
                Some((rank, ranks, strategy, seed)) => {
                    self.start_lobes(*rank, *ranks, strategy, *seed)?
                }
                None => HashMap::new(),
            },
        };
        let mut preferred: Vec<i64> = vec![0; n];
        for (d, slot) in preferred.iter_mut().enumerate().take(self.num_dates) {
            *slot = if let Some(wished) = wishes.get(&d) {
                let ((peak, _, _, _), target) = wished[0];
                target.unwrap_or(peak as i64)
            } else if let Some(q) = quantile {
                let cdf = &self.cdf[d];
                (searchsorted_left(cdf, q * cdf[self.m - 1]) as i64).min(self.m as i64 - 1)
            } else {
                argmax(&self.likelihood[d]).unwrap() as i64
            };
        }
        let mut placed: HashSet<usize> = (0..self.num_dates).collect();
        for &level in &self.level_rows {
            // A flat table has no mode; start between the dated levels.
            let (d, _) = self.deposition_of[&level];
            let neighbours: Vec<f64> = self.depositions[d]
                .ids
                .iter()
                .filter(|r| placed.contains(r))
                .map(|&r| preferred[r] as f64)
                .collect();
            preferred[level] = if neighbours.is_empty() {
                (self.m / 2) as i64
            } else {
                mean(&neighbours) as i64
            };
            placed.insert(level);
        }
        for &b in &self.boundary_rows {
            let neighbours: Vec<f64> = self
                .group_dates
                .iter()
                .filter(|(o, y, _)| *o == b || *y == b)
                .flat_map(|(_, _, dates)| dates.iter().map(|&d| preferred[d] as f64))
                .collect();
            preferred[b] = if neighbours.is_empty() {
                (self.m / 2) as i64
            } else {
                mean(&neighbours) as i64
            };
            if !wishes.is_empty() {
                // Just outside every date it bounds, where the posterior puts
                // it anyway, so the walk does not drag the dates into
                // whatever lobe is younger than it.
                let sides: Vec<f64> = self
                    .group_dates
                    .iter()
                    .filter(|(o, y, dates)| !dates.is_empty() && (*o == b || *y == b))
                    .map(|(o, _, dates)| {
                        if b == *o {
                            (dates.iter().map(|&d| preferred[d]).max().unwrap() + 1) as f64
                        } else {
                            (dates.iter().map(|&d| preferred[d]).min().unwrap() - 1) as f64
                        }
                    })
                    .collect();
                if !sides.is_empty() {
                    preferred[b] = mean(&sides) as i64;
                }
            }
        }

        // The youngest cell each parameter may take and still leave a cell
        // for each link of the longest chain below it.
        let mut floor: Vec<Option<i64>> = vec![None; n];
        fn compute_floor(model: &Model, row: usize, floor: &mut Vec<Option<i64>>) -> i64 {
            if let Some(f) = floor[row] {
                return f;
            }
            let mut value = model.window[row].0;
            for &younger in &model.above[row] {
                value = value.max(1 + compute_floor(model, younger, floor));
            }
            floor[row] = Some(value);
            value
        }
        for row in 0..n {
            compute_floor(self, row, &mut floor);
        }

        // What each parameter must clear to leave the wished lobes of the
        // dates younger than it open.
        let lobe_floor: HashMap<usize, i64> = wishes
            .iter()
            .map(|(&d, w)| (d, self.lobe_floor(d, &w[0].0)))
            .collect();
        let mut clears: Vec<Option<i64>> = vec![None; n];
        fn compute_clears(
            model: &Model,
            row: usize,
            lobe_floor: &HashMap<usize, i64>,
            clears: &mut Vec<Option<i64>>,
        ) -> i64 {
            if let Some(c) = clears[row] {
                return c;
            }
            let mut value = -1;
            for &y in &model.above[row] {
                let inner = compute_clears(model, y, lobe_floor, clears);
                value = value.max(lobe_floor.get(&y).copied().unwrap_or(-1).max(inner) + 1);
            }
            clears[row] = Some(value);
            value
        }

        let mut order: Vec<usize> = (0..n).collect();
        order.sort_by_key(|&r| depth[r].unwrap());
        let mut index: Vec<i64> = vec![0; n];
        let mut set = vec![false; n];
        for row in order {
            let mut lo = floor[row].unwrap();
            let mut hi = self.window[row].1;
            for &older in &self.below[row] {
                debug_assert!(set[older]);
                hi = hi.min(index[older] - 1);
            }
            if !wishes.is_empty() {
                let c = compute_clears(self, row, &lobe_floor, &mut clears);
                if lo < c && c <= hi {
                    lo = c;
                }
            }
            if hi < lo {
                return Err(Error::Model(format!(
                    "the model has no state satisfying all of its constraints: '{}' must be older \
                     than {lo} and younger than {hi} on the calendar grid at once",
                    self.params[row]
                )));
            }
            let start = wishes
                .get(&row)
                .and_then(|w| self.start_in_lobe(row, w, lo, hi));
            index[row] = start.unwrap_or_else(|| preferred[row].clamp(lo, hi));
            set[row] = true;
        }
        // Mid-prior: its conditional is drawn exactly on the first sweep.
        let scale = self.scale_grid.iter().map(|g| g.len() / 2).collect();
        let rate = self
            .depositions
            .iter()
            .map(|d| d.k_nodes.len() / 2)
            .collect();
        let state = State { index, scale, rate };
        if !self.log_posterior(&state, 1.0).is_finite() {
            return Err(Error::Model(
                "could not find a feasible starting state for this model".into(),
            ));
        }
        Ok(state)
    }

    /// The youngest cell of `lobe` a start there may take: the first the lobe
    /// reaches LOBE_HELD_FRACTION of its peak.
    fn lobe_floor(&self, row: usize, lobe: &Lobe) -> i64 {
        let &(peak, _, first, last) = lobe;
        let inlier = self.inlier_row(row);
        let held = (first..last)
            .find(|&c| inlier[c] >= LOBE_HELD_FRACTION * inlier[peak])
            .unwrap();
        held as i64
    }

    /// The cell in [lo, hi] a date starts at: in the first wished lobe the
    /// window still holds, or None.
    fn start_in_lobe(
        &self,
        row: usize,
        wished: &[(Lobe, Option<i64>)],
        lo: i64,
        hi: i64,
    ) -> Option<i64> {
        let inlier = self.inlier_row(row);
        for &((peak, _, first, last), target) in wished {
            let a = lo.max(first as i64);
            let b = hi.min(last as i64 - 1);
            if a <= b {
                let inside = &inlier[a as usize..=b as usize];
                let held = LOBE_HELD_FRACTION * inlier[peak];
                if inside.iter().copied().fold(f64::NEG_INFINITY, f64::max) >= held {
                    if let Some(t) = target {
                        let cell = t.clamp(a, b);
                        if inlier[cell as usize] >= held {
                            return Some(cell);
                        }
                    }
                    return Some(a + argmax(inside).unwrap() as i64);
                }
            }
        }
        None
    }

    // -- moves ------------------------------------------------------------------

    /// The parameter sets a translation proposal is offered over: the whole
    /// model, each deposition sequence with what pens it in, each span group
    /// with its boundaries (and anything sharing one), the group with its
    /// nearest enclosing boundary on either side, and each flagged date with
    /// one of its boundaries.
    pub fn move_sets(&self) -> Vec<Vec<usize>> {
        let by_id = |rows: &BTreeSet<usize>| -> Vec<usize> {
            let mut out: Vec<usize> = rows.iter().copied().collect();
            out.sort_by(|a, b| self.params[*a].cmp(&self.params[*b]));
            out
        };
        let mut sets: Vec<Vec<usize>> = vec![(0..self.params.len()).collect()];
        for deposition in &self.depositions {
            let mut members: BTreeSet<usize> = deposition.ids.iter().copied().collect();
            for group in &self.plan.span_groups {
                let contained: HashSet<usize> = group
                    .contained_ids
                    .iter()
                    .filter_map(|c| self.param_row.get(c).copied())
                    .collect();
                if deposition.ids.iter().any(|r| contained.contains(r)) {
                    members.insert(self.param_row[&group.older_boundary_id]);
                    members.insert(self.param_row[&group.younger_boundary_id]);
                    members.extend(
                        group
                            .date_ids
                            .iter()
                            .filter_map(|d| self.param_row.get(d).copied()),
                    );
                }
            }
            sets.push(by_id(&members));
        }
        for (older, younger, dates) in &self.group_dates {
            if dates.is_empty() {
                continue;
            }
            let mut members: BTreeSet<usize> = dates.iter().copied().collect();
            members.insert(*older);
            members.insert(*younger);
            for (o2, y2, d2) in &self.group_dates {
                if [o2, y2].iter().any(|b| *b == older || *b == younger) {
                    members.extend(d2.iter().copied());
                    members.insert(*o2);
                    members.insert(*y2);
                }
            }
            sets.push(by_id(&members));
            sets.extend(self.with_enclosure(&members).iter().map(&by_id));
            if dates.len() > 1 {
                for &d in dates {
                    if !self.outlier_parts.contains_key(&self.params[d]) {
                        continue;
                    }
                    sets.push(by_id(&[d, *older].into_iter().collect()));
                    sets.push(by_id(&[d, *younger].into_iter().collect()));
                }
            }
        }
        sets
    }

    /// `members` with the nearest boundary penning it in from outside, once
    /// per side.
    fn with_enclosure(&self, members: &BTreeSet<usize>) -> Vec<BTreeSet<usize>> {
        let own: Vec<usize> = members
            .iter()
            .copied()
            .filter(|&m| self.is_boundary(m))
            .collect();
        let mut variants = Vec::new();
        for (outward, inward) in [(&self.below, &self.above), (&self.above, &self.below)] {
            let outside: BTreeSet<usize> = own
                .iter()
                .flat_map(|&m| outward[m].iter().copied())
                .filter(|&b| self.is_boundary(b) && !members.contains(&b))
                .collect();
            let nearest: BTreeSet<usize> = outside
                .iter()
                .copied()
                .filter(|&b| !inward[b].iter().any(|o| outside.contains(o)))
                .collect();
            if !nearest.is_empty() {
                variants.push(members.union(&nearest).copied().collect());
            }
        }
        variants
    }

    /// A date's likelihood without its outlier component.
    pub fn inlier_row(&self, row: usize) -> &[f64] {
        match self.outlier_parts.get(&self.params[row]) {
            Some((inlier, _, _)) => inlier,
            None => &self.likelihood[row],
        }
    }

    /// The distances, in cells, between the calibration peaks of the dates in
    /// `rows`: the jumps that carry a group between its lobes. Cached per set.
    pub fn peak_gaps(&self, rows: &[usize]) -> Vec<i64> {
        if let Some(found) = self.peak_gaps_cache.borrow().get(rows) {
            return found.clone();
        }
        let bin = round_even(PEAK_JUMP_JITTER_YEARS / self.dt).max(1);
        let mut counts: BTreeMap<i64, usize> = BTreeMap::new();
        for &row in rows {
            if !self.is_drawable(row) {
                continue;
            }
            let inlier = self.inlier_row(row);
            let peak = inlier.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            if peak <= 0.0 {
                continue;
            }
            let found = find_peaks(inlier, PEAK_PROMINENCE * peak);
            for (i, &a) in found.iter().enumerate() {
                for &b in &found[i + 1..] {
                    let gap = round_even((b as f64 - a as f64).abs() / bin as f64) * bin;
                    if gap > 2 * bin {
                        *counts.entry(gap).or_insert(0) += 1;
                    }
                }
            }
        }
        let mut common: Vec<i64> = counts.keys().copied().collect();
        common.sort_by_key(|g| (std::cmp::Reverse(counts[g]), *g));
        common.truncate(MAX_PEAK_GAPS);
        common.sort_unstable();
        self.peak_gaps_cache
            .borrow_mut()
            .insert(rows.to_vec(), common.clone());
        common
    }

    /// The lobes holding at least LOBE_MIN_MASS of `row` (an inlier row), in
    /// cell order, each running from the valley before its peak to the one
    /// after.
    pub fn lobes_of(row: &[f64]) -> Vec<Lobe> {
        let total = sum(row);
        let top = row.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        if total <= 0.0 || top <= 0.0 {
            return Vec::new();
        }
        let peaks = find_peaks(row, PEAK_PROMINENCE * top);
        if peaks.is_empty() {
            return Vec::new();
        }
        let valleys: Vec<usize> = peaks
            .windows(2)
            .map(|w| w[0] + crate::numeric::argmin(&row[w[0]..=w[1]]).unwrap())
            .collect();
        let mut edges = vec![0];
        edges.extend(valleys);
        edges.push(row.len());
        peaks
            .iter()
            .zip(edges.windows(2))
            .filter_map(|(&peak, e)| {
                let share = sum(&row[e[0]..e[1]]) / total;
                (share >= LOBE_MIN_MASS).then_some((peak, share, e[0], e[1]))
            })
            .collect()
    }

    /// The lobes worth starting a chain in, or none unless the date has two.
    pub fn lobes(&self, row: usize) -> Vec<Lobe> {
        if let Some(found) = self.lobes_cache.borrow().get(&row) {
            return found.clone();
        }
        let found = Self::lobes_of(self.inlier_row(row));
        let kept = if found.len() >= 2 { found } else { Vec::new() };
        self.lobes_cache.borrow_mut().insert(row, kept.clone());
        kept
    }

    /// For each date with several lobes, the lobes chain `rank` of the
    /// `ranks` that do not start at the modes would like to start in, first
    /// choice first. See the Python docstring for the strategies.
    pub fn start_lobes(
        &self,
        rank: usize,
        ranks: usize,
        strategy: &str,
        seed: u64,
    ) -> Result<Wishes, Error> {
        if !START_STRATEGIES.contains(&strategy) || strategy == "quantile" {
            return Err(Error::Model(format!(
                "no lobe start for strategy '{strategy}'"
            )));
        }
        let mut groups: Vec<Vec<usize>> =
            self.group_dates.iter().map(|(_, _, d)| d.clone()).collect();
        let grouped: HashSet<usize> = groups.iter().flatten().copied().collect();
        groups.extend(
            (0..self.num_dates)
                .filter(|d| !grouped.contains(d))
                .map(|d| vec![d]),
        );
        let mut wishes = Wishes::new();
        for (index, dates) in groups.iter().enumerate() {
            let members: Vec<usize> = dates
                .iter()
                .copied()
                .filter(|&d| !self.lobes(d).is_empty())
                .collect();
            if members.is_empty() {
                continue;
            }
            let mut f = (rank as f64 + 0.5) / ranks as f64;
            if strategy == "random" {
                let mut rng = Generator::new(&[seed, index as u64][..]);
                let shift = rng.integers(0, ranks as i64) as usize;
                f = (((rank + shift) % ranks) as f64 + 0.5) / ranks as f64;
            }
            let when = if strategy == "calendar" {
                let peaks: Vec<f64> = members
                    .iter()
                    .flat_map(|&d| self.lobes(d))
                    .map(|l| l.0 as f64)
                    .collect();
                let (lo, hi) = peaks
                    .iter()
                    .fold((f64::INFINITY, f64::NEG_INFINITY), |(a, b), &p| {
                        (a.min(p), b.max(p))
                    });
                lo + f * (hi - lo)
            } else {
                0.0
            };
            for &d in &members {
                let lobes = self.lobes(d);
                let mut order: Vec<usize> = (0..lobes.len()).collect();
                let first = (lobes.len() - 1).min((lobes.len() as f64 * f) as usize);
                if strategy == "calendar" {
                    order.sort_by(|&a, &b| {
                        (lobes[a].0 as f64 - when)
                            .abs()
                            .total_cmp(&(lobes[b].0 as f64 - when).abs())
                            .then(a.cmp(&b))
                    });
                } else {
                    order.sort_by_key(|&i| (i.abs_diff(first), i));
                }
                let mut targets: HashMap<usize, Option<i64>> =
                    order.iter().map(|&i| (i, None)).collect();
                if strategy == "equalised" {
                    let inside = (lobes.len() as f64 * f - first as f64)
                        .clamp(EQUALISED_EDGE, 1.0 - EQUALISED_EDGE);
                    let inlier = self.inlier_row(d);
                    for (i, &(_, _, lo, hi)) in lobes.iter().enumerate() {
                        let cdf = cumsum(&inlier[lo..hi]);
                        let last = cdf[cdf.len() - 1];
                        targets.insert(
                            i,
                            Some((lo + searchsorted_left(&cdf, inside * last)) as i64),
                        );
                    }
                }
                wishes.insert(d, order.iter().map(|&i| (lobes[i], targets[&i])).collect());
            }
        }
        Ok(wishes)
    }

    /// A starting state with `row` in `lobe` and the rest of its group in
    /// their own lobes nearest it; None if the date did not end up there.
    pub fn probe_start(&self, row: usize, lobe: Lobe) -> Option<State> {
        let (peak, _, first, last) = lobe;
        let members = self
            .group_dates
            .iter()
            .find(|(_, _, dates)| dates.contains(&row))
            .map_or_else(|| vec![row], |(_, _, d)| d.clone());
        let mut wishes = Wishes::new();
        for &member in &members {
            let mut lobes = self.lobes(member);
            if !lobes.is_empty() {
                lobes.sort_by_key(|l| (l.0.abs_diff(peak), l.0));
                wishes.insert(member, lobes.into_iter().map(|l| (l, None)).collect());
            }
        }
        let mut own = vec![(lobe, None)];
        own.extend(
            wishes
                .get(&row)
                .into_iter()
                .flatten()
                .filter(|w| w.0.2 != first)
                .copied(),
        );
        wishes.insert(row, own);
        let state = self.initial_state(None, None, Some(wishes)).ok()?;
        let at = state.index[row];
        (first as i64 <= at && at < last as i64).then_some(state)
    }

    /// Propose translating `rows` by a common offset and accept on the exact
    /// MH ratio. Returns (state, its log posterior, taken, wide kernel used).
    /// The draws taken, and when, are Python's to the letter: the seed
    /// reproduces a run only if every move consumes what it does there.
    pub fn shift(
        &self,
        state: State,
        rng: &mut Generator,
        rows: &[usize],
        scale: f64,
        current_lp: Option<f64>,
        beta: f64,
    ) -> (State, f64, bool, bool) {
        let current_lp = current_lp.unwrap_or_else(|| self.log_posterior(&state, beta));
        let wide = rng.random() < WIDE_MOVE_FRACTION;
        let gaps = if wide {
            self.peak_gaps(rows)
        } else {
            Vec::new()
        };
        let steps = if !gaps.is_empty() && rng.random() < PEAK_JUMP_FRACTION {
            let jitter = round_even(PEAK_JUMP_JITTER_YEARS / self.dt);
            let gap = gaps[rng.integers(0, gaps.len() as i64) as usize];
            let sign = if rng.random() < 0.5 { 1 } else { -1 };
            gap * sign + rng.integers(-jitter, jitter + 1)
        } else {
            let scale = if wide {
                WIDE_MOVE_YEARS / self.dt
            } else {
                scale
            };
            round_even(rng.normal(0.0, scale.max(1e-9)))
        };
        if steps == 0 {
            return (state, current_lp, false, wide);
        }
        let mut proposal = state.clone();
        for &row in rows {
            let moved = state.index[row] + steps;
            if moved < 0 || moved >= self.m as i64 {
                return (state, current_lp, false, wide);
            }
            proposal.index[row] = moved;
        }
        let proposed_lp = self.log_posterior(&proposal, beta);
        if proposed_lp == f64::NEG_INFINITY {
            return (state, current_lp, false, wide);
        }
        if proposed_lp >= current_lp || rng.random() < (proposed_lp - current_lp).exp() {
            return (proposal, proposed_lp, true, wide);
        }
        (state, current_lp, false, wide)
    }
}
