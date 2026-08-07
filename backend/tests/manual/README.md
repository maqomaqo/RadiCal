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

`tests/fixtures/oxcal/` holds three actual OxCal 4.4 analyses of the same
eight dates (Nenthorn, Otago; SHCal20; default UniformSpanPrior and
resolution; no outlier model), each with its `.oxcal` definition and the full
exported distributions. They differ only in how the two branches relate:

- **ordered** — the original. `Quarry` and `Rockshelter` are sibling
  Sequences inside `Sequence("Nenthorn")`, carrying no boundaries of their own.
- **unordered** — both wrapped in one `Phase("Sites")`.
- **shared_boundary** — a `Boundary("Quarry to Rockshelter")` between them.

The UNMODELLED half is asserted in `tests/test_oxcal_agreement.py` and passes
tightly: distribution overlap 0.978-0.983, every reported range edge within 2
years, on eight bimodal dates.

**OxCal orders sibling groups even without boundaries.** Its `ordered` and
`shared_boundary` runs agree with each other (Fire_Feature_J at AD 1311-1350
in both; Amodel 96 and 95.5) while `unordered` differs (Amodel 106.6). So the
implied ordering and the explicit one are the same model to OxCal.

The MODELLED comparison then localises the app's error precisely:

| run | ordering? | width ratio | mean error vs OxCal | group separation (OxCal / app) |
|---|---|---|---|---|
| `unordered` | no | 1.01 | 3.4 yr rms | −0.1 / −0.2 yr |
| `ordered` | implied | 0.85 | 12.6 yr rms | 11.7 / 37.0 yr |
| `shared_boundary` | explicit | 0.75 | 25.3 yr rms | 13.2 / 57.3 yr |

With no ordering constraint the app reproduces OxCal to within 4 years. Add
one and it over-separates the groups by three to four times, satisfying the
constraint by pushing Quarry entirely into the early calibration lobe
(secondary-lobe mass 0.000-0.001) and Rockshelter into the late one
(0.27-0.77) — a chronological break at AD 1350 that is an artefact of the
SHCal20 wiggle, not of the data.

**This is a sampling failure, not a modelling one.** `shared_boundary` is two
groups sharing a boundary, exactly what `sequence_marginals` computes, so the
exact answer for the app's own model is available:

| | Quarry mean AD | Rockshelter mean AD | separation | Rockshelter late-lobe mass |
|---|---|---|---|---|
| OxCal 4.4 | 1335.7 | 1348.9 | 13.2 yr | 0.169 |
| exact reference | 1334.6 | 1354.1 | 19.4 yr | 0.262 |
| app (SMC) | ~1321 | ~1379 | 57.3 yr | 0.71 |

The maths agrees with OxCal to within about 5 years; the sampler does not.
Two things are ruled out: the boundary integration window (widening the
reference's to match the app's 0-5722 cal BP moves it *closer* to OxCal —
separation 17.1, late lobe 0.204), and the sampling budget (4x the draws
moves separation only 57.3 → 50.8 and rms 25.3 → 20.9). It moves the right
way with more draws, so SMC is mis-weighting the two modes rather than being
hard-trapped in one.

Note that `r_hat` 1.01 and `ess` in the hundreds-to-thousands are reported
throughout: all four chains agree on the same wrong answer, so convergence
diagnostics give no warning here. This is exactly the multimodality SMC was
chosen over NUTS to handle (see `BayesianChronology.sample`), failing when a
hard ordering constraint is layered on top of it.

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
