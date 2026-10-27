# RadiCal on a server, in Rust

The Docker version moves from the FastAPI backend to `radical-server`, the Rust server RadiCal
Desktop already runs ([`desktop/`](../desktop/README.md)). Accounts come back, for the
server only: RadiCal Desktop stays one person on one computer, with no sign-in.

## Why

Measured on the same requests (Apple M4 Pro, both native, October 2026):

| Task | FastAPI | Rust |
|---|---|---|
| Sampled model with outliers, 24 dates | 84.9 s | 9.0 s |
| Four sampled models queued together | 339 s | 16.2 s |
| PDF report, 60 dates | 5.6 s | 0.09 s |
| Exact model, 60 dates | 2.6 s | 2.8 s |

The larger reason is that there would be one backend, not two. Today every change below the
frontend has to be made in Python and again in Rust, and kept matching.

## What stays the same

- **The frontend.** It already runs against both, and already has the sign-in, account and
  token pages a server needs.
- **The API.** Same paths, status codes and JSON, including the auth routes. Sign-in tokens
  are HS256 JWTs signed with `RADICAL_SECRET_KEY`, as now, and password hashes are bcrypt.
  So after the move, existing passwords, open sessions and personal access tokens keep
  working.
- **What a person sees.** Their own projects, analyses, templates, curves and jobs, and a 404
  for anyone else's (the existing 404-not-403 rule).
- **Configuration.** `RADICAL_SECRET_KEY`, `RADICAL_PUBLIC_URL` and the `RADICAL_SMTP_*`
  settings mean what they mean now.

## Design

**One program, two modes.** `radical-server` runs either:

- **single-user** (the default, and what RadiCal Desktop uses): no accounts, as today; or
- **accounts** (`--accounts`, which the Docker image sets).

A request reaches each route as a `Caller`:

- in single-user mode, `Caller::Local`;
- in accounts mode, `Caller::User(id)`, from a sign-in token or a personal access token,
  checked once in middleware.

Routes ask the `Caller` which rows they may see, rather than each checking a token. A missing
or bad token is a 401, as FastAPI answers.

**Ownership in the store.**
- **New tables:** `users` and `personal_access_tokens`.
- **New columns:** `owner_id` on projects, analyses, model templates and jobs; `created_by` on
  stored curves and reservoir snapshots. All are nullable.
- **Everything else** (dates, results, model elements, runs) belongs to its analysis or
  project, as in Python.
- **Desktop:** leaves the columns empty and filters on nothing.

The store migration is the same file in both modes, so a desktop database and a server database
never differ in shape.

**Accounts, ported from `api/auth.py` and `services/auth.py`:**
- **Routes:** register, sign in, `/me`, change password, `/options`, forgot and reset
  password, and list, create and revoke personal access tokens.
- **First account:** the first account adopts the work that existed before there were
  accounts, as now.
- **Email:** password-reset email over SMTP.
- **Command line:** `radical-server reset-password EMAIL` replaces
  `python -m app.reset_password`.

**The contract test grows.** The replay currently skips every recorded exchange made as a
second account, or with no account. In accounts mode it replays all of them:
- `test_auth.py`;
- the "someone else's analysis is 404" tests in every module;
- the anonymous 401s.

So the backend suite, which already pins ownership on every route, becomes the test of the Rust
server's ownership too.

## Phases

### S1: Accounts in the store (about 1 week)

- **Schema:** a migration adds the two tables and the owner columns.
- **Rows:** the store's row types gain them.
- **Desktop:** a desktop database migrates in place and keeps working.
- **Done when:** existing store tests pass, and a migrated desktop database opens with every
  owner empty.

### S2: Signing in (about 1–2 weeks)

- **Mode:** `--accounts` and the `Caller` extractor.
- **Tokens:** sign-in tokens (JWT, 12 hours) and personal access tokens (`rct_…`, SHA-256
  stored, read-only ones refused writes).
- **Routes:** the 10 auth routes, first-account adoption, password-reset email, and the
  `reset-password` subcommand.
