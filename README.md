# RadiCal

A web-based tool for radiocarbon dating analysis: calibration against IntCal20/Marine20/SHCal20,
Bayesian chronological modelling with stratigraphic constraints (OxCal-style Sequences, Phases and
Boundaries), and interactive posterior visualization.

## Features

- **Three analysis types**: single calibration, independent calibration of several samples at once
  ("group calibration"), and full Bayesian modelling with stratigraphic constraints.
- **Calibration curves**: IntCal20, Marine20, SHCal20, with marine reservoir (ΔR) correction.
  Which curves exist, and everything countable about each of them — point count, calendar and
  radiocarbon range, the calendar grid's step structure, a content hash — comes from one registry
  (`backend/app/services/curve_registry.py`) that measures them from the curves it actually loads,
  and is what the curve pickers, the CSV importer and the OxCal importer all read.
- **Your own curves**: import a curve from a `.14c` or a headed CSV (columns matched by name in any
  order; a BC/AD calendar column converted on the way in), previewed before anything is stored.
  An imported curve is then selectable on any date, understood by the CSV and OxCal importers under
  whatever aliases it was given, and exportable byte-for-byte as uploaded. Its numbers are immutable
  — a correction is a new curve, not an edit — and a curve something has been calibrated against
  cannot be deleted, only archived, so an analysis that used it still reruns. The **Curves** page
  is where they are imported, described, downloaded and archived, and it shows what was measured
  from each curve — points, range, calendar grid — beside it.
- **Curve browser**: look at any curve's shape with its own ±1σ/±2σ error bounds, scrolled and
  zoomed along the calendar axis. A strip under the plot shows the whole 55,000 years with the
  current view marked — drag it to move, drag an edge to zoom, click to jump — because panning
  inside a chart never says *where* you are. Hovering reports what the curve says at that
  calendar age and how steeply it is running, which is what decides how tightly anything can be
  dated there: near 1 the curve tracks calendar time, near 0 it is a plateau and a measurement
  spreads over centuries, below 0 it reverses and one measurement splits into disjoint ranges.
- **Compare and simulate**: overlay a second curve and read their *difference* on its own panel —
  two curves drawn over each other are indistinguishable at any zoom that fits a millennium on
  screen, while IntCal20 − SHCal20 shows the hemispheric offset (about 40 ¹⁴C years) at a glance.
  Clicking a calendar year shows what a date from it would actually look like: inside the Hallstatt
  plateau, a ±25 yr measurement comes back as **four disjoint calendar ranges spread over 337
  years**, which says what a plateau costs more plainly than any single number. The visible window
  exports as CSV.
