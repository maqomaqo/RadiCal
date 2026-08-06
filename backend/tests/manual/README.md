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

or one check at a time: `phase`, `sequence`, `order`, `multiphase`, `nuts`.

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

## Known gap: OxCal's UniformSpanPrior

`bayesian.py` does not implement OxCal's `1/(b_1 - b_n)^(n-2)` span-prior
correction (OxCal analysis operations; Nicholls and Jones 2001), which keeps a
sequence's overall span prior independent of how many sub-groups it is divided
into. `verify_model.py sequence` reports the size of the effect: on the dates
it is around 1%, but it tightens the two OUTERMOST boundaries substantially
(95% width 597 → 420 yr on that fixture). So reported Start/End boundary
ranges are wider here than OxCal would give; the dates themselves are
unaffected. It applies only when a sequence has three or more boundaries.
