# RadiCal Desktop — a standalone Rust build

Written 2026-10-15. A plan, not a commitment: nothing here is built yet.

Goal: one installer per platform (`.dmg`, `.msi`, `.AppImage`/`.deb`), no Docker, no Postgres,
no Python, no account. Double-click, and the app opens on the user's own projects. Bayesian runs
fast enough that "Re-run All" on a project is something you wait for, not something you leave.

---

## 1. What the codebase gives us to work with

Checked against `main` at `7e9baee`.

| Layer | Size | What it is | Fate in the desktop build |
|---|---|---|---|
| `frontend/src` | ~53k lines TS/TSX | React 18, MUI, Plotly, React Flow, Zustand | **Kept.** Runs in the desktop webview. Small edits only (§5). |
| `backend/app/services` | ~25k lines | Calibration, curve registry, `exact_chain`, `exact_parallel`, `gibbs`, intervals, agreement, KDE, resolution, wiggle match, dispersal, OxCal parse/export, imports, PDF | **Ported to Rust.** This is the real work. |
| `backend/app/api` | ~10k lines | 14 FastAPI routers under `/api` | **Ported to Rust** as a local HTTP server with the same routes and JSON shapes. |
| `models.py` + Alembic | 15 tables | `String`/`Float`/`JSON`/`DateTime` only — no Postgres-specific types | **SQLite.** The test suite already runs on SQLite (`tests/conftest.py`), so the schema is known to be portable. |
| `auth.py`, `User`, `PersonalAccessToken` | | Multi-account separation | **Dropped.** One local user. |
| `calibration_data/` | 576 KB | IntCal20, Marine20, SHCal20 | Embedded in the binary (`include_bytes!`), hashes checked at build. |
| `backend/tests` | 60 files, OxCal 4.4 fixtures | Solver and OxCal-agreement tests | **The parity oracle** for the port (§4). This is the single most valuable asset for this project. |

Two facts that shape the plan:

- **The numerical dependencies are small.** Beyond NumPy arrays, the services use `gammaln`,
  `find_peaks` (prominence only), `fftconvolve`, the `chi` distribution, `CubicSpline`
  (calibration.py), `svd`/`pinv`, and `default_rng`. Every one has a direct Rust equivalent or is
  under a hundred lines to write. There is no PyMC, no sparse solver, nothing exotic.
- **`gibbs`'s sweep loop is pure Python and holds the GIL** (`services/jobs.py` docstring). That is
  why the job queue has one worker, and why sixty dates take 43 s. A compiled loop is where
  "fast" comes from; expect one to two orders of magnitude on the sampler, and real parallelism
  across chains and across a project's analyses.

---

## 2. Architecture

```
┌──────────────── RadiCal.app (Tauri 2) ─────────────────┐
│  System webview  ── frontend/dist (unchanged React) ── │
│        │ fetch('/api/...')  + launch token header       │
│        ▼                                                │
│  radical-server (axum, 127.0.0.1:<random port>)         │
│        │                                                │
│        ├── radical-store  (SQLite, rusqlite, migrations)│
│        ├── radical-jobs   (worker pool, cancel, progress)│
│        ├── radical-io     (CSV/.oxcal/.14c/project I/O) │
│        ├── radical-report (PDF: typst + plotters SVG)   │
│        └── radical-core   (calibration + all solvers)   │
└─────────────────────────────────────────────────────────┘
```

### 2.1 Why Tauri, and why keep the React frontend

- Tauri uses the OS webview, so the installer is ~10–20 MB rather than Electron's ~150 MB, and the
  shell is Rust, which is where the rest of the code is going.
- Rewriting 53k lines of working, critiqued UI in egui/Iced/Slint would cost more than the whole
  backend port and lose Plotly and React Flow. No native Rust UI toolkit has a plotting story
  close to Plotly's interactivity yet.

### 2.2 Why a local HTTP server rather than Tauri commands

The frontend makes 64 bare `fetch('/api/...')` calls across 13 files, funnelled through the
`window.fetch` wrapper in `frontend/src/api/client.ts`. Keeping HTTP means:

- **No frontend rewrite.** The webview loads `http://127.0.0.1:<port>/`, so `/api/...` is
  same-origin, exactly as in Docker today.
- **The same routes and JSON shapes**, so the frontend's existing tests and types stay valid, and
  a script can still talk to the API.
- The port is random per launch and bound to loopback only. Other local processes could still
  reach it, so the server requires a **per-launch random token**, handed to the webview at
  startup and attached by the existing fetch wrapper — the same seam the bearer token uses now.

