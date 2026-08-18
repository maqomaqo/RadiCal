# Remaining Work: RadiCal

*Rewritten 2026-08-05, re-audited 2026-08-13 (file/documentation cleanup pass
-- see the Environment Notes and Priority #2 below for what changed). Every
claim below was checked by reading the referenced file or exercising the
running app. If you're reading this after more work has happened, treat it
the same way: verify before trusting.*

---

## What's Actually Working

Listed because some of it is easy to assume is missing, and because the
previous revisions of this file understated parts of it.

- **Calibration math** (`backend/app/services/calibration.py`) — full
  IntCal20/Marine20/SHCal20 curves (9,501 points for IntCal20), correct
  quadrature error combination, and the curve-error normalization term
  matching current OxCal/CALIB practice.
- **Multi-range HPD reporting** — disjoint calendar-age ranges with per-range
  probabilities, on both the direct-calibration and MCMC paths.
- **Bayesian chronological model** — calibration-curve-aware likelihood,
  stratigraphic order constraints, TPQ/TAQ limiters and per-sample practical
  prior bounds. Solved two ways: `backend/app/services/exact_chain.py`
  integrates a chain-shaped model exactly, and
  `backend/app/services/gibbs.py` samples anything else with OxCal's MH/Gibbs
  scheme. Both are pure numpy; the PyMC/SMC sampler they replaced is gone.
- **OxCal-style context Boundaries** — a context's dates are modelled as a
  random uniform scatter between a start and end Boundary; the Boundaries
  are analytically marginalized out of the MCMC and recovered from their
  exact conditional afterwards, so "Modelled Start/End" genuinely extend
  beyond the oldest/youngest dated sample. Verified against the closed form
  `E[a+b] = 2w/(K-3)`.
- **Stratigraphy / Harris Matrix UI** — the model tree editor, TPQ/TAQ, and a
  laid-out Harris matrix derived from the tree (`utils/harrisLayout.ts`):
  nesting as boxes within boxes, Boundaries as labelled rules, and ordering
  arrows only between adjacent blocks nothing else already separates. The
  matrix is a read-only view; a tree cannot contain a cycle, so the cycle
  check that guarded the old pairwise-relationship editor is gone with it.
- **Project management** — projects, assigning existing analyses to a project
  after the fact, re-running a single analysis or every analysis in a
  project, and deleting either.
- **Project date collections** — a project holds its own set of dates
  (`ProjectSample`), enterable individually or by bulk paste, deployable in
  any selection into a new analysis and auto-grouped by default context.
  Deploy copies the values, so an analysis is a self-contained snapshot.
- **Duplicate and edit analyses** — `POST /analysis/{id}/duplicate` deep-copies
  inputs (no results, status `pending`); `PUT /analysis/{id}/inputs` replaces
  a saved analysis's inputs with full validation and clears stale results.
  The frontend edit mode reuses the New Analysis → Stratigraphy pages.
- **Posterior visualization** — Plotly charts with shaded HPD bands, modelled
  vs unmodelled comparison, selectable HPD confidence, and opt-in point
  estimates.
- **Real CSV/JSON export**, **input validation** on all analysis endpoints,
  and **self-healing schema drift** (`database.py`, for added columns; new
  tables come from `create_all()`).
- **Backend tests** — 80, in `backend/tests/`.

---

## Priority: HIGH

### 1. PDF report generation
- **Current:** `GET /results/{id}/pdf` returns a proper `501` (it does not
  pretend to work), but the feature doesn't exist.
- **Needed:** ReportLab report — metadata, results table, posterior plots,
  sample details, curve/method references. `reportlab` is already in
  `requirements.txt`.
- **Impact:** High. Still the largest gap between what `README.md` claims and
  what the app does, and there's no citable/shareable output without it.
- **Effort:** 2–3 hours.

### 2. Documentation is materially wrong
**Resolved 2026-08-13.** `README.md`, `docs/USER_MANUAL.md` and
`docs/INSTALLATION.md` were rewritten against the actual running app: no more
PyMC/D3.js/react-table in the tech stack (none are dependencies or imports
any more), no fictional "4-step workflow", no PDF-export-as-working-feature,
correct frontend port (5173, not 3000 — `docs/INSTALLATION.md` had
previously sent two different ports for the same thing in adjacent
sections), a real API endpoint list instead of invented ones
(`POST /api/analysis/multi` never existed; `/api/harris-matrix/*` and
`/api/results/{id}/graphs` do exist but aren't mentioned since nothing in
the frontend calls them — see #6 below), and coverage of what the app
actually does now: three analysis types, project date collections and
deploy, duplicate/edit/re-run, the model-tree Harris matrix, the agreement
index, and the prior/posterior plot overlay. `README.md`'s license section
also claimed MIT; the repo's `LICENSE` file is CC0.

---

## Priority: MEDIUM

### 3a. Legacy calibration distributions are stored in the old units
- **Current:** `CalibrationCurve.get_display_distribution` now returns a
  probability DENSITY (integrating to 1 over calendar years) rather than the
  per-point masses it used to, so the series can be plotted against a
  calendar axis and compared with a modelled posterior. Rows written before
  that change still hold masses in `RadiocarbonResult.probability_distribution`
  — only for plain and group calibrations; a Bayesian analysis's distribution
  comes from `exact_chain._plottable` and was always a density.
- **Impact:** Low, and bounded. The plot's y-axis auto-scales, so the shape is
  identical and only the axis numbers differ. The reported HPD ranges are
  computed on the full curve grid from the masses and never touched this
  series. The one real effect is the client-side confidence re-selection in
  `utils/hpd.ts`, which now weights each point by the calendar width it
  stands for: correct for a density, slightly off for a legacy mass array,
  and only where the window spans one of the curve's resolution changes
  (IntCal20/SHCal20 step 1, 5, 10, 20 years in different ranges).
- **Needed:** either a one-off backfill, or nothing — re-running an analysis
  regenerates it. Deliberately not done as part of the fix: rewriting stored
  results is a bigger call than correcting what gets written next.


### 3. Async MCMC processing
- **Current:** still fully synchronous in the request handler. This got more
  pressing, not less: "Re-run All" on a project now runs *every* analysis in
  one blocking request, and deploy→edit→run is a normal flow.
- **Needed:** a task queue (Celery/RQ) plus a job-status endpoint the UI can
  poll; `Analysis.status` already has the `pending`/`running`/`completed`/
  `failed` vocabulary to carry it.
- **Effort:** 3–4 hours.

### 4. Authentication & user management
- **Current:** none. Anyone can read/modify/delete any project or analysis.
- Fine for single-user local use; required before any shared deployment.
- **Effort:** 3–4 hours for basic JWT + user table.

### 5. Real database migrations (Alembic)
- **Current:** `_ensure_schema_migrations` in `database.py` is a
  manually-maintained list that only handles "add column". New tables are
  picked up by `create_all()` (that's how `project_samples` arrived), but
  renames, type changes and backfills aren't covered at all.
- **Effort:** 2–3 hours including migrating the stopgap away.

### 6. ~~Wire the curves API into the frontend~~ (and decide about harris_matrix.py)
- **Curves: done 2026-08-18.** `backend/app/services/curve_registry.py` is now
  the single source of truth — it measures each curve's metadata from the
  curve that actually loaded, and the five separate hardcoded lists (the
  loaders in `calibration.py`, the `Literal` in `schemas/sample.py`, the table
  in `api/curves.py`, the OxCal name map in `oxcal_script.py`, and the two
  `<MenuItem>` lists in the frontend) all resolve through it. The wrong point
  counts are gone with the table that held them: the API reported 5,141 /
  1,341 / 5,141 where the loaded curves hold 9,501 / 5,501 / 9,501.
  `frontend/src/api/curves.ts` + `hooks/useCurves.ts` are what the pickers
  read, and `tests/test_curves.py` (29 tests) covers a module that had none.
  See `docs/CURVE_MANAGER_PLAN.md` for what this was phase 1 of.
- **Still open:** `harris_matrix.py`.
- `harris_matrix.py`'s two endpoints (`/api/harris-matrix/validate`,
  `/api/harris-matrix/relationships`) are in the same position: live routes,
  operating on a plain nodes/edges shape that predates the model tree
  (`services/model_tree.py`) the real Harris matrix editor
  (`HarrisMatrixDiagram.tsx`) actually talks to via the analysis endpoints
  directly. Nothing in the frontend calls them either.
- **Needed:** for curves, fetch options from `/api/curves` (single source of
  truth) or delete the endpoint — either way fix the point counts. For
  harris_matrix.py, a call: keep it as a documented-but-unused piece of API
  surface for external consumers, or delete it. Left as-is rather than
  deleted outright during the 2026-08-13 cleanup pass, since removing live
  (if unused) API endpoints is a bigger call than deleting dead frontend
  code — worth a deliberate decision, not a drive-by deletion.
- **Impact:** Low, now that the curves half has landed — one API module that
  looks live but isn't connected to anything.
- **Effort:** harris_matrix.py is a delete-or-keep decision more than effort.

### 7. Database connection pooling
- **Current:** `create_engine(DATABASE_URL, echo=False)` with no
  `pool_size`/`max_overflow`. Only bites under concurrent load.
- **Effort:** 15 minutes.

---

## Priority: LOW

### 8. Multi-analysis comparison UI
Now considerably more useful than when first listed: deploying one set of
dates into several differently-modelled analyses is a first-class workflow,
and there's no way to view those results side by side. ~2–3 hours.

### 9. Advanced Bayesian features
Outlier detection (t-distribution likelihood), custom priors, explicit
phase-duration priors, and OxCal-style agreement indices. ~4–5 hours.

### 10. Excel export / measurement import into an analysis
CSV/JSON export works and bulk paste now covers getting dates *into a
project*. Still missing: Excel-formatted output, and importing measurements
directly into an analysis (as opposed to via a project collection). ~2 hours.

### 11. Calibration curve management UI
Planned in full in `docs/CURVE_MANAGER_PLAN.md`. **Phases 1-4 are done**:
the registry behind all of it (see #6), and the backend for user-imported
curves — parse-then-store preview, `.14c`/CSV/BC-AD reading, immutable curve
data guarded by a content hash checked on every load, archive-rather-than-
delete while in use, aliases so an OxCal file naming an imported curve
imports, and byte-for-byte export. Covered by `tests/test_curve_import.py`
(40 tests).
…and the `/curves` page over it: import dialog with a preview of the curve
before anything is stored, editable description/citation/aliases, download,
archive/restore, and a delete that asks what is using the curve first.
…and the curve browser at `/curves/{id}/browse`: curve shape with ±1σ/±2σ
bands, a full-range time scrubber, a windowed `/points` endpoint whose
decimation keeps each bucket's extremes, and a hover readout reporting the
local gradient.
**Remaining:** res14c-style resolution profiles (phase 5), mixed curves — the
mixing math still isn't implemented (6), compare/simulate (7) and docs (8).
~13–18 hours.

---

## Known Gaps and Rough Edges

Smaller, specific things found during this audit — none are blocking, but
each is a real edge.

1. **Deploying as `analysis_type: "calibration"` only calibrates the first
   date.** `DeployRequest` accepts `calibration`, but `_rerun_analysis`'s
   calibration branch does `sample = samples[0]` (correct for
   single-calibration analyses, which is all that used to exist). Not
   reachable from the UI — `ProjectSampleTable` always sends `bayesian` — but
   the API allows it and would silently drop the other dates. Either reject
   multi-date calibration deploys or loop over the samples.
2. **CSV/JSON export omits context Boundaries.** `results.py` exports
   per-sample rows only, so the modelled start/end shown on the Results page
   can't be exported. Worth adding when PDF export is built.
3. **A context with only 2–3 dates gets very wide Boundaries** (95% HPD can
   span thousands of years). This is mathematically correct — `E[a+b]` is
   only finite for K ≥ 4, so few dates genuinely don't constrain a phase's
   duration — and OxCal behaves the same way, but it surprises people. A
   short note in the UI next to the Context Spans table would help.
4. **MCMC failure modes aren't distinguished.** A failed run returns a 400
   with the raw exception; "bad input", "didn't converge" and "crashed" all
   look the same to the user. Convergence diagnostics (r_hat/ESS) are
   computed during development but never surfaced.
5. **No error boundaries in the frontend.** Most pages have `try/catch` and
   an error state, but an unexpected render-time exception still blanks the
   page.
6. **`ProjectSample` has no per-analysis provenance link.** Deploy copies
   values deliberately (see the snapshot decision), but nothing records
   *which* collection entry an analysis sample came from, so there's no "this
   analysis is using an outdated version of this date" indicator. Adding a
   nullable `source_project_sample_id` would enable that without weakening
   the snapshot guarantee.

---

## Testing & QA

- **Backend: 491 tests** (`pytest --collect-only`, 2026-08-14) in
  `backend/tests/`, including several files added since the 80-test count
  above was first recorded: `test_agreement.py` (the agreement index),
  `test_charcoal_outlier.py`, `test_solver_reporting.py`,
  `test_exports.py` (47 tests covering `results.py`'s export/table/graphs/pdf
  routes — see below), and `test_imports.py` (92 tests covering CSV and
  `.oxcal` file import). Still present from before:
  - `test_calibration.py` — calibration and HPD logic.
  - `test_validation.py` — request validation.
  - `test_stratigraphy.py` — cycle detection, group expansion, TPQ/TAQ.
  - `test_exact_chain.py`, `test_gibbs.py` — the two solvers, against a
    brute-force integration, against each other, and against real OxCal 4.4
    runs.
  - `test_rerun.py` — project assignment, rerun, project batch rerun.
  - `test_project_samples.py` — date collection CRUD, deploy, snapshot
    semantics, duplicate, edit-inputs.
  - `test_imports.py` — file import. Its load-bearing tests parse the three
    `tests/fixtures/oxcal/*.oxcal` files and assert the result equals the
    hand-built trees in `oxcal_fixture.model_tree()`, which are themselves
    held against OxCal's own published output by `test_oxcal_agreement.py` —
    so the importer is chained onto an oracle reaching out to OxCal. The
    other half tests refusals: an OxCal construct the app does not model must
    fail the import rather than be read as the nearest thing that is
    implemented.
  - `conftest.py` provides a throwaway SQLite DB. There is no
    `requires_pymc` skip marker: both solvers are pure numpy, so every test
    runs everywhere and a Bayesian analysis in the suite is a real one.
  - `test_curves.py` — the curve registry and the endpoints over it (added
    2026-08-18). Its load-bearing tests hold the served metadata against the
    loaded curve rather than against a second constant: what makes the point
    count right is that it is counted, which is exactly what the hand-written
    table it replaced could not claim.
- **Not covered:** `harris_matrix.py` still has no tests at all — consistent
  with it being unused by the frontend; see #6 above. `results.py` and
  `curves.py` are no longer in this list.
- **Frontend: 127 tests** (Vitest, `npm test` in `frontend/`), in
  `utils/harrisLayout.test.ts` (27), `components/ModelTreeEditor.test.ts` (15),
  `utils/distribution.test.ts` (12), `utils/hpd.test.ts` (10),
  `utils/curveScrubber.test.ts` (18), `api/curves.test.ts` (9),
  `components/ImportCurveDialog.test.ts` (8), `utils/stackedPlotGeometry.test.ts` (7),
  `utils/curveShape.test.ts` (6), `utils/plotExport.test.ts` (5),
  `theme/diagramColors.test.ts` (5) and
  `components/ProjectSampleTable.test.ts` (5). Most of these cover geometry that
  cannot be verified any other way: React Flow and Plotly draw into measured
  viewports, so the DOM reports elements at whatever coordinates they were
  handed and never says whether those coordinates collide or overflow. Those
  modules are therefore pure arithmetic, kept out of their components, with
  the defect each replaced pinned as a property — for the matrix, no implied
  edges, containment as nesting, children centred on their parent; for the
  stacked plot, a constant pixel gap below the axis at every row count, and
  a prior overlay that cannot paint outside its own row.
- **Still untested on the frontend:** `utils/stratigraphy.ts` and
  `ProjectSampleTable`'s `parsePastedDates` are pure functions and are now
  cheap to cover, the harness being in place. `ImportDatesDialog` holds no
  parsing logic of its own — the file goes to the backend and the parsed
  result comes back — so it needs a component test (and hence jsdom) rather
  than a unit one.
- **No linting.** The `lint` script was dropped: there has never been an
  ESLint config anywhere in the repo, so it only ever exited "couldn't find a
  configuration file" — a script that cannot run is worse than no script,
  since it reads as coverage that exists. `npm run type-check` (`tsc
  --noEmit`) is the static check that does work. Adding ESLint back is a real
  option, but it means choosing a ruleset and fixing whatever it finds, not
  restoring a line to `package.json`. The `eslint`, `@typescript-eslint/*`
  and `eslint-plugin-*` devDependencies are still installed and now have
  nothing to run them.
- **Integration/E2E: none automated.** The flows in this session were
  verified by hand against the running stack.

---

## Environment Notes

1. **`npm install` works.** The previously-recorded "partially installed
   node_modules" problem was caused by a committed `package-lock.json`
   containing 21 `@esbuild/*` entries with no `version` field, which crashed
   npm's resolver with `Invalid Version`. Regenerating the lockfile fixed it,
   and also cleared what looked like unrelated baseline `tsc` errors
   (`reactflow` "no exported member", MUI `Grid`/`Stack` overload
   mismatches). `tsc --noEmit` is now **clean** (0 errors, 2026-08-13) — the
   remaining unused-`React`-import warnings and the `ImportMeta.env` error
   were fixed as part of the file cleanup below, not just filtered out.
   `npm run build` succeeds.
2. **Files can be deleted.** The old note that `rm` returns "Operation not
   permitted" no longer applies — verified directly, and again on
   2026-08-13. The four superseded frontend stubs (`BayesianConfig.tsx`,
   `HarrisMatrixEditor.tsx`, `StratigraPhyForm.tsx`, and `MeasurementForm.tsx`
   which had already been removed by the time of this note) are gone. So is
   `backend/app/models/sample.py` — a second, incompatible `RadiocarbonSample`
   mapped to the same table as the real one in `app/models.py`, self-labelled
   `# DEAD CODE -- do not import from here` with the same "can't delete"
   note; that note was also stale.
3. **`frontend/src/services/api.ts` was entirely orphaned** — an axios-based
   API client with its own typed interfaces, imported by nothing (every page
   now calls `fetch()` directly; the real types live in `types/index.ts` and
   inline per component). Deleted along with it: `axios`, plus four more
   dependencies with zero imports anywhere in `src/` — `d3` (and
   `@types/d3`), `date-fns`, `html2canvas`, `jspdf`, `papaparse`,
   `@tanstack/react-table` (all charting/export work goes through Plotly and
   hand-rolled CSV parsing instead). `npm install` after removing them from
   `package.json` dropped 69 packages from `node_modules`. This is also what
   fixed the `ImportMeta.env` error noted above — it lived in the dead
   `api.ts` file, not in anything actually loaded by the app, which is why
   `tsc` had been reporting it as a "trivial pre-existing" error for as long
   as anyone had been filtering it out instead of tracing it.
4. **Config had drifted the same way.** `.env.example` and
   `docker-compose.yml` still declared `MCMC_SAMPLES`/`MCMC_TUNE`/
   `MCMC_CHAINS`/`MCMC_CORES` (from the removed PyMC sampler; per-analysis
   settings now live in `bayesian_config`, not the environment),
   `BACKEND_PORT`, `DEBUG`, `UPLOAD_DIR`/`MAX_FILE_SIZE` (no upload feature
   exists), `API_SECRET_KEY` (no auth exists yet — see Priority #4 above) and
   `VITE_APP_NAME` (not read anywhere in `frontend/src`) — none read by any
   code in `backend/app/` or `frontend/src/`, confirmed by grep before
   removal. `docker-compose.yml`'s `CORS_ORIGINS` default and the same
   fallback hardcoded in `main.py` both still said `localhost:3000`, a port
   nothing in this app has used since the frontend moved to Vite on 5173;
   fixed both. Also dropped the `version: '3.8'` key Compose has been
   warning is obsolete on every single `docker compose` invocation.
5. **Docker builds occasionally fail with `DeadlineExceeded`** fetching base
   image metadata — transient registry flakiness, not a config problem.
   Retrying after a minute works. Both services bind-mount their source and
   hot-reload, so the running app can be ahead of the built images.

---

## Suggested Order

1. ~~Rewrite `README.md` and `docs/USER_MANUAL.md` to match reality~~ —
   **done 2026-08-13**, along with `docs/INSTALLATION.md`.
2. **PDF export** — the last big advertised-but-missing feature; fold in the
   context Boundaries that CSV export also misses (gap #2).
3. ~~Delete the four superseded component stubs~~ — **done 2026-08-13**,
   along with the dead `app/models/sample.py`, the orphaned
   `frontend/src/services/api.ts`, six unused npm dependencies, and the
   config drift both had left behind (see Environment Notes).
4. **Async MCMC** — the synchronous "Re-run All" is the most likely thing to
   feel broken as projects grow.
5. ~~**Wire up the curves API**~~ — **done 2026-08-18** (see #6); decide
   whether `harris_matrix.py` stays.
6. **Frontend tests** for the pure utils, then the untested backend router
   (`harris_matrix.py` — `results.py` and `curves.py` are now covered).
7. Everything else — auth, Alembic, pooling, comparison UI, curve
   management — as concrete need arises.

---

*Status: calibration and Bayesian math correct and tested (including
OxCal-style Boundaries); project management, date collections, deploy,
duplicate/edit and re-run all working end to end. Documentation now matches
the app (2026-08-13) and the codebase's accumulated dead files/config have
been cleared out. PDF export is the one remaining high-priority gap.*
