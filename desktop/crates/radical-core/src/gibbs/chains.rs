//! Several chains at once: independent chains advanced in lockstep, one
//! burn-in chunk or one round at a time, under ONE stopping rule read off
//! their pooled rounds. Port of `gibbs.solve_chains` and what it pools with.
//!
//! Python runs each chain in its own worker process, because the sweep holds
//! the GIL. Here each is a thread over its own copy of the model; the chains
//! share nothing while they sweep, so the answer is the same however the
//! threads are scheduled.

use crate::Error;
use crate::numeric::{cumsum, mean, sum};
use crate::rng::Generator;

use super::chain::{
    Cancelled, Chain, ChainResult, Histograms, Progress, Start, after_burn_in, burn_chunks,
    convergence, shrink_due, shrink_round, total,
};
use super::model::Model;
use super::{
    DEFAULT_START_STRATEGY, MAX_CHAIN_WORKERS, MAX_SWEEPS, ROUNDS, START_QUANTILE_RANGE,
    START_STRATEGIES, TARGET_CHAIN_THRESHOLD_GAP, TARGET_CONVERGENCE, TARGET_RHAT,
};

/// How many chains a run may have. Python caps it at the machine's cores,
/// each chain being a worker process; here a chain is a thread, more threads
/// than cores are only slower, and a cap that moved with the machine would
/// make one analysis's answer depend on the computer it ran on. So the cap
/// is the fixed MAX_CHAIN_WORKERS.
pub fn worker_cap() -> usize {
    MAX_CHAIN_WORKERS
}

/// Where each chain of a multi-chain run starts, as a likelihood quantile:
/// None (the modes) for chain 0, so it stays the single-chain run, and one
/// draw from each of `chains - 1` equal strata of START_QUANTILE_RANGE.
pub fn start_quantiles(chains: usize, seed: u64) -> Vec<Option<f64>> {
    if chains <= 1 {
        return vec![None; chains];
    }
    let mut rng = Generator::new(seed);
    let (low, high) = START_QUANTILE_RANGE;
    let width = (high - low) / (chains - 1) as f64;
    let mut out = vec![None];
    out.extend((0..chains - 1).map(|c| Some(low + (c as f64 + rng.random()) * width)));
    out
}

/// Each chain's start: chain 0 at the modes, the rest at a stratified
/// quantile and, unless `strategy` is 'quantile', in a lobe it chooses.
pub fn chain_starts(chains: usize, seed: u64, strategy: &str) -> Result<Vec<Start>, Error> {
    if !START_STRATEGIES.contains(&strategy) {
        return Err(Error::Model(format!("unknown start strategy '{strategy}'")));
    }
    Ok(start_quantiles(chains, seed)
        .into_iter()
        .enumerate()
        .map(|(c, quantile)| Start {
            quantile,
            lobe: (c > 0 && strategy != "quantile")
                .then(|| (c - 1, chains - 1, strategy.to_owned(), seed)),
        })
        .collect())
}

/// Several chains' results as one, plus how they agreed.
#[derive(Debug, Clone, PartialEq)]
pub struct Pooled {
    pub result: ChainResult,
    pub chains: usize,
    pub sweeps_per_chain: Vec<usize>,
    pub per_chain_convergence: Vec<f64>,
    pub between_chain_convergence: Option<f64>,
    pub rhat: Option<f64>,
    pub chain_threshold_gap: Option<f64>,
}

impl Pooled {
    /// One chain's result, with nothing to compare it against.
    pub fn single(result: ChainResult) -> Self {
        let within = worst(&result.convergence);
        Self {
            chains: 1,
            sweeps_per_chain: vec![result.sweeps],
            per_chain_convergence: vec![within],
            between_chain_convergence: None,
            rhat: None,
            chain_threshold_gap: None,
            result,
        }
    }
}

fn worst(measure: &[f64]) -> f64 {
    if measure.is_empty() {
        0.0
    } else {
        measure.iter().copied().fold(f64::INFINITY, f64::min)
    }
}

/// What a multi-chain solve is asked for.
#[derive(Debug, Clone, PartialEq)]
pub struct ChainsOptions {
    pub chains: usize,
    pub sweeps: usize,
    pub seed: u64,
    pub target_convergence: f64,
    pub max_sweeps: Option<usize>,
    pub min_sweeps: usize,
    pub collect_draws: usize,
    pub start_strategy: String,
    pub temperatures: usize,
    pub match_oxcal: bool,
}

