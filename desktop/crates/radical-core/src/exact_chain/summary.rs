//! An exact density turned into what the rest of the app reports: point
//! estimates, HPD ranges at the reporting resolution, the plotted series and
//! draws.

use crate::Error;
use crate::calibration::{HpdRange, SIGMA_1, SIGMA_2, hpd_ranges_from_distribution};
use crate::exact_chain::{DISPLAY_THRESHOLD, PLOT_MARGIN_YEARS};
use crate::numeric::{arange, cumsum, interp, interp_one, py_max, py_min, sum};
use crate::rng::Generator;

/// One parameter's posterior, in the summary shape the API stores for a date
/// or a Boundary.
#[derive(Debug, Clone, PartialEq)]
pub struct Posterior {
    pub posterior_mean: f64,
    pub posterior_median: f64,
    pub posterior_std: f64,
    /// The envelope of `hpd_68_ranges`, (min, max).
    pub hpd_68: (f64, f64),
    pub hpd_95: (f64, f64),
    pub hpd_68_ranges: Vec<HpdRange>,
    pub hpd_95_ranges: Vec<HpdRange>,
    /// [cal BP, density], trimmed to where it has mass.
    pub probability_distribution: Vec<[f64; 2]>,
    /// Inverse-CDF draws from the marginal, for code that wants samples.
    pub posterior_samples: Vec<f64>,
    /// P(the date is an outlier | all the data), for a date with an outlier
    /// model.
    pub outlier_probability: Option<f64>,
    /// The date's agreement index, filled in by [`crate::agreement::attach`].
    pub agreement_index: Option<f64>,
}

/// For each cell t, the total weight of the boundary pairs that straddle it,
/// younger boundary at or below t and older at or above, from the weight
/// matrix's row sums (`on_younger`, by younger boundary) and column sums
/// (`on_older`, by older boundary) alone.
///
/// The matrix is zero below its diagonal -- an older boundary is never the
/// younger -- so a pair with its younger boundary at or below t straddles t
/// unless its older boundary is below t as well:
///
/// ```text
/// S(t) = sum_{y <= t} rows(y) - sum_{o < t} cols(o)
///      = sum_{o >= t} cols(o) - sum_{y > t} rows(y)
/// ```
///
/// Each form subtracts two partial sums, which are small on the young side for
/// the first and on the old side for the second, so each is used on its own
/// side and neither cancels two large numbers.
pub fn straddling(on_younger: &[f64], on_older: &[f64]) -> Vec<f64> {
    let n = on_younger.len();
    let rows_to = cumsum(on_younger);
    let cols_to = cumsum(on_older);
    let rev = |v: &[f64]| -> Vec<f64> {
        let mut r: Vec<f64> = v.iter().rev().copied().collect();
        r = cumsum(&r);
        r.reverse();
        r
    };
    let rows_from = rev(on_younger);
    let cols_from = rev(on_older);
    let half = 0.5 * rows_to[n - 1];
    (0..n)
        .map(|t| {
            if rows_to[t] <= half {
                rows_to[t] - cols_to[t] + on_older[t]
            } else {
                cols_from[t] - (rows_from[t] - on_younger[t])
            }
        })
        .collect()
}

/// P(this date is an outlier | all the data).
///
/// Given the date landed at t, its probability of being an outlier is the
/// responsibility of the outlier component there, p*L_out(t)/L_mix(t).
/// Averaging that over the date's own posterior marginalises t out:
///
/// ```text
/// P(outlier) = integral of posterior(t) * p*L_out(t)/L_mix(t) dt
/// ```
///
/// Both pieces are already computed, so this costs two array products.
pub fn outlier_probability(density: &[f64], mixture: &[f64], broad: &[f64], prior: f64) -> f64 {
    let weight = sum(density);
    if weight <= 0.0 {
        return 0.0;
    }
    let terms: Vec<f64> = density
        .iter()
        .zip(mixture.iter().zip(broad))
        .map(|(&d, (&mix, &b))| {
            let responsibility = if mix > 0.0 {
                prior * b / mix.max(1e-300)
            } else {
                0.0
            };
            d / weight * responsibility
        })
        .collect();
    sum(&terms)
}

/// The most probability any single distribution leaves in the outermost 1% of
/// the grid. A boundary's tail is genuinely heavy, so a non-trivial value means
/// the window clipped something real and should be widened.
pub fn edge_mass(densities: &[Vec<f64>], grid_len: usize) -> f64 {
    let edge = (grid_len / 100).max(1);
    let mut worst = 0.0;
    for dens in densities {
        let total = sum(dens);
        if total > 0.0 {
            let n = dens.len();
            let share = (sum(&dens[..edge]) + sum(&dens[n - edge..])) / total;
            worst = py_max(worst, share);
        }
    }
    worst
}

/// The cell edges a grid's points stand for, and the cumulative mass at each.
fn cells(grid: &[f64], mass: &[f64]) -> (Vec<f64>, Vec<f64>) {
    let dt = grid[1] - grid[0];
    let mut edges = Vec::with_capacity(grid.len() + 1);
    edges.push(grid[0] - dt / 2.0);
    edges.extend(grid.iter().map(|g| g + dt / 2.0));
    let mut cdf = Vec::with_capacity(mass.len() + 1);
    cdf.push(0.0);
    cdf.extend(cumsum(mass));
    (edges, cdf)
}

