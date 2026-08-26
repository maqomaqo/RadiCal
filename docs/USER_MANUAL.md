# User Manual

## Table of Contents

1. [Getting Started](#getting-started)
2. [Creating an Analysis](#creating-an-analysis)
3. [Adding Measurements](#adding-measurements)
4. [Importing Dates from a File](#importing-dates-from-a-file)
5. [Calibration Curves](#calibration-curves)
6. [Model Structure (Bayesian only)](#model-structure-bayesian-only)
7. [Running an Analysis](#running-an-analysis)
8. [Viewing Results](#viewing-results)
9. [Durations and Differences](#durations-and-differences)
10. [Exporting Data](#exporting-data)
11. [Projects and Date Collections](#projects-and-date-collections)
12. [Best Practices](#best-practices)

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
  terrestrial), Marine20, and any curve you have imported or mixed yourself. Selecting Marine
  switches to a marine curve, since marine carbon calibrated against a terrestrial curve is wrong
  by centuries. See [Calibration Curves](#calibration-curves).
- **Outlier settings** (Bayesian analyses only) — an optional prior probability that this date does
  not belong to the modelled population, with a choice of outlier model (general vs. charcoal,
  i.e. one-sided for material likely to predate its context).

If a project is assigned and its date collection already has dates in it, an **Add from Project's
Date Collection** button appears above the measurement form — pick any subset to copy into this
analysis instead of retyping them.

## Importing Dates from a File

**Import from a File** (on New Analysis, and **Import File** on a project's Dates tab) reads a
spreadsheet of dates or an OxCal model. Nothing is saved until you have seen what was read: the
file is parsed, the dates and any model structure are shown for review, and only then does
confirming put them where hand-typed dates go.

An import is **all or nothing**. One unreadable row fails the whole file and lists every problem
found, rather than importing what it could and leaving you to work out which dates are missing.

### CSV

One date per row with a header row naming the columns. Only `sample_name`, `age_bp` and
`error_1sigma` are required; everything else takes the same default a typed-in date would.
Column **order does not matter** and common spellings are understood — `Lab Code`, `Age BP`,
`± 1σ`, `sd`, `Material`, `Curve`, `Context` all resolve to the right field. Comma, tab and
semicolon separators are all read.

Use **Download template** in the dialog for a file with the full column list and three worked
example rows. The optional columns are `material_type`, `curve_id`, `sample_type`, `delta_r`,
`delta_r_error`, `phase`, `tpq_age_bp`, `taq_age_bp`, `outlier_model`, `outlier_prior`, `depth_cm`,
`location` and `notes`.

A `phase` column is what turns a list of dates into a model: dates sharing a phase name are
grouped, and a starting structure is built around them — one Start/End pair with the phases
**unordered** inside it, since a CSV says which dates go together but nothing about which group
came first. Ordering them is then a decision you make in the Model Structure editor.

The app's own results export is readable back in, so exporting a table, correcting it in a
spreadsheet and re-importing works. Its structural rows and its posterior columns are recognised
and skipped rather than read back as input.

### OxCal (.oxcal)

An OxCal model file is read directly — its `Sequence`, `Phase`, `Boundary` and `Hiatus` structure,
per-scope `Curve` and `Delta_R`, and per-date `Outlier` settings all come across, keeping the names
OxCal gave them. Importing one from New Analysis brings the model structure with it.

A reservoir offset can be written either way OxCal allows: in full, `Delta_R("LocalMarine",-162,28)`,
or stated once under a name and cited wherever it applies, `Delta_R("=LocalMarine", )`. The offset
belongs to the curve it was stated for — OxCal applies `Delta_R` to the current `Curve` — so a phase
holding marine dates on Marine20 and terrestrial ones on SHCal20 gives the offset to the marine
dates only. The preview shows the ΔR carried by each date, and a note says how many dates each named
offset reached.

OxCal's language is larger than what this app models. Anything that would change the posterior and
cannot be honoured — `Tau_Boundary`, `Sigma_Boundary` and `Zero_Boundary` (non-uniform
transitions), `C_Date`, `R_Combine`, the deposition-model sequences (`P_Sequence`, `D_Sequence`,
`V_Sequence`), a curve the app does not have, or a custom `Outlier_Model` — **fails the import**
with a message naming it, rather than being read as the nearest thing that is implemented. An
unrecognised command fails it too. Commands that only affect what OxCal plots (`Sum`, `KDE_Plot`,
`Span`, `Difference`, `Order`…) are skipped with a note, since they query a posterior rather than
shape one.

Two differences in how the file is read are reported rather than left implicit: a date sitting
directly inside a `Sequence` is put in a Phase of its own (this app attaches dates to Phases; its
position, and so the ordering, is unchanged), and several independent top-level items are wrapped
in one Phase, which asserts no ordering between them.

## Calibration Curves

**Curves** in the top navigation lists every curve this app can calibrate against, and is where you
add your own. Three are shipped with it — IntCal20, SHCal20 and Marine20 — and cannot be edited or
removed.

Everything measured in that table is read from the curve that actually loads: its point count,
calendar range, and the structure of its calendar grid (IntCal20 steps 1 year up to 5,000 cal BP
and 20 years beyond 25,000). Nothing is recorded separately from the file, so what you see is what
a calibration will use.

### Importing a curve

**Import Curve** takes an IntCal-format `.14c` file, or a CSV with a calendar age, a radiocarbon age
and a 1σ error column. Column names are matched however they are spelled and in whatever order they
appear, and a calendar column in BC/AD (negative for BC) can be converted on the way in.

Nothing is stored until you have seen the file read: the dialog reports the point count, the ranges,
what it assumed, and draws the curve with its ±1σ band. That drawing is the check worth making — a
calibration curve rises left to right with wiggles, and anything else means the columns were read
the wrong way round. A date list is short enough to read after importing it; a curve is thousands of
rows that nobody will check by eye, and a wrong one produces confident, plausible, wrong calendar
ages for every sample calibrated against it.

Files are refused rather than repaired, each refusal naming the row and the reason: a non-positive
error, a duplicated calendar age, a radiocarbon column that looks like F¹⁴C rather than an age, a
negative cal BP (post-bomb curves need a different likelihood, which this app does not have). Rows
written oldest-first are simply sorted, as the IntCal files themselves are written that way.

Give a curve **aliases** if files refer to it by other names — an alias of `IntCal13` is what lets an
`.oxcal` model written against IntCal13 import once you have added that curve.

**A stored curve's numbers never change.** You can correct its name, citation, DOI, notes and
aliases at any time; correcting the *data* means importing the corrected file as a new curve. An
analysis run last month has to keep meaning what it said. For the same reason a curve that something
has been calibrated against cannot be deleted: the app offers to **archive** it instead, which
removes it from the curve pickers while leaving it resolvable, so those analyses still re-run.

### Mixed curves

**New Mixed Curve** builds a curve from two others in a fixed proportion — OxCal's `Mix_Curves`.
Its use is a sample whose carbon came from more than one reservoir, most often a human or animal
with a partly marine diet: their bone is neither terrestrial nor marine carbon, and calibrating it
against either curve alone is wrong by centuries in one direction or the other, with nothing in the
result to say so.

State the proportion as a percentage of the first curve, and **give its uncertainty**. A dietary
fraction comes from stable isotopes and has real error, and that error widens the mixed curve most
where the two curves are furthest apart — which is exactly where being wrong about the diet moves a
date furthest. Left at zero, the curve claims the proportion is known exactly.

An `.oxcal` file using `Mix_Curves` will import, matched against a mixture you already have. If you
have none matching, the import fails naming exactly what to create — opening a file never creates
curves behind your back.

### Browsing a curve

Clicking a curve's name opens the browser, which answers three questions.

**What shape is the curve, and how well known is it?** The chart draws the curve with its own ±1σ
and ±2σ bands, oldest on the left. Every calibrated date this app produces is this curve read
backwards, so its wiggles are not detail: they are why a date splits into two ranges, or spreads
over three centuries.

**Where am I?** The strip under the chart is the whole 55,000 years with your current view marked.
Drag it to move, drag an edge to zoom, click to jump; there are also zoom buttons and presets for
the whole curve, the Holocene, and the Hallstatt plateau.

**What is the curve doing here?** Pointing at it reports what the curve says at that calendar age
and its **local gradient**, which is what governs how tightly anything can be dated there:

| Gradient | Reads as | What it means for a date |
|---|---|---|
| below −0.05 | reversal | the curve doubles back, so one measurement matches several separate calendar ranges |
| −0.05 to 0.35 | plateau | nearly flat: one measurement spreads over a wide calendar span |
| 0.35 to 0.8 | shallow | flatter than calendar time, so dates here spread |
| 0.8 to 1.6 | ordinary | the curve tracks calendar time; a date calibrates about as tightly as it was measured |
| above 1.6 | steep | rising faster than calendar time, so dates here calibrate unusually tightly |

Three more controls sit in the header bar. **Compare with** overlays a second curve and adds a panel
showing their *difference* — worth doing, because two curves drawn over each other look alike at any
zoom that fits a millennium on screen, while IntCal20 − SHCal20 shows the hemispheric offset (about
40 ¹⁴C years) at a glance. **Dates as** switches the calendar convention. **Expected resolution** is
described next.

**Clicking a calendar year** shows what a date from it would actually look like: the curve's own
radiocarbon age there, calibrated back at a laboratory precision you choose. Inside the Hallstatt
plateau a ±25 year measurement comes back as four disjoint calendar ranges spread over 337 years;
a century and a half earlier, on the steep drop into it, the same precision gives one range of 37
years. That is the clearest statement of what a plateau costs, and no amount of laboratory precision
removes it.

### Expected resolution

Switching **Expected resolution** on adds a panel, on the same calendar axis, showing how tightly a
date from each year could be known *before anyone measures anything*. The line is the mean width of
the 95.4% calibrated range; the band around it is the 5th–95th percentile, i.e. how lucky or unlucky
a single date can be.

The method is Svetlik et al. (2019): for every radiocarbon age a sample of that period could
plausibly return, the laboratory error is scaled with age, the measurement calibrated, and the width
of its calibrated range recorded; those widths are then averaged over the measurements that period
could actually yield. **Lab ±1σ** sets the precision assumed. It starts at ±25, which is what a
commercial date typically carries; ±15 is about the best a good AMS laboratory offers, and is the
figure the method's published curves use — which is why it describes the *best possible*
resolution.

Read it as a planning tool. On IntCal20 at the default ±25, the Hallstatt plateau sits at about 330
years while the drop at its start is 103 — and at the ±15 of the best AMS work, 312 against 33. The
gap between those two places is far larger than the gap between a good laboratory and an ordinary
one, which is the point: where you are on the curve matters more than what you pay for the
measurement. **Export profile** saves the whole
curve's profile as CSV; **Export this window** saves the visible stretch of the curve itself.

## Model Structure (Bayesian only)

Optional, but this is what makes a Bayesian analysis different from a Group Calibration: build the
model as nested Sequences and Phases with Boundaries placed where you want them (the OxCal idiom),
then assign each date to a Phase. TPQ (terminus post quem — a maximum age) and TAQ (terminus ante
quem — a minimum age) limiters can be set on a date or on a whole Phase.

Reached via **Specify Model Structure** / **Edit Model Structure**, which has an **Editor** tab for
building the tree and a **Harris Matrix Diagram** tab showing it drawn. The Results page renders
the same diagram for a completed analysis, and the Posterior Distributions plots follow its order.

### Reading the Harris matrix

The diagram is a picture of the model tree, not a second place to edit it — build the structure on
the Editor tab and the diagram follows. **Oldest is at the bottom**, as the convention has it.

- A **context** (solid box) is a Phase that dates are attached to, showing how many it holds and
  any TPQ/TAQ limiter.
- A **box drawn around other boxes** is a Sequence or a grouping Phase. It groups without ordering:
  a Phase containing two Phases says they belong together and says nothing about which came first,
  so they sit side by side inside it.
- A **labelled rule** across a group is a Boundary — a modelled transition — with its 95% range
  once the analysis has run.
- An **arrow** means the lower block is older. Arrows are drawn only between adjacent blocks, and
  only where no Boundary rule already separates them: a relationship implied by another one is left
  out, which is what keeps a nested model readable.

Nothing in the diagram is draggable. Position carries meaning here — a context sits centred beneath
the group that contains it — so it is laid out rather than arranged by hand.

**Model Settings**, on the New Analysis form itself:
- **Solver** — Automatic (recommended: exact integration where the model allows it, sampling
  otherwise), Exact solver only (fails rather than silently sampling), or MH/Gibbs (forces OxCal's
  own sampling method throughout — for reproducing a published OxCal analysis).
- **Posterior draws** — how many samples each date's posterior is reported with.
- **Uniform span prior** — OxCal's `UniformSpanPrior` correction (on by default), which keeps a
  sequence's overall span prior independent of how finely it's subdivided. Only affects a sequence
  with three or more Boundaries.
- **Durations and differences** — off by default; see
  [Durations and Differences](#durations-and-differences) below for what it adds and when to want
  it. A Group Calibration has the same option, labelled **Differences between dates**, under
  **Reporting**.

## Running an Analysis

**Run Analysis** submits the form. A saved analysis (Group Calibration, Bayesian, or Single
Calibration with "Save this analysis" on) navigates straight to its Results page.

## Viewing Results

- **Results Summary** — one row per date (and, for a Bayesian model, per Boundary), in the model's
  own structure order. Columns: the date's unconstrained ("unmodelled") calibration, its modelled
  posterior HPD range at the confidence you've selected, and diagnostics.
- **HPD Confidence** — 68.3%, 95.4%, or 99.7%: the two-sided normal coverages of 1, 2 and 3
  standard deviations, which is what "1 sigma" means and what OxCal reports against. These are
  the exact coverages (68.269%, 95.450%, 99.730%), not the labels rounded back into the
  calculation — the level is the threshold an HPD region is accumulated to, so the digits move
  the reported range.
- **Show Mean / Show Median** — optional point estimates alongside the HPD ranges.
- Any date whose diagnostics are worth a second look (poor agreement with its own unconstrained
  calibration, or a high outlier probability) is called out in a banner above the table.
- **Durations and Differences** — above the results table, when the run was asked for them. See
  [Durations and Differences](#durations-and-differences); a run that was not asked for them says
  so in one line rather than showing an empty table.
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

## Durations and Differences

Off by default, and turned on before the run — under **Model Settings** for a Bayesian analysis
(**Durations and differences**) or **Reporting** for a Group Calibration (**Differences between
dates**). To add it to an analysis that has already run, **Edit** it, turn the option on, and run it
again; **Re-run** on its own reuses the settings the analysis was saved with.

Everything in this section is a **length of time in years**, never a calendar date, so none of it is
converted to BC/AD when you change the reporting format.

### The three durations

- **Sequence** — from a Sequence's first Boundary to its last. How long the whole of it was in use.
- **Phase** — the interval a Phase's two Boundaries enclose. This is OxCal's `Interval()`, and it is
  the *modelled* duration: it is normally longer than the spread of the dates inside, because those
  dates are treated as a sample of the phase rather than the whole of it.
- **Dated events** — from the oldest to the youngest dated event in a Phase (OxCal's `Span()`).
  Nothing is inferred beyond the dates themselves.

Reading the second against the third is the point of having both. A phase whose dated events span 30
years but whose modelled duration is 250 is a phase your dates have barely sampled.

A Phase with fewer than two dates gets no "dated events" row — one date has no span. A Sequence with
only one bounded group gets no Sequence row, because its Phase duration is already the same quantity.

### Differences

One row per pair. For two **phases** the figure is signed: **positive is a gap** between them in
years, **negative is an overlap**. For two **dates** in a Group Calibration, positive means the first
of the pair is the older.

**Overlap** grades how far the two are from being separable in time, from `none` to `extensive`.
For phases it is the probability that their modelled intervals intersect; for dates it is how far
the model is from being able to say which came first (100% is a coin flip). The bands are the same
68.3% and 95.4% levels every HPD in the app is quoted at, so `none` means "separated at 2 sigma" and
`extensive` means "overlapping at 2 sigma".

The years column beside it is headed **Separation** for phases and **Difference** for dates.

Two phases either side of a single shared Boundary read **shares a transition** in place of both
figures, and the Overlap column reads **—**: one phase's end *is* the next one's start, so the model
gives them no gap and no overlap to measure. Neither zero is a finding, which is why neither is
printed — on screen, in the CSV or in the PDF. Put a **Hiatus** between two Boundaries where you
want the gap itself modelled. Two phases sitting inside the *same* pair of Boundaries are not
compared at all — the model brackets them identically, so it has nothing to say about their order.

In a plain Sequence, where every Phase shares its Boundaries with its neighbours, this table has
little to add: adjacent pairs share a transition, and the gap between two non-adjacent Phases is
the durations of the Phases between them, which the Durations table already reports. The table
earns its place where the model leaves the order genuinely open: Phases separated by a Hiatus, and
Phases in different branches of the model.

### What these cost

Every quantity here depends on several modelled ages at once, which means it can only be read off
*joint* draws of the posterior rather than the per-parameter distributions the rest of the page
shows. Those draws are the only Monte Carlo in the exact solver's output, so these carry a sampling error — the panel says how many draws they came from, and whether those draws were independent (the exact solver, or a Group Calibration) or an MCMC chain.
The reported ranges are quoted at a step that widens for a very long duration, rather than claiming
five-year precision the draws cannot support.

## Exporting Data

- **Export CSV** (on the Results page) — the full results table, including the unmodelled
  comparison, in the currently-selected date format. Where a run produced durations and differences,
  they follow as a second block below it, after a blank line and their own header — they share no
  column with the results table, since every figure in them is a length rather than a date.
- **JSON** — available via the API (`GET /api/results/{id}/export/json`) for anyone who wants the
  full result machine-readable; there's no button for it in the UI yet.
- **Curve data** (on a curve's browser page) — **Export this window** saves the visible stretch of
  the curve as CSV (calendar age, radiocarbon age, error, local gradient); **Export profile** saves
  its whole expected-resolution profile. A curve itself can be downloaded from the Curves page, and
  an imported one comes back byte-for-byte as it was uploaded.
- **Export PDF** (on the Results page) — a printable report of the whole analysis: what it is and
  when it was run, the curve(s) it was calibrated against with their references, which solver
  computed the posteriors, the results table in the model's own reading order, the durations and
  differences where there are any, and a plot of every date and Boundary with its HPD ranges marked. Like the CSV it reads in the date format currently
  selected. It is rendered on the server and draws a plot per modelled parameter, so a large model
  takes a few seconds — the button says so while it works.

## Projects and Date Collections

A **Project** groups analyses, and can also hold its own collection of dates — entered one at a
time, pasted in bulk (`name, age, error[, material[, context[, curve]]]` per line), or imported
from a file (see [Importing Dates from a File](#importing-dates-from-a-file)) — independent
of any particular analysis. A collection holds dates rather than models, so importing an OxCal file
here keeps each date's phase as its default context but not the structure; import it from a new
analysis to keep that. Deploying a selection of them copies the values into a new analysis, so
editing the collection afterwards never changes an analysis already created from it, and the same
dates can be deployed more than once to model them differently each time.

From a project page you can also re-run every analysis in it at once, or re-run, duplicate, or edit
the inputs of a single analysis (editing replaces its saved inputs and re-runs it in place, keeping
the same result page). Deploying always creates a **Bayesian** analysis and opens its editor, so
changing the Analysis Type there is the usual way to end up with a group calibration — that
converts the analysis you are editing rather than creating a second one. Choosing Single
Calibration for more than one date stores it as a group calibration, since a single calibration is
one date on its own.

## Best Practices

1. **Sample data** — use consistent units (age always in years BP), and double-check stratigraphic
   order before building a model structure around it.
2. **Calibration curves** — IntCal20 for Northern Hemisphere terrestrial samples, SHCal20 for
   Southern Hemisphere terrestrial, Marine20 for marine samples. Use the most recent curve version.
   For a sample whose carbon came from more than one reservoir, build a mixed curve rather than
   choosing the nearer of the two — see [Calibration Curves](#calibration-curves).
3. **Marine corrections** — consult the literature for an appropriate local ΔR; document its source
   in the sample's notes.
4. **Reading diagnostics** — the agreement index compares a date's modelled posterior against its
   own unconstrained calibration (100 = the model left it alone; below ~60 is conventionally worth a
   second look). It flags a date the model had to move, which is not automatically the same as a
   date that's simply wrong — see the discussion in `backend/app/services/agreement.py` if you're
   using it to hunt for outliers specifically.
5. **Check the curve before commissioning the dates** — the browser's expected-resolution panel
   says how tightly a date from your period can be known at best. If your question needs finer
   resolution than the curve can give there, no laboratory precision will supply it, and it is
   better to know that before the samples are sent than after.
6. **Wide Boundaries on a short phase are expected** — a Phase's Start/End Boundaries genuinely need
   four or more dated members before their span is even mathematically well-constrained; two or
   three dates in a phase can produce a very wide 95% range and that's correct, not a bug.
