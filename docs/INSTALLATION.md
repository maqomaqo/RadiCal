# Installation Guide

## Requirements

| To... | You need |
|---|---|
| Run RadiCal (recommended) | [Docker Desktop](https://www.docker.com/products/docker-desktop/) on macOS or Windows, or Docker Engine with the Compose plugin on Linux |
| Run the backend without Docker | Python **3.12, 3.13 or 3.14**, and PostgreSQL 15 or later (or just the Docker stack's own database) |
| Run the frontend without Docker | Node.js **22 or 24** (the LTS releases; Vite needs 22.12 or later) |

Python 3.11 and older will not install the pinned scientific packages (numpy 2.5, scipy 1.18),
and Node 18 and 20 are past end of life. On Windows on ARM, use Docker: not every pinned package
has a native ARM64 Windows build.

The commands below are written for macOS Terminal (zsh) and Windows PowerShell. Docker Compose
is `docker compose` (a space); the older `docker-compose` survives on some installs only as a
compatibility alias.

## Getting the code

The repository is private, so GitHub needs to know who you are. It stopped accepting account
passwords for `git clone` in 2021. The simplest route on either platform is the
[GitHub CLI](https://cli.github.com/):

```bash
gh auth login
gh repo clone maqomaqo/RadiCal
cd RadiCal
```

Plain `git clone https://github.com/maqomaqo/RadiCal.git` also works where Git Credential
Manager is installed (it comes with Git for Windows; on macOS, `brew install
--cask git-credential-manager`), which opens a browser sign-in; or give it a
[personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
with read access to this repository as the password.

## Quick start with Docker

```bash
cp .env.example .env
docker compose up -d
```

The first start builds both images and takes a few minutes; after that it is
seconds. Then:

- the app: http://localhost:5173
- the API's interactive docs: http://localhost:8000/docs

Open the app and create an account. `docker compose logs -f backend` shows the
backend's output if something does not come up.

### macOS

Docker Desktop runs on both Apple silicon and Intel Macs, and every image here
has a native arm64 build.

### Windows

Docker Desktop needs the WSL 2 backend (its installer offers to enable it).
Run the commands above in PowerShell; `cp` works there as an alias for
`Copy-Item`.

**Where to put the checkout.** The development stack bind-mounts the source
into its containers so that edits reload. Docker Desktop passes no file-change
events from a Windows drive (`C:\...`) into a Linux container, so with the
checkout there the frontend does not notice an edit. Either:

- clone inside WSL 2 (open an Ubuntu terminal, clone into `~`, run
  `docker compose` from there) — edits reload instantly and file access is much
  faster; or
- keep the checkout on `C:\` and add `CHOKIDAR_USEPOLLING=true` to `.env`, which
  makes the frontend poll for changes. The backend already polls.

If you only want to *use* RadiCal rather than work on it, none of this
matters — or skip the development stack entirely and run the published image
(see [the README](../README.md#running-the-published-image)).

**Line endings.** Git for Windows checks files out with CRLF line endings by
default. Nothing in the app minds, including the calibration curve files.

## Running without Docker

Useful when working on one half of the app. The pieces are independent: run the
database in Docker and the backend on the host, or everything on the host.

### 1. A database

The easiest database is the Docker stack's own, on port 5433:

```bash
docker compose up -d postgres
```

That is what `DATABASE_URL` in `.env.example` points at, so there is nothing
else to set.

To use a PostgreSQL installed on the machine instead (Homebrew or
[Postgres.app](https://postgresapp.com/) on macOS, the
[EDB installer](https://www.postgresql.org/download/windows/) on Windows),
create a user and a database it **owns**. Ownership matters: since PostgreSQL 15
a user can no longer create tables in a database it does not own, and the
backend's first start fails with `permission denied for schema public`.

macOS (Homebrew and Postgres.app make you the superuser):

```bash
createuser -P radiocarbon                    # choose a password
createdb -O radiocarbon radiocarbon_db
```

Windows (the EDB installer's superuser is `postgres`, and its tools are not on
`PATH`; adjust `17` to your version):

```powershell
$pg = "C:\Program Files\PostgreSQL\17\bin"
& "$pg\createuser.exe" -U postgres -P radiocarbon
& "$pg\createdb.exe" -U postgres -O radiocarbon radiocarbon_db
```

Then set `DATABASE_URL` in `.env` to
`postgresql://radiocarbon:YOUR_PASSWORD@localhost:5432/radiocarbon_db`.

The backend creates the tables itself on its first start — there is no separate
setup step.

### 2. The backend

macOS:

```bash
cd backend
python3.12 -m venv venv        # or python3.13 / python3.14
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

On macOS the command is `python3`, not `python`. `python3 --version` says
which one you have; the installers at [python.org](https://www.python.org/downloads/)
and Homebrew (`brew install python@3.13`) both work.

Windows (PowerShell):

```powershell
cd backend
py -3.13 -m venv venv          # or -3.12 / -3.14
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

`py` is the Python launcher that the python.org installer adds. If PowerShell
refuses to run `Activate.ps1` ("running scripts is disabled on this system"),
allow scripts you create yourself, once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Either way the API is then on http://localhost:8000 (docs at `/docs`). The
backend reads the repository's `.env` and finds the calibration curves in
`calibration_data/` by itself.

Tests (they use a throwaway SQLite file, not your database):

```bash
pip install -r requirements-dev.txt
pytest
```

### 3. The frontend

```bash
cd frontend
npm ci
npm run dev
```

Open http://localhost:5173. The dev server forwards everything under `/api` to
the backend at `http://localhost:8000`; to use a backend somewhere else, set
`API_PROXY_TARGET` before starting it (`API_PROXY_TARGET=http://host:8000 npm run
dev`, or `$env:API_PROXY_TARGET = "http://host:8000"` first in PowerShell).

`npm test` runs the frontend's unit tests.

## Upgrading

Pull, then rebuild and restart:

```bash
git pull
docker compose up -d --build --renew-anon-volumes
```

`--build` installs any new backend or frontend dependencies into the images.
`--renew-anon-volumes` matters for the frontend: its installed packages live in
a volume of their own, which otherwise keeps the *old* ones and hides the
rebuilt image's. The database is a named volume and is not touched.

For a backend run outside Docker, recreate the virtual environment when
`requirements.txt` changes Python versions (it now needs 3.12 or later):
delete `backend/venv` and repeat step 2.

## Schema changes

The schema is versioned with Alembic (`backend/alembic/`). Day to day this
needs no attention: the backend migrates on startup, whether that means
creating the database from nothing or applying migrations added since the last
run.

A database created before Alembic was adopted (any version before 2026-08-19)
is recognised on its first start after an upgrade, brought up to date, and
joined to the migration history automatically. Nothing to do by hand, and
existing analyses are kept.

**Changing a model.** Edit `backend/app/models.py`, then generate the
migration and read what it produced before committing it:

```bash
docker compose exec backend alembic revision --autogenerate -m "what changed"
```

Autogenerate is a good first draft, not an authority — it does not see a
rename (it proposes a drop and an add, which loses the data) and it cannot
know a backfill is needed. `backend/tests/test_migrations.py` fails if the
models and the migrations disagree, so a forgotten migration cannot reach
anyone's database.

To take migration out of startup — for a deployment that would rather run it
deliberately — set `RADICAL_SKIP_MIGRATIONS=1` and run
`alembic upgrade head` yourself.

## Stopping

```bash
docker compose down        # stop; the database is kept
docker compose down -v     # stop AND delete the database
```

## Troubleshooting

**A port is already in use.** The stack uses 5173 (frontend), 8000 (backend)
and 5433 (Postgres, mapped to its own 5432 inside the container). Change the
left-hand number under `ports:` in `docker-compose.yml`.

**`pip install` fails building numpy, scipy or psycopg.** The Python is too old
(3.11 or earlier) or not a 64-bit CPython. Every pinned package has a
ready-made wheel for 3.12–3.14 on Windows x64, macOS and Linux; a compile step
means pip found none for your interpreter. Check with `python3 --version`
(macOS) or `py --list` (Windows) and recreate the venv with a supported one.

**The app loads but signing in fails, and the terminal running `npm run dev`
shows `http proxy error`.** The backend is not running, or not on
`localhost:8000`. Start it, or set `API_PROXY_TARGET`.

**The backend stops at startup with `permission denied for schema public`.**
The database is not owned by the user in `DATABASE_URL`. Fix it with
`ALTER DATABASE radiocarbon_db OWNER TO radiocarbon;` as the superuser, or
recreate it with `createdb -O` as above.

**The backend cannot connect to the database.** From the host, the host name is
`localhost` (port 5433 for the Docker stack's database); `postgres` is its name
only inside the Docker network. Check the user and password in `.env` match the
ones the database was created with. The Docker stack's database keeps the
credentials it was *first* created with — changing `POSTGRES_PASSWORD` later
does nothing until `docker compose down -v`.

**On Windows, the frontend in Docker does not reload.** See
[Where to put the checkout](#windows).

## Environment variables

Set in `.env` (see `.env.example` for the full, commented list):

- `DATABASE_URL` — the connection string for a backend run outside Docker. The
  Docker stack builds its own from `POSTGRES_USER`, `POSTGRES_PASSWORD` and
  `POSTGRES_DB`.
- `CORS_ORIGINS` — origins the backend accepts cross-origin requests from.
  Nothing needs it in the standard setups, where the frontend calls `/api` on
  its own origin.
- `RADICAL_ENV`, `RADICAL_SECRET_KEY` — see
  [Accounts](../README.md#accounts). Required for anything reachable by other
  people.
- `RADICAL_SKIP_MIGRATIONS` — set to `1` to stop the backend migrating the
  database on startup (see Schema changes above).
- `CHOKIDAR_USEPOLLING` — set to `true` on Windows with the checkout on a
  Windows drive (see [Windows](#windows)).
- `API_PROXY_TARGET` — where the frontend dev server forwards `/api`; defaults to
  `http://localhost:8000`.
- `INTCAL_CURVE_PATH` / `MARINE_CURVE_PATH` / `SHCAL_CURVE_PATH` — only needed
  if the curve files live somewhere other than `calibration_data/`.

MCMC/Bayesian settings (posterior draws, solver choice, the uniform span prior)
are configured per-analysis in the New Analysis form, not via environment
variables.
