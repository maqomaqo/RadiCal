# Installation Guide

## Prerequisites

- Docker and Docker Compose (recommended)
- Node.js 18+ (for local development)
- Python 3.11+ (for local development)
- PostgreSQL 14+ (for local development without Docker)

## Quick Start with Docker

```bash
# Clone repository
git clone https://github.com/maqomaqo/radiocarbon-calibration-webapp.git
cd radiocarbon-calibration-webapp

# Copy environment template
cp .env.example .env

# Build and start containers
docker-compose up -d

# Wait for services to be ready (about 30-60 seconds)
# Access the webapp at http://localhost:3000
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
- Frontend: 3000, 5173
- Backend: 8000  
- Database: 5432

Change ports in `docker-compose.yml` or `.env` if conflicts occur.

### Database Connection Issues
Verify PostgreSQL is running and credentials in `.env` match.

### CORS Issues
Update `CORS_ORIGINS` in `.env` to include your frontend URL.

## Environment Variables

Key variables in `.env`:
- `VITE_API_URL` - Backend API URL for frontend
- `DATABASE_URL` - PostgreSQL connection string
- `MCMC_SAMPLES` - Number of MCMC samples for Bayesian analysis
- `MCMC_TUNE` - Burn-in iterations
- `MCMC_CHAINS` - Number of parallel chains
