"""
Reader for the checked-in OxCal run in tests/fixtures/oxcal/ -- a real
analysis (Nenthorn, Otago; SHCal20; OxCal 4.4, default UniformSpanPrior and
resolution, no outlier model) exported from OxCal itself, so the app can be
held against the field-standard tool rather than only against its own maths.

Not named test_* on purpose: it is a helper, used by test_oxcal_agreement.py
and by tests/manual/verify_model.py.

Four files:
  *.oxcal      the model definition, exactly as OxCal ran it
  Raw.csv      one row per (element, distribution, calendar year): the full
               likelihood and posterior distributions at 5-year steps, in AD
  Table.csv    the summary ranges OxCal reports, unmodelled and modelled, at
               68.3% and 95.4%, also in AD
  *_c14.csv    the input dates

Note on Table.csv: its from/to columns are the ENVELOPE of the HPD region,
not one range per lobe. OxCal's own raw distribution for WK51189 is
AD 1293-1328 plus 1333-1393, which the table reports as 1291-1394. Compare
envelopes against it, or compare range-by-range against the raw export.
"""
import csv
import os
import re
import numpy as np

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'oxcal')
MODEL = os.path.join(FIXTURES, 'Nenthorn_sites_3.oxcal')
RAW = os.path.join(FIXTURES, 'Nenthorn_sites_3 Raw.csv')
TABLE = os.path.join(FIXTURES, 'Nenthorn_sites_3 Table.csv')
C14 = os.path.join(FIXTURES, 'Nenthorn_c14.csv')

# The model's own grouping, read off Nenthorn_sites_3.oxcal. Quarry and
# Rockshelter are sibling Sequences inside Sequence("Nenthorn"), so the model
# does order them: everything in Quarry before everything in Rockshelter.
FIRE_FEATURE_J = ['WK51189', 'WK50102', 'WK50105']
FIRE_FEATURE_K = ['WK50099', 'WK50100', 'WK50101']
FIRE_FEATURE_A = ['ANU55209', 'ANU55206']
QUARRY = FIRE_FEATURE_J
ROCKSHELTER = FIRE_FEATURE_K + FIRE_FEATURE_A


def key(name):
    """OxCal drops punctuation from lab codes: 'Wk-51189' -> 'WK51189'."""
    return re.sub(r'[^A-Za-z0-9]', '', name).upper()


def ad_to_calbp(ad):
    """OxCal reports AD; the app works in cal BP. No year zero is crossed here."""
    return 1950.0 - np.asarray(ad, dtype=float)


def as_ad(ranges):
    """cal BP (min, max) ranges -> AD (from, to)."""
    return [(1950.0 - hi, 1950.0 - lo) for lo, hi in ranges]


def measurements():
    """The input dates as the app's measurement dicts, with their phase."""
    phase_of = {**{d: 'ff_j' for d in FIRE_FEATURE_J},
                **{d: 'ff_k' for d in FIRE_FEATURE_K},
                **{d: 'ff_a' for d in FIRE_FEATURE_A}}
    out = []
    with open(C14, encoding='utf-8-sig') as fh:
        for row in csv.DictReader(fh):
            name = key(row['R_Date'])
            out.append({
                'id': name,
                'sample_name': row['R_Date'],
                'age_bp': float(row['BP']),
                'error_1sigma': float(row['Error']),
                'material_type': row['Material'],
                'curve_id': 'shcal20',
                'element_id': phase_of[name],
                'oxcal_phase': row['Phase'],
            })
    return out


def model_tree(ordered=True):
    """
    The .oxcal model as one of the app's model trees. `ordered=False` drops
    the Quarry-before-Rockshelter constraint (both branches inside one Phase),
    which is only for probing how much of OxCal's output that constraint
    explains -- the fixture's own model is the ordered one.
    """
    def el(eid, etype, name, children=None):
        return {'id': eid, 'element_type': etype, 'name': name,
                'tpq_age_bp': None, 'taq_age_bp': None, 'children': children or []}

    quarry = el('quarry', 'sequence', 'Quarry',
                [el('ff_j', 'phase', 'Fire_Feature_J')])
    rockshelter = el('rock', 'sequence', 'Rockshelter', [
        el('fires', 'phase', 'Fires', [
            el('ff_k', 'phase', 'Fire_Feature_K'),
            el('ff_a', 'phase', 'Fire_Feature_A'),
        ]),
    ])
    middle = [quarry, rockshelter] if ordered else [
        el('sites', 'phase', 'Sites', [quarry, rockshelter])
    ]
    return el('nenthorn', 'sequence', 'Nenthorn', [
        el('b_start', 'boundary', 'Start Nenthorn'),
        *middle,
        el('b_end', 'boundary', 'End Nenthorn'),
    ])


