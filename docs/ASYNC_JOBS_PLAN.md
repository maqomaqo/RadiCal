# Async analysis runs — scoping

Written 2026-08-19. Scopes `REMAINING_WORK.md` item 3, "Async MCMC processing": today every
solver run, rerun and PDF render happens inside the request handler that asked for it.

Everything measured below was measured, not estimated — in the running `radiocarbon_backend`
container on this machine. The measurements changed the shape of the answer, so they come first.

---

## 1. What is actually wrong

### 1.1 It is not one problem, it is three

The item as filed ("a task queue plus a job-status endpoint the UI can poll") answers one of
them. Kept apart, they have very different costs and can land in any order:

| | Problem | Who suffers | Fix |
|---|---|---|---|
| **A** | CPU work runs on the event loop, so one run freezes the *whole server* | every other user and every other tab | drop `async` on the CPU-bound routes — ~30 min, no new infrastructure |
| **B** | The caller waits on an open HTTP connection with no progress and no way out | the person who pressed Run | job record + polling |
| **C** | A run does not survive a restart, and two runs cannot proceed at once | nobody yet, at one user | a real broker (Redis + Celery/RQ) |

**A is the severe one and it is not what the item describes.** It is also the cheapest to fix.

### 1.2 A: every request handler blocks the event loop

All 50 route handlers across `backend/app/api/*.py` are declared `async def`, and none of them
offloads anything — there is no `run_in_threadpool`, no `asyncio.to_thread`, no executor
anywhere in `backend/app/`. FastAPI runs an `async def` handler *on the event loop itself*; only
a plain `def` handler is pushed to a worker thread. So the numpy/Python solver loops in
`services/gibbs.py`, the exact integration in `services/exact_chain.py` and the matplotlib
figures in `services/pdf_report.py` all execute with the loop held.

Measured against the running container, using the PDF route (the shortest of the three blocking
paths, so this is the *mild* case):

```
GET /api/curves, warm, server idle                     0.0023 s
GET /api/curves, warm, issued during a PDF render      3.85   s     <-- queued behind it
GET /api/results/{id}/pdf  ("Nenthorn > Shag (all areas)")   4.88 s
```

A request that costs 2.3 ms took 3.85 seconds because something else was computing. Nothing is
served in the meantime — not the health check, not a curve list, not another user's page load.
During a Bayesian run the same freeze lasts for as long as §1.3 says the run lasts.

### 1.3 B: how long a run actually takes

Wall-clock for `gibbs.run()`, each model run to its own convergence target:

| Model | Params | Sweeps | Wall clock | ms/sweep |
|---|---|---|---|---|
| `oxcal_fixture` ordered — 8 dates, no outlier model | 10 | 66,000 | **12.7 s** | 0.19 |
| `nenthorn_charcoal_fixture` — 11 dates, 2 outlier models | 13 | 38,000 | **28.7 s** | 0.76 |
| `shag_fixture` — 16 dates, 1 phase, outlier model | 18 | 28,000 | **28.6 s** | 1.02 |
| synthetic — 30 dates / 6 phases | 37 | 30,000 | **18.4 s** | 0.61 |
| synthetic — 60 dates / 12 phases | 73 | 30,000 | **43.4 s** | 1.45 |

Two things follow.

**The typical case is tens of seconds, not milliseconds and not hours.** That is the awkward
band: too long to hold a request open behind any reverse proxy (nginx's default
`proxy_read_timeout` is 60 s, and the app ships no proxy config of its own), long enough that a
user assumes the page has hung, short enough that WebSockets and a streaming progress channel
would be over-engineering. Polling at 1 s is the right instrument.

**The ceiling is set by `MAX_SWEEPS = 200,000`, not by the table above.** The sweep counts vary
by a factor of two between models of similar size, because — as `gibbs.run`'s own docstring
argues — run length follows from how tightly the structure couples parameters, not from how big
it is. A model that fights its convergence target runs to the cap. At the measured per-sweep
costs that bounds a run at roughly **38 s (8 dates), 2 minutes (30 dates), 5 minutes (60
dates)** — every second of it with the event loop held.

