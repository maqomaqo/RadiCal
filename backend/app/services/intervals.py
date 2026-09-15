"""
Durations and differences -- the quantities that are functions of SEVERAL
modelled ages at once, rather than of one.

Why this is not just more of exact_chain.py
------------------------------------------
Every other number the solvers report is a MARGINAL: this date's calendar age,
that boundary's. A duration is not. "How long did Phase 2 last" is
`older boundary - younger boundary`, and the two boundaries are not independent
-- the model requires one to be older, and the same dates hold them apart.

Pairing two marginals answers a different question, and wrongly in SHAPE rather
than scale, which an eyeball check would not catch: on a single phase of six
dates it puts 8.2% of the pairs below zero, a phase ending before it starts,
where the joint posterior gives that no probability at all. So this module is
fed joint draws rather than marginals.

The four quantities, in the vocabulary the rest of the app uses:

  sequence duration  the timespan of a Sequence: its first Boundary minus its
                     last. The site-wide "how long was this place in use".
  phase duration     the timespan a pair of Boundaries encloses -- OxCal's
                     Interval(). It is the modelled duration of the phase,
                     so it can and usually does exceed the spread of the dates
                     inside it: the dated events are a sample of the phase, not
                     the whole of it.
  sample duration    the timespan of the DATED EVENTS in a phase, max minus min
                     -- OxCal's Span(). Always shorter than the phase duration
                     above, and the gap between the two is exactly how much of
                     the phase the dates did not catch.
  difference         between two phases: the signed years between their
                     modelled intervals, POSITIVE for a gap and NEGATIVE for an
                     overlap. Between two dates in a group calibration: the
                     signed years between them, positive when the first is the
                     older.

Overlap, and the word attached to it
------------------------------------
`overlap` on a difference record is a probability in [0, 1], and it means two
slightly different things because a phase is an interval and a date is a point:

  two phases  P(their modelled intervals intersect at all).
  two dates   P(the ordering is unresolved) = 2 * min(P(A older), P(B older)).
              A pair whose posterior says "A is older" 50/50 scores 1.0; a pair
              the model has fully separated scores 0.

Both are on the same scale and read the same way -- 1 means the model cannot
tell them apart in time, 0 that it separates them cleanly -- so one severity
vocabulary serves both. The bands are 1 - SIGMA_2, 1 - SIGMA_1, SIGMA_1 and
SIGMA_2: the same coverages every HPD here is quoted at, and their complements.
So "none" means separated at 2 sigma and "extensive" overlapping at 2 sigma,
rather than a new convention the reader has to learn.

Monte Carlo, deliberately
-------------------------
These are computed from draws even on the exact path, which is a real
qualification on numbers this app otherwise reports with no Monte Carlo error.
Taken knowingly:

- Only a single bounded group's phase duration is cheaply exact, the joint of
  one adjacent boundary pair already being on hand as a matrix. A sequence
  duration couples the two ends of the chain and a sample duration is the range
  of K conditionally-independent dates; neither factorises, and both would cost
  an O(M^3) pass.
- Mixing an exact phase duration with a sampled sample duration in one table,
  where the point is comparing the two, would put a systematic difference
  between them that has nothing to do with the archaeology.
- The exact path's draws are INDEPENDENT draws from the true joint posterior
  (forward-filter/backward-sample over the chain the solver already built), not
  an MCMC chain, so the error is plain 1/sqrt(N).

`draws` and `independent` are reported alongside the numbers, so the
qualification travels with them.
"""
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.services.calibration import SIGMA_1, SIGMA_2, hpd_ranges_from_distribution

# Joint draws taken on the exact path and for a group calibration, where they
# are independent and therefore cost only their own arithmetic. The sampled
# path keeps whatever its own run gives it; see gibbs.run's `collect_draws`.
DEFAULT_DRAWS = 20000

