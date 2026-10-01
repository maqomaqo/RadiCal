"""
A kernel density summary of a set of dates: OxCal's KDE_Plot (Bronk Ramsey
2017, "Methods for summarizing radiocarbon datasets", Radiocarbon 59(6)).

What it answers is the question a Sum is usually asked -- how the dated
events are spread through time -- without the Sum's spikes and cliffs, which
are mostly the calibration curve's wiggles, not anything about the events.
Each draw takes ONE calendar age per date and builds an ordinary Gaussian KDE
from those n points (eq. 1); the plotted curve is the mean of those KDEs over
many draws, and the band around it their standard deviation, as OxCal's ±1σ
band is. Pooling every draw of every date under one kernel would not be
this: it is a smoothed Sum, still carrying each date's whole calibrated width.

The bandwidth is OxCal's. Silverman's rule (eq. 2),

    h_S(t) = (4/3)^(1/5) * sd(t) * n^(-1/5)

is optimal for a normal underlying distribution and over-smooths anything
multimodal, so it is treated as a CEILING: h = g * h_S with g ~ U(0, 1)
(eqs. 3-4), and g is weighted by how well each date is predicted by a KDE of
all the others (eq. 5),

    L(g | t) ∝ prod_i [ sum_{j != i} K((t_i - t_j) / (g h_S)) / ((n-1) g h_S) ] ^ ((n-2)/n)

The power is (n-2)/n because a mean and a standard deviation have already
been taken from the n points; at n = 2 it is zero and g stays uniform, there
being nothing to say about multimodality in two points.

Where this differs from OxCal is only in how it gets there. OxCal moves g by
Metropolis-Hastings inside its MCMC. Here each joint state of t is taken as
given -- an exact inverse-CDF draw for unmodelled dates, as a group
calibration's differences are drawn, or a solver's joint draw inside a model
-- and g is drawn from its conditional on that t directly, from L(g | t)
evaluated on a grid. Same target, and no chain for g to converge. Not yet
pinned against an OxCal KDE_Plot export: do that before quoting agreement
with OxCal.

Inside a model (report_phases) the dates are a solver's joint draws, and
there is one KDE per Phase, as OxCal's KDE_Plot used in place of Phase().
The kernels are then reflected at the Boundaries bracketing the phase in each
state (eq. 8): the events cannot fall outside them, so neither should the
density, and reflecting keeps it flat up to the transition rather than
rounding it off. Eq. 5 is left as the paper states it, on plain kernels.

Pure numpy. Every kernel sum is a linearly binned histogram convolved with the
kernel at an eighth of its width, so the cost of a draw grows with the span of
the dates, not with n squared: a regional dataset of thousands of dates is the
case this summary exists for.
"""
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.services.intervals import draws_from_distribution, plan_durations

# Draws of the whole dataset. Each contributes a complete, smooth KDE rather
# than one point, so the mean settles far sooner than a histogram of draws
# would; this is enough for the ±1σ band to be stable to a few percent.
DEFAULT_DRAWS = 500

# The fewest dates a KDE is offered for. Two have a standard deviation and so
# a Silverman bandwidth; one has neither.
MIN_DATES = 2

# Silverman's constant for a normal kernel, (4/3)^(1/5) ~= 1.06.
SILVERMAN = (4.0 / 3.0) ** 0.2

# How many kernel widths a Gaussian is carried out to. At 6 sigma it is
# e^-18 of its peak, below anything a plotted density or a likelihood ratio
# can register.
KERNEL_REACH = 6.0

# Bins per kernel width in every binned kernel sum. Linear binning's error
# falls as (bin / h)^2: at an eighth of a width it is about 0.1% of the
# density, and the same fraction of every term in eq. 5, so it barely moves
# the likelihood's shape in g.
BINS_PER_BANDWIDTH = 8.0

# Below this fraction of a point's own kernel peak, what is left after taking
# the point out of the binned sum is rounding error, not the other points'
# contribution. Those few points are summed exactly instead.
CANCELLATION = 1e-9

# ...unless there are more of them than this, when the bandwidth leaves that
# many dates with no neighbour in reach. Eq. 5 is then lower than at any
# bandwidth wide enough to reach them by more than the exact figure could
# change, so -inf is the right weight and the O(n) sum per point is not paid.
MAX_ISOLATED = 200

# The g grid. Coarse midpoints across (0, 1), then a finer pass across the
# cells either side of the best one: for large n the likelihood is sharply
# peaked and a uniform grid alone would land one node on it or none.
G_COARSE = 32
G_FINE = 16

# The output curve: never coarser than a third of the median bandwidth (so
# the narrowest real feature is drawn), never finer than a year, and never
# more points than this.
MAX_POINTS = 1500


