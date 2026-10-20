//! Posterior summaries for every date and boundary from a sampled run, in the
//! shape the exact solvers return, so a caller does not need to know which
//! produced them. Port of `gibbs.solve`, `probe_unvisited_lobes` and
//! `_deposition_report`.

use std::collections::HashMap;

use crate::exact_chain::summary::summarize;
use crate::exact_chain::{OutlierScale, Posterior, REPORTING_STEP_YEARS};
use crate::intervals::{Block, DEFAULT_DRAWS, Draws, report};
use crate::measurement::{Calibrator, Measurement};
use crate::model_tree::Element;
use crate::numeric::{cumsum, interp_one, sum};
use crate::rng::Generator;
use crate::{Error, py_pow};

use super::chain::{Cancelled, Chain, Progress, RunOptions, Start, run};
use super::chains::{ChainsOptions, Pooled, solve_chains};
use super::model::{Lobe, Model};
use super::{
    DEFAULT_SEED, DEFAULT_START_STRATEGY, DEFAULT_SWEEPS, DEFAULT_TEMPERATURES, MAX_SWEEPS,
    TARGET_CHAIN_THRESHOLD_GAP, TARGET_CONVERGENCE, TARGET_RHAT,
};

/// The lobe probe: a lobe holding at least PROBE_SHARE of a date's likelihood
/// but under PROBE_EMPTY of its pooled posterior is probed with a short chain
/// started in it, read over sweeps PROBE_READ_FROM..PROBE_SWEEPS; staying
/// PROBE_STAYS of that time means the run was TRAPPED away from it.
pub const PROBE_SHARE: f64 = 0.10;
pub const PROBE_EMPTY: f64 = 0.01;
pub const PROBE_SWEEPS: usize = 1000;
pub const PROBE_READ_FROM: usize = 500;
pub const PROBE_STAYS: f64 = 0.5;
pub const MAX_PROBES: usize = 12;
/// A probe chain counts as in its lobe only in the lobe's CORE, where the
/// likelihood is at least this fraction of the lobe's peak.
pub const PROBE_CORE: f64 = 0.1;

/// What a sampled solve is asked for beyond the model.
#[derive(Debug, Clone, PartialEq)]
pub struct SampleOptions {
    pub sweeps: usize,
    pub num_samples: usize,
    pub seed: u64,
    pub uniform_span_prior: bool,
    pub grid: Option<Vec<f64>>,
    pub target_convergence: f64,
    pub max_sweeps: Option<usize>,
    pub min_sweeps: usize,
    pub estimate_durations: bool,
    /// Fit the spatial models off the joint states.
    pub dispersal: Option<crate::dispersal::DispersalSpec>,
    pub interval_draws: usize,
    pub chains: usize,
    pub resolution: f64,
    pub temperatures: usize,
    pub match_oxcal: bool,
}

impl Default for SampleOptions {
    fn default() -> Self {
        Self {
            sweeps: DEFAULT_SWEEPS,
            num_samples: 4000,
            seed: DEFAULT_SEED,
            uniform_span_prior: true,
            grid: None,
            target_convergence: TARGET_CONVERGENCE,
            max_sweeps: None,
            min_sweeps: 0,
            estimate_durations: false,
            dispersal: None,
            interval_draws: DEFAULT_DRAWS,
            chains: 1,
            resolution: REPORTING_STEP_YEARS,
            temperatures: DEFAULT_TEMPERATURES,
            match_oxcal: false,
        }
    }
}

