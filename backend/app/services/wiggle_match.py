"""
Wiggle matching -- OxCal's D_Sequence and V_Sequence: a series of dates
separated by KNOWN gaps (tree rings, varves, any sampled sequence), solved in
closed form.

Why this is its own module
--------------------------
It shares none of services/exact_chain.py's machinery. There are no transfer
matrices, no message passing, no span density and no boundaries, because there
is only ONE free parameter: the calendar age at the series reference point.
Every member's age follows from it and its own measured gap. Folding a
one-parameter closed form into a 1,357-line chain integrator would obscure
both.

The maths
---------
Writing t for the calendar age (cal BP) at the reference point and g_i for
member i's `ring_offset`,

    p(t)  proportional to  product over i of  L_i(t + g_i)

with each L_i the ordinary calibration likelihood -- curve error, Delta-R and
any TPQ/TAQ limiter included, exactly as exact_chain._date_likelihood builds
it. Evaluate every member on a shared grid shifted by its own gap, sum the
logs, exponentiate. O(n*M), deterministic, and exact up to the grid step.

Two consequences worth stating rather than leaving to be rediscovered:

  * There is no sampler and nothing to converge, so none of the qualifications
    services/intervals.py carries about Monte Carlo error apply here.
  * The gaps are HARD. A D_Sequence asserts the ring counts are right; if they
    are not, the model has no way to say so except through the agreement
    index. That is why the combination index below is a required output and
    not a nicety.
  * NO OUTLIER MODEL. It follows from the line above: an outlier model gives
    one date room to sit apart from the rest, and a wiggle match has no room
    to give -- every member is the same age plus a fixed number. A flagged
    member is REFUSED rather than matched as though it were not flagged, which
    is what would happen silently otherwise (the likelihoods here come from
    exact_chain._date_likelihood, and the outlier mixture lives a level up in
    date_tables). The agreement index is what says a member does not belong.

    That is a statement about this app, not about OxCal, which does allow
    Outlier() inside a D_Sequence. Implementing it would mean marginalising an
    offset scale independently per member -- exact_chain's approximation
    rather than OxCal's shared scale -- and on a series whose members are one
    rigid distribution that is a stronger assumption than it looks. It is not
    worth making without a real OxCal fixture to hold it against.

THE SIGN CONVENTION, WHICH IS THE THING MOST LIKELY TO BE WRONG
---------------------------------------------------------------
Rings count forward in time; cal BP counts backward. A flipped sign here does
not produce nonsense -- it produces a tight range in the wrong place, which is
indistinguishable from a good answer without an independent check.

So, once, and the field help and the importer both say the same thing:

    ring_offset is years BEFORE the reference member. It is 0 AT the
    reference and INCREASES INTO THE PAST, so a member's calendar age is
    the reference age PLUS its offset:  t_i = t + g_i.

The reference is the point g = 0, whether or not a member sits exactly there;
`reference_sample_id` on the element records which member the entry helper
measured from, for labelling.

tests/test_wiggle_match.py pins this against an ASYMMETRIC stretch of the
curve, so a reversed series lands somewhere clearly wrong rather than merely
shifted -- a symmetric test region would pass with the sign inverted, which is
the failure mode the fixture is designed against.

Why this builds its own grid
----------------------------
exact_chain._build_grid pads the dates' support by max(800, 6 * support),
because BOUNDARIES genuinely range far beyond the outermost date -- that is
what they are for. A wiggle match has no boundaries at all, so at a fixed
point budget that padding is paid for in resolution: on a 600-ring series it
takes the step from about 1 year to 6.6, and a 6.6-year cell quantising an
answer whose selling point is +/-11 years gives away most of what the method
buys. So the grid here is the series' own support plus a modest FIXED pad --
fixed because the only reason to reach past the support is to keep a reported
range off the window edge -- found in two passes, coarse then fine.

Measured against the shipped IntCal20, both passes included: 3.4 ms for 8
members over 250 rings and 5.7 ms for 12 over 600, on a fine grid of about 470
points at one year. It runs inside the request; it does not need
services/jobs.py, and there is room to build the grid twice.
"""
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.services import agreement as agreement_service
from app.services.calibration import get_curve
from app.services.exact_chain import (
    MIN_GRID_STEP_YEARS,
    REPORTING_STEP_YEARS,
    SUPPORT_THRESHOLD,
    _date_likelihood,
    _summarize,
    convolve_on_grid,
)
from app.services.model_tree import WiggleGroup, inherited_bounds
from app.services.stratigraphy import effective_bounds