impl Default for ChainsOptions {
    fn default() -> Self {
        Self {
            chains: super::DEFAULT_CHAINS,
            sweeps: super::DEFAULT_SWEEPS,
            seed: 42,
            target_convergence: TARGET_CONVERGENCE,
            max_sweeps: None,
            min_sweeps: 0,
            collect_draws: 0,
            start_strategy: DEFAULT_START_STRATEGY.to_owned(),
            temperatures: super::DEFAULT_TEMPERATURES,
            match_oxcal: false,
        }
    }
}

/// Run every chain's step at once, one thread each.
fn everyone<T: Send>(chains: &mut [Chain], step: impl Fn(&mut Chain) -> T + Sync) -> Vec<T> {
    std::thread::scope(|scope| {
        let handles: Vec<_> = chains.iter_mut().map(|c| scope.spawn(|| step(c))).collect();
        handles
            .into_iter()
            .map(|h| h.join().expect("a chain thread panicked"))
            .collect()
    })
}

/// The chains' rounds pooled round by round.
fn pooled_rounds(per_chain: &[&[Histograms]]) -> Vec<Histograms> {
    let count = per_chain.iter().map(|r| r.len()).min().unwrap_or(0);
    (0..count)
        .map(|i| total(&per_chain.iter().map(|r| r[i].clone()).collect::<Vec<_>>()))
        .collect()
}