Tauri commands (IPC) stay available for the few things HTTP cannot do well: native save/open
dialogs, "reveal in Finder", menus, and progress events (§5).

### 2.3 Cargo workspace

```
desktop/
  Cargo.toml                 # workspace
  crates/
    radical-core/            # pure maths, no I/O, no async — the port of services/*
    radical-io/              # oxcal_script, oxcal_export, date_import, curve_import, project_export/import, date_format
    radical-store/           # SQLite schema, migrations, repositories
    radical-jobs/            # worker pool, cancellation, progress, orphan reconciliation
    radical-report/          # PDF report
    radical-server/          # axum routers mirroring backend/app/api
    radical-py/              # PyO3 bindings to radical-core — TEMPORARY, for parity (§4)
  src-tauri/                 # the desktop shell, bundling, updater
```

`radical-core` has no dependency on SQLite, axum or Tauri. That keeps it testable on its own,
bindable from Python during the port, and reusable later (a CLI, or a WASM build for the web).

### 2.4 Crate choices

| Need | Python today | Rust |
|---|---|---|
| Arrays | NumPy | `ndarray` (or plain `Vec<f64>` in hot loops — most of these are 1-D grids) |
| `gammaln`, `chi` | SciPy | `statrs` |
| `fftconvolve` | SciPy | `realfft` |
| `CubicSpline` | SciPy | Hand-written, **not-a-knot** boundary to match SciPy's default |
| `find_peaks(prominence=)` | SciPy | Hand-written (~80 lines); port SciPy's prominence algorithm exactly |
| `svd`, `pinv` | NumPy | `faer` or `nalgebra` |
| RNG | `default_rng` (PCG64) | `rand_pcg::Pcg64` + `rand_distr` (see §4.3 on reproducibility) |
| Parallel chains / re-run all | not possible (GIL) | `rayon` |
| HTTP | FastAPI | `axum` + `tower-http` (static files, compression) |
| Validation / schemas | Pydantic | `serde` + `validator`; generate TS types with `ts-rs` to check against `frontend/src/types` |
| DB | SQLAlchemy + Alembic | `rusqlite` (bundled SQLite) + `refinery` migrations |
| CSV | `csv` | `csv` |
| Outbound HTTP (reservoir ingest only) | `requests` | `reqwest` (rustls) |
| PDF | ReportLab + Matplotlib | `typst` as a library for layout, `plotters` → SVG for figures |

---

## 3. Phases

Each phase ends with something that works and is tested. Rough sizes assume one developer
familiar with the Python code; the solver phases dominate.

### Phase 0 — Groundwork (≈1 week)

- Create `desktop/` workspace, CI on macOS/Windows/Linux running `cargo test` and `cargo clippy`.
- **Get `pytest` running in CI first** (it ran nowhere before this phase). The port leans on that
  suite, so it has to be green and fast enough to run per PR. Split the 16-minute suite into a
  fast tier (unit + fixtures) and a slow tier (long OxCal agreement runs, nightly).
- Freeze a **golden corpus**: run every fixture in `backend/tests` (the stratigraphic
  section and outlier fixtures, the OxCal resolution fixtures, the wiggle matches) through the
  current Python and save inputs + outputs as JSON in `desktop/corpus/`. These outputs, plus the existing
  OxCal 4.4 fixtures, are the targets.

### Phase 1 — Calibration core (≈2 weeks)

Port to `radical-core`: `curve_registry` (measuring, not hardcoding, curve facts — keep that
property), `calibration`, `curve_mix`, `reservoir` (ΔR maths), `statistics`, `date_format`,
`resolution`, `curve_view` decimation, `kde`.

Done when: single and group calibrations match the Python output to 1e-9 on every golden case,
and `test_calibration.py`, `test_curves.py`, `test_curve_mix.py`, `test_resolution.py`,
`test_kde.py` pass with the Rust implementation swapped in through `radical-py` (§4).

### Phase 2 — Model tree and exact solvers (≈3–4 weeks)

Port `model_tree`, `stratigraphy`, `exact_chain` (3.4k lines), `exact_parallel`, `intervals`,
`agreement`, `wiggle_match`.

These carry the recent OxCal-matching work (ordered-tie weights, `F14C` likelihood, outlier
scaling), so port them function-by-function with the Python docstrings carried across as Rust
doc comments. Each function lands with a parity test against the Python one on random and
golden inputs before the next is started.

