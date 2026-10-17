//! OxCal's sampler: a Metropolis-Hastings / Gibbs hybrid over the model's
//! dates and boundaries, for models the exact solvers cannot integrate. Port
//! of `services/gibbs.py`, whose module docstring states the maths; the parts
//! a reader of this port has to keep in mind are here.
//!
//! - **No annealing path.** It targets the true posterior from the first
//!   sweep, so constraints are HARD (an interval truncation) and the
//!   (older - younger)^(-K) span density goes unfloored: a date's allowed
//!   interval IS the gap between its boundaries, which is what cancels the
//!   eps^(-K) blow-up.
//! - **The trapezoid, in one place.** A date sitting exactly on something it
//!   is ordered against carries half weight per tied edge
//!   ([`model::Model::endpoint_weight`]), and two DATES tied in one cell are
//!   weighted per cluster by their linear extensions. The Gibbs draws, the
//!   Metropolis moves and the log posterior all weigh a configuration this
//!   one way, or they would target different distributions.
//! - **Exact conditionals.** Each date, boundary, shared outlier scale and
//!   deposition rigidity is drawn from its own full conditional by inverse
//!   CDF -- a true Gibbs step, where OxCal makes one MH proposal per
//!   parameter. Group translations ([`model::Model::shift`]) carry whole
//!   phases between calibration lobes, accepted on the exact MH ratio.
//! - **Parallel tempering and several chains.** Each chain runs hot copies on
//!   a flattened likelihood that swap states with it; several chains start
//!   apart and stop together on their pooled rounds ([`chains`]).
//!
//! The draws are NumPy's ([`crate::rng`]): with the same seed and the same
//! arithmetic, a chain here is the chain Python runs, draw for draw. Where the
//! arithmetic differs in its last bit -- a sum NumPy hands to BLAS, an outlier
//! smear Python forms by FFT -- an inverse-CDF draw or an acceptance can come
//! out the other way, and from there the two chains are independent runs of
//! one sampler, compared statistically.

pub mod chain;
pub mod chains;
pub mod model;
pub mod solve;

pub use chain::{Cancelled, RunOptions, run};
pub use chains::{chain_starts, solve_chains, start_quantiles};
pub use model::Model;
pub use solve::{Sampled, SampleOptions, solve};

use crate::special::gammaln;
use crate::numeric::sum;

/// Matching OxCal 4.4: every parameter's hard limits are pulled in to the
/// range it has sampled, once, at the end of the first round from the fourth
/// on whose pooled convergence is above 0.5 (see `Model::shrink_limits`).
pub const SHRINK_AFTER_FRACTION: f64 = 0.4;
pub const SHRINK_MIN_CONVERGENCE: f64 = 0.5;
pub const SHRINK_TRIM: f64 = 1e-6;
/// Sweeps discarded before anything is accumulated, as a fraction of the
/// total: the early ones say more about where the chain began than about the
/// posterior.
pub const BURN_IN_FRACTION: f64 = 0.2;
/// Starting scale of a translation proposal, in years: comparable to a
/// measurement error, so a proposal can leave the current lobe. Adapted
/// towards TARGET_ACCEPTANCE during burn-in.
pub const GROUP_SHIFT_YEARS: f64 = 60.0;
/// Translation proposals per move set per sweep.
pub const MOVES_PER_SWEEP: usize = 2;
/// Temperatures per chain, its own included, and the lowest power the hottest
/// copy raises the likelihood to.
pub const DEFAULT_TEMPERATURES: usize = 3;
pub const COLDEST_HOT_BETA: f64 = 0.05;
pub const TARGET_ACCEPTANCE: f64 = 0.3;
/// Proposals per move set between scale adjustments.
pub const ADAPT_INTERVAL: usize = 50;
/// The share of translation proposals drawn from a deliberately WIDE kernel,
/// and its scale in years: a tuned kernel steps a few years, and calibration
/// lobes 40 years apart are then crossed only by diffusion.
pub const WIDE_MOVE_FRACTION: f64 = 0.3;
pub const WIDE_MOVE_YEARS: f64 = 120.0;
/// Of the wide proposals, the share that jump by a gap between the members'
/// own calibration peaks, and how much that jump is blurred, in years.
pub const PEAK_JUMP_FRACTION: f64 = 0.5;
pub const PEAK_JUMP_JITTER_YEARS: f64 = 5.0;
/// A calibration peak counts when it stands this far above the valleys either
/// side of it, as a fraction of the date's highest point.
pub const PEAK_PROMINENCE: f64 = 0.05;
/// Most distinct peak gaps kept per move set, the most common first.
pub const MAX_PEAK_GAPS: usize = 12;
/// A lobe is worth starting a chain in once it holds this share of its date's
/// inlier likelihood.
pub const LOBE_MIN_MASS: f64 = 0.05;
/// A lobe is still open to a date's start when the window its neighbours
/// leave reaches this fraction of the way up the lobe's peak.
pub const LOBE_HELD_FRACTION: f64 = 0.25;
/// How a chain that does not start at the modes picks a lobe for each date
/// that has several (`Model::start_lobes`).
pub const START_STRATEGIES: [&str; 5] = ["quantile", "position", "calendar", "equalised", "random"];
pub const DEFAULT_START_STRATEGY: &str = "quantile";
/// How far into a lobe the 'equalised' strategy keeps a start from its edges.
pub const EQUALISED_EDGE: f64 = 0.15;
/// Sweeps a run starts with, and the ceiling it extends to.
pub const DEFAULT_SWEEPS: usize = 20000;
pub const MAX_SWEEPS: usize = 200_000;
/// OxCal's own target for its convergence measure.
pub const TARGET_CONVERGENCE: f64 = 0.95;
/// Rounds the accumulation is split into.
pub const ROUNDS: usize = 8;
pub const DEFAULT_CHAINS: usize = 4;
/// The seed a run takes when the analysis names none; chain c takes seed + c.
pub const DEFAULT_SEED: u64 = 42;
/// Most chains run at once, whatever is asked for.
pub const MAX_CHAIN_WORKERS: usize = 8;
/// Gelman-Rubin R-hat the chains must agree to before a pooled run may stop.
pub const TARGET_RHAT: f64 = 1.05;
/// The largest difference any chain may show from the pooled probability of
/// a parameter being older than a given year before a pooled run may stop.
pub const TARGET_CHAIN_THRESHOLD_GAP: f64 = 0.05;
/// The span of the dates' likelihoods the extra chains start across.
pub const START_QUANTILE_RANGE: (f64, f64) = (0.025, 0.975);
/// Nodes the deposition rigidity k is tabulated over, log-uniform between the
/// sequence's own bounds.
pub const DEPOSITION_K_NODES: usize = 24;