**"Re-run All" multiplies it.** `POST /api/projects/{id}/rerun` loops `_rerun_analysis` over
every analysis in the project in one request. The eleven saved Bayesian analyses currently in
this database would, at the measured rates, take somewhere between three and twenty minutes as
a single HTTP call. The unassigned pseudo-project takes a different route —
`ProjectDetail.tsx:262` fans out client-side into N parallel `POST .../rerun` calls — which
under a single-threaded event loop serialises anyway, just with N connections open instead of
one.

### 1.4 C: durability, and how much it is worth here

`uvicorn ... --reload` in `docker-compose.yml` runs one process, one worker. A restart — and
`--reload` restarts on every source edit — loses any run in flight and leaves `Analysis.status`
reading `'running'` forever, since nothing reconciles it at boot.

That is real, but it is worth weighing against the app's stated posture: no authentication
exists (`REMAINING_WORK.md` item 4), and the docs describe single-user local use. A Redis
container, a Celery worker image, a second Dockerfile target and the deployment documentation
that goes with them is substantial operational weight to add for one user's crash-safety. §3
prices both.

---

## 2. What already fits, and what does not

Working in favour:

- **The status vocabulary exists.** `Analysis.status` is already
  `pending`/`running`/`completed`/`failed`, with `error_message` beside it, and every write path
  already maintains it. `Results.tsx:1086` already renders anything that is neither `completed`
  nor `failed` as an amber chip, so a run in progress has a visible state today without a line
  of new frontend code.
- **The worker seam exists.** `_rerun_analysis(analysis, db)` (`api/analysis.py:1254`) already
  takes a `Session` as an argument rather than reaching for the request's one, raises on
  failure, and commits only on success. It is already callable from a thread that opened its
  own `SessionLocal`.
- **The frontend throws the inline results away.** `POST /api/analysis/bayesian` returns the
  full `results`/`contexts` payload, but `NewAnalysis.tsx:792` reads exactly one field —
  `data.data.id` — and navigates to `/results/{id}`, which refetches from
  `GET /api/analysis/{id}`. **Returning a job id instead of results costs the frontend nothing.**
  This is the single fact that makes the change small.
- **No unsaved Bayesian runs.** `save=false` exists on all three request schemas, but the
  frontend only ever sends it on the single-calibration path (`NewAnalysis.tsx:687`). Nothing
  has to be designed for "run a Bayesian model but persist nothing" — there is nowhere to hang
  a job record for such a run, and no caller needs one.

Working against:

- **`gibbs`'s sweep loop is pure Python**, so it holds the GIL. Moving it to a worker *thread*
  restores responsiveness but does not buy parallelism: two concurrent runs will each take
  roughly twice as long. Only a separate *process* fixes that, and §4.2 chooses one worker
  rather than pretending otherwise. (On responsiveness this proved pessimistic — see the
  measurements under Option 1, where a request during a live Gibbs run was indistinguishable
  from an idle one, because the numpy calls in each sweep release the GIL.)
- **No connection pool sizing.** `create_engine(DATABASE_URL, echo=False)` with no `pool_size`
  (`REMAINING_WORK.md` item 7, filed as a 15-minute job "only bites under concurrent load").
  A background worker *is* concurrent load — it holds a second connection for the length of a
  run. Item 7 stops being independent of this work and should land with it.
- **`test_rerun.py` assumes synchronicity.** `TestRerunAnalysis` and `TestRerunProject` post and
  immediately assert on results. Whatever shape the change takes has to keep a synchronous path
  for them, or teach them to wait.

---

## 3. Three options, priced

### Option 1 — offload only. No queue, no job records. **Done 2026-08-19.**

