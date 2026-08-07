"""
Reader for the OxCal 4.4 run in tests/fixtures/oxcal_outlier/ -- Shag River
Mouth, Otago: 16 dates in one Phase on SHCal20, with an outlier model active.

    Outlier_Model("General", T(5), U(0,4), "t")
    Outlier("General", 0.05)   on every date

NZ7756 at 970 +/- 47 BP against the other fifteen at 537-660 BP is the
outlier, and OxCal treats it as one: it reports agreement 5.3 for that date
against 71-151 for the rest, and drags its modelled range from AD 1044-1158
to AD 1394-1410.

Kept separate from oxcal_fixture.py rather than folded into it. That fixture
is three runs of ONE model whose only difference is the branch relationship,
and its helpers are built around that; this is a different site, a different
structure and an outlier model, and pretending the two are variants of one
thing would make both harder to read.

The `P` column
--------------
The summary table's P column is the one this fixture exists for, and it is
the INLIER probability, not the outlier probability -- NZ7756 reads 0.1 while
the fifteen sound dates read 98.5-99.4. Read the other way round it would say
the model considers the fifteen consistent dates outliers and the one wild
date sound, which is contradicted by everything else in the run: NZ7756 is
the date OxCal moved 300 years and scored 5.3 on. So the posterior outlier
probability is 100 - P: 99.9% for NZ7756, 0.6-1.5% for the rest against a 5%
prior.
"""
import csv
import os
import re

import numpy as np

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'fixtures', 'oxcal_outlier')
MODEL = os.path.join(FIXTURES, 'Shag_River.oxcal')
RAW = os.path.join(FIXTURES, 'Shag_River Raw.csv')
TABLE = os.path.join(FIXTURES, 'Shag_River Table.csv')

OUTLIER = 'NZ7756'
OUTLIER_PRIOR = 0.05
PHASE_ID = 'shag_phase'


def measurements():
    """The 16 dates, read from the .oxcal file itself so they cannot drift."""
    text = open(MODEL, encoding='utf-8-sig').read()
    out = []
    for name, age, error in re.findall(r'R_Date\("([^"]+)",\s*(\d+),\s*(\d+)\)', text):
        out.append({
            'id': name,
            'sample_name': name,
            'age_bp': float(age),
            'error_1sigma': float(error),
            'curve_id': 'shcal20',
            'element_id': PHASE_ID,
            'outlier_prior': OUTLIER_PRIOR,
        })
    return out


def model_tree():
    """One Phase between two Boundaries, as the .oxcal file has it."""
    def el(eid, etype, name, children=None):
        return {'id': eid, 'element_type': etype, 'name': name,
                'tpq_age_bp': None, 'taq_age_bp': None, 'children': children or []}

    return el('shag_seq', 'sequence', 'Shag River Mouth', [
        el('shag_start', 'boundary', 'Start'),
        el(PHASE_ID, 'phase', 'Start Phase 1'),
        el('shag_end', 'boundary', 'End'),
    ])


def distributions():
    """
    {name: {'likelihood': (calbp, density), 'posterior': (...)}} ascending in
    cal BP and normalised to integrate to 1.

    Only the dated samples are returned. The export also carries the outlier
    model's own T and U hyperparameters, which are not calendar distributions
    and would be meaningless converted as though they were.
    """
    wanted = {m['id'] for m in measurements()}
    grouped = {}
    with open(RAW, encoding='utf-8-sig') as fh:
        for row in csv.DictReader(fh):
            if row['name'] not in wanted:
                continue
            grouped.setdefault(row['name'], {}).setdefault(row['type'], []).append(
                (float(row['value']), float(row['probability'])))

    out = {}
    for name, kinds in grouped.items():
        out[name] = {}
        for kind, pairs in kinds.items():
            pairs.sort()
            calbp = (1950.0 - np.array([p[0] for p in pairs]))[::-1]
            prob = np.array([p[1] for p in pairs])[::-1]
            step = float(np.median(np.diff(calbp))) if len(calbp) > 1 else 5.0
            total = prob.sum() * step
            out[name][kind] = (calbp, prob / total if total > 0 else prob)
    return out


def table():
    """
    {name: {'agreement': A, 'inlier_percent': P, 'outlier_probability': 1-P/100,
            'modelled': {68: (from, to), 95: (from, to)}, 'unmodelled': {...}}}
    in AD, plus 'Amodel'/'Aoverall' under the key 'model'.
    """
    out = {}
    with open(TABLE, encoding='utf-8-sig') as fh:
        for row in csv.reader(fh):
            if len(row) > 12 and row[0].startswith('R_Date'):
                name = row[0].split(None, 1)[1]
                cells = [c.strip() for c in row[1:9]]
                out[name] = {
                    'unmodelled': {68: (float(cells[0]), float(cells[1])),
                                   95: (float(cells[2]), float(cells[3]))},
                    'modelled': {68: (float(cells[4]), float(cells[5])),
                                 95: (float(cells[6]), float(cells[7]))},
                    'agreement': float(row[10]),
                    'inlier_percent': float(row[12]),
                    'outlier_probability': 1.0 - float(row[12]) / 100.0,
                }
    text = open(TABLE, encoding='utf-8-sig').read()
    out['model'] = {
        'Amodel': float(re.search(r'Amodel ([\d.]+)', text).group(1)),
        'Aoverall': float(re.search(r'Aoverall ([\d.]+)', text).group(1)),
    }
    return out