# Most bins a reported duration is broken into. A duration posterior can be
# thousands of years wide, and an HPD at the 5-year step a calendar date is
# quoted at would claim resolution the draws do not have. The step widens
# instead, and is reported as `step` on every summary.
#
# Forty rather than a couple of hundred, because an HPD region is built by
# taking bins in order of density until the confidence is met, so bins near the
# cut go in or out on their own Monte Carlo error and the region comes back in
# pieces. On a two-phase model's gap at 20,000 draws, a 5-year step reported
# the 68% region as eight fragments where the posterior is one long-tailed
# hump; at 60 bins it was still in two, at 40 it is one range. More draws do
# NOT fix it, since the density really is flat where the cut falls.
#
# Forty also lands the ordinary case -- a duration a couple of hundred years
# wide -- back on the five-year step exactly, so only a genuinely wide one is
# coarsened. Same failure exact_chain._reported_ranges rebins to fix, one step
# further along because these bins hold draws rather than an exact density.
MAX_BINS = 40

# The spread the bin width is taken from, as percentiles rather than full
# min-to-max: a couple of draws far out in a boundary's heavy tail would
# otherwise make the reported RESOLUTION a function of the most extreme draw,
# so more draws would coarsen the answer. Measured from min/max, 20,000 to
# 60,000 draws moved the step from 10 years to 15.
SPREAD_PERCENTILES = (0.5, 99.5)
# ...but the bins still have to cover every draw, so the full range is allowed
# this multiple of MAX_BINS before the step widens to fit it.
MAX_BINS_WITH_TAILS = 6 * MAX_BINS

# Pairs a difference table holds before it drops to consecutive pairs only.
# 300 is 25 dates, already a table nobody reads across.
MAX_PAIRS = 300

# Overlap probability -> word. Upper bound of each band, ordered; anything
# above the last is SEVERITY_TOP. See the module docstring for the numbers.
SEVERITY_BANDS: Tuple[Tuple[float, str], ...] = (
    (1.0 - SIGMA_2, 'none'),        # separated at 2 sigma
    (1.0 - SIGMA_1, 'slight'),      # separated at 1 sigma
    (SIGMA_1, 'moderate'),
    (SIGMA_2, 'substantial'),
)
# "extensive", not "significant": the top band is where the model can say the
# LEAST about the ordering, and "significant" reads as a finding to anyone who
# has met the word in statistics -- the one place a reader could mistake a
# failure to separate for a result. Renamed at the source rather than relabelled
# in the UI because this string is persisted on the analysis and printed
# verbatim into the CSV and the PDF; a display-only rename would have the screen
# and the exports disagree about the same pair.
SEVERITY_TOP = 'extensive'
SEVERITY_WORDS = tuple(word for _, word in SEVERITY_BANDS) + (SEVERITY_TOP,)

# What a shared-boundary pair says instead of a figure and a severity word.
# Its gap and overlap probability are both exactly zero BY CONSTRUCTION -- one
# phase's end IS the next one's start -- so a reader meeting `0` and `none` in
# an export could not tell the structure from a result, and `none` elsewhere
# here means "separated at 2 sigma", the opposite of what it says.
SHARED_BOUNDARY_TEXT = 'shares a transition'


def _reporting_step() -> float:
    """
    The finest step a duration is reported at: the same five years every
    calendar HPD here is. Imported inside the function because exact_chain
    imports THIS module, and restating the number would let the two drift.
    """
    from app.services.exact_chain import REPORTING_STEP_YEARS
    return REPORTING_STEP_YEARS


def severity(overlap: float) -> str:
    """The word for an overlap probability. See the module docstring."""
    for upper, word in SEVERITY_BANDS:
        if overlap < upper:
            return word
    return SEVERITY_TOP


# --------------------------------------------------------------------------
# Summarising a derived quantity
# --------------------------------------------------------------------------

def _step_for(values: np.ndarray, base: Optional[float] = None) -> float:
    """
    Bin width for a set of draws: the reporting step, widened until MAX_BINS
    covers the draws' central spread (and MAX_BINS_WITH_TAILS the whole of
    them), and always a whole multiple of the reporting step so two quantities
    on one page are quoted on compatible grids.

    `base` overrides the five-year floor for a quantity that is NOT measured in
    years. The only such caller is a dispersal speed in km/yr (services/
    dispersal.py), where the default would quote a front whose whole posterior
    spans half a kilometre per year in a single five-wide bin and return an
    HPD of nonsense. Every quantity actually measured in years leaves it alone,
    so they all stay on one grid.
    """
    base = _reporting_step() if base is None else float(base)
    low, high = np.percentile(values, SPREAD_PERCENTILES)
    bins = [float(high - low) / (MAX_BINS * base),
            float(values.max() - values.min()) / (MAX_BINS_WITH_TAILS * base)]
    return base * max(1.0, np.ceil(max(bins)))


