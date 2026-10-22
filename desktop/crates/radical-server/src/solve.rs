//! Solving a Bayesian model (`api/analysis.py`'s `_run_bayesian` and
//! `_solve_bayesian`): the wiggle match, the exact solvers or the sampler,
//! chosen per model, with the solver record that says which ran and why.
//! Shared by a fresh run and a re-run, so the two cannot drift apart.
//!
//! The model tree stays JSON here, as the backend keeps it: it is stored and
//! returned with keys the solvers never read. [`element`] reads the part the
//! solvers do.

use std::collections::HashMap;

use radical_core::agreement;
use radical_core::cross_check::{self, CrossCheck, Row};
use radical_core::dispersal::DispersalSpec;
use radical_core::exact_chain::{self, Evidence, OutlierScale, Posterior, SolveOptions};
use radical_core::exact_parallel;
use radical_core::gibbs::chain::Progress;
use radical_core::gibbs::solve::{DepositionReport, LobeProbe};
use radical_core::gibbs::{self, SampleOptions};
use radical_core::intervals::Draws;
use radical_core::kde;
use radical_core::measurement::{Calibrator, Measurement};
use radical_core::model_tree::{
    Element, Param, Params, elements_of_type, plan_model, standalone_wiggle_match,
    validate_ordinates,
};
use radical_core::wiggle_match;
use radical_core::{Error, Mode};
use radical_jobs::Handle;
use radical_store::Timestamp;
use radical_store::rows::{AnalysisResult, RadiocarbonSample};
use serde_json::{Map, Value, json};

use crate::State;
use crate::blocks;
use crate::routes::analysis::ranges_json;

// --------------------------------------------------------------------------
// Inputs
// --------------------------------------------------------------------------

fn param(v: &Value) -> Param {
    match v {
        Value::Null => Param::Null,
        Value::Bool(b) => Param::Bool(*b),
        Value::Number(n) => Param::Number(n.as_f64().unwrap_or(f64::NAN)),
        Value::String(s) => Param::Text(s.clone()),
        // Nothing the solvers read is a list or a mapping; kept as text so a
        // setting they do not know cannot stop the run.
        other => Param::Text(other.to_string()),
    }
}

fn opt_str(v: &Value) -> Option<String> {
    v.as_str().map(str::to_owned)
}

/// The part of a stored or requested tree the solvers read.
pub fn element(v: &Value) -> Element {
    Element {
        id: v["id"].as_str().unwrap_or_default().to_owned(),
        element_type: v["element_type"].as_str().unwrap_or_default().to_owned(),
        name: opt_str(&v["name"]),
        tpq_age_bp: v["tpq_age_bp"].as_f64(),
        taq_age_bp: v["taq_age_bp"].as_f64(),
        params: v["params"].as_object().map(|p| {
            p.iter()
                .map(|(k, v)| (k.clone(), param(v)))
                .collect::<Params>()
        }),
        children: v["children"]
            .as_array()
            .map(|c| c.iter().map(element).collect())
            .unwrap_or_default(),
    }
}

/// A saved analysis's date as the solvers take it (`_measurement_dicts`).
pub fn stored_measurement(s: &RadiocarbonSample) -> Measurement {
    Measurement {
        id: s.id.clone(),
        sample_name: Some(s.sample_name.clone()),
        age_bp: s.age_bp,
        error_1sigma: s.error_1sigma,
        curve_id: s.curve_id.clone(),
        delta_r: s.delta_r,
        delta_r_error: s.delta_r_error,
        tpq_age_bp: s.tpq_age_bp,
        taq_age_bp: s.taq_age_bp,
        element_id: s
            .element_id
            .clone()
            .filter(|e| !e.is_empty())
            .or_else(|| s.group_id.clone()),
        group_id: None,
        outlier_prior: s.outlier_prior,
        outlier_model: s.outlier_model.clone(),
        inbuilt_years: s.inbuilt_years,
        inbuilt_shape: s.inbuilt_shape.clone(),
        ring_offset: s.ring_offset,
        ring_offset_error: s.ring_offset_error,
        depth_cm: s.depth_cm,
        latitude: s.latitude,
        longitude: s.longitude,
    }
}

/// Python's truthiness of a JSON value.
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// A stored `bayesian_config`, read as the backend's `.get()`s read it.
struct Config<'a>(&'a Value);

