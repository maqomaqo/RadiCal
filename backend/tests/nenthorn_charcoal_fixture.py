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
This is the only fixture that still disagrees, and it has flipped sign: the span
OVERSHOOTS, at 43.3 sampled and 54.9 exact against OxCal's 29.2.

The "declared-outlier degeneracy" this note used to blame is NOT the cause, and
the evidence for it did not survive the quadrature fix. That mechanism -- every
date at the same instant, every date an outlier, paid for by the broad component's
floor -- predicts spans that are too NARROW, which is the opposite of the symptom
now, and the saturation that was its main evidence was measured while the
quadrature bug was inflating exactly the coincident configurations it described.
Three further measurements rule it out directly:

  * the response is MONOTONE and in the helpful direction. Sweeping the three
    declared dates' prior 0.00 -> 0.05 -> 0.20 -> 0.50 -> 1.00 gives spans of
    169.2, 148.5, 115.2, 81.3, 54.9. The outlier model is doing real work here and
    the best answer is at the maximum absorption the prior allows -- there is no
    saturation and no flip. Pinned in test_charcoal_outlier.py.
  * both offset scales are inferred correctly. OxCal's exported U() posteriors are
    mean 1.79 (Charcoal, 68% 1.49-2.06) and 1.81 (General, 68% 0.50-3.20 -- barely
    constrained); the sampler gets 1.797 (1.443-2.034) and 1.803 (0.365-3.196).
  * the flagged dates are not the ones misbehaving. Their marginals are no wider
    relative to OxCal's than their own phase-mates' under the general model
    (WK50098 14.6 -> 20.1 against WK50099 14.2 -> 18.6).

PART OF IT WAS THE LIMITS FACTOR, measured directly: with OxCal's UniformSpanPrior
off this model's span is 36.91 years rather than its default 29.24, against the 43.3
(sampler) and 51.4 (exact) we gave before implementing the factor. Now 40.3 exact,
and the separation excess falls from +3.85 to +1.31 on the sampler. What remains is
the exact solver's per-date treatment of the outlier scale, and the band-by-band
young tail described next -- that being the remainder, not the whole of it.

