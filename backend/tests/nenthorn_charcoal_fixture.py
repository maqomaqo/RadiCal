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
    structure is expressed) and its dates were more spread. That is why the
    agreement asserted in tests/test_charcoal_outlier.py is a bound of a few years
    rather than the sub-year agreement the other fixtures get -- though the gap has
    largely closed. See PHASE WIDTH below.

PHASE WIDTH: why the app's phases were narrower than OxCal's
-----------------------------------------------------------
DIAGNOSED and now ACTED ON, via exact_chain.MIN_SPAN_ERROR_FRACTION.

The app's modelled phases came out several times narrower than OxCal's, and not
because of anything specific to this fixture: it reproduced on the simplest model
available, tests/fixtures/oxcal_outlier (Shag River: ONE phase, sixteen dates, a
5% General prior), where our span was 4.2 years against OxCal's 19.3.

The cause is a DEGENERATE MODE in the published model. Under Bronk Ramsey 2009b
eq 12-16 an outlier's event age is free of its own measurement -- the offset
absorbs the difference -- so its likelihood gains a broad component that floors it
everywhere. The configuration "every date at one instant, every date an outlier"
then stops being astronomically unlikely: it costs q^K in prior but wins more back
from the floor, because at a common instant most dates sit far into their own tails
where the floor beats the inlier density by orders of magnitude. Two tells: a prior
of 0.001 was already enough to flip which region held the mass, and the answer was
then IDENTICAL from 0.001 to 0.1 -- saturation, not graded inference.

Demonstrated rather than argued: excluding spans below about ten years from our own
posterior reproduced OxCal's answer on Shag River to 0.47 years, against 2.51 with
the region left in. tests/test_charcoal_outlier.py runs that demonstration.

What was ruled out first, each by measurement:

  * the uniform-scatter exponent. Scaled on both fixtures; the response is a cliff
    (Shag River: K=16 gives 4.1, K=14.4 gives 4.5, K=12 jumps to 91.7), so no
    value reproduces OxCal.
  * our implementation. exact_chain agrees with the independent grid integration
    in tests/manual/oxcal_reference.py to 0.00 years on BOTH boundary marginals
    and every date, with and without the outlier mixture. Only the date marginals
    had ever been checked before.
  * our reading of the priors. Bronk Ramsey 2001 eq 5-7 and 2009a eq 26-30: the
    (span)^(-n) phase factor, the (t_m - t_a)^-(m-2) correction AND its sign, and
    the limits factor (which applies only when the outer boundaries carry
    chronological constraints, so not here). All three match what is implemented.
  * our reading of the outlier model. 2009b eq 12-16 put the offset INSIDE
    r(t_i + phi_i 10^u delta_i), so convolving in t is right, and eq 11 confirms u
    is "a scaling parameter common to the model as a whole" -- the shared scale
    services/gibbs.py samples.
  * the calibration likelihood. Per-date means agree with OxCal's exported
    likelihoods to 0.5 years, and their standard deviations and 68% widths to 0.3%.
  * tightening the outlier model itself, to lower the floor at source. The span
    does not respond until the model is effectively switched off (offsets capped
    at ~3 years), and then it overshoots; lighter tails do nothing at all
    (identical answers from T(5) to T(1000)). Meanwhile even a modest tightening
    triples the date error and doubles the outlier-probability error, so it would
    trade the agreement won elsewhere for a partial, unstable improvement.

What acting on it bought, across all five OxCal comparisons (rms of modelled dates
vs OxCal's, exact solver, before -> after):

    oxcal/shared_boundary   5.45 -> 1.73
    oxcal/ordered           6.45 -> 2.24
    oxcal/unordered         5.11 -> 1.06
    outlier/Shag            2.51 -> 2.51   (span unchanged at 4.2; see below)
    charcoal/Nenthorn       9.92 -> 8.08   (sampler 6.28 -> 4.56)

and on THIS fixture the sampler's span went from 10.6 years to 28.7 against
OxCal's 29.2. The improvement to the no-outlier fixtures was not expected and is
the most telling result: the narrow-span region was distorting models with no
outlier model at all.

The trade, stated plainly: at the calibrated fraction Shag River's own span stays
4.2 years against OxCal's 19.3. A larger fraction fixes that and damages the other
four. See MIN_SPAN_ERROR_FRACTION for the scan.

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