impl Config<'_> {
    /// `config.get(key, default)`, as a truth value.
    fn flag(&self, key: &str, default: bool) -> bool {
        self.0.get(key).map_or(default, truthy)
    }

    /// `config.get(key) or default`, as a number.
    fn number_or(&self, key: &str, default: f64) -> f64 {
        match self.0.get(key).and_then(Value::as_f64) {
            Some(x) if x != 0.0 => x,
            _ => default,
        }
    }

    fn get(&self, key: &str) -> &Value {
        self.0.get(key).unwrap_or(&Value::Null)
    }

    /// `_reporting_resolution`.
    fn resolution(&self) -> f64 {
        self.number_or("resolution", exact_chain::REPORTING_STEP_YEARS)
    }

    /// `config.get('mcmc_samples', 2000)`.
    fn samples(&self) -> usize {
        self.0
            .get("mcmc_samples")
            .and_then(Value::as_u64)
            .unwrap_or(2000) as usize
    }

    /// `_dispersal_spec`.
    fn dispersal(&self) -> Option<DispersalSpec> {
        if !self.flag("estimate_dispersal", false) {
            return None;
        }
        let origin = self.get("dispersal_origin");
        let placed = match (origin["lat"].as_f64(), origin["lon"].as_f64()) {
            (Some(lat), Some(lon)) => Some((lat, lon)),
            _ => None,
        };
        Some(DispersalSpec { origin: placed })
    }
}

// --------------------------------------------------------------------------
// Results as JSON
// --------------------------------------------------------------------------

/// A posterior as the backend's dict, with or without its raw samples.
pub fn posterior_json(p: &Posterior, samples: bool) -> Value {
    let mut out = Map::new();
    out.insert("posterior_mean".into(), json!(p.posterior_mean));
    out.insert("posterior_median".into(), json!(p.posterior_median));
    out.insert("posterior_std".into(), json!(p.posterior_std));
    out.insert("hpd_68".into(), json!([p.hpd_68.0, p.hpd_68.1]));
    out.insert("hpd_95".into(), json!([p.hpd_95.0, p.hpd_95.1]));
    out.insert("hpd_68_ranges".into(), ranges_json(&p.hpd_68_ranges));
    out.insert("hpd_95_ranges".into(), ranges_json(&p.hpd_95_ranges));
    out.insert(
        "probability_distribution".into(),
        json!(p.probability_distribution),
    );
    if samples {
        out.insert("posterior_samples".into(), json!(p.posterior_samples));
    }
    Value::Object(out)
}

/// A date's result: its posterior, the outlier probability where it was
/// flagged, and its agreement index where one was formed.
fn date_json(p: &Posterior, samples: bool, agreement: bool) -> Value {
    let mut out = posterior_json(p, samples);
    if let Some(o) = p.outlier_probability {
        out["outlier_probability"] = json!(o);
    }
    if agreement {
        out["agreement_index"] = json!(p.agreement_index);
    }
    out
}

fn outlier_scales(scales: &[(String, OutlierScale)]) -> Value {
    Value::Object(
        scales
            .iter()
            .map(|(model, s)| {
                (
                    model.clone(),
                    json!({
                        "log10_mean": s.log10_mean,
                        "log10_hpd_68": [s.log10_hpd_68.0, s.log10_hpd_68.1],
                        "years_mean": s.years_mean,
                    }),
                )
            })
            .collect(),
    )
}

fn evidence_json(e: &Evidence) -> Value {
    match e {
        Evidence::Unavailable(reason) => json!({"available": false, "reason": reason}),
        Evidence::Available {
            log,
            log10,
            lower,
            upper,
            step,
            approximate,
            readings,
        } => json!({
            "available": true, "log": log, "log10": log10, "lower": lower,
            "upper": upper, "step": step, "approximate": approximate,
            "readings": readings,
        }),
    }
}

fn row_json(r: &Row) -> Value {
    json!({
        "id": r.id, "name": r.name, "distance": r.distance,
        "threshold_gap": r.threshold_gap, "sampled_mean": r.sampled_mean,
        "direct_mean": r.direct_mean,
    })
}