/// One deposition_sequence as the sampler works in it: parameter rows oldest
/// first, the depth gaps between adjacent levels, and the grid of rigidities
/// k may take.
#[derive(Debug, Clone)]
pub struct Deposition {
    pub element_id: String,
    pub name: String,
    /// Parameter rows, oldest first.
    pub ids: Vec<usize>,
    pub depth_gaps: Vec<f64>,
    /// None when the run infers k.
    pub fixed_k: Option<f64>,
    pub k_nodes: Vec<f64>,
    pub log_k_min: f64,
    pub log_k_max: f64,
    pub depth_units: String,
}

impl Deposition {
    pub fn variable(&self) -> bool {
        self.fixed_k.is_none()
    }

    /// a_i = k * dz_i, the Dirichlet shape of each interval.
    pub fn shape_parameters(&self, k: f64) -> Vec<f64> {
        self.depth_gaps.iter().map(|dz| k * dz).collect()
    }
}

/// log p(dt | k) for one deposition sequence, NORMALISED: `gaps` are the
/// elapsed years between adjacent levels, oldest first, `alpha` their shapes
/// k*dz. -inf where any interval has collapsed, which is a real divergence of
/// the continuum density wherever k*dz < 1. The normalisation is carried
/// because k is a parameter: with it drawn, Gamma(A)/prod Gamma(a_i) is a
/// function of it.
pub fn log_deposition_density(gaps: &[f64], alpha: &[f64]) -> f64 {
    if gaps.iter().any(|&g| g <= 0.0) {
        return f64::NEG_INFINITY;
    }
    let total = sum(gaps);
    let big_a = sum(alpha);
    let gamma_terms: Vec<f64> = alpha.iter().map(|&a| gammaln(a)).collect();
    let log_terms: Vec<f64> = alpha.iter().zip(gaps).map(|(a, g)| (a - 1.0) * g.ln()).collect();
    gammaln(big_a) - sum(&gamma_terms) + sum(&log_terms) - (big_a - 1.0) * total.ln()
}

/// Current position of every parameter, as an index into the calendar grid,
/// and each outlier model's and deposition sequence's current node.
///
/// Indices rather than years throughout: an allowed interval is then a pair of
/// slice bounds and a conditional draw a search on a cumulative row. Signed,
/// since a span between two of them is a difference that may cross zero.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct State {
    /// By parameter row.
    pub index: Vec<i64>,
    /// By outlier model, in `Model::outlier_models` order.
    pub scale: Vec<usize>,
    /// By deposition sequence, in `Model::depositions` order.
    pub rate: Vec<usize>,
}
