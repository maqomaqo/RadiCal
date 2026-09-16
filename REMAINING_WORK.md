# Remaining Work: RadiCal

*Rewritten 2026-08-05; re-audited 2026-08-13, 2026-08-20 and again 2026-09-07.
The 2026-09-07 pass re-checked every claim against the working tree, corrected
the test counts and several stale file paths, and then closed six of the eleven
open items it had just tidied -- including authentication, the last thing above
LOW and the only one that changed what the rest of the app should be. Stale
notes are corrected rather than deleted, since a note that was wrong is worth
knowing about.*

*Every claim here was checked by reading the referenced file, running the suite,
or exercising the running app. If you are reading this after more work has
happened, treat it the same way: verify before trusting.*

---

## What's Actually Working

Listed because some of it is easy to assume is missing, and because the previous
revisions of this file understated parts of it.

- **Calibration math** (`backend/app/services/calibration.py`) — full
  IntCal20/Marine20/SHCal20 curves (9,501 points for IntCal20), correct
  quadrature error combination, and the curve-error normalization term matching
  current OxCal/CALIB practice.
- **Calibration curve manager and browser** — one registry
  (`services/curve_registry.py`) behind every curve the app knows, with the
  metadata measured from the curve that loads rather than recorded beside it;
  user-imported curves whose data is immutable and hash-checked on load; mixed
  curves (OxCal's `Mix_Curves`, previously listed here as not implemented at
  all); and a browser showing a curve's shape and error bounds, its local
  gradient, its expected temporal resolution after Svetlik et al. (2019), a
  comparison against a second curve, and what a date from any chosen year would
  actually look like.
- **Marine reservoir offsets** — per-date ΔR and its error, on both a project
  collection entry and an analysis sample (`delta_r`/`delta_r_error` in
  `models.py`), joined in quadrature so a poorly known offset widens the
  measurement rather than only shifting it. Entered by hand, or **derived from
  the date's coordinates** against a local mirror of the CHRONO/calib.org
  Marine20 database (`services/reservoir.py`): an inverse-variance weighted
  mean of the nearest N measurements, with σ the larger of the internal error
  and the scatter, agreeing 20/20 with calib.org's own `regioncalc.php`. The
  lookup runs against a frozen, hash-verified snapshot on this installation, so
  a site's coordinates never leave it, and a derived value carries its
  provenance — cleared the moment anybody edits the number by hand.
- **Site coordinates, and one map** — WGS84 decimal degrees on a collection
  entry and an analysis sample, refused in DMS or with a decimal comma rather
  than silently misread. The map (`components/SiteMap.tsx`) is drawn on
  `scattergeo` over a topojson the app serves itself, so it makes **no network
  request at all**; raster tiles are opt-in per project behind a notice saying
  what the request gives away, because a tile request names the square of the
  earth on screen. Coordinates are stored at full precision and rounded only on
  export, at a precision the project owner chooses.
- **Wave of advance** (`services/dispersal.py`) — how fast the thing being
  dated moved, and when it set out. A site's arrival is the posterior of the
  Boundary that *opens* it, never its oldest date, which is a *terminus ante
  quem* and late by however hard the site was dug; the fit is per joint draw,
  so the chronological uncertainty is inside the interval rather than bolted
  on. Refused below six sites, and a front running the other way is reported as
  a negative speed rather than flipped. Its coverage is measured, not asserted:
  see Open #4.
- **Multi-range HPD reporting** — disjoint calendar-age ranges with per-range
  probabilities, on both the direct-calibration and MCMC paths.
- **Bayesian chronological model** — calibration-curve-aware likelihood,
  stratigraphic order constraints, TPQ/TAQ limiters and per-sample practical
  prior bounds. Solved two ways: `backend/app/services/exact_chain.py`
  integrates a chain-shaped model exactly, and `backend/app/services/gibbs.py`
  samples anything else with OxCal's MH/Gibbs scheme. Both are pure numpy; the
  PyMC/SMC sampler they replaced is gone.
- **OxCal-style context Boundaries** — a context's dates are modelled as a
  random uniform scatter between a start and end Boundary; the Boundaries are
  analytically marginalized out of the MCMC and recovered from their exact
  conditional afterwards, so "Modelled Start/End" genuinely extend beyond the
  oldest/youngest dated sample. Verified against the closed form
  `E[a+b] = 2w/(K-3)`.
- **Durations, differences and joint draws** — phase durations and
  between-parameter differences from the joint posterior rather than from
  marginals (`services/intervals.py`). See Done #10.
- **Outlier models and the agreement index** — OxCal's General and Charcoal
  outlier models with a sampled shared offset scale, and `services/agreement.py`
  reported on the results page, in every export and in the PDF.
- **Stratigraphy / Harris Matrix UI** — the model tree editor, TPQ/TAQ, and a
  laid-out Harris matrix derived from the tree (`utils/harrisLayout.ts`):
  nesting as boxes within boxes, Boundaries as labelled rules, and ordering
  arrows only between adjacent blocks nothing else already separates. The matrix
  is a read-only view; a tree cannot contain a cycle, so the cycle check that
  guarded the old pairwise-relationship editor is gone with it.
- **Project management** — projects, assigning existing analyses to a project
  after the fact, re-running a single analysis or every analysis in a project,
  and deleting either.
- **Project date collections** — a project holds its own set of dates
  (`ProjectSample`), enterable individually or by bulk paste, deployable in any
  selection into a new analysis and auto-grouped by default context. Deploy
  copies the values, so an analysis is a self-contained snapshot.
- **Duplicate and edit analyses** — `POST /analysis/{id}/duplicate` deep-copies
  inputs (no results, status `pending`); `PUT /analysis/{id}/inputs` replaces a
  saved analysis's inputs with full validation and clears stale results. The
  frontend edit mode reuses the New Analysis → Stratigraphy pages.
- **Multi-analysis comparison** — `/projects/:projectId/compare`
  (`pages/CompareResults.tsx`) puts several analyses of one project side by
  side, with CSV export of the comparison. See Done #12.
- **Posterior visualization** — Plotly charts with shaded HPD bands, modelled vs
  unmodelled comparison, selectable HPD confidence, and opt-in point estimates.
- **In-app documentation** — the user manual is served inside the app
  (`/help`, from `frontend/src/content/USER_MANUAL.md` through
  `components/Markdown.tsx`) with a glossary at `/help/glossary`, rather than
  living only as a file in `docs/`.
- **Accounts** — open registration, and everything private to the one account
  that owns it: projects, analyses, jobs, and imported or mixed calibration
  curves alike. The only things shared are the three curves that ship with the
  app. See Done #18 and Done #19.