fn cross_check_json(c: &CrossCheck) -> Value {
    json!({
        "status": c.status,
        "reason": c.reason,
        "shape": c.shape,
        "threshold": c.threshold,
        "threshold_gap_limit": c.threshold_gap_limit,
        "checked": c.checked,
        "worst": c.worst.as_ref().map(row_json),
        "differing": c.differing.iter().map(row_json).collect::<Vec<_>>(),
        "seconds": c.seconds,
    })
}

fn lobe_probe_json(p: &LobeProbe) -> Value {
    json!({
        "probed": p.probed,
        "not_reached": p.not_reached.iter().map(|u| json!({
            "id": u.id, "name": u.name, "from_bp": u.from_bp, "to_bp": u.to_bp,
            "likelihood_share": u.likelihood_share, "occupancy": u.occupancy,
        })).collect::<Vec<_>>(),
        "left": p.left,
        "untestable": p.untestable,
        "skipped": p.skipped,
    })
}

fn deposition_json(d: &DepositionReport) -> Value {
    let mut out = json!({
        "element_id": d.element_id,
        "name": d.name,
        "depth_units": d.depth_units,
        "levels": d.levels,
        "variable": d.variable,
        "log_k_min": d.log_k_min,
        "log_k_max": d.log_k_max,
    });
    if let Some(m) = d.log10_k_mean {
        out["log10_k_mean"] = json!(m);
    }
    if let Some(k) = d.k_mean {
        out["k_mean"] = json!(k);
    }
    if let Some((lo, hi)) = d.log10_k_hpd_68 {
        out["log10_k_hpd_68"] = json!([lo, hi]);
    }
    // Set (to None) for a fixed rate, and from the run for a sampled one.
    if !d.variable || d.log10_k_mean.is_some() {
        out["pinned"] = json!(d.pinned);
    }
    out
}

// --------------------------------------------------------------------------
// Solving
// --------------------------------------------------------------------------

/// What a run produced.
pub struct Solved {
    /// Each date's result, by the id it was solved under.
    pub results: Vec<(String, Posterior)>,
    /// Whether the dates carry an agreement index (every path but none).
    pub agreement: bool,
    /// Each element's posterior -- Boundaries, a wiggle match's reference
    /// age, an undated level's age -- by element id.
    pub elements: Vec<(String, Posterior)>,
    pub solver: Value,
    pub intervals: Option<Value>,
    pub summaries: Option<Value>,
}

impl Solved {
    pub fn element(&self, id: &str) -> Option<&Posterior> {
        self.elements.iter().find(|(e, _)| e == id).map(|(_, p)| p)
    }

    /// A date's result as the run's response gives it: no raw samples.
    pub fn result_json(&self, p: &Posterior) -> Value {
        date_json(p, false, self.agreement)
    }
}

/// Why a run did not finish.
pub enum Failed {
    Cancelled,
    /// A refusal from the maths, in the words the backend's `str(e)` gives.
    Error(Error),
    /// A fault in the app: the database, say.
    Internal(String),
}

impl From<Error> for Failed {
    fn from(e: Error) -> Self {
        Self::Error(e)
    }
}

impl From<radical_store::Error> for Failed {
    fn from(e: radical_store::Error) -> Self {
        Self::Internal(format!("database: {e}"))
    }
}

impl From<crate::ApiError> for Failed {
    fn from(e: crate::ApiError) -> Self {
        Self::Internal(e.detail.as_str().unwrap_or_default().to_owned())
    }
}

impl Failed {
    /// A refusal the run wrote itself, as `ValueError(message)` is.
    pub fn refused(message: impl Into<String>) -> Self {
        Self::Error(Error::Model(message.into()))
    }

    /// What the run's own request answers with: `str(e)`.
    pub fn message(&self) -> String {
        match self {
            Self::Cancelled => "Cancelled".to_owned(),
            Self::Error(e) => e.to_string(),
            Self::Internal(m) => m.clone(),
        }
    }

    pub fn job_error(self) -> radical_jobs::JobError {
        match self {
            Self::Cancelled => radical_jobs::JobError::Cancelled,
            Self::Error(e) => radical_jobs::JobError::Core(e),
            Self::Internal(m) => radical_jobs::JobError::Internal(m),
        }
    }

    /// `failures.classify(e).message`: what is stored and shown.
    pub fn classified(self) -> String {
        radical_jobs::classify(&self.job_error()).message
    }
}

/// The sampler's progress callback.
pub type OnProgress<'a> = Option<&'a mut dyn FnMut(&Progress) -> bool>;