/// A lobe the run left empty that a chain started in it did not leave.
#[derive(Debug, Clone, PartialEq)]
pub struct Unreached {
    pub id: String,
    pub name: String,
    pub from_bp: f64,
    pub to_bp: f64,
    pub likelihood_share: f64,
    pub occupancy: f64,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct LobeProbe {
    pub probed: usize,
    pub not_reached: Vec<Unreached>,
    pub left: usize,
    pub untestable: usize,
    pub skipped: usize,
}

/// What a deposition sequence inferred about its own rigidity.
#[derive(Debug, Clone, PartialEq)]
pub struct DepositionReport {
    pub element_id: String,
    pub name: String,
    pub depth_units: String,
    pub levels: usize,
    pub variable: bool,
    pub log_k_min: f64,
    pub log_k_max: f64,
    pub k_mean: Option<f64>,
    pub log10_k_mean: Option<f64>,
    pub log10_k_hpd_68: Option<(f64, f64)>,
    /// None for a fixed k; whether a tenth of the run sat on either end node.
    pub pinned: Option<bool>,
}

/// A sampled solve, in the exact solvers' shape plus what only a run has.
#[derive(Debug, Clone, PartialEq)]
pub struct Sampled {
    pub measurements: Vec<(String, Posterior)>,
    /// Boundaries and undated levels with any mass, with their names.
    pub boundaries: Vec<(String, String, Posterior)>,
    pub lobe_probe: Option<LobeProbe>,
    pub limits_shrunk: bool,
    pub deposition: Vec<DepositionReport>,
    pub uniform_span_prior: bool,
    pub sweeps: usize,
    pub chains: usize,
    pub sweeps_per_chain: Vec<usize>,
    pub within_chain_convergence: f64,
    pub per_chain_convergence: Vec<f64>,
    pub between_chain_convergence: Option<f64>,
    pub rhat: Option<f64>,
    pub rhat_target: Option<f64>,
    pub convergence: f64,
    pub chain_threshold_gap: Option<f64>,
    pub chain_threshold_gap_target: Option<f64>,
    pub temperatures: usize,
    pub converged: bool,
    pub acceptance: Vec<f64>,
    pub outlier_scale: Vec<(String, OutlierScale)>,
    pub grid: (f64, f64, f64),
    pub joint_draws: Option<Draws>,
    pub joint_scales: Option<HashMap<String, Vec<f64>>>,
    pub joint_boundaries: Option<Draws>,
    pub intervals: Option<Block>,
    /// The pooled histograms the summaries came from, by parameter row.
    pub histograms: Vec<Vec<f64>>,
    pub params: Vec<String>,
}

/// Whether calibration lobes the run left empty are empty because the model
/// rules them out or because no chain reached them: a chain STARTED in one
/// leaves a lobe the model rules out and stays in one with real mass.
pub fn probe_unvisited_lobes(
    model: &Model,
    histograms: &[Vec<f64>],
    seed: u64,
    temperatures: usize,
) -> Result<LobeProbe, Error> {
    let mut targets: Vec<(f64, usize, Lobe)> = Vec::new();
    for (d, row) in histograms.iter().enumerate().take(model.num_dates) {
        let total = sum(row);
        if total <= 0.0 {
            continue;
        }
        for lobe in model.lobes(d) {
            let (_, share, first, last) = lobe;
            if share >= PROBE_SHARE && sum(&row[first..last]) < PROBE_EMPTY * total {
                targets.push((share, d, lobe));
            }
        }
    }
    targets.sort_by(|a, b| b.0.total_cmp(&a.0));
    let mut record = LobeProbe {
        skipped: targets.len().saturating_sub(MAX_PROBES),
        ..LobeProbe::default()
    };
    for (i, &(share, d, lobe)) in targets.iter().take(MAX_PROBES).enumerate() {
        let (peak, _, first, last) = lobe;
        let inlier = model.inlier_row(d);
        let core: Vec<bool> = (0..model.m)
            .map(|c| c >= first && c < last && inlier[c] >= PROBE_CORE * inlier[peak])
            .collect();
        let Some(state) = model.probe_start(d, lobe) else {
            record.untestable += 1;
            continue;
        };
        let mut chain = Chain::new(
            model.clone(),
            PROBE_SWEEPS,
            seed + 7919 + i as u64,
            None,
            Some(PROBE_SWEEPS),
            0,
            &Start::default(),
            temperatures,
        )?;
        chain.state = state;
        let mut inside = 0;
        for sweep in 0..PROBE_SWEEPS {
            chain.sweep();
            if sweep >= PROBE_READ_FROM && core[chain.state.index[d] as usize] {
                inside += 1;
            }
        }
        let occupancy = inside as f64 / (PROBE_SWEEPS - PROBE_READ_FROM) as f64;
        record.probed += 1;
        if occupancy >= PROBE_STAYS {
            let m = &model.measurements[d];
            record.not_reached.push(Unreached {
                id: m.id.clone(),
                name: m.label().to_owned(),
                from_bp: model.grid[first],
                to_bp: model.grid[last - 1],
                likelihood_share: share,
                occupancy,
            });
        } else {
            record.left += 1;
        }
    }
    Ok(record)
}

/// What each deposition sequence inferred about its own rigidity, and
/// whether the posterior ran against the prior's bound.
fn deposition_report(model: &Model, rate_histograms: &[Vec<f64>]) -> Vec<DepositionReport> {
    model
        .depositions
        .iter()
        .enumerate()
        .map(|(d, deposition)| {
            let mut entry = DepositionReport {
                element_id: deposition.element_id.clone(),
                name: deposition.name.clone(),
                depth_units: deposition.depth_units.clone(),
                levels: deposition.ids.len(),
                variable: deposition.variable(),
                log_k_min: deposition.log_k_min,
                log_k_max: deposition.log_k_max,
                k_mean: None,
                log10_k_mean: None,
                log10_k_hpd_68: None,
                pinned: None,
            };
            if let Some(k) = deposition.fixed_k {
                entry.k_mean = Some(k);
                return entry;
            }
            let visits = &rate_histograms[d];
            let total = sum(visits);
            if total <= 0.0 {
                return entry;
            }
            let nodes: Vec<f64> = deposition.k_nodes.iter().map(|k| k.log10()).collect();
            let weights: Vec<f64> = visits.iter().map(|v| v / total).collect();
            let mean = sum(&nodes
                .iter()
                .zip(&weights)
                .map(|(n, w)| n * w)
                .collect::<Vec<_>>());
            let cdf = cumsum(&weights);
            entry.log10_k_mean = Some(mean);
            entry.k_mean = Some(py_pow(10.0, mean));
            entry.log10_k_hpd_68 = Some((
                interp_one(0.16, &cdf, &nodes),
                interp_one(0.84, &cdf, &nodes),
            ));
            entry.pinned = Some(weights[0] > 0.1 || weights[weights.len() - 1] > 0.1);
            entry
        })
        .collect()
}

/// Posterior summaries for every date, boundary and undated level from a
/// sampled run. `chains` above one runs that many chains in parallel under
/// one stopping rule ([`solve_chains`]). Joint states are kept whatever was
/// asked for, because Amodel is a function of every date at once.
pub fn solve(
    measurements: &[Measurement],
    root: &Element,
    options: &SampleOptions,
    cal: &Calibrator,
    on_progress: Option<&mut dyn FnMut(&Progress) -> bool>,
) -> Result<Result<Sampled, Cancelled>, Error> {
    let temperatures = if options.match_oxcal {
        1
    } else {
        options.temperatures
    };
    let model = Model::new(
        measurements,
        root,
        options.grid.clone(),
        options.uniform_span_prior,
        cal,
    )?;
    let wants_intervals =
        options.estimate_durations || !model.depositions.is_empty() || options.dispersal.is_some();
    let pooled = if options.chains > 1 {
        let chains = ChainsOptions {
            chains: options.chains,
            sweeps: options.sweeps,
            seed: options.seed,
            target_convergence: options.target_convergence,
            max_sweeps: options.max_sweeps,
            min_sweeps: options.min_sweeps,
            collect_draws: options.interval_draws,
            start_strategy: DEFAULT_START_STRATEGY.to_owned(),
            temperatures,
            match_oxcal: options.match_oxcal,
        };
        match solve_chains(&model, &chains, on_progress)? {
            Ok(p) => p,
            Err(c) => return Ok(Err(c)),
        }
    } else {
        let max_sweeps = match options.max_sweeps {
            None if options.min_sweeps > 0 => {
                Some(options.sweeps.max(MAX_SWEEPS).max(options.min_sweeps))
            }
            other => other,
        };
        let run_options = RunOptions {
            sweeps: options.sweeps,
            seed: options.seed,
            burn_in: None,
            target_convergence: options.target_convergence,
            max_sweeps,
            collect_draws: options.interval_draws,
            min_sweeps: options.min_sweeps,
            start: Start::default(),
            temperatures,
            match_oxcal: options.match_oxcal,
        };
        match run(model.clone(), &run_options, on_progress)? {
            Ok(r) => Pooled::single(r),
            Err(c) => return Ok(Err(c)),
        }
    };
    let chain = &pooled.result;
    let mut rng = Generator::new(options.seed);

    let mut summaries = Vec::with_capacity(model.num_dates);
    for (d, histogram) in chain.histograms.iter().enumerate().take(model.num_dates) {
        let mut summary = summarize(
            &model.grid,
            histogram,
            &mut rng,
            options.num_samples,
            options.resolution,
        )?;
        if let Some(&(_, p)) = chain.outlier_probability.iter().find(|(r, _)| *r == d) {
            summary.outlier_probability = Some(p);
        }
        summaries.push((model.params[d].clone(), summary));
    }
    let mut boundaries = Vec::new();
    for &b in &model.boundary_rows {
        let density = &chain.histograms[b];
        if sum(density) <= 0.0 {
            continue;
        }
        let id = &model.params[b];
        let name = model
            .plan
            .boundary_name(id)
            .unwrap_or("Boundary")
            .to_owned();
        let posterior = summarize(
            &model.grid,
            density,
            &mut rng,
            options.num_samples,
            options.resolution,
        )?;
        boundaries.push((id.clone(), name, posterior));
    }
    for &level in &model.level_rows {
        let density = &chain.histograms[level];
        if sum(density) <= 0.0 {
            continue;
        }
        let name = model
            .level_names
            .get(&level)
            .cloned()
            .unwrap_or_else(|| "Undated level".to_owned());
        let posterior = summarize(
            &model.grid,
            density,
            &mut rng,
            options.num_samples,
            options.resolution,
        )?;
        boundaries.push((model.params[level].clone(), name, posterior));
    }

    let lobe_probe = if options.match_oxcal {
        None
    } else {
        Some(probe_unvisited_lobes(
            &model,
            &chain.histograms,
            options.seed,
            temperatures,
        )?)
    };
    let within = if chain.convergence.is_empty() {
        0.0
    } else {
        chain
            .convergence
            .iter()
            .copied()
            .fold(f64::INFINITY, f64::min)
    };
    let outlier_scale = model
        .outlier_models
        .iter()
        .enumerate()
        .filter_map(|(m, name)| {
            let visits = &chain.scale_histograms[m];
            let total = sum(visits);
            if total <= 0.0 {
                return None;
            }
            let nodes = &model.scale_grid[m];
            let weights: Vec<f64> = visits.iter().map(|v| v / total).collect();
            let mean = sum(&nodes
                .iter()
                .zip(&weights)
                .map(|(n, w)| n * w)
                .collect::<Vec<_>>());
            let cdf = cumsum(&weights);
            Some((
                name.clone(),
                OutlierScale {
                    log10_mean: mean,
                    log10_hpd_68: (interp_one(0.16, &cdf, nodes), interp_one(0.84, &cdf, nodes)),
                    years_mean: py_pow(10.0, mean),
                },
            ))
        })
        .collect();

    let (mut joint_draws, mut joint_scales, mut joint_boundaries, mut intervals) =
        (None, None, None, None);
    if let Some(draws) = &chain.draws {
        let joint: Draws = model
            .params
            .iter()
            .enumerate()
            .map(|(col, p)| {
                (
                    p.clone(),
                    draws
                        .iter()
                        .map(|row| model.grid[row[col] as usize])
                        .collect(),
                )
            })
            .collect();
        let first = model.params.len();
        joint_scales = Some(
            model
                .outlier_models
                .iter()
                .enumerate()
                .map(|(i, name)| {
                    (
                        name.clone(),
                        draws
                            .iter()
                            .map(|row| model.scale_grid[i][row[first + i] as usize])
                            .collect(),
                    )
                })
                .collect(),
        );
        joint_draws = Some(
            model.params[..model.num_dates]
                .iter()
                .map(|d| (d.clone(), joint[d].clone()))
                .collect(),
        );
        joint_boundaries = Some(
            model.params[model.num_dates..]
                .iter()
                .map(|p| (p.clone(), joint[p].clone()))
                .collect(),
        );
        if wants_intervals {
            intervals = Some(report(
                Some(root),
                measurements,
                &joint,
                false,
                options.estimate_durations,
                options.dispersal.as_ref(),
                Some(options.resolution),
            ));
        }
    }

    let rhat = pooled.rhat;
    let gap = pooled.chain_threshold_gap;
    Ok(Ok(Sampled {
        measurements: summaries,
        boundaries,
        lobe_probe,
        limits_shrunk: chain.limits_shrunk,
        deposition: deposition_report(&model, &chain.rate_histograms),
        uniform_span_prior: !model.span_priors.is_empty(),
        sweeps: chain.sweeps,
        chains: pooled.chains,
        sweeps_per_chain: pooled.sweeps_per_chain.clone(),
        within_chain_convergence: within,
        per_chain_convergence: pooled.per_chain_convergence.clone(),
        between_chain_convergence: pooled.between_chain_convergence,
        rhat,
        rhat_target: rhat.map(|_| TARGET_RHAT),
        convergence: within,
        chain_threshold_gap: gap,
        chain_threshold_gap_target: gap.map(|_| TARGET_CHAIN_THRESHOLD_GAP),
        temperatures,
        converged: within >= options.target_convergence
            && rhat.is_none_or(|r| r <= TARGET_RHAT)
            && gap.is_none_or(|g| g <= TARGET_CHAIN_THRESHOLD_GAP),
        acceptance: chain.acceptance.clone(),
        outlier_scale,
        grid: (model.grid[0], model.grid[model.m - 1], model.dt),
        joint_draws,
        joint_scales,
        joint_boundaries,
        intervals,
        histograms: chain.histograms.clone(),
        params: model.params.clone(),
    }))
}