- **Expected temporal resolution**: under the curve, on the same calendar axis, how tightly a date
  from each year could be known *before anyone measures anything* — the method of Svetlik et al.
  (2019), as implemented in [res14c](https://github.com/demjanp/res14c_online). Mean plus a
  5th–95th percentile band for how lucky one date can be, with the assumed laboratory error
  selectable — ±25 by default, what a commercial date typically carries, down to the ±15 of the
  best AMS work — and a CSV export. On IntCal20 at ±25 it puts the Hallstatt plateau at about 330
  years against 103 at the steep drop into it; at ±15 the same two points are 312 and 33. Either
  way it is the difference between a date worth commissioning and one that will not answer the
  question.
- **Mixed curves**: OxCal's `Mix_Curves`, for a sample whose carbon came from more than one
  reservoir — a human or animal with a partly marine diet, whose bone is neither terrestrial nor
  marine carbon and is wrong by centuries against either curve alone. Built from two curves and a
  proportion, with the uncertainty on that proportion widening the result most where the two
  curves are furthest apart, which is where being wrong about the diet moves a date furthest. An
  `.oxcal` file using `Mix_Curves` now imports, matched against a mixture this app already has —
  and naming exactly what to create when it has none.
- **Bayesian chronological modelling**: an explicit model tree of nested Sequences, Phases,
  Boundaries and Hiatuses (the OxCal idiom), each date assigned to a Phase; TPQ/TAQ age limiters;
  an optional outlier model per date. Solved exactly where the model permits it, and by OxCal's own
  MH/Gibbs scheme otherwise — validated against real OxCal 4.4 runs in
  [`test_oxcal_agreement.py`](backend/tests/test_oxcal_agreement.py),
  [`test_exact_chain.py`](backend/tests/test_exact_chain.py) and
  [`test_gibbs.py`](backend/tests/test_gibbs.py).
- **Wiggle matching** (OxCal's `D_Sequence`/`V_Sequence`): a series of dates separated by counted
  rings or varves has ONE free parameter — the calendar age at the reference ring — so it is solved
  in closed form rather than sampled. At the Hallstatt plateau that is the difference between a
  single date's 285-year range in two pieces and eight samples over 250 rings giving 20 years in
  one. Enter ring numbers and the app converts them to gaps, in the one direction that is right:
  a gap counts years *before* the reference and increases into the past. Where the gaps were
  measured rather than counted, each carries its own uncertainty.
- **Age-depth modelling** (OxCal's `P_Sequence`): deposition modelled as a Poisson process down a
  core, giving age against depth with a band round it, the accumulation rate between each pair of
  levels, and an age at any depth you ask for — including ones nothing was dated at. The rigidity
  is inferred from the dates rather than set, and reported alongside the band: a stiff model draws
  a straighter line through a calibration plateau than the dates alone support, and the app says
  when that rigidity has run up against the end of the range it was allowed.
- **Harris matrix diagram**: the model tree drawn to the conventional reading, oldest at the bottom
  — nesting shown as boxes within boxes, Boundaries as labelled rules carrying their modelled
  range, and an arrow only between adjacent blocks that nothing else already separates. Laid out
  from the tree by `utils/harrisLayout.ts`, whose geometry is unit-tested rather than eyeballed.
- **Durations and differences** (opt-in): how long each Sequence, each Phase, and each phase's set
  of *dated events* lasted, and how far apart the phases sit — a gap in years where they are
  separate, an overlap where they are not, each graded from "none" to "extensive" at the same
  68.3%/95.4% levels the HPD regions use. The three durations are three different questions: a Phase's
  duration is the interval its Boundaries enclose, its dated events span less than that, and the
  distance between the two is how much of the phase the dates did not catch. Every one of these is a
  function of several modelled ages at once, so they are read off *joint* draws of the posterior —
  exactly what the marginal distributions elsewhere in the app cannot give you (pairing two boundary
  marginals puts 8% of one test phase's durations below zero). On the exact solver those draws are
  independent draws from the true posterior, by backward sampling over the same chain it already
  built. A group calibration gets the differences without the durations: dates with no relationship
  asserted between them belong to no phase.
- **Diagnostics**: an OxCal-style agreement index per date (how much the model had to move it away
  from its own unconstrained calibration) and an outlier posterior probability where an outlier model
  is applied.
- **Posterior visualization**: Plotly charts, individually or all stacked on one shared calendar
  axis, with HPD range bars at 68.3%/95.4%/99.7% confidence, optional mean/median markers, and an
  optional overlay of each date's unconstrained ("prior") calibration behind its modelled posterior.
- **Projects and date collections**: group analyses under a project; keep a project's own collection
  of dates (entered individually, by bulk paste, or by file import) and deploy any selection of them
  into a new analysis, independently of how many other analyses have already used them; re-run one
  analysis or every analysis in a project; duplicate or edit an existing analysis's inputs.
- **File import**: read dates from a CSV (header-driven, column order irrelevant, common lab-report
  spellings understood) or straight from an OxCal `.oxcal` model file, whose Sequences, Phases,
  Boundaries, wiggle-matched and deposition sequences, per-scope curves and per-date outlier
  settings all come across. Every import is previewed before anything is stored, and is
  all-or-nothing. An OxCal construct this app does not model — a non-uniform Boundary, `C_Date`,
  `R_Combine`, `U_Sequence`, an unknown curve — fails the import naming itself rather than being
  read as the nearest thing that is implemented.
- **Export**: CSV, JSON, and a printable PDF report — the analysis's metadata, the curves and
  solver that actually produced it, the results table in the model's own reading order with dates
  and Boundaries reporting through the same columns, the durations-and-differences table where one
  was produced, and a posterior plot for each parameter with its unmodelled calibration behind it.
  All three come from one assembly, so the three downloads of one analysis cannot disagree; a column appears in the report only when the run has something to put
  in it, so a plain calibration is not padded out with the diagnostics only a model produces.

## Tech Stack

### Frontend
- **React 18** with TypeScript, built with **Vite**
- **Material-UI** for components
- **Plotly.js** for posterior distribution charts
- **React Flow** for the Harris matrix / model tree diagram
- **Zustand** for the shared New Analysis / Model Structure draft state

### Backend
- **Python 3.11** with **FastAPI**
- **NumPy/SciPy** for the calibration math and both Bayesian solvers (exact integration for
  chain-shaped models, OxCal's own MH/Gibbs scheme otherwise — no PyMC, no MCMC sampler dependency)
- **SQLAlchemy** ORM over **PostgreSQL**, with **Alembic** migrations applied on startup
- **ReportLab** and **Matplotlib** for the PDF report — the figures are drawn through matplotlib's
  object API rather than pyplot, whose global figure registry is a leak waiting to happen in a
  long-lived server process

## Project Structure

```
RadiCal/
├── frontend/                 # React TypeScript application
│   └── src/
│       ├── components/       # Reusable UI (plots, Harris matrix, dialogs, forms)
│       ├── pages/            # Route-level pages (Dashboard, NewAnalysis, Results, ...)
│       ├── stores/           # Shared draft state (Zustand)
│       ├── theme/            # MUI theme customization
│       ├── types/            # Shared TypeScript types
│       └── utils/            # Date formatting, HPD/stratigraphy helpers
├── backend/                  # FastAPI application
│   └── app/
│       ├── api/               # Route handlers
│       ├── services/          # Calibration math, the three solvers, model tree, agreement
│       ├── schemas/            # Pydantic request/response schemas
│       ├── models.py           # SQLAlchemy ORM models
│       └── main.py
├── backend/tests/            # pytest suite, plus fixtures from real OxCal 4.4 runs
├── calibration_data/         # Full IntCal20/Marine20/SHCal20 curve files
├── docs/                     # Installation guide and user manual
├── docker-compose.yml
└── .env.example
```

## Quick Start

### Prerequisites
- Docker and Docker Compose (recommended), **or** Node.js 18+, Python 3.11+ and PostgreSQL 14+ for
  running the two services directly.

### Installation

```bash
git clone https://github.com/maqomaqo/RadiCal.git
cd RadiCal
cp .env.example .env
docker-compose up -d

# Frontend: http://localhost:5173
# Backend API + interactive docs: http://localhost:8000/docs
```

Open the frontend and **create an account** — the first one made on an installation takes
ownership of any projects and analyses that were there before accounts existed, and the sign-up
screen says how many it adopted.

See [`docs/INSTALLATION.md`](docs/INSTALLATION.md) for running the frontend and backend outside
Docker, and for troubleshooting.

### Running the published image

Every push to `main` publishes a ready-built image, `ghcr.io/maqomaqo/radical:latest`, that
contains the backend, the built frontend and the calibration curves in one container (built by
[`.github/workflows/publish-image.yml`](.github/workflows/publish-image.yml) from the root
[`Dockerfile`](Dockerfile), for both amd64 and arm64). It needs only a PostgreSQL database and a
signing key:

```bash
export RADICAL_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
docker compose -f docker-compose.prod.yml up -d

# Everything, frontend and API, on http://localhost:8000
```

or, against a database you already have:

```bash
docker run -d -p 8000:8000 \
  -e DATABASE_URL=postgresql://user:password@db-host:5432/radical \
  -e RADICAL_SECRET_KEY=... \
  -v radical_uploads:/app/uploads \
  ghcr.io/maqomaqo/radical:latest
```

The image runs as `RADICAL_ENV=production` and refuses to start without `RADICAL_SECRET_KEY` (see
[Accounts](#accounts)). The repository is private, so pulling needs a
[GitHub token with `read:packages`](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#authenticating-to-the-container-registry)
unless the package is made public in its settings.

### Accounts

Every project and analysis belongs to one account and is visible to nobody else. There is no
sharing and no administrator who can see everything. Calibration curves are the deliberate
exception: they are reference data, readable by everyone signed in and editable only by whoever
imported them, because a per-account curve registry would let two people on one site calibrate
against different copies of the same curve.

Registration is **open** — anyone who can reach the app can create an account. The gate is
expected to be whatever fronts the deployment (a VPN, an institutional SSO proxy, a private
network); what accounts add behind that gate is attribution and separation, not admission control.

Before exposing this to anyone else, set `RADICAL_SECRET_KEY` to a long random string and
`RADICAL_ENV` to something other than `development`. Login tokens are signed with it, so without
one the app falls back to a key published in this repository — it says so at startup, and refuses
to start at all once `RADICAL_ENV` says the deployment is real. See `.env.example`.

### Development

Both containers bind-mount their source and hot-reload, so `docker-compose up -d` is normally enough
during development too. To run a service directly on the host instead:

#### Frontend
```bash
cd frontend
npm install
npm run dev
```

#### Backend
```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

#### Backend tests
The runtime image installs `requirements.txt` only, so the test dependencies live in a separate
file:
```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

#### Frontend tests
```bash
cd frontend
npm test
```
Vitest, running in plain node. It covers the pure helpers — most importantly
`utils/harrisLayout.ts`, whose geometry cannot be checked any other way: React Flow draws into a
measured viewport, so nothing in the DOM tells you whether the boxes actually line up.

## API Overview

Full interactive documentation (generated from the actual FastAPI routes) is at
`http://localhost:8000/docs` once the backend is running.

**Every endpoint below needs a bearer token** except `/api/auth/register`, `/api/auth/login` and
`/api/health`. Get one from `POST /api/auth/login` and send it as `Authorization: Bearer <token>`;
it lasts twelve hours. Someone else's project or analysis answers **404**, not 403 — an id must
not be a way to discover that a row exists.

The main groups:

- **Authentication** (`/api/auth/register`, `/api/auth/login`, `/api/auth/me`,
  `/api/auth/password`) — register and login both return a token and the account it belongs to.
  Changing a password requires the current one, so a token left on a shared machine cannot be used
  to lock its owner out.

- **Calibration curves** (`/api/curves`, `/api/curves/{id}`, `/api/curves/type/{type}`) — what
  curves are available and what is true about each. Served from the registry, so this list and the
  list a request is validated against cannot drift apart. Imported curves add
  `/api/curves/import/preview` (parse only, stores nothing), `POST /api/curves`,
  `PATCH /api/curves/{id}` (editorial fields only), `DELETE /api/curves/{id}` (409 while in use,
  `?archive=true` instead), `/api/curves/{id}/restore`, `/api/curves/{id}/usage` and
  `/api/curves/{id}/export`. `/api/curves/{id}/points` serves a windowed, decimated slice for the
  browser — min/max per bucket rather than every Nth point, so a curve's wiggles survive being
  drawn at any zoom. `/api/curves/{id}/resolution` (and `.csv`) serves the expected-resolution
  profile, `/api/curves/compare` two curves and their difference,
  `POST /api/curves/{id}/simulate` a hypothetical date from one calendar year, and
  `POST /api/curves/mixed` a mixture of two curves.
- **Analysis** (`/api/analysis`, `/api/analysis/{id}`, `/api/analysis/calibrate`,
  `/api/analysis/calibrate-group`, `/api/analysis/bayesian`, plus rerun/duplicate/edit-inputs and
  model-tree endpoints) — create, run, retrieve, and manage analyses of all three types.
- **Projects** (`/api/projects`, `/api/projects/{id}`, `/api/projects/{id}/rerun`) — group analyses
  and re-run them in bulk.
- **Project date collections** (`/api/projects/{id}/samples`, `/api/projects/{id}/deploy`) — a
  project's own reusable set of dates.
- **Import** (`/api/import/dates`, `/api/import/csv-template`, `/api/import/csv-columns`) — parse an
  uploaded `.csv` or `.oxcal` and return what it held. These only read: the parsed dates are stored
  through the endpoints above, so an upload is not a second route into the database with its own
  validation to drift out of step.
- **Results** (`/api/results/{id}/export/csv`, `/api/results/{id}/export/json`,
  `/api/results/{id}/pdf`, `/api/results/{id}/table`) — export a completed analysis. The CSV and
  the PDF both take `?date_format=`, the reporting convention the results page is showing. An
  analysis with no results still exports, saying so, rather than failing the download.

## Documentation

- [`docs/INSTALLATION.md`](docs/INSTALLATION.md) — setup and troubleshooting.
- [`frontend/src/content/USER_MANUAL.md`](frontend/src/content/USER_MANUAL.md) — using the app. It
  lives inside the frontend because the app ships it: the Help page (the question mark in the top
  bar, `/help`) renders this exact file, so there is no second copy to keep in step. Edit it as
  Markdown; `frontend/src/utils/markdown.ts` documents the subset the page can render, and its
  tests fail on anything outside it or on a cross-reference that no longer resolves.
- [`REMAINING_WORK.md`](REMAINING_WORK.md) — the open work: what is not built yet, checked against
  the code.

## References

- Svetlik, I., et al. (2019). The best possible time resolution: how precise could a radiocarbon dating method be? Radiocarbon, 61(6).
- Reimer, P. J., et al. (2020). IntCal20 Northern Hemisphere radiocarbon age calibration curve. Radiocarbon, 62(4).
- Bronk Ramsey, C. (2009). Bayesian analysis of radiocarbon dates. Radiocarbon, 51(1).
- Bronk Ramsey, C. (1995). Radiocarbon calibration and analysis of stratigraphy: the OxCal program. Radiocarbon, 37(2).
- Buck, C. E., et al. (1996). Bayesian Approach to Interpreting Archaeological Data.
- Heaton, T. J., et al. (2020). Marine20—the marine radiocarbon age calibration curve. Radiocarbon, 62(4).
- Nicholls, G., and Jones, M. (2001). Radiocarbon dating with temporal order constraints. Applied Statistics, 50(4).
- Bronk Ramsey, C. (2008). Deposition models for chronological records. Quaternary Science Reviews, 27(1-2).
- Galimberti, M., Bronk Ramsey, C., and Manning, S. W. (2004). Wiggle-match dating of tree-ring sequences. Radiocarbon, 46(2).