def _smoothed(counts: np.ndarray) -> np.ndarray:
    """
    Bin counts under a [1, 2, 1]/4 kernel, ends held rather than zero-padded so
    no mass leaves the support -- which matters because a duration piles up
    hard against zero.

    An HPD region is assembled bin by bin in order of density, so two adjacent
    bins straddling the cut are decided by their own sampling noise and the
    region fragments. Widening the bins (MAX_BINS) is most of the fix and this
    is the rest. It widens the reported region slightly, the conservative
    direction, and touches only the region: mean, median and standard deviation
    all come from the raw draws.
    """
    if len(counts) < 3:
        return counts.astype(float)
    padded = np.concatenate([counts[:1], counts, counts[-1:]]).astype(float)
    return 0.25 * padded[:-2] + 0.5 * padded[1:-1] + 0.25 * padded[2:]


def _ranges(centres: np.ndarray, weights: np.ndarray, confidence: float,
            step: float) -> List[Dict]:
    """HPD ranges over binned draws, each widened to the edges of its bins --
    the same correction exact_chain._reported_ranges applies."""
    keep = weights > 0
    if not np.any(keep):
        return []
    found = hpd_ranges_from_distribution(centres[keep], weights[keep], confidence)
    for entry in found:
        entry['min'] -= step / 2.0
        entry['max'] += step / 2.0
    return found


def summarise(values: Sequence[float],
              base_step: Optional[float] = None) -> Optional[Dict]:
    """
    Draws of a quantity measured in YEARS, turned into the reported shape.

    Deliberately NOT the key names a calendar posterior uses. A duration is not
    a date, converting it to BC/AD would be nonsense, and the exports format
    calendar-age columns BY NAME -- so different names are what stops a
    duration reaching a calendar formatter by accident.

    `base_step` is for the one quantity here that is not in years -- a front
    speed in km/yr -- and says what resolution to quote it at. See _step_for.
    """
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return None

    step = _step_for(values, base_step)
    lo = np.floor(values.min() / step) * step
    hi = np.ceil(values.max() / step) * step + step
    edges = np.arange(lo, hi + step, step)
    counts, _ = np.histogram(values, bins=edges)
    centres = (edges[:-1] + edges[1:]) / 2.0
    smoothed = _smoothed(counts)
    density = smoothed / (smoothed.sum() * step) if smoothed.sum() > 0 else smoothed

    hpd_68 = _ranges(centres, smoothed, SIGMA_1, step)
    hpd_95 = _ranges(centres, smoothed, SIGMA_2, step)
    # Trimmed to where there is mass but CONTIGUOUS across it, empty bins in
    # the middle kept: utils/hpd.hpdEnvelopeFromHistogram re-derives its own HPD
    # from this series and reads the bin width off the first two points, so
    # holes would be measured against the wrong width.
    occupied = np.nonzero(smoothed > 0)[0]
    span = (slice(int(occupied[0]), int(occupied[-1]) + 1) if occupied.size
            else slice(0, 0))

    return {
        'mean': float(values.mean()),
        'median': float(np.median(values)),
        'std': float(values.std(ddof=1)) if values.size > 1 else 0.0,
        'hpd_68': [min(r['min'] for r in hpd_68), max(r['max'] for r in hpd_68)] if hpd_68 else None,
        'hpd_95': [min(r['min'] for r in hpd_95), max(r['max'] for r in hpd_95)] if hpd_95 else None,
        'hpd_68_ranges': hpd_68,
        'hpd_95_ranges': hpd_95,
        # The step the ranges above are quoted at -- see MAX_BINS.
        'step': float(step),
        'distribution': [[float(c), float(d)] for c, d in zip(centres[span], density[span])],
    }


def draws_from_distribution(ages: np.ndarray, weights: np.ndarray,
                            rng: np.random.Generator, count: int) -> np.ndarray:
    """
    Independent draws from a discretised distribution by inverse CDF, for a
    group calibration: its dates are independent by construction, so drawing
    each separately IS drawing from the joint.
    """
    ages = np.asarray(ages, dtype=float)
    weights = np.asarray(weights, dtype=float)
    total = weights.sum()
    if total <= 0:
        raise ValueError('a calibration came out empty; nothing to draw from')
    cdf = np.cumsum(weights) / total
    return np.interp(rng.random(count), cdf, ages)


