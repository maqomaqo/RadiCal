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
  * The app's span on this model no longer UNDERSHOOTS OxCal's 29.2 years, as it
    did on every model before the quadrature fix -- it overshoots, at 43-55
    depending on the solver. This is the one fixture where that happens, and the
    reason is the outright-outlier declaration rather than the phase model. That
    is why the agreement asserted in tests/test_charcoal_outlier.py is a bound of
    a few years rather than the sub-year agreement the other fixtures get. See
    PHASE WIDTH below for how far it is understood.

PHASE WIDTH: mostly RESOLVED, and it was a numerical bug
-------------------------------------------------------
The app's modelled phases were several times narrower than OxCal's, on every model
rather than just this one. The cause was a first-order error in the interval
quadrature, not the phase prior, not the outlier model, and not the sampler:
exact_chain._interval_integral summed grid cells y THROUGH o inclusive, so an
interval whose span is k*step received (k+1) cells of likelihood mass while the
(span)^(-K) factor used k*step. That is a factor of two per date at the smallest
span -- 2^K over a group, 256 for eight dates and 65536 for sixteen -- landing
exactly on the near-coincident configurations the span prior already favours.

How it was caught, since every earlier attempt missed it:

  * your Span() export. OxCal's Span() is E[max t - min t], which the boundary
    marginals cannot give (Jensen), and comparing the same quantity on both sides
    showed our tail reached where OxCal's did but with a spike at zero that OxCal
    lacked -- 85% of our mass below five years, with the median span exactly ONE
    GRID CELL. A spike at the grid step is a numerical signature.
  * refining the grid then moved the answer (span 5.81 -> 8.12 -> 12.16 as the
    step went 1.0 -> 0.5 -> 0.25), which a real feature of the model would not do.
  * a continuum reference with no quadrature at all -- three Gaussian likelihoods,
    so the interval integrals are exact error functions -- settled it. Truth 181.5
    years; the old form gave 126.4 at a ten-year step and converged only as the
    step vanished, the trapezoidal form 186.3 and then 181.9 at one year. Now
    pinned in tests/test_exact_chain.py's TestIntervalQuadrature.

Why it hid for so long: the brute-force checks in the suite and the "independent"
reference in tests/manual/oxcal_reference.py both used the same cumulative-sum
trick, so they agreed with the solver exactly and confirmed the ALGORITHM while
sharing its quadrature. Comparisons against OxCal could see the symptom but cannot
distinguish a discretisation error from a modelling difference -- which is why the
priors kept checking out against Bronk Ramsey 2001 eq 5-7 and 2009a eq 26-30 (they
were right) and why a minimum-span floor appeared to help (it excluded precisely
the region the artefact inflated).

What the correction bought, rms of modelled dates against OxCal's:

    oxcal/shared_boundary   5.45 -> 1.26
    oxcal/ordered           6.45 -> 2.51
    oxcal/unordered         5.11 -> 0.20
    outlier/Shag            2.51 -> 0.18   (span 4.2 -> 20.7, OxCal 19.3)
    charcoal/Nenthorn       9.92 -> 10.63  (span 10.6 -> 54.9, OxCal 29.2)

WHAT IS LEFT, on this fixture
-----------------------------
This is now the only fixture that disagrees, and it has flipped sign: the span
OVERSHOOTS at 54.9 years against OxCal's 29.2. That points at the degenerate mode,
which is unchanged by any of the above and remains diagnosed but not addressed:

Under Bronk Ramsey 2009b eq 12-16 an outlier's event age is free of its own
measurement -- the offset absorbs the difference -- so its likelihood gains a
broad, nearly flat component. That floors every flagged date's likelihood
everywhere, and "every date at the same instant, every date an outlier" stops
being astronomically unlikely: it costs q^K in prior but gains more back from the
floor. On Shag River a prior of 0.001 already flipped which region held the mass,
and the answer was then IDENTICAL from 0.001 to 0.1 -- saturation, the tell of an
artefact rather than a graded inference.

Ruled out as causes of that, each by measurement: the uniform-scatter exponent
(multiplicatively and additively -- the response is a cliff, and K-1 and K-2 are
both much worse); our reading of the priors (2001 eq 5-7, 2009a eq 26-30, all
matching); our reading of the outlier model (2009b eq 12-16 put the offset inside
r(t_i + phi_i 10^u delta_i), so convolving in t is right, and eq 11 confirms u is
shared across the model -- which services/gibbs.py samples); the calibration
likelihood (means within 0.5 years of OxCal's, widths within 0.3%); and tightening
the outlier model itself (the span does not respond until the model is effectively
off, and lighter tails do nothing at all).

A minimum-span floor was implemented for this and REVERTED -- see the git history
for commit 5e0aed3 and its revert. It was fitted rather than derived, it fired
hardest on models with no outlier at all, and it is now clear WHY it appeared to
work: it was masking the quadrature bug.

Practical consequence meanwhile: modelled positions are good (6.7 years rms from
OxCal on this fixture with the sampler, 10.6 with the exact solver) but the span is
too WIDE here rather than too narrow -- 43.3 sampled and 54.9 exact against OxCal's
29.2 -- so on a model with a date declared an outright outlier a boundary's HPD
from this app is wider than the same model's in OxCal. Everywhere else the two now
agree closely; the 4-year gap between the solvers on this fixture is the shared
outlier scale, which the sampler infers and the exact solver integrates out per
date (see exact_chain's module docstring), not the quadrature.
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