# Points the coarse first pass is allowed. Its only job is to find where the
# product has mass, so it can be coarse; the answer comes from the second.
COARSE_GRID_POINTS = 4000
# Calendar years kept either side of the located support in the fine pass.
# FIXED rather than proportional to the support: there is no boundary to
# accommodate, and the only reason to reach beyond the support at all is so a
# reported range is not clipped at the window edge.
FINE_PAD_YEARS = 200.0
# Points the fine pass is allowed before the step is coarsened. Only reached
# by a series whose support is thousands of years wide, i.e. one that has not
# resolved -- and there a fine step would be reporting resolution the answer
# does not have.
MAX_FINE_GRID_POINTS = 60000
# A gap uncertainty is truncated at this many sigma when its kernel is built.
# Five is far enough out that the discarded mass is below the grid's own
# resolution, and near enough that a 200-year sigma does not build a kernel
# wider than the window.
GAP_KERNEL_SIGMAS = 5.0


class WiggleMatchError(ValueError):
    """A series that cannot be matched, with the reason an analyst needs."""


def _member_support(measurement: Dict, offset: float) -> Tuple[float, float]:
    """
    Where this member puts the REFERENCE age, in cal BP: its own likelihood's
    support shifted back by its gap.
    """
    curve = get_curve(measurement.get('curve_id') or 'intcal20')
    cal, prob = curve.calibrate(
        measurement['age_bp'], measurement['error_1sigma'],
        measurement.get('delta_r'), measurement.get('delta_r_error'),
    )
    significant = np.where(prob >= prob.max() * SUPPORT_THRESHOLD)[0]
    if not len(significant):
        return float(cal[0]) - offset, float(cal[-1]) - offset
    return float(cal[significant[0]]) - offset, float(cal[significant[-1]]) - offset


def _curve_limits(measurements: Sequence[Dict]) -> Tuple[float, float]:
    """The calendar range every member's curve can be read over."""
    lo, hi = -np.inf, np.inf
    for m in measurements:
        curve = get_curve(m.get('curve_id') or 'intcal20')
        lo = max(lo, float(curve.cal_ages[0]))
        hi = min(hi, float(curve.cal_ages[-1]))
    return lo, hi


def _gap_kernel(step: float, sigma: float) -> np.ndarray:
    """
    A gap's own uncertainty as a convolution kernel over calendar years --
    what makes a V_Sequence a V_Sequence.

    Marginalising G_i ~ N(g_i, sigma_i) turns member i's shifted likelihood
    into that likelihood convolved with this, so a D_Sequence is exactly the
    sigma = 0 case of the same code path rather than a separate one.
    """
    reach = max(step, GAP_KERNEL_SIGMAS * sigma)
    lags = np.arange(-reach, reach + step, step)
    kernel = np.exp(-0.5 * (lags / sigma) ** 2)
    total = kernel.sum()
    return kernel / total if total > 0 else kernel


