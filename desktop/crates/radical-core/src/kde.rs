//! A kernel density summary of a set of dates: OxCal's KDE_Plot (Bronk Ramsey
//! 2017, "Methods for summarizing radiocarbon datasets", Radiocarbon 59(6)).
//! Port of `services/kde.py`.
//!
//! What it answers is the question a Sum is usually asked -- how the dated
//! events are spread through time -- without the Sum's spikes and cliffs,
//! which are mostly the calibration curve's wiggles, not anything about the
//! events. Each draw takes ONE calendar age per date and builds an ordinary
//! Gaussian KDE from those n points (eq. 1); the plotted curve is the mean of
//! those KDEs over many draws, and the band around it their standard
//! deviation, as OxCal's ±1σ band is. Pooling every draw of every date under
//! one kernel would not be this: it is a smoothed Sum, still carrying each
//! date's whole calibrated width.
//!
//! The bandwidth is OxCal's. Silverman's rule (eq. 2),
//!
//! ```text
//! h_S(t) = (4/3)^(1/5) * sd(t) * n^(-1/5)
//! ```
//!
//! is optimal for a normal underlying distribution and over-smooths anything
//! multimodal, so it is treated as a CEILING: h = g * h_S with g ~ U(0, 1)
//! (eqs. 3-4), and g is weighted by how well each date is predicted by a KDE
//! of all the others (eq. 5),
//!
//! ```text
//! L(g | t) ∝ prod_i [ sum_{j != i} K((t_i - t_j) / (g h_S)) / ((n-1) g h_S) ] ^ ((n-2)/n)
//! ```
//!
//! The power is (n-2)/n because a mean and a standard deviation have already
//! been taken from the n points; at n = 2 it is zero and g stays uniform,
//! there being nothing to say about multimodality in two points.
//!
//! Where this differs from OxCal is only in how it gets there. OxCal moves g
//! by Metropolis-Hastings inside its MCMC. Here each joint state of t is taken
//! as given -- an exact inverse-CDF draw for unmodelled dates, or a solver's
//! joint draw inside a model -- and g is drawn from its conditional on that t
//! directly, from L(g | t) evaluated on a grid. Same target, and no chain for
//! g to converge.
//!
//! Every kernel sum is a linearly binned histogram convolved with the kernel
//! at an eighth of its width, so the cost of a draw grows with the span of the
//! dates, not with n squared.
//!
//! Not here yet: `report_phases`, one KDE per Phase inside a model. It needs
//! the model tree's duration plan (`services/intervals.py`), which is ported
//! with the solvers in Phase 2; [`summarise_draws`] is what it calls.

use crate::Error;
use crate::numeric::{
    argmax, argsort_quicksort, convolve_same, cumsum, diff, interp, interp_bounded, linspace, mean,
    median, percentile, std, sum,
};
use crate::rng::Generator;

/// Draws of the whole dataset. Each contributes a complete, smooth KDE rather
/// than one point, so the mean settles far sooner than a histogram of draws
/// would; this is enough for the ±1σ band to be stable to a few percent.
pub const DEFAULT_DRAWS: usize = 500;

/// The fewest dates a KDE is offered for. Two have a standard deviation and
/// so a Silverman bandwidth; one has neither.
pub const MIN_DATES: usize = 2;

/// How many kernel widths a Gaussian is carried out to. At 6 sigma it is
/// e^-18 of its peak, below anything a plotted density or a likelihood ratio
/// can register.
pub const KERNEL_REACH: f64 = 6.0;

/// Bins per kernel width in every binned kernel sum. Linear binning's error
/// falls as (bin / h)^2: at an eighth of a width it is about 0.1% of the
/// density, and the same fraction of every term in eq. 5.
pub const BINS_PER_BANDWIDTH: f64 = 8.0;

