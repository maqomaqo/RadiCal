//! One chain, resumable, and the single-chain run. Port of `gibbs._Chain`
//! and `gibbs.run`.

use std::collections::HashMap;

use crate::Error;
use crate::numeric::sum;
use crate::rng::Generator;

use super::model::Model;
use super::{
    ADAPT_INTERVAL, BURN_IN_FRACTION, COLDEST_HOT_BETA, DEFAULT_TEMPERATURES, GROUP_SHIFT_YEARS,
    MAX_SWEEPS, MOVES_PER_SWEEP, ROUNDS, SHRINK_AFTER_FRACTION, SHRINK_MIN_CONVERGENCE,
    SHRINK_TRIM, State, TARGET_ACCEPTANCE, TARGET_CONVERGENCE,
};

/// A count per grid cell, per parameter row.
pub type Histograms = Vec<Vec<f64>>;

/// Raised out of a run when its progress callback asked it to stop.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cancelled;

/// What a progress callback is told: after burn-in chunks and every round.
#[derive(Debug, Clone, PartialEq)]
pub struct Progress {
    pub phase: &'static str,
    pub sweeps: usize,
    pub max_sweeps: usize,
    /// The WORST parameter's convergence, the one the stopping rule uses.
    pub convergence: f64,
    pub chains: usize,
}

/// A chain's start: the likelihood quantile its dates start at (None: the
/// modes) and, for dates with several lobes, (rank, ranks, strategy, seed).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Start {
    pub quantile: Option<f64>,
    pub lobe: Option<(usize, usize, String, u64)>,
}

/// One chain's state between commands: its own model (the limits it may
/// shrink are its own), generator, state and hot copies, and what it has
/// accumulated.
#[derive(Debug, Clone)]
pub struct Chain {
    pub model: Model,
    pub rng: Generator,
    pub state: State,
    pub betas: Vec<f64>,
    pub hot_states: Vec<State>,
    pub swaps_proposed: Vec<usize>,
    pub swaps_accepted: Vec<usize>,
    pub burn_in: usize,
    pub max_sweeps: usize,
    pub move_sets: Vec<Vec<usize>>,
    pub scales: Vec<f64>,
    proposed: Vec<usize>,
    accepted: Vec<usize>,
    since_adapt: Vec<usize>,
    took_since_adapt: Vec<usize>,
    pub swept: usize,
    pub rounds: Vec<Histograms>,
    pub scale_counts: Vec<Vec<f64>>,
    pub rate_counts: Vec<Vec<f64>>,
    /// Running total of each flagged date's outlier responsibility, by row.
    pub outlier_total: Vec<(usize, f64)>,
    pub accumulated: usize,
    per_round_draws: usize,
    pub round_draws: Vec<Vec<Vec<i32>>>,
    pub round_length: usize,
    pub limits_shrunk: bool,
}

/// What a chain hands back when it is finished.
#[derive(Debug, Clone, PartialEq)]
pub struct ChainResult {
    pub histograms: Histograms,
    pub rate_histograms: Vec<Vec<f64>>,
    /// Whole states, one row per kept sweep: every parameter's cell, then
    /// each outlier model's scale node.
    pub draws: Option<Vec<Vec<i32>>>,
    pub scale_histograms: Vec<Vec<f64>>,
    /// By flagged date row.
    pub outlier_probability: Vec<(usize, f64)>,
    pub sweeps: usize,
    pub sweeps_kept: usize,
    pub proposed_moves: usize,
    pub accepted_moves: usize,
    pub acceptance: Vec<f64>,
    pub scales: Vec<f64>,
    pub convergence: Vec<f64>,
    pub limits_shrunk: bool,
}