- **Done when:** the replay matches `test_auth.py`, and a token made by the Python backend is
  accepted by the Rust server with the same secret.

### S3: Ownership on every route (about 2–3 weeks)

- **Scope:** every query is scoped by `Caller`, across all 75 routes, including:
  - curve visibility (the built-in curves for everyone, imported ones for their owner only);
  - snapshot deletion by its creator;
  - jobs;
  - project export and import;
  - run history;
  - influence.
- **Replay:** it replays every recorded exchange in accounts mode, and also still runs in
  single-user mode for the desktop.
- **Done when:** both replays show 0 differed and 0 not ported, with every exemption
  explained.

### S4: The database (about 1 week)

**Decided: SQLite**, over PostgreSQL. The store is written for SQLite, and a
server for a group has small writes and CPU-bound solves, which suits one writer and many
readers (WAL).

- **What it removes:** the database container, its password and its separate backups.
- **Moving data across:** `radical-server import-postgres DATABASE_URL` copies an existing
  installation into a SQLite file in one step:
  - every account, token, project, analysis, result, curve and snapshot;
  - with owners kept and ids unchanged, so bookmarked links still work.
  It refuses to write into a database that already has data.
- **Done when:** a copy of a real PostgreSQL installation imports, and the replay's checks
  pass against the result. The check is that each account sees what it saw before, with the
  same numbers.

### S5: The Docker image (about 1 week)

- **Root `Dockerfile`:** builds the frontend and `radical-server` (multi-stage, amd64 and
  arm64), then runs
  `radical-server --accounts --frontend /app/static --db /data/radical.db` on port 8000.
- **Image:** no Python, no BLAS, no PostgreSQL client, so it is much smaller: one binary, the
  frontend and the curves.
- **`docker-compose.prod.yml`:** one service, a `/data` volume, the same environment variables
  and port.
- **Health:** the healthcheck hits `/api/health`.
- **Upgrading an existing installation** is one documented sequence:
  1. download the new compose file;
  2. run `docker compose run --rm radical import-postgres …` once, against the old database
     container;
  3. start the new image.
- **`INSTALLATION.md`:** rewritten to match: fresh install, upgrading, password reset, and
  backups (copy one file).
- **Done when:** a fresh install and an upgrade from the current image both work, step by
  step from the guide.

### S6: Cut over and retire Python (about 1 week)

- **Releases:** publish the Rust image as the next major version.
  - The last Python image stays on GHCR under its version tag, so an installation can go back.
  - Going back means returning to the old compose file and database container: the importer
    copies, so nothing in PostgreSQL is changed.
- **One release later, delete Python:**
  - delete `backend/`, its CI jobs, and `docker-compose.yml`'s development stack. The frontend
    dev server then proxies to `radical-server`.
  - the recorded corpus (`desktop/corpus`) stays as the contract.
- **New tests** from then on are written in Rust.

**Total: roughly 7–9 weeks.** S3 is the largest, because ownership touches every route.

## Decisions

1. **SQLite or PostgreSQL** for the server (S4). **Decided: SQLite**, with
   `import-postgres` for existing installations.
2. **`/api/docs`.** FastAPI serves an interactive API page. **Decided:** the Rust server serves
   the recorded OpenAPI document (`radical-server/api/openapi.json`, held to the corpus by a
   test) with the same Swagger and ReDoc pages.
3. **How long the Python image stays available** for going back (S6). **Decided:** one
   release. The last Python image is `v1.0.0` (also `1.0` and `1`); the Rust server is
   released as `v2.0.0`.

## Afterwards

- **Exact solver.** It is the one place the Rust server is not faster: NumPy hands its large
  matrix products to Apple Accelerate or OpenBLAS. Calling a BLAS from `radical-core`'s
  `matmul` would close the gap. It is optional and independent of the move.
- **Two queues.** Solves now run one per core, less one, so a busy server queues less.
  What is left of it, a per-account limit and a queue that survives a restart, is
  `REMAINING_WORK.md` §8.