/// HPD ranges at `step` -- the reporting resolution, five years unless the
/// analysis asked for another -- rather than at the solver's internal grid
/// step.
///
/// Two reasons. The internal step is chosen from the date range and the number
/// of groups, so reporting at it would make a published range depend on an
/// implementation detail -- the same dates in a longer sequence would come back
/// with different widths. And a modelled posterior on a wiggly curve is
/// genuinely multi-peaked, so a fine grid picks out the peak tops and skips the
/// dips: at 1 year one fixture date's 68.3% region came back as three fragments
/// totalling 24 years where OxCal reports 35, and rebinned to 5 years it gives
/// 30.
///
/// Each grid point stands for the cell [t - dt/2, t + dt/2]; the mass in a
/// reporting bin is the cumulative mass between its edges, read off the cell
/// edges by interpolation. Exact for a density that is constant within a cell,
/// and -- unlike a histogram of the points -- unaffected by how many grid
/// points happen to fall in each bin, which for a 1.015 year step is four or
/// five and put a +/-20% sawtooth on the bin masses.
pub fn reported_ranges(grid: &[f64], mass: &[f64], confidence: f64, step: f64) -> Vec<HpdRange> {
    let (cell_edges, cdf) = cells(grid, mass);
    let lo = (cell_edges[0] / step).floor() * step;
    let hi = (cell_edges[cell_edges.len() - 1] / step).ceil() * step;
    let edges = arange(lo, hi + step, step);
    let at_edges = interp(&edges, &cell_edges, &cdf);
    let binned: Vec<f64> = at_edges
        .windows(2)
        .map(|w| (w[1] - w[0]).max(0.0))
        .collect();
    let centres: Vec<f64> = edges.windows(2).map(|w| (w[0] + w[1]) / 2.0).collect();
    let (kept_centres, kept): (Vec<f64>, Vec<f64>) = centres
        .iter()
        .zip(&binned)
        .filter(|(_, b)| **b > 0.0)
        .map(|(c, b)| (*c, *b))
        .unzip();
    if kept.is_empty() {
        return vec![HpdRange {
            min: grid[0],
            max: grid[grid.len() - 1],
            probability_percent: 100.0,
        }];
    }
    let mut ranges = hpd_ranges_from_distribution(&kept_centres, &kept, confidence, None)
        .expect("positive bins make a distribution");
    for r in &mut ranges {
        r.min -= step / 2.0;
        r.max += step / 2.0;
    }
    ranges
}

/// The density trimmed to where it actually has mass.
///
/// The solver's grid runs far wider than the dates because the boundaries need
/// room -- 2031 years on one fixture against a posterior occupying about 100 --
/// so handing the whole grid to the plot would draw every modelled date as a
/// spike in a mostly-empty axis.
pub fn plottable(grid: &[f64], density: &[f64]) -> Vec<[f64; 2]> {
    let peak = density.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    if peak <= 0.0 {
        return grid.iter().zip(density).map(|(&a, &p)| [a, p]).collect();
    }
    let threshold = peak * DISPLAY_THRESHOLD;
    let first = density
        .iter()
        .position(|&d| d >= threshold)
        .expect("the peak");
    let last = density
        .iter()
        .rposition(|&d| d >= threshold)
        .expect("the peak");
    let margin = ((PLOT_MARGIN_YEARS / (grid[1] - grid[0])).round_ties_even() as usize).max(1);
    let lo = first.saturating_sub(margin);
    let hi = (last + margin + 1).min(grid.len());
    (lo..hi).map(|i| [grid[i], density[i]]).collect()
}

fn envelope(ranges: &[HpdRange]) -> (f64, f64) {
    let lo = ranges.iter().map(|r| r.min).reduce(py_min).expect("ranges");
    let hi = ranges.iter().map(|r| r.max).reduce(py_max).expect("ranges");
    (lo, hi)
}

/// An exact density in the summary shape the rest of the app expects. Point
/// estimates come from the density itself rather than from draws, so they
/// carry no Monte Carlo error; the draws exist only for downstream code that
/// wants samples. `step` is the calendar step the HPD ranges are reported at.
pub fn summarize(
    grid: &[f64],
    density: &[f64],
    rng: &mut Generator,
    num_samples: usize,
    step: f64,
) -> Result<Posterior, Error> {
    let dt = grid[1] - grid[0];
    let total = sum(density) * dt;
    if total <= 0.0 {
        return Err(Error::Model(
            "a posterior came out empty; the model may be contradictory".into(),
        ));
    }
    let density: Vec<f64> = density.iter().map(|d| d / total).collect();
    let mass: Vec<f64> = density.iter().map(|d| d * dt).collect();

    let weighted: Vec<f64> = grid.iter().zip(&mass).map(|(g, m)| g * m).collect();
    let mean = sum(&weighted);
    let spread: Vec<f64> = grid
        .iter()
        .zip(&mass)
        .map(|(g, m)| {
            let d = g - mean;
            d * d * m
        })
        .collect();
    let variance = sum(&spread);
    // Each grid point stands for the cell [t - dt/2, t + dt/2], so the
    // cumulative mass THROUGH a cell is reached at its far edge, not its
    // centre. Read against the centres, the median and every draw came out
    // half a cell young: 22.5 for a density symmetric about 25 on a five-year
    // grid.
    let (cell_edges, cdf) = cells(grid, &mass);
    let median = interp_one(0.5, &cdf, &cell_edges);

    let hpd_68 = reported_ranges(grid, &mass, SIGMA_1, step);
    let hpd_95 = reported_ranges(grid, &mass, SIGMA_2, step);
    let draws = interp(&rng.random_n(num_samples), &cdf, &cell_edges);

    Ok(Posterior {
        posterior_mean: mean,
        posterior_median: median,
        posterior_std: py_max(variance, 0.0).sqrt(),
        hpd_68: envelope(&hpd_68),
        hpd_95: envelope(&hpd_95),
        hpd_68_ranges: hpd_68,
        hpd_95_ranges: hpd_95,
        probability_distribution: plottable(grid, &density),
        posterior_samples: draws,
        outlier_probability: None,
        agreement_index: None,
    })
}