/// Below this fraction of a point's own kernel peak, what is left after
/// taking the point out of the binned sum is rounding error, not the other
/// points' contribution. Those few points are summed exactly instead.
pub const CANCELLATION: f64 = 1e-9;

/// ...unless there are more of them than this, when the bandwidth leaves that
/// many dates with no neighbour in reach. Eq. 5 is then lower than at any
/// bandwidth wide enough to reach them by more than the exact figure could
/// change, so -inf is the right weight and the O(n) sum per point is not paid.
pub const MAX_ISOLATED: usize = 200;

/// The g grid: coarse midpoints across (0, 1), then a finer pass across the
/// cells either side of the best one. For large n the likelihood is sharply
/// peaked and a uniform grid alone would land one node on it or none.
pub const G_COARSE: usize = 32;
pub const G_FINE: usize = 16;

/// The output curve: never coarser than a third of the median bandwidth, never
/// finer than a year, and never more points than this.
pub const MAX_POINTS: f64 = 1500.0;

/// Silverman's constant for a normal kernel, (4/3)^(1/5) ~= 1.06, as Python's
/// `**` computes it.
fn silverman_constant() -> f64 {
    crate::py_pow(4.0 / 3.0, 0.2)
}

fn sqrt_two_pi() -> f64 {
    (2.0 * std::f64::consts::PI).sqrt()
}

/// Eq. 2: the Silverman bandwidth for n points, the ceiling on h.
pub fn silverman_bandwidth(t: &[f64]) -> f64 {
    silverman_constant() * std(t, 1) * crate::py_pow(t.len() as f64, -0.2)
}

/// A normalised N(0, h) sampled every `delta` years, centred.
fn kernel(h: f64, delta: f64) -> Vec<f64> {
    let half = ((KERNEL_REACH * h / delta).ceil() as i64).max(1);
    let norm = h * sqrt_two_pi();
    (-half..=half)
        .map(|i| {
            let x = i as f64 * delta / h;
            (-0.5 * (x * x)).exp() / norm
        })
        .collect()
}

/// sum_i K_h(x - t_i) on a grid of spacing `delta`, by linear binning and a
/// direct convolution, with what a caller needs to read it back at the points
/// and to take each point's own contribution out again.
struct Binned {
    field: Vec<f64>,
    start: f64,
    left: Vec<usize>,
    frac: Vec<f64>,
    kernel: Vec<f64>,
}

fn binned_field(t: &[f64], h: f64, delta: f64) -> Binned {
    let kernel = kernel(h, delta);
    let half = kernel.len() / 2;
    let t_min = t.iter().copied().fold(f64::INFINITY, f64::min);
    let t_max = t.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let start = t_min - (half + 1) as f64 * delta;
    let mut left = Vec::with_capacity(t.len());
    let mut frac = Vec::with_capacity(t.len());
    for &v in t {
        let pos = (v - start) / delta;
        let floor = pos.floor();
        left.push(floor as usize);
        frac.push(pos - floor);
    }
    let size = (((t_max - start) / delta).ceil() as usize + half + 3).max(kernel.len());
    // Two weighted bincounts, as NumPy forms them, then added.
    let (mut lower, mut upper) = (vec![0.0; size + 1], vec![0.0; size + 1]);
    for (&l, &f) in left.iter().zip(&frac) {
        lower[l] += 1.0 - f;
        upper[l + 1] += f;
    }
    let counts: Vec<f64> = (0..size).map(|i| lower[i] + upper[i]).collect();
    let field = convolve_same(&counts, &kernel);
    Binned {
        field,
        start,
        left,
        frac,
        kernel,
    }
}

