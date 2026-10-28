# The published RadiCal image: ghcr.io/maqomaqo/radical
#
# RadiCal on a server: radical-server (desktop/crates/radical-server) in
# accounts mode, serving the built frontend from /app/static, so the
# frontend's same-origin `fetch('/api/...')` calls need no proxy in front.
# Its data is one SQLite file on the /data volume, and the calibration curves
# are compiled into the binary. Nothing else is needed: no database server.
#
# Built for the platform of the machine building it. The publishing workflow
# (.github/workflows/publish-image.yml) builds amd64 and arm64 on runners of
# each kind and joins them, because a Rust build under emulation takes hours.
#
# The development stack (docker-compose.yml) runs this image's server without
# its built frontend, beside frontend/'s hot-reloading dev server.

# ---- Frontend build -------------------------------------------------------
# Pinned to the build host's platform: the output is plain JS/CSS, identical
# for every target, and tsc + terser under QEMU emulation is painfully slow.
FROM --platform=$BUILDPLATFORM node:24-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# The bundle is ~6 MB before minification, with source maps; terser over that
# blows through node's default heap on a GitHub runner, which is shared with
# the arm64 stage building alongside. Local builds only pass because a laptop
# gives node a bigger default.
ENV NODE_OPTIONS=--max-old-space-size=4096
RUN npm run build

# ---- Server ---------------------------------------------------------------
# desktop/rust-toolchain.toml pins the compiler; rustup installs that one.
FROM rust:1-bookworm AS server
WORKDIR /build
COPY calibration_data/ calibration_data/
COPY desktop/ desktop/
WORKDIR /build/desktop
RUN cargo build --release --locked -p radical-server \
    && cp target/release/radical-server /radical-server

# ---- Runtime --------------------------------------------------------------
FROM debian:bookworm-slim AS runtime
# Certificates for the two things it reaches out to: the mail server for
# password-reset emails, and calib.org for ΔR snapshots.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /data radical \
    && mkdir -p /data && chown radical /data

COPY --from=server /radical-server /usr/local/bin/radical-server
COPY --from=frontend /build/dist /app/static

# Everything RadiCal keeps is in /data/radical.db: mount a volume here.
VOLUME ["/data"]
USER radical

ENV RADICAL_DB=/data/radical.db \
    RADICAL_ENV=production

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["radical-server", "health"]

# `docker compose run --rm radical reset-password EMAIL` and
# `... import-postgres` replace the arguments below.
ENTRYPOINT ["radical-server"]
CMD ["--accounts", "--host", "0.0.0.0", "--port", "8000", "--frontend", "/app/static"]