impl Chain {
    #[allow(clippy::too_many_arguments)] // Python's _Chain takes these as positional arguments.
    pub fn new(
        model: Model,
        sweeps: usize,
        seed: u64,
        burn_in: Option<usize>,
        max_sweeps: Option<usize>,
        collect_draws: usize,
        start: &Start,
        temperatures: usize,
    ) -> Result<Self, Error> {
        let rng = Generator::new(seed);
        let state = model.initial_state(start.quantile, start.lobe.as_ref(), None)?;
        let count = temperatures.max(1);
        // COLDEST_HOT_BETA ** (arange(count) / (count - 1)), NumPy's power.
        let betas: Vec<f64> = if count == 1 {
            vec![1.0]
        } else {
            (0..count)
                .map(|i| COLDEST_HOT_BETA.powf(std::hint::black_box(i as f64 / (count - 1) as f64)))
                .collect()
        };
        let hot_states = vec![state.clone(); count - 1];
        let burn_in = burn_in.unwrap_or((sweeps as f64 * BURN_IN_FRACTION) as usize);
        let max_sweeps = max_sweeps.unwrap_or(sweeps.max(MAX_SWEEPS));
        let move_sets = model.move_sets();
        let sets = move_sets.len();
        let scales = vec![(GROUP_SHIFT_YEARS / model.dt).max(1.0); sets];
        let scale_counts = model
            .scale_grid
            .iter()
            .map(|g| vec![0.0; g.len()])
            .collect();
        let rate_counts = model
            .depositions
            .iter()
            .map(|d| vec![0.0; d.k_nodes.len()])
            .collect();
        let mut flagged: Vec<usize> = model.likelihood_at.keys().copied().collect();
        flagged.sort_unstable();
        let outlier_total = flagged.into_iter().map(|r| (r, 0.0)).collect();
        let per_round_draws = if collect_draws > 0 {
            (collect_draws / ROUNDS).max(1)
        } else {
            0
        };
        let round_length = ((sweeps - burn_in) / ROUNDS).max(1);
        Ok(Self {
            model,
            rng,
            state,
            betas,
            hot_states,
            swaps_proposed: vec![0; count - 1],
            swaps_accepted: vec![0; count - 1],
            burn_in,
            max_sweeps,
            move_sets,
            scales,
            proposed: vec![0; sets],
            accepted: vec![0; sets],
            since_adapt: vec![0; sets],
            took_since_adapt: vec![0; sets],
            swept: 0,
            rounds: Vec::new(),
            scale_counts,
            rate_counts,
            outlier_total,
            accumulated: 0,
            per_round_draws,
            round_draws: Vec::new(),
            round_length,
            limits_shrunk: false,
        })
    }

    /// One sweep of the chain, and of each tempered copy with a swap
    /// attempted between every neighbouring pair: exchanged with probability
    /// min(1, exp((b_i - b_j)(L_j - L_i))), which leaves every copy's target
    /// invariant.
    pub fn sweep(&mut self) {
        let state = std::mem::replace(
            &mut self.state,
            State {
                index: Vec::new(),
                scale: Vec::new(),
                rate: Vec::new(),
            },
        );
        self.state = self.sweep_state(state, 1.0, true);
        if self.hot_states.is_empty() {
            self.swept += 1;
            return;
        }
        for k in 0..self.hot_states.len() {
            let beta = self.betas[k + 1];
            let hot = std::mem::replace(
                &mut self.hot_states[k],
                State {
                    index: Vec::new(),
                    scale: Vec::new(),
                    rate: Vec::new(),
                },
            );
            self.hot_states[k] = self.sweep_state(hot, beta, false);
        }
        let mut states = Vec::with_capacity(self.hot_states.len() + 1);
        states.push(std::mem::replace(
            &mut self.state,
            State {
                index: Vec::new(),
                scale: Vec::new(),
                rate: Vec::new(),
            },
        ));
        states.append(&mut self.hot_states);
        for k in 0..states.len() - 1 {
            self.swaps_proposed[k] += 1;
            let exponent = (self.betas[k] - self.betas[k + 1])
                * (self.model.log_likelihood_sum(&states[k + 1])
                    - self.model.log_likelihood_sum(&states[k]));
            if exponent >= 0.0 || self.rng.random() < exponent.exp() {
                states.swap(k, k + 1);
                self.swaps_accepted[k] += 1;
            }
        }
        self.state = states.remove(0);
        self.hot_states = states;
        self.swept += 1;
    }

