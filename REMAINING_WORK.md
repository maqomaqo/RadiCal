# Remaining Work: RadiCal

*Rewritten 2026-08-05 after re-auditing against the actual code. Every claim
below was checked by reading the referenced file or exercising the running
app — the previous version had drifted (it still described the filesystem as
unable to delete files, and listed as open several things that had since
landed). If you're reading this after more work has happened, treat it the
same way: verify before trusting.*

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
- **Bayesian chronological model** (`backend/app/services/bayesian.py`) —
  calibration-curve-aware likelihood, stratigraphic order constraints as
  smooth (SMC-safe) penalties, TPQ/TAQ limiters, per-sample practical prior
  bounds, and SMC sampling chosen for multimodal posteriors.
- **OxCal-style context Boundaries** — a context's dates are modelled as a
  random uniform scatter between a start and end Boundary; the Boundaries
  are analytically marginalized out of the MCMC and recovered from their
  exact conditional afterwards, so "Modelled Start/End" genuinely extend
  beyond the oldest/youngest dated sample. Verified against the closed form
  `E[a+b] = 2w/(K-3)`.
- **Stratigraphy / Harris Matrix UI** — contexts, above/below/equal
  relationships, TPQ/TAQ, a drag-and-drop Harris matrix, and live cycle
  detection that blocks a contradictory run.
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
- `docs/USER_MANUAL.md` still describes a "4-step workflow" that doesn't
  exist, and documents PDF export as a working feature.
- `README.md` advertises "PDF export capabilities" and export of tables and
  graphs as "CSV, PDF, and image files".
- Neither mentions project date collections, deploy, duplicate/edit, re-run,
  or context Boundaries — i.e. most of what the app now does.
- **Impact:** High, and cheap to fix. Wrong docs are worse than none: they
  send a user looking for a PDF button that returns a 501.
- **Effort:** 1–2 hours.

---

## Priority: MEDIUM

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

### 6. Wire the curves API into the frontend
- **Current:** `GET /api/curves` exists but nothing calls it —
  `NewAnalysis.tsx` hardcodes its own `<MenuItem value="intcal20">` list, and
  `ProjectSampleTable.tsx` now hardcodes a *second* copy. The endpoint's
  metadata is also wrong: it claims IntCal20 has 5,141 points; the loaded
  curve has 9,501.
- **Needed:** fetch options from `/api/curves` (single source of truth) or
  delete the endpoint. Either way fix the point counts.
- **Impact:** Medium — harmless today, but there are now two hardcoded lists
  to forget when a curve is added.
- **Effort:** ~1 hour.

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
Upload custom curves; mix marine/terrestrial curves (the mixing math isn't
implemented at all). ~2–3 hours for upload; mixing is a separate piece of
work.

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

- **Backend: 80 tests**, all passing, in `backend/tests/`:
  - `test_calibration.py` (10) — calibration and HPD logic.
  - `test_validation.py` (15) — request validation.
  - `test_stratigraphy.py` (16) — cycle detection, group expansion, TPQ/TAQ.
  - `test_bayesian.py` (9) — Boundary conditional against its closed form,
    plus end-to-end MCMC behaviour.
  - `test_rerun.py` (8) — project assignment, rerun, project batch rerun.
  - `test_project_samples.py` (21) — date collection CRUD, deploy, snapshot
    semantics, duplicate, edit-inputs.
  - `conftest.py` provides a throwaway SQLite DB and a `requires_pymc` skip
    marker; tests needing a real MCMC run are marked with it.
- **Not covered:** `results.py`, `curves.py`, `harris_matrix.py` have no
  tests at all (14 endpoints between them).
- **Frontend tests: none.** No Vitest/Jest/Testing Library in `package.json`.
  `utils/hpd.ts`, `utils/stratigraphy.ts` (cycle detection) and
  `ProjectSampleTable`'s `parsePastedDates` are pure functions and would be
  the cheapest, highest-value place to start.
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
   mismatches). `tsc --noEmit` is now down to 6 trivial pre-existing warnings
   (unused `React` imports, one `ImportMeta.env`), and `npm run build`
   succeeds.
2. **Files can be deleted.** The old note that `rm` returns "Operation not
   permitted" no longer applies — verified directly. This means the four
   superseded stubs (`BayesianConfig.tsx`, `HarrisMatrixEditor.tsx`,
   `MeasurementForm.tsx`, `StratigraPhyForm.tsx`, all now just a comment and
   `export {}`) can and should actually be deleted rather than left as
   tombstones.
3. **Docker builds occasionally fail with `DeadlineExceeded`** fetching base
   image metadata — transient registry flakiness, not a config problem.
   Retrying after a minute works. Both services bind-mount their source and
   hot-reload, so the running app can be ahead of the built images.

---

## Suggested Order

1. **Rewrite `README.md` and `docs/USER_MANUAL.md`** to match reality — the
   cheapest fix with the highest chance of misleading someone if skipped.
2. **PDF export** — the last big advertised-but-missing feature; fold in the
   context Boundaries that CSV export also misses (gap #2).
3. **Delete the four superseded component stubs** now that deletion works.
4. **Async MCMC** — the synchronous "Re-run All" is the most likely thing to
   feel broken as projects grow.
5. **Wire up the curves API** before a fourth hardcoded curve list appears.
6. **Frontend tests** for the pure utils, then the untested backend routers.
7. Everything else — auth, Alembic, pooling, comparison UI, curve
   management — as concrete need arises.

---

*Status: calibration and Bayesian math correct and tested (including
OxCal-style Boundaries); project management, date collections, deploy,
duplicate/edit and re-run all working end to end. PDF export and accurate
documentation are the two remaining high-priority gaps.*
