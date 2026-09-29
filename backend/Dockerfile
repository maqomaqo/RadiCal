FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    gfortran \
    libopenblas-dev \
    liblapack-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Test dependencies, off by default so a production build stays lean; the dev
# compose stack sets it. They are installed FROM THE PINNED FILE rather than by
# hand, because the pins are load-bearing: fastapi's TestClient runs through
# httpx, and an httpx release that drifts from the pinned starlette can take
# every API test out at once. An ad-hoc `pip install pytest httpx` in a running
# container is exactly how this one drifted once already.
ARG INSTALL_DEV=false
COPY requirements-dev.txt .
RUN if [ "$INSTALL_DEV" = "true" ]; then pip install --no-cache-dir -r requirements-dev.txt; fi

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
