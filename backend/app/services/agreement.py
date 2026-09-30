"""
Agreement indices -- how well each modelled date still fits the measurement
it came from, and how well the model as a whole does.

A Bayesian model moves every date, which means one that does not belong --
residual charcoal, an intrusive fragment, a lab error -- gets dragged into line
with its neighbours and reported with a confident range. The agreement index is
the standard warning (Bronk Ramsey 1995): it compares each date's MODELLED
posterior against its own likelihood, so a date the model had to move a long
way scores low however tidy its final range looks.

The definition
--------------
For one date with likelihood L (a density in calendar years) and posterior p:

    A_i = 100 * integral(p * L) / integral(L^2)
        = 100 * E_posterior[ l(t_i) ],     l = L / integral(L^2)

the posterior expectation of the date's NORMALISED likelihood l. A date the
model leaves alone scores 100; one pushed off its likelihood scores low; and a
date the model SHARPENS onto the peak of its own likelihood scores above 100,
which is why values over 100 are normal and not a bug. Below about 60 is the
conventional threshold for taking a second look.

Amodel is the same expectation taken over the JOINT posterior, every date at
once, with the usual 1/sqrt(n) exponent:

    A_model = 100 * ( E_posterior[ product of l_i(t_i) ] ) ^ (1/sqrt(n))

At n = 1 that IS A_i, and with a model that leaves the dates independent it
returns A_overall; what separates the two is the correlation the model's
structure puts between the dates -- which is why Amodel drops below Aoverall
exactly when constraints are doing work. It is not a function of the per-date
indices and cannot be reached from them.

A_overall combines the per-date indices as if they were independent:

    A_overall = 100 * (product of A_i/100) ^ (1/sqrt(n))

Both are EXPECTATIONS, and are computed as such: the likelihood is evaluated
exactly at each posterior point -- a joint draw, or a node of a tabulated
posterior -- and only integral(L^2), a one-dimensional integral of a known
function, is done on a grid, at COMPARISON_STEP_YEARS. The index is a ratio of
integrals and the right value is the one a finer grid converges to; OxCal's
own index moves with its calculation resolution (Nenthorn's Aoverall is 100.4
at its default five years, 105.4 at one), and at one year this module
reproduces it: per-date rms 0.23-0.30, Aoverall 105.5 against 105.4
(tests/fixtures/oxcal_resolution). Against OxCal at its default five years it
reads about 0.7 per date higher, which is OxCal's coarser approximation.

Outlier models
--------------
A date under an outlier model is compared against the likelihood term that
model puts in Bayes' theorem, AS THE MODEL IS SPECIFIED: the measurement fits
either the date itself or the date moved by a shift delta, the second with the
outlier PRIOR p,

    L(t, delta) = (1 - p) * L(t) + p * L(t + delta),   delta ~ k_s

with k_s the model's offset kernel at scale s (Student-t for General, a
one-sided exponential for Charcoal). delta is a parameter of the model, with a
posterior of its own. The solvers integrate it out -- L becomes
(1 - p) * L + p * (L smeared by k_s) -- but that is a way of computing, and a
diagnostic should not change with how the posterior was computed. So the
expectation runs over delta as well, which has a closed form given t and s:

    E[l] = [ (1 - p) L(t) + p ((1 - q) B(t) + q D(t) / B(t)) ] / Q
    B = L * k_s,  D = L^2 * k_s  (convolutions),  q = p B / ((1 - p) L + p B)
    Q = ((1 - p)^2 + p^2) integral(L^2) + 2 p (1 - p) integral(L B)

q is the chance the state is the outlier branch. In that branch delta sits
wherever puts t + delta back on the likelihood, so the branch always fits --
but it carries weight p, fixed, so a date explained ONLY as an outlier scores
about 100 p / (1 - p)^2, near 5 at the usual 0.05. Weighting by the posterior
outlier probability instead would let the model rescue a moved date after the
fact and make the index confirm itself.

This is also what OxCal does. Scored off RadiCal's own run, Shag River Mouth's
NZ7756 -- which both programs call an outlier with 99.9% -- comes out 5.2
against OxCal's 5.4, where integrating delta out gives 0.5 and takes the
model's Aoverall to 48.9 against OxCal's 84.2; with delta it is 84.9.

The scale s is shared by every date under the same outlier model and has its
own posterior. services/gibbs.py samples it, and each joint draw carries its
own s, so the expectation is over (t_i, s) together. services/exact_chain.py
instead integrates s out per date against its prior -- a different model, see
gibbs.Model._build_outlier_tables -- and a date it solved is scored with the
kernel marginalised the same way, which is the term its own posterior was built
from.

An outlier model also adds a term to n, in both overall indices. OxCal gives
each Outlier_Model a scale parameter of its own -- the U(0,4) under General --
and lists it in the results table with A = 100, which it then counts: n is the
dates PLUS one per outlier model in use. The term contributes nothing to the
product but widens the 1/sqrt(n) exponent. Fed OxCal's own per-date indices
the rule reproduces its Aoverall on all six OxCal runs we hold to within 0.1.

What it does NOT do
-------------------
It detects a date the MODEL HAD TO MOVE, which is not the same as a date that
does not belong. On a single unconstrained phase, a date 600 radiocarbon years
from its four neighbours scores 100.5, because the phase simply widens and
nothing is forced anywhere; put the same date where a constraint bites and it
collapses to zero. This is a diagnostic for conflict between the data and the
model's STRUCTURE -- finding a date that is merely wrong needs an outlier model
instead.

The element's TPQ/TAQ bounds are deliberately not applied to the likelihood,
though the solvers' own likelihood tables carry them: a bound is part of the
model, and judging a date against a likelihood already truncated by it would
hide exactly the conflict with the bound this exists to show.
"""
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from app.services.calibration import get_curve
from app.services.exact_chain import (
    DEFAULT_OUTLIER_MODEL, OUTLIER_KERNEL_HALF_WIDTH_YEARS,
    _outlier_kernel, _outlier_kernel_at_scale, convolve_on_grid,
)

