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
12. [Comparing Analyses](#comparing-analyses)
13. [Best Practices](#best-practices)
14. [Appendix A: Mathematical Methods](#appendix-a-mathematical-methods)
15. [Bibliography](#bibliography)

## Getting Started

1. Navigate to `http://localhost:5173` (or your deployment URL).
2. Click **New Analysis** (top navigation bar, or from the Dashboard).

There is no multi-step wizard. New Analysis is a single form: settings on the left, adding
measurements on the right, the measurement list and (for a Bayesian analysis) the model structure
below, and a Run Analysis button at the bottom.

## Creating an Analysis

- **Analysis Name**: required.
- **Analysis Type**:
  - **Single Calibration**: one date, calibrated on its own. Has an extra "Save this analysis"
    toggle: switch it off to run a quick test (the result is shown right there on the page) without
    creating anything in a project or the analysis list.
  - **Group Calibration**: several dates, each calibrated independently. No stratigraphy is
    applied; this is the direct calibration of every date in the group, not a Bayesian model.
  - **Bayesian**: one or more dates modelled together with stratigraphic constraints. See
    [Model Structure](#model-structure-bayesian-only) below.
- **Project**: optional. Assigning a project makes that project's own date collection available to
  pull from (see [Projects and Date Collections](#projects-and-date-collections)), and adds this
  analysis to the project's list.
- **Result Date Format**: how calendar ages are displayed (e.g. cal BP, BC/AD). This only changes
  the labelling; the underlying calibration is unaffected.

## Adding Measurements

Each date needs, at minimum, a **Sample Name**, **Age (BP)** and **1σ Error**. Also available:

- **Material**: free text (charcoal, bone, shell, etc.).
- **Sample Type**: Terrestrial or Marine. Marine samples get an extra **ΔR** (local reservoir
  offset) field with its own 1σ error; both are required together, since they're combined in
  quadrature with the measurement's own uncertainty.
- **Calibration Curve**: IntCal20 (Northern Hemisphere terrestrial), SHCal20 (Southern Hemisphere
  terrestrial), Marine20, and any curve you have imported or mixed yourself. Selecting Marine
  switches to a marine curve, since marine carbon calibrated against a terrestrial curve is wrong
  by centuries. See [Calibration Curves](#calibration-curves).
- **Outlier settings** (Bayesian analyses only): an optional prior probability that this date does
  not belong to the modelled population, with a choice of outlier model (general vs. charcoal,
  i.e. one-sided for material likely to predate its context).

If a project is assigned and its date collection already has dates in it, an **Add from Project's
Date Collection** button appears above the measurement form. Pick any subset to copy into this
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
Column **order does not matter** and common spellings are understood: `Lab Code`, `Age BP`,
`± 1σ`, `sd`, `Material`, `Curve`, `Context` all resolve to the right field. Comma, tab and
semicolon separators are all read.

Use **Download template** in the dialog for a file with the full column list and three worked
example rows. The optional columns are `material_type`, `curve_id`, `sample_type`, `delta_r`,
`delta_r_error`, `phase`, `tpq_age_bp`, `taq_age_bp`, `outlier_model`, `outlier_prior`, `depth_cm`,
`location` and `notes`.

The `phase` column can be used to prepare the list of dates for use in a model. Dates sharing a phase name are
grouped, and a starting structure is built around them: one Start/End pair with the phases
**unordered** inside it, since the CSV can't say which group
came first. Ordering them is then a decision you make in the Model Structure editor.

The app's own results export is readable back in, so exporting a table, correcting it in a
spreadsheet and re-importing works. Its structural rows and its posterior columns are recognised
and skipped rather than read back as input.

### OxCal (.oxcal)

An OxCal model file is read directly. Its `Sequence`, `Phase`, `Boundary` and `Hiatus` structure,
per-scope `Curve` and `Delta_R`, and per-date `Outlier` settings all come across, keeping the names
OxCal gave them. Importing one from New Analysis brings the model structure with it.

A reservoir offset can be written int he ways OxCal allows: in full, `Delta_R("LocalMarine",-162,28)`,
or stated once under a name and cited wherever it applies, `Delta_R("=LocalMarine", )`. The offset
belongs to the curve it was stated for (OxCal applies `Delta_R` to the current `Curve`), so a phase
holding marine dates on Marine20 and terrestrial ones on SHCal20 gives the offset to the marine
dates only. The preview shows the ΔR carried by each date, and a note says how many dates each named
offset reached.

OxCal's language is larger than what this app models. Anything that would change the posterior and
cannot be honoured **fails the import** with a message naming it, rather than being read as the
nearest thing that is implemented: `Tau_Boundary`, `Sigma_Boundary` and `Zero_Boundary`
(non-uniform transitions), `C_Date`, `R_Combine`, the deposition-model sequences (`P_Sequence`,
`D_Sequence`, `V_Sequence`), a curve the app does not have, or a custom `Outlier_Model`. An
unrecognised command fails it too. Commands that only affect what OxCal plots (`Sum`, `KDE_Plot`,
`Span`, `Difference`, `Order`…) are skipped with a note, since they query a posterior rather than
shape one.

Two differences in how the file is read are reported rather than left implicit: a date sitting
directly inside a `Sequence` is put in a Phase of its own (this app attaches dates to Phases; its
position, and so the ordering, is unchanged), and several independent top-level items are wrapped
in one Phase, which asserts no ordering between them.

## Calibration Curves

**Curves** in the top navigation lists every curve this app can calibrate against, and is where you
add your own. Three are shipped with it (IntCal20, SHCal20 and Marine20) and cannot be edited or
removed.

Everything measured in that table is read from the curve that actually loads: its point count,
calendar range, and the structure of its calendar grid (IntCal20 steps 1 year up to 5,000 cal BP
and 20 years beyond 25,000). Nothing is recorded separately from the file, so what you see is what
a calibration will use.

### Importing a curve

**Import Curve** takes an IntCal-format `.14c` file, or a CSV with a calendar age, a radiocarbon age
and a 1σ error column. Column names are matched however they are spelled and in whatever order they
appear, and a calendar column in BC/AD (negative for BC) can be converted on the way in.

The dialog reports the point count, the ranges, what it assumed, and draws the curve with its ±1σ band. 
Check that the curve draws correctly, to confirm the rows and columns were input/read in the right order.

Files are refused rather than repaired, each refusal naming the row and the reason: a non-positive
error, a duplicated calendar age, a radiocarbon column that looks like F¹⁴C rather than an age, a
negative cal BP (post-bomb curves need a different likelihood, which this app does not have). Rows
written oldest-first are simply sorted, as the IntCal files themselves are written that way.

Give a curve **aliases** if files refer to it by other names. An alias of `IntCal13` is what lets an
`.oxcal` model written against IntCal13 import once you have added that curve.

**A stored curve's numbers never change.** You can correct its name, citation, DOI, notes and
aliases at any time, but correcting the *data* means importing the corrected file as a new curve. An
analysis run last month has to keep meaning what it said. For the same reason a curve that something
has been calibrated against cannot be deleted: the app offers to **archive** it instead, which
removes it from the curve pickers while leaving it resolvable, so those analyses still re-run.

### Mixed curves

**New Mixed Curve** builds a curve from two others in a fixed proportion, OxCal's `Mix_Curves`.
Its use is a sample whose carbon came from more than one reservoir, most often a human or animal
with a partly marine diet: their bone is neither terrestrial nor marine carbon, and calibrating it
against either curve alone is wrong by centuries in one direction or the other, with nothing in the
result to say so.

State the proportion as a percentage of the first curve, and **give its uncertainty**. A dietary
fraction comes from stable isotopes and has real error, and that error widens the mixed curve most
where the two curves are furthest apart, which is exactly where being wrong about the diet moves a
date furthest. Left at zero, the curve claims the proportion is known exactly.

An `.oxcal` file using `Mix_Curves` will import, matched against a mixture you already have. If you
have none matching, the import fails and says what you will have to create.

### Browsing a curve

Clicking a curve's name opens the browser, which answers three questions.

**What shape is the curve, and how well known is it?** The chart draws the curve with its own ±1σ
and ±2σ bands, oldest on the left. The wiggles in a curve are why a date splits into two ranges, or spreads
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
showing their *difference*, worth doing because two curves drawn over each other look alike at any
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
figure the method's published curves use, which is why it describes the *best possible*
resolution.

You could read this as a planning tool. On IntCal20 at the default ±25, the Hallstatt plateau sits at about 330
years while the drop at its start is 103; at the ±15 of the best AMS work, 312 against 33. The
gap between those two places is far larger than variation in laboratory accuracy. So, where you are on the curve 
matters more than what you pay for the measurement. **Export profile** saves the whole
curve's profile as CSV; **Export this window** saves the visible stretch of the curve itself.

## Model Structure (Bayesian only)

Optional, but this is what makes a Bayesian analysis different from a Group Calibration. Build the
model as nested Sequences and Phases with Boundaries placed where you want them,
then assign each date to a Phase. RadiCal uses the same terminology as Oxcal. 
TPQ (terminus post quem, a maximum age) and TAQ (terminus ante quem, a minimum age) 
limiters can be set on a date or on a whole Phase.

Reached via **Specify Model Structure** / **Edit Model Structure**, which has an **Editor** tab for
building the tree and a **Harris Matrix Diagram** tab showing it drawn. The Results page renders
the same diagram for a completed analysis, and the Posterior Distributions plots follow its order.

### Reading the Model diagram

The diagram is a picture of the model tree. Build the structure on
the Editor tab and the diagram follows. **Oldest is at the bottom** as in a stratigraphic profile.

- A **context** (solid box) is a Phase that dates are attached to, showing how many it holds and
  any TPQ/TAQ limiter.
- A **box drawn around other boxes** is a Sequence or a grouping Phase. It groups without ordering.
  A Phase containing two Phases says they belong together and says nothing about which came first,
  so they sit side by side inside it.
- A **labelled rule** across a group is a Boundary, a modelled transition, with its 95% range
  once the analysis has run.
- An **arrow** means the lower block is older. Arrows are drawn only between adjacent blocks, and
  only where no Boundary rule already separates them: a relationship implied by another one is left
  out, which is what keeps a nested model readable.

Nothing in the diagram is draggable. Position carries meaning here (a context sits centred beneath
the group that contains it), so it is laid out rather than arranged by hand.

**Model Settings**, on the New Analysis form itself:
- **Solver**: Automatic (recommended: exact integration where the model allows it, sampling
  otherwise), Exact solver only (fast, but only works on chain-shaped models), or MH/Gibbs (works on all models, uses the same MCMC sampling method as OxCal throughout). See
  [Choosing a solver](#choosing-a-solver) for when to override Automatic and what it costs.
- **Posterior draws**: how many samples each date's posterior is reported with.
- **Uniform span prior**: OxCal's `UniformSpanPrior` correction (on by default), which keeps a
  sequence's overall span prior independent of how finely it's subdivided. Only affects a sequence
  with three or more Boundaries.
- **Durations and differences**: off by default; see
  [Durations and Differences](#durations-and-differences) below for what it adds and when to want
  it. A Group Calibration has the same option, labelled **Differences between dates**, under
  **Reporting**.

## Running an Analysis

**Run Analysis** submits the form. A saved analysis (Group Calibration, Bayesian, or Single
Calibration with "Save this analysis" on) navigates straight to its Results page.

### Choosing a solver

Only a Bayesian analysis asks you to choose a solver. A Single or Group Calibration asserts no
relationship between dates, so there is nothing to solve jointly. **Automatic is the right answer
for almost every run**: it integrates the posterior exactly where the model as written allows it
and samples otherwise, and the Results page always says which of the two actually ran. The override
exists for the handful of cases below.

**Exact integration** applies to a *chain-shaped* model - one line of Phases separated by Boundaries,
with every date inside a bounded group, and no ordering the chain does not already imply. It computes 
the posterior on a calendar grid by message passing. There is no sampler, so nothing to converge, no 
run length to choose, and no calibration lobe the chain can miss. It is also the faster of the two, usually 
by a wide margin: a single deterministic pass, with no progress bar because there is no iteration to report.

**MH/Gibbs sampling** is OxCal's own scheme (Metropolis-Hastings within Gibbs) and takes any model:
branching structures, parallel groups, two groups ordered against each other with no Boundary
between them, dates sitting outside every Boundary. It runs a minimum of 20,000 sweeps and extends
up to 200,000 until the worst parameter reaches 95% on OxCal's own convergence measure; the achieved
figure is reported with the results, and a run that fell short is flagged rather than quietly
returned.

Where both can be asked, they agree to under a year, and they are checked against each other on
every model both can handle.

| | Exact solver | MH/Gibbs sampler |
|---|---|---|
| Model shapes | chain-shaped only; fails rather than sampling | all |
| Result | deterministic; the same input gives the same numbers | stochastic; converges to the same posterior |
| Speed | one pass; typically much faster | 20,000–200,000 sweeps, extended until converged |
| Convergence | not applicable, since nothing iterates | reported per run; can fall short and say so |
| Multi-lobe calibration | resolved exactly | handled by whole-group translation moves, but a sampled answer |
| Duration/difference draws | independent draws from the joint posterior | draws from an MCMC chain, so correlated |
| Outlier models | how big an outlier's offset is, taken over its prior range date by date (an approximation) | one offset size shared across the model and estimated from the data, as OxCal does |
| Progress and cancellation | neither: it completes or fails | progress reported, run cancellable |

**Choose MH/Gibbs deliberately when:**

- You are **reproducing a published OxCal analysis** and want the method to match as well as the
  model.
- You want an **independent cross-check** of an exactly-integrated result: two different routes to
  the same posterior disagreeing by more than a year or two is worth investigating, and **Compare**
  will measure the difference, provided the solver is the only thing you changed between the two
  runs (see [Comparing Analyses](#comparing-analyses)).
- Several dates carry **outlier models** and the run is about *which* dates are outliers. An outlier
  model does not only say a date might not belong. It also carries an assumption about **how far
  off an outlier is likely to be**: tens of years, or several centuries. OxCal treats that offset
  size as a single unknown shared by every date using the model and lets the data estimate it, so
  three charcoal dates that all read old together tell the model what an old-wood offset looks like
  in *this* assemblage, and each date is then judged against that learned size. The exact solver
  cannot pool information that way, so it works the offset size out over its prior range separately
  for each date, judging each one on its own. The difference shows up mainly in the **reported
  outlier probabilities**, and it stays small when the outlier priors are small. On a real OxCal comparison the exact path's outlier
  probabilities sat 0.017 from OxCal's where the sampler reached 0.002.

  So: **it matters** if you have put outlier models on a group of dates expecting a shared cause
  (old wood, residuality, a suspect laboratory batch) and the outlier probabilities are the answer
  you are after, or you intend to quote them alongside OxCal's. **It does not matter** if one or two
  dates carry a small outlier prior as insurance and what you are reading off the run is the
  modelled date ranges; there the two paths differ by less than the ranges are quoted to.

**Choose Exact solver only when:**

- You need a **reproducible, deterministic** number: the same analysis re-run must give the same
  figures to the digit, with no convergence caveat attached.
- You want to **force exact integration on a model Automatic declined to integrate**, and you accept
  that this changes the model. See the warning below.

**The warning about forcing Exact.** Automatic integrates exactly only where the model needs no
help. A model that is not a chain *as authored*, most often a Sequence that orders two groups with
no Boundary between them, can be made solvable by supplying the transitions its ordering implies,
and choosing **Exact solver only** does exactly that. This is not a restatement of your model: it
splits one span term over K dates into two terms over K₁ + K₂ dates, and K is the exponent that
holds a date to its group, so the modelled ranges genuinely move. Measured against OxCal on the
app's own fixtures (rms of the modelled dates, in years), sampling the model as written beat
integrating the patched model both times: 1.49 against 2.51 on the `ordered` fixture, and 4.53
against 10.63 on the charcoal fixture. So forcing Exact on such a model cross-checks *a different
model* as well as a different method, and the Results page says how many transitions were supplied
whenever this happens. If the exact solver is what you want, the better fix is usually to write the
missing Boundary into the model yourself, which makes it exactly solvable and says out loud what the
patch would have done silently.

If a model cannot be integrated exactly even with those transitions supplied, **Exact solver only**
fails the run and names the reason rather than quietly falling back to sampling. That is the point
of the setting: Automatic is what you want if you would have been happy with either answer.

The mathematics behind both solvers is in [A.6 Solvers](#a6-solvers).

## Viewing Results

- **Results Summary**: one row per date (and, for a Bayesian model, per Boundary), in the model's
  own structure order. Columns: the date's unconstrained ("unmodelled") calibration, its modelled
  posterior HPD range at the confidence you've selected, and diagnostics.
- **HPD Confidence**: 68.3%, 95.4%, or 99.7%, the two-sided normal coverages of 1, 2 and 3
  standard deviations, which is what "1 sigma" means and what OxCal reports against. These are
  the exact coverages (68.269%, 95.450%, 99.730%), not the labels rounded back into the
  calculation. The level is the threshold an HPD region is accumulated to, so the digits move
  the reported range.
- **Show Mean / Show Median**: optional point estimates alongside the HPD ranges.
- Any date whose diagnostics are worth a second look (poor agreement with its own unconstrained
  calibration, or a high outlier probability) is called out in a banner above the table.
- **Durations and Differences**: above the results table, when the run was asked for them. See
  [Durations and Differences](#durations-and-differences); a run that was not asked for them says
  so in one line rather than showing an empty table.
- **Model Structure**: a summary (phase and ordering-relationship counts) and, for a Bayesian
  analysis, the read-only Harris matrix diagram of the constraints actually used.
- **Posterior Distributions**: Plotly charts, oldest first:
  - **Stacked** (default): every date (and Boundary) as one row each, sharing a single calendar
    axis, so overlap between dates is directly visible. Each row is scaled to its own peak, so
    heights compare precision, not probability.
  - **Individual**: one full-size plot per date/Boundary.
  - **Show boundary plots**: hide/show Boundary rows separately from dates.
  - **Show prior (unmodelled) distribution**: off by default; overlays each date's plain
    calibration (no stratigraphic constraints) as a grey shadow behind its modelled posterior, so
    you can see exactly how far the model moved it.

## Durations and Differences

Off by default, and turned on before the run: under **Model Settings** for a Bayesian analysis
(**Durations and differences**) or **Reporting** for a Group Calibration (**Differences between
dates**). To add it to an analysis that has already run, **Edit** it, turn the option on, and run it
again; **Re-run** on its own reuses the settings the analysis was saved with.

Everything in this section is a **length of time in years**, never a calendar date, so none of it is
converted to BC/AD when you change the reporting format.

### The three durations

- **Sequence**: from a Sequence's first Boundary to its last. How long the whole of it was in use.
- **Phase**: the interval a Phase's two Boundaries enclose. This is OxCal's `Interval()`, and it is
  the *modelled* duration: it is normally longer than the spread of the dates inside, because those
  dates are treated as a sample of the phase rather than the whole of it.
- **Dated events**: from the oldest to the youngest dated event in a Phase (OxCal's `Span()`).
  Nothing is inferred beyond the dates themselves.

Reading the second against the third is the point of having both. A phase whose dated events span 30
years but whose modelled duration is 250 is a phase your dates have barely sampled.

A Phase with fewer than two dates gets no "dated events" row, since one date has no span. A
Sequence with only one bounded group gets no Sequence row, because its Phase duration is already the same quantity.

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
printed, on screen or in the CSV or the PDF. Put a **Hiatus** between two Boundaries where you
want the gap itself modelled. Two phases sitting inside the *same* pair of Boundaries are not
compared at all: the model brackets them identically, so it has nothing to say about their order.

In a plain Sequence, where every Phase shares its Boundaries with its neighbours, this table has
little to add: adjacent pairs share a transition, and the gap between two non-adjacent Phases is
the durations of the Phases between them, which the Durations table already reports. The table
earns its place where the model leaves the order genuinely open: Phases separated by a Hiatus, and
Phases in different branches of the model.

### What these cost

Every quantity here depends on several modelled ages at once, which means it can only be read off
*joint* draws of the posterior rather than the per-parameter distributions the rest of the page
shows. Those draws are the only Monte Carlo in the exact solver's output, so these carry a sampling
error, and the panel says how many draws they came from and whether those draws were independent
(the exact solver, or a Group Calibration) or an MCMC chain.
The reported ranges are quoted at a step that widens for a very long duration, rather than claiming
five-year precision the draws cannot support.

## Exporting Data

- **Export CSV** (on the Results page): the full results table, including the unmodelled
  comparison, in the currently-selected date format. Where a run produced durations and differences,
  they follow as a second block below it, after a blank line and their own header. They share no
  column with the results table, since every figure in them is a length rather than a date.
- **JSON**: available via the API (`GET /api/results/{id}/export/json`) for anyone who wants the
  full result machine-readable; there's no button for it in the UI yet.
- **Curve data** (on a curve's browser page): **Export this window** saves the visible stretch of
  the curve as CSV (calendar age, radiocarbon age, error, local gradient); **Export profile** saves
  its whole expected-resolution profile. A curve itself can be downloaded from the Curves page, and
  an imported one comes back byte-for-byte as it was uploaded.
- **Export PDF** (on the Results page): a printable report of the whole analysis: what it is and
  when it was run, the curve(s) it was calibrated against with their references, which solver
  computed the posteriors, the results table in the model's own reading order, the durations and
  differences where there are any, and a plot of every date and Boundary with its HPD ranges marked. Like the CSV it reads in the date format currently
  selected. It is rendered on the server and draws a plot per modelled parameter, so a large model
  takes a few seconds, and the button says so while it works.
- **Export CSV** (on a comparison page): every paired date with each run's median, HPD range and
  agreement index, then the shift and width ratio against the reference run. Below it, the model's
  derived quantities in three separate blocks (modelled transitions, durations, separations),
  because a calendar age and a length of time share no column any more than they share an axis on
  screen, and each block names its own units and what a positive shift means in them (older,
  longer, a wider gap). A quantity that only some of the runs have carries their names in a
  **Present in** column rather than being left as a blank that reads like a missing measurement,
  and two phases either side of one shared Boundary read *shares a transition* here exactly as they
  do everywhere else. The dates that could not be paired follow under their own header, so a
  spreadsheet of eleven dates from a comparison of fourteen says so itself. The settings diff and
  the agreement scatter are not exported.
- **Download PNG** (for each Plot): provides a print-quality PNG file of the plot window in the view currently shown on screen.

## Projects and Date Collections

A **Project** groups analyses, and can also hold its own collection of dates, entered one at a
time, pasted in bulk (`name, age, error[, material[, context[, curve]]]` per line), or imported
from a file (see [Importing Dates from a File](#importing-dates-from-a-file)), independent
of any particular analysis. A collection holds dates rather than models, so importing an OxCal file
here keeps each date's phase as its default context but not the structure; import it from a new
analysis to keep that. Deploying a selection of dates copies the values into a new analysis, so
editing the project's date collection afterwards never changes an analysis already created, and the same
dates can be deployed more than once to model them differently each time.

From a project page you can also re-run every analysis in it at once, or re-run, duplicate, or edit
the inputs of a single analysis (editing replaces its saved inputs and re-runs it in place, keeping
the same result page). Ticking two or more finished analyses and pressing **Compare** opens them
side by side; see [Comparing Analyses](#comparing-analyses). 

Deploying dates from a collection always creates a **Bayesian** analysis and opens its editor, but
by changing the Analysis Type you can instead do a group calibration. Choosing Single
Calibration for more than one date automatically stores it as a group calibration, since a single calibration is
one date on its own.

## Comparing Analyses

The same dates can be run in different analyses with different parameters. **Compare** puts them side by side and
computes the difference, which is otherwise a handful of numbers buried in two long tables.

Tick two to four finished analyses on a project page and press **Compare**. **The first one you
tick is the reference**: every shift and every ratio on the page is measured against it, and the
other runs are what is being held up to it. The comparison is an ordinary URL, so it can be
bookmarked or sent to a colleague.

**Reporting format** and **HPD Confidence** are set once at the top and apply to every panel . A comparison whose
runs used **different calibration curves** is allowed but captioned: it is a real question to ask,
but then the shift measures the curve rather than the model. It is best to keep all other things equal in such a comparison.
This is also true for comparing things like Delta_R values, or even the choice of solver. The Exact Solver will produce slightly 
different results from the MH/Gibbs sampler, and you can measure that difference using **Compare**, but only if you isolate it as a parameter.

### Dates

One row per date, one HPD bar per analysis in that row, all on one calendar axis, with a hairline
dropped down each row at the reference run's median to read the others against. A bar is drawn in
segments where the region is disjoint.

Rows sort by **largest shift first** by default, so the dates these analyses disagree about come to
the top and the ones they agree on fall away. **Reference analysis order** and **Name** can be selected as different ordering options.

Three columns beside each row:

- **Δ median**: this run's median minus the reference's, in years, **positive meaning older**.
  Did the answer change?
- **Width ratio**: the width of this run's HPD region divided by the reference's. Below 1 is
  tighter. Did constraining the date buy anything?
- **Agreement**: each run's agreement index, listed only for runs that compute one (a group
  calibration has no model for a date to agree with).

The two questions come apart constantly. A model that moves nothing while halving every interval is
a good result that a plain overlay would render as "no change".

**Click a row** and it expands into the distributions themselves: each run's posterior drawn over
the others, with the unconstrained calibration behind them in grey where every compared run is
modelled. This allows you to check whether e.g. a run that appears to have "moved the date 140 years" 
has really just moved probability *mass* from one lobe to another. This can't be seen in the interval plots.
Every curve is scaled to unit area, so a tighter posterior is correctly taller.

Dates that could not be paired are listed under **Not compared** rather than dropped. A date is
matched to its counterpart by **sample name** (case and spacing are ignored), so a date deployed
from a project collection matches, and one re-typed under a different name does not. A name
appearing twice inside one analysis is not compared at all: which of the two the other run's date
corresponds to is not decidable.

### Models

The second tab asks the other question: *I changed the model, was it
worth it, and what did it cost*.

**Model differences** is read out of what each analysis stored:
curve, ΔR, outlier models, age limiters, structure, solver, and the draw count behind any durations.
A field the runs disagree on is marked **±**, one they share **=**. Under it the model trees are
matched element by element and drawn side by side, with a chip for anything that **moved** to a
different parent or is **only in** some of the runs. Elements are matched by type and name; an
unnamed element is counted but not matched, since the only other thing to match it on is position.

**Findings**
Compares the calendar estimates for each transition, and their durations. Obviously not all models have 
the same boundaries, so a boundary in the reference model that is missing in the comparison is drawn
with a dashed line where the other runs would be, and reads **no counterpart**. Two runs that list the
same pair of phases in opposite order are left unpaired too: every figure in one is the negative of
the other, which is a structural difference worth seeing rather than something to be negated into a
match.

**Date agreement changes** plots each run's agreement index against the reference's, one point per date. The
diagonal is "this date fits no better and no worse than before"; below it, worse, and dotted rules
mark the conventional threshold of 60 on both axes. This is the half of a comparison the Dates tab
cannot show: a date that appears there as a clean 140-year gain in
precision can sit well below the diagonal here, because the precision was bought by overruling the
date rather than by learning something about it.


## Best Practices

1. **Sample data**: use consistent units (age always in years BP), and double-check stratigraphic
   order before building a model structure around it.
2. **Calibration curves**: IntCal20 for Northern Hemisphere terrestrial samples, SHCal20 for
   Southern Hemisphere terrestrial, Marine20 for marine samples. Use the most recent curve version.
   For a sample whose carbon came from more than one reservoir, build a mixed curve rather than
   choosing the nearer of the two; see [Calibration Curves](#calibration-curves).
3. **Marine corrections**: consult the literature for an appropriate local ΔR; document its source
   in the sample's notes.
4. **Reading diagnostics**: the agreement index compares a date's modelled posterior against its
   own unconstrained calibration (100 = the model left it alone; below ~60 is conventionally worth a
   second look). It flags a date the model had to move, which is not automatically the same as a
   date that's simply wrong; see the discussion in `backend/app/services/agreement.py` if you're
   using it to hunt for outliers specifically.
5. **Check the curve before commissioning the dates**: the browser's expected-resolution panel
   says how tightly a date from your period can be known at best. If your question needs finer
   resolution than the curve can give there, no laboratory precision will supply it, and it is
   better to know that before the samples are sent than after.
6. **Compare runs on both tabs, not one**: a model that tightens every interval has not
   necessarily improved anything. Read the Dates tab for what moved and the Models tab's agreement
   scatter for what it cost; a date that gained precision by being overruled looks like a success on
   the first and a problem on the second. And do not read a difference between two runs as a
   probability; see [Comparing Analyses](#comparing-analyses).
7. **Wide Boundaries on a short phase are expected**: a Phase's Start/End Boundaries genuinely need
   four or more dated members before their span is even mathematically well-constrained; two or
   three dates in a phase can produce a very wide 95% range and that's correct, not a bug.

## Appendix A: Mathematical Methods

This appendix describes how each number the app reports is computed, and cites the source of every
method taken from the literature. Symbols follow the code: `t` is a calendar age in cal BP, `b` a
Boundary parameter, `L` a likelihood, and `K` the number of dates in a group. Full references are in
the [Bibliography](#bibliography) at the end.

### A.1 Conventions

**Calendar ages** are held internally as cal BP throughout, "present" being AD 1950 by convention
(Stuiver and Polach 1977). Conversion to BC/AD or BCE/CE is presentation only: `year = 1950 - calBP`,
with no year zero, so 1 BC is followed by AD 1.

**Confidence levels** are the two-sided normal coverages of one, two and three standard deviations,
computed rather than rounded:

```
sigma_1 = erf(1/sqrt(2)) = 0.682689...   reported as 68.3%
sigma_2 = erf(2/sqrt(2)) = 0.954500...   reported as 95.4%
sigma_3 = erf(3/sqrt(2)) = 0.997300...   reported as 99.7%
```

The distinction is not cosmetic. The level is the threshold an HPD set is accumulated to, so a
truncated 0.95 selects a different set of grid points from the exact 0.954500: over thirty IntCal20
calibrations between 500 and 10,000 BP, the two differed in 27 cases, by up to 10 years.

**Reporting resolution** is not one number, because the three kinds of range are built from
different things:

| Range | Reported on | Why |
| --- | --- | --- |
| Unmodelled calibration | the curve's own grid | the posterior is exact at every grid point the curve provides |
| Modelled date or Boundary | a 5-year grid | independent of the solver's internal step, which varies with the model (see [A.4](#a4-summarising-a-posterior)) |
| Duration or difference | an adaptive step, reported with the figure | computed from draws, so the step is set by what the draws can support (see [A.9](#a9-durations-and-differences)) |

So a single calibration or the unmodelled column of a Bayesian result can and does return ranges
that are not multiples of five: SHCal20 is tabulated at 1-year steps through the late Holocene, and
a range there is quoted to the year. Only the *modelled* posterior is rebinned. Point estimates and
plots always use the full-resolution posterior.

### A.2 Calibrating one date

The probability method of Stuiver and Reimer (1993), in the form OxCal uses (Bronk Ramsey 2001,
eqs. 1–2). For a measurement `a ± sigma_a` against a curve giving radiocarbon age `mu(t)` with
1-sigma curve error `sigma_c(t)`:

```
sigma_combined(t)^2 = sigma_c(t)^2 + sigma_a^2 + sigma_dR^2

p(t) proportional to  exp( -(mu(t) - (a - dR))^2 / (2 sigma_combined(t)^2) )
                      / sigma_combined(t)
                      * w(t)
```

Three details carry weight:

- **The `1/sigma` factor is kept**, not just the exponential. Curve error varies substantially with
  calendar age, and omitting the normalising term biases the posterior toward stretches of curve
  where the curve itself is poorly known. This is the correction CALIB made and OxCal inherited.
- **`w(t)` is the calendar-year width each curve grid point stands for** (half the distance to each
  neighbour). IntCal20 and its relatives are tabulated at 1, 5, 10 and 20-year steps in different
  ranges, so summing the likelihood unweighted would concentrate posterior mass wherever the curve
  file happens to be densely sampled.
- **`dR` is the local marine reservoir offset**, applied as a shift to the measured age because the
  marine curve models only the global average. Its uncertainty `sigma_dR` joins the quadrature sum,
  so a poorly known offset widens the calibrated range rather than only moving it.

Curve values at non-grid calendar ages are linearly interpolated in both `mu` and `sigma_c`, and
clamped at the curve's ends rather than extrapolated.

The built-in curves are IntCal20 (Reimer et al. 2020), SHCal20 (Hogg et al. 2020) and Marine20
(Heaton et al. 2020).

### A.3 Mixed curves

For a sample whose carbon came from two reservoirs (the classic case being a partly marine diet),
the app builds the curve OxCal's `Mix_Curves` builds:

```
mu_mix(t)    = p mu_A(t) + (1 - p) mu_B(t)
sigma_mix(t) = sqrt( p^2 sigma_A(t)^2
                   + (1-p)^2 sigma_B(t)^2
                   + ((mu_A(t) - mu_B(t)) sigma_p)^2 )
```

`p` is the proportion from the first curve and `sigma_p` its 1-sigma uncertainty. The third term
under the root is what makes an uncertain diet widen the date: it is largest where the two curves
are furthest apart, which is exactly where being wrong about the proportion costs most. The mixed
curve is defined on the union of the two parent grids, restricted to their overlap.

### A.4 Summarising a posterior

**HPD regions.** Grid points are taken in order of decreasing probability density until the target
coverage is reached; the selected points are then grouped into maximal runs, each reported as a
range with its own share of the total probability. Because calibration curves are not monotonic, a
single date routinely produces several disjoint ranges, and reporting only the outer envelope would
claim probability in the near-empty gaps between them. The percentages are shares of the entire
distribution, so they sum to slightly more than the level requested: the set is built from whole
grid points and the last one taken overshoots. This is standard radiocarbon reporting practice
(Stuiver and Reimer 1993; Bronk Ramsey 2009a).

**Point estimates** are computed from the density, not from draws, so they carry no Monte Carlo
error even on the sampled path: mean and standard deviation as probability-weighted moments, median
by interpolating the CDF at 0.5, mode as the highest-density grid point.

**Rebinning, for modelled posteriors only.** A modelled posterior is multiplied by the model's own
constraints on top of an already-wiggly likelihood, and comes off a grid whose step the solver
chooses from the date range and the number of groups. Two problems follow. Reporting at that step
would make a published range depend on an implementation detail, and the same dates in a longer
sequence would come back with different widths. At a fine step the HPD picks out peak tops and
skips the dips between them.

Modelled ranges are therefore rebinned to the 5-year reporting step before the HPD is taken.
Unmodelled calibration is *not* rebinned. It has no solver grid to hide, and the curve's own
spacing is the honest resolution of the answer.

The results page re-selects the region in the browser when you change the confidence selector, since
only 68.3% and 95.4% are stored and 99.7% is offered as well. That re-selection applies the same
rebinning, so the table, the plots, the Harris matrix, the exports and the stored result all report
one set of bounds.

### A.5 The Bayesian model

The model is the one introduced by Buck et al. (1991, 1992) and set out at length in Buck, Cavanagh
and Litton (1996): the calibration likelihoods multiplied by a prior expressing the archaeological
relationships. In the form the app implements, following OxCal (Bronk Ramsey 1995, 2009a):

```
p(t, b)  proportional to  prod_dates L_d(t_d)
                        * prod_groups (b_older,g - b_younger,g)^(-K_g)
                        * c(t, b)
```

- `L_d` is date `d`'s own calibration likelihood from A.2.
- The **span factor** `(older - younger)^(-K)` is the uniform phase model: conditional on the pair of
  Boundaries enclosing it, each of a group's `K` dates is uniformly distributed between them. Each
  date belongs to exactly one bounded group, the innermost enclosing pair, so a nested group's
  dates are not also counted in its parent's span term.
- `c(t, b)` is the indicator that every ordering constraint holds: sequence order, stratigraphic
  relationships, and any TPQ/TAQ limits. Constraints are hard truncations, not soft penalties.
  Stratigraphic relationships are expanded to pairwise sample constraints by the law of
  superposition (a sample above another was deposited later and is younger) and displayed as a
  version of the Harris matrix (Harris 1979), oldest at the bottom. Where a sample and its group both carry a
  TPQ or TAQ, the tighter of the two wins.

**Boundaries** are single shared parameters. A Boundary placed between two Phases is simultaneously
the end of the earlier and the start of the later, not two independent transitions. This is why
adjacent phases have no gap to measure (see [Differences](#differences)). A deliberate gap is
modelled as two Boundaries with a Hiatus between them.

**Uniform span prior.** Placing `n` ordered Boundaries across a span leaves the `n-2` interior ones
free within it, and the induced prior therefore favours long sequences purely for having more room
to arrange them in. Dividing that freedom out is the correction of Nicholls and Jones (2001), and is
OxCal's default. The app divides by the exact discrete count

```
C(span in grid cells - 1, n - 2)
```

rather than by the continuum `span^(n-2)`. The two agree only as the span grows large, and the prior
concentrates mass at small spans where they differ most; with the likelihoods switched off, so the
reported posterior is the prior and the answer is known in closed form, the binomial reproduces it
to 0.00% total-variation distance where the power form was in error by 0.4–1.6%.

The same option carries the limits factor `1/g` of Bronk Ramsey (2008, appendix A.31; 2009a
eqs. 29–30), which accounts for the finite window the outer boundaries may occupy:

```
g = min( (ulim_m - llim_a) - (t_m - t_a),
         (t_m - t_a) - (llim_m - ulim_a),
          ulim_a - llim_a,
          ulim_m - llim_m )
```

Which of the four terms is smallest decides which way the term biases the span. It is not
negligible: on eight overlapping dates in one phase it is worth about 18% of the reported span, and
it narrows.

### A.6 Solvers

The app chooses between two solvers by model shape. Both target the same posterior, and are checked
against each other on models both can handle.

**Exact integration** (chain-shaped models: a Sequence of Phases separated by Boundaries). Every
date other than the one being marginalised enters only through its likelihood integrated over an
interval, which a cumulative sum supplies for all intervals at once. What remains is a chain over
the Boundaries, solved by forward/backward message passing:

```
T_i(b_i, b_i+1) = (b_i - b_i+1)^(-K_i) prod_{j in group i} F_j(b_i+1, b_i)
alpha_i+1(b)    = sum over b_i > b  of  alpha_i(b_i) T_i(b_i, b)
beta_i(b)       = sum over b_i+1 < b of  T_i(b, b_i+1) beta_i+1(b_i+1)
```

This is O(M²) per group on an M-point calendar grid, deterministic, and exact up to the grid step.
There is no sampler to converge and no mode to miss, which matters because resolving which of
several calibration lobes is right is much of the reason to build a model at all.

The interval integrals `F` are **trapezoidal**, with half weight on each endpoint cell. This is not a
refinement: a plain cumulative difference gives an interval spanning `k` cells the mass of `k+1`
cells, while the span factor charges `k`, so near-coincident configurations gain `2^K` and every
modelled phase comes out several times too narrow.

**Metropolis-Hastings / Gibbs sampling** (everything else) is the scheme OxCal uses (Metropolis et
al. 1953; Hastings 1970; Geman and Geman 1984; applied to this problem by Buck et al. 1992 and Bronk
Ramsey 1995). Each full conditional is one-dimensional and exactly samplable: a date's conditional
is its own likelihood over the interval its neighbours and boundaries allow (the span factor drops
out, being independent of where within the interval the date sits); a Boundary's is the product of
the span factors of the groups it bounds. The app draws these by inverse CDF on a precomputed
cumulative table rather than by rejection as OxCal does: the same target distribution, at bounded
cost per draw. Conditionally independent dates are updated in blocks, and whole-group translation
moves are proposed on the exact MH ratio so a tightly constrained group can cross between
calibration lobes, which single-site updating cannot do.

**Convergence** is OxCal's own diagnostic: the overlap integral between the distribution accumulated
over the first half of the run and over the second half, per parameter, targeting 0.95. The run
extends until the worst parameter meets the target or the sweep ceiling is reached, and the achieved
value is always reported.

### A.7 Outlier models

A Bernoulli outlier indicator is integrated out per date, which is exact and costs the exact solver
nothing because the indicator touches only that date's own likelihood:

```
L_mix(t) = (1 - p) L_inlier(t) + p L_outlier(t)
```

`p` is the prior outlier probability set on the date. The outlier component is the inlier likelihood
convolved with an offset kernel, following Bronk Ramsey (2009b), whose treatment builds on Christen
(1994):

- **General**: a Student-t offset with 5 degrees of freedom, scale `10^u` with `u` uniform on
  `[0, 4]`. Symmetric: the date may be too old or too young.
- **Charcoal**: a one-sided exponential gap truncated at 10 scale lengths, scale `10^u` with `u`
  uniform on `[0, 3]`, i.e. OxCal's `Outlier_Model("Charcoal", Exp(1,-10,0), U(0,3), "t")`. The mass
  sits on the side that says the dated material formed *before* the context that held it.

The scale is integrated over its prior on a 24-node log grid. On the exact path this is done
independently per date, which is an approximation: OxCal shares one scale across every date using
the model and lets the data constrain it, and a shared hyperparameter does not factorise along the
message passing. The sampler does share it properly. The cost of the approximation is small where
outlier priors are small: on a real OxCal comparison the exact path's outlier probabilities sit
0.017 from OxCal's where the sampler reaches 0.002.

**Reported outlier probability** is the posterior probability that the date is an outlier, obtained
by averaging the outlier component's responsibility over the date's own posterior:

```
P(outlier) = integral  posterior(t) * p L_out(t) / L_mix(t)  dt
```

The outlier component is deliberately *not* renormalised over the computational window. Its
convolution is already proper on the real line, and most of its mass lies off the grid, which is
precisely the statement that a flagged date's true age could be anywhere. Rescaling the remainder to
one would make the reported probability a function of the window width.

### A.8 Agreement indices

The standard OxCal diagnostic (Bronk Ramsey 1995), comparing each date's modelled posterior against
its own unmodelled likelihood over a shared calendar grid:

```
A_i = 100 * sum(modelled * unmodelled) / sum(unmodelled^2)
```

A date the model leaves alone scores 100. One the model had to push off its own likelihood scores
low; one the model *sharpens* onto the peak of its likelihood scores above 100, so values over 100
are expected. Below about 60 is the conventional threshold for a second look. The overall index
combines them:

```
A_overall = 100 * (product of A_i/100)^(1/sqrt(n))
```

Both are computed on a 5-year grid, matching OxCal's export resolution, and both distributions are
normalised over a window spanning *both*; normalising the likelihood over only the posterior's
support would let a badly displaced date score well on the sliver where the two still overlap.

Against 24 known answers from OxCal 4.4 across three models, the individual indices reproduce to
rms 0.81 and worst 1.4, the overall ones to 0.1.

Note the limits of the diagnostic: it detects a date the *model had to move*, which is not the same
as a date that does not belong. On an unconstrained phase, a date 600 radiocarbon years from its
neighbours scores 100.5, because the phase simply widens. Use an outlier model to find a date that
is merely wrong.

OxCal's `Amodel` is deliberately not implemented: it cannot be derived from the per-date indices,
and a guess at the headline number people screen on would be worse than reporting nothing.

### A.9 Durations and differences

These are functions of several modelled ages at once, so they are computed from **joint** draws of
the posterior, never by pairing up marginals. The distinction is not subtle: pairing draws from two
boundary marginals on a single six-date phase puts 8.2% of them below zero, a phase ending before
it starts, where the joint posterior gives that no probability at all.

On the exact path the draws are independent draws from the true joint posterior, obtained by
forward-filtering/backward-sampling over the same chain the solver already built (Frühwirth-Schnatter
1994; Carter and Kohn 1994), so the Monte Carlo error is a plain `1/sqrt(N)` with no autocorrelation.
The default is 20,000 draws. These are the only Monte Carlo figures the exact solver reports, which
is why the draw count and whether the draws were independent are printed alongside them.

**The three durations** are OxCal's quantities under the app's own names: the *Sequence duration*
(first Boundary minus last), the *Phase duration* (the interval a pair of Boundaries encloses,
OxCal's `Interval`), and the *dated-events* or sample duration (the range of the dates themselves,
OxCal's `Span`). The gap between the last two is how much of the phase the dates failed to sample.

**Differences** between two phases are the signed years between their modelled intervals, positive
for a gap and negative for an overlap, computed as one quantity from the intersection length of the
two intervals in each draw. Between two dates in a group calibration, the signed difference in
calendar age.

**Overlap** is a probability, and means two slightly different things because a phase is an interval
and a date is a point:

```
two phases   P(their modelled intervals intersect at all)
two dates    P(the ordering is unresolved) = 2 min( P(A older), P(B older) )
```

Both run 0 to 1 and are read the same way. The five grades are the app's own reporting confidences
rather than round numbers: the band edges are `1 - sigma_2`, `1 - sigma_1`, `sigma_1` and `sigma_2`,
so `none` means separated at 2 sigma and `extensive` means overlapping at 2 sigma.

**Reporting resolution.** A duration posterior can be thousands of years wide, so quoting it at the
5-year calendar step would claim a resolution the draws do not have. The histogram is capped at 40
bins, with the bin width taken from the 0.5–99.5 percentile spread rather than the full range (so
that asking for more draws cannot coarsen the answer by finding a more extreme one). The step
actually used is reported with every figure.

### A.10 Expected curve resolution

How tightly a date from a given calendar age can be known, before anyone measures anything. The
method is Svetlik et al. (2019), as implemented in Demjan's `res14c`; this app's version was written
from the published method and its own calibration code rather than translated, and reproduces the
reference implementation's published figures to about 0.3% over 1,000–4,000 cal BP, including the
sharp 33-year trough at 2,750 cal BP.

1. Scale the laboratory uncertainty with age, `u(a) = u0 exp(a / 16066)`, because counting
   statistics degrade as less ¹⁴C remains. The constant is twice the 8033-year mean life implied by
   the Libby half-life on which conventional radiocarbon ages are reported (Stuiver and Polach 1977).
2. Calibrate each radiocarbon age a sample of that period could plausibly return.
3. Take the width of its 95.4% HPD envelope (first to last point, gaps included) as the resolution
   that measurement achieved.
4. For each calendar year, average those widths over the radiocarbon ages a sample of that true age
   could yield, weighted by how likely each is, giving a mean and a 5th–95th percentile band.

The default assumed laboratory uncertainty is 25 years, what a commercial date typically carries,
rather than the reference implementation's 15, which is close to the best AMS can offer.

### A.11 Curve display

Two presentation choices in the curve browser are worth stating, since both change what you see.

**Decimation is min/max per bucket**, not every Nth point. Stride sampling removes exactly the
extremes that make a curve wiggly: plateau reversals, short excursions, the features that split a
calibrated date into two ranges. Keeping the lowest and highest radiocarbon age in each bucket
preserves the envelope at any zoom.

**Local slope** is measured over ±50 calendar years on the full grid, before decimation. A
point-to-point gradient on IntCal20's 1-year grid is dominated by year-to-year scatter and swings
between +4 and −3 across stretches that are flat at any scale that matters; ±50 separates a plateau
from ordinary curve threefold without over-smoothing.

### A.12 Comparing two runs

Everything on the comparison page is computed in the browser from the payloads the two analyses
already return; no solver runs again, and no figure is derived that is not a function of the two
runs' own published summaries.

**Matching.** Dates are keyed on `sample_name`, normalised for case and internal whitespace. This is
the strongest key available: a date deployed from a project collection is a *copy*, carrying no
reference back to the collection entry, so no identifier is shared between two analyses of the same
dates. Boundaries, durations and separations are keyed on kind and name; separations on the ordered
pair, since reversing it negates every figure in the record. Nothing anywhere is matched by
position.

**The two figures reported per date** are

```
delta_median = median_run - median_reference        years, positive = older
width_ratio  = |HPD_run| / |HPD_reference|          at the selected confidence
```

where `|HPD|` is the outer span of the region, computed through the same selection the results
table uses, so a width quoted here is the width printed there. For a modelled posterior that
selection rebins to the 5-year reporting step (A.4); for an unmodelled calibration it works on the
curve's own grid. A duration's region is selected from its own histogram at its own reported step
(A.9), never rebinned to a calendar step.

**Why no joint statistic is offered.** A statement of the form `P(this date is older under run B
than under run A)` requires the joint distribution of the two runs' parameters. That distribution
was never sampled: each run produced draws from its own posterior, independently, and the two sets
of draws have no correspondence: draw 1 of run A and draw 1 of run B are not the same
hypothetical past. Pairing them would manufacture a dependence structure out of nothing, in the
same way that pairing two boundary marginals manufactures phases that end before they start (A.9).
A difference of medians and a ratio of interval widths are functions of two summaries and need no
such structure, which is why those are what the page reports.

## Bibliography

- Bronk Ramsey, C. (1995). Radiocarbon calibration and analysis of stratigraphy: the OxCal program.
  *Radiocarbon* 37(2), 425–430.
- Bronk Ramsey, C. (1998). Probability and dating. *Radiocarbon* 40(1), 461–474.
- Bronk Ramsey, C. (2001). Development of the radiocarbon calibration program. *Radiocarbon* 43(2A),
  355–363.
- Bronk Ramsey, C. (2008). Deposition models for chronological records. *Quaternary Science Reviews*
  27(1–2), 42–60.
- Bronk Ramsey, C. (2009a). Bayesian analysis of radiocarbon dates. *Radiocarbon* 51(1), 337–360.
- Bronk Ramsey, C. (2009b). Dealing with outliers and offsets in radiocarbon dating. *Radiocarbon*
  51(3), 1023–1045.
- Buck, C. E., Kenworthy, J. B., Litton, C. D., and Smith, A. F. M. (1991). Combining archaeological
  and radiocarbon information: a Bayesian approach to calibration. *Antiquity* 65(249), 808–821.
- Buck, C. E., Litton, C. D., and Smith, A. F. M. (1992). Calibration of radiocarbon results
  pertaining to related archaeological events. *Journal of Archaeological Science* 19(5), 497–512.
- Buck, C. E., Cavanagh, W. G., and Litton, C. D. (1996). *Bayesian Approach to Interpreting
  Archaeological Data*. Chichester: Wiley.
- Carter, C. K., and Kohn, R. (1994). On Gibbs sampling for state space models. *Biometrika* 81(3),
  541–553.
- Christen, J. A. (1994). Summarizing a set of radiocarbon determinations: a robust approach.
  *Journal of the Royal Statistical Society Series C (Applied Statistics)* 43(3), 489–503.
- Frühwirth-Schnatter, S. (1994). Data augmentation and dynamic linear models. *Journal of Time
  Series Analysis* 15(2), 183–202.
- Geman, S., and Geman, D. (1984). Stochastic relaxation, Gibbs distributions, and the Bayesian
  restoration of images. *IEEE Transactions on Pattern Analysis and Machine Intelligence* 6(6),
  721–741.
- Harris, E. C. (1979). *Principles of Archaeological Stratigraphy*. London: Academic Press.
- Hastings, W. K. (1970). Monte Carlo sampling methods using Markov chains and their applications.
  *Biometrika* 57(1), 97–109.
- Heaton, T. J., Köhler, P., Butzin, M., Bard, E., Reimer, R. W., Austin, W. E. N., Bronk Ramsey, C.,
  et al. (2020). Marine20 — the marine radiocarbon age calibration curve (0–55,000 cal BP).
  *Radiocarbon* 62(4), 779–820.
- Hogg, A. G., Heaton, T. J., Hua, Q., Palmer, J. G., Turney, C. S. M., Southon, J., et al. (2020).
  SHCal20 Southern Hemisphere calibration, 0–55,000 years cal BP. *Radiocarbon* 62(4), 759–778.
- Metropolis, N., Rosenbluth, A. W., Rosenbluth, M. N., Teller, A. H., and Teller, E. (1953).
  Equation of state calculations by fast computing machines. *Journal of Chemical Physics* 21(6),
  1087–1092.
- Nicholls, G., and Jones, M. (2001). Radiocarbon dating with temporal order constraints. *Journal of
  the Royal Statistical Society Series C (Applied Statistics)* 50(4), 503–521.
- Reimer, P. J., Austin, W. E. N., Bard, E., Bayliss, A., Blackwell, P. G., Bronk Ramsey, C., et al.
  (2020). The IntCal20 Northern Hemisphere radiocarbon age calibration curve (0–55 cal kBP).
  *Radiocarbon* 62(4), 725–757.
- Stuiver, M., and Polach, H. A. (1977). Discussion: reporting of ¹⁴C data. *Radiocarbon* 19(3),
  355–363.
- Stuiver, M., and Reimer, P. J. (1993). Extended ¹⁴C data base and revised CALIB 3.0 ¹⁴C age
  calibration program. *Radiocarbon* 35(1), 215–230.
- Svetlik, I., Jull, A. J. T., Molnár, M., Povinec, P. P., Kolář, T., Demján, P., et al. (2019). The
  best possible time resolution: how precise could a radiocarbon dating method be? *Radiocarbon*
  61(6), 1729–1740.

**Software.** Demján, P. `res14c` — reference implementation of the Svetlik et al. resolution
method, https://github.com/demjanp/res14c_online. OxCal (C. Bronk Ramsey), against whose 4.4 output
this app's agreement indices, outlier probabilities and modelled ages are validated:
https://c14.arch.ox.ac.uk/oxcal.html