# --------------------------------------------------------------------------
# What a model has to report -- pure structure, no draws involved
# --------------------------------------------------------------------------

def _children(element: Dict) -> List[Dict]:
    return list(element.get('children') or [])


def _name_of(element: Dict) -> str:
    from app.services.model_tree import element_label
    return element_label(element)


def _enclosures(root: Dict) -> Tuple[Dict[str, Tuple[str, str]], List[Dict]]:
    """
    (enclosing, groups).

    `enclosing` maps every element id to the INNERMOST pair of Boundary
    siblings bracketing it, as (older, younger). An element with no such pair
    above it is absent from the map.

    `groups` is one entry per consecutive pair of Boundary siblings:
    {'older', 'younger', 'elements'}, `elements` being the non-Boundary
    children between them.

    The same innermost-pair-wins rule model_tree._plan_element applies to dates,
    rederived here rather than threaded through ModelPlan because a duration
    needs the ELEMENT each pair brackets and a SpanGroup records only dates.
    """
    enclosing: Dict[str, Tuple[str, str]] = {}
    groups: List[Dict] = []

    def walk(element: Dict, inherited: Optional[Tuple[str, str]]) -> None:
        children = _children(element)
        boundary_at = [i for i, c in enumerate(children)
                       if c.get('element_type') == 'boundary']

        if element.get('element_type') == 'sequence' and boundary_at:
            for older_index, younger_index in zip(boundary_at, boundary_at[1:]):
                groups.append({
                    'older': children[older_index]['id'],
                    'younger': children[younger_index]['id'],
                    'elements': [children[i] for i in range(older_index + 1, younger_index)],
                })

        for index, child in enumerate(children):
            if child.get('element_type') == 'boundary':
                continue
            pair = inherited
            if element.get('element_type') == 'sequence':
                before = [i for i in boundary_at if i < index]
                after = [i for i in boundary_at if i > index]
                if before and after:
                    pair = (children[before[-1]]['id'], children[after[0]]['id'])
            if pair is not None:
                enclosing[child['id']] = pair
            walk(child, pair)

    if root.get('id'):
        walk(root, None)
    return enclosing, groups


def plan_durations(root: Dict, measurements: List[Dict]) -> List[Dict]:
    """
    Every duration this model can report, as specs: dicts carrying what to
    compute it from (`boundaries`, or `dates`) and how to label it.

    Three kinds, in the order they are reported:

      sequence_duration  first to last Boundary CHILD of a Sequence.
                         Suppressed when those are its only two boundaries with
                         one phase between them, since the phase duration is
                         then the same quantity under another name.
      phase_duration     each consecutive pair of Boundary siblings, provided
                         something other than a Hiatus sits between them. A
                         pair with only a Hiatus between them is a GAP, reported
                         as a difference instead.
      sample_duration    each phase holding two or more dates.
    """
    if not root:
        return []
    enclosing, groups = _enclosures(root)

    dates_by_element: Dict[str, List[str]] = {}
    for m in measurements:
        key = m.get('element_id')
        if key:
            dates_by_element.setdefault(key, []).append(m['id'])

    names: Dict[str, str] = {}
    stack = [root]
    elements: List[Dict] = []
    while stack:
        node = stack.pop(0)
        elements.append(node)
        names[node['id']] = _name_of(node)
        stack = _children(node) + stack

    group_pairs = {(g['older'], g['younger']) for g in groups}
    specs: List[Dict] = []

    for element in elements:
        if element.get('element_type') != 'sequence':
            continue
        boundaries = [c['id'] for c in _children(element)
                      if c.get('element_type') == 'boundary']
        if len(boundaries) < 2:
            continue
        pair = (boundaries[0], boundaries[-1])
        if len(boundaries) == 2 and pair in group_pairs:
            # The sequence IS one bounded group; the phase duration says it.
            continue
        specs.append({
            'kind': 'sequence_duration',
            'id': f"sequence:{element['id']}",
            'name': names[element['id']],
            'element_id': element['id'],
            'boundaries': pair,
            'dates': (),
        })

    for group in groups:
        substantive = [e for e in group['elements']
                       if e.get('element_type') != 'hiatus']
        if not substantive:
            # Two boundaries with only a Hiatus between them: the gap the
            # difference table already reports as the phases' separation.
            continue
        if len(substantive) == 1:
            name, element_id = names[substantive[0]['id']], substantive[0]['id']
        else:
            name = ' + '.join(names[e['id']] for e in substantive)
            element_id = None
        specs.append({
            'kind': 'phase_duration',
            'id': f"phase:{group['older']}:{group['younger']}",
            'name': name,
            'element_id': element_id,
            'boundaries': (group['older'], group['younger']),
            'dates': (),
        })

    for element in elements:
        if element.get('element_type') != 'phase':
            continue
        date_ids = dates_by_element.get(element['id'], [])
        if len(date_ids) < 2:
            continue
        specs.append({
            'kind': 'sample_duration',
            'id': f"sample:{element['id']}",
            'name': names[element['id']],
            'element_id': element['id'],
            'boundaries': enclosing.get(element['id'], ()),
            'dates': tuple(date_ids),
        })

    return specs