/// Eq. 5: the leave-one-out predictive log-likelihood of bandwidth g * h_S.
///
/// Read back off the binned field at each point, less that point's own
/// contribution through the same binning. That subtraction is exact in
/// principle, but for a date with no neighbour in reach it is two equal
/// numbers cancelling, so those dates are summed directly in log space.
pub fn log_likelihood(t: &[f64], h_s: f64, g: f64) -> f64 {
    let n = t.len();
    if n < 3 {
        // The power (n - 2)/n is zero: the likelihood is flat in g.
        return 0.0;
    }
    let h = g * h_s;
    let b = binned_field(t, h, h / BINS_PER_BANDWIDTH);
    let centre = b.kernel.len() / 2;
    let mut others = Vec::with_capacity(n);
    let mut own = Vec::with_capacity(n);
    for (&l, &f) in b.left.iter().zip(&b.frac) {
        let at_point = (1.0 - f) * b.field[l] + f * b.field[l + 1];
        let mine = ((1.0 - f) * (1.0 - f) + f * f) * b.kernel[centre]
            + 2.0 * f * (1.0 - f) * b.kernel[centre + 1];
        others.push(at_point - mine);
        own.push(mine);
    }
    let isolated: Vec<usize> = (0..n)
        .filter(|&i| others[i] <= CANCELLATION * own[i])
        .collect();
    if isolated.len() > MAX_ISOLATED {
        return f64::NEG_INFINITY;
    }
    let pairs = (n - 1) as f64;
    let mut logs: Vec<f64> = others
        .iter()
        .enumerate()
        .map(|(i, &o)| {
            if isolated.contains(&i) {
                (1.0 / pairs).ln()
            } else {
                (o / pairs).ln()
            }
        })
        .collect();
    if !isolated.is_empty() {
        let exact = exact_log_others(t, &isolated, h);
        for (k, &i) in isolated.iter().enumerate() {
            logs[i] = exact[k] - pairs.ln();
        }
    }
    (n - 2) as f64 / n as f64 * sum(&logs)
}

/// log sum_{j != i} K_h(t_i - t_j) for the points `which`, by log-sum-exp,
/// so a neighbour forty kernel widths away still counts.
fn exact_log_others(t: &[f64], which: &[usize], h: f64) -> Vec<f64> {
    which
        .iter()
        .map(|&i| {
            let exponent: Vec<f64> = t
                .iter()
                .enumerate()
                .map(|(j, &tj)| {
                    if j == i {
                        f64::NEG_INFINITY
                    } else {
                        let z = (t[i] - tj) / h;
                        -0.5 * (z * z)
                    }
                })
                .collect();
            let peak = exponent.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            let shifted: Vec<f64> = exponent.iter().map(|&e| (e - peak).exp()).collect();
            peak + sum(&shifted).ln() - (h * sqrt_two_pi()).ln()
        })
        .collect()
}

/// The g grid: nodes on (0, 1), the cell bounds each stands for, and L(g | t)
/// at each scaled so the best is 1.
#[derive(Debug, Clone, PartialEq)]
pub struct GGrid {
    pub nodes: Vec<f64>,
    pub bounds: Vec<f64>,
    pub weight: Vec<f64>,
}

fn midpoints(edges: &[f64]) -> Vec<f64> {
    edges.windows(2).map(|w| 0.5 * (w[0] + w[1])).collect()
}

