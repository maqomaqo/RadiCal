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

### 4. Wiggle-match decisions left open
From [`docs/AGE_DEPTH_AND_WIGGLE_MATCH_PLAN.md`](docs/AGE_DEPTH_AND_WIGGLE_MATCH_PLAN.md):
- **§12.3:** a sapwood allowance is not modelled.

---

## Data

### 5. Provenance link from an analysis date to its collection entry
Deploying copies a date's values into the analysis on purpose, as a snapshot,
but nothing records which `ProjectSample` a `RadiocarbonSample` came from. So
nothing can show "this analysis uses an outdated version of this date". A
nullable `source_project_sample_id` (one Alembic migration) would enable that
without weakening the snapshot.

### 6. Bundled ΔR snapshot
From [`docs/SPATIAL_METADATA_AND_DISPERSAL_PLAN.md`](docs/SPATIAL_METADATA_AND_DISPERSAL_PLAN.md) §14:
- **Op 0 (a person has to do this):** email p.j.reimer@qub.ac.uk asking to
  redistribute the Marine20 reservoir compilation as a bundled snapshot, citing
  Reimer & Reimer 2001 and naming the app.
- **Op 3d (blocked on op 0):** commit
  `calibration_data/marine_reservoir_<date>.csv`, produced by the ingest's
  parser, with its hash in the registry. If permission is refused, drop 3d; the
  ingest stays the only source.

Until then, a fresh install has no ΔR lookup until someone runs the ingest from
the card on the Curves page.

### 7. Per-species habit correction (plan §13.6)
The reservoir bias is measured per feeding class only. A per-species correction
needs a published source, and the reservoir database doesn't contain one.

Regional kriging (plan §5.7) and over-water distance (§9) are also unbuilt.
They are deferred until there is a concrete need.

---

## Deployment and performance

### 8. One solver thread, shared by every account
`services/jobs.py` runs an in-process `ThreadPoolExecutor(max_workers=1)`, and
registration is open. Any account can occupy the only solver thread, and a
restart fails whatever was mid-flight (`jobs.reconcile_orphans`). This is fine
behind a VPN or institutional proxy. Revisit it before the app is reachable
publicly: add workers, or put a broker such as RQ or Celery behind the same
`submit` seam. Resize the Postgres pool in `database.py` (currently 10 + 20
overflow) alongside it.

### 9. PDF rendering runs inside the request
`GET /results/{id}/pdf` draws one matplotlib figure per modelled parameter,
synchronously. That is about four seconds for a 45-parameter model. Move it
onto the job queue if models grow.

---

## Testing

### 10. CI runs no tests
`.github/workflows/publish-image.yml` builds and publishes the image and
nothing else. Neither `pytest` nor `vitest` runs on a push or a PR. The backend
suite takes about 16 minutes in the dev container.

### 11. No automated end-to-end tests
Flows are verified by hand against the running stack. The obvious first
scenarios, for example Playwright against `docker-compose.yml`: sign in → new
analysis → run → results; deploy from a collection; compare two analyses.

### 12. Frontend unit gaps
- `utils/stratigraphy.ts` (`flattenStructure`, `expandRelationships`,
  `findCycle`) is pure and untested.
- `hooks/useJob.ts` and `api/jobs.ts` are covered only end to end, through the
  backend suite.
- `ImportDatesDialog` needs a component test. That means adding jsdom, since
  Vitest currently runs in `node` (`vite.config.ts`).