Done when: `test_exact_chain.py`, `test_exact_parallel.py`, `test_exact_ordered.py`,
`test_exact_tails.py`, `test_intervals.py`, `test_agreement.py`, `test_wiggle_match.py`,
`test_model_tree.py`, `test_stratigraphy.py` pass against Rust.

### Phase 3 — Gibbs / MH sampler (≈3–4 weeks)

Port `gibbs` (3.4k lines), `influence`, `cross_check`. This is the biggest speed win and the
biggest correctness risk.

- Keep the sampler's structure (sweep order, shrink trigger from the fourth round, peak-based
  initialisation via `find_peaks`) identical; optimise only after parity.
- Run independent chains on `rayon`. Report the same convergence diagnostics.
- Cancellation: check an `AtomicBool` between rounds, as `JobHandle.check_cancelled` does now.

Done when: `test_gibbs.py`, `test_oxcal_agreement.py`, `test_match_oxcal.py`,
`test_charcoal_outlier.py`, `test_influence.py`, `test_cross_check.py` pass against Rust within
their existing tolerances, and a benchmark table (8 dates / 60 dates / sweep cap) replaces the
numbers in `docs/ASYNC_JOBS_PLAN.md`.

### Phase 4 — I/O and dispersal (≈2 weeks)

Port `oxcal_script` (1.9k lines — parse and fail-by-name on unsupported constructs, as now),
`oxcal_export`, `date_import`, `curve_import`, project export/import, `dispersal`,
`reservoir_registry`, `reservoir_ingest` (the one outbound call).

Done when: `test_imports.py`, `test_oxcal_export.py`, `test_project_import.py`,
`test_project_export.py`, `test_collection_csv.py`, `test_curve_import.py`, `test_dispersal*.py`,
`test_reservoir*.py` pass, and a round trip (Python export → Rust import → Rust export) is
byte-stable.

### Phase 5 — Store, jobs and server (≈3 weeks)

- `radical-store`: the 13 tables that remain without `users` and `personal_access_tokens`.
  Ownership columns become implicit. JSON columns stay JSON (SQLite `json1`). WAL mode, one
  writer connection, a small read pool. The database lives in the OS app-data folder
  (`~/Library/Application Support/RadiCal`, `%APPDATA%\RadiCal`, `~/.local/share/radical`).
- `radical-jobs`: same seam as `services/jobs.py` — `submit(kind, target_id, work)`, progress
  writes throttled to 0.5 s, cancel set, `reconcile_orphans` on startup. Workers = cores − 1
  instead of one, now that there is no GIL.
- `radical-server`: one axum router per FastAPI router, same paths, same status codes (keep the
  404-not-403 rule meaningless but harmless), same JSON. Generate an OpenAPI document with
  `utoipa` and diff it against the FastAPI one in CI — that diff is the contract test.

Done when: the existing frontend, built unchanged, works end to end against the Rust server in
a plain browser, and the OpenAPI diff is empty apart from the removed auth routes.

### Phase 6 — PDF report (≈1–2 weeks)

Rebuild `pdf_report` (1.4k lines) with `plotters` drawing each posterior figure to SVG and
`typst` laying out the report. Render it on the job pool, which also closes
`REMAINING_WORK.md` §9. Check against today's PDFs by eye and by extracted text.

### Phase 7 — Desktop shell (≈2 weeks)

- Tauri 2 app that starts `radical-server` in-process on a random loopback port and opens the
  window on it.
- Frontend edits (behind a `desktop` build flag so the Docker build is unaffected):
  - Remove `SignIn` and `Account` and the 401 handler; the fetch wrapper sends the launch token.
  - Downloads (`utils/saveDownload.ts`, `utils/compareExport.ts`, `DispersalPanel`) go through a
    native save dialog via a Tauri command, because blob-URL downloads are unreliable in
    webviews.
  - Optional: replace 1 s job polling with Tauri events. Polling works fine on loopback, so this
    is polish.
- Native menu (File ▸ New Analysis / Import .oxcal / Open Project Export), "Open with RadiCal"
  file association for `.oxcal`, single-instance lock, window state memory.
- **Migration from the Docker version**: File ▸ Import Project Export reads the JSON that
  `api/project_export.py` already produces. No Postgres reader needed.

### Phase 8 — Packaging and release (≈1 week, plus signing paperwork)

