# Remaining Work: RadiCal

Open work only. Nothing here is high priority, and there are no known bugs.
Every item was checked against the code; verify again before starting one.

---

## Modelling

### 1. Phase-duration priors and custom per-date priors
Durations are reported from the joint posterior (`services/intervals.py`), but
nothing lets a user state one up front, such as "this phase lasted at most a
century", before the model sees the dates. The uniform span prior is the only
duration prior. Custom per-date priors are missing too. A user-specified
duration prior pays off exactly where the reported duration comes back wider
than the archaeology allows.

### 2. Wiggle matching inside a larger model
A wiggle sequence nested in a bigger structure is refused by name, at the
importer and at the run (`model_tree.py`, `api/analysis.py`). Its members are
one calendar age plus fixed gaps, not n free parameters, so the surrounding
Boundaries and span terms have to be told that. Reading the series as n dates
in a phase would report a match that was never made. The data model already
carries the reference member (`ELEMENT_PARAMS['wiggle_sequence']`), so this is
solver work, not a migration.

### 3. Outlier model on a wiggle-match member
Refused at the request schema, the importer and `wiggle_match.solve`. OxCal
does allow `Outlier()` inside a `D_Sequence`. Implementing it means
marginalising an offset scale per member, which is `exact_chain`'s
approximation, not OxCal's shared scale. Get a real OxCal fixture first.

### 4. Sapwood allowance on a wiggle match
A wiggle match dates its reference member, not the felling of the tree. A
sapwood allowance, an estimated number of missing outer rings, is not modelled,
so the user has to account for it by hand. Modelling it is one more gap with an
error, which the `V_Sequence` machinery already covers, and the reference
member is already explicit in the data model, so it needs no migration. It
matters most once a wiggle match can sit inside a Phase (item 2).

---

## Data

### 5. Provenance link from an analysis date to its collection entry
Deploying copies a date's values into the analysis on purpose, as a snapshot,
but nothing records which `ProjectSample` a `RadiocarbonSample` came from. So
nothing can show "this analysis uses an outdated version of this date". A
nullable `source_project_sample_id` (one store migration) would enable that
without weakening the snapshot.

### 6. Bundled ΔR snapshot
- **Permission (a person has to do this):** email p.j.reimer@qub.ac.uk asking
  to redistribute the Marine20 reservoir compilation as a bundled snapshot,
  citing Reimer & Reimer 2001 and naming the app.
- **The snapshot (blocked on permission):** commit
  `calibration_data/marine_reservoir_<date>.csv`, produced by the ingest's
  parser, with its hash in the registry. If permission is refused, drop this;
  the ingest stays the only source.

Until then, a fresh install has no ΔR lookup until someone runs the ingest from
the card on the Curves page.

### 7. Per-species habit correction
The reservoir bias is measured per feeding class only. A per-species correction
needs a published source, and the reservoir database doesn't contain one.

Two ΔR lookup methods are also unbuilt, and deferred until there is a concrete
need:
- **Regional kriging**, as a second method beside the nearest-N weighted mean.
  It must be fitted inside a bounding box the user chooses, show the fitted
  variogram (nugget included) before a value is accepted, refuse to
  extrapolate outside the fitted points' hull or range, and report measurement
  plus interpolation error.
- **Over-water distance.** Every distance is great-circle, so a lookup can
  draw on points across a peninsula. Kriging would need this, or a stated
  caveat, to be honest.

---

## Deployment and performance

### 8. Solver workers shared by every account, lost on restart
`radical-jobs` runs one in-process queue with the machine's cores less one as
workers, and registration on a server is open. Any account can fill every
worker, there is no per-account limit, and a restart fails whatever was
mid-flight (`reconcile_orphans`). This is fine behind a VPN or institutional
proxy. Revisit it before a server is reachable publicly: a per-account cap on
queued work, and a queue that survives a restart.

### 9. Unsigned desktop installers
RadiCal Desktop's installers are not code-signed, so macOS and Windows warn
before the first open (`docs/INSTALLATION.md` tells users how to get past it).
- **macOS:** an Apple Developer ID certificate and notarisation. The release
  workflow already uses them once the `APPLE_*` secrets are set.
- **Windows:** Azure Trusted Signing or an OV certificate, which the release
  workflow does not handle yet.

Both take weeks of paperwork before any code changes. Once they're done, drop
the warning section from `docs/INSTALLATION.md`.

### 10. Exact solver's matrix products
The exact solver is the one place the Rust server is not faster than the
Python one was: NumPy handed its large matrix products to Apple Accelerate or
OpenBLAS. Calling a BLAS from `radical-core`'s `matmul` would close the gap.

---

## Testing

### 11. No automated end-to-end tests
Flows are verified by hand against the running stack. The obvious first
scenarios, for example Playwright against `docker-compose.yml`: sign in → new
analysis → run → results; deploy from a collection; compare two analyses.

RadiCal Desktop runs the same frontend in three different webviews (WebKit on
macOS, WebView2 on Windows, WebKitGTK on Linux), with Plotly and React Flow the
most exposed. Smoke tests of the Results, Stratigraphy and Curve Browser pages
on all three, in CI with `tauri-driver`, would catch a webview difference
before users do.

### 12. Frontend unit gaps
- `utils/stratigraphy.ts` (`flattenStructure`, `expandRelationships`,
  `findCycle`) is pure and untested.
- `hooks/useJob.ts` and `api/jobs.ts` have no tests of their own; only the
  server side of jobs is tested, by `radical-server`'s tests.
- `ImportDatesDialog` needs a component test. That means adding jsdom, since
  Vitest currently runs in `node` (`vite.config.ts`).
