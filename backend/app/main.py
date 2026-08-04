from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os
from app.api import curves, analysis, harris_matrix, results, projects
from app.database import init_db

load_dotenv()

app = FastAPI(
    title="RadiCal API",
    description="Bayesian analysis of radiocarbon dates",
    version="0.1.0"
)

# CORS Configuration
origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
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

# API Routes
app.include_router(curves.router, prefix="/api", tags=["calibration curves"])
app.include_router(analysis.router, prefix="/api", tags=["analysis"])
app.include_router(harris_matrix.router, prefix="/api", tags=["harris matrix"])
app.include_router(results.router, prefix="/api", tags=["results"])
app.include_router(projects.router, prefix="/api", tags=["projects"])

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
