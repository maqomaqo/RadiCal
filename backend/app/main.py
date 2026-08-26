from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os
from app.api import curves, analysis, results, projects, project_samples, imports, jobs
from app.database import init_db

load_dotenv()

app = FastAPI(
    title="RadiCal API",
    description="Bayesian analysis of radiocarbon dates",
    version="0.1.0"
)

# CORS Configuration
origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize database
try:
    init_db()
    print("✓ Database initialized successfully")
except Exception as e:
    print(f"⚠ Warning: Database initialization failed: {e}")

# The job queue is an in-process thread pool, not a broker (services/jobs.py),
# so a row still reading 'queued' or 'running' at startup describes work that
# will never finish and would be polled forever. This does not save the work,
# it stops the app lying about it.
try:
    from app.services.jobs import reconcile_orphans
    _orphans = reconcile_orphans()
    if _orphans:
        print(f"✓ Failed {_orphans} job(s) left mid-flight by a restart")
except Exception as e:
    print(f"⚠ Warning: could not reconcile interrupted jobs: {e}")

# API Routes
app.include_router(curves.router, prefix="/api", tags=["calibration curves"])
app.include_router(analysis.router, prefix="/api", tags=["analysis"])
app.include_router(results.router, prefix="/api", tags=["results"])
app.include_router(projects.router, prefix="/api", tags=["projects"])
app.include_router(project_samples.router, prefix="/api", tags=["project dates"])
app.include_router(imports.router, prefix="/api", tags=["import"])
app.include_router(jobs.router, prefix="/api", tags=["jobs"])

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "version": "0.1.0"}

@app.get("/")
async def root():
    return {
        "message": "RadiCal API",
        "docs": "/docs",
        "health": "/api/health"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