def _log_terms(grid: np.ndarray, members: List[Tuple[Dict, float, float]],
               bounds_by_id: Dict[str, Tuple]) -> Optional[List[np.ndarray]]:
    """
    Each member's log likelihood as a function of the REFERENCE age on `grid`,
    scaled to peak at zero; None when some member has no support on it.

    Each member is evaluated at grid + its own gap, which is the whole of the
    model: shift, multiply. Where a gap carries an uncertainty the shifted
    likelihood is smeared by it first (see _gap_kernel), which is the only
    difference between a D_Sequence and a V_Sequence.
    """
    step = float(grid[1] - grid[0])
    terms: List[np.ndarray] = []
    for measurement, offset, offset_error in members:
        shifted = _date_likelihood(
            grid + offset, measurement,
            bounds_by_id.get(measurement['id'], (None, None)))
        if offset_error > 0:
            shifted = convolve_on_grid(shifted, _gap_kernel(step, offset_error))
        peak = shifted.max()
        if peak <= 0:
            # Nowhere on this window can this member be. Returned as None
            # rather than raising: the caller decides, and the coarse pass
            # reaching here simply means the window is in the wrong place.
            return None
        with np.errstate(divide='ignore'):
            terms.append(np.log(shifted / peak))
    return terms


def _log_product(grid: np.ndarray, members: List[Tuple[Dict, float, float]],
                 bounds_by_id: Dict[str, Tuple]) -> np.ndarray:
    """log of the product over members, as a function of the REFERENCE age."""
    terms = _log_terms(grid, members, bounds_by_id)
    if terms is None:
        return np.full(len(grid), -np.inf)
    return np.sum(terms, axis=0)


def _member_density(grid: np.ndarray, index: int,
                    members: List[Tuple[Dict, float, float]],
                    terms: List[np.ndarray],
                    bounds_by_id: Dict[str, Tuple],
                    reference_density: np.ndarray) -> np.ndarray:
    """
    Member `index`'s own age, t_i = t + G_i, as a density on grid + g_i.

    With a COUNTED gap (sigma 0) G_i is g_i exactly, and this is the
    reference's density moved bodily -- one distribution translated.

    With a MEASURED gap it is not. Marginalising every other gap as the
    reference does, then the reference age itself, leaves

        p(t_i)  proportional to  L_i(t_i) * (R_-i conv N(0, sigma_i))(t_i - g_i)

    where R_-i is the product of every OTHER member's smeared term. The
    member's own measurement stays sharp and what the rest of the series says
    about it is blurred by how well its position in the series is known. So
    the member gets a width of its own: usually wider than the reference --
    knowing the series' age to +/-4 years does not place a ring whose
    position in it is only known to +/-5 -- but narrower where its own
    measurement sits on a steep stretch of curve and pins it harder than the
    series pins gap 0. Checked against a brute-force integral over (t, G_i).
    """
    measurement, offset, offset_error = members[index]
    if offset_error <= 0:
        return reference_density
    step = float(grid[1] - grid[0])
    others = [term for k, term in enumerate(terms) if k != index]
    rest = _density(np.sum(others, axis=0)) if others else np.ones(len(grid))
    smeared_rest = convolve_on_grid(rest, _gap_kernel(step, offset_error))
    own = _date_likelihood(grid + offset, measurement,
                           bounds_by_id.get(measurement['id'], (None, None)))
    density = own * smeared_rest
    # The two integrate to the same mass analytically; only a window that
    # clips the smear could empty this, and the translated reference is the
    # honest fallback there rather than an exception.
    return density if density.sum() > 0 else reference_density


def _density(log_weights: np.ndarray) -> np.ndarray:
    finite = np.isfinite(log_weights)
    if not np.any(finite):
        return np.zeros(len(log_weights))
    out = np.zeros(len(log_weights))
    out[finite] = np.exp(log_weights[finite] - log_weights[finite].max())
    return out