def plan_phase_differences(root: Dict, measurements: List[Dict]) -> Tuple[List[Dict], Optional[str]]:
    """
    Every pair of phases whose separation the model has something to say
    about, plus a note when the table had to be cut down.

    A phase is comparable only if a pair of Boundaries brackets it, since that
    bracket IS its modelled interval. Two phases sharing BOTH boundaries are
    skipped: they are bracketed identically, so "they coincide exactly" would
    be structural rather than inferred.

    A pair sharing ONE boundary is kept and flagged `shared_boundary`. Its gap
    is exactly zero by construction, and saying so plainly beats dropping the
    row and leaving the reader to wonder.
    """
    if not root:
        return [], None
    enclosing, _groups = _enclosures(root)

    dated: Dict[str, int] = {}
    for m in measurements:
        if m.get('element_id'):
            dated[m['element_id']] = dated.get(m['element_id'], 0) + 1

    # Depth first, siblings oldest first -- the order the results table and
    # every export put the model in.
    phases: List[Dict] = []

    def walk(element: Dict) -> None:
        if element.get('element_type') == 'phase' and element['id'] in enclosing:
            phases.append(element)
        for child in _children(element):
            walk(child)

    walk(root)

    pairs = [(a, b) for i, a in enumerate(phases) for b in phases[i + 1:]
             if enclosing[a['id']] != enclosing[b['id']]]
    note = None
    if len(pairs) > MAX_PAIRS:
        pairs = [(phases[i], phases[i + 1]) for i in range(len(phases) - 1)
                 if enclosing[phases[i]['id']] != enclosing[phases[i + 1]['id']]]
        note = (f'{len(phases)} phases make more than {MAX_PAIRS} pairs, so only '
                'each phase against the next one is reported.')

    specs = []
    for older, younger in pairs:
        a, b = enclosing[older['id']], enclosing[younger['id']]
        specs.append({
            'kind': 'phase_difference',
            'id': f"phase-diff:{older['id']}:{younger['id']}",
            'from_id': older['id'],
            'from_name': _name_of(older),
            'to_id': younger['id'],
            'to_name': _name_of(younger),
            'from_boundaries': a,
            'to_boundaries': b,
            'shared_boundary': bool(set(a) & set(b)),
        })
    return specs, note


def plan_date_differences(dates: List[Dict]) -> Tuple[List[Dict], Optional[str]]:
    """
    Every pair of dates in a group calibration, in the order given.

    `dates` are {'id', 'name'} in reporting order. Cut down to consecutive
    pairs past MAX_PAIRS, as phases are.
    """
    pairs = [(a, b) for i, a in enumerate(dates) for b in dates[i + 1:]]
    note = None
    if len(pairs) > MAX_PAIRS:
        pairs = list(zip(dates, dates[1:]))
        note = (f'{len(dates)} dates make more than {MAX_PAIRS} pairs, so only '
                'each date against the next one is reported.')
    return [{
        'kind': 'date_difference',
        'id': f"date-diff:{a['id']}:{b['id']}",
        'from_id': a['id'],
        'from_name': a['name'],
        'to_id': b['id'],
        'to_name': b['name'],
    } for a, b in pairs], note


