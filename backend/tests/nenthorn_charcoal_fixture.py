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
    5-6). That is why the agreement asserted in tests/test_charcoal_outlier.py is
    a bound of a few years rather than the sub-year agreement the other fixtures
    get. See PHASE WIDTH below for how far that is understood.

PHASE WIDTH: why the app's phases are narrower than OxCal's
----------------------------------------------------------
PARTLY DIAGNOSED. One fix was implemented and REVERTED -- see "the attempt" below,
which is the more useful half of this note.

The app's modelled phases come out several times narrower than OxCal's. It is not
specific to this fixture, to nesting, or to declared outliers: it reproduces on
the simplest model available, tests/fixtures/oxcal_outlier (Shag River: ONE phase,
sixteen dates, a 5% General prior, no nesting and no ordering), where our span is
4.2 years against OxCal's 19.3 and our dates spread 0.8 against 5.9.

A degenerate mode accounts for part of it. Under Bronk Ramsey 2009b eq 12-16 an
outlier's event age is free of its own measurement -- the offset absorbs the
difference -- so its likelihood gains a broad, nearly flat component. That floors
every flagged date's likelihood everywhere, and "every date at the same instant,
every date an outlier" stops being astronomically unlikely: it costs q^K in prior
but gains more back from the floor, because at a common instant most dates sit far
into their own tails where the floor beats the inlier density by orders of
magnitude. On Shag River a prior of 0.001 already flips which region holds the
mass, and the answer is then IDENTICAL from 0.001 to 0.1 -- saturation, the tell
of an artefact rather than a graded inference.

Ruled out as causes, each by measurement:

  * the uniform-scatter exponent, multiplicatively AND additively. The response is
    a cliff (Shag River: K=16 gives 4.1, K=14.4 gives 4.5, K=12 jumps to 91.7), and
    K-1 and K-2 -- the forms Bronk Ramsey 2001's "combinations proportional to
    s^(n-2)" might suggest -- are much worse (shared_boundary 5.45 -> 7.88 -> 15.29).
  * our implementation of the phase model. exact_chain agrees with the independent
    grid integration in tests/manual/oxcal_reference.py to 0.00 years on BOTH
    boundary marginals and every date, with and without the outlier mixture. That
    check had never been run on the boundaries before.
  * our reading of the priors. Bronk Ramsey 2001 eq 5-7 and 2009a eq 26-30 were
    obtained and checked: the (span)^(-n) phase factor, the (t_m - t_a)^-(m-2)
    correction and its sign, and the limits factor (which applies only when the
    outer boundaries carry chronological constraints, so not here). All match.
  * our reading of the outlier model. 2009b eq 12-16 put the offset INSIDE
    r(t_i + phi_i 10^u delta_i), so convolving in t is right, and eq 11 confirms u
    is "a scaling parameter common to the model as a whole" -- the shared scale
    services/gibbs.py samples.
  * the calibration likelihood. Per-date means agree with OxCal's exported
    likelihoods to 0.5 years and their standard deviations and 68% widths to 0.3%.
  * the sampler. exact_chain integrates rather than sampling and agrees with the
    sampler to 0.1 years on the span, so both find the same narrow answer.
  * tightening the outlier model to lower the floor at source. The span does not
    respond until the model is effectively off (offsets capped near 3 years) and
    then it overshoots; lighter tails do nothing at all (identical from T(5) to
    T(1000)); and even a modest tightening triples the date error and doubles the
    outlier-probability error.

THE ATTEMPT, and why it was reverted
------------------------------------
A minimum phase span was implemented, set to a fraction of the median calendar
precision of the dates in the group, and calibrated at 0.10. It improved all five
OxCal comparisons (rms of modelled dates: 5.45 -> 1.73, 6.45 -> 2.24, 5.11 -> 1.06,
2.51 -> 2.51, 9.92 -> 8.08) and took this fixture's span from 10.6 to 28.7 against
OxCal's 29.2. It was still wrong, for reasons worth not rediscovering:

  * it did not remove an unresolvable region, it changed the inference. A 2.9-year
    floor tripled a 12.7-year span. Had it only been discarding configurations the
    data cannot distinguish, the answers would not have moved.
  * it fired hardest where there is no pathology. The three fixtures with NO
    outlier model have no floored likelihood and so no degenerate mode, yet they
    improved the most (3-5x). On those models it was not implementing the
    diagnosis at all -- it was compensating for the same unexplained narrowness by
    a different route.
  * no missing algebra could justify it. The exponent tests above rule out a
    factor the floor might have been standing in for, and eq 27 matches what is
    implemented, so the floor was an ADDITION to OxCal's model rather than an
    implementation of it.
  * it encodes something false as a prior. A near-instantaneous phase is
    physically possible (a single depositional event); "we cannot resolve it" is a
    statement about inference and belongs in the reporting.
  * it made the prior data-dependent: re-dating the same site more precisely would
    change the prior, and so the inference about the same reality.
  * it was fitted and fragile. Per-fixture optima ranged 0.05-0.25, the response
    to the constant was a cliff rather than a gradient, and five comparisons with
    one free parameter is a thin validation set. Models outside the fixtures moved
    in both directions, which is what overfitting looks like.
  * it degraded uniform_span_prior=False (4.17 -> 5.86). A real correction should
    help both settings; one tuned against a single setting helps only that one.

So the honest state is: the diagnosis above is sound, it is incomplete, and the
narrowness on models with NO outlier model is the open question. That is a
well-posed target -- our span posterior is hypersensitive near zero on every model
-- and it is where the next attempt should start, not at a threshold.

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