def distributions():
    """
    {name: {'likelihood': (calbp, prob), 'posterior': (calbp, prob)}}, each
    ascending in cal BP and normalised to integrate to 1.
    """
    grouped = {}
    with open(RAW, encoding='utf-8-sig') as fh:
        for row in csv.DictReader(fh):
            grouped.setdefault(key(row['name']), {}).setdefault(row['type'], []).append(
                (float(row['value']), float(row['probability']))
            )

    out = {}
    for name, kinds in grouped.items():
        out[name] = {}
        for kind, pairs in kinds.items():
            pairs.sort()
            calbp = ad_to_calbp([p[0] for p in pairs])[::-1]      # ascending cal BP
            prob = np.array([p[1] for p in pairs])[::-1]
            step = float(np.median(np.diff(calbp))) if len(calbp) > 1 else 5.0
            total = prob.sum() * step
            out[name][kind] = (calbp, prob / total if total > 0 else prob)
    return out


def table_ranges():
    """
    {name: {'unmodelled': {68: [(from_ad, to_ad)], 95: [...]}, 'modelled':
    {...}, 'label': ..., 'op': ...}} -- OxCal's own reported ranges, in AD.
    """
    out, current = {}, None
    with open(TABLE, encoding='utf-8-sig') as fh:
        rows = list(csv.reader(fh))
    for row in rows[2:]:
        if len(row) < 9:
            continue
        name = row[0].strip()
        cells = [c.strip() for c in row[1:9]]
        if name:
            parts = name.split(None, 1)          # "R_Date WK51189" -> op, label
            label = parts[1] if len(parts) > 1 else name
            current = key(label)
            if not any(cells):
                continue
            out[current] = {'unmodelled': {68: [], 95: []},
                            'modelled': {68: [], 95: []},
                            'label': label, 'op': parts[0]}
        if current is None or current not in out:
            continue
        for (which, conf), (a, b) in zip(
            (('unmodelled', 68), ('unmodelled', 95), ('modelled', 68), ('modelled', 95)),
            ((0, 1), (2, 3), (4, 5), (6, 7)),
        ):
            if cells[a] and cells[b]:
                out[current][which][conf].append((float(cells[a]), float(cells[b])))
    return out


def hpd_from_distribution(calbp, prob, confidence):
    """
    HPD ranges as [(min_calbp, max_calbp)] from a distribution on a regular
    grid. Uses bin EDGES, so a single-bin range gets the width the bin
    represents rather than zero.
    """
    step = float(np.median(np.diff(calbp))) if len(calbp) > 1 else 5.0
    mass = prob * step
    mass = mass / mass.sum()
    order = np.argsort(mass)[::-1]
    cut = int(np.searchsorted(np.cumsum(mass[order]), confidence)) + 1
    chosen = np.sort(order[:cut])

    ranges, start = [], chosen[0]
    for a, b in zip(chosen, chosen[1:]):
        if b != a + 1:
            ranges.append((calbp[start] - step / 2, calbp[a] + step / 2))
            start = b
    ranges.append((calbp[start] - step / 2, calbp[chosen[-1]] + step / 2))
    return ranges


def hpd_from_draws(draws, confidence, step=5.0):
    """The same, from MCMC draws binned on OxCal's own 5-year grid."""
    lo = np.floor(draws.min() / step) * step
    hi = np.ceil(draws.max() / step) * step + step
    counts, edges = np.histogram(draws, bins=np.arange(lo, hi + step, step), density=True)
    centres = (edges[:-1] + edges[1:]) / 2.0
    return hpd_from_distribution(centres, counts, confidence)


def envelope(ranges):
    return (min(lo for lo, _ in ranges), max(hi for _, hi in ranges))


def total_width(ranges):
    return sum(hi - lo for lo, hi in ranges)


def overlap(c1, p1, c2, p2, grid=None):
    """
    Overlapping-coefficient of two densities, 1.0 for identical. Compared on
    a shared grid, since the two come from different sources.
    """
    if grid is None:
        grid = np.arange(max(c1.min(), c2.min()), min(c1.max(), c2.max()), 1.0)
    a = np.interp(grid, c1, p1)
    b = np.interp(grid, c2, p2)
    step = grid[1] - grid[0]
    a = a / (a.sum() * step)
    b = b / (b.sum() * step)
    return float(np.minimum(a, b).sum() * step)