- GitHub Actions matrix builds: macOS universal `.dmg`, Windows `.msi`, Linux `.AppImage` + `.deb`.
- **Signing is what makes it "easy install"**: Apple Developer ID + notarisation (otherwise
  Gatekeeper blocks it), Windows code signing (Azure Trusted Signing or an OV certificate,
  otherwise SmartScreen warns). Start the paperwork in Phase 0; it has lead time.
- `tauri-plugin-updater` against GitHub Releases, signed update manifests.
- Update `docs/INSTALLATION.md` to a desktop section: download, open, done.

**Total: roughly 18–24 weeks of focused work**, about two thirds of it in Phases 1–4.

---

## 4. Keeping the numbers right

The port is only worth doing if a date calibrated in RadiCal Desktop is the same date as in
RadiCal today. Three mechanisms:

### 4.1 Swap-in testing through PyO3

`radical-py` exposes each ported Rust function under the same name and signature as the Python
one. A pytest flag (`--impl=rust`) monkeypatches `app.services.<module>.<fn>` to the Rust
version, and the **existing suite runs unchanged** against it. Every ported module therefore
inherits years of tests and the OxCal 4.4 fixtures for free, and the Python app can even ship
Rust-accelerated solvers before the desktop app exists — a useful intermediate release.

`radical-py` is deleted once Phase 5 is done and the tests have been carried across to Rust.

### 4.2 Golden corpus

The Phase 0 corpus runs in `cargo test`, so parity does not depend on a Python install once the
port is done.

### 4.3 Randomness

`np.random.default_rng` is PCG64 seeded through NumPy's `SeedSequence`. Phase 1 ports both
(`radical_core::rng`), with `random`, `uniform` and `choice`, and the corpus holds them to
NumPy's state and draws bit for bit. So wherever a service draws only those, **a Rust run
reproduces a Python run with the same seed**: the KDE summary does, to the rounding of its
kernel sums. Consequences and decisions:

- NumPy's `normal`, `exponential` and `integers` draws use their own algorithms (ziggurat tables,
  Lemire's bounded integers). Port each when a solver that uses it is ported, against corpus
  cases of its own, rather than substituting another algorithm.
- Until then, sampler parity is judged statistically (the existing tolerances), not bit for bit.
- The exact solvers are deterministic apart from the seeded posterior draws in `exact_chain`
  (`seed`, `seed + 1`, `seed + 2`); compare those bit for bit where their draws are ported, and
  by distribution where not.
- Within the desktop app, a seed must reproduce a run exactly. Record the engine version on
  each `AnalysisRun`, so run history says which implementation produced it.

---

## 5. What changes for the user

| Today (Docker) | Desktop |
|---|---|
| Install Docker, fetch compose file, generate a secret, `docker compose up` | Download, open |
| Create an account and sign in | Opens straight to Dashboard |
| One solver thread | All cores; re-run all in parallel |
| Data in a Postgres volume | One SQLite file in app data; File ▸ Back Up copies it |
| Browser tab at localhost:8000 | A real window, menus, file associations |
| Reservoir ingest from the Curves page | Unchanged (needs network) |

Kept deliberately: projects, collections, imports/exports, curve import and mixing, run history,
model templates, PDF reports — everything in the README's feature list.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| Subtle solver divergence from the OxCal-matched Python | §4.1 swap-in tests per function; no optimisation before parity |
| `oxcal_script` parser drift | Port its test corpus first; fail-by-name behaviour is part of the contract |
| Webview differences (Safari WebKit on macOS, WebView2 on Windows, WebKitGTK on Linux) — Plotly and React Flow are the exposure | Smoke-test the Results, Stratigraphy and Curve Browser pages on all three in CI with `tauri-driver`; this also starts `REMAINING_WORK.md` §10 |
| PDF looks different | Accept "different but equivalent"; review side by side before release |
| Two codebases during the port | The Docker app stays the release until Phase 7; `radical-py` lets it benefit from each ported module along the way |
| Signing certificates take weeks | Start in Phase 0 |

---

## 7. Decisions to make before starting

1. **Does the web/Docker version continue?** If yes, the Rust server can replace FastAPI there too
   (same routes), and auth comes back as an optional layer. If no, delete `auth` outright.
2. **Keep a scriptable local API?** Free with the HTTP design; it just needs a stable port option
   and a token the user can copy.
3. **Licence check**: RadiCal is GPL-3.0 (`LICENSE`); confirm every crate in §2.4 is compatible
   (they are MIT/Apache-2.0 today; `typst` is Apache-2.0).
4. **Minimum OS versions**: macOS 11+, Windows 10+ (WebView2), a recent WebKitGTK on Linux.