def silverman_bandwidth(t: np.ndarray) -> float:
    """Eq. 2: the Silverman bandwidth for n points, the ceiling on h."""
    n = t.size
    return SILVERMAN * float(np.std(t, ddof=1)) * n ** -0.2


def _kernel(h: float, delta: float) -> np.ndarray:
    """A normalised N(0, h) sampled every `delta` years, centred."""
    half = max(1, int(np.ceil(KERNEL_REACH * h / delta)))
    offsets = np.arange(-half, half + 1) * delta
    return np.exp(-0.5 * (offsets / h) ** 2) / (h * np.sqrt(2.0 * np.pi))


def _binned_field(t: np.ndarray, h: float, delta: float
                  ) -> Tuple[np.ndarray, float, np.ndarray, np.ndarray, np.ndarray]:
    """
    sum_i K_h(x - t_i) on a grid of spacing `delta`, by linear binning and a
    direct convolution.

    Returns the field, the grid's first position, and for each point its left
    bin, its fractional offset into that bin and the kernel itself -- what a
    caller needs to read the field back AT the points (the leave-one-out sum)
    and to take each point's own contribution out again.
    """
    kernel = _kernel(h, delta)
    half = kernel.size // 2
    lo = float(t.min()) - (half + 1) * delta
    pos = (t - lo) / delta
    left = np.floor(pos).astype(int)
    frac = pos - left
    size = max(int(np.ceil((float(t.max()) - lo) / delta)) + half + 3, kernel.size)
    counts = (np.bincount(left, 1.0 - frac, minlength=size)
              + np.bincount(left + 1, frac, minlength=size))[:size]
    field = np.convolve(counts, kernel, mode='same')
    return field, lo, left, frac, kernel


def log_likelihood(t: np.ndarray, h_s: float, g: float) -> float:
    """
    Eq. 5: the leave-one-out predictive log-likelihood of bandwidth g * h_S.

    Read back off the binned field at each point, less that point's own
    contribution through the same binning. That subtraction is exact in
    principle, but for a date with no neighbour in reach it is two equal
    numbers cancelling, so those dates are summed directly in log space.
    """
    n = t.size
    if n < 3:
        # The power (n - 2)/n is zero: the likelihood is flat in g.
        return 0.0
    h = g * h_s
    field, _, left, frac, kernel = _binned_field(t, h, h / BINS_PER_BANDWIDTH)
    centre = kernel.size // 2
    at_points = (1.0 - frac) * field[left] + frac * field[left + 1]
    own = ((1.0 - frac) ** 2 + frac ** 2) * kernel[centre] \
        + 2.0 * frac * (1.0 - frac) * kernel[centre + 1]
    others = at_points - own
    isolated = np.nonzero(others <= CANCELLATION * own)[0]
    if isolated.size > MAX_ISOLATED:
        return float('-inf')
    others[isolated] = 1.0
    logs = np.log(others / (n - 1))
    if isolated.size:
        logs[isolated] = _exact_log_others(t, isolated, h) - np.log(n - 1)
    return float((n - 2) / n * logs.sum())


def _exact_log_others(t: np.ndarray, which: np.ndarray, h: float) -> np.ndarray:
    """log sum_{j != i} K_h(t_i - t_j) for the points `which`, by
    log-sum-exp, so a neighbour forty kernel widths away still counts."""
    exponent = -0.5 * ((t[which, None] - t[None, :]) / h) ** 2
    exponent[np.arange(which.size), which] = -np.inf
    peak = exponent.max(axis=1)
    summed = np.log(np.exp(exponent - peak[:, None]).sum(axis=1))
    return peak + summed - np.log(h * np.sqrt(2.0 * np.pi))


