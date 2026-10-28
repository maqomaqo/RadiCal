# RadiCal
[![DOI](https://zenodo.org/badge/1322368174.svg)](https://doi.org/10.5281/zenodo.23727568)

A tool for radiocarbon dating analysis: calibration, Bayesian chronological modelling with constraints, and interactive posterior visualization. It runs as a desktop app (RadiCal Desktop, for macOS, Windows and Linux) or on a server that a group shares in the browser.

## Features

- **Calibration** of single dates or groups of dates against IntCal20, Marine20 and SHCal20, with
  marine reservoir (ΔR) correction.
- **Custom and mixed curves**: import your own curve (`.14c` or CSV) or mix two curves in a chosen
  proportion.
- **Curve browser**: view, compare and simulate dates against any curve, including expected
  temporal resolution.
- **Bayesian chronological modelling**: OxCal-style Sequences, Phases, Boundaries and outlier
  models.
- **Wiggle matching and age-depth modelling**
- **Kernel density estimates**: how a group of dates is spread through time,
  without the spurious peaks of a summed distribution.
- **Geographic modelling**: place dates on a map, derive ΔR from a marine sample's position, and
  model the spread of a phenomenon across a landscape — speed, direction and likely origin.
- **Diagnostics and summaries**: agreement indices, durations, and differences between phases.
- **Interactive plots** of posteriors with HPD ranges.
- **Projects**: organise analyses and reusable date collections.
- **Import and export**: import from CSV or OxCal `.oxcal` files; export to CSV, JSON or a PDF
  report.

## Tech Stack

### Frontend
- **React 18** with TypeScript, built with **Vite**
- **Material-UI** for components
- **Plotly.js** for posterior distribution charts
- **React Flow** for the matrix / model tree diagram
- **Zustand** for the shared New Analysis / Model Structure draft state

### Server
- **Rust**, one binary (`radical-server`) that serves the API and the built frontend, both in
  RadiCal Desktop and in the Docker image
- **axum** for the HTTP API; calibration, both Bayesian solvers (exact integration for
  chain-shaped models, MH/Gibbs otherwise) and the PDF report in RadiCal's own crates
- **SQLite** for storage, one file per installation, with migrations applied on startup
- The answers of the earlier Python/FastAPI server, recorded in `desktop/corpus`, are the contract
  the Rust server is tested against

## Project Structure

```
RadiCal/
├── frontend/                 # React TypeScript application
│   └── src/
│       ├── components/       # Reusable UI (plots, Harris matrix, dialogs, forms)
│       ├── content/          # The user manual, shown on the Help page
│       ├── pages/            # Route-level pages (Dashboard, NewAnalysis, Results, ...)
│       ├── stores/           # Shared draft state (Zustand)
│       ├── theme/            # MUI theme customization
│       ├── types/            # Shared TypeScript types
│       └── utils/            # Date formatting, HPD/stratigraphy helpers
├── desktop/                  # The Rust workspace
│   ├── crates/
│   │   ├── radical-core/     # Calibration math, the solvers, model tree, agreement
│   │   ├── radical-io/       # File formats: date tables, curves, OxCal models
│   │   ├── radical-store/    # The SQLite store and its migrations
│   │   ├── radical-jobs/     # The background job queue
│   │   ├── radical-server/   # The HTTP API, accounts, the PDF report, the binary
│   │   └── radical-desktop/  # RadiCal Desktop's app shell
│   └── corpus/               # Recorded answers from the Python server, replayed as tests
├── calibration_data/         # Full IntCal20/Marine20/SHCal20 curve files
├── docs/                     # Installation and desktop release guides
├── docker-compose.yml
└── .env.example
```

## Installation

**RadiCal Desktop**, for your own computer, is an app: download the installer for macOS, Windows or
Linux from the [latest release](https://github.com/maqomaqo/RadiCal/releases/latest) and open it.
There is no account to make, and your work stays on your computer.

**RadiCal on a server**, for a group sharing one installation, runs in Docker on macOS, Windows or
Linux; Docker is the only thing to install. In short, from an empty folder:

```bash
curl -fLo docker-compose.yml https://raw.githubusercontent.com/maqomaqo/RadiCal/main/docker-compose.prod.yml
docker run --rm ghcr.io/maqomaqo/radical new-secret >> .env
docker compose up -d
```

then open http://localhost:8000 and create an account. 
On Windows, two of those lines differ in PowerShell - read the following installation guide for details.
[`docs/INSTALLATION.md`](docs/INSTALLATION.md) walks through each step on macOS and Windows,
with everyday use, updating, backups, upgrading an earlier installation, and troubleshooting.

The image, `ghcr.io/maqomaqo/radical`, holds the app, its web interface and the calibration curves
in one container, for both Intel/AMD and ARM machines, and keeps everything in one SQLite file.

## Accounts

On a server, every project and analysis belongs to a user account. There is no
sharing and no administrator who can see everything. RadiCal Desktop has no accounts: it is one
person's, and everything in it is theirs.

Registration is **open** — anyone who can reach the app can create an account. The gate is
expected to be whatever fronts the deployment (a VPN, an institutional SSO proxy, a private
network); what accounts add behind that gate is attribution and separation, not admission control.

Sign-ins are signed with `RADICAL_SECRET_KEY`, the key made during installation; the app refuses
to start without one.

## API Overview

This is RadiCal on a server's API. RadiCal Desktop runs the same API inside the app, but answers
only its own window (with a token made each launch), so scripts cannot reach it.

Full interactive documentation is at
`http://localhost:8000/api/docs` on a running installation, and the OpenAPI schema at
`/api/openapi.json`. The old `/docs` and `/openapi.json` redirect there.

**Every endpoint below needs a bearer token** except `/api/auth/register`, `/api/auth/login` and
`/api/health`. Get one from `POST /api/auth/login` and send it as `Authorization: Bearer <token>`;
it lasts twelve hours. For a script, make a **personal access token** (`rct_...`) on the Account
page instead: it lasts 7 days to a year, may be read-only (GET requests only), and can be revoked. Someone else's project or analysis answers **404**, not 403 — an id must
not be a way to discover that a row exists.

The main groups:

- **Authentication** (`/api/auth/register`, `/api/auth/login`, `/api/auth/me`,
  `/api/auth/password`, `/api/auth/tokens`) — register and login both return a token and the account
  it belongs to. Changing a password requires the current one, so a token left on a shared machine
  cannot be used to lock its owner out. `/api/auth/tokens` lists, makes (`POST`, the secret returned
  once) and revokes (`DELETE /api/auth/tokens/{id}`) personal access tokens; it and the password
  route refuse an access token, so a leaked one cannot make its own successor.

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

- [`docs/INSTALLATION.md`](docs/INSTALLATION.md) — installing RadiCal Desktop, or RadiCal on a server with Docker, and troubleshooting.
- [`frontend/src/content/USER_MANUAL.md`](frontend/src/content/USER_MANUAL.md) — using the app. It
  lives inside the frontend because the app ships it: the Help page (the question mark in the top
  bar, `/help`) renders this exact file, so there is no second copy to keep in step. Edit it as
  Markdown; `frontend/src/utils/markdown.ts` documents the subset the page can render, and its
  tests fail on anything outside it or on a cross-reference that no longer resolves.
- [`REMAINING_WORK.md`](REMAINING_WORK.md) — the open work: what is not built yet, checked against
  the code.

## References
RadiCal's calibration, Bayesian model and sampler follow the published methods below, and its
calibration and both solvers are tested against OxCal 4.4's own results. Some parts are its own:
the exact solver, which calculates the same posterior directly rather than by sampling, and parts
of the geographic analysis, such as the search for a dispersal origin and fitting each dispersal
to every posterior draw. Appendix A of the [user manual](frontend/src/content/USER_MANUAL.md#appendix-a-mathematical-methods)
sets out how each number is computed and which source, if any, it draws on.

- Svetlik, I., et al. (2019). The best possible time resolution: how precise could a radiocarbon dating method be? Radiocarbon, 61(6).
- Reimer, P. J., et al. (2020). IntCal20 Northern Hemisphere radiocarbon age calibration curve. Radiocarbon, 62(4).
- Bronk Ramsey, C. (2009). Bayesian analysis of radiocarbon dates. Radiocarbon, 51(1).
- Bronk Ramsey, C. (1995). Radiocarbon calibration and analysis of stratigraphy: the OxCal program. Radiocarbon, 37(2).
- Buck, C. E., et al. (1996). Bayesian Approach to Interpreting Archaeological Data.
- Heaton, T. J., et al. (2020). Marine20—the marine radiocarbon age calibration curve. Radiocarbon, 62(4).
- Nicholls, G., and Jones, M. (2001). Radiocarbon dating with temporal order constraints. Applied Statistics, 50(4).
- Bronk Ramsey, C. (2008). Deposition models for chronological records. Quaternary Science Reviews, 27(1-2).
- Galimberti, M., Bronk Ramsey, C., and Manning, S. W. (2004). Wiggle-match dating of tree-ring sequences. Radiocarbon, 46(2).
- Ammerman, A. J., and Cavalli-Sforza, L. L. (1971). Measuring the rate of spread of early farming in Europe. Man, 6(4).