Every handler became a plain `def`, so FastAPI runs it in a worker thread instead of on the
loop — not only the eight CPU-bound routes this section originally proposed. The wider scope
was the right one: the whole stack is synchronous, SQLAlchemy included, so `GET /api/curves`
was on the loop too and cost 238 ms cold. Fixing eight routes would have left the same class of
defect in the other forty-two.

Three handlers keep `async def`, because `await file.read()` on an upload is genuinely
awaitable: `preview_curve`, `create_curve` and `import_dates`. Each now awaits only the read and
hands its body — parsing a whole `.14c` file, or an `.oxcal` model — to `run_in_threadpool`.
`app/main.py`'s `health_check` and `root` stay `async def` as well: they touch nothing, so they
are correct on the loop and cheaper there than a thread hop.

Measured after, against the same analysis and the same route as §1.2:

```
GET /api/curves, warm, server idle                     0.0026 s
GET /api/curves, during the same 4.6 s PDF render      0.0029 s     (was 3.85 s)
GET /api/health, during the same render                0.0014 s
GET /api/curves, during a 6.5 s Gibbs run              0.0026 - 0.0081 s
```

The freeze is gone; a concurrent request is now indistinguishable from an idle one.

**One thing measured better than §2 predicted.** That section warned that `gibbs`'s pure-Python
sweep loop holds the GIL, so other requests would still be *measurably* slower during a run.
They are not — 2.6 to 8.1 ms during a live Gibbs run against 2.5 to 3.1 ms idle. The numpy calls
inside each sweep release the GIL, and the interpreter's switch interval covers the rest. The
caveat stands in principle and would show up under real concurrent load; it does not show up at
one client polling once a second, which is what the job polling in §4.6 will be.

- **Fixes:** A entirely, for everyone but the caller.
- **Does not fix:** B or C. The caller still waits, still has no progress bar, still trips a
  60-second proxy timeout on a long "Re-run All".
- **Actual cost:** about 40 minutes including `tests/test_event_loop.py`.
- **Guarded by:** `tests/test_event_loop.py`, which asserts the invariant structurally — every
  handler is either declared sync or on a named list saying why not, and each exemption must
  still show `await file.read()` and `run_in_threadpool` in its source. A new blocking
  `async def` route fails the suite.

### Option 2 — in-process job table and a polling endpoint. **Built 2026-08-19.**

A `jobs` table, a single-worker `ThreadPoolExecutor`, and a status endpoint. No new services, no
new runtime dependency, nothing added to `docker-compose.yml`.

- **Fixes:** A and B. Also bounds C, by reconciling orphaned jobs at startup instead of
  surviving the restart.
- **Cost:** 4–6 hours including tests and the frontend polling (§4 breaks it down).
- **Risk:** the executor is a process-local singleton, so it is correct only while the backend
  runs as one process. That is exactly how it is deployed, and the startup reconciliation in
  §4.5 is what makes the assumption visible rather than silent.

Built as specified, with two departures worth naming:

- **The PDF route did not join the queue**, per the recommendation in §7. At 4.9 s it is under
  the threshold where a job record earns its keep, and the event-loop fix already stopped it
  freezing the server.
- **`POST /analysis/bayesian` splits rather than defers.** Validation and input persistence stay
  in the request — so a malformed model still answers 422 immediately, as it always did — and
  only the solve-and-store half is queued. That half became a closure over the request and over
  `groups_map`/`samples_map`, which is available precisely *because* the queue is in-process:
  there is no serialization boundary to marshal them across. It does not close over the
  `analysis` row, which belongs to the request's session; the worker re-fetches it by id.

Measured after, on the same 12-date model used throughout:

```
POST /api/analysis/bayesian    0.026 s   (was 6.5 s, the length of the run)
job runs, reporting progress   7.1 s     burn-in, then convergence 0.867 -> 0.951
cancel during a run            0.3 s     to stop, at the next round boundary
```

The progress trace is worth reading for one detail: that run finished at **15% of the sweep
cap**, having converged. A bar that reaches 0.15 and then says "done" reads as a bug, which is
why the convergence figure is shown beside it and never on its own.