# Step integral(L^2) and the outlier component are tabulated at. Calibration
# curves are annual at their finest, so a likelihood has structure at one year
# and a coarser grid mis-states the integral; see the module docstring.
COMPARISON_STEP_YEARS = 1.0
# Likelihood below this fraction of its peak is outside the date's support.
SUPPORT_THRESHOLD = 1e-6
# How far either side of everything being compared an outlier component is
# tabulated. Its tails carry mass for thousands of years at the widest scales,
# but at a density that adds nothing measurable to integral(L^2) -- the prior
# squared times a density squared.
OUTLIER_MARGIN_YEARS = 500.0
# Conventional threshold for a date worth re-examining (Bronk Ramsey 1995).
POOR_AGREEMENT_BELOW = 60.0
# Floor applied before taking logs in the overall index. A date pushed clean
# off its likelihood scores a true zero, and dropping those would remove
# precisely the worst dates and let a broken model report a healthy overall.
MINIMUM_FOR_OVERALL = 0.01
# Normalised likelihood given to a posterior point the date's own likelihood
# puts no mass at, where a true zero would take the joint product to -inf and
# lose every other draw with it.
_UNSUPPORTED = 1e-300


def unmodelled_likelihood(measurement: Dict, grid: np.ndarray) -> np.ndarray:
    """
    The date's own calibration likelihood at `grid` -- what it would say with
    no model at all. Unnormalised; any array of calendar ages will do.
    """
    curve = get_curve(measurement.get('curve_id') or 'intcal20')
    delta_r = measurement.get('delta_r') or 0.0
    delta_r_error = measurement.get('delta_r_error') or 0.0
    grid = np.asarray(grid, dtype=float)
    c14, curve_error = curve.interpolate(grid)
    sigma2 = curve_error ** 2 + measurement['error_1sigma'] ** 2 + delta_r_error ** 2
    lik = np.exp(-0.5 * (c14 - (measurement['age_bp'] - delta_r)) ** 2 / sigma2) / np.sqrt(sigma2)
    # Nothing beyond the curve's ends; interpolate() clamps rather than
    # extrapolates there (see exact_chain._date_likelihood).
    return np.where((grid >= curve.cal_ages[0]) & (grid <= curve.cal_ages[-1]), lik, 0.0)


def _outlier_prior(measurement: Dict) -> float:
    return float(measurement.get('outlier_prior') or 0.0)


def _outlier_model(measurement: Dict) -> str:
    return measurement.get('outlier_model') or DEFAULT_OUTLIER_MODEL


