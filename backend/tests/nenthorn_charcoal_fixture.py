"""
Reader for the OxCal 4.4 run in tests/fixtures/oxcal_charcoal/ -- Nenthorn,
Otago, eleven dates on SHCal20 with BOTH outlier models active:

    Outlier_Model("General",  T(5),         U(0,4), "t")
    Outlier_Model("Charcoal", Exp(1,-10,0), U(0,3), "t")

    Sequence("Nenthorn")
      Boundary("Start Nenthorn")
      Sequence("Quarry")       -> Phase("Fire_Feature_J")    4 dates
      Sequence("Rockshelter")  -> Phase("Fires")
                                    Phase("Fire_Feature_K")  4 dates
                                    Phase("Fire_Feature_A")  3 dates
      Boundary("End Nenthorn")

Why a third OxCal fixture. tests/fixtures/oxcal covers ordering on a wiggly
curve with no outlier model; tests/fixtures/oxcal_outlier covers the GENERAL
outlier model on a single phase. Neither exercises the CHARCOAL model against
OxCal at all -- so the one-sided kernel was only ever checked for its direction
and against the general model, never against the thing it claims to implement.
That is how it came to use a single fixed 200-year scale where OxCal draws the
scale over three decades.

The oldest date in each of the three phases carries Outlier("Charcoal", 1.00) --
declared residual outright, not merely suspected -- which is what makes this run
sensitive to the charcoal kernel's shape: at prior 1.0 the mixture IS the
outlier component, so nothing else moderates it.

Two structural notes, both mattering when comparing:

  * There is no Boundary between Quarry and Rockshelter, so the model is not a
    chain and exact_chain declines it. The app supplies the transition the
    ordering implies (model_tree.with_implicit_boundaries), which is a real
    change to the model -- it splits one span term over 11 dates into two over 4
    and 7.
  * OxCal's own span is wider than anything the app produces on this model (29.2
    years between the boundary means, against 9-20 depending on how the
    structure is expressed). That is a separate, unresolved difference in how
    the uniform-scatter exponent is counted for NESTED groups, and it is why the
    agreement asserted in tests/test_charcoal_outlier.py is a bound of about ten
    years rather than the sub-year agreement the other fixtures get.
"""
import csv
import os
import re

import numpy as np

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'fixtures', 'oxcal_charcoal')
MODEL = os.path.join(FIXTURES, 'Nenthorn_Seq.oxcal')
RAW = os.path.join(FIXTURES, 'Nenthorn_Seq Raw.csv')

# The oldest date in each phase, declared residual with certainty.
CHARCOAL = ('WK50104', 'WK50098', 'ANU55207')
# Which phase each date sits in, in the .oxcal file's own reading order.
PHASES = {
    'WK50104': 'J', 'WK51189': 'J', 'WK50102': 'J', 'WK50105': 'J',
    'WK50098': 'K', 'WK50099': 'K', 'WK50100': 'K', 'WK50101': 'K',
    'ANU55207': 'A', 'ANU55209': 'A', 'ANU55206': 'A',
}
BOUNDARIES = ('Start Nenthorn', 'End Nenthorn')


def measurements():
    """
    The eleven dates with their outlier settings, read from the .oxcal file so
    they cannot drift from the run they are compared against.
    """
    text = open(MODEL, encoding='utf-8-sig').read()
    # Each R_Date is followed by its own Outlier(model, prior).
    pattern = re.compile(
        r'R_Date\("([^"]+)",\s*(\d+),\s*(\d+)\)\s*\{\s*'
        r'Outlier\("([^"]+)",\s*([\d.]+)\)', re.S)
    out = []
    for name, age, error, model, prior in pattern.findall(text):
        out.append({
            'id': name,
            'sample_name': name,
            'age_bp': float(age),
            'error_1sigma': float(error),
            'curve_id': 'shcal20',
            'material_type': 'charcoal',
            'sample_type': 'terrestrial',
            'element_id': PHASES[name],
            'outlier_model': model.lower(),
            'outlier_prior': float(prior),
        })
    return out


def model_tree():
    """The .oxcal nesting, element for element."""
    def el(eid, etype, name, children=None):
        return {'id': eid, 'element_type': etype, 'name': name,
                'tpq_age_bp': None, 'taq_age_bp': None, 'children': children or []}

    return el('nenthorn', 'sequence', 'Nenthorn', [
        el('bstart', 'boundary', 'Start Nenthorn'),
        el('quarry', 'sequence', 'Quarry', [
            el('J', 'phase', 'Fire_Feature_J')]),
        el('rock', 'sequence', 'Rockshelter', [
            el('fires', 'phase', 'Fires', [
                el('K', 'phase', 'Fire_Feature_K'),
                el('A', 'phase', 'Fire_Feature_A')])]),
        el('bend', 'boundary', 'End Nenthorn'),
    ])


def distributions():
    """
    {name: {'likelihood': (calbp, density), 'posterior': (...)}} ascending in
    cal BP, normalised to sum to one.

    Dates and boundaries only. The export also carries the two outlier models'
    own T/U/Exp hyperparameter distributions, which are offsets and scales
    rather than calendar ages and would be meaningless converted as though they
    were.
    """
    wanted = {m['id'] for m in measurements()} | set(BOUNDARIES)
    grouped = {}
    with open(RAW, encoding='utf-8-sig') as fh:
        for row in csv.DictReader(fh):
            if row['op'] not in ('R_Date', 'Boundary') or row['name'] not in wanted:
                continue
            grouped.setdefault(row['name'], {}).setdefault(row['type'], []).append(
                (float(row['value']), float(row['probability'])))

    out = {}
    for name, kinds in grouped.items():
        out[name] = {}
        for kind, pairs in kinds.items():
            pairs.sort()
            # The export is in AD/BC; everything in this app is cal BP.
            calbp = (1950.0 - np.array([p[0] for p in pairs]))[::-1]
            prob = np.array([p[1] for p in pairs])[::-1]
            total = prob.sum()
            out[name][kind] = (calbp, prob / total if total > 0 else prob)
    return out


def posterior_means():
    """{name: OxCal's modelled posterior mean, cal BP} for dates and boundaries."""
    dists = distributions()
    return {name: float((cal * prob / prob.sum()).sum())
            for name, kinds in dists.items()
            for cal, prob in [kinds['posterior']]}