# --------------------------------------------------------------------------
# Specs + joint draws -> reported records
# --------------------------------------------------------------------------

def _duration_values(spec: Dict, draws: Dict[str, np.ndarray]) -> Optional[np.ndarray]:
    if spec['kind'] == 'sample_duration':
        stacked = [draws[d] for d in spec['dates'] if d in draws]
        if len(stacked) < 2:
            return None
        block = np.vstack(stacked)
        return block.max(axis=0) - block.min(axis=0)
    older, younger = spec['boundaries']
    if older not in draws or younger not in draws:
        return None
    # cal BP counts backwards, so the older boundary is the larger number.
    return draws[older] - draws[younger]


def _interval_of(boundaries: Tuple[str, str], draws: Dict[str, np.ndarray]):
    older, younger = boundaries
    if older not in draws or younger not in draws:
        return None
    return draws[older], draws[younger]


def evaluate(duration_specs: List[Dict], difference_specs: List[Dict],
             draws: Dict[str, np.ndarray]) -> Tuple[List[Dict], List[Dict]]:
    """
    Turn specs plus joint draws into the records the API stores.

    `draws` maps a parameter id to calendar ages in cal BP, one entry per joint
    draw. Every array must be the same length and come from ONE set of draws:
    per-parameter marginals give the wrong answer for every quantity here.
    """
    durations = []
    for spec in duration_specs:
        values = _duration_values(spec, draws)
        if values is None:
            continue
        summary = summarise(values)
        if summary is None:
            continue
        durations.append({
            'kind': spec['kind'],
            'id': spec['id'],
            'name': spec['name'],
            'element_id': spec.get('element_id'),
            'dates': len(spec.get('dates') or ()) or None,
            'years': summary,
        })

    differences = []
    for spec in difference_specs:
        if spec['kind'] == 'phase_difference':
            first = _interval_of(spec['from_boundaries'], draws)
            second = _interval_of(spec['to_boundaries'], draws)
            if first is None or second is None:
                continue
            # Each interval is [younger end, older end] on the cal BP number
            # line, so the intersection runs from the younger of the older ends
            # to the older of the younger ends. Its length is positive where
            # they overlap and negative where they are apart -- one quantity
            # covering both, so there is no separate gap column to keep in step.
            intersection = (np.minimum(first[0], second[0])
                            - np.maximum(first[1], second[1]))
            signed = -intersection          # positive = gap, negative = overlap
            overlap = float(np.mean(intersection > 0.0))
            older_probability = None
        else:
            first_draws = draws.get(spec['from_id'])
            second_draws = draws.get(spec['to_id'])
            if first_draws is None or second_draws is None:
                continue
            signed = first_draws - second_draws
            older = float(np.mean(signed > 0.0))
            # How far the ordering is from settled: 1 at a coin flip on which
            # came first, 0 when it is certain.
            overlap = float(2.0 * min(older, 1.0 - older))
            older_probability = older

        summary = summarise(signed)
        if summary is None:
            continue
        record = {
            'kind': spec['kind'],
            'id': spec['id'],
            'from_id': spec['from_id'],
            'from_name': spec['from_name'],
            'to_id': spec['to_id'],
            'to_name': spec['to_name'],
            'years': summary,
            'overlap': overlap,
            'severity': severity(overlap),
        }
        if older_probability is not None:
            record['older_probability'] = older_probability
        if spec.get('shared_boundary'):
            record['shared_boundary'] = True
        differences.append(record)

    return durations, differences


# --------------------------------------------------------------------------
# Age against depth
# --------------------------------------------------------------------------

# Where the age-depth envelopes are cut. QUANTILES, not HPD regions: an
# envelope is drawn as a band and has to nest, and two HPD regions on a
# multi-peaked posterior need not -- the 68.3% one can reach outside the
# 95.4% one at a different depth and the band crosses itself. The coverages
# are the same two every other figure here is quoted at.
BAND_LEVELS = ((SIGMA_1, 68), (SIGMA_2, 95))


