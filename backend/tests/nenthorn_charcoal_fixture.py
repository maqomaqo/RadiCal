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
    structure is expressed), and its dates are more spread (14.1 years against
    5-6). That difference is UNRESOLVED, and it is why the agreement asserted in
    tests/test_charcoal_outlier.py is a bound of a few years rather than the
    sub-year agreement the other fixtures get. See PHASE WIDTH below.

PHASE WIDTH: what is known about the one difference still open
--------------------------------------------------------------
The app's modelled phases come out several times narrower than OxCal's. It is
NOT specific to this fixture, to nesting, or to declared outliers -- it
reproduces on the simplest model available, tests/fixtures/oxcal_outlier (Shag
River: ONE phase, sixteen dates, a 5% General prior, no nesting and no ordering):

    boundary means      OxCal Start 560.4  End 541.1  span 19.3   spread 5.9
    exact solver              554.9      550.7        4.2         0.8
    MH/Gibbs                  553.8      549.7        4.1         0.8

Ruled out, each by measurement rather than argument:

  * the uniform-scatter EXPONENT. Scaled on both fixtures, and the response is a
    cliff rather than a gradient -- on Shag River K=16 gives a span of 4.1, K=14.4
    gives 4.5, and K=12 jumps to 91.7. No value reproduces 19.3. (That cliff is
    itself worth noting: a span posterior with no stable intermediate scale
    suggests the missing ingredient stabilises the span rather than just
    reweighting it.)
  * the calibration likelihood. Per-date means agree with OxCal's exported
    likelihoods to 0.5 years and their standard deviations and 68% widths to 0.3%.
  * the shared outlier scale. Implemented (gibbs.Model._build_outlier_tables) and
    validated against OxCal's own inferred hyperparameter; the width did not move.
  * the sampler. exact_chain integrates rather than sampling and agrees with the
    sampler to 0.1 years on the span, so the narrow answer is the target's, not a
    mixing failure.
  * the UniformSpanPrior groups correction. Shag River has two boundaries, so its
    exponent is zero and the correction is off there anyway.

So it is in the BASE phase/span prior, and OxCal's published documentation does
not contain that algebra: hlp_analysis_oper.html and hlp_analysis_detail.html
both only point at "equation 6 of Bronk Ramsey 2001 and equation 30 of Bronk
Ramsey 2009a", and the math_gi.htm page carries the formulas as images. Those two
equations are what is needed to settle it. Worth knowing while reading them: what
this app calls one UniformSpanPrior flag is two options in current OxCal
(UniformSpanLimits and UniformSpanGroups), and both are CORRECTIONS to that base
prior rather than the prior itself.

Practical consequence meanwhile: modelled positions are good (6.3 years rms from
OxCal on this fixture with the sampler) but reported RANGES are tighter than
OxCal's, so a boundary's HPD from this app is narrower than the same model's in
OxCal.
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