/// `_job_progress`: the sampler's progress as the job's, and the job's
/// cancellation as the sampler's stop.
pub fn job_progress(handle: &Handle) -> impl FnMut(&Progress) -> bool + '_ {
    move |info: &Progress| {
        let cap = info.max_sweeps;
        handle.update(
            (cap > 0).then(|| info.sweeps as f64 / cap as f64),
            Some(json!({
                "phase": info.phase,
                "sweeps": info.sweeps,
                "max_sweeps": (cap > 0).then_some(cap),
                "convergence": info.convergence,
            })),
            false,
        );
        !handle.cancelled()
    }
}

/// `_run_bayesian`: solve `tree` against `dates` as `config` asks.
pub fn run_bayesian(
    state: &State,
    dates: &[Measurement],
    tree: &Value,
    config: &Value,
    on_progress: OnProgress,
) -> Result<Solved, Failed> {
    let config = Config(config);
    let match_oxcal = config.flag("match_oxcal", false);
    let cal = Calibrator {
        curves: &*state.curves,
        mode: if match_oxcal {
            Mode::MatchOxcal
        } else {
            Mode::Standard
        },
    };
    let mut solved = solve_bayesian(dates, tree, &config, &cal, on_progress)?;
    if match_oxcal {
        solved.solver["match_oxcal"] = json!(true);
    }
    Ok(solved)
}

fn model_error(message: &str) -> Failed {
    Failed::Error(Error::Model(message.to_owned()))
}