def calendar_band(values: np.ndarray) -> Dict:
    """A calendar age from joint draws, as a median and two bands.

    Calendar-age key names, deliberately unlike a duration's: these ARE dates
    and the exports format them as such, where every other figure in this
    module is a length in years and must never be converted to BC/AD.

    Public because a dispersal fit's t0 -- the front's arrival age at the
    origin -- is a calendar age in exactly this sense, and reporting it in the
    duration grammar would tell the UI not to format it as a date.
    """
    entry = {
        'median': float(np.median(values)),
        'mean': float(values.mean()),
    }
    for coverage, label in BAND_LEVELS:
        tail = (1.0 - coverage) / 2.0
        low, high = np.quantile(values, [tail, 1.0 - tail])
        entry[f'band_{label}'] = [float(low), float(high)]
    return entry


def plan_age_depth(root: Optional[Dict], measurements: List[Dict]) -> List[Dict]:
    """
    Every deposition sequence in the model, as the levels and intervals an
    age-depth answer is made of.

    A stack of per-date posteriors is NOT the deliverable of an age-depth
    model: what the reader wants is age against depth, with a band round it,
    and how fast the deposit accumulated between one level and the next.
    Every one of those is a function of SEVERAL ages at once, which is why it
    lives in this module and comes off joint draws.
    """
    from app.services.model_tree import DepositionGroup, plan_model
    if not root:
        return []
    groups: List[DepositionGroup] = plan_model(root, measurements).deposition_groups
    names = {m['id']: (m.get('sample_name') or m['id']) for m in measurements}

    specs = []
    for group in groups:
        specs.append({
            'element_id': group.element_id,
            'name': group.name,
            'depth_units': group.depth_units,
            'levels': [
                {'id': pid, 'depth': depth, 'dated': dated,
                 'name': names.get(pid) or group.level_names.get(pid)
                 or f'{depth:g} {group.depth_units}'}
                for pid, depth, dated in group.levels
            ],
        })
    return specs


def evaluate_age_depth(specs: List[Dict], draws: Dict[str, np.ndarray]) -> List[Dict]:
    """
    Specs plus joint draws into the age-depth records the API stores.

    The accumulation rate between two levels is (older age - younger age)
    divided by the depth between them, and it is read off the SAME draw of
    both ages. Pairing their marginals would give a rate distribution that is
    wrong in shape rather than scale -- the identical trap this module opens
    with for durations, and the reason nothing here is computed from a
    marginal.
    """
    records = []
    for spec in specs:
        levels = [level for level in spec['levels'] if level['id'] in draws]
        if len(levels) < 2:
            continue

        reported_levels = []
        for level in levels:
            reported_levels.append({
                'id': level['id'],
                'name': level['name'],
                'depth': level['depth'],
                'dated': level['dated'],
                **calendar_band(draws[level['id']]),
            })

        accumulation = []
        for older, younger in zip(levels, levels[1:]):
            depth_gap = abs(older['depth'] - younger['depth'])
            if depth_gap <= 0:
                continue
            elapsed = draws[older['id']] - draws[younger['id']]
            years = summarise(elapsed)
            if years is None:
                continue
            rate = elapsed / depth_gap
            tail = (1.0 - SIGMA_1) / 2.0
            low, high = np.quantile(rate, [tail, 1.0 - tail])
            accumulation.append({
                'from_depth': older['depth'],
                'to_depth': younger['depth'],
                'from_name': older['name'],
                'to_name': younger['name'],
                'depth_gap': float(depth_gap),
                # The elapsed time, in the same shape every other duration
                # here takes, so it renders and exports through the same code.
                'years': years,
                # ...and the same thing per unit depth, which is what an
                # accumulation rate IS and what the literature quotes.
                'rate_median': float(np.median(rate)),
                'rate_band_68': [float(low), float(high)],
            })

        records.append({
            'element_id': spec['element_id'],
            'name': spec['name'],
            'depth_units': spec['depth_units'],
            'levels': reported_levels,
            'accumulation': accumulation,
        })
    return records


