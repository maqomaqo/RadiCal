"""
Durations and differences -- the quantities that are functions of SEVERAL
modelled ages at once, rather than of one.

What is here and why it is not just more of exact_chain.py
----------------------------------------------------------
Every number the solvers report until now is a MARGINAL: this date's calendar
age, that boundary's calendar age. A duration is not. "How long did Phase 2
last" is `older boundary - younger boundary`, and the two boundaries are not
independent -- the model requires one to be older than the other, and the same
dates hold them apart. Pairing up two marginals answers a different question,
and the clearest sign of it is that the answer is not even a duration: on a
single phase of six dates, pairing draws from the two boundary marginals puts
8.2% of them BELOW ZERO -- a phase that ends before it starts -- where the
joint posterior gives that no probability at all. (It is not simply "wider",
either: on that model the two spreads are within 3% of each other, and on a
two-phase model the joint is the wider of the two. The pairing is wrong in
shape, not in scale, which is why an eyeball check would not catch it.)

exact_chain.solve's own docstring warns about exactly this -- "a future caller
wanting, say, P(date A older than date B) must not read it off these". This
module is that future caller, and it is fed joint draws rather than marginals.

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

Both are on the same scale and are read the same way -- 1 means "the model
cannot tell these apart in time", 0 means "the model separates them cleanly" --
so one severity vocabulary serves both. The bands are the app's own reporting
confidences rather than round numbers picked to look decisive: 0.046 and 0.954
are the complements of the 95.4% (2 sigma) level, 0.317 and 0.683 those of the
68.3% (1 sigma) level, which are the same thresholds every HPD in this app is
quoted at. So "none" means separated at 2 sigma and "extensive" means
overlapping at 2 sigma, and neither is a new convention the reader has to
learn.

Monte Carlo, deliberately
-------------------------
These are all computed from draws, including on the exact path, and that is a
real qualification on numbers this app otherwise reports without any Monte
Carlo error at all. It is taken knowingly:

- Only the phase duration of a single bounded group is exactly computable
  cheaply (the joint of one adjacent boundary pair is already on hand as a
  matrix). A sequence duration couples the two ends of the chain, and a sample
  duration is the range of K conditionally-independent dates -- neither
  factorises, and both would cost an O(M^3) pass to get exactly.
- Mixing an exact phase duration with a sampled sample duration in one table,
  where the whole point is comparing the two, would put a systematic difference
  between them that has nothing to do with the archaeology.
- The draws on the exact path are INDEPENDENT draws from the true joint
  posterior (forward-filter/backward-sample over the same chain the solver
  already built -- see exact_chain._messages), not an MCMC chain, so the error
  is plain 1/sqrt(N) with no autocorrelation to reason about.

`draws` and `independent` are reported alongside the numbers rather than left
implicit, so the qualification travels with them.
"""
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.services.calibration import hpd_ranges_from_distribution

# Joint draws taken on the exact path and for a group calibration, where they
# are independent and therefore cost only their own arithmetic. The sampled
# path keeps whatever its own run gives it; see gibbs.run's `collect_draws`.
DEFAULT_DRAWS = 20000

# Most bins a reported duration is broken into. A duration posterior can be
# thousands of years wide, and quoting an HPD for it at the 5-year step a
# calendar date is quoted at would claim a resolution the draws do not have --
# at 20,000 draws over 4,000 years that is 25 draws a bin, which is noise
# masquerading as detail. The step widens instead, and is reported as `step` on
# every summary so a reader can see what resolution the answer is at.
#
# Forty rather than a couple of hundred, because of what an HPD does with a
# noisy histogram. The region is built by taking bins in order of density until
# the confidence is met, so bins sitting near the cut go in or out on their own
# Monte Carlo error and the region comes back in pieces. Measured on a
# two-phase model's gap at 20,000 draws: a 5-year step reported the 68% region
# as eight fragments ("85-90 (1.0%); 110-115 (1.0%); ...; 280-485 (60.5%)")
# where the posterior is one long-tailed hump. At 60 bins it was still in two
# pieces; at 40 both the 68% and the 95% region come back as one range. More
# draws do NOT fix it on their own -- the density really is flat where the cut
# falls, so which bins make it is ambiguous however many draws there are, which
# is why the answer is coarser bins rather than a longer run.
#
# Forty also lands a duration a couple of hundred years wide -- the ordinary
# case -- back on the five-year step exactly, so only a genuinely wide one is
# coarsened at all. This is the same failure exact_chain._reported_ranges
# rebins to fix, one step further along because these bins hold draws rather
# than an exact density.
MAX_BINS = 40

