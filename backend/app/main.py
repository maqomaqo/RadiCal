from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import os
from app.api import (auth, curves, analysis, results, projects, project_samples,
                     imports, jobs, reservoir, dispersal, run_history, influence)
from app.database import init_db

load_dotenv()

# The schema and the docs live under /api with everything else, so they pass
# through any proxy that forwards /api -- the development server's, or a
# deployment's -- and a client can read the schema from the same base URL it
# calls. The old root paths redirect, below.
app = FastAPI(
    title="RadiCal API",
    description="Bayesian analysis of radiocarbon dates",
    version="0.1.0",
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
)


@app.get("/docs", include_in_schema=False)
def _old_docs():
    return RedirectResponse("/api/docs", status_code=308)


@app.get("/redoc", include_in_schema=False)
def _old_redoc():
    return RedirectResponse("/api/redoc", status_code=308)


@app.get("/openapi.json", include_in_schema=False)
def _old_schema():
    return RedirectResponse("/api/openapi.json", status_code=308)

# CORS Configuration
origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Compression, for everything: the built frontend (Plotly alone is 4.5 MB
# and 1.3 MB gzipped), the offline basemap's topojson (1.7 MB, 0.6 MB), and
# the API's own JSON, where a results payload is mostly arrays of numbers
# that shrink severalfold. Level 6 rather than Starlette's default 9: on the
# Plotly chunk it is within 0.3% of the size for under two thirds of the CPU,
# and this runs per response. Below a kilobyte the header costs more than it
# saves.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

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

# API Routes.
#
# auth first, because everything below it depends on the current_user it
# defines: registering and signing in are the only endpoints that do not
# require an account, and /api/health below is the only other thing reachable
# without one.
app.include_router(auth.router, prefix="/api", tags=["authentication"])
app.include_router(curves.router, prefix="/api", tags=["calibration curves"])
app.include_router(analysis.router, prefix="/api", tags=["analysis"])
app.include_router(results.router, prefix="/api", tags=["results"])
app.include_router(projects.router, prefix="/api", tags=["projects"])
app.include_router(project_samples.router, prefix="/api", tags=["project dates"])
app.include_router(imports.router, prefix="/api", tags=["import"])
app.include_router(jobs.router, prefix="/api", tags=["jobs"])
app.include_router(reservoir.router, prefix="/api", tags=["marine reservoir"])
app.include_router(dispersal.router, prefix="/api", tags=["dispersal"])
app.include_router(run_history.router, prefix="/api", tags=["run history"])
app.include_router(influence.router, prefix="/api", tags=["influence"])

# A deployment signing tokens with the published development secret is a
# deployment where anyone holding this source can mint a token for any
# account. services/auth.py refuses to start in that state when RADICAL_ENV
# says this is not development; here, where it is allowed, it is at least said
# out loud rather than passing silently.
try:
    from app.services import auth as _auth_service
    if _auth_service.using_the_development_secret():
        print(
            "⚠ RADICAL_SECRET_KEY is not set, so login tokens are signed with "
            "the built-in development key. Fine locally; set a real one before "
            "this is reachable by anyone else."
        )
except RuntimeError:
    # secret_key() refusing: RADICAL_ENV says this is a real deployment and
    # no key is set. Stopping here, with that message as the last thing in
    # the log, beats a container that reports healthy and then answers every
    # sign-in with a 500.
    raise
except Exception as e:
    print(f"⚠ Warning: could not check the signing key: {e}")


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "version": "0.1.0"}

# The built frontend, when this is the published image (the root Dockerfile
# puts `vite build`'s output at /app/static). Registered last so every /api
# route, and FastAPI's own /api/docs and /api/openapi.json, are matched first; the
# catch-all then hands any path the frontend owns to index.html, which is
# what BrowserRouter needs for a deep link or a reload on /projects/42. In
# the development stack there is no static/ directory, the frontend is Vite's
# dev server, and "/" stays the API's own front page.
STATIC_DIR = os.path.abspath(
    os.getenv("RADICAL_STATIC_DIR",
              os.path.join(os.path.dirname(os.path.dirname(__file__)), "static"))
)

class _HashedAssets(StaticFiles):
    """
    /assets, cached for good. Every file vite writes there carries a hash of
    its content in its name, so a given URL can never serve different bytes:
    a browser that has it need not ask again, and a release that changes a
    file changes its name, which index.html (never cached, below) points to.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


if os.path.isdir(STATIC_DIR):
    app.mount("/assets", _HashedAssets(directory=os.path.join(STATIC_DIR, "assets")),
              name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        candidate = os.path.abspath(os.path.join(STATIC_DIR, path))
        # abspath resolved any ".."; anything that escaped STATIC_DIR is
        # simply not a file the frontend has, so it gets the app shell.
        if (candidate.startswith(STATIC_DIR + os.sep)
                and os.path.isfile(candidate)):
            # public/ files keep their names across releases (the logo, the
            # basemap geometry), so they get a day rather than forever.
            return FileResponse(candidate,
                                headers={"Cache-Control": "public, max-age=86400"})
        # The shell is what names the current release's hashed assets, so it
        # is revalidated every time: a cached copy would keep loading the
        # previous release's chunks after a deploy.
        return FileResponse(os.path.join(STATIC_DIR, "index.html"),
                            headers={"Cache-Control": "no-cache"})
else:
    @app.get("/")
    async def root():
        return {
            "message": "RadiCal API",
            "docs": "/api/docs",
            "health": "/api/health"
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