/// The coarse pass finds where the likelihood is; the fine pass resolves it.
pub fn g_grid(t: &[f64], h_s: f64) -> GGrid {
    let edges = linspace(0.0, 1.0, G_COARSE + 1);
    let coarse = midpoints(&edges);
    let coarse_ll: Vec<f64> = coarse.iter().map(|&g| log_likelihood(t, h_s, g)).collect();
    let best = argmax(&coarse_ll).expect("a coarse grid");
    let lo = edges[best.saturating_sub(1)];
    let hi = edges[(best + 2).min(G_COARSE)];
    let fine = midpoints(&linspace(lo, hi, G_FINE + 1));
    let fine_ll: Vec<f64> = fine.iter().map(|&g| log_likelihood(t, h_s, g)).collect();

    // The fine pass replaces the coarse cells it covers.
    let mut nodes = Vec::new();
    let mut ll = Vec::new();
    for (&g, &l) in coarse.iter().zip(&coarse_ll) {
        if g < lo || g > hi {
            nodes.push(g);
            ll.push(l);
        }
    }
    nodes.extend(&fine);
    ll.extend(&fine_ll);
    let order = argsort_quicksort(&nodes);
    let nodes: Vec<f64> = order.iter().map(|&i| nodes[i]).collect();
    let ll: Vec<f64> = order.iter().map(|&i| ll[i]).collect();

    let mut bounds = vec![0.0];
    bounds.extend(midpoints(&nodes));
    bounds.push(1.0);
    let best_ll = ll
        .iter()
        .copied()
        .filter(|v| v.is_finite())
        .fold(f64::NEG_INFINITY, f64::max);
    let weight = ll
        .iter()
        .map(|&v| {
            if v.is_finite() {
                (v - best_ll).exp()
            } else {
                0.0
            }
        })
        .collect();
    GGrid {
        nodes,
        bounds,
        weight,
    }
}

/// One g from its conditional on t: a cell by L(g | t) times its width, then
/// a position uniformly within it.
pub fn draw_g(t: &[f64], h_s: f64, rng: &mut Generator) -> f64 {
    let grid = g_grid(t, h_s);
    let widths = diff(&grid.bounds);
    let mass: Vec<f64> = grid
        .weight
        .iter()
        .zip(&widths)
        .map(|(w, d)| w * d)
        .collect();
    let total = sum(&mass);
    if total.is_nan() || total <= 0.0 {
        // Every bandwidth predicts some date as impossible -- only reachable
        // with coincident points. Silverman's own width is the safe answer.
        return 1.0;
    }
    let p: Vec<f64> = mass.iter().map(|m| m / total).collect();
    let cell = rng.choice(&p);
    rng.uniform(grid.bounds[cell], grid.bounds[cell + 1])
}

/// The calendar ages a summary is drawn on, from every state's dates and the
/// bandwidths chosen for them.
pub fn output_grid(states: &[Vec<f64>], bandwidths: &[f64]) -> Vec<f64> {
    let widest = bandwidths.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let all = states.iter().flatten().copied();
    let lo = all.clone().fold(f64::INFINITY, f64::min) - 3.0 * widest;
    let hi = all.fold(f64::NEG_INFINITY, f64::max) + 3.0 * widest;
    let span = hi - lo;
    let step = py_max(
        py_max(1.0, py_min(span / 300.0, median(bandwidths) / 3.0)),
        span / MAX_POINTS,
    );
    let step = step.ceil();
    let start = (lo / step).floor() * step;
    crate::numeric::arange(start, hi + step, step)
}

/// Python's `max(a, b)`: `a` unless `b` is strictly greater.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

/// Python's `min(a, b)`: `a` unless `b` is strictly less.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

/// Eq. 1 on `ages`: the KDE of the points t at bandwidth h, binned at no more
/// than an eighth of h and read off by interpolation.
///
/// `bounds` (lo, hi), in cal BP, reflects the kernels at those ages and zeroes
/// the density outside them: inside a model the events cannot fall beyond
/// their Boundaries, so neither should the kernels drawn around them (Bronk
/// Ramsey 2017, eq. 8). Each point near an edge gains its mirror image there,
/// which keeps the curve flat up to the transition rather than rounding it
/// off, and keeps its integral at one.
pub fn kde_density(t: &[f64], h: f64, ages: &[f64], bounds: Option<(f64, f64)>) -> Vec<f64> {
    let mut points = t.to_vec();
    if let Some((lo, hi)) = bounds {
        let reach = KERNEL_REACH * h;
        points.extend(t.iter().filter(|&&v| v < lo + reach).map(|&v| 2.0 * lo - v));
        points.extend(t.iter().filter(|&&v| v > hi - reach).map(|&v| 2.0 * hi - v));
    }
    let step = if ages.len() > 1 {
        ages[1] - ages[0]
    } else {
        1.0
    };
    let delta = py_min(step, h / BINS_PER_BANDWIDTH);
    let b = binned_field(&points, h, delta);
    let positions: Vec<f64> = (0..b.field.len())
        .map(|i| b.start + delta * i as f64)
        .collect();
    let n = t.len() as f64;
    let mut density: Vec<f64> = interp_bounded(ages, &positions, &b.field, 0.0, 0.0)
        .into_iter()
        .map(|d| d / n)
        .collect();
    if let Some((lo, hi)) = bounds {
        for (d, &a) in density.iter_mut().zip(ages) {
            if a < lo || a > hi {
                *d = 0.0;
            }
        }
    }
    density
}