fn solve_bayesian(
    dates: &[Measurement],
    tree: &Value,
    config: &Config,
    cal: &Calibrator,
    on_progress: OnProgress,
) -> Result<Solved, Failed> {
    let requested = config.get("solver").as_str().unwrap_or("auto").to_owned();
    let requested = if requested.is_empty() {
        "auto".to_owned()
    } else {
        requested
    };
    let match_oxcal = config.flag("match_oxcal", false);
    let resolution = config.resolution();
    let estimate_durations = config.flag("estimate_durations", false);
    let estimate_kde = config.flag("estimate_kde", false);
    let dispersal = config.dispersal();

    // "Apply inbuilt-age allowances" off runs the model as if every
    // allowance were zero.
    let owned: Vec<Measurement>;
    let dates = if config.0.get("apply_inbuilt") == Some(&Value::Bool(false)) {
        owned = dates
            .iter()
            .map(|m| Measurement {
                inbuilt_years: None,
                ..m.clone()
            })
            .collect();
        &owned[..]
    } else {
        dates
    };

    let root = element(tree);
    validate_ordinates(Some(&root), dates)?;

    if let Some(wiggle) = wiggle(dates, &root, &requested, resolution, cal)? {
        return Ok(wiggle);
    }

    let layout = exact_layout(&root, dates);
    if match_oxcal && requested == "exact" {
        return Err(model_error(
            "Match OxCal 4.4 uses the MH/Gibbs sampler, so it cannot be combined with the exact solver. Choose Automatic or the MH/Gibbs sampler.",
        ));
    }
    if requested == "exact"
        && let Err(reason) = &layout
    {
        return Err(model_error(&format!(
            "this model cannot be integrated exactly, so the exact solver was not used: {reason}. Either fix that, or choose the MH/Gibbs sampler, which has no such restriction."
        )));
    }

    if let Ok(layout) = &layout
        && requested != "gibbs"
        && !match_oxcal
    {
        let options = SolveOptions {
            num_samples: config.samples(),
            uniform_span_prior: config.flag("uniform_span_prior", true),
            estimate_durations,
            dispersal,
            resolution,
            ..SolveOptions::default()
        };
        let (solution, shape) = match layout {
            Layout::Chain(chain) => (
                exact_chain::solve(dates, &root, chain, &options, cal)?,
                "chain",
            ),
            Layout::Parallel(stages) => (
                exact_parallel::solve(dates, &root, stages, &options, cal)?,
                "parallel",
            ),
        };
        let mut measurements = solution.measurements.clone();
        let draws = (!solution.joint_draws.is_empty()).then_some(&solution.joint_draws);
        let overall = agreement::attach(
            &mut measurements,
            dates,
            draws,
            solution.joint_scales.as_ref(),
            cal,
        )?;
        let amodel = match draws {
            Some(d) => agreement::model_agreement(dates, d, solution.joint_scales.as_ref(), cal)?,
            None => None,
        };
        let mut solver = json!({
            "method": "exact",
            "requested": requested,
            "shape": shape,
            "resolution": resolution,
            "reason": null,
            "model_agreement": amodel,
            "overall_agreement": overall,
        });
        if let Some(scales) = solution.outlier_scale.as_ref().filter(|s| !s.is_empty()) {
            solver["outlier_scale"] = outlier_scales(scales);
        }
        if let Some(e) = &solution.evidence {
            solver["evidence"] = evidence_json(e);
        }
        let summaries = phase_summaries(
            &root,
            dates,
            Some(&solution.joint_boundaries),
            Some(&solution.joint_draws),
            solution.grid.step,
            estimate_kde,
        );
        return Ok(Solved {
            results: measurements,
            agreement: true,
            elements: solution
                .boundaries
                .iter()
                .map(|(id, b)| (id.clone(), b.posterior.clone()))
                .collect(),
            solver,
            intervals: solution.intervals.as_ref().map(blocks::block),
            summaries,
        });
    }

    let reason = layout.err();
    let seed = config.get("seed").as_u64().unwrap_or(gibbs::DEFAULT_SEED);
    let min_sweeps = config.number_or("mcmc_min_sweeps", 0.0) as usize;
    let options = SampleOptions {
        num_samples: config.samples(),
        seed,
        min_sweeps,
        uniform_span_prior: config.flag("uniform_span_prior", true),
        estimate_durations,
        dispersal,
        chains: config.number_or("mcmc_chains", gibbs::DEFAULT_CHAINS as f64) as usize,
        temperatures: config.number_or("mcmc_temperatures", gibbs::DEFAULT_TEMPERATURES as f64)
            as usize,
        resolution,
        match_oxcal,
        ..SampleOptions::default()
    };
    let mut sampled = match gibbs::solve(dates, &root, &options, cal, on_progress)? {
        Ok(s) => s,
        Err(_) => return Err(Failed::Cancelled),
    };
    let overall = agreement::attach(
        &mut sampled.measurements,
        dates,
        sampled.joint_draws.as_ref(),
        sampled.joint_scales.as_ref(),
        cal,
    )?;
    let amodel = match sampled.joint_draws.as_ref().filter(|d| !d.is_empty()) {
        Some(d) => agreement::model_agreement(dates, d, sampled.joint_scales.as_ref(), cal)?,
        None => None,
    };
    let cross = if config.0.get("cross_check") == Some(&Value::Bool(false)) {
        Value::Null
    } else if match_oxcal {
        json!({
            "status": "not_checked",
            "reason": "the run matches OxCal 4.4, which the direct calculation does not",
            "shape": null, "threshold": cross_check::DISAGREEMENT, "checked": 0,
            "worst": null, "differing": [], "seconds": null,
        })
    } else {
        cross_check_json(&cross_check::check(
            dates,
            &root,
            &sampled,
            config.flag("uniform_span_prior", true),
            cal,
        ))
    };
    let solver = json!({
        "method": "gibbs",
        "cross_check": cross,
        "lobe_probe": sampled.lobe_probe.as_ref().map(lobe_probe_json),
        "requested": requested,
        "resolution": resolution,
        "model_agreement": amodel,
        "overall_agreement": overall,
        "reason": reason,
        "seed": seed,
        "min_sweeps": min_sweeps,
        "sweeps": sampled.sweeps,
        "convergence": sampled.convergence,
        "converged": sampled.converged,
        "chains": sampled.chains,
        "sweeps_per_chain": sampled.sweeps_per_chain,
        "within_chain_convergence": sampled.within_chain_convergence,
        "per_chain_convergence": sampled.per_chain_convergence,
        "between_chain_convergence": sampled.between_chain_convergence,
        "rhat": sampled.rhat,
        "rhat_target": sampled.rhat_target,
        "deposition": sampled.deposition.iter().map(deposition_json).collect::<Vec<_>>(),
        "outlier_scale": outlier_scales(&sampled.outlier_scale),
    });
    let summaries = phase_summaries(
        &root,
        dates,
        sampled.joint_boundaries.as_ref(),
        sampled.joint_draws.as_ref(),
        sampled.grid.2,
        estimate_kde,
    );
    Ok(Solved {
        agreement: true,
        elements: sampled
            .boundaries
            .iter()
            .map(|(id, _, p)| (id.clone(), p.clone()))
            .collect(),
        solver,
        intervals: sampled.intervals.as_ref().map(blocks::block),
        summaries,
        results: sampled.measurements,
    })
}