@lru_cache(maxsize=128)
def _kernel(model: str, log_scale: Optional[float]) -> np.ndarray:
    """
    The offset kernel at one scale, or marginalised over the scale prior when
    `log_scale` is None -- the same kernels the solvers build their likelihood
    tables from, so the comparison is against the term they actually used.
    """
    if log_scale is None:
        return _outlier_kernel(COMPARISON_STEP_YEARS, OUTLIER_KERNEL_HALF_WIDTH_YEARS, model)
    return _outlier_kernel_at_scale(COMPARISON_STEP_YEARS, OUTLIER_KERNEL_HALF_WIDTH_YEARS,
                                    model, float(log_scale))


def _support(measurement: Dict) -> Tuple[float, float]:
    """Where the date's likelihood has any meaningful mass, in cal BP."""
    curve = get_curve(measurement.get('curve_id') or 'intcal20')
    cal, prob = curve.calibrate(
        measurement['age_bp'], measurement['error_1sigma'],
        measurement.get('delta_r'), measurement.get('delta_r_error'),
    )
    significant = np.where(prob >= prob.max() * SUPPORT_THRESHOLD)[0]
    if not len(significant):
        return float(cal[0]), float(cal[-1])
    return float(cal[significant[0]]), float(cal[significant[-1]])


class _Normalised:
    """
    One date's normalised likelihood l = L / integral(L^2), ready to evaluate
    at posterior points -- per outlier scale where the date carries a model
    whose scale was sampled.

    The calibration part of L is evaluated EXACTLY at each point; only the
    outlier component, a convolution, is read off a table, and it is smooth.
    """

    def __init__(self, measurement: Dict, points: np.ndarray,
                 unmodelled: Optional[np.ndarray] = None):
        self.measurement = measurement
        self.prior = 0.0 if unmodelled is not None else _outlier_prior(measurement)
        self.supplied = unmodelled
        if unmodelled is not None:
            lo, hi = float(unmodelled[:, 0].min()), float(unmodelled[:, 0].max())
        else:
            lo, hi = _support(measurement)
        pad = OUTLIER_MARGIN_YEARS if self.prior > 0 else 0.0
        lo = min(lo, float(points.min())) - pad
        hi = max(hi, float(points.max())) + pad
        step = COMPARISON_STEP_YEARS
        start = np.floor(lo / step) * step
        self.grid = start + step * np.arange(int(np.ceil((hi - start) / step)) + 1)
        calibration = self._calibration(self.grid)
        # Normalised to a DENSITY, not to unit sum over the grid: the index is a
        # ratio of integrals, and the outlier component below is a density too.
        self.mass = float(calibration.sum() * step)
        self.calibration = calibration / self.mass if self.mass > 0 else calibration
        self._by_scale: Dict[Optional[float], Tuple[np.ndarray, float]] = {}

    def _calibration(self, t: np.ndarray) -> np.ndarray:
        if self.supplied is not None:
            return np.interp(t, self.supplied[:, 0], self.supplied[:, 1], left=0.0, right=0.0)
        return unmodelled_likelihood(self.measurement, t)

    def _component(self, log_scale: Optional[float]) -> Tuple[np.ndarray, np.ndarray, float]:
        """
        (B, D, Q) at one scale: the calibration and its square smeared by the
        offset kernel, and the normalising integral -- see the module docstring.
        """
        if log_scale not in self._by_scale:
            step = COMPARISON_STEP_YEARS
            square = float((self.calibration ** 2).sum() * step)
            p = self.prior
            if p > 0:
                kernel = _kernel(_outlier_model(self.measurement), log_scale)
                broad = convolve_on_grid(self.calibration, kernel)
                smeared_square = convolve_on_grid(self.calibration ** 2, kernel)
                cross = float((self.calibration * broad).sum() * step)
                total = ((1.0 - p) ** 2 + p ** 2) * square + 2.0 * p * (1.0 - p) * cross
            else:
                broad = smeared_square = None
                total = square
            self._by_scale[log_scale] = (broad, smeared_square, total)
        return self._by_scale[log_scale]

    @property
    def usable(self) -> bool:
        return self.mass > 0

    def at(self, t: np.ndarray, log_scale: Optional[float] = None) -> np.ndarray:
        """
        The normalised likelihood's expectation at the calendar ages `t`, under
        one outlier scale -- over the outlier shift as well, where there is one.
        """
        broad, smeared_square, total = self._component(log_scale)
        if total <= 0:
            return np.zeros_like(t, dtype=float)
        p = self.prior
        own = self._calibration(t) / self.mass
        if p <= 0:
            return own / total
        b = np.interp(t, self.grid, broad, left=0.0, right=0.0)
        d = np.interp(t, self.grid, smeared_square, left=0.0, right=0.0)
        mixture = (1.0 - p) * own + p * b
        # p * [(1 - q) B + q D / B] with q = p B / mixture, written so that
        # neither B nor the mixture is ever divided by where it is zero.
        shifted = np.where(mixture > 0,
                           (p * (1.0 - p) * own * b + p * p * d) / np.maximum(mixture, 1e-300),
                           0.0)
        return ((1.0 - p) * own + shifted) / total

    def over(self, t: np.ndarray, log_scales: Optional[np.ndarray]) -> np.ndarray:
        """l at each point, each under its own scale when there are several."""
        if log_scales is None or self.prior <= 0:
            return self.at(t)
        out = np.empty(len(t))
        for scale in np.unique(log_scales):
            chosen = log_scales == scale
            out[chosen] = self.at(t[chosen], float(scale))
        return out