- **Real CSV/JSON/PDF export**, **input validation** on all analysis endpoints,
  and **versioned schema migrations** (Alembic, `backend/alembic/`, applied on
  startup — see Done #5).
- **Background job queue** — a long solve is queued rather than held open in the
  request that asked for it (`services/jobs.py`), with progress, cancellation
  and a polling endpoint. See [`docs/ASYNC_JOBS_PLAN.md`](docs/ASYNC_JOBS_PLAN.md).
- **Tests** — 899 backend, 425 frontend. Counts and per-file breakdown in
  [Testing & QA](#testing--qa).

---

# Open Work

**Nothing is high or medium priority, and there are no known bugs.** Every gap
this file has carried under those headings is closed, and so is authentication
-- the item that used to be described here as the one that stops being elective
the moment this is not a single-user tool. The last MEDIUM item, legacy
distribution units in stored results, closed on 2026-09-14 by checking the
database and finding no such rows (Done #21). What is left is genuinely
elective.

## Priority: LOW

### 1. Phase-duration priors and custom priors
*Reduced 2026-08-20.* This was "Advanced Bayesian features", listing four
things, and two of them have since been built: **outlier detection** as OxCal's
own General and Charcoal models with a sampled shared offset scale
(`exact_chain._outlier_kernel_at_scale`, validated against real OxCal runs in
`test_charcoal_outlier.py`), and the **agreement index** in `services/agreement.py`.

Still missing: explicit phase-duration priors (the uniform span prior is there,
but not a user-specified duration), and custom per-date priors. ~2 hours.

Note the direction: durations are now REPORTED (Done #10), which is the
posterior side. This item is the prior side — telling the model a phase lasted
at most a century before it sees the dates — and the two are independent. Having
the reporting makes this easier to judge, though: a user-specified duration
prior is worth something exactly where the reported duration comes back far
wider than the archaeology allows.

### 2. `ProjectSample` has no per-analysis provenance link
Deploy copies values deliberately (see the snapshot decision), but nothing
records *which* collection entry an analysis sample came from, so there is no
"this analysis is using an outdated version of this date" indicator. Adding a
nullable `source_project_sample_id` would enable that without weakening the
snapshot guarantee — and, since Alembic landed, adding the column is a migration
rather than an entry on a hand-maintained list. (No such column exists;
confirmed 2026-09-07.)

### 3. Wiggle matching and age-depth modelling — **delivered**
*Added 2026-09-02, delivered 2026-09-09.* Moved to Done #20. What is left of it
is one deliberate limitation, recorded there: a wiggle match must be the whole
model, and one nested inside a larger structure is refused by name rather than
approximated.

### 4. Spatial metadata, ΔR lookup, and dispersal modelling
*Added 2026-09-07*, from a plan committed the day before and not previously
listed here. Planned in
[`docs/SPATIAL_METADATA_AND_DISPERSAL_PLAN.md`](docs/SPATIAL_METADATA_AND_DISPERSAL_PLAN.md),
measured against the live calib.org database on 2026-09-06 rather than
estimated. Three things, in ascending difficulty: coordinates on a date;
deriving a marine date's ΔR from those coordinates against the CHRONO/calib.org
Marine20 reservoir database; and inferring dispersal — timing, pace, direction —
from dated sites in space.

The plan is **written for a fork** and says so — its §12 names the three files
that would conflict on a merge back.

**In execution from 2026-09-14.** The plan was reviewed against the code that
day: the measurements stand, and four things that landed after it was written
(users and ownership, age-depth as a section of `Analysis.intervals`, parallel
Gibbs chains, Plotly's `scattermap`) are corrected inline, marked *[rev.
2026-09-14]*. Its new §14 is the operation sequence — nineteen numbered
operations across the nine features, two migrations (ops 1a and 3a, from head
`f3b81c60d5a7`), each op naming its files, tests and done-condition.

**Features 1–6 are built (2026-09-14/15).** Coordinates on a date with
precision-controlled export; the offline `SiteMap` and click-to-place;
the CHRONO mirror as an immutable, hash-verified snapshot versioned like a
calibration curve; the ΔR lookup, validated 20/20 against calib.org's own
`regioncalc.php`; the freshness probe; and the wave of advance. Alembic head
is `b7c4e0a13d92`.

Three numbers worth keeping from it:

- **Strangford Lough at N=5 reads −209 ± 44**, which is the plan's own
  acceptance figure, asserted end to end.
- **The dispersal interval is calibrated.** `tests/test_dispersal_coverage.py`
  simulates whole fronts through IntCal20 and a solver and asks whether the
  reported 95% interval contains the speed the data were built from: **94.5% of
  200 fronts** on the exact path, **92.5% of 40** on the sampled one — re-measured after the
  dispersal models were finished and identical to the digit. That is
  the claim risk 11.4 doubted — *OLS on autocorrelated residuals with an assumed
  origin will always return a tidy v* — and a simulation was the only way to
  settle it. Rendered in the UI, a Neolithic-scale synthetic front reads
  **0.948 km/yr, 95% 0.800–1.12**.
- **The mixed-encoding row count drifted 54 → 60** between two measurements
  eight days apart, which is exactly the drift risk 11.7 was written about. The
  fixture is a capture of the source's own bytes, so it stays measurable.

**What is not built.** Op 3d, the bundled snapshot CSV, is gated on op 0 — the
redistribution-permission email to the database's maintainer, which the app
cannot send. Until it is answered a fresh install has no lookup until somebody
runs the ingest, which feature 5's card on the Curves page is the way to do.
Features 7–9 (trend surface and isochrons, inferred origin as a likelihood
region, MST-proposed Sequence) are unstarted; each waits only on 6d, which is
in. Regional kriging (§5.7) and over-water distance (§9) stay deferred by
design.

---

# Done

Kept rather than deleted: each of these records a decision and what it cost, and
several were closed by finding that the note itself was wrong.

### 1. ~~PDF report generation~~
**Resolved 2026-08-19.** `GET /results/{id}/pdf` renders a real report:
metadata, a method note naming the curves (with their references, read from the
curve registry) and which solver actually ran, the results table in the model's
own reading order with dates and Boundaries reporting through the same columns,
each context's modelled span where nothing else already reports it, and a
posterior plot per modelled parameter with the unmodelled calibration behind
each date. `services/pdf_report.py` renders; the route assembles. It takes
`?date_format=` exactly as the CSV does, and an analysis with no results still
produces a report saying so rather than failing the download.

Two things worth knowing, since neither is visible from the route:

- The report renders from what `_gather_export_rows`/`_structure_rows` already
  assemble for the CSV and JSON, so the three downloads of one analysis cannot
  disagree. The one exception is the per-date posterior series, which that
  assembly deliberately does not select — a spreadsheet has no use for a few
  hundred numbers per date — so the PDF route queries it separately rather than
  adding it to rows the JSON export would then carry too.
- Columns are chosen by what the run actually produced rather than by its
  declared type, so a plain calibration is not padded out with the agreement
  index and unmodelled comparison that only a model has.

Rendering is synchronous and costs a matplotlib figure per modelled parameter:
about four seconds for a 45-parameter model, which the Results page's button
states while it works. That is the thing to revisit if models grow, and it is a
smaller problem than a job queue.

### 2. ~~Documentation is materially wrong~~
**Resolved 2026-08-13.** `README.md`, the user manual and `docs/INSTALLATION.md`
were rewritten against the actual running app: no more PyMC/D3.js/react-table in
the tech stack (none are dependencies or imports any more), no fictional "4-step
workflow", no PDF-export-as-working-feature, correct frontend port (5173, not
3000 — `docs/INSTALLATION.md` had previously sent two different ports for the
same thing in adjacent sections), a real API endpoint list instead of invented
ones (`POST /api/analysis/multi` never existed; `/api/harris-matrix/*` and
`/api/results/{id}/graphs` did exist but weren't mentioned since nothing in the
frontend called them — the former has since been deleted, see Done #6), and
coverage of what the app actually does now. `README.md`'s license section also
claimed MIT; the repo's `LICENSE` file is CC0.

The manual has since moved out of `docs/` and into the app itself
(`frontend/src/content/USER_MANUAL.md`, served at `/help`), so it is read where
it is used rather than in the repository. References to `docs/USER_MANUAL.md`
elsewhere are stale by that much.

### 3. ~~CSV/JSON export omits context Boundaries~~
**Stale as written, resolved 2026-08-19.** The CSV has exported Boundaries in
model order since `_structure_rows` landed — that note predated it. What was
genuinely missing is the *Context Spans* table (`SampleGroup.modeled_start` /
`modeled_end`), which the PDF report now carries. It is a legacy path either
way: every Bayesian run since model trees persists explicit Boundary elements,
and adjacent contexts share a boundary, so reporting both would print each
transition twice — once as one context's end and again as the next one's start.
Both the report and the results page suppress the table whenever the model has
Boundaries of its own, which leaves it showing only for analyses stored before
trees existed.

### 4. ~~Async MCMC processing~~
**Scoped 2026-08-19 — see [`docs/ASYNC_JOBS_PLAN.md`](docs/ASYNC_JOBS_PLAN.md).**
Measuring it split one item into three, and the worst part of it turned out not
to be the queue at all:

- **The severe half was not the queue, and is now fixed (2026-08-19).** All 50
  route handlers were `async def` with nothing offloaded, so every solver run
  and PDF render executed *on the event loop* — the whole server unresponsive
  for the length of it, not just the caller. Measured: `GET /api/curves` cost
  2.3 ms idle and **3.85 s** when issued during a 4.9 s PDF render. Every
  handler is now a plain `def`; the three that read an upload keep `async def`
  for the read alone and hand their bodies to `run_in_threadpool`. The same
  measurement now reads **2.9 ms**, and 2.6–8.1 ms during a live Gibbs run.
  `tests/test_event_loop.py` holds the invariant: a new blocking `async def`
  route fails the suite.
- **The caller's own wait is fixed too (2026-08-19).** `POST /analysis/bayesian`,
  `POST /analysis/{id}/rerun` and `POST /projects/{id}/rerun` answer **202** with
  a `job_id` as soon as the work is accepted — measured at 26 ms for a run that
  used to hold the connection for 6.5 s — and the page follows it at
  `GET /api/jobs/{job_id}`. A `jobs` table, a single-worker executor
  (`services/jobs.py`), and one progress/cancel callback inside `gibbs.run`.
  `?wait=true` keeps the old synchronous behaviour permanently, for anything
  scripting the API without an event loop to poll from.
- **Progress and cancellation are real.** The sampler reports its phase, sweep
  count and achieved convergence after every round; a cancel stops it at the
  next round boundary — measured at 0.3 s — and writes nothing, so the analysis
  keeps the results it already had. Note that progress is a fraction of the
  sweep *cap*, and a converged run typically finishes around 15% of it: that is
  a success, which is why the convergence figure is always shown beside the bar.
- **Not Celery/RQ.** A broker buys durability across restarts and parallel runs;
  a single local user with no authentication needs neither yet. Anything left
  mid-flight by a restart is failed at startup instead (`jobs.reconcile_orphans`),
  and the executor is a seam a distributed backend would replace without
  touching a caller. Revisit now that Done #18 has landed.
- **Connection pooling landed with it** (Done #7), as predicted: the worker
  holds a connection for the whole length of a run, so `database.py` now sizes
  the pool (Postgres only — SQLite takes none of those arguments, and the suite
  runs on SQLite).
- Covered by `tests/test_jobs.py` (16 tests). The pre-existing tests that assert
  on results now ask for `?wait=true`, which is what they always meant.

### 5. ~~Real database migrations (Alembic)~~
**Done 2026-08-19.** `backend/alembic/` holds the schema history, and
`init_db()` brings the database up to it on startup instead of calling
`create_all()` and then patching what it missed.

- **The reason to bother was not tidiness.** It is
  `tests/test_migrations.py::test_the_migrations_and_the_models_agree`, which
  asserts there is nothing left for `--autogenerate` to produce. A column added
  to a model without a migration is invisible to every other test — the
  developer's own database already has it — and surfaces on a fresh install as
  "column does not exist" on every request to that endpoint. That test was
  checked by introducing the drift on purpose: it fails and names the column.
- **Old databases adopt themselves.** One with the tables but no
  `alembic_version` is recognised at startup, topped up by the old column list
  one last time, and *stamped* at the baseline rather than upgraded to it (the
  baseline's `CREATE TABLE`s would fail against tables that exist). Verified
  against a database missing two columns and holding a row: columns added, row
  intact.
- **The stopgap is closed, not deleted.** Those lists are the only record of
  what an old database lacks, so deleting them would strand anyone upgrading
  straight from a pre-Alembic version — silently, since their tables exist. It
  is marked CLOSED, takes no new entries, and runs at most once in a database's
  life.
- **The baseline's `downgrade()` refuses.** Autogenerate wrote one that drops
  every table, which here means deleting every analysis in the database. An
  irreversible loss of someone's work should not be one mistyped command away.
- **`RADICAL_SKIP_MIGRATIONS=1`** takes migration out of startup for a
  deployment that would rather run `alembic upgrade head` deliberately.
- Note that autogenerate does not see a **rename** — it proposes a drop and an
  add, which loses the column's data — and cannot know a **backfill** is needed.
  Both still need writing by hand; the difference from before is that there is
  now somewhere to write them.

### 6. ~~Wire the curves API into the frontend, and decide about harris_matrix.py~~
- **Curves: done 2026-08-18.** `backend/app/services/curve_registry.py` is now
  the single source of truth — it measures each curve's metadata from the curve
  that actually loaded, and the five separate hardcoded lists (the loaders in
  `calibration.py`, the `Literal` in `schemas/sample.py`, the table in
  `api/curves.py`, the OxCal name map in `oxcal_script.py`, and the two
  `<MenuItem>` lists in the frontend) all resolve through it. The wrong point
  counts are gone with the table that held them: the API reported 5,141 / 1,341
  / 5,141 where the loaded curves hold 9,501 / 5,501 / 9,501.
  `frontend/src/api/curves.ts` + `hooks/useCurves.ts` are what the pickers read,
  and `tests/test_curves.py` covers a module that had none.
- **harris_matrix.py: deleted 2026-08-19.** Its two endpoints
  (`/api/harris-matrix/validate`, `/api/harris-matrix/relationships`) were live
  routes on a plain nodes/edges shape that predates the model tree
  (`services/model_tree.py`) the real Harris matrix editor
  (`HarrisMatrixDiagram.tsx`) actually talks to, via the analysis endpoints
  directly. Nothing called them, and they were not a smaller version of the
  working path — they were a different, weaker one: `validate` checked only that
  an edge's endpoints exist, with no cycle detection, no TPQ/TAQ contradiction
  check and no vocabulary check on the relationship, all of which
  `BayesianRequest`'s validators do before an analysis is ever run.
  `relationships` renamed three keys. Keeping them as "API surface for external
  consumers" would have meant publishing a validator that passes models the real
  one rejects.
- The decision was deferred through the 2026-08-13 cleanup on the grounds that
  removing live endpoints is a bigger call than deleting dead frontend code. It
  is, and this is the deliberate version of it rather than a drive-by.

### 7. ~~Database connection pooling~~
**Done 2026-08-19**, with the job queue (Done #4) that made it stop being
optional: a background worker holds a connection for the whole length of a run,
which is exactly the "concurrent load" this was waiting for. `database.py` now
sets `pool_size`/`max_overflow`/`pool_pre_ping`/`pool_recycle` — on Postgres
only, since SQLite has no pool worth the name and rejects the arguments, and the
test suite runs on SQLite.

### 8. ~~Calibration curve management UI~~
**Done 2026-08-18.** Planned in full in what was `docs/CURVE_MANAGER_PLAN.md`;
the plan was deleted once delivered, so references to that filename elsewhere
are stale. **All seven phases shipped**: the registry behind all of it (Done
#6), and the backend for user-imported curves — parse-then-store preview,
`.14c`/CSV/BC-AD reading, immutable curve data guarded by a content hash checked
on every load, archive-rather-than-delete while in use, aliases so an OxCal file
naming an imported curve imports, and byte-for-byte export. Covered by
`tests/test_curve_import.py`.

…and the `/curves` page over it: import dialog with a preview of the curve
before anything is stored, editable description/citation/aliases, download,
archive/restore, and a delete that asks what is using the curve first.

…and the curve browser at `/curves/{id}/browse`: curve shape with ±1σ/±2σ bands,
a full-range time scrubber, a windowed `/points` endpoint whose decimation keeps
each bucket's extremes, and a hover readout reporting the local gradient.

…and the expected-resolution profile (Svetlik et al. 2019) beneath the curve on
the same axis, with a selectable laboratory error and a CSV export. Validated
against the reference implementation's published output at 0.36% mean difference
over 1,000–4,000 cal BP.

…and mixed curves: the mixing math (which had never been implemented at all), a
builder on the Curves page, and `Mix_Curves` support in the OxCal importer,
matched against a stored mixture rather than created by opening a file.

…compare mode (a second curve overlaid, with their difference on its own panel),
click-a-year to simulate a date, CSV export of the visible window, and the
documentation for all of it — the user manual has a Calibration Curves section,
and every figure quoted in it was checked against the running app.
**Nothing remaining**; the whole plan is delivered.

### 9. ~~Deploying as `analysis_type: "calibration"` silently drops all but the first date~~
**Fixed 2026-08-20**, the day after that audit promoted it out of "Known Gaps" —
it was filed as a rough edge, but silently discarding a user's dates on a path
the API accepts is a bug.

Fixed in both places it lived:

- **The entry point.** `deploy_samples` routes a multi-date `calibration`
  request to `group_calibration`, which is what several independent dates with
  no model relating them already is. One date is still a plain `calibration` —
  the routing is for the case that was broken, not a redefinition of the type.
  The response reports the type actually created rather than echoing the one
  asked for.
- **The truncation itself.** `_rerun_analysis`'s calibration branch read
  `samples[0]`, correct when a single-date analysis was the only way to reach
  it. It now loops, sharing one branch with `group_calibration` — the work is
  identical for both, so the difference belongs in what the results page calls
  it, not in what gets computed. Deploy can no longer create the bad state, but
  a database may already hold one, and a rerun now repairs it rather than
  truncating again.

Five tests in `TestDeployingAsCalibration`, checked by reverting the fix: they
fail with "a date was dropped: 1 == 3". Verified against the live Postgres stack
as well — three dates in, three results out, three distinct sample ids. No
stored analysis was affected: the live database held no multi-date `calibration`
analyses and no result rows with a null `sample_id`.

### 10. ~~Durations, differences, and joint draws~~
**Added and delivered 2026-08-22.** Not a gap this file had listed — a feature
request. It answers "how long did this phase last" and "how far apart are these
two", which nothing in the app could say before, because every number it
reported was a MARGINAL and every quantity here is a function of several
modelled ages at once.

`services/intervals.py` holds the vocabulary and the arithmetic; the work was
getting it the joint posterior to work from.

- **exact_chain** gained forward-filter/backward-sampling over the chain it
  already builds (`_messages(...)[2]`), so the draws are INDEPENDENT draws from
  the true joint — no MCMC, no burn-in. The UniformSpanPrior correction breaks
  the Markov property, so that path draws the two ends together from `C * A[n]`
  and fills the interior in between them; both are held against hand-written
  distributions in `test_intervals.TestTheJointSampler`.
- **gibbs** keeps whole states as it sweeps, folded and burnt in on exactly the
  same schedule as its histograms (`_burn_in_start`), so the marginals and the
  durations describe one stretch of one run.

Two decisions worth knowing:

- **It is Monte Carlo, and says so.** Only a single phase's duration is cheaply
  exact; a sequence duration couples the ends of the chain and a sample duration
  is the range of K dates, and neither factorises. Reporting one exactly and the
  other from draws would put a systematic difference between two quantities
  whose whole point is being compared. `draws` and `independent` ride on the
  stored block, and the UI and the PDF both state it.
- **The reported bin width is 40 bins over a percentile spread, not min-to-max.**
  An HPD region is assembled bin by bin in order of density, so bins near the
  cut go in or out on their own sampling noise: at a five-year step a
  single-humped gap came back as eight fragments. Forty bins plus a [1,2,1]/4
  smoothing of the counts collapses that to one range, and taking the spread
  from percentiles keeps more draws from COARSENING the answer (from min/max,
  20k → 60k draws moved the step from 10 years to 15).

What it does not do: no duration for a phase with no Boundaries around it (the
bracket IS the interval), no comparison between two phases the model brackets
identically, and nothing at all unless the run was asked for it.

### 11. ~~One sigma was three different numbers, and labelled as a fourth~~
**Fixed 2026-08-23.** Raised as "the results page says 68.2% but the convention
is 68.3% — is that just the label?". It was not just the label.

`confidence` is the threshold an HPD set is accumulated to
(`hpd_ranges_from_distribution`), so the digits are the reported range. The app
had grown three values for one sigma and three for two, in different layers:

| | 1 sigma | 2 sigma |
|---|---|---|
| stored plain/group calibrations, unmodelled export column, curve endpoints | 0.68 | 0.95 |
| results page selector, precomputed unmodelled ranges | 0.682 | 0.954 |
| both solvers, the durations | 0.683 | 0.954 |
| resolution profile | — | 0.9545 |

Measured over thirty IntCal20 calibrations (500–10,000 BP at ±15/25/40) against
the exact coverages:

- **1 sigma:** 0.68 differed in 11 of 30 cases, by up to 5 years. 0.682 in 0 of
  30; 0.683 in 1 of 30, by 1 year.
- **2 sigma:** 0.95 differed in **27 of 30**, by up to 10 years. 0.954 in 9 of
  30 by up to 5; 0.9545 in 0 of 30.
- **3 sigma:** 0.997 differed in 24 of 30, by up to 10 years.

So the two-decimal truncations were the material error and the three-decimal
forms were nearly right. The label was wrong on its own terms too: erf(1/√2) =
0.68269, which **rounds to 68.3%**, and 68.2% is a truncation. 95.4% and 99.7%
were already correct.

`SIGMA_1/2/3` now live in `services/calibration.py`, computed as erf(n/√2)
rather than typed, and every call site reads them. `utils/hpd.ts` mirrors them
(JavaScript has no erf) and pins the digits in its own suite; the mirror is
deliberate, and is the arrangement `services/pdf_report.py` already uses for the
plot palette.

Two consequences worth knowing:

- **Stored results keep their old numbers until re-run.** The same call the
  legacy-distribution-units note made (now Done #21): rewriting stored results
  is a bigger decision than correcting what gets written next. Closed out on
  2026-09-14: the four analyses (50 results) that predated this fix were
  re-run in place through `_rerun_analysis`, and the two plain calibrations
  were checked row by row against a fresh calibration. Nothing stored now
  predates it. A Bayesian analysis's
  unmodelled column is derived at read time and so updates immediately, while
  its modelled ranges do not — so an analysis that has not been re-run since is
  internally a few years out of step. Re-running it fixes it.
- **`storedHpdBands` lost a distinction it was keeping on purpose.** It labelled
  the backend's stored ranges a bare "68%/95%" precisely because that really was
  the level behind them while the results page re-selected at the sigma
  coverages. Both sides are on the sigma coverages now, so the label says
  68.3%/95.4% like everything else.

### 12. ~~Multi-analysis comparison UI~~
**Done, struck off 2026-09-07.** This file still carried it as open work while
the feature was already in the app: `pages/CompareResults.tsx` is routed at
`/projects/:projectId/compare` from `App.tsx`, and the arithmetic behind it is
factored out and unit-tested — `utils/comparePairing.ts` (16 tests),
`utils/compareExport.ts` (15), `utils/compareDerived.ts` (14) and
`utils/compareConfig.ts` (10). CSV export of a comparison landed with it.

The reason it was worth building is the one the old open note gave: deploying
one set of dates into several differently-modelled analyses is a first-class
workflow, and there was no way to view those results side by side.

### 13. ~~No error boundaries in the frontend~~
**Done 2026-09-07.** `components/ErrorBoundary.tsx` wraps the routed pages in
`App.tsx`, so a render-time exception now shows what happened and offers a
reload instead of leaving a blank white page with the error only in the
console.

Three placement decisions, none of them arbitrary:

- **Inside the Router and below `Navigation`**, so a broken page still leaves
  the user something to navigate with rather than replacing the whole chrome.
- **Keyed on `location.pathname`**, which is why `RoutedPages` exists as its
  own component (`useLocation` has to be inside the Router). Without the key,
  one page that threw would keep the fallback showing over every page navigated
  to afterwards, because nothing else ever resets a boundary's state.
- **It shows the message and not the stack.** A message is often legible and
  makes two occurrences comparable; a minified stack is neither, and putting it
  on screen invites a screenshot of it instead of the console output that
  actually has the frames.

It caught a real bug within a minute of being wired in: the thin-phase work in
Done #15 had put a `useMemo` below `Results`'s early returns, which React
reports as "Rendered more hooks than during the previous render". That is
exactly the class of failure this exists for — the page would otherwise have
gone white — and it is the reason the fallback says nothing you have saved is
affected, because for a render fault that is true and is the first thing a
user wants to know.

Not a replacement for the pages' own error states. A failed request is an
expected outcome with a known remedy and belongs where the user asked for the
thing; this is for the unexpected, and says so rather than pretending to know.

### 14. ~~A failed run reports its cause as a raw exception~~
**Done 2026-09-07.** `services/failures.py` classifies an exception before it
is stored, and every site that used to write `str(exception)` into
`error_message` now writes the classified message — two in `api/analysis.py`,
two in `api/projects.py`, and the job worker in `services/jobs.py`.

The distinction it draws is the one that was missing: a ValueError raised
inside this package carries a sentence written for an analyst and is kept
verbatim; anything else is a fault in the app and is reported as one, without
putting its repr on screen. Three categories, chosen because the reader's next
step differs:

- **curve** — a curve the analysis names is gone or unreadable. The remedy is
  a different curve, and the message says so.
- **model** — the model as specified cannot be solved. The remedy is the
  structure, the limiters or the dates.
- **internal** — a fault in the app. The message says the analysis still holds
  whatever results it had, which is the thing a user actually wants to know,
  and the repr goes to `Failure.detail` and the log rather than the screen.

**It reads the traceback, not the message text.** A deliberate failure is
attributed by the frame that raised it, so `int('not a number')` is not
mistaken for one of ours despite being the same exception type, and a phase
somebody named "internal error" cannot change its own classification. The
innermost of our frames wins, so a curve failure surfacing through an API
handler is still a curve failure.

`classify` cannot raise: a classifier that failed while reporting a failure
would replace a bad message with no message at all.

No new column. The message carries the distinction, so the schema is
unchanged; `Failure.category` exists for a caller that wants to style it and is
currently read only by the job log line.

Covered by `tests/test_failures.py` (15 tests), which call functions that
really raise — `validate_tree`, `hpd_ranges_from_distribution`, `resolve` —
rather than constructing exceptions, since an exception with no traceback has
no origin to attribute and a test written that way would pass against a
message-matching classifier.

### 15. ~~A context with only 2–3 dates gets very wide Boundaries~~
**Done 2026-09-07.** `utils/thinPhases.ts` finds them and the results table
shows a "Few dates" chip beside the affected rows, with the reason in its
tooltip — the same chip-in-a-tooltip idiom (`describeChild`, `tabIndex={0}`)
the outlier column already uses, so the reason is reachable by keyboard and on
a tablet rather than only on hover.

The threshold is not a tuning knob: `E[a+b] = 2w/(K-3)` is where it comes from,
and four is where that stops dividing by zero or by a negative. Three dates
genuinely carry no information about how much earlier a phase began than its
oldest date, so the very wide range is the model reporting what the dates
support. The wording says that, rather than implying something went wrong.

Two things the arithmetic has to get right, and both are pinned by tests:

- **A phase counts its whole subtree**, because that is what its Boundaries
  bracket. Two dates directly in a phase with six in a sub-phase is eight, not
  two.
- **A Boundary belongs to both phases it separates**, and is loose if either is
  thin. It also has to resolve the phase that FOLLOWS it, which a walk that
  only looked at phases already visited would miss — a Sequence puts the
  Boundary before the phase it opens.

Covered by `utils/thinPhases.test.ts` (21 tests). Checked against the live app
on a real 22-date model: two thin phases (1 and 2 dates) and the four
Boundaries around them flagged, each naming its own neighbour, and nothing
flagged on the well-dated phases.

### 16. ~~No linting~~
**Closed 2026-09-07 by removing the dead dependencies, not by adding ESLint.**
`eslint`, `@typescript-eslint/eslint-plugin`, `@typescript-eslint/parser`,
`eslint-plugin-react-hooks` and `eslint-plugin-react-refresh` were installed
with no config anywhere in the repo for them to run against, so they were
coverage that read as existing and did not. They are gone from `package.json`
and from the lockfile.

`npm run type-check` (`tsc --noEmit`) is the static check, and it is a real
one. If ESLint is wanted later it is a deliberate piece of work — choose a
ruleset, fix what it finds — rather than restoring a line to `package.json`,
which is what the previous state made it look like.

### 17. ~~Excel export~~
**Declined 2026-09-07.** CSV, JSON and PDF all work, and a CSV opens in Excel.
An `.xlsx` writer would add a dependency and a fourth export path to keep in
step with `_gather_export_rows` for formatting the other three already carry
adequately. Recorded rather than deleted so it is not re-proposed as an
oversight: it was considered and judged not worth the hour.

### 18. ~~Authentication & user management~~
**Done 2026-09-07.** The last item this file carried above LOW, and the only
one that changed what the rest of the app should be. Three decisions were
taken first, because each of them decides the schema:

- **Registration is open.** Anyone who can reach the app can create an
  account. The gate is expected to be whatever fronts the deployment — a VPN,
  an institutional SSO proxy, a private network — and what accounts add behind
  that gate is attribution and separation, not admission control.
- **Everything is private to its owner.** No sharing, no read-only access, no
  administrator who can see everything. A project, an analysis and a job
  belong to one user and are invisible to every other.
- **The first account adopts the work that came before it.**

`services/auth.py` is the arithmetic — bcrypt hashing, JWT signing — with no
HTTP in it. `api/auth.py` is the routes and the `current_user` dependency.

**Two rules the whole API depends on**, stated in one place because they are
easy to get subtly wrong in each individual route:

- **Someone else's row is a 404, not a 403.** A 403 confirms the id exists,
  which turns any endpoint taking an id into a way to enumerate other people's
  work. `owned_or_404` is the only way routes reach a row.
- **A list endpoint filters, it does not check.** The owner goes into the
  query, before any optional narrowing, so no combination of parameters can
  widen past it and no new row type leaks by someone forgetting a check inside
  a loop.

Applied across all 53 routes. The places where it was not mechanical:

- **`_validate_project_id`** now takes the user, so a project id cannot be
  used to file an analysis into a project you cannot see.
- **`jobs.submit` takes an owner**, so a job id is not a way to watch — or
  stop — someone else's run.
- **`project_samples._require_project`** was already the choke point every
  route in that file went through, so the check went there: a ProjectSample
  carries no owner of its own, and the project IS its access boundary.
- **`list_projects`'s three grouped queries** each needed the filter
  separately. Without it the project cards would have counted other people's
  analyses.
- **`curve_registry.usage` partitions rather than filters** — see below.

**Curves are the deliberate exception.** They stay shared: reference data,
readable by anyone signed in, like the three built-in curves. A per-user
registry would undo the single-source-of-truth that the whole of
`curve_registry.py` is built on (Done #6), and two analysts on one site
calibrating against different copies of one curve is a worse outcome than
either of them seeing that the curve exists. What is NOT shared is the right
to change one: `created_by` keeps editing, archiving and deleting with whoever
imported it, so nobody can alter reference data another user's stored results
were computed against. That check answers **403, not 404** — unlike everywhere
else — because the curve is not hidden from them and pretending otherwise
would be a lie they can immediately disprove.

`usage()` had to split in two for the same reason. Whether a curve may be
deleted is a question about EVERYONE's work — scoping it to the caller would
let one user delete a curve another's results depend on — so the `in_use`
verdict stays global while the id lists hold only the caller's own rows and
everyone else's collapse to a count.

**The signing key refuses to be a default.** A hardcoded fallback secret is
not a convenience, it is a published private key: anyone with the source could
mint a token for any account. There is a development key so a fresh checkout
runs, it is named so it is obvious in a log or a code search, startup says so
out loud, and `secret_key()` raises rather than returning it once `RADICAL_ENV`
declares the deployment is real.

**Existing data.** The owner columns are nullable, which is not laziness: a
migration runs before any account exists, so there is nobody to attribute rows
to yet. The first registration adopts every ownerless project and analysis
inside its own transaction, and reports the count — on an install that had
work in it, that number is the difference between "my analyses are gone" and
"they are mine now". Verified against the live Postgres stack: 6 projects and
19 analyses were waiting for it.

**On the frontend**, the token reaches the server by wrapping `window.fetch`
once (`api/client.ts`) rather than by editing 64 bare `fetch('/api/...')`
calls across 13 files. Patching a global is usually the wrong instinct; it is
right here because a call site added later cannot forget it — the same
argument `test_auth.TestEveryRouteIsProtected` makes for asserting over the
route table instead of keeping a list. The wrap is narrow: same-origin `/api/`
only, one header, never overwriting one a caller set. A 401 from anywhere ends
the session once and shows the sign-in screen, except from `/api/auth/login`,
where a 401 is an answer rather than an expiry and must not sign somebody out
as they try to sign in.

Covered by `tests/test_auth.py` (62) and `utils/client.test.ts` (14). The
load-bearing part of the first is `TestOneUserCannotReachAnother`: two real
accounts, real work under one, and fourteen routes the other tries to reach it
through, each of which must answer 404 — plus the other half, that the owner
can still reach all of it, since a filter that 404s for everybody would pass
the first test alone. `TestEveryRouteIsProtected` walks the app's own route
table, so a new endpoint that forgets `current_user` fails the suite rather
than quietly becoming a hole. The frontend's heavier half is what does NOT get
the header: another origin, a path outside `/api`, an origin that merely
starts the same way, and our origin appearing later in a URL.

**What this unblocks.** Two decisions were explicitly waiting on it (Done #4):
the thread pool rather than a broker, and a connection pool sized for one
worker. Both are now worth revisiting — and open registration plus a
single-worker queue means any account can occupy the only solver thread, which
is the first thing to look at if this is ever public rather than behind a VPN.

### 19. ~~Calibration curves were shared between accounts~~
**Changed 2026-09-07**, the same day #18 landed and directly because of it.
Accounts arrived with curves as the deliberate exception to per-user privacy:
shared reference data, readable by anyone signed in, with only editing
restricted to whoever imported one. That was wrong, and it took having the
accounts to see why.

The reasoning it replaced was that a per-user registry would undo the single
source of truth `curve_registry.py` is built on. It does not — the registry
still is one, it just answers a question with a `who` in it — and the argument
skipped what a shared list actually does on an install with more than one
person on it:

- **A curve in development reads as a supported option.** Nothing distinguishes
  a curve somebody is still checking from IntCal20 in a picker, so a colleague
  picks it and gets a real result against an unfinished curve.
- **An old curve becomes an app-wide default by accident.** One imported for a
  single site years ago sits in the list forever, and a newcomer has no way to
  know it is not something the app endorses. Reference data is only reference
  data if somebody vouched for it, and nothing here does.

So a stored curve — imported or mixed — is now private to the account that
created it, and the three builtins are the only shared ones.

**The interesting part was where to put the check.** Scoping `resolve()` — the
loader — would have meant threading a user id through both solvers, the PDF
renderer and the exporters, to reach code whose only job is to read a file. So
`curve_registry.py` is split explicitly instead, and its docstring names the
two halves: the functions that CHOOSE (`list_specs`, `exists`,
`resolve_alias`, `find_mixed`, `known_ids`, `curve_ids`) take a required
`owner_id`, and the functions that LOAD (`resolve`, `get_spec`, `usage`) take
an already-authorised id and do not ask who wants it. `owner_id` is required
rather than defaulted precisely because a default is how one of them quietly
starts returning everyone's curves again; `SCOPE_ANY` is spelled out at the
three call sites that genuinely mean all of them.

Six things had to be scoped separately, because each is its own way in, and a
test that only checked `GET /api/curves` would have passed against a build
where `/curves/{id}/points` served anything asked for:

- every id-taking curve route, gated by `visible_or_404` before it resolves;
- the analysis and project-date endpoints, via `authorise_curve_ids` — the
  boundary between the schema's shape check and the ownership question;
- CSV and `.oxcal` import, which resolve a file's curve NAMES against the
  uploader's own curves, so importing a file is not a way to ask what exists;
- the alias index, which is cached and is now keyed by owner — one shared
  cache would hand whichever user warmed it second the curves of whoever
  warmed it first;
- mixed curves, including duplicate detection, which is per user: two accounts
  may each hold their own 30/70 mixture, since a curve one cannot see is not a
  duplicate they could have reused;
- error messages, which were the subtlest of it. The schema validator's
  "unknown curve" used to list every stored id, which would have named every
  account's imports; it now names only the builtins, and the route's own
  refusal lists the caller's own curves.

**Two decisions worth knowing.** Curve ids are one global primary key while
curves are per user, so two accounts importing the same slug would collide on
an insert: the second is silently given `slug-2` rather than refused, because
refusing would be both a dead end and a disclosure. The user's own clash with
their own curve is still a 409, since that one they can see and act on. And a
first registration adopts ownerless curves alongside the projects and analyses
— it has to, or an analysis would come back to its owner with its curve gone,
which is worse than the analysis being missing, because the analysis is still
there and no longer reproducible.

**One pre-existing bug fell out of it.** Three `except Exception` handlers in
`api/analysis.py` caught every exception including deliberate `HTTPException`s
and re-raised them as a 400 with `str(e)` — so a considered 422 arrived as a
400 reading `"422: ..."`. Any purposeful refusal raised inside those blocks had
been flattened this way; the curve check was just the first to be tested for
its exact status. They now re-raise `HTTPException` untouched.

Covered by `tests/test_curve_privacy.py` (41 tests), which asserts the absence
of a route rather than the presence of a filter, and includes the registry's
own scoping — where a mistake would otherwise be silent, since a listing
function that stopped filtering would keep every endpoint test passing while
the picker filled up again.

---

### 20. ~~Wiggle matching and age-depth modelling~~
**Added 2026-09-02, delivered 2026-09-09.** Planned in
[`docs/AGE_DEPTH_AND_WIGGLE_MATCH_PLAN.md`](docs/AGE_DEPTH_AND_WIGGLE_MATCH_PLAN.md)
and built to it. Two features with one shape -- a sequence whose members are
separated by a measured distance on an axis outside the model -- and two very
different solvers behind them.

**Wiggle matching** (`D_Sequence`/`V_Sequence`) is `services/wiggle_match.py`:
one free parameter, the calendar age at the series reference point, and a closed
form. Shift each member's likelihood by its own `ring_offset` and multiply.
There is no sampler, so none of `services/intervals.py`'s Monte Carlo
qualifications apply to it. It builds its OWN calendar grid rather than
borrowing `exact_chain._build_grid`, whose `BOUNDARY_WINDOW_FACTOR` padding
exists for boundaries a wiggle match does not have --
`test_wiggle_match.TestTheGrid` halves the step and requires the reported range
not to move, which is the check that would have failed on the borrowed one.

The 19x on the worked example holds: at 2500 cal BP a single +/-25 date comes
back 285 years wide in two ranges, and eight samples over 250 rings come back 20
years wide in one. `TestWhatTheMatchBuys` asserts it as a RATIO rather than
against a remembered figure, so a curve update moves the number and not the test.

**Age-depth** (`P_Sequence`) is in `services/gibbs.py`, as the plan said it would
be: a level's full conditional is its own likelihood times two power terms, one
per neighbour, so it stays one-dimensional and tabulated. `k` is drawn from its
own conditional exactly as `Model.draw_scale` draws the outlier scale, and its
posterior is REPORTED -- a stiff model interpolates confidently through a
plateau, and `pinned` says when the answer is leaning on the end of the prior's
range rather than on the dates.

Three things worth knowing, none of them visible from the endpoint:

- **The double interval prior was the real risk**, and it is prevented in
  `model_tree.plan_model` rather than in the solver: a deposition sequence's
  levels bubble up as `SpanGroup.contained_ids` rather than as `date_ids`, so
  they are penned in by the enclosing Boundaries and charged no `(span)^(-K)` on
  top of their own Poisson prior. `TestTheSpanPriorIsNotAppliedTwice` asserts
  that structurally as well as in the answer, because it would not have shown up
  as a crash or as a wrong-looking number.
- **The strongest check is the brute force.** Three dated levels make a 3-D
  integral computable directly on a grid, and `TestAgainstBruteForce` writes the
  deposition density out again from the paper rather than calling the sampler's
  own -- so what is checked is the sampler against the model. Whether the MODEL
  is discretised finely enough is a separate question, answered by refining the
  grid rather than argued about.
- **The sign convention is stated once** (`services/wiggle_match.py`, the field
  help, `utils/ringOffsets.ts`, the importer) and pinned against an ASYMMETRIC
  stretch of IntCal20. A reversed series on a symmetric stretch comes back merely
  shifted and every assertion still passes, which is the failure mode that
  fixture exists to catch.

Both import. `D_Sequence`, `V_Sequence` and `P_Sequence` are gone from
`oxcal_script.UNSUPPORTED`, and `Date()` is NARROWED rather than lifted: inside a
`P_Sequence` with a `z=` it is an undated level, which is what makes "how old is
47 cm" answerable, and anywhere else it is still an undated free parameter with
nothing bounding it. `U_Sequence` stays refused, deliberately -- it is a
different prior over the same depths, and reading one as the other would change
the posterior silently.

Where a V_Sequence is not reproduced exactly, the import says so rather than
leaving it in the arithmetic: OxCal's gaps form a chain, so two members' 
distances from the reference share every gap between them, and this app carries
one uncertainty per member and treats them as independent. The gap errors are
added in quadrature along the series and a warning states the difference.

**What is deliberately NOT done.** An OUTLIER MODEL on a wiggle-match member is
refused, at the request schema, at the importer and in the solver itself. It
follows from the gaps being hard: an outlier model gives a date room to sit
apart from the others and a wiggle match has none to give, so the agreement
index is what says a member does not belong. It was refused rather than
implemented because implementing it means marginalising an offset scale
independently per member -- `exact_chain`'s approximation rather than OxCal's
shared scale -- and OxCal does allow `Outlier()` in a `D_Sequence`, so a real
fixture should come before an implementation. The guard is repeated in
`wiggle_match.solve` and not only at the edges, because that function is the
one that would otherwise drop it: its likelihoods come from
`exact_chain._date_likelihood`, where the outlier mixture lives a level up in
`date_tables`.

A wiggle sequence nested inside a larger model is refused by name too, at the
importer and at the run. Its members are one calendar
age with fixed gaps added rather than n free parameters, and the surrounding
Boundaries and span terms would have to be told that; reading the series as n
dates in a phase -- the only thing the existing solvers could do with it -- would
report a match that was never made. The data model already carries what the
composed case needs, including the reference member
(`ELEMENT_PARAMS['wiggle_sequence']`), so adding the inference later is not a
migration of everything.

Two open decisions from the plan stay open, and both are recorded where they
bite: no pass/fail threshold is printed against the combination agreement (plan
S12.1 -- the same standard `agreement.py` holds `Amodel` to), and a sapwood
allowance is not modelled (S12.3).


### 21. ~~Legacy calibration distributions are stored in the old units~~
**Closed 2026-09-14 by checking, not by backfilling.** Carried as the last
MEDIUM item since 2026-08-14, when `CalibrationCurve.get_display_distribution`
started returning a probability density (integrating to 1 over calendar years)
instead of per-point masses, and rows written before that change were assumed
to still hold masses in `RadiocarbonResult.probability_distribution`. The note
weighed a one-off backfill against leaving them, and chose to leave them.

The question was moot. The live database's oldest `analysis_results` row is
dated 2026-08-22, eight days after the change; 0 of 422 stored results predate
it. Nothing was written in the old units, so there is nothing to backfill or to
delete. The only client-side thing that cared, the calendar-width weighting in
`utils/hpd.ts`, has therefore only ever seen densities.

The original note is kept above in Done #11's cross-reference, because the
sigma change of 2026-08-25 makes the same call about stored results and that
one was not moot: four analyses (50 results) predated it. They were re-run
the same day, which is the tool for it -- a migration is still the wrong one
for a few years' difference in HPD endpoints.

The removed text, for the record:

> **1. Legacy calibration distributions are stored in the old units**
> - **Current:** `CalibrationCurve.get_display_distribution` returns a probability
>   DENSITY (integrating to 1 over calendar years) rather than the per-point
>   masses it used to, so the series can be plotted against a calendar axis and
>   compared with a modelled posterior. Rows written before that change still hold
>   masses in `RadiocarbonResult.probability_distribution` — only for plain and
>   group calibrations; a Bayesian analysis's distribution comes from
>   `exact_chain._plottable` and was always a density.
> - **Impact:** Low, and bounded. The plot's y-axis auto-scales, so the shape is
>   identical and only the axis numbers differ. The reported HPD ranges are
>   computed on the full curve grid from the masses and never touched this series.
>   The one real effect is the client-side confidence re-selection in
>   `utils/hpd.ts`, which weights each point by the calendar width it stands for:
>   correct for a density, slightly off for a legacy mass array, and only where
>   the window spans one of the curve's resolution changes (IntCal20/SHCal20 step
>   1, 5, 10, 20 years in different ranges).
> - **Needed:** either a one-off backfill, or nothing — re-running an analysis
>   regenerates it. Deliberately not done as part of the fix: rewriting stored
>   results is a bigger call than correcting what gets written next.
> - Cheaper than when this was written: a backfill is now an ordinary Alembic
>   migration with somewhere to live (see Done #5). Note that Done #11 leaves
>   stored results out of step in the same way and for the same reason, so the two
>   backfills are one job if either is ever done.


## Testing & QA

- **Backend: 899 tests** in `backend/tests/`, across 29 files — 898 pass and
  1 skips (`pytest -q` in the container, 2026-09-07, 16m25s). `test_auth.py`
  (62), `test_curve_privacy.py` (41) and `test_failures.py` (15) are the day's
  additions. By file:

  | | | | |
  |---|---|---|---|
  | `test_imports.py` 104 | `test_exact_chain.py` 66 | `test_auth.py` 62 | `test_oxcal_agreement.py` 44 |
  | `test_exports.py` 44 | `test_project_samples.py` 42 | `test_curve_privacy.py` 41 | `test_curve_import.py` 41 |
  | `test_gibbs.py` 40 | `test_intervals.py` 33 | `test_pdf_report.py` 32 | `test_curve_view.py` 32 |
  | `test_model_tree.py` 31 | `test_curves.py` 30 | `test_agreement.py` 27 | `test_rerun.py` 26 |
  | `test_calibration.py` 25 | `test_curve_mix.py` 21 | `test_solver_reporting.py` 18 | `test_date_format.py` 18 |
  | `test_charcoal_outlier.py` 18 | `test_stratigraphy.py` 17 | `test_resolution.py` 17 | `test_jobs.py` 16 |
  | `test_validation.py` 15 | `test_failures.py` 15 | `test_analysis_list_intervals.py` 11 | `test_migrations.py` 8 |
  | `test_event_loop.py` 5 | | | |

  | | | | |
  |---|---|---|---|
  | `test_imports.py` 104 | `test_exact_chain.py` 66 | `test_oxcal_agreement.py` 44 | `test_exports.py` 44 |
  | `test_project_samples.py` 42 | `test_curve_import.py` 41 | `test_gibbs.py` 40 | `test_intervals.py` 33 |
  | `test_curve_view.py` 32 | `test_pdf_report.py` 32 | `test_model_tree.py` 31 | `test_curves.py` 30 |
  | `test_agreement.py` 27 | `test_rerun.py` 26 | `test_calibration.py` 25 | `test_curve_mix.py` 21 |
  | `test_solver_reporting.py` 18 | `test_date_format.py` 18 | `test_charcoal_outlier.py` 18 | `test_stratigraphy.py` 17 |
  | `test_resolution.py` 17 | `test_jobs.py` 16 | `test_validation.py` 15 | `test_analysis_list_intervals.py` 11 |
  | `test_migrations.py` 8 | `test_event_loop.py` 5 | `test_auth.py` 62 | `test_failures.py` 15 |

  Several are worth knowing about for *how* they test rather than what:
  - `test_imports.py` — its load-bearing tests parse the three
    `tests/fixtures/oxcal/*.oxcal` files and assert the result equals the
    hand-built trees in `oxcal_fixture.model_tree()`, which are themselves held
    against OxCal's own published output by `test_oxcal_agreement.py` — so the
    importer is chained onto an oracle reaching out to OxCal. The other half
    tests refusals: an OxCal construct the app does not model must fail the
    import naming itself rather than be read as the nearest thing that is
    implemented.
  - `test_exact_chain.py`, `test_gibbs.py` — the two solvers, against a
    brute-force integration, against each other, and against real OxCal 4.4
    runs.
  - `test_pdf_report.py` — reads the text back out of the generated PDF with
    nothing but the standard library, so the assertions are about what the
    report *says* rather than about it being a PDF at all.
  - `test_curves.py` — holds the served metadata against the loaded curve rather
    than against a second constant: what makes the point count right is that it
    is counted, which is exactly what the hand-written table it replaced could
    not claim.
  - `test_migrations.py` — asserts there is nothing left for `--autogenerate` to
    produce, which is the only way a model changed without a migration fails on
    the machine that changed it rather than on someone else's.
  - `test_event_loop.py` — asserts structurally that no handler can block the
    event loop, since that guarantee is a declaration rather than a timing.
  - `test_auth.py` — same structural argument for a second invariant:
    `TestEveryRouteIsProtected` walks the app's own route table, so an
    endpoint added without `current_user` fails the suite rather than becoming
    a quiet hole. Its other half builds two real accounts and tries fourteen
    routes between them.
  - `test_failures.py` — calls functions that really raise rather than
    constructing exceptions, because the classifier reads the traceback: a
    test written the other way would pass against a message-matching
    implementation that fails in production.
  - `conftest.py` provides a throwaway SQLite DB, built by running the real
    migrations. There is no `requires_pymc` skip marker: both solvers are pure
    numpy, so every test runs everywhere and a Bayesian analysis in the suite is
    a real one.
- **Every backend router has tests.** `harris_matrix.py` was the last one
  without, and it was deleted rather than covered (Done #6).
- **Frontend: 425 tests** in 33 files (Vitest, `npm test` in `frontend/`, all
  passing 2026-09-07), including `utils/thinPhases.test.ts` (21) and
  `api/client.test.ts` (14) added the same day. The largest: `utils/hpd.test.ts` (35),
  `utils/harrisLayout.test.ts` (32), `utils/curvePlotGeometry.test.ts` (26),
  `components/ModelTreeEditor.test.ts` (20), `theme/curveColors.test.ts` (18),
  `utils/curveScrubber.test.ts` (18), `utils/markdown.test.ts` (18),
  `utils/comparePairing.test.ts` (16), `utils/dateFormat.test.ts` (16),
  `utils/compareExport.test.ts` (15), then fourteen files between 5 and 14 and
  seven more below that.

  Most cover geometry that cannot be verified any other way: React Flow and
  Plotly draw into measured viewports, so the DOM reports elements at whatever
  coordinates they were handed and never says whether those coordinates collide
  or overflow. Those modules are therefore pure arithmetic, kept out of their
  components, with the defect each replaced pinned as a property — for the
  matrix, no implied edges, containment as nesting, children centred on their
  parent; for the stacked plot, a constant pixel gap below the axis at every row
  count, and a prior overlay that cannot paint outside its own row.
- **Still untested on the frontend:** `utils/stratigraphy.ts` and
  `ProjectSampleTable`'s `parsePastedDates` are pure functions and are cheap to
  cover, the harness being in place. The job polling added in Done #4
  (`hooks/useJob.ts`, `api/jobs.ts`) is likewise pure enough to test and is
  currently covered only end-to-end through the backend suite.
  `ImportDatesDialog` holds no parsing logic of its own — the file goes to the
  backend and the parsed result comes back — so it needs a component test (and
  hence jsdom) rather than a unit one.
- **Integration/E2E: none automated.** Flows are verified by hand against the
  running stack; the job queue and the progress display were checked that way on
  2026-08-19.

---

## Environment Notes

1. **`npm install` works.** The previously-recorded "partially installed
   node_modules" problem was caused by a committed `package-lock.json`
   containing 21 `@esbuild/*` entries with no `version` field, which crashed
   npm's resolver with `Invalid Version`. Regenerating the lockfile fixed it,
   and also cleared what looked like unrelated baseline `tsc` errors
   (`reactflow` "no exported member", MUI `Grid`/`Stack` overload mismatches).
   `tsc --noEmit` was clean at 0 errors on 2026-08-13, and `npm run build`
   succeeds.
2. **Files can be deleted.** The old note that `rm` returns "Operation not
   permitted" no longer applies — verified directly, and again on 2026-08-13.
   The four superseded frontend stubs (`BayesianConfig.tsx`,
   `HarrisMatrixEditor.tsx`, `StratigraPhyForm.tsx`, and `MeasurementForm.tsx`
   which had already been removed by the time of this note) are gone. So is
   `backend/app/models/sample.py` — a second, incompatible `RadiocarbonSample`
   mapped to the same table as the real one in `app/models.py`, self-labelled
   `# DEAD CODE -- do not import from here` with the same "can't delete" note;
   that note was also stale.
3. **`frontend/src/services/api.ts` was entirely orphaned** — an axios-based API
   client with its own typed interfaces, imported by nothing (every page now
   calls `fetch()` directly; the real types live in `types/index.ts` and inline
   per component). Deleted along with it: `axios`, plus four more dependencies
   with zero imports anywhere in `src/` — `d3` (and `@types/d3`), `date-fns`,
   `html2canvas`, `jspdf`, `papaparse`, `@tanstack/react-table` (all
   charting/export work goes through Plotly and hand-rolled CSV parsing
   instead). `npm install` after removing them from `package.json` dropped 69
   packages from `node_modules`. This is also what fixed the `ImportMeta.env`
   error noted above — it lived in the dead `api.ts` file, not in anything
   actually loaded by the app, which is why `tsc` had been reporting it as a
   "trivial pre-existing" error for as long as anyone had been filtering it out
   instead of tracing it.
4. **Config had drifted the same way.** `.env.example` and `docker-compose.yml`
   still declared `MCMC_SAMPLES`/`MCMC_TUNE`/`MCMC_CHAINS`/`MCMC_CORES` (from
   the removed PyMC sampler; per-analysis settings now live in
   `bayesian_config`, not the environment), `BACKEND_PORT`, `DEBUG`,
   `UPLOAD_DIR`/`MAX_FILE_SIZE` (no upload feature exists), `API_SECRET_KEY` (no
   auth did not exist yet — see Done #18) and `VITE_APP_NAME` (not read anywhere in
   `frontend/src`) — none read by any code in `backend/app/` or `frontend/src/`,
   confirmed by grep before removal. `docker-compose.yml`'s `CORS_ORIGINS`
   default and the same fallback hardcoded in `main.py` both still said
   `localhost:3000`, a port nothing in this app has used since the frontend
   moved to Vite on 5173; fixed both. Also dropped the `version: '3.8'` key
   Compose has been warning is obsolete on every single `docker compose`
   invocation.
5. **Docker builds occasionally fail with `DeadlineExceeded`** fetching base
   image metadata — transient registry flakiness, not a config problem.
   Retrying after a minute works. Both services bind-mount their source and
   hot-reload, so the running app can be ahead of the built images.
6. **The runtime image does not contain the test dependencies.** The Dockerfile
   installs `requirements.txt` only, so `pytest` and `httpx` have to be
   installed into the container before the suite can run there:
   `docker compose exec backend pip install -r requirements-dev.txt`. Any
   rebuild loses them again. Noted 2026-08-20 after a rebuild did exactly that.
7. **Running the suite outside the container needs the dev requirements too.**
   A bare `pytest` from `backend/` fails to import `app` at all unless
   `PYTHONPATH` includes `backend/`, and four modules
   (`test_analysis_list_intervals.py`, `test_event_loop.py`,
   `test_migrations.py`, `test_pdf_report.py`) additionally need
   `python-multipart` and `alembic`. Both are environment gaps, not repo
   problems — the counts above are the full 781 with those installed.

---

## Where to start

1. **Revisit the queue and the connection pool.** Both were sized on the
   grounds that there was one user (Done #4), which stopped being true today.
   Open registration plus a single-worker queue means any account can occupy
   the only solver thread — the first thing to look at if this becomes public
   rather than sitting behind a VPN.
2. **Before any real deployment**, set `RADICAL_SECRET_KEY` and `RADICAL_ENV`.
   The app refuses to start without them once `RADICAL_ENV` says it is real,
   but that is a backstop, not a plan.
3. Everything else as concrete need arises.

---

*Status, 2026-09-14: calibration and Bayesian math correct and tested (including
OxCal-style Boundaries, both outlier models, durations and differences from the
joint posterior, wiggle matching in closed form and age-depth modelling against
a Poisson deposition process); project management, date collections, deploy,
duplicate/edit, re-run and side-by-side comparison working end to end; a long
run is queued, reports progress and can be stopped; the schema is versioned; the
user manual ships inside the app; and every project and analysis now belongs to
an account and is private to it. **Every HIGH and MEDIUM item this file has ever
carried is closed** -- the last MEDIUM one, on 2026-09-14, by finding the
database had never held the rows it worried about. What remains is four
elective items, of which one is a planned but unbuilt feature — spatial metadata/ΔR/dispersal (Open #4, and written
for a fork).*

*The one thing to do before this is reachable by anyone else: set
`RADICAL_SECRET_KEY`. Without it, login tokens are signed with a key published
in this repository.*