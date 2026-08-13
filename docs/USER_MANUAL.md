# User Manual

## Table of Contents

1. [Getting Started](#getting-started)
2. [Creating an Analysis](#creating-an-analysis)
3. [Adding Measurements](#adding-measurements)
4. [Model Structure (Bayesian only)](#model-structure-bayesian-only)
5. [Running an Analysis](#running-an-analysis)
6. [Viewing Results](#viewing-results)
7. [Exporting Data](#exporting-data)
8. [Projects and Date Collections](#projects-and-date-collections)
9. [Best Practices](#best-practices)

## Getting Started

1. Navigate to `http://localhost:5173` (or your deployment URL).
2. Click **New Analysis** (top navigation bar, or from the Dashboard).

There is no multi-step wizard — New Analysis is a single form: settings on the left, adding
measurements on the right, the measurement list and (for a Bayesian analysis) the model structure
below, and a Run Analysis button at the bottom.

## Creating an Analysis

- **Analysis Name** — required.
- **Analysis Type**:
  - **Single Calibration** — one date, calibrated on its own. Has an extra "Save this analysis"
    toggle: switch it off to run a quick test (the result is shown right there on the page) without
    creating anything in a project or the analysis list.
  - **Group Calibration** — several dates, each calibrated independently. No stratigraphy is
    applied; this is the direct calibration of every date in the group, not a Bayesian model.
  - **Bayesian** — one or more dates modelled together with stratigraphic constraints. See
    [Model Structure](#model-structure-bayesian-only) below.
- **Project** — optional. Assigning a project makes that project's own date collection available to
  pull from (see [Projects and Date Collections](#projects-and-date-collections)), and adds this
  analysis to the project's list.
- **Result Date Format** — how calendar ages are displayed (e.g. cal BP, BC/AD). This only changes
  the labelling; the underlying calibration is unaffected.

## Adding Measurements

Each date needs, at minimum, a **Sample Name**, **Age (BP)** and **1σ Error**. Also available:

- **Material** — free text (charcoal, bone, shell, etc.).
- **Sample Type** — Terrestrial or Marine. Marine samples get an extra **ΔR** (local reservoir
  offset) field with its own 1σ error — both are required together, since they're combined in
  quadrature with the measurement's own uncertainty.
- **Calibration Curve** — IntCal20 (Northern Hemisphere terrestrial), SHCal20 (Southern Hemisphere
  terrestrial), or Marine20.
- **Outlier settings** (Bayesian analyses only) — an optional prior probability that this date does
  not belong to the modelled population, with a choice of outlier model (general vs. charcoal,
  i.e. one-sided for material likely to predate its context).

If a project is assigned and its date collection already has dates in it, an **Add from Project's
Date Collection** button appears above the measurement form — pick any subset to copy into this
analysis instead of retyping them.

## Model Structure (Bayesian only)

Optional, but this is what makes a Bayesian analysis different from a Group Calibration: build the
model as nested Sequences and Phases with Boundaries placed where you want them (the OxCal idiom),
then assign each date to a Phase. TPQ (terminus post quem — a maximum age) and TAQ (terminus ante
quem — a minimum age) limiters can be set on a date or on a whole Phase.

Reached via **Specify Model Structure** / **Edit Model Structure**, which has two ways to build the
same tree: a drag-and-drop Harris matrix diagram, and a List Editor for the same structure as plain
controls. The Results page later renders this same tree read-only as a diagram, and the Posterior
Distributions plots follow its order.

**Model Settings**, on the New Analysis form itself:
- **Solver** — Automatic (recommended: exact integration where the model allows it, sampling
  otherwise), Exact solver only (fails rather than silently sampling), or MH/Gibbs (forces OxCal's
  own sampling method throughout — for reproducing a published OxCal analysis).
- **Posterior draws** — how many samples each date's posterior is reported with.
- **Uniform span prior** — OxCal's `UniformSpanPrior` correction (on by default), which keeps a
  sequence's overall span prior independent of how finely it's subdivided. Only affects a sequence
  with three or more Boundaries.

## Running an Analysis

**Run Analysis** submits the form. A saved analysis (Group Calibration, Bayesian, or Single
Calibration with "Save this analysis" on) navigates straight to its Results page.

## Viewing Results

- **Results Summary** — one row per date (and, for a Bayesian model, per Boundary), in the model's
  own structure order. Columns: the date's unconstrained ("unmodelled") calibration, its modelled
  posterior HPD range at the confidence you've selected, and diagnostics.
- **HPD Confidence** — 68.2%, 95.4%, or 99.7% (the Gaussian-equivalent 1/2/3-sigma conventions).
- **Show Mean / Show Median** — optional point estimates alongside the HPD ranges.
- Any date whose diagnostics are worth a second look (poor agreement with its own unconstrained
  calibration, or a high outlier probability) is called out in a banner above the table.
- **Model Structure** — a summary (phase and ordering-relationship counts) and, for a Bayesian
  analysis, the read-only Harris matrix diagram of the constraints actually used.
- **Posterior Distributions** — Plotly charts, oldest first:
  - **Stacked** (default) — every date (and Boundary) as one row each, sharing a single calendar
    axis, so overlap between dates is directly visible. Each row is scaled to its own peak, so
    heights compare precision, not probability.
  - **Individual** — one full-size plot per date/Boundary.
  - **Show boundary plots** — hide/show Boundary rows separately from dates.
  - **Show prior (unmodelled) distribution** — off by default; overlays each date's plain
    calibration (no stratigraphic constraints) as a grey shadow behind its modelled posterior, so
    you can see exactly how far the model moved it.

## Exporting Data

- **Export CSV** (on the Results page) — the full results table, including the unmodelled
  comparison, in the currently-selected date format.
- **JSON** — available via the API (`GET /api/results/{id}/export/json`) for anyone who wants the
  full result machine-readable; there's no button for it in the UI yet.
- **PDF** — not implemented. `GET /api/results/{id}/pdf` exists and returns a `501` rather than a
  fake success, so this is a deliberate gap, not a bug, and there's nothing to click for it in the
  UI.

## Projects and Date Collections

A **Project** groups analyses, and can also hold its own collection of dates — entered one at a
time or pasted in bulk (`name, age, error[, material[, context[, curve]]]` per line) — independent
of any particular analysis. Deploying a selection of them copies the values into a new analysis, so
editing the collection afterwards never changes an analysis already created from it, and the same
dates can be deployed more than once to model them differently each time.

From a project page you can also re-run every analysis in it at once, or re-run, duplicate, or edit
the inputs of a single analysis (editing replaces its saved inputs and re-runs it in place, keeping
the same result page).

## Best Practices

1. **Sample data** — use consistent units (age always in years BP), and double-check stratigraphic
   order before building a model structure around it.
2. **Calibration curves** — IntCal20 for Northern Hemisphere terrestrial samples, SHCal20 for
   Southern Hemisphere terrestrial, Marine20 for marine samples. Use the most recent curve version.
3. **Marine corrections** — consult the literature for an appropriate local ΔR; document its source
   in the sample's notes.
4. **Reading diagnostics** — the agreement index compares a date's modelled posterior against its
   own unconstrained calibration (100 = the model left it alone; below ~60 is conventionally worth a
   second look). It flags a date the model had to move, which is not automatically the same as a
   date that's simply wrong — see the discussion in `backend/app/services/agreement.py` if you're
   using it to hunt for outliers specifically.
5. **Wide Boundaries on a short phase are expected** — a Phase's Start/End Boundaries genuinely need
   four or more dated members before their span is even mathematically well-constrained; two or
   three dates in a phase can produce a very wide 95% range and that's correct, not a bug.
