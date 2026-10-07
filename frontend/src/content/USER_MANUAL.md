# User Manual

## Table of Contents

1. [Getting Started](#getting-started)
2. [Creating an Analysis](#creating-an-analysis)
3. [Adding Dates](#adding-dates)
4. [Importing Dates from a File](#importing-dates-from-a-file)
5. [Calibration Curves](#calibration-curves)
6. [Marine Reservoir Offsets](#marine-reservoir-offsets)
7. [Inbuilt Age](#inbuilt-age)
8. [Model Structure (Bayesian only)](#model-structure-bayesian-only)
9. [Running an Analysis](#running-an-analysis)
10. [Viewing Results](#viewing-results)
11. [Durations and Differences](#durations-and-differences)
12. [Kernel Density Estimates](#kernel-density-estimates)
13. [Same-Event Test](#same-event-test)
14. [Wiggle Matching](#wiggle-matching)
15. [Age-Depth Modelling](#age-depth-modelling)
16. [Geographic Analysis](#geographic-analysis)
17. [Exporting Data](#exporting-data)
18. [Projects and Date Collections](#projects-and-date-collections)
19. [Comparing Analyses](#comparing-analyses)
20. [Best Practices](#best-practices)
21. [Appendix A: Mathematical Methods](#appendix-a-mathematical-methods)
22. [Bibliography](#bibliography)

## Getting Started

1. Open RadiCal in your browser: `http://localhost:8000` on the computer it is installed on, or
   your deployment's address.
2. Click **New Analysis** (top navigation bar, or from the Dashboard).

## Creating an Analysis

New Analysis is a single form: settings on the left, measurements on the right, the measurement
list and model structure (for a Bayesian analysis) below, and a Run Analysis button at the bottom.

- **Analysis name**: required.
- **Analysis type**:
  - **Single Calibration**: one date, calibrated on its own. An extra "Save this analysis"
    toggle lets you run a quick test without creating anything in a project.
  - **Group Calibration**: several dates, each calibrated independently. No stratigraphic
    constraints; this is direct calibration, not a Bayesian model.
  - **Bayesian**: one or more dates modelled together with stratigraphic constraints. See
    [Model Structure](#model-structure-bayesian-only).
- **Project**: optional. Assigning a project makes its date collection available and adds this
  analysis to the project's list.
- **Result date format**: how calendar ages are displayed (e.g. cal BP, BC/AD). This changes
  labelling only; the underlying calibration is unaffected.

## Adding Dates

Each date needs, at minimum, a **Sample name**, **Age (BP)** and **1σ error**. Also available:

- **Material**: free text (charcoal, bone, shell, etc.).
- **Sample type**: Terrestrial or Marine. Marine samples show an extra **ΔR** (local reservoir
  offset) field with its own 1σ error; both are required together, since they are combined in
  quadrature with the measurement's own uncertainty.
- **Curve**: IntCal20 (Northern Hemisphere terrestrial), SHCal20 (Southern Hemisphere
  terrestrial), Marine20, and any curve you have imported or mixed. A marine sample should be
  calibrated against a marine curve — the offset is centuries otherwise. See
  [Calibration Curves](#calibration-curves).
- **Outlier settings** (Bayesian analyses only): an optional prior probability that this date
  does not belong to the modelled population, with a choice of outlier model (general vs.
  charcoal, i.e. one-sided for material likely to predate its context).

If a project is assigned and its date collection already has dates, an **Add from Project's
Date Collection** button appears above the measurement form. Pick any subset to copy into this
analysis.

## Importing Dates from a File

**Import from a File** (on New Analysis, and **Import File** on a project's Dates tab) reads a
spreadsheet of dates or an OxCal model. The file is parsed first and shown for review; confirming
places them where hand-typed dates go.

An import is **all or nothing**. One unreadable row fails the whole file, with every problem listed,
rather than silently importing a partial set.

### CSV

One date per row with a header row naming the columns. Only `sample_name`, `age_bp` and
`error_1sigma` are required; everything else takes the same default a typed-in date would.
Column **order is free** and common spellings are understood: `Lab Code`, `Age BP`,
`± 1σ`, `sd`, `Material`, `Curve`, `Context` all resolve correctly. Comma, tab and
semicolon separators all work.

Use **Download template** for a file with the full column list and three worked example rows.
The optional columns are `material_type`, `curve_id`, `sample_type`, `delta_r`,
`delta_r_error`, `phase`, `tpq_age_bp`, `taq_age_bp`, `outlier_model`, `outlier_prior`, `depth_cm`,
`location` and `notes`.

The `phase` column prepares dates for use in a model. Dates sharing a phase name are
grouped, and a starting structure is built: one Start/End pair with the phases
**unordered** inside it, since a CSV cannot express which group came first. Ordering them is
then your decision in the Model Structure editor.

The app's own results export is readable back in — structural rows and posterior columns are
recognised and skipped — so you can export a table, correct it in a spreadsheet, and re-import.

### OxCal (.oxcal)

An OxCal model file is read directly. Its `Sequence`, `Phase`, `Boundary` and `Hiatus` structure,
per-scope `Curve` and `Delta_R`, and per-date `Outlier` settings all come across, keeping their
OxCal names. Importing from New Analysis brings the model structure with it.

A reservoir offset can be written in the ways OxCal allows: in full, `Delta_R("LocalMarine",-162,28)`,
or stated once under a name and cited wherever it applies, `Delta_R("=LocalMarine", )`. The offset
belongs to the stated curve (OxCal applies `Delta_R` to the current `Curve`), so a phase
holding marine dates on Marine20 and terrestrial ones on SHCal20 gives the offset to the marine
dates only. The preview shows the ΔR carried by each date and how many dates each named
offset reached.

`Options(){ Resolution=n; };` sets the analysis's **Resolution** (see
**Model settings** under [Model Structure](#model-structure-bayesian-only)). Other options
like `kIterations` are skipped with a note.

`D_Sequence` and `V_Sequence` come across as a **wiggle match**, with each `Gap()` turned into a
distance from the youngest member; `P_Sequence` comes across as an **age-depth
model**, reading each element's `z=` as its depth below the surface. A `Date()` with a `z=` inside a
`P_Sequence` becomes an undated level — the "how old is 47 cm" that an age-depth model answers.
A `Date()` anywhere else is refused, having nothing to bound it.

A simulated date, `R_Simulate("name", -1000, 25)`, comes across as an ordinary date whose
radiocarbon age is the curve's own value at that calendar year (OxCal's numbering: negative is BC),
plus any ΔR in force, with the error given. OxCal adds random scatter to a simulated measurement,
so its results vary; this app omits the scatter, giving the same result every time. The date's
notes record that it was simulated and for which year.

OxCal's language is larger than what this app models. Anything that would change the posterior and
cannot be honoured **fails the import** with a message naming the unsupported feature:
`Tau_Boundary`, `Sigma_Boundary` and `Zero_Boundary` (non-uniform transitions), `C_Date`,
`R_Combine` (import the dates separately and pool them with
[Test as one event](#same-event-test)), `U_Sequence` (a different deposition prior from the `P_Sequence` this app implements),
an unavailable curve, or a custom `Outlier_Model`. A `D_Sequence` or `V_Sequence` nested inside a
larger model also fails — this app solves a wiggle match on its own, and reading its members as
ordinary dates in a phase would report a match that was never made. Unrecognised commands fail the
import too. Commands that only query a posterior (`Sum`, `KDE_Plot`, `Span`, `Difference`,
`Order`…) are skipped with a note, as are display attributes like `color="red";`.

When a model is refused but its dates can still be read, the import offers to bring in the dates
without the model. Each date keeps its curve, ΔR and outlier setting, and its Phase name becomes
its context; the dates are then calibrated independently. A problem with a date itself (e.g. an
unavailable curve) still stops the import.

Two differences in reading are made explicit: a date sitting directly inside a `Sequence` is placed
in a Phase of its own (position and ordering preserved), and several independent top-level items
are wrapped in one Phase, asserting no ordering between them.

## Calibration Curves

**Curves** in the top navigation lists every curve available, and is where you
add your own. Three are shipped (IntCal20, SHCal20 and Marine20) and cannot be edited or removed.

**Curves you import or mix are private to your account.** Other users on the same installation
cannot see them. Only the three shipped curves are shared. This is deliberate: a curve you are
still checking should not appear in a colleague's picker, and a curve added for one site years ago
should not become something a newcomer picks by mistake. To share a curve with a colleague, use
**Download** on the curve's row (which returns the exact bytes you imported), and have them import
it themselves.

Everything measured in the table is read from the curve file: its point count, calendar range, and
grid structure (IntCal20 steps 1 year up to 5,000 cal BP and 20 years beyond 25,000).

### Importing a curve

**Import Curve** takes an IntCal-format `.14c` file, or a CSV with calendar age, radiocarbon age
and 1σ error columns. Column names are matched regardless of spelling and order, and a calendar
column in BC/AD (negative for BC) can be converted on the way in.

The dialog reports the point count, ranges, assumptions made, and draws the curve with its ±1σ band.
Check that the curve draws correctly to confirm the rows and columns were read in the right order.

Files are refused rather than repaired, each refusal naming the row and reason: a non-positive
error, a duplicated calendar age, a radiocarbon column that looks like F¹⁴C rather than an age, or
a negative cal BP (post-bomb curves need a different likelihood, which this app does not implement).
Rows written oldest-first are simply sorted, as the IntCal files themselves are written that way.

Give a curve **aliases** if files refer to it by other names. An alias of `IntCal13` lets an
`.oxcal` model written against IntCal13 import once you have added that curve.

**A stored curve's numbers are permanent.** You can correct its name, citation, DOI, notes and
aliases, but correcting the *data* means importing the corrected file as a new curve. An
analysis run last month must keep meaning what it said. For the same reason, a curve that has been
used in a calibration cannot be deleted; the app offers to **archive** it instead, removing it from
curve pickers while leaving it resolvable for those older analyses.

### Mixed curves

**New Mixed Curve** builds a curve from two others in a fixed proportion, as per OxCal's `Mix_Curves`.
This is for a sample whose carbon came from more than one reservoir — most commonly a human or
animal with a partly marine diet: their bone is neither terrestrial nor marine carbon, and
calibrating against either curve alone is wrong by centuries.

State the proportion as a percentage of the first curve, and **give its uncertainty**. A dietary
fraction comes from stable isotopes and has real error, and that error widens the mixed curve most
where the two source curves are furthest apart — which is exactly where being wrong about the diet
moves a date furthest. Left at zero, the curve claims the proportion is known exactly.

An `.oxcal` file using `Mix_Curves` imports against a mixture you already have. If no match exists,
the import fails and names what you need to create.

### Browsing a curve

Clicking a curve's name opens the browser, which answers three questions.

**What shape is the curve, and how well known is it?** The chart draws the curve with its own ±1σ
and ±2σ bands, oldest on the left. The wiggles are why a date can split into two ranges, or spread
over three centuries.

**Where am I?** The strip under the chart shows the whole 55,000 years with your current view
marked. Drag it to move, drag an edge to zoom, click to jump; zoom buttons and presets cover the
whole curve, the Holocene, and the Hallstatt plateau.

**What is the curve doing here?** Pointing at the curve reports its value at that calendar age and
its **local gradient**, which governs how tightly anything can be dated there:

| Gradient | Reads as | What it means for a date |
|---|---|---|
| below −0.05 | reversal | the curve doubles back; one measurement matches several separate calendar ranges |
| −0.05 to 0.35 | plateau | nearly flat; one measurement spreads over a wide calendar span |
| 0.35 to 0.8 | shallow | flatter than calendar time; dates here spread |
| 0.8 to 1.6 | ordinary | the curve tracks calendar time; a date calibrates about as tightly as it was measured |
| above 1.6 | steep | rising faster than calendar time; dates here calibrate unusually tightly |

Three more controls sit in the header bar. **Compare with** overlays a second curve and adds a panel
showing their *difference* — useful because two curves drawn together look alike at most zoom levels,
while IntCal20 − SHCal20 shows the hemispheric offset (~40 ¹⁴C years) at a glance. **Dates as**
switches the calendar convention. **Expected resolution** is described next.

**Clicking a calendar year** shows what a date from it would actually look like: the curve's own
radiocarbon age there, calibrated back at a laboratory precision you choose. Inside the Hallstatt
plateau a ±25 year measurement comes back as four disjoint calendar ranges spread over 337 years;
a century and a half earlier, on the steep drop into it, the same precision gives one range of 37
years. This is the clearest statement of what a plateau costs, and no amount of laboratory precision
removes it.

### Expected resolution

Switching **Expected resolution** on adds a panel showing how tightly a date from each year could be
known *before anyone measures anything*. The line is the mean width of the 95.4% calibrated range;
the band is the 5th–95th percentile — how lucky or unlucky a single date can be.

The method is Svetlik et al. (2019): for every radiocarbon age a sample of that period could
plausibly return, the laboratory error is scaled with age, the measurement calibrated, and the width
of its calibrated range recorded; those widths are averaged over the measurements that period could
yield. **Lab ±1σ** sets the precision assumed (default ±25, a typical commercial date; ±15 is about
the best AMS offers, and is the figure the method's published curves use).

This serves as a planning tool. On IntCal20 at the default ±25, the Hallstatt plateau sits at about
330 years while the drop at its start is 103; at ±15, 312 against 33. The gap between positions on
the curve is far larger than variation in laboratory accuracy — where you are on the curve matters
more than what you pay for the measurement. **Export profile** saves the whole curve's profile as
CSV; **Export this window** saves the visible stretch of the curve.

## Marine Reservoir Offsets

A marine sample calibrates against Marine20, which already models the *global* average offset of
the surface ocean from the atmosphere. **ΔR** is the *local* deviation from that average, entered as
a value with its own 1σ uncertainty, both in ¹⁴C years. RadiCal accepts a ΔR in three ways.

**Typed.** On the measurement form, a Marine sample type shows the ΔR and ± fields; in a project's
date collection, tick dates and press **Set ΔR › Enter a value** to write one value to all of
them at once. Leave both fields empty to clear the offset. A marine date with no ΔR set calibrates
as ΔR = 0; the collection's ΔR column distinguishes these from corrected ones by showing **none set**.

**Imported.** An OxCal file's `Delta_R(...)` comes across with the dates it applies to; a CSV can
carry `delta_r` and `delta_r_error` columns.

**Derived from position.** A date with coordinates (see [Geographic Analysis](#geographic-analysis))
can have its ΔR read off the nearest published reservoir measurements, reproducing calib.org's
Marine Reservoir Correction Database combination rule to the printed integer (see
[A.15](#a15-deriving-a-reservoir-offset-from-position)).

### The reservoir database

The measurements come from Reimer & Reimer's Marine Reservoir Correction Database, mirrored on
this installation as a **snapshot** — frozen once stored and citable by id, like a calibration
curve, so a published date keeps meaning what it said. Nothing is shipped with the app: on the
**Curves** page, the **Marine reservoir database** card shows what is mirrored. **Check calib.org**
queries the source (one small request), and **Fetch the database** brings it down (~1 MB). Neither
happens automatically. Fetching a newer snapshot adds it beside the old one; dates citing the old
snapshot continue to cite it.

### Deriving a value

In a project's date collection, select the dates from **one site** — they must all be placed at
the same coordinate, since a centroid of two sites is a place neither of them is — and press
**Set ΔR › Derive from position**. The panel shows:

- **The value**, large, as `ΔR ± σ` from *N* points, with the database it came from.
- **Nearest points: N.** The weighted mean is taken over the *N* nearest reservoir measurements.
  The value changes with *N* — somewhere like Callao it moves by nearly 200 years across the
  slider's range. Adjust before accepting; the default of 10 matches calib.org's.
- **Distance to the nearest** (and farthest used) measurement. Beyond 100 km the line turns amber:
  the value is then a regional average rather than a local offset.
- **The point list**: each measurement's distance, ΔR, feeding habit and locality. Untick a point
  to exclude it. A point with no published uncertainty cannot be weighted and is shown struck
  through, so a nearest-10 that is really nearest-9 is visible.
- **Feeding habit.** Deposit feeders can take up old carbon from sediment, carnivores sit ~35 years
  older than nearby suspension feeders, and migratory animals may not have lived where they were
  found. These three habits are marked, and one line under the value gives the counterfactual —
  *Suspension feeders only (7 of 10): −188 ± 61* — as a link that swaps the value over. When the
  two differ by more than a published ΔR typically claims for itself, a warning appears. Grazers
  and unknown habits are not marked — they are indistinguishable from the suspension pool.
- **Apply to.** Where the selection can be widened, choose whether the value lands on the selected
  dates only, every date at the same named location, or every date within a distance of one.
  Terrestrial dates in the target are skipped, with a count shown.

**Apply** writes the rounded value and uncertainty to the dates, together with a derivation record
(snapshot id, centre, *N*, points used and excluded, suspension-only alternative). This record
travels with the date into every analysis.

## Inbuilt Age

A charcoal date gives the age of the wood when that wood stopped taking up carbon, not when it was
burnt. A twig is about as old as the fire. Heartwood from a long-lived tree may have stopped growing
decades before anyone burnt it. That gap is the **inbuilt age**. It can only make a date look
*older* than the event, never younger.

By default RadiCal assumes no gap: each date is read as dating its event exactly. A date can carry
an **inbuilt-age allowance** instead: **Inbuilt age (yr)** and **Allowance** on the date's form,
in the date collection, or as `inbuilt_years` and `inbuilt_shape` columns in a CSV. It applies in
Bayesian models, where the event is what is modelled. Allowing a gap moves the modelled events
*later*, and usually shortens a phase whose earliest dates are charcoal.

| Allowance | What it tells the model | Effect on the event | When it fits |
| --- | --- | --- | --- |
| None | The material is the same age as the event. | No shift. | Short-lived material: twigs, seeds, eggshell, bone, shell. |
| **Up to N years** (uniform) | The material is somewhere between fresh and N years old, any age in that range equally likely, never older. | Anywhere from 0 to N years later than the date alone implies; N/2 on average, never more than N. | You have a stated **maximum**, such as a published inbuilt age from species and ring counts. |
| **About N years** (exponential) | The material is most likely young; older is possible but increasingly unlikely, with no limit. | On average N years later. Most offsets are small, but a long tail allows the occasional much older one: with N = 15, about 1 date in 7 is offset by more than 30 years. | You know a *typical* age but have no ceiling, such as mixed charcoal of unknown species. |

An allowance is not the **Charcoal outlier model**. An allowance applies to its date every time, as
a modest offset. The outlier model is a small chance (say 5%) that a date is badly old, possibly by
centuries, which the model may or may not take up. The two can be combined on one date.

**Comparing with and without.** **Apply inbuilt-age allowances** (Advanced settings, on by default)
runs the model as if no date had one when it is off. On a Bayesian result whose dates carry
allowances, **Compare with and without inbuilt age** (the tree button beside Re-run) runs a copy
with the switch the other way and opens the two side by side. The Shag River Mouth dates show why
it matters: with WkA-56765 left out, the probability that Layer 11 began before AD 1360 is 0.97 with
no allowance and about 0.1 with the published maxima.

Allowances do not apply in a group or single calibration, which calibrate the material itself, nor
in a wiggle match, whose members are rings of one series. Dates can be pooled with **Replace with
pooled date** only if they share the same allowance, which the pooled date then carries. See
[A.19](#a19-inbuilt-age) for the arithmetic.

## Model Structure (Bayesian only)

Optional, but this is what distinguishes a Bayesian analysis from a Group Calibration. Build the
model as nested Sequences and Phases with Boundaries, then assign each date to a Phase. RadiCal
uses the same terminology as OxCal. TPQ (terminus post quem, maximum age) and TAQ (terminus ante
quem, minimum age) limiters can be set on a date or on a whole Phase.

The editor is reached via **Specify Model Structure** / **Edit Model Structure**, with an **Editor**
tab for building the tree and a **Matrix diagram** tab showing it drawn. The Results page renders
the same diagram for a completed analysis, and Posterior distribution plots follow its order.

### Reading the Model diagram

The diagram pictures the model tree. **Oldest is at the bottom**, as in a stratigraphic profile.

- A **context** (solid box) is a Phase that dates are attached to, showing its count and any
  TPQ/TAQ limiter.
- A **box around other boxes** is a Sequence or grouping Phase. A solid edge means a Sequence
  (ordered); a dashed edge means a Phase (grouped without ordering). A Phase containing two Phases
  asserts they belong together without saying which came first, so they sit side by side.
- A **labelled rule** across a group is a Boundary — a modelled transition — showing its 95% range
  once the analysis has run.
- An **arrow** means the lower block is older. Arrows appear only between adjacent blocks where no
  Boundary rule already separates them; implied relationships are omitted to keep a nested model
  readable.

Position carries meaning (a context sits centred beneath its group), so the diagram is laid out
rather than arranged by hand.

**Model settings** (on the New Analysis form):
- **Solver**: Automatic (recommended: exact integration where the model allows, sampling
  otherwise), Exact solver only (fast, but only works on chain-shaped models), or MH/Gibbs (works
  on all models). See [Choosing a solver](#choosing-a-solver).
- **Minimum sweeps per chain** (sampled runs only): blank by default, which runs each chain until
  it converges. Set a number to make every chain run at least that long — the only way to ask for a
  longer run, since a run otherwise stops as soon as it converges.
- **Seed** (sampled runs only): blank uses the fixed default, 42, so re-running unchanged inputs
  repeats the result exactly. A different seed gives an independent replicate. The seed a run took
  is shown with its sampling details and recorded in the JSON export.
- **Resolution (years)**: the calendar step for modelled ranges and durations. 5 years by default;
  1 or 2 suits dates from the last few centuries, 10 or 20 for the Pleistocene. Only reporting
  changes — posteriors, agreement indices and plots are the same at any setting (see
  [A.4](#a4-summarising-a-posterior)).
- **Uniform span prior**: OxCal's `UniformSpanPrior` correction (on by default), keeping a
  sequence's overall span prior independent of how finely it is subdivided. Only affects a
  sequence with three or more Boundaries.
- **Apply inbuilt-age allowances**: on by default. Off runs the model as if no date had an
  [inbuilt-age allowance](#inbuilt-age).
- **Durations and differences**: off by default; see
  [Durations and Differences](#durations-and-differences) for what it adds and when to enable it.
  A Group Calibration has the same option under **Reporting**, labelled **Differences between
  dates**.

## Running an Analysis

**Run Analysis** submits the form. A saved analysis navigates straight to its Results page.

### Choosing a solver

Only a Bayesian analysis requires a solver choice. A Single or Group Calibration asserts no
relationship between dates, so there is nothing to solve jointly. **Automatic is the right answer
for almost every run**: it integrates the posterior exactly where possible and samples otherwise. The
Results page always shows which method actually ran. The overrides exist for the cases below.

**Exact integration** applies to *chain-shaped* models — a single line of Phases separated by
Boundaries, with every date inside a bounded group and no ordering the chain does not already imply.
A Sequence of Sequences qualifies, as long as each inner Sequence starts and ends with its own
Boundary. It computes the posterior on a calendar grid by message passing: deterministic, no sampler
to converge, no calibration lobe to miss. It is also faster, usually by a wide margin — a single
pass with no progress bar because there is no iteration.

**MH/Gibbs sampling** follows OxCal's Metropolis-Hastings approach (see A.6) and handles any model:
branching structures, parallel groups, two groups ordered against each other with no Boundary
between them. It is budgeted 20,000 sweeps, stops as soon as the worst parameter reaches 95%
convergence, and extends up to 200,000 if needed; the achieved figure is reported with results, and
an unconverged run is flagged.

By default four independent chains run in parallel on separate cores and stop together: convergence
is read off their pooled draws, reaching the target in about a quarter of the sweeps per chain and
finishing about three times sooner. The first chain starts with every date at its calibration peak;
the others start spread across the dates' calibrated ranges. The chains must also agree with one
another (Gelman–Rubin R̂ under 1.05) — a check a single chain cannot make on itself: a chain
settled into one of a date's two calendar ranges looks converged, and only another chain in the
other range can reveal the discrepancy. Agreement is strong evidence, not proof. A calendar range
that no chain reaches is still invisible to R̂, so where a result has two well-separated ranges and
the decision rests on which is right, check by another method as well. For two common model shapes
the app runs this check automatically (see [A.6](#a6-solvers)). The chain count is set in the
analysis options; one chain is the plain run.

**Replicating a sampled run.** A sampled result carries Monte Carlo error: a second run with a
different seed gives slightly different figures. **Run again with a new seed** (the dice button
beside Duplicate on the Results page) copies the analysis under a fresh seed, names the copy after
it, and runs it, leaving this result in place. Set the two side by side in
[Comparing Analyses](#comparing-analyses): figures that move by more than you would report are not
yet settled, and a longer run (**Minimum sweeps per chain**) is the remedy. Replicates share any
bias the sampler has, so agreement between them does not show the answer is right; the direct
calculation above is the check for that.

Where both solvers can be used, they agree to under a year, and they are checked against each
other on every model both can handle.

| | Exact solver | MH/Gibbs sampler |
|---|---|---|
| Model shapes | chain-shaped only; fails on others | all |
| Result | deterministic; same input, same numbers | stochastic; converges to the same posterior |
| Speed | one pass; typically much faster | up to 200,000 sweeps per chain; chains run in parallel |
| Convergence | n/a | reported per run, within and between chains; can fall short |
| Multi-lobe calibration | resolved exactly | handled by whole-group translation moves (sampled) |
| Duration/difference draws | independent draws from the joint posterior | draws from an MCMC chain (correlated) |
| Outlier models | offset size integrated over prior range per date (an approximation) | one offset size shared across the model and estimated from the data, as OxCal does |
| Progress and cancellation | neither: it completes or fails | progress reported, run cancellable |

**Choose MH/Gibbs deliberately when:**

- You are **reproducing a published OxCal analysis** and want the method to match as well as the
  model.
- You want an **independent cross-check** of an exactly-integrated result: two different routes to
  the same posterior disagreeing by more than a year or two is worth investigating, and **Compare**
  can measure the difference (see [Comparing Analyses](#comparing-analyses)).
- Several dates carry **outlier models** and the run is about *which* dates are outliers. OxCal
  treats the outlier offset size as a single unknown shared by every date using the model and lets
  the data estimate it, so three charcoal dates that all read old together inform the model about
  what an old-wood offset looks like in *this* assemblage. The exact solver works the offset size
  out over its prior range separately for each date. The difference shows up mainly in the
  **reported outlier probabilities**, and it stays small when the outlier priors are small. On a
  real OxCal comparison the exact path's outlier probabilities sat 0.017 from OxCal's where the
  sampler reached 0.002.

  In practice: **it matters** if you have put outlier models on a group of dates expecting a shared
  cause (old wood, residuality, a suspect laboratory batch) and the outlier probabilities are the
  answer you are after. **It does not matter** if one or two dates carry a small outlier prior as
  insurance and what you are reading off the run is the modelled date ranges.

**Choose Exact solver only when:**

- You need a **reproducible, deterministic** number with no convergence caveat.
- You want to **force exact integration on a model Automatic declined to integrate**, accepting
  that this changes the model. See the warning below.

**Warning about forcing Exact.** Automatic integrates exactly only where the model supports it.
A model that is not chain-shaped as authored — most often a Sequence that orders two groups with
no Boundary between them — can be made solvable by supplying the transitions its ordering implies.
Choosing **Exact solver only** does exactly that. This changes the model: it splits one span term
over K dates into two terms over K₁ + K₂ dates, and K is the exponent that holds a date to its
group, so the modelled ranges genuinely move. Measured against OxCal on the app's own fixtures (rms
of the modelled dates, in years), sampling the model as written outperformed integrating the patched
model both times: 1.49 against 2.51 on the `ordered` fixture, and 4.53 against 10.63 on the
charcoal fixture. The Results page shows how many transitions were supplied. If exact integration is
what you want, the better fix is usually to write the missing Boundary into the model yourself —
this makes it exactly solvable and states the structural claim explicitly.

If a model cannot be integrated exactly even with transitions supplied, **Exact solver only** fails
the run and names the reason. That is the point of the setting: Automatic is what you want if
either answer would have been fine.

## Viewing Results

- **Results summary**: one row per date (and, for a Bayesian model, per Boundary), in the model's
  own structure order. Columns: the date's unconstrained ("unmodelled") calibration, its modelled
  posterior HPD range at the selected confidence, and diagnostics.
- **HPD confidence**: 68.3%, 95.4%, or 99.7% — the two-sided normal coverages of 1, 2 and 3
  standard deviations. These are the exact coverages (68.269%, 95.450%, 99.730%), and the level
  determines which grid points enter the HPD region, so the digits matter.
- **Show Mean / Show Median**: optional point estimates alongside the HPD ranges.
- **P before a date**: an optional column, turned on under **Columns**. Type a year into the
  **P before** field that appears beside HPD confidence — `AD 1360`, `1360 CE`, `500 BC` or
  `590 cal BP`; a bare number is read in the page's date format. Each date and Boundary then shows
  the probability it lies *before* that year (earlier, so older): P(Layer 11 began by AD 1360) is
  the Boundary's figure in a column headed **P before AD 1360**. Figures are read off the stored
  posteriors, so no re-run is needed. Values beyond three places read `>0.999` or `<0.001` rather
  than rounding to certainty. The year is remembered per analysis, and the exports carry the column
  while it is showing. See [A.4](#a4-summarising-a-posterior) for how it is computed.
- Dates whose diagnostics are worth a second look (poor agreement or high outlier probability) are
  called out in a banner above the table.
- **Durations and Differences**: above the results table when the run was asked for them. See
  [Durations and Differences](#durations-and-differences).
- **Model Structure**: a summary (phase and ordering-relationship counts) and, for a Bayesian
  analysis, the read-only matrix diagram of the constraints used.
- **Posterior distributions**: Plotly charts, oldest first:
  - **Stacked** (default): every date (and Boundary) as one row each, sharing a single calendar
    axis. Each row is scaled to its own peak, so heights compare precision, not probability.
  - **Individual**: one full-size plot per date/Boundary.
  - **Show boundary plots**: hide/show Boundary rows separately from dates.
  - **Show prior (unmodelled) distribution**: overlays each date's plain calibration as a grey
    shadow behind its modelled posterior, showing exactly how far the model moved it.

## Durations and Differences

Off by default. Turn on before the run: **Durations and differences** under **Model settings**
(Bayesian) or **Differences between dates** under **Reporting** (Group Calibration). To add to an
already-run analysis, **Edit** it, enable the option, and re-run.

Everything here is a **length of time in years** — never a calendar date — so none of it changes
when you switch the reporting format.

### The durations

- **Sequence**: from a Sequence's first Boundary to its last — how long the whole was in use.
- **Phase**: the interval enclosed by a Phase's two Boundaries. This is OxCal's `Interval()`, the
  *modelled* duration, normally longer than the spread of dates inside because those dates are
  treated as a sample of the phase rather than the whole of it.
- **Hiatus**: the gap between two adjacent Boundaries — from the end of one phase to the start of
  the next. This is OxCal's `Interval()` placed between an end Boundary and a start Boundary.
  Listed between the two phases it separates. The Hiatus element itself adds nothing to the model;
  two Boundaries side by side define a gap whether or not a Hiatus is drawn between them. A single
  Boundary between two phases is a shared transition, with no gap.
- **Dated events**: from the oldest to the youngest dated event in a Phase (OxCal's `Span()`).
  Nothing is inferred beyond the dates themselves.

Reading Phase against Dated events is the point of having both. A phase whose dated events span 30
years but whose modelled duration is 250 is a phase your dates have barely sampled.

A Phase with fewer than two dates gets no "dated events" row. A Sequence with only one bounded group
gets no Sequence row, because its Phase duration already measures the same quantity.

### Phases through time

A figure showing every datable phase on one calendar axis, one strip each, in model order. Shading
represents the probability the phase was **in progress** in each year, read off the same joint draws
as the durations: darker means more probable, and hover gives the figure. Along a Sequence the
strips follow one another; across parallel branches, a column read top to bottom shows which phases
were probably running simultaneously. A phase without its own Boundaries uses its dated events, and
a single-date phase shows that date's probability. **Download PNG** saves the figure, and the PDF
report includes it.

### Differences

For **phases**, the table compares only pairs whose order the model leaves open: phases in
different branches (e.g. two Sequences side by side inside one Phase). These are the only phases
that can overlap, so overlap is a finding. Phases in one Sequence are never compared, since the
Sequence already orders them; neither are nested phases, which overlap by construction. A model with
no parallel branches has no Differences table.

One row per pair. For two **phases** the figure is signed: **positive is a gap**, **negative is an
overlap**. For two **dates** in a Group Calibration, positive means the first of the pair is older.

**Overlap** grades how far the two are from being separable in time, from `none` to `extensive`.
For phases it is the probability that their modelled intervals intersect; for dates it is how far
the model is from saying which came first (100% is a coin flip). The bands use the same 68.3% and
95.4% levels as the rest of the app: `none` means separated at 2σ, `extensive` means overlapping
at 2σ.

### What these cost

Every quantity here depends on several modelled ages at once, so it comes from *joint* draws of the
posterior rather than per-parameter distributions. These draws are the only Monte Carlo in the exact
solver's output, so they carry a sampling error; the panel reports the draw count and whether draws
were independent (exact solver or Group Calibration) or from an MCMC chain. Reported ranges use a
step that widens for very long durations, rather than claiming five-year precision the draws cannot
support.

## Kernel Density Estimates

Off by default. Enable **Kernel density estimate** under **Reporting** (Group Calibration) or
**Kernel density estimates** under **Model settings** (Bayesian). To add to an already-run analysis,
**Edit**, enable, and re-run.

The plot shows how the dates spread through time, as OxCal's `KDE_Plot` does. It answers the
question usually asked of a summed probability distribution (OxCal's `Sum`), without the Sum's
artefactual peaks and cliffs, which mostly come from the wiggles in the calibration curve.

The line is the estimate; the shaded band is its ±1σ variability across draws. Where the band is
narrow, the shape is well supported. The kernel width the dates chose is printed above the plot.

Two cautions:

- The estimate treats the dates as a **sample of one population of events**. Several dates from
  one context, or one well-dated site among sparsely dated ones, will raise the curve wherever they
  fall.
- The estimate is **conservative**: measurement uncertainty is not removed, so a KDE is somewhat
  wider than the underlying event distribution, and its edges sit too early and too late.
  Where the dates belong to a bounded phase, modelling them as one (Bayesian) and reading the
  Boundaries gives better start/end estimates.

In a **Bayesian analysis** there is one estimate per Phase holding two or more dates, drawn from
*modelled* dates. Where the Phase sits between Boundaries, the curve stops at them rather than
tailing off past — the model constrains events to the interval, so the estimate respects that.

A Group Calibration has one estimate of all its dates. Either way, at least two dates are needed.

## Same-Event Test

Whether a set of radiocarbon measurements could all date one event: several samples from one
short-lived context, or repeat measurements of one sample. This is Ward and Wilson's (1978) χ² test,
as OxCal reports it under `R_Combine`.

**Where to find it**

- **Before a Bayesian run**, in the model editor. Every Phase holding two or more dates has
  **Test as one event** below its dates, with all dates ticked initially; untick any to test the
  rest. This is where the answer matters: it decides whether dates enter the Phase as separate
  events or are pooled into a single date first. Where the ticked dates pass, **Replace with
  pooled date** puts their pooled age in the Phase in their place, as OxCal's `R_Combine` would,
  with notes listing the dates pooled. The originals leave the analysis and Undo cannot restore
  them; dates deployed from a project's collection remain in the collection.
- A **Group Calibration** tests all its dates together, in its own panel below the plots.
  **Export CSV** adds the test as a block below the results, and **Export PDF** as a table.

**Reading it.** Compare `T` against the **critical value**; the 5% is the risk of a false alarm,
not one of the numbers to compare.

- **`T`** measures how far the dates sit from their pooled age, each in units of its own error.
  A date one standard error from the pool adds 1 to `T`, two errors adds 4, three adds 9. Dates
  of one event usually give a `T` near the number of dates minus one; a single stray date pushes
  it up quickly.
- **The critical value** is how large `T` can get from measurement error alone. It depends only
  on how many dates there are (more dates scatter more): 3.84 for two, 14.07 for eight.
  The panel's **df** (degrees of freedom) is dates minus one.

| `T` is | Result | What it means |
|---|---|---|
| below the critical value | Consistent with one event | The dates scatter no more than their errors explain. |
| above the critical value | More scatter than one event allows | Scatter this wide would happen less than 5% of the time if the dates were one event. |

Two examples, as the test reports them:

- `T = 5.7 · 5% critical 14.07 · df 7`: eight dates from Nenthorn. 5.7 < 14.07 → **consistent with
  one event**.
- `T = 27.9 · 5% critical 11.07 · df 5`: six dates from Layers 11–7. 27.9 > 11.07 → **more scatter
  than one event allows**.

How far `T` is from the critical value matters as much as which side it falls on. The second example
is more than twice the threshold — a clear result. A `T` of 11.5 against 11.07 would be marginal,
as would 10.5. Treat a near miss on either side as undecided, and look at the dates themselves for
the one that sits apart.

| Dates | df | Critical value (5%) |
|---|---|---|
| 2 | 1 | 3.84 |
| 3 | 2 | 5.99 |
| 4 | 3 | 7.81 |
| 5 | 4 | 9.49 |
| 6 | 5 | 11.07 |
| 8 | 7 | 14.07 |
| 10 | 9 | 16.92 |
| 12 | 11 | 19.68 |
| 16 | 15 | 25.00 |
| 20 | 19 | 30.14 |

A pass does not prove the dates are one event. It says only that the measurements cannot tell
them apart — also true of events a few decades apart on a plateau.

**Why the test comes before the model.** A modelled Phase is a span of time holding separate
events, so a long-lived Phase is expected to fail, and a pass says nothing about its length — the
model's own duration answers that. What the test reveals is whether some dates should not be
separate events at all. Replicate measurements of one sample, or several samples from one bone or
one short-lived deposit, are one event; entered separately, they count as several and make the
model claim more knowledge of the Phase's span than the data supports. Where they pass, replace
them with their pooled date.

**The pooled age.** Where the dates pass, the panel gives their error-weighted mean and its
calibration. Where they fail, there is no pooled age: averaging dates of different events is
meaningless.

**When the test is omitted.** The test compares conventional radiocarbon ages, so every date must
be on one curve with one ΔR. Dates on different curves (a marine shell and a terrestrial seed in
one Phase, say) are omitted from the test. A shared ΔR shifts all ages equally and leaves `T`
unchanged; it is applied once when the pooled age is calibrated. See [A.18](#a18-same-event-test).

## Wiggle Matching

For a series of dates whose separations are **counted** rather than inferred: blocks of tree rings
along one timber, varves in a lake core, samples taken at known intervals. OxCal calls it
`D_Sequence`, or `V_Sequence` where separations were measured and carry their own uncertainty.

A wiggle match has **one** unknown — the calendar age at the reference point — because every other
member's age is that plus its own gap. RadiCal solves it in closed form: deterministic, no Monte
Carlo qualifications. The Results page labels it **Wiggle match**.

**Building one.** On the Model Structure page, **Build from data › Make it a wiggle match** puts
every date into one series. Then give each date its gap — or press **Set gaps from ring numbers** and
enter ring numbers counting outwards from the pith. The app does the subtraction (which is where the
sign usually goes wrong).

The **Rings / Varves / Years** dropdown labels the axis and results panel; the maths is the same.

**The convention:** a gap is years **before** the reference. It is 0 at the reference and
**increases into the past**. A sample nearer the pith has the larger gap and the older age. The
reference defaults to the outermost sample.

**What it buys.** At a calibration plateau a single date is nearly useless, but a long enough series
can resolve it: at 2500 cal BP, one date at ±25 gives a 285-year range in two disjoint pieces,
where eight samples over 250 rings give 20 years in one. But **length decides whether the match
resolves at all; count only decides how tightly**. Twelve samples packed into 60 rings will not
break a plateau; eight spread over 250 will. The app warns when a series is short — better to know
before the dates are commissioned than after.

**Reading the result.** The panel gives the age at the reference point, with its range at the
selected confidence. Every member's posterior is the *same distribution moved by its own gap* — one
answer read at several points on the timber — so with counted gaps every member's range is the same
width. A member whose gap was **measured** (entered with a ±) gets a width of its own — usually
wider, but narrower where its own measurement sits on a steep stretch of curve.

The panel's figure places each laboratory measurement (±1σ) on the calibration curve at the
reference age plus its gap. A good match lies along the curve's wiggles; a date that sits off the
curve is ringed, and usually has the wrong ring count. If the reference age splits into several
ranges, the series has not settled on one place on the curve: a longer series resolves it, while
more samples inside the same span usually will not.

The **combination agreement** is the check that the match worked. The gaps are asserted, so a
misidentified sample simply degrades the overall fit. RadiCal quotes the index without a pass mark:
OxCal's combination threshold scales with n, and this app does not print a threshold it has not
verified against a real OxCal run.

**Outlier models** are incompatible with wiggle matching: the gaps are asserted, so there is no room
for a date to sit apart. The agreement index is what identifies a misfit member.

A wiggle match must currently be the **whole** model; nested inside a larger structure is refused.

## Age-Depth Modelling

For a core or profile where dates are separated by **depth**: peat, lake sediment, a stratified
section. OxCal calls it `P_Sequence`; it is what Bacon and clam users expect.

Deposition is modelled as a Poisson process along the depth axis, so the time between two levels
carries its own prior rather than being left uniform. This is sampled — the ages are coupled by the
deposition rate — and the Results page identifies it as such.

**Building one.** **Build from data › Make it an age-depth model** on the Model Structure page puts
every date into a deposition sequence; each then needs its depth. Add an **Undated level** to ask
"how old was this level" at an undated horizon — a tephra, a pollen boundary, the base of a feature.

**The rigidity.** `k` is events per unit depth, governing how much the depths constrain the ages:
large drives them onto a straight line, small leaves little but ordering. A badly chosen value
dominates the answer, so RadiCal **infers it from the dates** by default (the **Rate inferred**
setting). **Rate fixed** holds it at a value you specify — use this when reproducing a published
`P_Sequence(name, k)`. Watch the band: a stiff model draws a straighter line through a calibration
plateau than the dates alone support. When the inferred rate runs up against the end of its allowed
range, the panel notes **Rate pinned at its limit** — widen the range in that case.

**Reading the result.** The deliverable is the band — age against depth, with a median line and
68.3% / 95.4% envelopes — not a stack of per-date posteriors. Beside it is the accumulation rate
between each pair of levels (years per unit depth). Both come from *joint* draws, for the same
reason durations do: a rate is a function of two ages at once. Undated levels are drawn with hollow
markers.

## Geographic Analysis

Dates can carry position, enabling three capabilities: a reservoir offset can be derived from
location ([Marine Reservoir Offsets](#marine-reservoir-offsets)), a model can be built site by site,
and a Bayesian run can ask whether the dated sites describe something *moving* across the landscape.

### Placing dates

A position is **latitude** and **longitude** in decimal degrees, WGS84 (negative for south and
west). Degrees-and-minutes are not read. Enter both or leave empty.

- **On the measurement form**, the two fields sit beside depth.
- **From a file**, a CSV with `latitude`/`longitude` columns (`lat`/`lon`, `x`/`y` and common
  lab spellings are recognised).
- **On the map.** In a project's date collection, tick dates from one site and press **Place on
  map**: click the map or type coordinates. Dates from one site share a coordinate. Sorting the
  collection by **Position** groups co-located dates.

### Basemaps

Every map in the app uses one of two basemaps.

**The offline basemap** is drawn by the app from coastline data it serves — no request leaves
the browser. Accurate to about a kilometre. This is the default.

**The tiled basemap** is turned on per project with **Show a basemap with coastlines, rivers and
place names** (Edit Project). It uses [OpenFreeMap](https://openfreemap.org)'s *Positron* — a
light-grey OpenStreetMap-based map trimmed so that islands are always islands and nothing competes
with the data drawn over it. Panning or zooming tells OpenFreeMap's server which patch of ground
is on screen (not the coordinates of any date, but enough to locate a site). Worth it for precise
placement; consider the trade-off for restricted sites. OpenFreeMap needs no account or key.

Once tiles are enabled, a small **OpenFreeMap** switch appears under each map, remembered by the
browser. Lines (isochrons, spanning-tree links) are drawn along great circles on both basemaps.

The Edit Project dialog also sets the project's **coordinate precision** — full, or rounded to
0.01°, 0.1° or 1° (~1, 11 or 110 km) — for exports and figures. Stored values are unaffected.

### Sites

For the models below, a **site** is an element opened by its own Boundary — a Sequence bracketed by
Start and End, one per place — whose dates carry coordinates. Its position is its dates' centroid,
and its arrival time is the posterior of its opening Boundary, not its oldest date: you almost
never excavate the genuinely first thing at a site, so the oldest date is a *terminus ante quem*
for arrival.

Placed dates sitting in the starting phase every analysis begins with are not yet sites.
On the Model Structure page, **Build from data › Group dates into sites** replaces the model with
one bracketed Sequence per site, keyed on **location** name, **context**, or **position** (dates
within 5 km). The menu shows how many sites each key would produce and how many dates lack a value.
No order between sites is asserted.

A Phase whose dates are more than 10 km apart is refused: two sites in one phase is a modelling
error, and silently placing the phase between them would report a front passing through somewhere
nobody excavated.

### Proposing an order from the map

**Build from data › Propose an order from the map** builds a minimum spanning tree over the sites
by great-circle distance, walks it from one end, and shows the resulting order alongside the model
as a **proposal** to accept or reject. A spanning tree is spatial adjacency, not chronology:
accepting it asserts that everything in one site predates everything in the next — a strong claim
the data may refute. Its value is that it makes the claim *testable*: run the model and the sites
that do not fit the spatial order announce themselves in the agreement indices. Undo restores the
previous structure.

### Spatial dispersal

On the New Analysis page, once dates are placed, the Bayesian solver settings offer **Spatial
dispersal** (off by default — a straight line through dated points returns a speed whether or not
there is a front). With it on, the run fits three things from the same joint posterior draws that
durations use (a speed is a function of many modelled ages at once). Results appear above the
results table.

- **Regional gradient** (no origin assumed). A plane `t = a + b·x + c·y` fitted per draw in a
  local equal-area projection. Reported as a **speed** (km/yr) and **direction of advance** with a
  circular 95% interval, drawn as **isochrons** on the map. This is the look at the data with no
  hypothesis, which is why it comes first.
- **Where it could have started**: the wave-of-advance fit profiled over a grid of candidate
  origins, rendered as a region rather than a point with false precision. The grid covers
  everywhere within ~700 km of a dated site. If the best cell sits on the zone's edge, the fit was
  still improving where the search stopped — usually meaning the dates fix the direction but not
  how far back it started.
- **Wave of advance** from a placed origin — `t = t₀ − d / v` per draw — reported as **front
  speed**, **arrival at the origin**, and site count. Press **Place an origin** to drop a pin. Its
  map draws **isochrons as circles** round the origin, and an **arrival against distance** plot
  shows every site at its distance and modelled arrival with the fitted front — where a pause or
  leapfrog shows. The panel also states how widely the sites **scatter** about the front: a
  quantity distinct from the speed's interval, which is how much the line moves between draws. A
  negative speed is reported as negative: either the front ran the other way, the origin is
  elsewhere, or these dates do not describe a front. Nothing is flipped to make the answer look
  like a dispersal.

**Residuals are the finding.** Every site is coloured on the map by its departure from the fit
(orange = early, blue-grey = late, pale = near zero), and the wave of advance lists them in a table
sorted by magnitude (ten largest shown first). A site within twice its own uncertainty of the front
is marked **fits**. Pointing at a row highlights it on the map and plot. **Download CSV** saves the
whole table. Straight, parallel, evenly spaced isochrons are inherent to the plane model, so a
curving or slowing front shows as a patch of neighbouring sites all one colour. A single early
outlier is often where the interesting archaeology is: a leapfrog, a maritime route, a redeposited
sample, or a wrong date.

**Refusals.** Each fit needs at least **six** sites; with fewer, a line returns a tidy speed with
nothing left over for the data to contradict. A trend surface also needs sites spread in two
dimensions (for a transect, use the wave of advance with a placed origin). A wave of advance needs
sites at more than a few distinct distances from the origin.

**Map controls.** Plain scroll wheel scrolls the page; hold **Ctrl** (**⌘** on Mac) to zoom. Touch
screens: read-only maps do not pan, so a swipe scrolls past them.

**Limits of the estimate.**

- The front is refitted on every posterior draw, so age uncertainty carries into the speed.
  Uncertainty in the origin, coordinates, and whether these sites are the true front is omitted.
- Residuals are treated as independent. Neighbouring sites share whatever the model misses, so the
  speed interval is narrower than it should be. The **site scatter** figure shows how far sites
  actually sit from the front.
- The speed is a line refitted to each draw and summarised. A hierarchical model would estimate the
  front and each site's scatter together.
- For a group calibration (no Boundaries), sites are dated by their **earliest dated event**. The
  oldest date is a *terminus ante quem* for arrival: it is late by an unknown amount, and later at
  the least-dug sites. The speed inherits that bias.

**Reading the origin search.** Shaded cells are candidate origins whose likelihood reaches the shown
share of the best cell's. Cells where sites get older with distance are where the front went, not
where it started, and are excluded. When the shaded region covers most of the search area, the dates
fix direction but not starting point.

**Reading the regional gradient.** Its isochrons are straight, parallel and evenly spaced by
construction, so a curving or slowing front shows in the site colours. The direction's
**concentration** runs from 0 (draws point every way) to 1 (all agree); below 0.5 the direction is
marked uncertain.

## Exporting Data

- **Export CSV** (Results page): the full results table in the selected date format. Where a run
  produced durations and differences, they follow as a second block (blank line, own header — since
  every figure is a length, not a date, they share no column with the results table). A kernel
  density estimate follows in two more blocks: one row of metadata, then the curve at each calendar
  year step. A Group Calibration's [same-event test](#same-event-test) comes last. While the
  **P before** column is showing, it is added as the table's last column, so no other column moves.
- **Export JSON** (Results page): the full result, machine-readable. Carries every date and Boundary
  in model order, durations, differences, KDEs, a Group Calibration's same-event test, and the solver record (solver,
  convergence, chains, agreement indices, cross-checks). While the **P before** column is showing,
  `p_before` carries its threshold and each date's and Boundary's probability. Also at
  `GET /api/results/{id}/export/json`.
- **Export all (zip)** (a project's ⋮ menu): the whole project in one file. `analyses/` holds every
  analysis exactly as its own Export JSON gives it, and `inputs/` what each was run from: settings,
  dates and model structure. `samples.csv` is the project's date collection in the columns the CSV
  import reads, so it goes straight back in. `summary.csv` has one row per modelled Boundary and
  duration of every analysis, with the solver, convergence, R̂, seed and cross-check beside it.
  Calendar ages there are cal BP and lengths of time are years, in separate columns. An analysis with
  nothing modelled still gets a row. PDFs are not included.
- **Curve data** (curve browser page): **Export this window** saves the visible stretch as CSV;
  **Export profile** saves the whole expected-resolution profile. A curve itself can be downloaded
  from the Curves page, byte-for-byte as imported.
- **Export PDF** (Results page): a printable report of the whole analysis: metadata, curves with
  references, solver, results table (with the **P before** column while it is showing), durations,
  KDE, a Group Calibration's same-event test, and a plot per modelled
  parameter with HPD ranges marked. Uses the currently selected date format. A large model takes a
  few seconds.
- **Export CSV** (comparison page): every paired date with each run's median, HPD range and
  agreement index, then shift and width ratio against the reference. Below it, derived quantities
  in separate blocks (transitions, durations, separations) — each block names its own units and
  what a positive shift means. Unpaired dates follow under their own header. Settings diff and
  agreement scatter are not exported.
- **Download PNG** (per plot): a print-quality PNG of the current view.

## Projects and Date Collections

A **Project** groups analyses and can hold its own date collection, independent of any particular
analysis. Dates can be entered one at a time, pasted in bulk
(`name, age, error[, material[, context[, curve]]]` per line), or imported from a file (see
[Importing Dates from a File](#importing-dates-from-a-file)). A collection holds dates rather than
models, so importing an OxCal file here keeps each date's phase as its default context but not the
structure; import from a new analysis to keep that.

Deploying dates copies the values into a new analysis, so editing the collection afterwards never
changes an analysis already created, and the same dates can be deployed more than once to model
differently.

From a project page you can re-run all analyses at once, or re-run, duplicate, or edit a single
analysis (editing replaces saved inputs and re-runs in place). Ticking two or more finished
analyses and pressing **Compare** opens them side by side; see
[Comparing Analyses](#comparing-analyses).

Deploying always creates a **Bayesian** analysis and opens its editor, but you can switch the type.
Choosing Single Calibration for more than one date stores it as a group calibration.

## Comparing Analyses

**Compare** puts two to four runs of the same dates side by side and computes the difference.

Tick two to four finished analyses on a project page and press **Compare**. **The first one you
tick is the reference**: every shift and ratio is measured against it. The comparison is an
ordinary URL, bookmarkable and shareable.

**Reporting format** and **HPD confidence** are set once at the top. A comparison whose runs used
**different calibration curves** is allowed but captioned: the shift then measures the curve rather
than the model. Best practice: isolate one parameter per comparison (curve, ΔR, solver, or model
structure).

**Earlier runs.** Each analysis keeps its last three completed runs, so a re-run (most often after
an update to RadiCal) does not lose the result it replaces. On the Results page, **Compare with an
earlier run** (the clock button beside Re-run) lists them with when they ran, the version of the
code and the seed, and opens one beside the current result. After **Re-run all** on a project, the
page lists every analysis whose Boundaries or durations moved by 10 years or more against its
previous run, or gained or lost a range, and says when its inputs had changed too. Unchanged
inputs on unchanged code give the same result every time, so anything listed moved because
something changed.

### Dates

One row per date, one HPD bar per analysis, all on one calendar axis, with a hairline at the
reference run's median. A bar is drawn in segments for disjoint regions.

Rows sort by **largest shift first** by default (alternatives: reference analysis order, name).

Three columns beside each row:

- **Δ median**: this run's median minus the reference's, in years, **positive = older**.
- **Width ratio**: this run's HPD width divided by the reference's. Below 1 is tighter.
- **Agreement**: each run's agreement index (only for runs that compute one).

These two questions come apart constantly. A model that moves nothing while halving every interval
is a good result that a plain overlay would render as "no change".

**Click a row** to expand the distributions: each run's posterior drawn over the others, with the
unconstrained calibration behind in grey. This lets you check whether a run that appears to have
"moved the date 140 years" has really just moved probability *mass* from one lobe to another —
invisible in the interval plots. All curves are scaled to unit area, so a tighter posterior is
correctly taller.

Dates are matched by **sample name** (case and spacing ignored). A name appearing twice inside one
analysis is excluded from comparison. Unmatched dates appear under **Not compared**.

### Models

The second tab asks: *I changed the model — was it worth it, and what did it cost?*

**Model differences** compares what each analysis stored: curve, ΔR, outlier models, age limiters,
structure, solver, draw count. Fields the runs disagree on are marked **±**, shared ones **=**. The
model trees are matched element by element and drawn side by side, with chips for anything that
**moved** or is **only in** some runs. Elements are matched by type and name; unnamed elements are
counted but not matched.

**Findings** compares calendar estimates for transitions and their durations. A boundary in the
reference with no counterpart in another run is drawn dashed and reads **no counterpart**. Runs
listing the same pair of phases in opposite order are left unpaired — every figure in one is the
negative of the other, a structural difference worth seeing.

**Date agreement changes** plots each run's agreement index against the reference's, one point per
date. The diagonal is "no better, no worse"; below it, worse. Dotted rules mark the conventional 60
threshold on both axes. This is the half of a comparison the Dates tab cannot show: a date that
gained 140 years of precision can sit well below the diagonal here, because the precision was bought
by overruling the date rather than learning something about it.


## Best Practices

1. **Sample data**: use consistent units (age always in years BP), and double-check stratigraphic
   order before building a model around it.
2. **Calibration curves**: IntCal20 for Northern Hemisphere terrestrial, SHCal20 for Southern
   Hemisphere terrestrial, Marine20 for marine samples. Use the most recent version. For a sample
   with mixed-reservoir carbon, build a mixed curve rather than choosing the nearer of the two
   (see [Calibration Curves](#calibration-curves)).
3. **Marine corrections**: consult the literature for an appropriate local ΔR; document its source.
4. **Reading diagnostics**: the agreement index compares a date's modelled posterior against its own
   unconstrained calibration (100 = the model left it alone; below ~60 is conventionally worth a
   second look). It flags a date the model had to move, which is not automatically the same as a
   date that is wrong.
5. **Check the curve before commissioning dates**: the browser's expected-resolution panel shows
   how tightly a date from your period can be known at best. If your question needs finer
   resolution than the curve provides, no laboratory precision will supply it.
6. **Compare runs on both tabs**: a model that tightens every interval has not necessarily improved
   anything. The Dates tab shows what moved; the Models tab's agreement scatter shows what it
   cost. A date that gained precision by being overruled looks like a success on the first and a
   problem on the second.
7. **Wide Boundaries on a short phase are expected**: a Phase's Start/End Boundaries genuinely need
   four or more dated members before their span is well-constrained; two or three dates in a phase
   can produce a very wide 95% range, and that is correct.

## Appendix A: Mathematical Methods

This appendix describes how each reported number is computed, citing the source of every method
taken from the literature. Symbols follow the code: `t` is a calendar age in cal BP, `b` a
Boundary parameter, `L` a likelihood, and `K` the number of dates in a group. Full references are
in the [Bibliography](#bibliography).

### A.1 Conventions

**Calendar ages** are held internally as cal BP, "present" being AD 1950 by convention
(Stuiver and Polach 1977). Conversion to BC/AD or BCE/CE is presentation only: `year = 1950 - calBP`,
with no year zero (1 BC is followed by AD 1).

**Confidence levels** are the two-sided normal coverages of one, two and three standard deviations,
computed rather than rounded:

```
sigma_1 = erf(1/sqrt(2)) = 0.682689...   reported as 68.3%
sigma_2 = erf(2/sqrt(2)) = 0.954500...   reported as 95.4%
sigma_3 = erf(3/sqrt(2)) = 0.997300...   reported as 99.7%
```

The distinction matters: the level is the threshold an HPD set is accumulated to, so a truncated
0.95 selects a different set of grid points from the exact 0.954500. Over thirty IntCal20
calibrations between 500 and 10,000 BP, the two differed in 27 cases, by up to 10 years.

**Reporting resolution** varies by range type:

| Range | Reported on | Rationale |
| --- | --- | --- |
| Unmodelled calibration | the curve's own grid | the posterior is exact at every grid point |
| Modelled date or Boundary | a 5-year grid, or the analysis's **Resolution** | independent of the solver's internal step |
| Duration or difference | an adaptive step, reported with the figure | set by what the draws can support (see [A.9](#a9-durations-and-differences)) |

A single calibration can return ranges that are not multiples of five: SHCal20 is tabulated at
1-year steps through the late Holocene, and a range there is quoted to the year. Only the
*modelled* posterior is rebinned. Point estimates and plots always use the full-resolution posterior.

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

Three details matter:

- **The `1/sigma` factor is kept**, not just the exponential. Curve error varies with calendar age,
  and omitting the normalising term biases the posterior toward stretches where the curve is poorly
  known. This is the correction CALIB made and OxCal inherited.
- **`w(t)` is the calendar-year width each curve grid point stands for** (half the distance to each
  neighbour). IntCal20 is tabulated at 1, 5, 10 and 20-year steps in different ranges, so
  unweighted summing would concentrate posterior mass wherever the file happens to be dense.
- **`dR` is the local marine reservoir offset**, applied as a shift because the marine curve
  models only the global average. Its uncertainty `sigma_dR` enters the quadrature sum, so a
  poorly known offset widens the calibrated range.

Curve values at non-grid calendar ages are linearly interpolated in both `mu` and `sigma_c`.
Beyond the curve's ends the likelihood is zero: the curve has nothing to say, so neither does the
date.

The built-in curves are IntCal20 (Reimer et al. 2020), SHCal20 (Hogg et al. 2020) and Marine20
(Heaton et al. 2020).

### A.3 Mixed curves

For a sample whose carbon came from two reservoirs (the classic case being a partly marine diet):

```
mu_mix(t)    = p mu_A(t) + (1 - p) mu_B(t)
sigma_mix(t) = sqrt( p^2 sigma_A(t)^2
                   + (1-p)^2 sigma_B(t)^2
                   + ((mu_A(t) - mu_B(t)) sigma_p)^2 )
```

`p` is the proportion from the first curve and `sigma_p` its 1σ uncertainty. The third term under
the root is what makes an uncertain diet widen the date: largest where the two curves are furthest
apart — exactly where being wrong about the proportion costs most. The mixed curve is defined on
the union of the two parent grids, restricted to their overlap.

### A.4 Summarising a posterior

**HPD regions.** Grid points are taken in order of decreasing probability *density* until the
*mass* they hold reaches the target coverage; the selected points are then grouped into maximal
runs, each reported as a range with its own share of total probability. The distinction matters
on a non-uniform grid: IntCal20 steps from 1 to 5 years at 5,000 cal BP, and a 5-year cell holds
five times the mass of a 1-year cell at the same density. Ranking by mass would let low-density
cells in the coarse stretch outrank high-density ones in the fine. Each point is ranked by mass
divided by calendar width (`w(t)` of A.2), while mass itself accumulates to the level; on a uniform
grid the two orderings coincide. Ties are broken toward the lower index.

Because calibration curves are non-monotonic, a single date routinely produces several disjoint
ranges; reporting only the outer envelope would claim probability in near-empty gaps. The
percentages sum to slightly more than the level requested, since the set is built from whole grid
points and the last one taken overshoots. This is standard radiocarbon practice (Stuiver and Reimer
1993; Bronk Ramsey 2009a).

**Point estimates** are computed from the density (no Monte Carlo error even on the sampled path):
mean and standard deviation as probability-weighted moments; median by interpolating the cumulative
mass at 0.5 across cell edges; mode as the highest-*density* grid point (mass divided by width).

**Rebinning (modelled posteriors only).** A modelled posterior comes off a solver grid whose step
varies with the date range and group count. Two problems follow: reporting at that step would make
a published range depend on an implementation detail, and the same dates in a longer sequence would
give different widths. At a fine step the HPD picks out peak tops and skips dips between them.

Modelled ranges are therefore rebinned to the reporting step (5 years by default, or the analysis's
**Resolution**) before HPD selection. Rebinning integrates the density: each grid point stands for
a cell half a step either side, cumulative mass is read off cell edges, and a reporting bin's mass
is the difference between readings at its two edges. This is exact for a constant-within-cell
density and, unlike counting which grid *points* fall in each bin, independent of grid alignment.
The solver's step is also chosen to tile the 5-year step exactly.

Setting a different **Resolution** changes only this rebinning. Posteriors, draws and agreement
indices are unaffected. This differs from OxCal, where `Resolution` is also the calculation grid.

Unmodelled calibration is not rebinned — it has no solver grid to hide, and the curve's own spacing
is the honest resolution.

The results page re-selects the region when you change the confidence selector, applying the same
rebinning, so the table, plots, matrix, exports and stored result all agree.

**Threshold probabilities** (the **P before** column) are P(t > T) for a threshold `T` in cal BP:
the share of the posterior older than `T`. Each is integrated from the stored density by the
trapezoid rule, cut exactly at `T` and divided by the series' own total, since a stored series is
trimmed to where it has mass. One calculation serves the table and every export. A modelled
posterior is stored on its solver grid, so a threshold between grid points is interpolated; on the
AD 1250–1450 test fixtures this reproduces P(Start 11 before AD 1360) of 0.999, 0.947 and 0.989 for
the three published-date models, and 0.49 for WkA-43681 (614 ± 20 BP) calibrated alone.

### A.5 The Bayesian model

The model of Buck et al. (1991, 1992; 1996), following OxCal (Bronk Ramsey 1995, 2009a):

```
p(t, b)  proportional to  prod_dates L_d(t_d)
                        * prod_groups (b_older,g - b_younger,g)^(-K_g)
                        * c(t, b)
```

- `L_d` is date `d`'s calibration likelihood from A.2.
- The **span factor** `(older - younger)^(-K)` is the uniform phase model: conditional on the
  enclosing Boundaries, each of a group's `K` dates is uniformly distributed between them. Each
  date belongs to exactly one bounded group (the innermost enclosing pair), so nested groups' dates
  are not double-counted.
- `c(t, b)` is the indicator that every ordering constraint holds: sequence order, stratigraphic
  relationships, TPQ/TAQ limits. These are hard truncations. Stratigraphic relationships are
  expanded to pairwise constraints by the law of superposition and displayed as a Harris matrix
  (Harris 1979), oldest at the bottom. Where a sample and its group both carry a TPQ or TAQ, the
  tighter wins.

**Boundaries** are shared parameters. A Boundary between two Phases is simultaneously the end of
the earlier and the start of the later. Adjacent phases therefore have no gap to measure; a
deliberate gap is modelled as two Boundaries side by side (conventionally with a Hiatus between
them) and reported as a Hiatus duration.

**Uniform span prior.** Placing `n` ordered Boundaries across a span leaves the `n-2` interior
ones free, and the induced prior favours long sequences purely for having more room to arrange them.
Dividing that out is the correction of Nicholls and Jones (2001), OxCal's default. The app divides
by the exact discrete count

```
C(span in grid cells - 1, n - 2)
```

rather than the continuum `span^(n-2)`. The two agree only as the span grows large, and the prior
concentrates mass at small spans where they differ most; with likelihoods switched off, the binomial
reproduces the answer to 0.00% total-variation distance where the power form was in error by
0.4–1.6%.

The same option carries the limits factor `1/g` of Bronk Ramsey (2008, appendix A.31; 2009a
eqs. 29–30):

```
g = min( (ulim_m - llim_a) - (t_m - t_a),
         (t_m - t_a) - (llim_m - ulim_a),
          ulim_a - llim_a,
          ulim_m - llim_m )
```

This is not negligible: on eight overlapping dates in one phase it is worth about 18% of the
reported span, and it narrows.

### A.6 Solvers

The app chooses between two solvers by model shape. Both target the same posterior and are checked
against each other on every model both can handle.

**Exact integration** (chain-shaped models: a Sequence of Phases separated by Boundaries, or a
Sequence of such Sequences, each bracketed by its own Boundaries). Every date other than the one
being marginalised enters through its integrated likelihood over an interval (a cumulative sum).
What remains is a chain over the Boundaries, solved by forward/backward message passing:

```
T_i(b_i, b_i+1) = (b_i - b_i+1)^(-K_i) prod_{j in group i} F_j(b_i+1, b_i)
alpha_i+1(b)    = sum over b_i > b  of  alpha_i(b_i) T_i(b_i, b)
beta_i(b)       = sum over b_i+1 < b of  T_i(b, b_i+1) beta_i+1(b_i+1)
```

This is O(M²) per group on an M-point grid, deterministic, and exact up to the grid step.

The interval integrals `F` are **trapezoidal**, with half weight on each endpoint cell. A plain
cumulative difference gives an interval spanning `k` cells the mass of `k+1` cells, while the span
factor charges `k`, so near-coincident configurations gain `2^K` and every modelled phase comes out
too narrow.

**Metropolis-Hastings / Gibbs sampling** (everything else) follows OxCal's scheme (Metropolis et
al. 1953; Hastings 1970; Buck et al. 1992; Bronk Ramsey 1995). Each full conditional is
one-dimensional and exactly samplable: a date's conditional is its likelihood over the interval its
neighbours and boundaries allow; a Boundary's is the product of the span factors of the groups it
bounds. The app draws by inverse CDF on a precomputed cumulative table rather than by
propose-and-reject. Conditionally independent dates are updated in blocks, and whole-group
translation moves are proposed on the exact MH ratio so a tightly constrained group can cross
between calibration lobes. Half the wide proposals jump by one of the distances between the group's
own calibration peaks (both directions, blurred by a few years), rather than from a fixed 120-year
kernel. The proposal stays symmetric, so the acceptance ratio is the posterior ratio alone.

Two Boundaries never share a grid cell; a date may sit on a Boundary at half weight. The sampler
enforces this exactly as the exact solver does. The span correction divides by the number of ways
interior Boundaries fit *strictly* inside a span, so letting two coincide adds arrangements the
correction never divides out, especially at the smallest spans.

**Convergence** uses the same diagnostic as OxCal: the overlap integral between the first-half and
second-half distributions, per parameter, targeting 0.95. The run extends until the worst parameter
meets the target or the sweep ceiling is reached.

**Several chains, one stopping rule.** By default four chains run in parallel, each seeded
differently. Chain 0 starts with every date at its likelihood mode; the others start at calibration
quantiles spread across their ranges (one quantile for every date, because the lobes that matter
belong to whole groups). The chains advance in step and convergence is read off their pooled draws,
so four chains reach the target in about a quarter of the sweeps each. Before stopping, the run
must also pass the Gelman–Rubin R̂ (Gelman and Rubin 1992):

```
R-hat = sqrt( ((n-1)/n * W + B/n) / W )
```

held under 1.05 for every parameter. A run that hits the sweep ceiling with chains still
disagreeing is reported as unconverged regardless of the pooled overlap.

**Checking a sampled result.** Both convergence measures compare the sampler against itself and
cannot detect a lobe no chain has reached. The app therefore directly calculates the same posterior
without sampling for two common shapes:

- A chain of Phases and Boundaries created using nested Sequences.
- Parallel chains between an outer pair (e.g. a site's Start and End surrounding one Sequence per
  area).
- Either of the above with Phases or Sequences that have no Boundary between them.
- Any of these with dates outside every Boundary: Phases or Sequences of dates before the model's
  first Boundary or after its last. They have no span term (their prior is flat), so given that
  Boundary they enter as one weight on it: the integral of their likelihoods beyond it, in their
  own order. A date out there carrying an outlier model is not checked.

Given the outer pair, the branches are independent:

```
p(S, E) ∝ C(S, E) · T0(S, E) · Π_b Z_b(S, E)
```

Each Boundary is compared on 5-year bins in two ways: by total variation distance, and by the
largest difference in the probability of the Boundary being older than any given year. Over 0.15 on
the first, or over 0.05 on the second, is reported as a difference, naming the Boundaries. The second
exists because total variation cannot see a small calibration range the sampler under-weights: on
one dune sequence the sampler put the probability of an early start at 0.00–0.03 against the
direct 0.08, at a total variation of only 0.08. The second is held only on runs of several chains,
since a single short chain's own noise can reach it. Runs that agree sit under about 0.08 and 0.03. A
difference should be read as the direct calculation being right.

**Calibration ranges no chain reached.** After every sampled run, each calibration range holding at
least a tenth of a date's likelihood but almost none of its posterior is probed: a short chain is
started with the date's phase placed in that range. A range the model rules out is left at once; a
range the sampler was trapped away from is not, and the run is then reported as having missed it,
naming the dates. This works on any model shape, which the direct calculation does not. On a dune
sequence whose four chains all sat in the late range, missing an early start with probability 0.08,
the probe chains stayed in the early range 89–99% of the time; across about 2,000 tested ranges the
model ruled out, they left all but one. A missed range should be read as the sampled answer being
unreliable there: the exact solver, where the model allows it, or a longer run with more chains is
the remedy. Other shapes are not checked, and the solver record
says why.

### A.7 Outlier models

A Bernoulli outlier indicator is integrated out per date:

```
L_mix(t) = (1 - p) L_inlier(t) + p L_outlier(t)
```

`p` is the prior outlier probability. The outlier component is the inlier likelihood convolved with
an offset kernel, following Bronk Ramsey (2009b), building on Christen (1994):

- **General**: a Student-t offset with 5 degrees of freedom, scale `10^u` with `u ~ U[0, 4]`.
  Symmetric: the date may be too old or too young.
- **Charcoal**: a one-sided exponential gap truncated at 10 scale lengths, scale `10^u` with
  `u ~ U[0, 3]` (OxCal's `Outlier_Model("Charcoal", Exp(1,-10,0), U(0,3), "t")`). Mass sits on
  the side saying the dated material formed *before* its context.

The scale is integrated over its prior on a 24-node log grid. On the exact path this is done
per date independently (an approximation — OxCal shares one scale across all dates using the
model). The sampler shares it properly. The cost is small where outlier priors are small: on a real
OxCal comparison the exact path's outlier probabilities sat 0.017 from OxCal's where the sampler
reached 0.002.

**Reported outlier probability** is the posterior probability of being an outlier:

```
P(outlier) = integral  posterior(t) * p L_out(t) / L_mix(t)  dt
```

The outlier component is deliberately not renormalised over the computational window. Its
convolution is already proper on the real line, and most of its mass lies off the grid — precisely
the statement that a flagged date's true age could be anywhere. Rescaling to one would make the
reported probability a function of window width.

### A.8 Agreement indices

The standard OxCal diagnostic (Bronk Ramsey 1995):

```
A_i = 100 * integral(p * L) / integral(L^2)
```

A date the model leaves alone scores 100. One the model pushes off its likelihood scores low; one
the model sharpens onto the peak scores above 100 (so values over 100 are expected). Below ~60 is
the conventional threshold.

`Amodel` and `Aoverall`:

```
Amodel   = 100 * (E[product of L_i(t_i) / integral(L_i^2)])^(1/sqrt(n))
Aoverall = 100 * (product of A_i/100)^(1/sqrt(n))
```

`Amodel` falls below `Aoverall` when the model's structure is doing work, because it sees the
correlation the ordering imposes. It is a Monte Carlo average over joint draws, good to about ±0.5.

**Resolution.** The indices are ratios of integrals. The likelihood is evaluated exactly at every
posterior point, and `integral(L^2)` is taken at one-year steps. OxCal's indices depend on its
calculation resolution: the same model gives `Aoverall` 100.4 at OxCal's default five years and
105.4 at one year. This app matches OxCal run at one year (per-date rms 0.23–0.30). Against a
default OxCal run it reads about 0.7 higher per date, and several points higher overall.

**Outlier models.** A date evaluated under an outlier model incorporates the outlier prior p and the
shift. A date identified solely as an outlier will score approximately 100p — around 5 at the
standard 0.05 level. Each active outlier model contributes one term to n, as OxCal includes its
scale parameter. Agreement with OxCal at one-year resolution: per-date rms of 0.4–0.5 and model
indices within ~1, even with flagged dates included.

The diagnostic detects a date the *model had to move*, which is distinct from a date that does not
belong. On an unconstrained phase, a date 600 radiocarbon years from its neighbours scores 100.5,
because the phase simply widens. Use an outlier model to find a date that is merely wrong.

### A.9 Durations and differences

These are functions of several modelled ages at once and are computed from **joint** draws of the
posterior. The distinction matters: pairing draws from two boundary marginals on a single six-date
phase puts 8.2% below zero (a phase ending before it starts), where the joint posterior gives that
zero probability.

On the exact path, draws are independent draws from the true joint posterior via
forward-filtering/backward-sampling (Frühwirth-Schnatter 1994; Carter and Kohn 1994), so Monte
Carlo error is a plain `1/sqrt(N)` with no autocorrelation. Default: 20,000 draws.

On the sampled path, draws are whole chain states, thinned and pooled across chains. They are
correlated, so durations converge more slowly than marginals; the run is not extended on their
account. The report identifies which kind of draws it holds.

**The durations**: Sequence duration (first Boundary minus last), Phase duration (OxCal's
`Interval`), Hiatus duration (gap between adjacent Boundaries with no phase between), and Dated
events (OxCal's `Span` — range of the dates themselves).

**Differences** between phases whose order the model leaves open: signed years between their
modelled intervals, positive for a gap, negative for overlap. Between dates in a group calibration:
signed difference in calendar age.

**Overlap** probabilities:

```
two phases   P(their modelled intervals intersect)
two dates    P(the ordering is unresolved) = 2 min( P(A older), P(B older) )
```

Both run 0–1. Bands: `none` = separated at 2σ, `extensive` = overlapping at 2σ.

**Reporting resolution.** The histogram is capped at 40 bins, with bin width taken from the
0.5–99.5 percentile spread. The step is reported with every figure.

### A.10 Expected curve resolution

Svetlik et al. (2019), as implemented in Demjan's `res14c`. This app's version was written from
the published method and reproduces the reference implementation's figures to ~0.3% over
1,000–4,000 cal BP.

1. Scale laboratory uncertainty with age: `u(a) = u0 exp(a / 16066)`.
2. Calibrate each radiocarbon age a sample of that period could plausibly return.
3. Take the width of its 95.4% HPD envelope as the resolution achieved.
4. For each calendar year, average those widths over the plausible radiocarbon ages, weighted by
   likelihood.

Default laboratory uncertainty is 25 years (a typical commercial date), not 15 (the reference
implementation's, near the best AMS can offer).

### A.11 Curve display

Two presentation choices worth noting:

**Decimation is min/max per bucket**, not every Nth point. Stride sampling removes exactly the
extremes that make a curve wiggly (plateau reversals, short excursions). Keeping the lowest and
highest radiocarbon age in each bucket preserves the envelope at any zoom.

**Local slope** is measured over ±50 calendar years on the full grid, before decimation.
Point-to-point gradient on IntCal20's 1-year grid is dominated by year-to-year scatter and swings
between +4 and −3 across stretches that are flat at any relevant scale; ±50 separates a plateau
from ordinary curve without over-smoothing.

### A.12 Comparing two runs

Everything on the comparison page is computed in the browser from the two analyses' existing
payloads; no solver runs again.

**Matching.** Dates are keyed on `sample_name`, normalised for case and whitespace. (A deployed
date is a *copy* with no reference back to the collection, so no other identifier is shared.)
Boundaries, durations and separations are keyed on kind and name; separations on the ordered pair,
since reversing it negates every figure.

**The two figures per date:**

```
delta_median = median_run - median_reference        years, positive = older
width_ratio  = |HPD_run| / |HPD_reference|          at the selected confidence
```

`|HPD|` is the outer span, computed through the same selection the results table uses. For modelled
posteriors, selection rebins to the 5-year reporting step (A.4); for unmodelled calibrations it
works on the curve's own grid. Duration regions use their own histogram and step (A.9).

**Why no joint statistic is offered.** A statement like `P(this date is older under run B than
under run A)` requires the joint distribution of the two runs' parameters. Each run produced draws
independently — draw 1 of run A and draw 1 of run B share no correspondence. Pairing them would
manufacture a dependence structure, just as pairing two boundary marginals manufactures phases that
end before they start (A.9). A difference of medians and a ratio of widths need no such structure.

### A.13 Wiggle matching

A series with counted separations has **one** free parameter. Writing `t` for the reference-point
calendar age and `g_i` for member `i`'s gap:

```
p(t)  proportional to  product over i of  L_i(t + g_i)
```

Each `L_i` is the ordinary calibration likelihood of A.2 (curve error, ΔR and any TPQ/TAQ included).
All members are evaluated on a shared grid shifted by their gaps, logs summed, result
exponentiated: `O(n·M)`, deterministic, exact up to the grid step.

**The sign.** `g_i` counts years *before* the reference: 0 at the reference, increasing into the
past. A pith-ward member has the larger gap and the larger cal BP.

**Measured gaps** (OxCal's `V_Sequence`) give each offset an uncertainty. Marginalising it
convolves that member's shifted likelihood with a Gaussian, so counted gaps are the `sigma = 0`
case. RadiCal carries **one uncertainty per member** (distance from reference), treated as
independent. For imported OxCal `V_Sequence` files, gap errors accumulate in quadrature.

A measured gap also gives its member a width of its own:

```
p(t_i)  proportional to  L_i(t_i) · (R_−i ⊛ N(0, sigma_i))(t_i − g_i)
```

where `R_−i` is the product of every other member's smeared term: the member's own measurement
stays sharp, and what the rest of the series says about it is blurred by positional uncertainty.

**The grid** is the series' own support plus a small pad, at one year, located by a coarse first
pass (not the solver grid of A.6, which pads generously for Boundaries that a wiggle match lacks).

**Agreement.** Each member's index is A.8's formula, and the combination is the same
`100·(∏A_i/100)^(1/√n)`. This matters more here than elsewhere: the gaps are rigid, so a
misidentified member only degrades the overall fit. No threshold is printed — OxCal's combination
threshold scales with n, and RadiCal has not verified it against a real run.

**Outlier models** are refused in a wiggle match. Every member is the same calendar age plus a
fixed number — there is no room for a date to sit apart. The agreement index identifies misfits.

### A.14 Age-depth modelling

Deposition is modelled as a Poisson process along depth (Bronk Ramsey 2008). Between two levels
separated by `Δz`, the process contributes `k·Δz` events, so elapsed times are Dirichlet in the
normalised increments:

```
p(t_1 ... t_n | k)  =  Gamma(A) / prod Gamma(a_i)
                       * prod (dt_i)^(a_i - 1)
                       * T^(-(A - 1))
```

with `a_i = k·Δz_i`, `A = Σ a_i`, `dt_i = t_i − t_(i+1) > 0`, `T = Σ dt_i`, levels ordered oldest
(deepest) first.

This couples adjacent ages directly, and `k` couples every level at once, so it cannot factorise
for the exact solver. It is sampled instead. One level's full conditional is its likelihood times
two power terms (one per neighbour): one-dimensional and tabulated on the calendar grid.

**Coincident levels are forbidden.** The exponent `a_i − 1` is negative wherever `k·Δz_i < 1`
(the ordinary case), making the density diverge at zero. Adjacent levels must differ by at least
one grid cell; the remaining error is ordinary discretisation error, verified by refining the grid.

**`k` is a parameter, not a setting.** It is drawn from its own conditional over a log-uniform
prior, just as the shared outlier scale of A.7 is. The normalising terms above are carried rather
than dropped — with `k` free they are functions of it, and dropping them gives the wrong rigidity
selection rule.

**Undated levels** are parameters with a flat likelihood table. A uniform table drawn over the
interval the neighbours allow, weighted by the two deposition terms, is the correct answer; the
sampler needs no special case.

**One prior, not two.** Levels in a deposition sequence must not also be treated as a uniform
scatter between enclosing Boundaries (A.5). Two priors over the same intervals would give phase
widths wrong by a factor that nothing on screen would reveal.

**The band and rates** come from joint draws, for the reason A.9 gives. Bands are quantiles rather
than HPD regions: an envelope must nest, and two HPD regions on a multi-peaked posterior need not.

### A.15 Deriving a reservoir offset from position

For a site at `(φ, λ)`, the *N* nearest reservoir measurements are combined as calib.org does:

```
w_i       =  1 / σ_i²
ΔR        =  Σ w_i ΔR_i / Σ w_i
internal  =  1 / √(Σ w_i)
scatter   =  √( Σ w_i (ΔR_i − ΔR)² / Σ w_i  ×  n / (n − 1) )      [ 0 when n = 1 ]
σ         =  max(internal, scatter)
```

The reported uncertainty is the *larger* of the weighted uncertainty in the mean and the weighted
standard deviation (Bevington 1969) — confirmed against calib.org's page to the printed integer.
Both are rounded to whole years on apply.

The same combination is run over suspension feeders alone, and the difference is what the panel
reports as feeding-habit effect. Measured across the database, deposit feeders, carnivores and
migratory animals sit 35–55 years older than nearby suspension feeders; grazers and unknowns are
indistinguishable from them and are neither marked nor excluded.

### A.16 Spatial dispersal

Sites are the elements of A.5 opened by their own Boundary, at their dates' centroid; arrival
age is that Boundary's posterior. All three fits are made **per joint draw** and pooled, so
chronological uncertainty is inside the estimate rather than bolted on afterwards (see A.9).

**Wave of advance.** For site *i* at great-circle distance `d_i` from an origin:

```
t_i  =  t₀ − d_i / v  +  ε_i
```

Fitted by OLS on each draw, keeping `v = −1 / slope` and `t₀`. Older is a larger cal BP, so a
spreading front has `v > 0`; a rising slope gives negative speed. The *J* values of `v` and `t₀`
are summarised as any duration is (median, 68.3%/95.4% HPD). Each site's residual is reported with
its own spread over draws.

**Trend surface.** In a Lambert azimuthal equal-area projection centred on the sites' centroid:

```
t_i  =  a + b·x_i + c·y_i
```

per draw; speed is `1 / √(b² + c²)`, direction is the gradient's azimuth, pooled with circular
mean and 95% interval. Isochrons are contours of the median plane.

**Origin profile.** The wave-of-advance fit repeated over a grid of candidate origins (~1°), keeping
the residual sum of squares per candidate per draw. The grid is the union of a disc around every
site (~700 km or half the longest spanning-tree link plus 150 km, whichever is larger). Candidates
where sites get *older* with distance are where the front went, not where it started, and are
excluded.

**Refusals:** fewer than six sites, too few distinct distances from origin, or sites too close to a
straight line for a plane to be determined (measured by the singular-value ratio of centred
coordinates). Residuals are treated as independent; spatial autocorrelation is omitted, so the
interval on `v` is optimistic. Distances are great-circle.

### A.17 Kernel density estimates

OxCal's `KDE_Plot` method (Bronk Ramsey 2017). Each **draw** takes one calendar age per date and
builds a Gaussian KDE from those points:

```
f(t) = 1/(n h) sum_i K((t - t_i) / h),     K = N(0, 1)
```

The plotted curve is the mean of `f` over draws; the band is its standard deviation. In a Group
Calibration, draws are from each date's calibration; in a Bayesian analysis, from the solver's
joint draws (500 states thinned evenly).

**Kernel width.** Silverman's rule (1986) serves as a ceiling:

```
h_S = (4/3)^(1/5) sd(t) n^(-1/5)
h   = g h_S,        g ~ U(0, 1)
```

`g` is weighted by leave-one-out likelihood:

```
L(g | t) ∝ prod_i [ 1/((n-1) g h_S) sum_{j≠i} K((t_i - t_j) / (g h_S)) ] ^ ((n-2)/n)
```

**Inside a model**, kernels are reflected at the Boundaries bracketing the Phase (Bronk Ramsey 2017,
eq. 8): the density is zero outside, and the curve stays flat up to the transition. The bandwidth
likelihood uses plain kernels, as the paper states. Solver draws (grid years) are spread uniformly
across their grid cell first, since dates of a short phase can otherwise share one year and have no
spread.

**Computation.** Each joint draw's `g` is drawn directly from `L(g | t)`, evaluated on 32 cells
across (0, 1) then refined to 16 across the best. 500 draws are used. Kernel sums use linear
binning and convolution (cost grows with span, not n²), within ~0.1% of the direct sum.

### A.18 Same-event test

Ward and Wilson (1978), case 1. Given ages `x_i` with errors `s_i`:

```
x̄   = sum(x_i / s_i²) / sum(1 / s_i²)
s_x̄ = (sum(1 / s_i²))^(-1/2)
T   = sum((x_i - x̄)² / s_i²)
```

If the dates are one event, `T ~ χ²(n-1)`, and they are reported consistent when `T` is below
the 95th percentile (the 5% critical value). This is OxCal's `R_Combine` test.

A shared ΔR shifts all ages equally, leaving `T` unchanged. It is applied once, to the pooled age,
with its error added in quadrature at calibration. Adding `σ_ΔR` to each date's error before
pooling would count one uncertainty `n` times.

### A.19 Inbuilt age

A date whose material may be older than its event by an offset `s ≥ 0` with density `k(s)` has,
for an event at calendar age `t` (cal BP, so older is larger), the likelihood

```
L_event(t) = ∫ k(s) L(t + s) ds
```

where `L` is the date's ordinary calibration likelihood. For an allowance **up to N years**,
`k(s) = 1/N` on `[0, N]`; for **about N years**, `k(s) = e^(-s/N) / N`, followed to `10N`. The
offset touches only its own date, so it is integrated out of that date's likelihood once, like the
outlier kernels of A.7, and both solvers, the direct cross-check and the agreement index use the
result. On the solver grid each lag cell takes the allowance's probability over that cell from its
distribution function, so an allowance that is not a whole number of grid steps keeps its mean. The
calibration is evaluated past the grid's old end by the allowance's reach before smoothing, so an
event near the edge still sees all of its material's likelihood. TPQ and TAQ limiters bound the
event, so they are applied after the smoothing.

Against the sampler-free reference built for the Shag River Mouth corpus (Petchey et al.'s Layer
11–4 model, published maxima on the charcoal), P(Start 11 before AD 1360) agrees within 0.1 with
and without allowances, for the published dates, with WkA-56765 moved to Layer 11, and with it left
out.

## Bibliography

- Ammerman, A. J., and Cavalli-Sforza, L. L. (1971). Measuring the rate of spread of early farming
  in Europe. *Man* 6(4), 674–688.
- Bevington, P. R. (1969). *Data Reduction and Error Analysis for the Physical Sciences*. New York:
  McGraw-Hill.
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
- Bronk Ramsey, C. (2017). Methods for summarizing radiocarbon datasets. *Radiocarbon* 59(6),
  1809–1833.
- Bronk Ramsey, C. (2021). Replies to M. Price in "Additional details on KDE algorithm and/or source
  code", OxCal mailing list, 23–24 March 2021. https://groups.google.com/g/oxcal/c/ZfhqJt0qyck
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
- Galimberti, M., Bronk Ramsey, C., and Manning, S. W. (2004). Wiggle-match dating of tree-ring
  sequences. *Radiocarbon* 46(2), 917–924.
- Gelman, A., and Rubin, D. B. (1992). Inference from iterative simulation using multiple sequences.
  *Statistical Science* 7(4), 457–472.
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
- Reimer, P. J., and Reimer, R. W. (2001). A marine reservoir correction database and on-line
  interface. *Radiocarbon* 43(2A), 461–463.
- Reimer, P. J., Austin, W. E. N., Bard, E., Bayliss, A., Blackwell, P. G., Bronk Ramsey, C., et al.
  (2020). The IntCal20 Northern Hemisphere radiocarbon age calibration curve (0–55 cal kBP).
  *Radiocarbon* 62(4), 725–757.
- Silverman, B. W. (1986). *Density Estimation for Statistics and Data Analysis*. London: Chapman
  and Hall.
- Stuiver, M., and Polach, H. A. (1977). Discussion: reporting of ¹⁴C data. *Radiocarbon* 19(3),
  355–363.
- Stuiver, M., and Reimer, P. J. (1993). Extended ¹⁴C data base and revised CALIB 3.0 ¹⁴C age
  calibration program. *Radiocarbon* 35(1), 215–230.
- Svetlik, I., Jull, A. J. T., Molnár, M., Povinec, P. P., Kolář, T., Demján, P., et al. (2019). The
  best possible time resolution: how precise could a radiocarbon dating method be? *Radiocarbon*
  61(6), 1729–1740.
- Ward, G. K., and Wilson, S. R. (1978). Procedures for comparing and combining radiocarbon age
  determinations: a critique. *Archaeometry* 20(1), 19–31.

**Software.** Demján, P. `res14c` — reference implementation of the Svetlik et al. resolution
method, https://github.com/demjanp/res14c_online. OxCal (C. Bronk Ramsey), against whose 4.4 output
this app's agreement indices, outlier probabilities and modelled ages are validated:
https://c14.arch.ox.ac.uk/oxcal.html
