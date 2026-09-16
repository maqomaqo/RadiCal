# The published RadiCal image: ghcr.io/maqomaqo/radical
#
# One container serving both halves of the app. The frontend is built to
# static files and served by the backend from /app/static (see the tail of
# app/main.py), so the same-origin `fetch('/api/...')` calls the frontend
# makes work without a proxy in front, and the calibration curves are baked
# in at /app/data where the backend looks for them by default. The only thing
# it needs from outside is a PostgreSQL database, via DATABASE_URL.
#
# The per-service Dockerfiles under frontend/ and backend/ remain the
# development stack: docker-compose.yml bind-mounts source into those and
# hot-reloads. This one is built by .github/workflows/publish-image.yml.

# ---- Frontend build -------------------------------------------------------
# Pinned to the build host's platform: the output is plain JS/CSS, identical
# for every target, and tsc + terser under QEMU emulation is painfully slow.
FROM --platform=$BUILDPLATFORM node:18-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Backend dependencies -------------------------------------------------
# Every pin in requirements.txt ships a wheel for both amd64 and arm64, so the
# toolchain is insurance against a future pin that does not; keeping it in a
# separate stage means the runtime image never carries it either way.
FROM python:3.11-slim AS backend-deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ gfortran libopenblas-dev liblapack-dev \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt

# ---- Runtime --------------------------------------------------------------
FROM python:3.11-slim
WORKDIR /app

COPY --from=backend-deps /install /usr/local
COPY backend/ ./
COPY calibration_data/ ./data/
COPY --from=frontend /build/dist ./static/

# Nothing here is written except uploads; mount a volume there to keep them.
VOLUME ["/app/uploads"]

ENV PYTHONUNBUFFERED=1 \
    RADICAL_ENV=production

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