enum Layout {
    Chain(exact_chain::Chain),
    Parallel(exact_parallel::Stages),
}

/// `_exact_layout`: how the exact solver would integrate the model, or why
/// it cannot.
fn exact_layout(root: &Element, dates: &[Measurement]) -> Result<Layout, String> {
    let reason = match exact_chain::layout_with_reason(Some(root), dates, false, true) {
        Ok(chain) => return Ok(Layout::Chain(chain)),
        Err(reason) => reason,
    };
    match exact_parallel::layout_with_reason(Some(root), dates) {
        Ok(Some(stages)) => Ok(Layout::Parallel(stages)),
        Ok(None) => Err(reason),
        Err(stages_reason) if stages_reason.is_empty() => Err(reason),
        Err(stages_reason) => Err(stages_reason),
    }
}

/// `_run_wiggle_match`: a model that IS one wiggle-matched series, solved in
/// closed form; None when the model has no wiggle match.
fn wiggle(
    dates: &[Measurement],
    root: &Element,
    requested: &str,
    resolution: f64,
    cal: &Calibrator,
) -> Result<Option<Solved>, Failed> {
    if elements_of_type(Some(root), "wiggle_sequence").is_empty() {
        return Ok(None);
    }
    let Some(element) = standalone_wiggle_match(Some(root), dates) else {
        return Err(model_error(
            "this model puts a wiggle match inside a larger structure, which this app does not yet solve. A wiggle-matched series has one calendar age with the measured gaps added to it, and the surrounding Boundaries and phases would have to model it that way rather than as separate dates. Run the wiggle match on its own for now.",
        ));
    };
    if requested == "gibbs" {
        return Err(model_error(
            "the MH/Gibbs sampler was asked for, but this model is a wiggle match: it has a single free parameter and a closed form, so there is nothing to sample and nothing to converge. Leave the solver on automatic.",
        ));
    }
    let plan = plan_model(root, dates);
    let solved = wiggle_match::solve(
        dates,
        &plan.wiggle_groups[0],
        Some(root),
        4000,
        42,
        resolution,
        cal,
    )?;
    let solver = json!({
        "method": "wiggle_match",
        "requested": requested,
        "resolution": resolution,
        "reason": null,
        "combination_agreement": solved.combination_agreement,
        "combination_threshold": solved.combination_threshold,
        "series_span": solved.series_span,
        "gap_units": solved.gap_units,
        "members": solved.members.iter().map(|m| json!({
            "id": m.id, "name": m.name, "ring_offset": m.ring_offset,
            "ring_offset_error": m.ring_offset_error,
            "agreement_index": m.agreement_index,
        })).collect::<Vec<_>>(),
        "reference_sample_id": solved.reference_sample_id,
        "grid": {"lower": solved.grid.0, "upper": solved.grid.1, "step": solved.grid.2},
    });
    Ok(Some(Solved {
        results: solved.measurements,
        agreement: true,
        elements: vec![(element.id.clone(), solved.reference)],
        solver,
        intervals: None,
        summaries: None,
    }))
}

/// `_phase_summary_block`: a kernel density estimate per Phase from the
/// run's joint draws, or None.
fn phase_summaries(
    root: &Element,
    dates: &[Measurement],
    boundaries: Option<&Draws>,
    draws: Option<&Draws>,
    grid_step: f64,
    wanted: bool,
) -> Option<Value> {
    if !wanted {
        return None;
    }
    let mut all: Draws = HashMap::new();
    for d in [boundaries, draws].into_iter().flatten() {
        all.extend(d.iter().map(|(k, v)| (k.clone(), v.clone())));
    }
    let records = kde::report_phases(Some(root), dates, &all, grid_step, 42);
    (!records.is_empty())
        .then(|| json!({"kde": records.iter().map(blocks::phase_kde).collect::<Vec<_>>()}))
}