### Option 3 — Redis + Celery/RQ, as the item originally proposed.

- **Fixes:** A, B and C, plus true parallel runs and multi-worker deployment.
- **Cost:** 8–12 hours realistically — the queue itself is the small part. A `redis` service and
  a `worker` service in `docker-compose.yml`, a worker entrypoint that imports the app's models
  without its routes, `docs/INSTALLATION.md` rewritten for a four-container stack, a broker to
  keep alive in CI, and test fixtures that either run the worker eagerly or spin a broker.
- **Buys, today:** durability across restarts for a single local user.

**Recommendation: Option 1 now, Option 2 as the answer to the item, shaped so that Option 3 is a
swap of the executor rather than a rewrite.** The `REMAINING_WORK.md` estimate of 3–4 hours is
about right for Option 2 and clearly wrong for the Celery/RQ design it names alongside it.

---

## 4. Option 2 in detail

### 4.1 Data

One new table, not a set of columns on `Analysis` — because a project rerun spans many analyses
and a PDF render belongs to none, so there is no single `Analysis` row to hang either on.

```
Job
  id            uuid
  kind          'analysis_run' | 'project_rerun' | 'pdf_report'
  target_id     analysis id or project id (nullable, indexed)
  state         'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress      float 0..1, nullable      -- fraction of MAX_SWEEPS reached
  detail        JSON, nullable            -- {'sweeps': n, 'convergence': x,
                                          --  'completed': i, 'total': n} per kind
  error_message text, nullable
  created_at / started_at / finished_at
```

`Analysis.status` keeps its present meaning and stays authoritative for "does this analysis have
results" — the job row is about the *run*, not the analysis. New tables are picked up by
`create_all()` (`database.py`), so no migration stopgap entry is needed; that is how
`project_samples` arrived.

### 4.2 Executor

A module-level `ThreadPoolExecutor(max_workers=1)` in a new `app/services/jobs.py`.

`max_workers=1` deliberately: §2 established that the sweep loop is GIL-bound, so two concurrent
runs would each take twice as long and the server would be less responsive during both. One
worker gives a genuine queue, a predictable "N ahead of you", and the option of raising it later
if the solver ever releases the GIL.

Each task opens its own `SessionLocal()` and closes it in a `finally` — it must not borrow the
request's session, which `get_db` closes when the response is sent.

### 4.3 Endpoints

- `POST /api/analysis/bayesian`, `.../rerun`, `/projects/{id}/rerun` return **202** with
  `{"job_id": ..., "id": <analysis id>, "status": "queued"}`.
- `GET /api/jobs/{job_id}` → the row above.
- `DELETE /api/jobs/{job_id}` → cooperative cancel (§4.4).
- **`?wait=true` keeps the old synchronous behaviour**, response shape unchanged. This is not
  only for `test_rerun.py`: an external API consumer scripting a batch has no event loop to
  poll from, and the app has no auth or client library that would let it be told otherwise.

### 4.4 Progress and cancellation — the one change inside the solver

`gibbs.run()` already accumulates in rounds and already knows its sweep count and its achieved
convergence at each round boundary. Add one optional callback, invoked there:

```python
def run(model, ..., on_progress: Optional[Callable[[int, float], bool]] = None)
```

Returning `False` asks the run to stop, which gives cancellation for free at the same seam —
and cancelling between rounds is clean, because a partial run is simply discarded and the
analysis returns to its previous state. Progress is `sweeps / max_sweeps` alongside the achieved
convergence, which is honest about being an upper bound: a run that converges early finishes at
30% and that is a success, not a truncation. The UI should show the convergence figure next to
the bar for that reason.

`exact_chain` gets no callback: it does not iterate, and it is fast.

### 4.5 Startup reconciliation