def report(root: Optional[Dict], measurements: List[Dict],
           draws: Dict[str, np.ndarray], independent: bool,
           durations: bool = True,
           dispersal: Optional[Dict] = None) -> Dict:
    """
    The whole durations-and-differences block for a modelled analysis, ready
    to store on Analysis.intervals.

    `durations` False leaves the durations and differences out and reports
    only the age-depth section. That is for a run that has joint draws for a
    reason of its own -- an age-depth model always does -- without the analyst
    having asked for phase durations, which are a modelling claim of their own
    (see BayesianConfigInput.estimate_durations).

    `dispersal` adds the dispersal section, computed here for the same reason
    durations are: every figure in it is a function of many modelled ages at
    once and so has to come off these joint draws. It is a spec rather than a
    flag -- `{'origin': (lat, lon) | None}` -- because the two models in it
    want different things. A trend surface assumes no origin at all, which is
    what makes it the right first look at a dataset; a wave of advance is
    measured FROM somewhere, and its speed is meaningless without the point it
    was measured from. So an origin buys the wave AND the trend, and no origin
    buys the trend alone.
    """
    duration_specs = plan_durations(root, measurements) if root and durations else []
    difference_specs, note = (plan_phase_differences(root, measurements)
                              if root and durations else ([], None))
    found_durations, differences = evaluate(duration_specs, difference_specs, draws)
    block = _block(found_durations, differences, draws, independent, note)
    block['age_depth'] = evaluate_age_depth(
        plan_age_depth(root, measurements), draws)
    if dispersal is not None:
        # Imported here rather than at module scope: dispersal imports this
        # module for summarise and calendar_band, and at the top the two would
        # be a cycle.
        from app.services import dispersal as dispersal_service
        block['dispersal'] = _dispersal_section(
            dispersal_service.derive_sites(root, measurements), draws, dispersal)
    return block


def _dispersal_section(sites, draws: Dict[str, np.ndarray],
                       spec: Dict) -> Dict:
    """
    The two spatial models, side by side.

    Both are always computed when either is, because they answer different
    questions off the same draws and comparing them is the point: a tidy wave
    from an assumed origin together with a trend pointing somewhere else means
    the ORIGIN is wrong, which is a finding neither model makes alone. The
    trend costs one more pseudo-inverse, so there is no reason to make the
    reader ask for it separately.
    """
    from app.services import dispersal as dispersal_service
    origin = spec.get('origin')
    return {
        # Null when no origin was placed -- which is not a refusal, it is the
        # model declining to invent the hypothesis it would be testing.
        'wave': (dispersal_service.fit_wave(sites, draws, origin)
                 if origin is not None else None),
        'trend': dispersal_service.fit_trend(sites, draws),
    }


def report_dates(dates: List[Dict], draws: Dict[str, np.ndarray],
                 dispersal: Optional[Dict] = None) -> Dict:
    """
    The same block for a group calibration: differences only.

    Dates with no relationship asserted between them belong to no phase and no
    sequence, so they have no duration: the table is pairwise differences only.

    A dispersal fit here runs on §6.2's fallback -- each place's earliest dated
    event, because there is no model and therefore no Boundary. That is a
    terminus ante quem rather than an arrival, and the sites carry
    `basis='earliest_dated_event'` saying so.
    """
    specs, note = plan_date_differences(dates)
    _durations, differences = evaluate([], specs, draws)
    # Independent by construction: a group calibration asserts no relationship,
    # so drawing each date separately IS drawing from the joint.
    block = _block([], differences, draws, True, note)
    block['age_depth'] = []
    if dispersal is not None:
        from app.services import dispersal as dispersal_service
        block['dispersal'] = _dispersal_section(
            dispersal_service.derive_sites_from_dates(dates, draws),
            draws, dispersal)
    return block


def _block(durations: List[Dict], differences: List[Dict],
           draws: Dict[str, np.ndarray], independent: bool,
           note: Optional[str]) -> Dict:
    sizes = [len(v) for v in draws.values()]
    return {
        'durations': durations,
        'differences': differences,
        # Filled in by report()/report_dates(); declared here so every block
        # has the key and no consumer has to tell "no age-depth model" from
        # "written before there was one".
        'age_depth': [],
        # None means NOT ASKED FOR, which the results page reads as an offer to
        # re-run -- the same convention durations and age-depth already use. A
        # fit that was asked for and refused is a section whose models carry a
        # `refused` string, which is a different thing and says so.
        'dispersal': None,
        'draws': min(sizes) if sizes else 0,
        # True when the draws are independent rather than an MCMC chain, where
        # autocorrelation makes the effective count smaller than the count.
        'independent': bool(independent),
        'note': note,
    }
