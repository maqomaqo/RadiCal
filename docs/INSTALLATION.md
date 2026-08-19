# Installation Guide

## Prerequisites

- Docker and Docker Compose (recommended)
- Node.js 18+ (for local development)
- Python 3.11+ (for local development)
- PostgreSQL 14+ (for local development without Docker)

## Quick Start with Docker

```bash
# Clone repository
git clone https://github.com/maqomaqo/RadiCal.git
cd RadiCal

# Copy environment template
cp .env.example .env

# Build and start containers
docker-compose up -d

# Wait for services to be ready (about 30-60 seconds)
# Access the webapp at http://localhost:5173
# API documentation at http://localhost:8000/docs
```

## Local Development

### Frontend

```bash
cd frontend
npm install
npm run dev
# Open http://localhost:5173
```

### Backend

```bash
cd backend
python -m venv venv

# On Linux/Mac:
source venv/bin/activate

# On Windows:
venv\Scripts\activate

pip install -r requirements.txt
uvicorn app.main:app --reload
# API available at http://localhost:8000
# Docs at http://localhost:8000/docs
```

### Database Setup (if not using Docker)

```bash
# Create PostgreSQL database
createdb radiocarbon_db

# Create user
createuser -P radiocarbon  # password: password

# Grant privileges
psql -c "ALTER ROLE radiocarbon WITH CREATEDB;"
```

Create the tables by starting the backend — it brings the database up to the
current schema on startup, whether that means creating it from nothing or
applying migrations added since the last run. There is no separate setup step.

### Schema changes

The schema is versioned with Alembic (`backend/alembic/`). Day to day this
needs no attention: the backend migrates on startup.

Two cases where it does.

**Upgrading an existing installation.** Pull, rebuild so the backend has the
new dependencies, and start it — it migrates on the way up:

```bash
docker compose up -d --build backend
```

A database created before Alembic was adopted (any version before 2026-08-19)
is recognised on that first start, brought up to date, and joined to the
migration history automatically. Nothing to do by hand, and existing analyses
are kept.

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

## Stopping Services

```bash
# With Docker Compose
docker-compose down

# Keep database (don't delete volumes)
docker-compose down

# Remove database volumes too
docker-compose down -v
```

## Troubleshooting

### Port Already in Use
- Frontend: 5173
- Backend: 8000
- Database: 5433 on the host (mapped to Postgres's own 5432 inside the container)

Change ports in `docker-compose.yml` if conflicts occur.

### Database Connection Issues
Verify PostgreSQL is running and credentials in `.env` match.

### CORS Issues
Update `CORS_ORIGINS` in `.env` to include your frontend URL.

## Environment Variables

Key variables in `.env` (see `.env.example` for the full, current list):
- `VITE_API_URL` - Backend API URL the frontend calls
- `DATABASE_URL` - PostgreSQL connection string
- `CORS_ORIGINS` - Origins the backend accepts requests from
- `INTCAL_CURVE_PATH` / `MARINE_CURVE_PATH` / `SHCAL_CURVE_PATH` - Calibration curve file locations;
  only needed if running the backend outside Docker with the data somewhere other than
  `calibration_data/`
- `RADICAL_SKIP_MIGRATIONS` - set to `1` to stop the backend migrating the database on startup
  (see Schema changes above). Unset by default, which is what a local install wants.

MCMC/Bayesian settings (posterior draws, solver choice, the uniform span prior) are configured
per-analysis in the New Analysis form, not via environment variables — the app used to read
`MCMC_SAMPLES`/`MCMC_TUNE`/`MCMC_CHAINS` from the environment, but that was tied to a PyMC-based
sampler that has since been replaced (see `REMAINING_WORK.md`).
