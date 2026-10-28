# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Mixed audience: academic/research archaeologists and geoscientists doing radiocarbon dating for
publications, fieldwork, and chronological analysis, alongside students and learners working
through radiocarbon calibration for the first time. Design and copy must serve both without
dumbing the tool down for the former or overwhelming the latter — progressive disclosure over a
lowest-common-denominator UI.

## Product Purpose

A tool for radiocarbon dating analysis, as a desktop app or on a shared server in the browser: calibration against IntCal20/Marine20/SHCal20,
Bayesian chronological modelling with stratigraphic constraints (OxCal-style Sequences, Phases,
Boundaries, Hiatuses), and interactive posterior visualization. Success is a calendar-date estimate
a user can trust and cite — correct, reproducible, and traceable to the curve/solver/settings that
produced it.

## Positioning

Standalone identity — not "OxCal in a browser" and not defined by OxCal, even though its solvers
were validated against real OxCal 4.4 runs for correctness (by the Python server's suite,
`test_oxcal_agreement.py`, `test_exact_chain.py` and `test_gibbs.py`, at tag `v2.1.0`; the Rust
server is held to that server's recorded answers in `desktop/corpus`). The mechanism
a neighboring tool can't casually copy:

- A single curve registry (`desktop/crates/radical-core/src/curve_registry.rs`) that *measures* every curve
  it loads (points, range, calendar grid, content hash) rather than hardcoding facts about them, so
  the curve pickers, CSV importer, and OxCal importer can't drift apart on what a curve is.
- Two from-scratch Bayesian solvers (exact integration for chain-shaped models, OxCal's own
  MH/Gibbs scheme otherwise) with no PyMC/MCMC sampler dependency.
- Interactive curve browser and expected-resolution profiling (Svetlik et al. 2019) — seeing what a
  plateau costs before commissioning a date — which OxCal itself doesn't offer.
- Mixed-curve support (`Mix_Curves`) for multi-reservoir samples, imported from or exported to OxCal
  files.
- Reusable per-project date collections that can be redeployed into any number of analyses.

OxCal-file interoperability (import/export) and OxCal-validated solver correctness are real
technical facts to state plainly; they are not the positioning story.

## Operating Context

Creating an analysis (single calibration, group calibration, or full Bayesian model); importing
dates from CSV or an OxCal `.oxcal` file; building a model tree of nested Sequences/Phases with
Boundaries and Hiatuses; assigning per-date outlier settings; running a solver; reading posterior
plots, the Harris matrix diagram, and diagnostics (agreement index, outlier posterior); exporting
CSV/JSON/PDF; grouping analyses under a project and re-running them in bulk.

## Capabilities and Constraints

- Three analysis types: single calibration, group calibration, Bayesian modelling.
- Three shipped curves (IntCal20, Marine20, SHCal20) plus user-imported and user-mixed curves, all
  flowing through the one curve registry.
- Imports are all-or-nothing and previewed before anything is stored; an OxCal construct the app
  doesn't model fails the import by name rather than being approximated.
- CSV/JSON/PDF exports for one analysis are assembled from one source, so the three cannot disagree.
- Domain terminology (BP, cal BP/BC/AD, HPD, ΔR, TPQ/TAQ, agreement index) is precise and
  OxCal-derived — do not soften, rename, or simplify it for a general audience; the mixed audience
  is served by disclosure and explanation, not by diluting the vocabulary itself.
- No accessibility standard has been established as a hard requirement (open — see Accessibility
  section).

## Brand Commitments

- Existing name: RadiCal.
- Existing logo and favicon (`frontend/public/RadiCal.png`, `frontend/public/favicon.png`).
- Existing MUI theme with a deliberate, non-default palette: a primary blue for the app's
  structural/brand color, plus a small dedicated diagram palette (`frontend/src/theme/diagramColors.ts`)
  — coral for Boundary, green for Phase, violet for Hiatus — kept intentionally separate from MUI's
  semantic success/warning colors so the model-tree diagrams don't read as a status display.

## Evidence on Hand

No real testimonials, customer quotes, or case studies exist and none should be fabricated. The one
piece of real evidence on hand is technical, not promotional: the Bayesian solvers have been
validated against real OxCal 4.4 runs, by the Python server's suite (`test_oxcal_agreement.py`,
`test_exact_chain.py`, `test_gibbs.py`, at tag `v2.1.0`), and the Rust server is held to that
server's recorded answers in `desktop/corpus` — describe this accurately
as validation evidence, not dressed up as a marketing claim.

## Product Principles

- Numerical and scientific correctness outranks cosmetic simplicity; validated-against-OxCal
  behavior is never traded away for a cleaner-looking shortcut.
- One source of truth per fact (the curve registry, the single export assembly) so two views of the
  same thing can never quietly disagree.
- Fail loud and specific on anything the app doesn't model, rather than silently approximating.
- Serve experts and learners at once through progressive disclosure, not through diluted
  terminology or a dumbed-down default view.
- RadiCal earns its own identity through its own feature set (curve registry, live curve browser,
  resolution profiling, mixed curves, reusable date collections) — OxCal compatibility is a fact
  about it, not its pitch.

## Accessibility & Inclusion

No product-specific accessibility standard has been established. Open decision — record any
requirement here if one is confirmed later.