# The spread the bin width is taken from, as percentiles rather than the full
# min-to-max. Two draws in a hundred thousand can sit far out in a boundary's
# heavy tail, and taking the width from them would make the reported RESOLUTION
# a function of the most extreme draw -- so asking for more draws would coarsen
# the answer instead of sharpening it, which is backwards. Measured: from
# min/max, going from 20,000 to 60,000 draws moved the step from 10 years to 15.
SPREAD_PERCENTILES = (0.5, 99.5)
# ...but the bins still have to cover every draw, so the full range is allowed
# this multiple of MAX_BINS before the step widens to fit it.
MAX_BINS_WITH_TAILS = 6 * MAX_BINS

# Pairs of phases (or of dates) a difference table will hold before it stops
# being every pair and becomes only the consecutive ones. 300 is 25 dates,
# which is already a table nobody reads across; past that the full matrix is
# neither useful nor small.
MAX_PAIRS = 300

# Overlap probability -> word. Upper bound of each band, ordered; anything
# above the last is SEVERITY_TOP. See the module docstring for why these four
# numbers and not four rounder ones.
SEVERITY_BANDS: Tuple[Tuple[float, str], ...] = (
    (0.046, 'none'),
    (0.317, 'slight'),
    (0.683, 'moderate'),
    (0.954, 'substantial'),
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


def _reporting_step() -> float:
    """
    The finest step a duration is reported at: the same five years every
    calendar HPD in this app is reported at.

    Imported here rather than at module scope because exact_chain imports THIS
    module, and restating the number instead would let the two drift -- which
    is the failure exact_chain.REPORTING_STEP_YEARS' own docstring is about.
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

def _step_for(values: np.ndarray) -> float:
    """
    Bin width for a set of draws: the reporting step, widened until MAX_BINS
    covers the draws' central spread (and MAX_BINS_WITH_TAILS the whole of
    them), and always a whole multiple of the reporting step so two quantities
    on one page are quoted on compatible grids.
    """
    base = _reporting_step()
    low, high = np.percentile(values, SPREAD_PERCENTILES)
    bins = [float(high - low) / (MAX_BINS * base),
            float(values.max() - values.min()) / (MAX_BINS_WITH_TAILS * base)]
    return base * max(1.0, np.ceil(max(bins)))


def _smoothed(counts: np.ndarray) -> np.ndarray:
    """
    Bin counts under a [1, 2, 1]/4 kernel, with the ends held rather than
    padded with zeros so no mass leaves the support -- which matters here,
    since a duration piles up hard against zero.

    Why smooth at all: an HPD region is assembled bin by bin in order of
    density, so two adjacent bins straddling the cut are decided by their own
    sampling noise and the region comes back fragmented. Widening the bins
    (MAX_BINS) is most of the fix and this is the rest of it. It widens the
    reported region very slightly, which is the conservative direction for an
    uncertainty statement, and it touches only the region: the mean, median and
    standard deviation are all computed from the raw draws.
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


def summarise(values: Sequence[float]) -> Optional[Dict]:
    """
    Draws of a quantity measured in YEARS turned into the reported shape.

    Deliberately NOT the key names a calendar posterior uses
    (`posterior_mean`, `probability_distribution`, ...). A duration is not a
    date: converting it to BC/AD would be nonsense, and the app has three
    export formats and a report that all format calendar-age columns by name.
    Different names mean a duration cannot be fed to a calendar formatter by
    accident, which is the only reliable way to stop that happening.
    """
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return None

    step = _step_for(values)
    lo = np.floor(values.min() / step) * step
    hi = np.ceil(values.max() / step) * step + step
    edges = np.arange(lo, hi + step, step)
    counts, _ = np.histogram(values, bins=edges)
    centres = (edges[:-1] + edges[1:]) / 2.0
    smoothed = _smoothed(counts)
    density = smoothed / (smoothed.sum() * step) if smoothed.sum() > 0 else smoothed

    hpd_68 = _ranges(centres, smoothed, 0.683, step)
    hpd_95 = _ranges(centres, smoothed, 0.954, step)
    # Trimmed to where there is mass, but CONTIGUOUS across it -- an empty bin
    # in the middle is kept. The page re-derives its own HPD from this series
    # when the confidence selector moves (utils/hpd.hpdEnvelopeFromHistogram),
    # and that reads the bin width off the first two points, so a series with
    # holes punched in it would be measured against the wrong width.
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
    Independent draws from a discretised distribution by inverse CDF.

    Used for a group calibration, where the dates are independent by
    construction, so drawing each one separately IS drawing from the joint.
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
    return element.get('name') or (element.get('element_type') or 'element').title()