/// `_analysis_result_row`: one date's modelled result as a stored row.
pub fn result_row(analysis_id: &str, sample_id: &str, p: &Posterior) -> AnalysisResult {
    AnalysisResult {
        id: radical_store::new_id(),
        analysis_id: analysis_id.to_owned(),
        sample_id: Some(sample_id.to_owned()),
        posterior_mean: Some(p.posterior_mean),
        posterior_median: Some(p.posterior_median),
        posterior_std: Some(p.posterior_std),
        hpd_68_min: Some(p.hpd_68.0),
        hpd_68_max: Some(p.hpd_68.1),
        hpd_95_min: Some(p.hpd_95.0),
        hpd_95_max: Some(p.hpd_95.1),
        hpd_68_ranges: Some(ranges_json(&p.hpd_68_ranges)),
        hpd_95_ranges: Some(ranges_json(&p.hpd_95_ranges)),
        posterior_samples: Some(json!(p.posterior_samples)),
        probability_distribution: Some(json!(p.probability_distribution)),
        agreement_index: p.agreement_index,
        outlier_probability: p.outlier_probability,
        created_at: Some(Timestamp::now()),
        ..AnalysisResult::default()
    }
}

// --------------------------------------------------------------------------
// Relabelling a request's ids as the stored ones
// --------------------------------------------------------------------------

/// `_relabel_tree`: element ids through `ids`, and a wiggle match's
/// reference sample through `samples`, in place.
pub fn relabel_tree(
    e: &mut Value,
    ids: &HashMap<String, String>,
    samples: &HashMap<String, String>,
) {
    if let Some(new) = e["id"].as_str().and_then(|id| ids.get(id)) {
        e["id"] = json!(new);
    }
    if let Some(new) = e["params"]["reference_sample_id"]
        .as_str()
        .and_then(|s| samples.get(s))
        .cloned()
    {
        e["params"]["reference_sample_id"] = json!(new);
    }
    if let Some(children) = e["children"].as_array_mut() {
        for c in children {
            relabel_tree(c, ids, samples);
        }
    }
}

/// `_relabel_intervals`: a durations block's ids through `ids`.
pub fn relabel_intervals(block: Option<Value>, ids: &HashMap<String, String>) -> Option<Value> {
    let mut block = block?;
    if ids.is_empty() || !truthy(&block) {
        return Some(block);
    }
    let rename = |v: &Value| -> Value {
        match v.as_str() {
            Some(s) => json!(
                s.split(':')
                    .map(|part| ids.get(part).map_or(part, String::as_str))
                    .collect::<Vec<_>>()
                    .join(":")
            ),
            None => v.clone(),
        }
    };
    for key in ["durations", "differences"] {
        let records: Vec<Value> = block[key]
            .as_array()
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(|mut r| {
                for field in ["id", "element_id", "from_id", "to_id"] {
                    if truthy(&r[field]) {
                        r[field] = rename(&r[field]);
                    }
                }
                r
            })
            .collect();
        block[key] = Value::Array(records);
    }
    let age_depth: Vec<Value> = block["age_depth"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .map(|mut r| {
            if truthy(&r["element_id"]) {
                r["element_id"] = rename(&r["element_id"]);
            } else if r.get("element_id").is_none() {
                r["element_id"] = Value::Null;
            }
            let levels: Vec<Value> = r["levels"]
                .as_array()
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .map(|mut l| {
                    l["id"] = rename(&l["id"]);
                    l
                })
                .collect();
            r["levels"] = Value::Array(levels);
            r
        })
        .collect();
    block["age_depth"] = Value::Array(age_depth);
    if truthy(&block["phase_activity"]) {
        let rows: Vec<Value> = block["phase_activity"]["rows"]
            .as_array()
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(|mut r| {
                r["id"] = rename(&r["id"]);
                r
            })
            .collect();
        block["phase_activity"]["rows"] = Value::Array(rows);
    }
    Some(block)
}

/// `_relabel_summaries`: each KDE's phase through `ids`.
pub fn relabel_summaries(block: Option<Value>, ids: &HashMap<String, String>) -> Option<Value> {
    let mut block = block?;
    if ids.is_empty() || !truthy(&block) {
        return Some(block);
    }
    let records: Vec<Value> = block["kde"]
        .as_array()
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .map(|mut r| {
            if let Some(new) = r["element_id"].as_str().and_then(|e| ids.get(e)) {
                r["element_id"] = json!(new);
            }
            r
        })
        .collect();
    block["kde"] = Value::Array(records);
    Some(block)
}