/// `run` over `options.chains` chains in parallel under ONE stopping rule:
/// the pooled first-half/second-half measure, then R-hat and the chain
/// threshold gap. Chain c is seeded seed + c, so chain 0 is the single-chain
/// run. `chains` is capped at [`worker_cap`].
pub fn solve_chains(
    model: &Model,
    options: &ChainsOptions,
    on_progress: Option<&mut dyn FnMut(&Progress) -> bool>,
) -> Result<Result<Pooled, Cancelled>, Error> {
    if options.chains < 1 {
        return Err(Error::Model("chains must be at least 1".into()));
    }
    let count = options.chains.min(worker_cap());
    let max_sweeps = options
        .max_sweeps
        .unwrap_or(options.sweeps.max(MAX_SWEEPS).max(options.min_sweeps));
    let mut on_progress = on_progress;
    let mut report = |phase: &'static str, swept: &[usize], convergence: f64| -> bool {
        match on_progress.as_deref_mut() {
            None => true,
            Some(callback) => callback(&Progress {
                phase,
                sweeps: swept.iter().sum(),
                max_sweeps: max_sweeps * count,
                convergence,
                chains: count,
            }),
        }
    };

    let starts = chain_starts(count, options.seed, &options.start_strategy)?;
    let mut chains = starts
        .iter()
        .enumerate()
        .map(|(c, start)| {
            Chain::new(
                model.clone(),
                options.sweeps,
                options.seed + c as u64,
                None,
                Some(max_sweeps),
                options.collect_draws,
                start,
                options.temperatures,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    let burn_in = chains[0].burn_in;

    let burn_chunk = (burn_in / 10).max(1);
    for chunk in burn_chunks(burn_in) {
        everyone(&mut chains, |c| c.burn(chunk));
        let swept: Vec<usize> = chains.iter().map(|c| c.swept).collect();
        if swept[0].is_multiple_of(burn_chunk) && !report("burn_in", &swept, 0.0) {
            return Ok(Err(Cancelled));
        }
    }

    let mut completed = 0;
    let mut shrunk = false;
    let pooled_measure = loop {
        everyone(&mut chains, Chain::round);
        completed += 1;
        let swept: Vec<usize> = chains.iter().map(|c| c.swept).collect();
        if options.match_oxcal && !shrunk && completed >= shrink_round() {
            let all: Vec<&[Histograms]> = chains.iter().map(|c| c.rounds.as_slice()).collect();
            if let Some(ranges) = shrink_due(&pooled_rounds(&all)) {
                for chain in &mut chains {
                    chain.shrink(&ranges)?;
                }
                shrunk = true;
            }
        }
        let kept: Vec<&[Histograms]> = chains.iter().map(|c| after_burn_in(&c.rounds)).collect();
        let pooled = pooled_rounds(&kept);
        let measure = convergence(&pooled);
        let worst_now = worst(&measure);
        if !report("sampling", &swept, worst_now) {
            return Ok(Err(Cancelled));
        }
        if pooled.len() < 2 || (options.match_oxcal && completed < ROUNDS) {
            continue;
        }
        if *swept.iter().min().unwrap() >= max_sweeps {
            break measure;
        }
        if worst_now < options.target_convergence {
            continue;
        }
        if *swept.iter().min().unwrap() < options.min_sweeps {
            continue;
        }
        // Pooled halves agree; now the chains must agree with each other.
        let totals: Vec<Histograms> = kept.iter().map(|rounds| total(rounds)).collect();
        let (_overlap, rhat) = between_chains(&totals);
        if rhat.is_some_and(|r| r <= TARGET_RHAT)
            && chain_threshold_gap(&totals).is_some_and(|g| g <= TARGET_CHAIN_THRESHOLD_GAP)
        {
            break measure;
        }
    };
    let results: Vec<ChainResult> = chains
        .iter()
        .map(|c| {
            let (measure, _, _) = c.convergence();
            c.result(measure)
        })
        .collect();
    let mut pooled = pool_chains(&results);
    pooled.per_chain_convergence = results.iter().map(|r| worst(&r.convergence)).collect();
    pooled.result.convergence = pooled_measure;
    Ok(Ok(pooled))
}

/// The chains' results as one result: counts add, outlier probabilities are
/// weighted by the sweeps each chain kept, acceptance rates averaged.
pub fn pool_chains(results: &[ChainResult]) -> Pooled {
    let kept: Vec<f64> = results.iter().map(|r| r.sweeps_kept as f64).collect();
    let kept_total = sum(&kept);
    let add = |pick: &dyn Fn(&ChainResult) -> &Vec<Vec<f64>>| -> Vec<Vec<f64>> {
        let mut out = pick(&results[0]).clone();
        for r in &results[1..] {
            for (a, b) in out.iter_mut().zip(pick(r)) {
                for (x, y) in a.iter_mut().zip(b) {
                    *x += y;
                }
            }
        }
        out
    };
    let histograms = add(&|r| &r.histograms);
    let scale_histograms = add(&|r| &r.scale_histograms);
    let rate_histograms = add(&|r| &r.rate_histograms);
    let outlier_probability = results[0]
        .outlier_probability
        .iter()
        .enumerate()
        .map(|(i, &(row, _))| {
            let mut weighted = 0.0;
            for (r, k) in results.iter().zip(&kept) {
                weighted += r.outlier_probability[i].1 * k;
            }
            (
                row,
                if kept_total > 0.0 {
                    weighted / kept_total
                } else {
                    0.0
                },
            )
        })
        .collect();
    let draws: Vec<Vec<i32>> = results
        .iter()
        .filter_map(|r| r.draws.clone())
        .flatten()
        .collect();
    let params = histograms.len();
    let within: Vec<f64> = (0..params)
        .map(|p| {
            results
                .iter()
                .map(|r| r.convergence.get(p).copied().unwrap_or(0.0))
                .fold(f64::INFINITY, f64::min)
        })
        .collect();
    let all: Vec<Histograms> = results.iter().map(|r| r.histograms.clone()).collect();
    let (between, rhat) = between_chains(&all);
    let gap = chain_threshold_gap(&all);
    let sets = results[0].acceptance.len();
    let acceptance = (0..sets)
        .map(|i| mean(&results.iter().map(|r| r.acceptance[i]).collect::<Vec<_>>()))
        .collect();
    Pooled {
        result: ChainResult {
            histograms,
            rate_histograms,
            draws: (!draws.is_empty()).then_some(draws),
            scale_histograms,
            outlier_probability,
            sweeps: results.iter().map(|r| r.sweeps).sum(),
            sweeps_kept: kept_total as usize,
            proposed_moves: results.iter().map(|r| r.proposed_moves).sum(),
            accepted_moves: results.iter().map(|r| r.accepted_moves).sum(),
            acceptance,
            scales: results[0].scales.clone(),
            convergence: within,
            limits_shrunk: results.iter().any(|r| r.limits_shrunk),
        },
        chains: results.len(),
        sweeps_per_chain: results.iter().map(|r| r.sweeps).collect(),
        per_chain_convergence: Vec::new(),
        between_chain_convergence: between,
        rhat,
        chain_threshold_gap: gap,
    }
}

/// The largest difference, over every parameter, chain and calendar year,
/// between one chain's and the pooled probability of the parameter being
/// older than that year; None with a single chain.
pub fn chain_threshold_gap(histograms: &[Histograms]) -> Option<f64> {
    if histograms.len() < 2 {
        return None;
    }
    let mut worst = 0.0_f64;
    for p in 0..histograms[0].len() {
        let chains: Vec<&Vec<f64>> = histograms.iter().map(|h| &h[p]).collect();
        let totals: Vec<f64> = chains.iter().map(|c| sum(c)).collect();
        if totals.iter().any(|&t| t <= 0.0) {
            return Some(1.0);
        }
        let mut grand = 0.0;
        for t in &totals {
            grand += t;
        }
        let pooled_counts = total_rows(&chains);
        let pooled: Vec<f64> = cumsum(&pooled_counts)
            .into_iter()
            .map(|c| c / grand)
            .collect();
        for (c, t) in chains.iter().zip(&totals) {
            let gap = cumsum(c)
                .iter()
                .zip(&pooled)
                .map(|(x, q)| (x / t - q).abs())
                .fold(f64::NEG_INFINITY, f64::max);
            worst = worst.max(gap);
        }
    }
    Some(worst)
}

fn total_rows(rows: &[&Vec<f64>]) -> Vec<f64> {
    let mut out = vec![0.0; rows[0].len()];
    for row in rows {
        for (a, b) in out.iter_mut().zip(row.iter()) {
            *a += b;
        }
    }
    out
}

/// `numpy.var(a, ddof=1)`.
fn var1(a: &[f64]) -> f64 {
    let centre = mean(a);
    let squares: Vec<f64> = a.iter().map(|x| (x - centre) * (x - centre)).collect();
    sum(&squares) / (a.len() - 1) as f64
}

/// How far the chains agree: (worst overlap of any chain with the pooled
/// histogram, worst Gelman-Rubin R-hat), or (None, None) with one chain.
/// R-hat is the gate; the overlap is information.
pub fn between_chains(histograms: &[Histograms]) -> (Option<f64>, Option<f64>) {
    if histograms.len() < 2 {
        return (None, None);
    }
    let mut worst_overlap = 1.0_f64;
    let mut worst_rhat = Some(1.0_f64);
    for p in 0..histograms[0].len() {
        let chains: Vec<&Vec<f64>> = histograms.iter().map(|h| &h[p]).collect();
        let totals: Vec<f64> = chains.iter().map(|c| sum(c)).collect();
        if totals.iter().any(|&t| t <= 0.0) {
            return (Some(0.0), None);
        }
        let pooled_counts = total_rows(&chains);
        let pooled_total = sum(&pooled_counts);
        let pooled: Vec<f64> = pooled_counts.iter().map(|c| c / pooled_total).collect();
        for (c, t) in chains.iter().zip(&totals) {
            let overlap: Vec<f64> = c
                .iter()
                .zip(&pooled)
                .map(|(x, q)| (x / t).min(*q))
                .collect();
            worst_overlap = worst_overlap.min(sum(&overlap));
        }
        let index: Vec<f64> = (0..pooled.len()).map(|i| i as f64).collect();
        let means: Vec<f64> = chains
            .iter()
            .zip(&totals)
            .map(|(c, t)| {
                sum(&index
                    .iter()
                    .zip(c.iter())
                    .map(|(g, x)| g * x)
                    .collect::<Vec<_>>())
                    / t
            })
            .collect();
        let variances: Vec<f64> = chains
            .iter()
            .zip(&totals)
            .zip(&means)
            .map(|((c, t), m)| {
                let terms: Vec<f64> = index
                    .iter()
                    .zip(c.iter())
                    .map(|(g, x)| (g - m) * (g - m) * x)
                    .collect();
                sum(&terms) / t
            })
            .collect();
        let n = mean(&totals);
        let within = mean(&variances);
        let between = n * var1(&means);
        if worst_rhat.is_none() {
            continue;
        }
        if within > 0.0 {
            let estimate = (n - 1.0) / n * within + between / n;
            worst_rhat = Some(worst_rhat.unwrap().max((estimate / within).sqrt()));
        } else if between > 0.0 {
            worst_rhat = None;
        }
    }
    (Some(worst_overlap), worst_rhat)
}
