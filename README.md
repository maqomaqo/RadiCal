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
  from each curve — points, range, calendar grid — beside it. Mixed curves and the visual curve
  browser are planned in [`docs/CURVE_MANAGER_PLAN.md`](docs/CURVE_MANAGER_PLAN.md).
- **Bayesian chronological modelling**: an explicit model tree of nested Sequences, Phases,
  Boundaries and Hiatuses (the OxCal idiom), each date assigned to a Phase; TPQ/TAQ age limiters;
  an optional outlier model per date. Solved exactly where the model permits it, and by OxCal's own
  MH/Gibbs scheme otherwise — see [`REMAINING_WORK.md`](REMAINING_WORK.md) for how the solvers were
  validated against real OxCal 4.4 runs.
- **Harris matrix diagram**: the model tree drawn to the conventional reading, oldest at the bottom
  — nesting shown as boxes within boxes, Boundaries as labelled rules carrying their modelled
  range, and an arrow only between adjacent blocks that nothing else already separates. Laid out
  from the tree by `utils/harrisLayout.ts`, whose geometry is unit-tested rather than eyeballed.
- **Diagnostics**: an OxCal-style agreement index per date (how much the model had to move it away
  from its own unconstrained calibration) and an outlier posterior probability where an outlier model
  is applied.
- **Posterior visualization**: Plotly charts, individually or all stacked on one shared calendar
  axis, with HPD range bars at 68%/95%/99.7% confidence, optional mean/median markers, and an
  optional overlay of each date's unconstrained ("prior") calibration behind its modelled posterior.
- **Projects and date collections**: group analyses under a project; keep a project's own collection
  of dates (entered individually, by bulk paste, or by file import) and deploy any selection of them
  into a new analysis, independently of how many other analyses have already used them; re-run one
  analysis or every analysis in a project; duplicate or edit an existing analysis's inputs.
- **File import**: read dates from a CSV (header-driven, column order irrelevant, common lab-report
  spellings understood) or straight from an OxCal `.oxcal` model file, whose Sequences, Phases,
  Boundaries, per-scope curves and per-date outlier settings all come across. Every import is
  previewed before anything is stored, and is all-or-nothing. An OxCal construct this app does not
  model — a non-uniform Boundary, `C_Date`, `R_Combine`, a deposition-model sequence, an unknown
  curve — fails the import naming itself rather than being read as the nearest thing that is
  implemented.
- **Export**: CSV and JSON. (PDF export is not implemented — see below.)

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
- **SQLAlchemy** ORM over **PostgreSQL**

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
│       ├── services/          # Calibration math, both Bayesian solvers, model tree, agreement
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

See [`docs/INSTALLATION.md`](docs/INSTALLATION.md) for running the frontend and backend outside
Docker, and for troubleshooting.

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
`http://localhost:8000/docs` once the backend is running. The main groups:

- **Calibration curves** (`/api/curves`, `/api/curves/{id}`, `/api/curves/type/{type}`) — what
  curves are available and what is true about each. Served from the registry, so this list and the
  list a request is validated against cannot drift apart. Imported curves add
  `/api/curves/import/preview` (parse only, stores nothing), `POST /api/curves`,
  `PATCH /api/curves/{id}` (editorial fields only), `DELETE /api/curves/{id}` (409 while in use,
  `?archive=true` instead), `/api/curves/{id}/restore`, `/api/curves/{id}/usage` and
  `/api/curves/{id}/export`.
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
  `/api/results/{id}/table`) — export a completed analysis. `/api/results/{id}/pdf` exists but
  returns `501 Not Implemented` — see [`REMAINING_WORK.md`](REMAINING_WORK.md).

## Documentation

- [`docs/INSTALLATION.md`](docs/INSTALLATION.md) — setup and troubleshooting.
- [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md) — using the app.
- [`REMAINING_WORK.md`](REMAINING_WORK.md) — what's built, what isn't, and why, kept current against
  the actual code rather than written once and left to drift.

## References

- Reimer, P. J., et al. (2020). IntCal20 Northern Hemisphere radiocarbon age calibration curve. Radiocarbon, 62(4).
- Bronk Ramsey, C. (2009). Bayesian analysis of radiocarbon dates. Radiocarbon, 51(1).
- Bronk Ramsey, C. (1995). Radiocarbon calibration and analysis of stratigraphy: the OxCal program. Radiocarbon, 37(2).
- Buck, C. E., et al. (1996). Bayesian Approach to Interpreting Archaeological Data.
- Heaton, T. J., et al. (2020). Marine20—the marine radiocarbon age calibration curve. Radiocarbon, 62(4).
- Nicholls, G., and Jones, M. (2001). Radiocarbon dating with temporal order constraints. Applied Statistics, 50(4).

## License

[CC0 1.0 Universal](LICENSE) — public domain dedication.
