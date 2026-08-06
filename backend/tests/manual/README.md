# Manual model verification

Not collected by pytest — every check here runs a real MCMC and takes minutes.
The fast regression guards that came out of these checks live in
`tests/test_bayesian.py` (`TestSpanTermsArePresent`, `TestSpanTermCannotDiverge`).

- `oxcal_reference.py` — an exact, MCMC-free reference for the OxCal
  uniform-phase model, by direct numerical integration over the boundaries.
  Independent of `app/services/bayesian.py` by construction, so agreement
  between them is evidence about the model rather than a shared bug.
- `verify_model.py` — drives the reference against what `bayesian.py` actually
  samples and prints the comparison.

Run inside the backend container:

```bash
docker compose exec backend python tests/manual/verify_model.py all
```

or one check at a time: `phase`, `sequence`, `order`, `multiphase`, `nenthorn`,
`nuts`.

**Reading the output.** Look for `app vs reference: width ratio` near 1.0 and a
small `max |mean diff|`. On `multiphase`, also check `worst r_hat` (should be
≤ ~1.05) and that the `adjacent sampled-boundary gaps` do not approach zero —
a collapsing gap is the signature of the divergence documented at
`_MIN_GROUP_SPAN_YEARS` in `app/services/bayesian.py`.

Reference figures at the time of writing, for the model as it now stands:

| check | width ratio | max mean diff | worst r_hat |
|---|---|---|---|
| `phase` (1 phase, 4 dates) | 1.03 | 5 yr | — (no boundary sampled) |
| `sequence` (2 phases) | 0.97 | 5 yr | — (one boundary sampled) |
| `order` (pure ordering) | 1.01 | <1 yr | 1.0 |
| `multiphase` (3 phases × 4 dates) | 0.94 | 21 yr | 1.02 |

## `nenthorn`: against a real OxCal 4.4 run

`tests/fixtures/oxcal/` holds an actual OxCal analysis (Nenthorn, Otago;
SHCal20; default UniformSpanPrior and resolution; no outlier model), including
the `.oxcal` model definition and the full exported distributions. The
UNMODELLED half of that comparison is asserted in
`tests/test_oxcal_agreement.py` and passes tightly: distribution overlap
0.978-0.983 and every reported range edge within 2 years, on eight bimodal
dates.

The MODELLED half does not yet agree, and `nenthorn` is where that is
measured. The model is a Sequence holding two sibling Sequences (Quarry, then
Rockshelter), so it orders every Quarry date before every Rockshelter date.
Run as written, and with that ordering dropped:

| | width ratio | overlap | secondary lobe (AD 1370-1420) |
|---|---|---|---|
| OxCal 4.4 | — | — | 0.126 |
| app, ordered (as the file defines it) | 0.85 | 0.72 | 0.180 |
| app, ordering dropped | 1.05 | 0.77 | 0.056 |

Both app runs converge cleanly (r_hat 1.01, ess > 1500), so this is not a
sampling failure. The ordered run satisfies the constraint by pushing Quarry
entirely into the early calibration lobe (secondary-lobe mass 0.000-0.001) and
Rockshelter largely into the late one (0.27-0.32) — a chronological break at
AD 1350 that is an artefact of the calibration wiggle, not of the data. OxCal
instead keeps both groups spanning both lobes with only about 11 years of
separation between their means, which is a much weaker effect than a hard
3-before-5 constraint produces.

So OxCal 4.4 appears to order sibling groups that carry no Boundary of their
own far more weakly than this app's hard constraint does, and how it does that
is not established here. The experiment that would settle it is the same model
re-run in OxCal with the two branches explicitly unordered (both inside one
`Phase`): if that output matches what is in the fixture, OxCal was not
enforcing the ordering at all.

## If you write another cross-check

Two traps, both found the hard way:

- **Don't floor `(max - min)` at a tiny epsilon on a discrete grid.** The
  `(max - min)^(2-K)` factor is integrable in the continuum because the
  coincident-dates set has measure zero, but on a grid that set is a real
  cell, and a `1e-9` floor hands it a weight of `1e9^(K-2)` that swamps the
  whole integral — every date's marginal then comes back identical. Floor at
  the grid spacing. The same spike is a real attractor for samplers: a
  Metropolis chain started with all dates coincident never leaves.
- **Match the boundary integration window** to the one `bayesian.py` used
  (`BayesianChronology.model_window`) before comparing. `bayesian.py` takes
  the wide-window limit for a two-sided marginalized group; integrating over
  a different finite window shifts the answer by a few years at K = 6-8.

`phase_marginals` has been checked against exhaustive K-dimensional
integration of the same target at K = 3, 4 and 6 (agreeing to 1-2 years and
to 0.02 in tail mass), and against an independent random-walk Metropolis
chain, so it can be trusted at the group sizes tested here.

## Known gap: OxCal's UniformSpanPrior

`bayesian.py` does not implement OxCal's `1/(b_1 - b_n)^(n-2)` span-prior
correction (OxCal analysis operations; Nicholls and Jones 2001), which keeps a
sequence's overall span prior independent of how many sub-groups it is divided
into. `verify_model.py sequence` reports the size of the effect: on the dates
it is around 1%, but it tightens the two OUTERMOST boundaries substantially
(95% width 597 → 420 yr on that fixture). So reported Start/End boundary
ranges are wider here than OxCal would give; the dates themselves are
unaffected. It applies only when a sequence has three or more boundaries.