def _scored(measurement: Dict, points: np.ndarray, weights: np.ndarray,
            log_scales: Optional[np.ndarray] = None,
            unmodelled: Optional[np.ndarray] = None) -> Optional[float]:
    points = np.asarray(points, dtype=float)
    weights = np.asarray(weights, dtype=float)
    keep = weights > 0
    if not keep.any():
        return None
    points, weights = points[keep], weights[keep] / weights[keep].sum()
    if log_scales is not None:
        log_scales = np.asarray(log_scales, dtype=float)[keep]
    table = _Normalised(measurement, points, unmodelled)
    if not table.usable:
        return None
    return float(100.0 * (weights * table.over(points, log_scales)).sum())


def agreement_index(
    measurement: Dict,
    modelled: Sequence[Sequence[float]],
    unmodelled: Optional[Sequence[Sequence[float]]] = None,
    log_scale: Optional[float] = None,
) -> Optional[float]:
    """
    A_i for one date from a TABULATED posterior, `modelled` as
    [[cal_age, density], ...] on a regular grid -- the expectation of the
    normalised likelihood over the posterior's nodes.

    `unmodelled` holds the comparison against a likelihood from elsewhere, in
    the same form, which is how the formula is checked against OxCal's exports
    without our own calibration muddying the test. `log_scale` fixes the
    outlier scale for a date under an outlier model; left out, the scale is
    marginalised over its prior.

    Returns None rather than a number when there is nothing to compare.
    """
    modelled = np.asarray(modelled, dtype=float)
    if modelled.ndim != 2 or len(modelled) < 2:
        return None
    supplied = None if unmodelled is None else np.asarray(unmodelled, dtype=float)
    # Density times the width each node stands for, so an irregular grid is
    # weighted correctly; on a regular one this is just the density.
    widths = np.gradient(modelled[:, 0])
    weights = np.maximum(modelled[:, 1], 0.0) * np.abs(widths)
    scales = None if log_scale is None else np.full(len(modelled), float(log_scale))
    return _scored(measurement, modelled[:, 0], weights, scales, supplied)


def date_agreement(measurement: Dict, draws: Sequence[float],
                   log_scales: Optional[Sequence[float]] = None) -> Optional[float]:
    """
    A_i for one date from a run's JOINT draws of it, each with the outlier
    scale the same state held when the date's outlier model was sampled.
    """
    drawn = np.asarray(draws, dtype=float)
    if not drawn.size:
        return None
    return _scored(measurement, drawn, np.ones(len(drawn)),
                   None if log_scales is None else np.asarray(log_scales, dtype=float))


def outlier_scale_terms(measurements: Iterable[Dict]) -> int:
    """
    How many outlier-model scale parameters OxCal would count in n: one per
    distinct outlier model any date is under (see the module docstring).
    """
    return len({_outlier_model(m) for m in measurements if _outlier_prior(m) > 0})