def _build_grid(members: List[Tuple[Dict, float, float]],
                bounds_by_id: Dict[str, Tuple]) -> np.ndarray:
    """
    The reference age's calendar grid: the series' own support plus
    FINE_PAD_YEARS, at MIN_GRID_STEP_YEARS, located by a coarse first pass.

    The coarse pass runs over the UNION of what each member alone allows,
    because the intersection can be empty on a series whose gaps are wrong --
    and "these ring counts cannot be right" is the answer that case deserves,
    not an exception from an empty range.
    """
    offsets = [offset for _m, offset, _e in members]
    supports = [_member_support(m, offset) for m, offset, _e in members]
    lo = min(s[0] for s in supports)
    hi = max(s[1] for s in supports)

    # Every member is read at grid + its own gap, so the window the CURVE has
    # to cover is wider than the reference's own by the series length. Getting
    # this wrong truncates the oldest member's likelihood silently -- the
    # curve clamps rather than raising -- so it is enforced here rather than
    # assumed.
    curve_lo, curve_hi = _curve_limits([m for m, _o, _e in members])
    lo = max(lo, curve_lo - min(offsets))
    hi = min(hi, curve_hi - max(offsets))
    if hi <= lo:
        raise WiggleMatchError(
            'the series reaches outside the calibration curve: with these gaps '
            'there is no calendar age at which every member can be read off it'
        )

    coarse_step = max(MIN_GRID_STEP_YEARS, (hi - lo) / COARSE_GRID_POINTS)
    coarse = np.arange(lo, hi + coarse_step, coarse_step)
    density = _density(_log_product(coarse, members, bounds_by_id))
    peak = density.max()
    if peak <= 0:
        raise WiggleMatchError(
            'no calendar age fits every member of this series at the gaps '
            'given. Either the gaps are wrong, or one of the dates does not '
            'belong to the series'
        )

    significant = np.where(density >= peak * SUPPORT_THRESHOLD)[0]
    fine_lo = max(lo, float(coarse[significant[0]]) - FINE_PAD_YEARS)
    fine_hi = min(hi, float(coarse[significant[-1]]) + FINE_PAD_YEARS)
    step = max(MIN_GRID_STEP_YEARS,
               (fine_hi - fine_lo) / MAX_FINE_GRID_POINTS)
    grid = np.arange(fine_lo, fine_hi + step, step)
    if len(grid) < 2:
        # A support narrower than one grid step. Widen rather than fail: the
        # answer is real and simply very tight.
        grid = np.arange(fine_lo - step, fine_hi + 2 * step, step)
    return grid


