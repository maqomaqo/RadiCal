# Manual model verification

**Historical.** This directory recorded the investigation into the PyMC/SMC
sampler, which has since been removed: a chain-shaped model is now integrated
exactly by `app/services/exact_chain.py`, and anything else is sampled by
`app/services/gibbs.py`, OxCal's own MH/Gibbs scheme. Both are pure numpy and
fast enough to assert in the suite, so `tests/test_exact_chain.py` and
`tests/test_gibbs.py` now cover as ordinary tests what used to need a manual
run taking minutes.

The findings below are kept because much of the reasoning in those modules cites
them, and because they are the measurements that justified replacing the sampler
rather than tuning it. Read them as a record of what SMC did, not as a
description of what the app does now.

- `oxcal_reference.py` — an exact, MCMC-free reference for the OxCal
  uniform-phase model, by direct numerical integration over the boundaries.
  Written independently of any solver in `app/services/`, so agreement with one
  is evidence about the model rather than a shared bug. Still current, and still
  the independent check behind the exact solver's own tests.
- `verify_model.py` — deleted along with the SMC path it drove. Its subject was
  `BayesianChronology`; the equivalent comparison now runs in
  `tests/test_gibbs.py`, against the exact solver as the oracle.

Figures below are from the model as it stood under SMC:

| check | width ratio | max mean diff | worst r_hat |
|---|---|---|---|
| `phase` (1 phase, 4 dates) | 1.03 | 5 yr | — (no boundary sampled) |
| `sequence` (2 phases) | 0.97 | 5 yr | — (one boundary sampled) |
| `order` (pure ordering) | 1.01 | <1 yr | 1.0 |
| `multiphase` (3 phases × 4 dates) | 0.94 | 21 yr | 1.02 |

## `hillside`: against a real OxCal 4.4 run

`tests/fixtures/oxcal/` holds three actual OxCal 4.4 analyses of the same
eight dates (Hillside; SHCal20; default UniformSpanPrior and
resolution; no outlier model), each with its `.oxcal` definition and the full
exported distributions. They differ only in how the two branches relate:

- **ordered** — the original. `North` and `South` are sibling
  Sequences inside `Sequence("Hillside")`, carrying no boundaries of their own.
- **unordered** — both wrapped in one `Phase("Sites")`.
- **shared_boundary** — a `Boundary("North to South")` between them.

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
constraint by pushing North entirely into the early calibration lobe
(secondary-lobe mass 0.000-0.001) and South into the late one
(0.27-0.77) — a chronological break at AD 1350 that is an artefact of the
SHCal20 wiggle, not of the data.

**This is a sampling failure, not a modelling one** — and it is why
`services/exact_chain.py` now exists. `shared_boundary` is two groups sharing
a boundary, exactly what `sequence_marginals` computes, so the exact answer
for the app's own model is available:

| | North mean AD | South mean AD | separation | South late-lobe mass |
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
chosen over NUTS to handle, failing when a hard ordering constraint is layered
on top of it.


### Fixed by solving instead of sampling

`services/exact_chain.py` integrates chain-shaped models exactly, and
`_run_bayesian` uses it whenever `exact_chain.layout()` recognises the model.
On the `shared_boundary` fixture, against OxCal:

| | mean error vs OxCal | group separation | South late-lobe mass |
|---|---|---|---|
| OxCal 4.4 | — | 13.2 yr | 0.169 |
| pm.sample_smc | 25.3 yr rms | 57.3 yr | 0.71 |
| exact solver | **4.2 yr rms** | **19.4 yr** | **0.262** |

It runs in about a second, is deterministic, and has no convergence
diagnostics to misread. Because it is that fast, the modelled comparison is
now an actual assertion in `tests/test_exact_chain.py` rather than something
only this harness could check.

`layout()` once declined the `ordered` fixture — two groups ordered against
each other while sharing one boundary pair. That was the remaining wrong answer
when this was written. It is now integrated as written, as ordered blocks
(`exact_chain._OrderedGroup`), 0.56 yr rms from OxCal; the app never adds a
Boundary to make a model solvable. Models the exact solver cannot take — a
branching site, say — go to `services/gibbs.py` rather than SMC.

### OxCal's UniformSpanPrior

Implemented in both `exact_chain` and `gibbs`, on by default as it is in OxCal,
and switchable via `bayesian_config.uniform_span_prior`. It divides the prior by
`(b_first - b_last)^(n-2)`, which stops a sequence's span prior depending on
how finely it is subdivided (Nicholls and Jones 2001).

Two measured facts shape what it is worth:

* **It cannot affect a single Phase.** The exponent is n-2, so at two
  boundaries -- the app's default shape -- it is the identity.
* **OxCal's companion limit correction is a no-op in practice.** Dividing by
  `((ulim - llim) - span)` to keep the span prior flat inside a finite window
  only matters when the span approaches the window; a real span of tens of
  years against a window of thousands makes it a constant. Measured
  identical at windows of 2,031 / 3,000 / 10,000 / 55,000 years, so it is
  not implemented.

On the fixture's two-group model it is a trade-off rather than an
improvement, which is why both settings are pinned in
`tests/test_exact_chain.py`:

| | dates vs OxCal | group separation (OxCal 13.2) | Start boundary 95.4% (OxCal AD 1277-1394) |
|---|---|---|---|
| span prior on | 5.4 yr rms | 5.7 yr | AD 1294-1392 |
| span prior off | 4.2 yr rms | 19.4 yr | AD 1211-1392 |