/// Median, mean and the 68% range (15.87th to 84.13th percentile).
#[derive(Debug, Clone, PartialEq)]
pub struct Spread {
    pub median: f64,
    pub mean: f64,
    pub range_68: [f64; 2],
}

pub fn spread(values: &[f64]) -> Spread {
    Spread {
        median: percentile(values, 50.0),
        mean: mean(values),
        range_68: [percentile(values, 15.87), percentile(values, 84.13)],
    }
}

/// A KDE_Plot: the mean density across draws and its ±1σ band, with the
/// bandwidths that produced it.
#[derive(Debug, Clone, PartialEq)]
pub struct Summary {
    pub dates: usize,
    pub draws: usize,
    pub reflected: bool,
    /// `[cal BP, mean density, ±1σ of the density across draws]`, ascending
    /// age, density per calendar year.
    pub distribution: Vec<[f64; 3]>,
    /// The bandwidth the data chose, in years.
    pub bandwidth: Spread,
    pub silverman: Spread,
    /// The factor on Silverman's ceiling the bandwidth was reached by. Near 1
    /// says Silverman alone would have done; well below 1, that the dates are
    /// clumpier than a normal.
    pub factor: Spread,
}

/// Independent draws from a discretised distribution by inverse CDF, for a
/// group calibration: its dates are independent by construction, so drawing
/// each separately IS drawing from the joint. Port of
/// `intervals.draws_from_distribution`.
pub fn draws_from_distribution(
    ages: &[f64],
    weights: &[f64],
    rng: &mut Generator,
    count: usize,
) -> Result<Vec<f64>, Error> {
    let total = sum(weights);
    if total <= 0.0 {
        return Err(Error::EmptyCalibration);
    }
    let cdf: Vec<f64> = cumsum(weights).into_iter().map(|c| c / total).collect();
    Ok(interp(&rng.random_n(count), &cdf, ages))
}

/// KDE_Plot of a set of independent dates, given each as (cal_ages,
/// probability masses) exactly as `CalibrationCurve::calibrate` returns them.
/// None for fewer than [`MIN_DATES`]: the option is inert rather than an
/// error. Ages are cal BP throughout.
pub fn summarise(
    calibrated: &[(&[f64], &[f64])],
    draws: usize,
    seed: u64,
) -> Result<Option<Summary>, Error> {
    if calibrated.len() < MIN_DATES {
        return Ok(None);
    }
    let mut rng = Generator::new(seed);
    let sampled = calibrated
        .iter()
        .map(|(ages, masses)| draws_from_distribution(ages, masses, &mut rng, draws))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(summarise_draws(&sampled, None, draws, seed))
}

