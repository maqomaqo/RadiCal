# RadiCal Desktop

The Rust build of RadiCal: RadiCal Desktop, and `radical-server`, which RadiCal on a server runs
too. How to release the desktop app is in [`docs/RELEASING_DESKTOP.md`](../docs/RELEASING_DESKTOP.md).

```
desktop/
  Cargo.toml            the workspace
  rust-toolchain.toml   the pinned compiler
  crates/
    radical-core/       the calibration maths, the exact solvers and the sampler
    radical-io/         file formats: date tables, curve files, OxCal models
    radical-store/      the database, one SQLite file
    radical-jobs/       runs that happen outside the request that asked for them
    radical-server/     the HTTP API the frontend talks to
    radical-desktop/    the desktop app: the server and the frontend in one window
    radical-corpus/     reads the golden corpus and checks it is intact
  bench/
    models.json         the models the sampler benchmark runs
  corpus/
    manifest.json       every corpus file's SHA-256, and the curves it was made against
    data/units/         one service function per case
    data/api/           whole API requests and responses: solver and server targets
    inputs/             the inputs some families were made from
    fixtures/           OxCal models, OxCal's own results for them, and captured pages
    ziggurat_tables.py  writes NumPy's normal-draw tables as Rust source
    html_entities.py    writes Python's HTML entity tables as Rust source
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

`cargo test -p radical-core --test models` runs every model in `data/api/models.json` through the
same pipeline the API ran it through and compares each number it stored. The sampled models
reproduce Python's runs draw for draw, except where noted in `tests/models/sampled.rs`.

`cargo run --release -p radical-core --example sampler_bench` times the sampler on the models in
`bench/models.json`.

`cargo test -p radical-core --test oxcal` runs the fixture models in `corpus/fixtures/oxcal*/`
and compares the answers with OxCal 4.4's own results for the same runs: calibrated ranges,
modelled dates and Boundaries, outlier probabilities and scales, and agreement indices.

`cargo test -p radical-core --test units -- --nocapture` also prints, for each corpus family, the
largest relative difference from the Python answer and where it was.

## The golden corpus

The corpus records what the earlier Python server answered, and the Rust code is held to it. The
Python server was removed after v2.1.0; the code that generated the corpus, and the Python files
that comments in this workspace name (`backend/...`), are in the repository at tag `v2.1.0`.

The corpus is no longer regenerated. When behaviour changes on purpose, check the change against
OxCal first (`--test oxcal`), then edit the cases it changes and put the file's new SHA-256
(`shasum -a 256 FILE`) in `manifest.json`, so the corpus diff records what changed. New tests are
written in Rust.

`radical-corpus`'s tests fail when a corpus file no longer matches the manifest. They also fail
when the curves in `calibration_data/` differ from the ones the corpus was generated against.
