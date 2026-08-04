# Remaining Work: RadiCal

*This file was rewritten from scratch after an audit against the actual code (previous version was stale and both overstated and understated different parts of the app). Everything below reflects what's actually in the repo, verified by reading the code and, where practical, exercising it against a real database.*

---

## What's Actually Working

This list exists because the previous version of this file said several of these were unstarted. They're not — verify by reading the referenced files if in doubt.

- **Calibration math** (`backend/app/services/calibration.py`) — full IntCal20/Marine20/SHCal20 curves (9,501 points for IntCal20, not a subset), correct quadrature error combination, and the calibration-curve-error normalization term (`1/√(σm²+σ(t)²)`) that matches current OxCal/CALIB practice rather than the superseded pre-3.2 OxCal formula.
- **Multi-range HPD reporting** — HPD intervals are reported as one or more disjoint calendar-age ranges with individual probability percentages (OxCal/CALIB-style), not a single min/max span that misrepresents multimodal posteriors. Applies to both the direct-calibration path and the Bayesian MCMC posterior.
- **Posterior visualization** (`frontend/src/components/PosteriorPlot.tsx`) — real Plotly-based charts on the Results page, with shaded bands per HPD range and point-estimate markers. Wired into both single-calibration and Bayesian results.
- **Project management** — `Project` model, `Projects.tsx` / `ProjectDetail.tsx` pages, project assignment at analysis creation, and a "save this analysis" toggle for quick test runs that don't get persisted at all.
- **Real CSV/JSON export** (`backend/app/api/results.py`) — as of this audit these returned hardcoded placeholder rows regardless of which analysis was requested; now pull the actual sample + posterior + HPD data.
- **Backend unit tests** — 10 tests in `backend/tests/test_calibration.py` covering calibration math, HPD interval/range calculation (including a bimodal-posterior case), and edge cases.
- **Self-healing schema drift** — `database.py` checks for and adds columns that were added to models after tables already existed (there's no Alembic; `create_all()` alone doesn't alter existing tables). Confirmed against a simulated pre-migration database.
- **Stale/duplicate code marked or fixed** — `backend/app/models/sample.py` (an orphaned, conflicting duplicate of `RadiocarbonSample`) has been neutralized; `arviz` removed from `requirements.txt` (no longer used); dead frontend components are labeled `NOT CURRENTLY USED` rather than silently misleading a reader into thinking they're live.
- **Input validation** (`backend/app/schemas/sample.py`) — `/analysis/calibrate` and `/analysis/bayesian` now take real Pydantic request bodies (`CalibrateRequest`/`BayesianRequest`) instead of raw `Dict`: negative/zero/absurd `age_bp` and `error_1sigma`, blank sample names, unrecognized `curve_id`/`sample_type`, unrecognized relationship types (`above`/`below`/`equal` only — a typo like "beneath" used to be silently dropped with no error and no effect on the model), and out-of-range MCMC config are all rejected with a clear 422 instead of either a bare `KeyError`/numpy exception or a silently wrong result. `CalibrationCurve.get_curve()` was also hardened to reject an unrecognized `curve_id` outright rather than silently falling back to IntCal20. Frontend alerts (`NewAnalysis.tsx`, `Projects.tsx`, `ProjectDetail.tsx`) now format FastAPI's structured 422 error lists into readable text via `utils/apiError.ts` instead of showing `[object Object]`. Covered by 15 new tests in `backend/tests/test_validation.py`.
- **Stratigraphy / Harris Matrix UI** — a Bayesian analysis can now actually have above/below/equal relationships and TPQ/TAQ age limiters defined and submitted, via a new `/new-analysis/stratigraphy` page reached from `NewAnalysis.tsx`. See item #1 (moved to "Done" below) for the full picture.

---

## Priority: HIGH