def overall_agreement(indices: Iterable[Optional[float]],
                      extra_terms: int = 0) -> Optional[float]:
    """
    The whole model's agreement, combining the per-date indices.

    `extra_terms` counts parameters that score exactly 100 and so add to n
    without touching the product -- outlier_scale_terms, for a model with an
    outlier model in it.

    Computed in log space, since the product runs over every date and would
    otherwise underflow on a large model. The 1/sqrt(n) exponent multiplies any
    systematic per-date bias by sqrt(n).
    """
    usable = [a for a in indices if a is not None]
    if not usable:
        return None
    floored = np.maximum(np.asarray(usable, dtype=float), MINIMUM_FOR_OVERALL)
    logs = np.log(floored / 100.0)
    return float(100.0 * np.exp(logs.sum() / np.sqrt(len(usable) + extra_terms)))


def _scales_for(measurement: Dict, scales: Optional[Dict[str, Sequence[float]]]):
    if not scales or _outlier_prior(measurement) <= 0:
        return None
    drawn = scales.get(_outlier_model(measurement))
    return None if drawn is None else np.asarray(drawn, dtype=float)


def model_agreement(
    measurements: List[Dict],
    draws: Dict[str, Sequence[float]],
    scales: Optional[Dict[str, Sequence[float]]] = None,
) -> Optional[float]:
    """
    A_model: the agreement index of the whole model, from its JOINT posterior.

    `draws` maps a date's id to the calendar ages it took across the run's
    joint states -- one state per draw, every date in it at once, which is
    what carries the correlation the model imposed. `scales` maps an outlier
    model to the log10 scale each of those states held, where the solver
    sampled it (gibbs); without it a flagged date's scale is marginalised over
    its prior, which is the model exact_chain solves.

    Dates missing from `draws`, or with no likelihood to speak of, are left
    out of both the product and n, exactly as A_overall leaves out a date whose
    index could not be computed. Returns None when no date could be included.
    """
    log_joint: Optional[np.ndarray] = None
    counted = 0
    for measurement in measurements:
        drawn = draws.get(measurement['id'])
        if drawn is None:
            continue
        drawn = np.asarray(drawn, dtype=float)
        if not drawn.size:
            continue
        table = _Normalised(measurement, drawn)
        if not table.usable:
            continue
        values = table.over(drawn, _scales_for(measurement, scales))
        # In logs: the product runs over every date, and at eight dates it is
        # already at 1e-14. A draw the likelihood gives no mass to contributes
        # nothing to the expectation -- the model put the date where its own
        # measurement says it cannot be.
        term = np.log(np.maximum(values, _UNSUPPORTED))
        log_joint = term if log_joint is None else log_joint + term
        counted += 1

    if log_joint is None or not counted:
        return None
    # log of the MEAN, not the mean of the logs: the index is an expectation,
    # and averaging in log space would report the geometric mean instead.
    peak = float(log_joint.max())
    log_expectation = peak + float(np.log(np.exp(log_joint - peak).mean()))
    # The outlier models' scale parameters count towards n here as they do in
    # A_overall, and for the same reason: OxCal counts them.
    n = counted + outlier_scale_terms(measurements)
    return float(100.0 * np.exp(log_expectation / np.sqrt(n)))


def attach(results: Dict[str, Dict], measurements: List[Dict],
           draws: Optional[Dict[str, Sequence[float]]] = None,
           scales: Optional[Dict[str, Sequence[float]]] = None) -> Optional[float]:
    """
    Add `agreement_index` to each per-date result in place and return the
    overall index.

    A date whose outlier scale was SAMPLED is scored off the joint draws, each
    under the scale its state held -- the only place that pairing survives.
    Every other date is scored off its `probability_distribution`, the full
    marginal, which carries no Monte Carlo error beyond the run's own.
    """
    indices = []
    for m in measurements:
        entry = results.get(m['id'])
        if entry is None:
            continue
        drawn = (draws or {}).get(m['id'])
        paired = _scales_for(m, scales)
        if drawn is not None and paired is not None and len(paired) == len(drawn):
            index = date_agreement(m, drawn, paired)
        else:
            index = agreement_index(m, entry.get('probability_distribution') or [])
        entry['agreement_index'] = index
        indices.append(index)
    return overall_agreement(indices, outlier_scale_terms(measurements))
