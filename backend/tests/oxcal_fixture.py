"""
Reader for the checked-in OxCal runs in tests/fixtures/oxcal/ -- a real
analysis (Nenthorn, Otago; SHCal20; OxCal 4.4, default UniformSpanPrior and
resolution, no outlier model) exported from OxCal itself, so the app can be
held against the field-standard tool rather than only against its own maths.

Not named test_* on purpose: it is a helper, used by test_oxcal_agreement.py
and by tests/test_gibbs.py.

THREE runs of the same eight dates, differing only in how the two branches
(Quarry and Rockshelter) relate to each other:

  ordered          the original. Quarry and Rockshelter are sibling Sequences
                   inside Sequence("Nenthorn") and carry no boundaries of
                   their own, so the ordering is implied by the structure.
  unordered        both branches wrapped in one Phase("Sites"), so nothing
                   orders them.
  shared_boundary  a Boundary("Quarry to Rockshelter") added between them, so
                   the transition is one explicit shared parameter.

`ordered` and `shared_boundary` come out nearly identical (Fire_Feature_J at
AD 1311-1350 in both; Amodel 96 and 95.5), while `unordered` differs
(Amodel 106.6). That is what establishes that OxCal DOES order sibling groups
carrying no boundaries of their own -- the implied ordering and the explicit
one agree, and dropping the ordering changes the answer.

Each run has four files:
  *.oxcal      the model definition as OxCal actually ran it
  * Raw.csv    one row per (element, distribution, calendar year): the full
               likelihood and posterior distributions at 5-year steps, in AD
  * Table.csv  the summary ranges OxCal reports, unmodelled and modelled, at
               68.3% and 95.4%, also in AD
plus one shared Nenthorn_c14.csv holding the input dates.

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
C14 = os.path.join(FIXTURES, 'Nenthorn_c14.csv')

# run name -> the stem its three files share
RUNS = {
    'ordered': 'Nenthorn_sites_3',
    'unordered': 'Nenthorn_sites_3_unordered',
    'shared_boundary': 'Nenthorn_sites_3_shared_boundary',
}
DEFAULT_RUN = 'ordered'


def paths(run=DEFAULT_RUN):
    """(model, raw, table) file paths for one run."""
    stem = RUNS[run]
    return (os.path.join(FIXTURES, f'{stem}.oxcal'),
            os.path.join(FIXTURES, f'{stem} Raw.csv'),
            os.path.join(FIXTURES, f'{stem} Table.csv'))


# The dates' grouping, read off the .oxcal files -- identical in all three
# runs; only the relationship between Quarry and Rockshelter changes.
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


def model_tree(run=DEFAULT_RUN):
    """
    One of the three .oxcal models as the app's model tree. Element names
    match the .oxcal files, which test_oxcal_agreement checks.
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
    if run == 'ordered':
        middle = [quarry, rockshelter]
    elif run == 'unordered':
        middle = [el('sites', 'phase', 'Sites', [quarry, rockshelter])]
    elif run == 'shared_boundary':
        middle = [quarry,
                  el('b_mid', 'boundary', 'Quarry to Rockshelter'),
                  rockshelter]
    else:
        raise ValueError(f'unknown run {run!r}')
    return el('nenthorn', 'sequence', 'Nenthorn', [
        el('b_start', 'boundary', 'Start Nenthorn'),
        *middle,
        el('b_end', 'boundary', 'End Nenthorn'),
    ])


def distributions(run=DEFAULT_RUN):
    """
    {name: {'likelihood': (calbp, prob), 'posterior': (calbp, prob)}}, each
    ascending in cal BP and normalised to integrate to 1.
    """
    grouped = {}
    with open(paths(run)[1], encoding='utf-8-sig') as fh:
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


def table_ranges(run=DEFAULT_RUN):
    """
    {name: {'unmodelled': {68: [(from_ad, to_ad)], 95: [...]}, 'modelled':
    {...}, 'label': ..., 'op': ...}} -- OxCal's own reported ranges, in AD.
    """
    out, current = {}, None
    with open(paths(run)[2], encoding='utf-8-sig') as fh:
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