It pulls the outermost boundaries markedly towards OxCal and moves the dates
slightly away, with OxCal's separation sitting between the two settings. It
also couples the ends of the chain, so the solve goes from O(M^2) to O(M^3)
per group -- about 0.9s to 4.5s on this fixture.

### Reporting resolution

Two defects surfaced by running the recommended shared-Boundary model through
the app itself, both now fixed and pinned in `tests/test_exact_chain.py`:

* **Every modelled date plotted as a narrow spike.** The solver's grid runs
  far wider than the dates because the boundaries need room -- 2031 years on
  this fixture for a posterior about 100 years wide -- and the whole grid was
  being handed to the plot. The series is now trimmed to where the density
  has mass. The MCMC path never had this, because it histogrammed its own
  draws.
* **68.3% and 95.4% ranges looked nearly the same.** Ranges were computed at
  the solver's internal step, which is derived from the date range and the
  group count (1.0 to 7.7 years across the cases here) -- so a published
  range depended on an implementation detail. Worse, a modelled posterior on
  a wiggly curve is genuinely multi-peaked, so a 1-year grid picks out the
  peak tops and skips the dips: Ld-10012's 68.3% region came back as three
  fragments totalling 24 years, two of them split by a single excluded year,
  where OxCal reports 35. Reporting now happens at a fixed 5 years, OxCal's
  own default. Rebinned that way the eight dates' 95.4% widths average 51.2
  against OxCal's 55.6.

### An open question this raised

For a SINGLE group the exact solver and the samplers disagree, and the
disagreement grows with group size: they agree within a year at K = 3-4, and
by K = 8 the solver says the eight Hillside dates average AD 1348.3 while
SMC, a random-walk Metropolis chain and importance sampling all say ~1341.
The evidence says the solver is right — it converges under grid refinement,
matches an explicit double loop, and is bracketed by both analytic limits
(the unconstrained average is AD 1348.6, the fully-collapsed product of all
eight likelihoods AD 1349.9, and ~1341 lies outside both). All three samplers
struggle with the same tight ridge; the importance sampler's ESS was 128 out
of 4 million.

OxCal sits at AD 1344, between the two. That residual is a MODELLING
difference, not a sampling one: OxCal applies span-prior corrections
(`UniformSpanPrior` and a limit correction for its own computational bounds)
that this app does not, and their effect should grow with group size, which
matches the pattern. Worth settling if single large phases matter.

## `calib_regioncalc`: the marine reservoir weighted mean

`calib_regioncalc.py` checks `services/reservoir.combine` against calib.org's
own `regioncalc.php`, which is the reference implementation for the formula
the Delta-R lookup derives its values with. Twenty random sets of 2-8 points,
one request each, paused between.

**Run 2026-09-14: 20/20 exact integer match**, on both the mean and its
uncertainty, over sets whose reported uncertainties span 22 to 447 years.

Why it matters more than an ordinary formula test: the app derives a Delta-R
locally, from a mirror, precisely so that a site's coordinates never reach
calib.org. That is only defensible if the number it derives is the one their
page would print -- otherwise the app has quietly forked the reference and a
reviewer checking a published date against calib.org would find a different
answer. The three published cases asserted in `tests/test_reservoir.py` pin
the discriminating cases; this pins the general agreement.

It reaches the network, so it stays out of the suite: `regioncalc.php` is a
courtesy interface on somebody else's host, and running it on every test run
would be an abuse of it.

## If you write another cross-check

Two traps, both found the hard way:

- **Don't floor `(max - min)` at a tiny epsilon on a discrete grid.** The
  `(max - min)^(2-K)` factor is integrable in the continuum because the
  coincident-dates set has measure zero, but on a grid that set is a real
  cell, and a `1e-9` floor hands it a weight of `1e9^(K-2)` that swamps the
  whole integral — every date's marginal then comes back identical. Floor at
  the grid spacing. The same spike is a real attractor for samplers: a
  Metropolis chain started with all dates coincident never leaves.
- **Match the boundary integration window** to the solver's own before
  comparing. The SMC model took the wide-window limit for a two-sided
  marginalized group; integrating over a different finite window shifts the
  answer by a few years at K = 6-8. The same caution applies to
  `exact_chain.solve` and `gibbs.solve`, which report their grid in the result
  (`grid`) so a comparison can use it.

`phase_marginals` has been checked against exhaustive K-dimensional
integration of the same target at K = 3, 4 and 6 (agreeing to 1-2 years and
to 0.02 in tail mass), and against an independent random-walk Metropolis
chain, so it can be trusted at the group sizes tested here.

## Known gap: OxCal's UniformSpanPrior

**Closed.** Both current solvers implement OxCal's `1/(b_1 - b_n)^(n-2)`
span-prior correction (OxCal analysis operations; Nicholls and Jones 2001),
which keeps a sequence's overall span prior independent of how many sub-groups
it is divided into; it is switchable via `bayesian_config.uniform_span_prior`
and on by default, as in OxCal. The measurement below is why it mattered: on
the dates the effect is around 1%, but it tightens the two OUTERMOST boundaries
substantially (95% width 597 → 420 yr on that fixture). So reported Start/End boundary
ranges are wider here than OxCal would give; the dates themselves are
unaffected. It applies only when a sequence has three or more boundaries.