On boot, any `Job` in `queued`/`running` and any `Analysis` in `running` becomes `failed` with
"interrupted by a server restart". Without it a `--reload` during development leaves rows stuck
in `running` and the UI polling forever. This is what makes C tolerable rather than fixed.

### 4.6 Frontend

- `NewAnalysis` navigates to `/results/{id}` on the 202 rather than on completion — it already
  discards the inline results (§2), so this is a smaller diff than it sounds.
- `Results` polls `GET /api/jobs/{job_id}` at 1 s while the analysis is not `completed`, and
  shows a determinate progress bar with the convergence readout and a Cancel button. The amber
  status chip already renders.
- `ProjectDetail`'s "Re-run All" becomes one job with `{completed, total}`; the client-side
  fan-out for the unassigned pseudo-project can now become one job too, since a job needs no
  project id.
- Polling stops on `completed`/`failed`/`cancelled`, on unmount, and after a ceiling
  (`MAX_SWEEPS` at the slowest measured rate ≈ 5 min, so 10 min) so a dead backend does not
  leave a tab polling forever.

### 4.7 Tests

| | |
|---|---|
| existing | `test_rerun.py` — `TestRerunAnalysis`, `TestRerunProject` move to `?wait=true`, or gain a `wait_for_job` helper. Nothing else in the suite posts an analysis and asserts on results in the same call. |
| new | job reaches `completed` and the analysis has results |
| | a failing run marks the **job** failed *and* the analysis failed, with the message on both |
| | two submissions queue rather than interleave; the second reports a position |
| | `on_progress` is called with non-decreasing sweeps, and returning `False` stops the run |
| | a cancelled run leaves the analysis's previous results intact |
| | startup reconciliation fails a row left `running` |
| | **a second request is served while a run is in flight** — the regression test for §1.2, and the only one that would have caught the current defect |

---

## 5. Deliberately not in scope

- **Authentication** (item 4). A job endpoint with no auth is exactly as exposed as every other
  endpoint here; this changes nothing either way.
- **Alembic** (item 5). `create_all()` covers a new table.
- **True parallelism / multi-process.** §4.2 explains why one worker is right at this size.
- **WebSockets or SSE.** At tens of seconds, 1 s polling is simpler and adequate.
- **Making the solver faster.** A separate question, and the wrong one to answer first: a
  30-second run that reports progress is fine, a 3-second run that freezes the server is not.
- **Connection pooling** (item 7) is *not* out of scope — see §2. It should land with this.

---

## 6. Effort

| | |
|---|---|
| ~~Option 1 — `async def` → `def`, plus `tests/test_event_loop.py`~~ | **done 2026-08-19** |
| ~~Option 2 — everything in §4, plus `tests/test_jobs.py`~~ | **done 2026-08-19** |
| `Job` model + `services/jobs.py` executor + startup reconciliation | 1.5 h |
| Endpoints: 202 responses, `GET`/`DELETE /api/jobs/{id}`, `?wait=true` | 1.0 h |
| `gibbs.run` progress/cancel callback | 0.5 h |
| Frontend polling, progress bar, cancel, Re-run All as one job | 1.5 h |
| Tests (§4.7) | 1.0 h |
| Connection pool sizing (item 7, now coupled) | 0.25 h |
| | **all of it landed 2026-08-19** |

---

## 7. Decisions taken

All three were settled as recommended when this was built.


1. **Redis or not** — Option 2 or Option 3. Turns on whether this app is ever deployed for more
   than one user. Recommendation: Option 2, and revisit with authentication (item 4), since
   multi-user is the thing that makes both worth their cost.
2. **Does the PDF route join the queue?** At 4.9 s it is under the threshold where a job record
   earns its keep, and Option 1 already stops it freezing the server. Recommendation: leave it
   synchronous, revisit if models grow — which is what `REMAINING_WORK.md` §1 already says about
   it.
3. **Does `?wait=true` stay permanently, or only until the tests are migrated?** Recommendation:
   permanently, and documented — there is no client library, so scripted consumers have no other
   sane path.