    /// One sweep of `state` at power `beta`: dates by block, boundaries,
    /// outlier scales, deposition rigidities, then the translation moves.
    /// `adapt` lets the translation scales tune during burn-in.
    fn sweep_state(&mut self, mut state: State, beta: f64, adapt: bool) -> State {
        let model = &self.model;
        for block in &model.blocks {
            let draws: Vec<f64> = (0..block.len()).map(|_| self.rng.random()).collect();
            for (&row, u) in block.iter().zip(draws) {
                state.index[row] = model.draw_date(row, &state, u, beta);
            }
        }
        for &row in &model.boundary_rows {
            let u = self.rng.random();
            state.index[row] = model.draw_boundary(row, &state, u);
        }
        for m in 0..model.outlier_models.len() {
            let u = self.rng.random();
            state.scale[m] = model.draw_scale(m, &state, u, beta);
        }
        for d in 0..model.depositions.len() {
            let u = self.rng.random();
            state.rate[d] = model.draw_deposition_rate(d, &state, u);
        }
        let mut current_lp = None;
        for index in 0..self.move_sets.len() {
            for _ in 0..MOVES_PER_SWEEP {
                let (next, lp, took, wide) = model.shift(
                    state,
                    &mut self.rng,
                    &self.move_sets[index],
                    self.scales[index],
                    current_lp,
                    beta,
                );
                state = next;
                current_lp = Some(lp);
                if !adapt {
                    continue;
                }
                self.proposed[index] += 1;
                self.accepted[index] += usize::from(took);
                if !wide {
                    self.since_adapt[index] += 1;
                    self.took_since_adapt[index] += usize::from(took);
                }
            }
            if adapt && self.swept < self.burn_in && self.since_adapt[index] >= ADAPT_INTERVAL {
                let rate = self.took_since_adapt[index] as f64 / self.since_adapt[index] as f64;
                let factor = if rate > TARGET_ACCEPTANCE { 1.3 } else { 0.75 };
                self.scales[index] = (self.scales[index] * factor).max(1.0);
                self.since_adapt[index] = 0;
                self.took_since_adapt[index] = 0;
            }
        }
        state
    }

    /// `count` sweeps with nothing accumulated.
    pub fn burn(&mut self, count: usize) {
        for _ in 0..count {
            self.sweep();
        }
    }

    /// `Model::shrink_limits`, for a run matching OxCal: not alongside
    /// tempered copies, which OxCal has none of.
    pub fn shrink(&mut self, ranges: &HashMap<usize, (i64, i64)>) -> Result<(), Error> {
        if !self.hot_states.is_empty() {
            return Err(Error::Model(
                "limits cannot be shrunk on a tempered chain".into(),
            ));
        }
        self.model.shrink_limits(ranges);
        self.limits_shrunk = true;
        Ok(())
    }

    /// One sampling round: `round_length` sweeps into a fresh histogram per
    /// parameter, appended and returned. Folds the rounds when there are
    /// too many, doubling the round length.
    pub fn round(&mut self) -> Histograms {
        let n = self.model.params.len();
        let mut current: Histograms = vec![vec![0.0; self.model.m]; n];
        let stride = self
            .round_length
            .checked_div(self.per_round_draws)
            .map_or(0, |s| s.max(1));
        let mut kept: Vec<Vec<i32>> = Vec::new();
        for step in 0..self.round_length {
            self.sweep();
            let state = &self.state;
            for (row, h) in current.iter_mut().enumerate() {
                h[state.index[row] as usize] += 1.0;
            }
            for (m, counts) in self.scale_counts.iter_mut().enumerate() {
                counts[state.scale[m]] += 1.0;
            }
            for (d, counts) in self.rate_counts.iter_mut().enumerate() {
                counts[state.rate[d]] += 1.0;
            }
            for (row, total) in &mut self.outlier_total {
                let node = self.model.scale_of(*row, state).unwrap();
                *total += self.model.responsibility_at[row][node][state.index[*row] as usize];
            }
            if stride > 0 && step % stride == 0 {
                let mut values: Vec<i32> = state.index.iter().map(|&i| i as i32).collect();
                values.extend(state.scale.iter().map(|&s| s as i32));
                kept.push(values);
            }
            self.accumulated += 1;
        }
        self.rounds.push(current.clone());
        if self.per_round_draws > 0 {
            self.round_draws.push(kept);
        }
        if self.rounds.len() > 2 * ROUNDS {
            self.rounds = folded(std::mem::take(&mut self.rounds));
            if self.per_round_draws > 0 {
                let draws = std::mem::take(&mut self.round_draws);
                let mut merged = Vec::new();
                let (pairs, rest) = draws.as_chunks::<2>();
                for [a, b] in pairs {
                    let joined: Vec<Vec<i32>> = a.iter().chain(b).cloned().collect();
                    merged.push(joined.into_iter().step_by(2).collect());
                }
                if let [last] = rest {
                    merged.push(last.clone());
                }
                self.round_draws = merged;
            }
            self.round_length *= 2;
        }
        current
    }

    /// (per-parameter measure, the worst of them, rounds it was read over).
    pub fn convergence(&self) -> (Vec<f64>, f64, usize) {
        let kept = after_burn_in(&self.rounds);
        let measure = convergence(kept);
        let worst = measure.iter().copied().fold(f64::INFINITY, f64::min);
        let worst = if measure.is_empty() { 0.0 } else { worst };
        (measure, worst, kept.len())
    }