What the rest of the discrepancy IS, measured band by band. Every one of the eleven
marginals is about a third too wide (1.34x OxCal's sd, on a common window), and
the excess is a specific YOUNG TAIL that OxCal does not have, with a matching
deficit in the oldest lobe. Averaged over the seven Rockshelter dates:

    cal BP band     OxCal   gibbs   exact
    540-580         0.028   0.100   0.246
    620-640         0.406   0.296   0.274

That tail is the charcoal kernel's one-sided smear -- correct in direction and
scale -- and what should resist it is the span prior: a residual date wandering
young has to drag a boundary with it, and (span)^(-K) makes that expensive. The
resistance is what differs, which is why the exact solver is 2.5x worse than the
sampler here rather than equally wrong: it can only solve this model after
with_implicit_boundaries splits one span term over ELEVEN dates into two over four
and seven, and K is the exponent doing the resisting. Same cause, smaller, on
tests/fixtures/oxcal's `ordered` run: 2.51 years rms patched against the sampler's
1.49 on the model as authored.

A SECOND, independent gap sits underneath, and it has nothing to do with outliers:
any two ORDERED groups come out further apart than OxCal puts them. Separation of
Quarry from Rockshelter, no outlier model anywhere, before and after the
UniformSpanPrior correction was fixed --

    run               OxCal   exact         gibbs
    unordered          -0.1     0.0          -0.1
    ordered            11.7    15.8 -> 14.7  14.0 -> 14.2
    shared_boundary    13.2    15.8 -> 14.7  16.5 -> 16.3

Unordered is exact. Part of the rest was a first-order discretisation error in the
correction -- it divided by a continuum power where the discrete model needs a
binomial count, see exact_chain._span_prior_correction -- worth about 1.1 years
here and pinned now by TestTheCorrectionIsExact against a closed form.

MOST OF WHAT SURVIVED THAT IS NOW FIXED, and it was not about ordering at all: it
was the UniformSpanPrior limits factor, which we did not implement (Bronk Ramsey
2008 A.31, 2009a eq 29) and now do. Asking OxCal to run these same models with the
option OFF is what identified it, by bringing the two into line --

    fixture           OxCal option OFF   ours (no such term)
    ordered                     13.47    14.18 (sampler)
    shared_boundary             26.30    26.7  (both refinements off)

-- against OxCal's defaults of 11.66 and 13.19: the app was reproducing OxCal with
one of OxCal's own refinements switched off, on every model where the phase prior
does real work. With the factor in, the separations are 12.4 and 14.0 against those
defaults, and this fixture's own gap falls from +3.85 to +1.31. See
exact_chain._span_prior_correction for the term and for why the sign of it is
subtle, and pair_limits for where the limits come from.

OxCal is not the one that is wrong, which was worth checking and is now checked:
raising its kIterations from the default 30 to 60 moves its answer 0.23 years while
its reported convergence rises 95.7 -> 97.1.

Two things measured along the way that any further work here should start from:

  * the sampler is faithful to the target it states. On the `ordered` shape, which
    exact_chain declines and which therefore had no oracle at all, the entire
    discrete posterior was enumerated directly -- every (b_older, b_younger, t_A,
    t_B) state -- and gibbs matches it to 0.25 years, converging under refinement.
  * the two solvers nevertheless disagree by an amount that GROWS with the number
    of boundaries, and it is not sampling noise: on four ordered phases the gap
    against exact_chain widens from +1.87 to +2.57 years as the sampler goes from
    30k to 400k sweeps, i.e. it converges to a different answer. Present before the
    correction was fixed and unchanged by it. Undiagnosed, and the reason the
    sampler is the worse of the two on shared_boundary while being much the better
    on this fixture.

Ruled out as causes, each by measurement: the uniform-scatter exponent
(multiplicatively and additively -- the response is a cliff, and K-1 and K-2 are
both much worse); our reading of the priors (2001 eq 5-7, 2009a eq 26-30, all
matching); our reading of the outlier model (2009b eq 12-16 put the offset inside
r(t_i + phi_i 10^u delta_i), so convolving in t is right, and eq 11 confirms u is
shared across the model -- which services/gibbs.py samples, correctly, per model);
the calibration likelihood (means within 0.5 years of OxCal's, widths within
0.5%); tightening the outlier model itself; and the implicit boundary as the sole
cause (the sampler on the model as authored still overshoots, 44.1 against 43.3
patched).

A minimum-span floor was implemented for this and REVERTED -- see the git history
for commit 5e0aed3 and its revert. It was fitted rather than derived, it fired
hardest on models with no outlier at all, and it is now clear WHY it appeared to
work: it was masking the quadrature bug.

A TRAP, if you measure widths here
----------------------------------
Do not compare the standard deviation of our exported curve against OxCal's
without putting both on a common window first. OxCal exports about 250 years
around each parameter; exact_chain._plottable trims at 1e-4 of the peak. For a
date with a broad outlier component those two rules keep very different amounts of
tail, and the difference is larger than the effect being measured -- on Shag River
it manufactures a 30% discrepancy that does not exist (0.70x on their own
supports, 0.97x on a common one). Comparing the unmodelled LIKELIHOODS does not
catch this, because they have no broad component and agree to 0.5% either way.
This is the same shape of error as the brute-force checks that shared the solver's
quadrature: a reference that differs from the thing it is checking in a way the
check cannot see.

Practical consequence meanwhile: modelled positions are good (4.5 years rms from
OxCal with the sampler on the model as authored, 6.7 patched, 10.6 exact) but on a
model with a date declared an outright outlier a boundary's HPD from this app is
WIDER than the same model's in OxCal, and the sampler is the one to prefer.
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