def _enclosures(root: Dict) -> Tuple[Dict[str, Tuple[str, str]], List[Dict]]:
    """
    (enclosing, groups).

    `enclosing` maps every element id to the INNERMOST pair of Boundary
    siblings that brackets it, as (older boundary id, younger boundary id) --
    oldest first, matching the tree's own sibling order. An element with no
    such pair anywhere above it is absent from the map.

    `groups` is one entry per consecutive pair of Boundary siblings:
    {'older', 'younger', 'elements'}, where `elements` are the non-Boundary
    children lying between them.

    This is the same claim rule model_tree._plan_element applies to dates when
    it builds its SpanGroups -- innermost pair wins -- rederived here rather
    than threaded through ModelPlan, because what a duration needs is the
    ELEMENT each pair brackets and a SpanGroup only records the dates.
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

      sequence_duration  first to last Boundary CHILD of a Sequence. Suppressed
                         when the Sequence has exactly those two boundaries and
                         nothing between them but one phase, because the phase
                         duration below is then the identical quantity under
                         another name.
      phase_duration     each consecutive pair of Boundary siblings, provided
                         something other than a Hiatus sits between them. A
                         pair with only a Hiatus between them is a GAP, and a
                         gap is reported as a difference rather than twice.
      sample_duration    each phase holding two or more dates. One date has no
                         span and zero dates have nothing to span.
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
            # Two boundaries with only a Hiatus (or nothing) between them: the
            # gap between the phases either side, which the difference table
            # reports as their separation. Reporting it here as well would
            # print one quantity twice under two names.
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

    A phase is comparable only if a pair of Boundaries brackets it: that
    bracket IS its modelled interval, and without one the model places no
    interval around it to compare. Two phases sharing BOTH boundaries are
    skipped -- they are bracketed identically, so the answer would be "they
    coincide exactly" for a structural reason rather than an inferred one.

    A pair sharing ONE boundary is kept and flagged `shared_boundary`. Its gap
    is exactly zero by construction (adjacent phases share their transition,
    which is the whole point of a Boundary between them), and saying that
    plainly is worth more than dropping the row and leaving the reader to
    wonder.
    """
    if not root:
        return [], None
    enclosing, _groups = _enclosures(root)

    dated: Dict[str, int] = {}
    for m in measurements:
        if m.get('element_id'):
            dated[m['element_id']] = dated.get(m['element_id'], 0) + 1

    # Reading order: depth first, siblings oldest first -- the order the
    # results table and every export already put the model in.
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

    `dates` are {'id', 'name'} in the order they should be reported. Cut down
    to consecutive pairs past MAX_PAIRS, the same way phases are.
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

    `draws` maps a parameter id (a date id or a boundary id) to an array of
    calendar ages in cal BP, one entry per joint draw. Every array must be the
    same length and must come from ONE set of draws -- that is the entire
    point; per-parameter marginals would give the wrong answer for every
    quantity here.
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
            # line, so their intersection runs from the younger of the two
            # older ends to the older of the two younger ends. Its length is
            # positive where they overlap and negative where they are apart --
            # one quantity covering both, which is why there is no separate
            # "gap" and "overlap" pair of columns to keep consistent.
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
            # How far the ordering is from settled: 1 when the posterior is a
            # coin flip on which came first, 0 when it is certain.
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


def report(root: Optional[Dict], measurements: List[Dict],
           draws: Dict[str, np.ndarray], independent: bool) -> Dict:
    """
    The whole durations-and-differences block for a modelled analysis, ready
    to store on Analysis.intervals.
    """
    duration_specs = plan_durations(root, measurements) if root else []
    difference_specs, note = (plan_phase_differences(root, measurements)
                              if root else ([], None))
    durations, differences = evaluate(duration_specs, difference_specs, draws)
    return _block(durations, differences, draws, independent, note)


def report_dates(dates: List[Dict], draws: Dict[str, np.ndarray]) -> Dict:
    """
    The same block for a group calibration: differences only.

    Dates laid out with no relationship asserted between them belong to no
    phase and no sequence, so there is no duration for them to have -- the
    table is the pairwise differences and nothing else.
    """
    specs, note = plan_date_differences(dates)
    _durations, differences = evaluate([], specs, draws)
    # Independent by construction: a group calibration asserts no relationship
    # between its dates, so drawing each from its own calibrated posterior IS
    # drawing from the joint.
    return _block([], differences, draws, True, note)


def _block(durations: List[Dict], differences: List[Dict],
           draws: Dict[str, np.ndarray], independent: bool,
           note: Optional[str]) -> Dict:
    sizes = [len(v) for v in draws.values()]
    return {
        'durations': durations,
        'differences': differences,
        'draws': min(sizes) if sizes else 0,
        # True when the draws are independent draws from the posterior (the
        # exact path, or a group calibration) rather than an MCMC chain, where
        # autocorrelation makes the effective count smaller than the count.
        'independent': bool(independent),
        'note': note,
    }