def solve(measurements: List[Dict], group: WiggleGroup,
          root: Optional[Dict] = None, num_samples: int = 4000,
          seed: int = 42, resolution: float = REPORTING_STEP_YEARS) -> Dict:
    """
    Solve one wiggle-matched series.

    Returns, in the shape the rest of the app already reads:

        {'reference':   the age at ring_offset 0, in _summarize's shape,
         'measurements': {date_id: that member's posterior, same shape},
         'agreement':   {date_id: A_i},
         'combination_agreement': the overall index over all members,
         'members':     [{'id', 'name', 'ring_offset', 'ring_offset_error',
                          'agreement_index'}] youngest first,
         'method':      'wiggle_match',
         'grid':        {'lower', 'upper', 'step'},
         'gap_units':   what the offsets are counted in,
         'series_span': the outermost gap minus the innermost}

    Each member's posterior is the reference's shifted by its own gap -- one
    distribution translated rather than n results, which every caller that
    renders them has to say -- except where the gap was measured rather than
    counted, which gives that member a width of its own (see
    _member_density).

    `resolution` is the calendar step the HPD ranges are reported at, as in
    exact_chain.solve.
    """
    by_id = {m['id']: m for m in measurements}
    element_bounds = inherited_bounds(root) if root else {}

    members: List[Tuple[Dict, float, float]] = []
    for date_id, offset, offset_error in group.members:
        measurement = by_id.get(date_id)
        if measurement is None:
            raise WiggleMatchError(
                f"the wiggle match names a date ('{date_id}') that is not in "
                'this analysis')
        members.append((measurement, float(offset), float(offset_error)))
    if not members:
        raise WiggleMatchError(
            'the wiggle match has no dates in it, so there is nothing to match')

    # Held here as well as at every edge (model_tree.validate_ordinates),
    # because this is the function that would otherwise drop the setting: the
    # likelihoods below are built by _date_likelihood, which is the plain
    # calibration, where an outlier model lives one level up in
    # exact_chain.date_tables. A member flagged as a possible outlier would be
    # matched as though it were not, and nothing on the page would say so.
    flagged = [m.get('sample_name') or m['id']
               for m, _offset, _error in members
               if (m.get('outlier_prior') or 0) > 0]
    if flagged:
        raise WiggleMatchError(
            f"{', '.join(sorted(flagged)[:4])} carr"
            f"{'ies' if len(flagged) == 1 else 'y'} an outlier model, which a "
            'wiggle match cannot apply: its gaps are asserted, so there is '
            'nothing for a date to be an outlier from. A member that does not '
            'belong shows up in the agreement index instead'
        )

    bounds_by_id: Dict[str, Tuple] = {}
    for measurement, _offset, _error in members:
        el_taq, el_tpq = element_bounds.get(
            measurement.get('element_id'), (None, None))
        bounds_by_id[measurement['id']] = effective_bounds(
            measurement, {'taq_age_bp': el_taq, 'tpq_age_bp': el_tpq})

    grid = _build_grid(members, bounds_by_id)
    terms = _log_terms(grid, members, bounds_by_id)
    density = (_density(np.sum(terms, axis=0)) if terms is not None
               else np.zeros(len(grid)))
    if density.sum() <= 0:
        raise WiggleMatchError(
            'no calendar age fits every member of this series at the gaps '
            'given. Either the gaps are wrong, or one of the dates does not '
            'belong to the series'
        )

    rng = np.random.default_rng(seed)
    reference = _summarize(grid, density, rng, num_samples, resolution)

    results: Dict[str, Dict] = {}
    member_records: List[Dict] = []
    indices: List[Optional[float]] = []
    for index, (measurement, offset, offset_error) in enumerate(members):
        shifted_grid = grid + offset
        # For a counted gap, the SAME density on a grid moved bodily by this
        # member's gap. Not a separate inference: _summarize normalises and
        # trims identically whatever the shift, so a member's plotted curve is
        # the reference's translated, which is exactly the claim the model
        # makes -- and exactly what the results panel has to say out loud, or
        # n identically-shaped curves read as n independent confirmations.
        # A measured gap widens its member; see _member_density.
        member_density = _member_density(
            grid, index, members, terms, bounds_by_id, density)
        summary = _summarize(shifted_grid, member_density, rng, num_samples, resolution)
        index = agreement_service.agreement_index(
            measurement, summary['probability_distribution'])
        summary['agreement_index'] = index
        results[measurement['id']] = summary
        indices.append(index)
        member_records.append({
            'id': measurement['id'],
            'name': measurement.get('sample_name') or measurement['id'],
            'ring_offset': offset,
            'ring_offset_error': offset_error or None,
            'agreement_index': index,
        })

    offsets = [offset for _m, offset, _e in members]
    return {
        'method': 'wiggle_match',
        'reference': reference,
        'measurements': results,
        'agreement': {r['id']: r['agreement_index'] for r in member_records},
        # The index itself only. What OxCal quotes a COMBINATION against
        # scales with n, and this app does not print a threshold it has not
        # pinned against a real run -- the same standard agreement.py holds
        # Amodel to.
        'combination_agreement': agreement_service.overall_agreement(indices),
        'members': member_records,
        'gap_units': group.gap_units,
        'reference_sample_id': group.reference_sample_id,
        'series_span': float(max(offsets) - min(offsets)),
        'grid': {'lower': float(grid[0]), 'upper': float(grid[-1]),
                 'step': float(grid[1] - grid[0])},
    }