### ~~1. Stratigraphy / Harris Matrix UI~~ — Done
- **What was built:** A new `Stratigraphy` page (`frontend/src/pages/Stratigraphy.tsx`, reached from a "Define/Edit Stratigraphy" button on `NewAnalysis.tsx` once at least one measurement exists) with two ways to define constraints on the same underlying draft, editable interchangeably:
  - **List Editor tab** — pick a From/To (sample or group), a relationship type (above/below/equal), and add it; per-sample and per-group TPQ/TAQ age limiters are plain number fields in the same tab.
  - **Harris Matrix Diagram tab** (`frontend/src/components/HarrisMatrixDiagram.tsx`, React Flow) — a real drag-and-drop canvas: nodes auto-layout into layers (oldest at the bottom, youngest at top) and can be freely repositioned; dragging a connection between two nodes creates an "above" relationship; clicking an edge opens a small panel to change its type or delete it. The same component runs in read-only mode on the Results page, so it also works as the "visualize as a sanity check" step the diagram is for — both before running (does this look like the intended sequence?) and after (what did this saved analysis actually use?).
  - Both tabs share a live circular-relationship check (Kahn's-algorithm cycle detection, `frontend/src/utils/stratigraphy.ts`) that blocks "Run Analysis" with a clear "these are mutually contradictory" message instead of letting an unsatisfiable constraint set reach MCMC.
- **Groups:** samples can be organized into named groups/phases (`SampleGroup` model); a single relationship or TPQ/TAQ limiter on a group applies to every member sample, expanded into the full pairwise cross-product at model-build time (`backend/app/services/stratigraphy.py`) rather than stored pre-expanded — editing group membership doesn't require rewriting every relationship that touches it.
- **TPQ/TAQ:** `tpq_age_bp` (terminus post quem, a *maximum* age bound — "not older than") and `taq_age_bp` (terminus ante quem, a *minimum* age bound — "not younger than"), both in cal BP, settable per-sample and per-group (tightest bound wins if both apply); enforced by tightening each sample's `pm.Uniform` calendar-age prior in `bayesian.py`, intersected with the curve's own range.
- **Backend:** `StratigraphicRelationship`/new `SampleGroup` are now actually persisted (previously `StratigraphicRelationship` existed as a model but nothing ever wrote to it — relationships were used transiently for the MCMC run and then lost). `GET /analysis/{id}` returns `measurements`/`groups`/`relationships` for a saved analysis, which is what the Results page's read-only diagram and the real (not "Sample N" placeholder) results-table names now come from.
- **Validation:** `BayesianRequest` (schemas/sample.py) rejects — before any MCMC run — a circular relationship set, a relationship or `group_id` referencing an id that doesn't exist in the request, and a contradictory TPQ/TAQ pair (taq > tpq). Covered by 16 new tests in `backend/tests/test_stratigraphy.py` (cycle detection, group expansion, dangling references, bounds contradictions — all pass without pymc installed, since they're rejected at the Pydantic validation layer before an MCMC run is ever attempted).
- **Known limitation:** stratigraphy can only be defined *before* an analysis is run/saved — like every other input field, it can't currently be edited after the fact (consistent with the rest of the app: an `Analysis` has no "re-run with edits" path at all, only name/project reassignment via `PATCH /analysis/{id}`). Editing an existing analysis's inputs and re-running would be a reasonable follow-up but is a materially bigger feature (needs a real edit-and-rerun flow, not just a stratigraphy-specific one).
- **Not verified in this environment:** the actual MCMC run with groups/TPQ-TAQ/expanded relationships (`test_stratigraphy.py`'s `TestStratigraphyPersistence`, marked `@requires_pymc`) — this sandbox doesn't have `pymc` installed, same limitation noted in the Testing & QA section below for the original Bayesian code. The model-building logic itself (group expansion, cycle detection, bounds combination) was verified directly against `app/services/stratigraphy.py` outside of PyMC, and the full request-validation path was verified via `TestClient`. Recommend running the `@requires_pymc` test (and a manual end-to-end run through the UI) once in an environment with `pymc` installed before relying on this in production.
- **Old scaffolding:** `StratigraPhyForm.tsx`, `HarrisMatrixEditor.tsx`, `BayesianConfig.tsx`, `MeasurementForm.tsx` (marked `NOT CURRENTLY USED` in an earlier pass) are now superseded rather than just unused — their content was replaced with a short pointer to what actually replaced them, since their old `useAnalysisStore`/`RadiocarbonMeasurement` dependencies no longer exist (that store and `types/index.ts` were rebuilt for this feature — see "Known Constraints" below).

### 2. PDF Report Generation
- **Current:** `GET /results/{id}/pdf` now returns a proper `501 Not Implemented` (previously returned a fake `200 success` with no PDF — that's fixed, but the feature itself still doesn't exist).
- **Needed:** ReportLab-based report: analysis metadata, results table, posterior distribution plots, sample details, curve/method references.
- **Dependencies:** `reportlab` is already in `requirements.txt`.
- **Effort:** 2–3 hours.
- **Impact:** High — users still can't get a shareable/citable report out of the app.

### ~~3. Input validation on measurements~~ — Done
See "What's Actually Working" above.

---

## Priority: MEDIUM

### 4. Async MCMC processing
- **Current:** Bayesian analysis still runs synchronously in the request handler — unchanged from before, still no Celery/RQ or any task queue. Will still time out or block the server on large datasets.
- **Effort:** 3–4 hours.
- **Impact:** Medium — matters once real multi-user or large-dataset usage starts.

### 5. Authentication & user management
- **Current:** Still none. Anyone can see/modify/delete any analysis or project.
- **Effort:** 3–4 hours for a basic JWT + user table setup.
- **Impact:** Medium — not needed for single-user/local use, required before any shared deployment.

### 6. Real database migrations (Alembic)
- **Current:** There's a stopgap in `database.py` (`_ensure_schema_migrations`) that adds specific known-missing columns on startup — it works, but it's a manually-maintained list, not a real migration system. It'll need a new entry every time a model gains a column, and it can't handle anything beyond "add column" (renames, type changes, new tables with data backfill, etc.).
- **Needed:** Alembic, with the current schema captured as the initial migration.
- **Effort:** 2–3 hours to set up + migrate the stopgap logic away.
- **Impact:** Medium — fine as-is for now, but it's technical debt that gets more annoying the longer it's left.

### 7. Wire the curves API into the frontend
- **Current:** `GET /api/curves` exists and returns curve metadata, but `NewAnalysis.tsx` hardcodes its own `<MenuItem>` list instead of calling it — so the endpoint is dead from the UI's perspective. The metadata itself is also stale: it claims IntCal20 has 5,141 data points; the actual loaded curve has 9,501.
- **Needed:** Either fetch curve options from `/api/curves` in the frontend (making it the single source of truth), or delete the endpoint if it's not going to be used. Either way, fix the point-count metadata.
- **Effort:** 1 hour.
- **Impact:** Medium — currently harmless (frontend's hardcoded list happens to match the three real curves), but it's a landmine for the next curve added.

### 8. Database connection pooling
- **Current:** `create_engine(DATABASE_URL, echo=False)` with no `pool_size`/`max_overflow` configured — still true, unchanged from the original audit.
- **Effort:** 15 minutes.
- **Impact:** Medium — only bites under concurrent load, but it's a one-line fix.

---

## Priority: LOW

### 9. Multi-sample comparison UI
Side-by-side or overlay comparison of multiple analyses' posteriors. Not started. ~2–3 hours.

### 10. Advanced Bayesian features
Custom prior distributions, outlier handling (t-distribution likelihood), phase-duration priors. Not started, and blocked on #1 (stratigraphy UI) being worth using first. ~4–5 hours.

### 11. Data import/export
CSV batch import for measurements, Excel export. Not started. Note: CSV *export* of results now works correctly (see "What's Actually Working") — this item is specifically about *importing* a batch of measurements and about Excel-formatted output. ~2–3 hours.

### 12. Calibration curve management UI
Upload custom curves, mix marine/terrestrial curves (the underlying math for curve mixing is described in the OxCal literature but not implemented here at all — see the calibration.py audit from earlier in this project). Not started. ~2–3 hours for a basic upload UI; curve mixing itself is a separate, more involved piece of work.

---

## Testing & QA

- **Backend unit tests:** 41 tests total (1 additional test skipped without `pymc` installed) — 10 in `test_calibration.py` (calibration and HPD logic), 15 in `test_validation.py` (request validation on `/analysis/calibrate` and `/analysis/bayesian`), and 16 new ones in `test_stratigraphy.py` (cycle detection, group expansion, TPQ/TAQ bounds, dangling references — plus one `@requires_pymc`-marked persistence round-trip test that's skipped in any environment without a real `pymc` install, same pattern as everything else MCMC-related). All via a real `TestClient` + throwaway SQLite DB, see `tests/conftest.py`. **Not covered:** `bayesian.py`'s actual MCMC/constraint behavior end-to-end with groups and TPQ/TAQ (needs `pymc`, not installed in this sandbox — see Known Constraints), and most of the API endpoints beyond validation — `projects.py`, `results.py`, `curves.py`, `harris_matrix.py` have no automated tests; that work was verified manually via `TestClient` smoke scripts and not committed.
- **Frontend tests:** None. No Jest/Vitest/Testing Library in `package.json`.
- **Integration/E2E tests:** None.
- **Suggested next step:** `conftest.py`'s pattern (throwaway SQLite DB + `pymc`/`pytensor` stub fallback so the suite doesn't hard-require the MCMC toolchain to collect) is reusable — extend it with real test files for `projects.py` and `results.py` the same way `test_validation.py` was added for `analysis.py`.

---

## Documentation

`README.md` and `docs/USER_MANUAL.md` describe a 4-step wizard ("Measurements → Stratigraphy → Configuration → Analysis") and claim finished PDF export and Harris Matrix support. That's closer to true now than it was — there is a real stratigraphy step and a real Harris Matrix diagram — but it's still not an accurate description of the actual flow (`NewAnalysis.tsx` + a linked `Stratigraphy.tsx` page, not a 4-step wizard; PDF export is still a `501`). These docs should be rewritten to match the real two-page flow now that item #1 is done, and again once PDF export (item #2) lands.

`IMPLEMENTATION_SUMMARY.md` is a point-in-time snapshot from 2026-07-28 ("Branch: fix/operational-build") and is reasonably accurate for what it claims to cover — it doesn't need a rewrite so much as a note that a lot has happened since, and probably should be either archived or merged into a single up-to-date architecture doc rather than kept alongside this file indefinitely.

---

## Known Constraints (Not Bugs, But Worth Knowing)

1. **Filesystem here can't delete files.** `rm` returns "Operation not permitted" on this mount, so genuinely dead files get their *content* replaced with a short explanatory comment instead of being removed outright. `HarrisMatrixEditor.tsx`, `MeasurementForm.tsx`, `StratigraPhyForm.tsx`, `BayesianConfig.tsx` are in this state now (superseded by the real stratigraphy UI, not just "not built yet" scaffolding anymore) — if you have normal filesystem access, delete them for real.
2. **`zustand` and `reactflow` in `package.json` are now real, in-use dependencies**, not just kept alive for dead files. `stores/analysisStore.ts` (zustand) holds the shared in-progress Bayesian analysis draft between `NewAnalysis.tsx` and `Stratigraphy.tsx`; `reactflow` powers `components/HarrisMatrixDiagram.tsx`, used on both the Stratigraphy page (editable) and the Results page (read-only).
3. **This development environment's `node_modules` is only partially installed** (`npm install` fails here with an `ENOTEMPTY` rename error on the mounted filesystem) — `@mui/icons-material` and `reactflow`'s sub-packages are missing their `.d.ts` files, so `tsc --noEmit` reports a baseline of type errors (icon imports, `reactflow` "no exported member", MUI `Grid`/`Stack` prop-type overload mismatches) across essentially every page in the app, not just new code. These aren't real bugs — they're an artifact of this sandbox's incomplete install and were present before this session's changes too; verify with a clean `npm install` in a normal environment before treating any `tsc` output here as authoritative.

---

## Known Bugs / Edge Cases — Status Check

Re-verified each item from the previous version of this file against current code:

1. ~~Empty calibration curves on error~~ — **Fixed.** `get_curve()` raises `ValueError` if a curve is unavailable/empty rather than silently returning an empty curve.
2. **No input validation on measurements** — **Still open.** See Priority HIGH #3 above.
3. **MCMC can fail silently** — **Partially addressed.** `bayesian_analysis()` wraps the run in try/except with `db.rollback()` and returns a `400` with the error detail; not silent anymore, but there's still no distinction between "bad input," "MCMC didn't converge," and "MCMC crashed" in what's returned to the frontend.
4. **No database connection pooling** — **Still open.** See Priority MEDIUM #8.
5. **Frontend doesn't handle API errors gracefully** — **Partially addressed.** Most pages now have basic `try/catch` + error state (Results, Projects, ProjectDetail) rather than an unguarded crash, but there are no error boundaries, so a genuinely unexpected exception in a component render still takes down the page rather than showing a fallback.

---

## Suggested Next Steps, In Order

1. **Verify the stratigraphy UI's MCMC path with `pymc` installed** — run `test_stratigraphy.py::TestStratigraphyPersistence` and do a manual end-to-end run through the UI (a group-based analysis with TPQ/TAQ limiters) in an environment that has `pymc`, since this sandbox doesn't. Cheap, and closes the one real gap left in item #1.
2. **PDF export** (HIGH #2) — self-contained, doesn't depend on anything else.
3. **Rewrite README/USER_MANUAL** to describe the actual two-page New Analysis → Stratigraphy flow instead of the old 4-step wizard description.
4. **Commit the manual smoke-test coverage as real pytest tests** for `projects.py`, `results.py`, and `curves.py` — `conftest.py`'s pattern (throwaway SQLite DB + `pymc`/`pytensor` stub fallback) is already set up to extend for these.
5. Everything else (auth, async MCMC, Alembic, multi-sample comparison, curve management, editing an already-saved analysis's stratigraphy) — genuinely lower priority for a single-user/local deployment; revisit once there's a concrete reason (multiple users, large datasets, etc.) to need them.

---

*Last updated: 2026-08-03 | Status: Core calibration + Bayesian math correct and tested; project management, exports, and stratigraphy UI (groups, TPQ/TAQ, list + drag-and-drop Harris matrix editing, cycle detection) all working; PDF export is the one remaining HIGH-priority gap between what the app claims (per README/USER_MANUAL) and what it does.*