    pub fn result(&self, convergence: Vec<f64>) -> ChainResult {
        let kept = after_burn_in(&self.rounds);
        let n = self.model.params.len();
        let mut histograms = vec![vec![0.0; self.model.m]; n];
        for round in kept {
            for (h, r) in histograms.iter_mut().zip(round) {
                for (a, b) in h.iter_mut().zip(r) {
                    *a += b;
                }
            }
        }
        let start = burn_in_start(self.rounds.len());
        let kept_draws: Vec<&Vec<Vec<i32>>> = self.round_draws.iter().skip(start).collect();
        let draws = if kept_draws.iter().any(|d| !d.is_empty()) {
            Some(kept_draws.into_iter().flatten().cloned().collect())
        } else {
            None
        };
        let accumulated = self.accumulated as f64;
        let sweeps_kept = histograms.first().map_or(0.0, |h| sum(h)) as usize;
        ChainResult {
            histograms,
            rate_histograms: self.rate_counts.clone(),
            draws,
            scale_histograms: self.scale_counts.clone(),
            outlier_probability: self
                .outlier_total
                .iter()
                .map(|&(r, v)| {
                    (
                        r,
                        if self.accumulated > 0 {
                            v / accumulated
                        } else {
                            0.0
                        },
                    )
                })
                .collect(),
            sweeps: self.swept,
            sweeps_kept,
            proposed_moves: self.proposed.iter().sum(),
            accepted_moves: self.accepted.iter().sum(),
            acceptance: self
                .accepted
                .iter()
                .zip(&self.proposed)
                .map(|(&a, &p)| if p > 0 { a as f64 / p as f64 } else { 0.0 })
                .collect(),
            scales: self.scales.clone(),
            convergence,
            limits_shrunk: self.limits_shrunk,
        }
    }
}

/// Each parameter's sampled range as (first, last) grid index, the histogram
/// trimmed at `trim` of its peak; parameters never sampled are left out.
pub fn sampled_ranges(totals: &Histograms, trim: f64) -> HashMap<usize, (i64, i64)> {
    let mut ranges = HashMap::new();
    for (row, counts) in totals.iter().enumerate() {
        let peak = counts.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        if peak <= 0.0 {
            continue;
        }
        let first = counts.iter().position(|&c| c >= trim * peak).unwrap();
        let last = counts.iter().rposition(|&c| c >= trim * peak).unwrap();
        ranges.insert(row, (first as i64, last as i64));
    }
    ranges
}

/// How many sampling rounds a run matching OxCal completes before it first
/// checks whether to shrink the limits.
pub fn shrink_round() -> usize {
    ((SHRINK_AFTER_FRACTION * ROUNDS as f64).ceil() as usize).max(1)
}

/// The rounds added together, cell by cell.
pub fn total(rounds: &[Histograms]) -> Histograms {
    let mut out: Histograms = rounds
        .first()
        .map(|r| r.iter().map(|h| vec![0.0; h.len()]).collect())
        .unwrap_or_default();
    for round in rounds {
        for (h, r) in out.iter_mut().zip(round) {
            for (a, b) in h.iter_mut().zip(r) {
                *a += b;
            }
        }
    }
    out
}

/// The ranges to shrink to, if a run matching OxCal has converged past
/// SHRINK_MIN_CONVERGENCE over everything sampled so far.
pub fn shrink_due(rounds: &[Histograms]) -> Option<HashMap<usize, (i64, i64)>> {
    let measure = convergence(rounds);
    if measure.is_empty()
        || measure.iter().copied().fold(f64::INFINITY, f64::min) <= SHRINK_MIN_CONVERGENCE
    {
        return None;
    }
    Some(sampled_ranges(&total(rounds), SHRINK_TRIM))
}

/// Adjacent pairs of rounds added together: the same accumulated
/// distribution, half the bookkeeping.
pub fn folded(rounds: Vec<Histograms>) -> Vec<Histograms> {
    let mut merged = Vec::with_capacity(rounds.len().div_ceil(2));
    let mut iter = rounds.into_iter();
    while let Some(mut first) = iter.next() {
        if let Some(second) = iter.next() {
            for (a, b) in first.iter_mut().zip(&second) {
                for (x, y) in a.iter_mut().zip(b) {
                    *x += y;
                }
            }
        }
        merged.push(first);
    }
    merged
}

