# RadiCal Desktop

The standalone Rust build of RadiCal. [`docs/RUST_DESKTOP_PLAN.md`](../docs/RUST_DESKTOP_PLAN.md)
describes what it will be and the order it is built in. This folder holds the work so far.

```
desktop/
  Cargo.toml            the workspace; crates are added as the phase that needs them starts
  rust-toolchain.toml   the pinned compiler
  crates/
    radical-core/       the calibration maths and the exact solvers, ported from
                        backend/app/services (Phases 1 and 2)
    radical-corpus/     reads the golden corpus and checks it is intact
  corpus/
    generate.py         writes the corpus from the Python implementation
    manifest.json       every corpus file's SHA-256, and the curves it was made against
    data/units/         one service function per case: the Phase 1 and 2 targets
    data/api/           whole API requests and responses: solver and server targets
```

## Building and testing

The Rust toolchain comes from [rustup](https://rustup.rs). The pinned version installs itself
the first time `cargo` runs here.

```bash
cd desktop
cargo test
cargo clippy --all-targets -- -D warnings
```

CI runs the same on macOS, Windows and Linux (`.github/workflows/desktop.yml`).

`cargo test -p radical-core --test models` runs every model the API solved without sampling
(`data/api/models.json`) through the same pipeline and compares each number it stored.

`cargo test -p radical-core --test units -- --nocapture` also prints, for each corpus family, the
largest relative difference from the Python answer and where it was.

## The golden corpus

The corpus records what the Python implementation answers today. The Rust port is checked
against it. The format is in `corpus/generate.py`'s docstring.

Regenerate it only when the Python behaviour changes on purpose. Then commit the result, so the
corpus diff records what changed. Run it with the backend's pinned dependencies:

```bash
cd backend
pip install -r requirements-dev.txt
python ../desktop/corpus/generate.py            # everything, a few minutes
python ../desktop/corpus/generate.py --units-only   # the unit cases only, seconds
```

`radical-corpus`'s tests fail when a corpus file no longer matches the manifest. They also fail
when the curves in `calibration_data/` differ from the ones the corpus was generated against.