/// KDE_Plot from joint draws already taken: `sampled[i][k]` is date i in
/// state k -- independent draws for unmodelled dates or the exact solver,
/// chain states for the sampler.
///
/// `bounds`, when given, is the (older, younger) Boundary draws in the same
/// states, and each state's kernels are reflected at its own Boundaries.
///
/// More states than `draws` are thinned evenly: each state contributes a
/// whole KDE, so a few hundred settle the mean.
pub fn summarise_draws(
    sampled: &[Vec<f64>],
    bounds: Option<(&[f64], &[f64])>,
    draws: usize,
    seed: u64,
) -> Option<Summary> {
    let n = sampled.len();
    let available = sampled.first().map_or(0, Vec::len);
    if n < MIN_DATES || available == 0 {
        return None;
    }
    let columns: Vec<usize> = if available > draws {
        linspace(0.0, (available - 1) as f64, draws)
            .into_iter()
            .map(|c| c.round_ties_even() as usize)
            .collect()
    } else {
        (0..available).collect()
    };
    // One state per entry: its dates, and its reflecting edges.
    let mut states: Vec<Vec<f64>> = columns
        .iter()
        .map(|&c| sampled.iter().map(|row| row[c]).collect())
        .collect();
    let mut edges: Option<Vec<(f64, f64)>> = bounds.map(|(older, younger)| {
        columns
            .iter()
            .map(|&c| (older[c].min(younger[c]), older[c].max(younger[c])))
            .collect()
    });
    let mut rng = Generator::new(seed);

    // A state whose dates all share one age has no Silverman bandwidth and so
    // no KDE. Such states are left out rather than the whole summary refused.
    // NumPy's std along the dates axis sums them in order, so the mean it
    // compares against is that sum over n.
    let spread_out: Vec<bool> = states
        .iter()
        .map(|s| {
            let mut total = 0.0;
            for &v in s {
                total += v;
            }
            let centre = total / n as f64;
            let mut squares = 0.0;
            for &v in s {
                squares += (v - centre) * (v - centre);
            }
            squares > 0.0
        })
        .collect();
    if !spread_out.iter().any(|&s| s) {
        return None;
    }
    let mut keep = spread_out.iter();
    states.retain(|_| *keep.next().unwrap());
    if let Some(e) = edges.as_mut() {
        let mut keep = spread_out.iter();
        e.retain(|_| *keep.next().unwrap());
    }
    let count = states.len();

    let mut silverman = Vec::with_capacity(count);
    let mut factor = Vec::with_capacity(count);
    for t in &states {
        let h_s = silverman_bandwidth(t);
        silverman.push(h_s);
        factor.push(draw_g(t, h_s, &mut rng));
    }
    let bandwidth: Vec<f64> = factor.iter().zip(&silverman).map(|(f, s)| f * s).collect();

    let ages = output_grid(&states, &bandwidth);
    let mut total = vec![0.0; ages.len()];
    let mut total_sq = vec![0.0; ages.len()];
    for (k, t) in states.iter().enumerate() {
        let density = kde_density(t, bandwidth[k], &ages, edges.as_ref().map(|e| e[k]));
        for (i, d) in density.into_iter().enumerate() {
            total[i] += d;
            total_sq[i] += d * d;
        }
    }
    let count_f = count as f64;
    let mean_density: Vec<f64> = total.iter().map(|t| t / count_f).collect();
    let sd: Vec<f64> = total_sq
        .iter()
        .zip(&mean_density)
        .map(|(sq, m)| (sq / count_f - m * m).max(0.0).sqrt())
        .collect();

    // Trimmed to where the curve or its band is visible, as a single date's
    // display distribution is: the padding is for the kernels' tails, not for
    // a plot of flat zero.
    let upper: Vec<f64> = mean_density.iter().zip(&sd).map(|(m, s)| m + s).collect();
    let floor = 1e-4 * upper.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let lo = upper.iter().position(|&u| u >= floor)?;
    let hi = upper.iter().rposition(|&u| u >= floor)? + 1;

    Some(Summary {
        dates: n,
        draws: count,
        reflected: edges.is_some(),
        distribution: (lo..hi)
            .map(|i| [ages[i], mean_density[i], sd[i]])
            .collect(),
        bandwidth: spread(&bandwidth),
        silverman: spread(&silverman),
        factor: spread(&factor),
    })
}