/// Burn-in in ten-ish chunks, so progress is reported during it.
pub fn burn_chunks(burn_in: usize) -> Vec<usize> {
    let chunk = (burn_in / 10).max(1);
    let mut chunks = vec![chunk; burn_in / chunk];
    if !burn_in.is_multiple_of(chunk) {
        chunks.push(burn_in % chunk);
    }
    chunks
}

/// The rounds to report from, dropping the earliest as burn-in: a FRACTION
/// of rounds, so burn-in scales with however long the run turned out.
pub fn after_burn_in(rounds: &[Histograms]) -> &[Histograms] {
    &rounds[burn_in_start(rounds.len())..]
}

pub fn burn_in_start(count: usize) -> usize {
    if count < 3 {
        0
    } else {
        (count as f64 * BURN_IN_FRACTION) as usize
    }
}

/// OxCal's convergence measure per parameter: the overlap between the
/// distribution over the first half of the rounds and the second half.
pub fn convergence(rounds: &[Histograms]) -> Vec<f64> {
    let params = rounds.first().map_or(0, Vec::len);
    if rounds.len() < 2 {
        return vec![0.0; params];
    }
    let split = rounds.len() / 2;
    let early = total(&rounds[..split]);
    let late = total(&rounds[split..]);
    early
        .iter()
        .zip(&late)
        .map(|(e, l)| {
            let (a, b) = (sum(e), sum(l));
            if a > 0.0 && b > 0.0 {
                let overlap: Vec<f64> = e.iter().zip(l).map(|(x, y)| (x / a).min(y / b)).collect();
                sum(&overlap)
            } else {
                0.0
            }
        })
        .collect()
}

/// What a single-chain run is asked for beyond the model.
#[derive(Debug, Clone, PartialEq)]
pub struct RunOptions {
    pub sweeps: usize,
    pub seed: u64,
    pub burn_in: Option<usize>,
    pub target_convergence: f64,
    pub max_sweeps: Option<usize>,
    pub collect_draws: usize,
    pub min_sweeps: usize,
    pub start: Start,
    pub temperatures: usize,
    pub match_oxcal: bool,
}

impl Default for RunOptions {
    fn default() -> Self {
        Self {
            sweeps: super::DEFAULT_SWEEPS,
            seed: 42,
            burn_in: None,
            target_convergence: TARGET_CONVERGENCE,
            max_sweeps: None,
            collect_draws: 0,
            min_sweeps: 0,
            start: Start::default(),
            temperatures: DEFAULT_TEMPERATURES,
            match_oxcal: false,
        }
    }
}

/// Sweep one chain and accumulate each parameter's marginal as a histogram,
/// extending the run until it has converged (or reached its cap), as OxCal
/// does. `on_progress` returning false stops the run between rounds.
pub fn run(
    model: Model,
    options: &RunOptions,
    on_progress: Option<&mut dyn FnMut(&Progress) -> bool>,
) -> Result<Result<ChainResult, Cancelled>, Error> {
    let mut on_progress = on_progress;
    let mut chain = Chain::new(
        model,
        options.sweeps,
        options.seed,
        options.burn_in,
        options.max_sweeps,
        options.collect_draws,
        &options.start,
        options.temperatures,
    )?;
    let mut report = |chain: &Chain, phase: &'static str, convergence: f64| -> bool {
        match on_progress.as_deref_mut() {
            None => true,
            Some(callback) => callback(&Progress {
                phase,
                sweeps: chain.swept,
                max_sweeps: chain.max_sweeps,
                convergence,
                chains: 1,
            }),
        }
    };
    let burn_chunk = (chain.burn_in / 10).max(1);
    for count in burn_chunks(chain.burn_in) {
        chain.burn(count);
        if chain.swept % burn_chunk == 0 && !report(&chain, "burn_in", 0.0) {
            return Ok(Err(Cancelled));
        }
    }
    let mut completed = 0;
    let measure = loop {
        chain.round();
        completed += 1;
        if options.match_oxcal
            && !chain.limits_shrunk
            && completed >= shrink_round()
            && let Some(ranges) = shrink_due(&chain.rounds)
        {
            chain.shrink(&ranges)?;
        }
        let (measure, worst, kept) = chain.convergence();
        if !report(&chain, "sampling", worst) {
            return Ok(Err(Cancelled));
        }
        if kept < 2 || (options.match_oxcal && chain.rounds.len() < ROUNDS) {
            continue;
        }
        if chain.swept >= chain.max_sweeps {
            break measure;
        }
        if worst >= options.target_convergence && chain.swept >= options.min_sweeps {
            break measure;
        }
    };
    Ok(Ok(chain.result(measure)))
}