def _g_grid(t: np.ndarray, h_s: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Nodes on (0, 1), the cell each stands for, and L(g | t) at each, scaled so
    the best is 1. The coarse pass finds where the likelihood is; the fine pass
    resolves it.
    """
    edges = np.linspace(0.0, 1.0, G_COARSE + 1)
    coarse = 0.5 * (edges[:-1] + edges[1:])
    coarse_ll = np.array([log_likelihood(t, h_s, g) for g in coarse])
    best = int(np.argmax(coarse_ll))
    lo, hi = edges[max(best - 1, 0)], edges[min(best + 2, G_COARSE)]
    fine_edges = np.linspace(lo, hi, G_FINE + 1)
    fine = 0.5 * (fine_edges[:-1] + fine_edges[1:])
    fine_ll = np.array([log_likelihood(t, h_s, g) for g in fine])

    # The fine pass replaces the coarse cells it covers.
    keep = (coarse < lo) | (coarse > hi)
    nodes = np.concatenate([coarse[keep], fine])
    ll = np.concatenate([coarse_ll[keep], fine_ll])
    order = np.argsort(nodes)
    nodes, ll = nodes[order], ll[order]
    bounds = np.concatenate([[0.0], 0.5 * (nodes[:-1] + nodes[1:]), [1.0]])
    finite = np.isfinite(ll)
    weight = np.zeros_like(ll)
    if finite.any():
        weight[finite] = np.exp(ll[finite] - ll[finite].max())
    return nodes, bounds, weight


def draw_g(t: np.ndarray, h_s: float, rng: np.random.Generator) -> float:
    """One g from its conditional on t: a cell by L(g | t) times its width,
    then a position uniformly within it."""
    _, bounds, weight = _g_grid(t, h_s)
    mass = weight * np.diff(bounds)
    total = mass.sum()
    if not total > 0:
        # Every bandwidth predicts some date as impossible -- only reachable
        # with coincident points. Silverman's own width is the safe answer.
        return 1.0
    cell = rng.choice(mass.size, p=mass / total)
    return float(rng.uniform(bounds[cell], bounds[cell + 1]))


def _output_grid(draws: np.ndarray, bandwidths: np.ndarray) -> np.ndarray:
    widest = float(bandwidths.max())
    lo = float(draws.min()) - 3.0 * widest
    hi = float(draws.max()) + 3.0 * widest
    span = hi - lo
    step = max(1.0, min(span / 300.0, float(np.median(bandwidths)) / 3.0), span / MAX_POINTS)
    step = float(np.ceil(step))
    start = np.floor(lo / step) * step
    return np.arange(start, hi + step, step)


def kde_density(t: np.ndarray, h: float, ages: np.ndarray,
                bounds: Optional[Tuple[float, float]] = None) -> np.ndarray:
    """
    Eq. 1 on `ages`: the KDE of the points t at bandwidth h, binned at no
    more than an eighth of h and read off by interpolation.

    `bounds` (lo, hi), in cal BP, reflects the kernels at those ages and
    zeroes the density outside them: inside a model the events cannot fall
    beyond their Boundaries, so neither should the kernels drawn around them
    (Bronk Ramsey 2017, eq. 8). Each point near an edge gains its mirror image
    there, which keeps the curve flat up to the transition rather than rounding
    it off, and keeps its integral at one.
    """
    points = t
    if bounds is not None:
        lo, hi = bounds
        reach = KERNEL_REACH * h
        points = np.concatenate([
            t, 2.0 * lo - t[t < lo + reach], 2.0 * hi - t[t > hi - reach]])
    step = float(ages[1] - ages[0]) if ages.size > 1 else 1.0
    delta = min(step, h / BINS_PER_BANDWIDTH)
    field, start, _, _, _ = _binned_field(points, h, delta)
    positions = start + delta * np.arange(field.size)
    density = np.interp(ages, positions, field, left=0.0, right=0.0) / t.size
    if bounds is not None:
        density[(ages < bounds[0]) | (ages > bounds[1])] = 0.0
    return density


def summarise(calibrated: Sequence[Tuple[np.ndarray, np.ndarray]],
              draws: int = DEFAULT_DRAWS, seed: int = 42) -> Optional[Dict]:
    """
    KDE_Plot of a set of independent dates, given each as (cal_ages,
    probability masses) exactly as CalibrationCurve.calibrate returns them.

    None for fewer than MIN_DATES, as a group calibration's differences are
    None for one date: the option is inert rather than an error.

    Ages are cal BP throughout, as every distribution the app stores is.
    """
    if len(calibrated) < MIN_DATES:
        return None
    rng = np.random.default_rng(seed)
    # (n_dates, draws): row i is every draw of date i, so column k is one
    # joint draw of the whole set.
    sampled = np.stack([draws_from_distribution(ages, masses, rng, draws)
                        for ages, masses in calibrated])
    return summarise_draws(sampled, draws=draws, seed=seed)


def summarise_draws(sampled: np.ndarray,
                    bounds: Optional[Tuple[np.ndarray, np.ndarray]] = None,
                    draws: int = DEFAULT_DRAWS, seed: int = 42) -> Optional[Dict]:
    """
    KDE_Plot from joint draws already taken: `sampled` is (n_dates, D), each
    column one joint state of the dates -- independent draws for unmodelled
    dates or the exact solver, chain states for the sampler.

    `bounds`, when given, is the (older, younger) Boundary draws in the same
    D states, and each state's kernels are reflected at its own Boundaries.

    More states than `draws` are thinned evenly: each state contributes a
    whole KDE, so a few hundred settle the mean, and the solvers keep tens of
    thousands for reasons of their own.
    """
    n, available = sampled.shape
    if n < MIN_DATES or available == 0:
        return None
    columns = (np.linspace(0, available - 1, draws).round().astype(int)
               if available > draws else np.arange(available))
    sampled = sampled[:, columns]
    count = columns.size
    edges = None
    if bounds is not None:
        older, younger = (np.asarray(b, dtype=float)[columns] for b in bounds)
        edges = np.stack([np.minimum(older, younger), np.maximum(older, younger)], axis=1)
    rng = np.random.default_rng(seed)

    # A state whose dates all share one age has no Silverman bandwidth and so
    # no KDE. Only reachable from gridded draws that were not spread across
    # their cells, and then rarely; such states are left out rather than the
    # whole summary refused.
    spread = sampled.std(axis=0) > 0
    if not spread.any():
        return None
    sampled = sampled[:, spread]
    if edges is not None:
        edges = edges[spread]
    count = int(spread.sum())

    silverman = np.empty(count)
    factor = np.empty(count)
    for k in range(count):
        t = sampled[:, k]
        silverman[k] = silverman_bandwidth(t)
        factor[k] = draw_g(t, silverman[k], rng)
    bandwidth = factor * silverman

    ages = _output_grid(sampled, bandwidth)
    total = np.zeros_like(ages)
    total_sq = np.zeros_like(ages)
    for k in range(count):
        density = kde_density(sampled[:, k], bandwidth[k], ages,
                              None if edges is None else tuple(edges[k]))
        total += density
        total_sq += density ** 2
    mean = total / count
    sd = np.sqrt(np.maximum(total_sq / count - mean ** 2, 0.0))

    # Trimmed to where the curve or its band is visible, as a single date's
    # display distribution is: the padding above is for the kernels' tails,
    # not for a plot of flat zero.
    visible = np.nonzero(mean + sd >= 1e-4 * float((mean + sd).max()))[0]
    lo, hi = visible[0], visible[-1] + 1

    return {
        'method': 'kde_plot',
        'dates': n,
        'draws': count,
        'reflected': edges is not None,
        # [cal BP, mean density, ±1σ of the density across draws], ascending
        # age, density per calendar year.
        'distribution': [[float(a), float(m), float(s)]
                         for a, m, s in zip(ages[lo:hi], mean[lo:hi], sd[lo:hi])],
        # The bandwidth the data chose, in years, and the factor on Silverman's
        # ceiling it was reached by. A factor near 1 says Silverman alone would
        # have done; well below 1, that the dates are clumpier than a normal.
        'bandwidth': _summary(bandwidth),
        'silverman': _summary(silverman),
        'factor': _summary(factor),
    }


def report_phases(root: Optional[Dict], measurements: List[Dict],
                  draws: Dict[str, np.ndarray], grid_step: float = 0.0,
                  seed: int = 42) -> List[Dict]:
    """
    One KDE_Plot per Phase holding two or more dates, from a solver's joint
    draws -- OxCal's KDE_Plot used in place of Phase(). The phases are the
    ones services/intervals.py reports a dated-events duration for, so the two
    sections name the same groups, and each KDE is reflected at the Boundary
    pair that innermost brackets its phase, when there is one.

    `draws` maps date and Boundary ids to their values in the same joint
    states. A phase whose dates did not all come back with draws -- none
    should -- is left out rather than summarised from part of it.

    `grid_step` is the solver's calendar grid. Its draws are grid points, so
    three dates in a short phase often share one in some state, and a state
    with no spread has no Silverman bandwidth. A grid point stands for its
    whole cell, so each draw is spread uniformly across it -- the continuous
    posterior the grid discretises. Jittering a date and its Boundary
    independently can put the date part of a cell outside it, so each state's
    Boundaries are widened to take in its dates. Squeezing the dates in
    instead would stack a phase that fits in one cell onto one age.
    """
    rng = np.random.default_rng(seed)

    def continuous(values) -> np.ndarray:
        values = np.asarray(values, dtype=float)
        if grid_step > 0:
            values = values + rng.uniform(-0.5, 0.5, values.size) * grid_step
        return values

    records = []
    for spec in plan_durations(root, measurements):
        if spec['kind'] != 'sample_duration':
            continue
        if not all(date_id in draws for date_id in spec['dates']):
            continue
        sampled = np.stack([continuous(draws[d]) for d in spec['dates']])
        pair = spec['boundaries']
        bounds = None
        if pair and all(b in draws for b in pair):
            older, younger = continuous(draws[pair[0]]), continuous(draws[pair[1]])
            bounds = (np.maximum(np.maximum(older, younger), sampled.max(axis=0)),
                      np.minimum(np.minimum(older, younger), sampled.min(axis=0)))
        summary = summarise_draws(sampled, bounds)
        if summary:
            records.append({'element_id': spec['element_id'], 'name': spec['name'],
                            **summary})
    return records


def _summary(values: np.ndarray) -> Dict[str, float]:
    low, median, high = np.percentile(values, [15.87, 50.0, 84.13])
    return {'median': float(median), 'mean': float(values.mean()),
            'range_68': [float(low), float(high)]}
