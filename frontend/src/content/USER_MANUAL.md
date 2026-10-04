# User Manual

## Table of Contents

1. [Getting Started](#getting-started)
2. [Creating an Analysis](#creating-an-analysis)
3. [Adding Dates](#adding-dates)
4. [Importing Dates from a File](#importing-dates-from-a-file)
5. [Calibration Curves](#calibration-curves)
6. [Marine Reservoir Offsets](#marine-reservoir-offsets)
7. [Model Structure (Bayesian only)](#model-structure-bayesian-only)
8. [Running an Analysis](#running-an-analysis)
9. [Viewing Results](#viewing-results)
10. [Durations and Differences](#durations-and-differences)
11. [Kernel Density Estimates](#kernel-density-estimates)
12. [Wiggle Matching](#wiggle-matching)
13. [Age-Depth Modelling](#age-depth-modelling)
14. [Geographic Analysis](#geographic-analysis)
15. [Exporting Data](#exporting-data)
16. [Projects and Date Collections](#projects-and-date-collections)
17. [Comparing Analyses](#comparing-analyses)
18. [Best Practices](#best-practices)
19. [Appendix A: Mathematical Methods](#appendix-a-mathematical-methods)
20. [Bibliography](#bibliography)

## Getting Started

1. Open RadiCal in your browser: `http://localhost:8000` on the computer it is installed on, or
   your deployment's address.
2. Click **New Analysis** (top navigation bar, or from the Dashboard).

There is no multi-step wizard. New Analysis is a single form: settings on the left, adding
measurements on the right, the measurement list and (for a Bayesian analysis) the model structure
below, and a Run Analysis button at the bottom.

## Creating an Analysis

- **Analysis name**: required.
- **Analysis type**:
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
- **Result date format**: how calendar ages are displayed (e.g. cal BP, BC/AD). This only changes
  the labelling; the underlying calibration is unaffected.

## Adding Dates

Each date needs, at minimum, a **Sample name**, **Age (BP)** and **1σ error**. Also available:

- **Material**: free text (charcoal, bone, shell, etc.).
- **Sample type**: Terrestrial or Marine. Marine samples get an extra **ΔR** (local reservoir
  offset) field with its own 1σ error; both are required together, since they're combined in
  quadrature with the measurement's own uncertainty.
- **Curve**: IntCal20 (Northern Hemisphere terrestrial), SHCal20 (Southern Hemisphere
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

`Options(){ Resolution=n; };` sets the analysis's **Resolution** (see
**Model settings** under [Model Structure](#model-structure-bayesian-only)). Other options, such as `kIterations`, configure OxCal's own run
and are skipped with a note.

`D_Sequence` and `V_Sequence` come across as a **wiggle match**, with each `Gap()` turned into a
distance from the youngest member of the series; `P_Sequence` comes across as an **age-depth
model**, reading each element's `z=` as its depth below the surface. A `Date()` with a `z=` inside a
`P_Sequence` becomes an undated level — the "how old is 47 cm" an age-depth model exists to answer —
and a `Date()` anywhere else is still refused, having nothing to bound it.

A simulated date, `R_Simulate("name", -1000, 25)`, comes across as an ordinary date whose
radiocarbon age is the curve's own value at that calendar year (OxCal's numbering: negative is BC),
plus any ΔR in force, with the error given. OxCal adds random scatter to a simulated measurement,
so its results vary slightly from run to run; this app leaves the scatter out, so the imported model
gives the same result every time. The date's notes record that it was simulated and for which year.

OxCal's language is larger than what this app models. Anything that would change the posterior and
cannot be honoured **fails the import** with a message naming it, rather than being read as the
nearest thing that is implemented: `Tau_Boundary`, `Sigma_Boundary` and `Zero_Boundary`
(non-uniform transitions), `C_Date`, `R_Combine`, `U_Sequence` (a different deposition prior from
the `P_Sequence` this app does model), a curve the app does not have, or a custom `Outlier_Model`.
A `D_Sequence` or `V_Sequence` nested inside a larger model fails too: this app solves a wiggle
match on its own, and reading its members as ordinary dates in a phase would report a match that
was never made. An unrecognised command fails it too. Commands that only affect what OxCal plots (`Sum`, `KDE_Plot`,
`Span`, `Difference`, `Order`…) are skipped with a note, since they query a posterior rather than
shape one. So are the display attributes: a `color="red";` written on a date or a phase sets the
colour OxCal draws it in, and a note says the colours were passed over. An assignment that is not
one of those still fails the import, naming the key — an unknown `key=` could be setting a
likelihood, and it is not guessed at.

When a model is refused but its dates can still be read, the import offers to bring in the dates
without the model, naming the command that stopped it. The same applies when every date sits inside
a `Sum`. Each date keeps its curve, ΔR and outlier setting, and the name of the Phase (or other
command) it sat in becomes its context; the dates are then calibrated independently. A problem with
a date itself, such as a curve the app does not have, still stops the import.

Two differences in how the file is read are reported rather than left implicit: a date sitting
directly inside a `Sequence` is put in a Phase of its own (this app attaches dates to Phases; its
position, and so the ordering, is unchanged), and several independent top-level items are wrapped
in one Phase, which asserts no ordering between them.

## Calibration Curves

**Curves** in the top navigation lists every curve this app can calibrate against, and is where you
add your own. Three are shipped with it (IntCal20, SHCal20 and Marine20) and cannot be edited or
removed.

**Curves you import or mix are private to your account.** Nobody else signed in to the same
installation sees them, in this list or in any curve picker, and they cannot open or download one.
Only the three shipped curves are common to everybody. This is deliberate: a curve you are still
checking should not appear in a colleague's picker looking like a supported option, and a curve
added for one site years ago should not quietly become something a newcomer picks by mistake. If a
colleague needs a curve you have, send them the file — **Download** on the curve's row gives you
back exactly the bytes you imported — and they import it themselves.

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

## Marine Reservoir Offsets

A marine sample calibrates against Marine20, which already models the *global* average offset of
the surface ocean from the atmosphere. **ΔR** is the *local* deviation from that average — the
part a global curve cannot know — and it is entered as a value with its own 1σ uncertainty, both in
¹⁴C years. RadiCal takes a ΔR in three ways.

**Typed.** On the measurement form, a Marine sample type shows the ΔR and ± fields; in a project's
date collection, tick the dates and press **Set ΔR › Enter a value** to write one value to all of
them at once. Leave both fields empty to clear the offset, and a marine date with none set
calibrates as ΔR = 0 — the collection's ΔR column says **none set** on exactly those dates so they
are not mistaken for corrected ones.

**Imported.** An OxCal file's `Delta_R(...)` comes across with the dates it applies to (see
[Importing Dates from a File](#importing-dates-from-a-file)); a CSV can carry `delta_r` and
`delta_r_error` columns.

**Derived from position.** A date that has coordinates (see
[Geographic Analysis](#geographic-analysis)) can have its ΔR read off the published reservoir
measurements nearest to it, the way calib.org's Marine Reservoir Correction Database does it — and
to the same printed integer, because the app reproduces that page's own combination rule rather
than calling it (see [A.15](#a15-deriving-a-reservoir-offset-from-position)).

### The reservoir database

The measurements come from Reimer & Reimer's Marine Reservoir Correction Database, mirrored on
this installation as a **snapshot** — frozen once stored and citable by id, like a calibration
curve, so a published date keeps meaning what it said even after the source database grows.
Nothing is shipped with the app: on the **Curves** page, under the curves, the **Marine reservoir
database** card says what is mirrored. **Check calib.org** asks the source what it currently holds
(one small request), and **Fetch the database** — or **Fetch a new snapshot** when a mirror already
exists — brings it down (about a megabyte). Neither happens on its own. Fetching a newer snapshot
adds it beside the old one; dates that cite the old one go on citing it.

### Deriving a value

In a project's date collection, select the dates from **one site** — they must all be placed, and
at the same coordinate, since a centroid of two sites is a place neither of them is — and press
**Set ΔR › Derive from position**. The panel shows:

- **The value**, large, as `ΔR ± σ` from *N* points, with the database it came from.
- **Nearest points: N.** The weighted mean is taken over the *N* reservoir measurements nearest the
  site, and it moves with *N* — somewhere like Callao it moves by nearly 200 years across the
  slider's range. Slide it before accepting a number; the default of 10 is calib.org's own.
- **The distance to the nearest measurement**, always, and the farthest one used. Beyond 100 km
  the line turns amber: the value is then a regional average rather than a local offset, and
  worth saying so in print.
- **The point list**: each measurement's distance, ΔR, feeding habit and locality. Untick a point
  to leave it out; a point with no published uncertainty cannot be weighted and is shown struck
  through rather than silently dropped, so a nearest-10 that is really a nearest-9 says so.
- **Feeding habit.** Deposit feeders can take up old carbon from sediment, carnivores sit about
  35 years older than nearby suspension feeders, and a migratory animal may not have lived where
  it was found. Those three habits are marked in the list, and one line under the value gives
  the counterfactual — *Suspension feeders only (7 of 10): −188 ± 61* — as a link that swaps the
  value over. When the two differ by more than a published ΔR typically claims for itself, the
  panel says so as a warning, because which set you use is then a decision rather than a
  detail. Grazers and unknown habits are not marked: measured against the suspension pool they
  are indistinguishable from it.
- **Apply to.** Where the selection can be widened, choose whether the value lands on the selected
  dates only, every date at the same named location, or every date within a distance of one. Only
  marine dates take it; terrestrial ones in the target are skipped and the button says how many.

**Apply** writes the rounded value and uncertainty to the dates, together with a record of how it
was derived — the snapshot id, the centre, *N*, the points used and excluded, and the
suspension-only alternative — so a derived offset never loses its derivation. It then travels with
the date into every analysis it is deployed to.

## Model Structure (Bayesian only)

Optional, but this is what makes a Bayesian analysis different from a Group Calibration. Build the
model as nested Sequences and Phases with Boundaries placed where you want them,
then assign each date to a Phase. RadiCal uses the same terminology as Oxcal. 
TPQ (terminus post quem, a maximum age) and TAQ (terminus ante quem, a minimum age) 
limiters can be set on a date or on a whole Phase.

Reached via **Specify Model Structure** / **Edit Model Structure**, which has an **Editor** tab for
building the tree and a **Matrix diagram** tab showing it drawn. The Results page renders
the same diagram for a completed analysis, and the Posterior distributions plots follow its order.

### Reading the Model diagram

The diagram is a picture of the model tree. Build the structure on
the Editor tab and the diagram follows. **Oldest is at the bottom** as in a stratigraphic profile.

- A **context** (solid box) is a Phase that dates are attached to, showing how many it holds and
  any TPQ/TAQ limiter.
- A **box drawn around other boxes** is a Sequence or a grouping Phase. A solid edge means a Sequence, which puts what it holds in order; a dashed edge means a Phase, which groups without ordering.
  A Phase containing two Phases says they belong together and says nothing about which came first,
  so they sit side by side inside it.
- A **labelled rule** across a group is a Boundary, a modelled transition, with its 95% range
  once the analysis has run.
- An **arrow** means the lower block is older. Arrows are drawn only between adjacent blocks, and
  only where no Boundary rule already separates them: a relationship implied by another one is left
  out, which is what keeps a nested model readable.

Nothing in the diagram is draggable. Position carries meaning here (a context sits centred beneath
the group that contains it), so it is laid out rather than arranged by hand.

**Model settings**, on the New Analysis form itself:
- **Solver**: Automatic (recommended: exact integration where the model allows it, sampling
  otherwise), Exact solver only (fast, but only works on chain-shaped models), or MH/Gibbs (works on all models, uses the same MCMC sampling method as OxCal throughout). See
  [Choosing a solver](#choosing-a-solver) for when to override Automatic and what it costs.
- **Posterior draws**: how many samples each date's posterior is reported with.
- **Resolution (years)**: the calendar step modelled ranges and durations are reported at, as
  OxCal's `Resolution` option. 5 years by default; 1 or 2 can suit dates from the last few centuries,
  whose ranges span only a few decades, and 10 or 20 dates from the Pleistocene. A saved analysis
  keeps the step it was run at until it is run again. Only the reporting changes: the posteriors, agreement indices
  and plots are the same whatever it is set to (see [A.4](#a4-summarising-a-posterior)).
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
with every date inside a bounded group, and no ordering the chain does not already imply. A Sequence
of Sequences counts, as long as each inner Sequence starts and ends with its own Boundary: it is the
same model as writing their contents out in one Sequence, and is solved as one. It computes 
the posterior on a calendar grid by message passing. There is no sampler, so nothing to converge, no 
run length to choose, and no calibration lobe the chain can miss. It is also the faster of the two, usually 
by a wide margin: a single deterministic pass, with no progress bar because there is no iteration to report.

**MH/Gibbs sampling** is OxCal's own scheme (Metropolis-Hastings within Gibbs) and takes any model:
branching structures, parallel groups, two groups ordered against each other with no Boundary
between them, dates sitting outside every Boundary. It is budgeted 20,000 sweeps, stops as soon as
the worst parameter reaches 95% on OxCal's own convergence measure, and extends up to 200,000 if it
has not; the achieved figure is reported with the results, and a run that fell short is flagged
rather than quietly returned.

By default four independent chains run at once on separate processor cores and stop together under
one rule: the convergence measure is read off their pooled draws, so the target is reached in about a
quarter of the sweeps per chain and the run finishes about three times sooner than a single chain
would. The first chain starts with every date at the peak of its own calibration; the others start
spread across the dates' calibrated ranges, one towards the older end, one in the middle, one towards
the younger end. The chains must also agree with one another before the run may stop — the
Gelman–Rubin R̂, reported with the results, has to sit under 1.05 — which is a check a single chain
cannot make on itself: a chain settled into one of a date's two calendar ranges looks perfectly
converged to itself, and only another chain that settled into the other range can show it up. That is
why the starts are spread: chains that all begin in the same range can all stay there and agree.
Agreement is strong evidence, not proof. A calendar range that none of the chains reaches is still
invisible to R̂, so where a result has two well-separated ranges and the decision rests on which one
is right, check it another way as well. For two common model shapes the app does this itself and
says on the Results page whether a direct calculation agrees (see [A.6](#a6-solvers)). The chain
count is set in the analysis options; one chain is the plain run.

Where both can be asked, they agree to under a year, and they are checked against each other on
every model both can handle.

| | Exact solver | MH/Gibbs sampler |
|---|---|---|
| Model shapes | chain-shaped only; fails rather than sampling | all |
| Result | deterministic; the same input gives the same numbers | stochastic; converges to the same posterior |
| Speed | one pass; typically much faster | up to 200,000 sweeps per chain, stopped as soon as converged; chains run in parallel |
| Convergence | not applicable, since nothing iterates | reported per run, within and between chains; can fall short and say so |
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

- **Results summary**: one row per date (and, for a Bayesian model, per Boundary), in the model's
  own structure order. Columns: the date's unconstrained ("unmodelled") calibration, its modelled
  posterior HPD range at the confidence you've selected, and diagnostics.
- **HPD confidence**: 68.3%, 95.4%, or 99.7%, the two-sided normal coverages of 1, 2 and 3
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
- **Posterior distributions**: Plotly charts, oldest first:
  - **Stacked** (default): every date (and Boundary) as one row each, sharing a single calendar
    axis, so overlap between dates is directly visible. Each row is scaled to its own peak, so
    heights compare precision, not probability.
  - **Individual**: one full-size plot per date/Boundary.
  - **Show boundary plots**: hide/show Boundary rows separately from dates.
  - **Show prior (unmodelled) distribution**: off by default; overlays each date's plain
    calibration (no stratigraphic constraints) as a grey shadow behind its modelled posterior, so
    you can see exactly how far the model moved it.

## Durations and Differences

Off by default, and turned on before the run: under **Model settings** for a Bayesian analysis
(**Durations and differences**) or **Reporting** for a Group Calibration (**Differences between
dates**). To add it to an analysis that has already run, **Edit** it, turn the option on, and run it
again; **Re-run** on its own reuses the settings the analysis was saved with.

Everything in this section is a **length of time in years**, never a calendar date, so none of it is
converted to BC/AD when you change the reporting format.

### The durations

- **Sequence**: from a Sequence's first Boundary to its last. How long the whole of it was in use.
- **Phase**: the interval a Phase's two Boundaries enclose. This is OxCal's `Interval()`, and it is
  the *modelled* duration: it is normally longer than the spread of the dates inside, because those
  dates are treated as a sample of the phase rather than the whole of it.
- **Hiatus**: the gap between two adjacent Boundaries, from the end of one phase to the start of
  the next. This is what OxCal's `Interval()` gives when placed between an end Boundary and a start
  Boundary. It is listed between the two phases it separates, under the Hiatus's own name if you
  gave it one and otherwise as "*earlier phase* → *later phase*". The Hiatus element itself adds
  nothing to the model, so two Boundaries side by side are a gap whether or not a Hiatus is drawn
  between them. A single Boundary between two phases is a shared transition, with no gap to report.
- **Dated events**: from the oldest to the youngest dated event in a Phase (OxCal's `Span()`).
  Nothing is inferred beyond the dates themselves.

Reading the second against the third is the point of having both. A phase whose dated events span 30
years but whose modelled duration is 250 is a phase your dates have barely sampled.

A Phase with fewer than two dates gets no "dated events" row, since one date has no span. A
Sequence with only one bounded group gets no Sequence row, because its Phase duration is already the same quantity.

### Phases through time

A figure showing every phase that can be placed in time on one calendar axis, one strip each, in
model order. A phase's shading is the probability it was **in progress** in that year, read off the
same joint draws as the durations: darker means more probable, and the hover gives the figure.
Along a Sequence the strips follow one another; across parallel branches, a column read top to
bottom shows which phases were probably running at the same time. A phase without Boundaries of its
own is represented by its dated events, and one represented by a single date has no extent to be in
progress over, so its strip is that date's own probability, in the dates' colour and scaled to its
peak. **Download PNG** saves the figure, and the PDF report includes it.

### Differences

For **phases**, the table compares only pairs whose order the model leaves open: phases in
different branches, such as two Sequences side by side inside one Phase. Those are the only phases
that can overlap, so the overlap is a finding. Phases in one Sequence are never compared, since the
Sequence already puts them in order: they cannot overlap, and the gap between two of them is
reported in Durations as Phase and Hiatus durations. A phase nested inside another is not compared
either, because it overlaps the outer one by construction. A model with no parallel branches has
no Differences table.

One row per pair. For two **phases** the figure is signed: **positive is a gap** between them in
years, **negative is an overlap**. For two **dates** in a Group Calibration, positive means the first
of the pair is the older.

**Overlap** grades how far the two are from being separable in time, from `none` to `extensive`.
For phases it is the probability that their modelled intervals intersect; for dates it is how far
the model is from being able to say which came first (100% is a coin flip). The bands are the same
68.3% and 95.4% levels every HPD in the app is quoted at, so `none` means "separated at 2 sigma" and
`extensive` means "overlapping at 2 sigma".

The years column beside it is headed **Separation** for phases and **Difference** for dates.

An analysis run before this rule may still list pairs either side of a single shared Boundary,
reading **shares a transition**. Run it again to drop them.

### What these cost

Every quantity here depends on several modelled ages at once, which means it can only be read off
*joint* draws of the posterior rather than the per-parameter distributions the rest of the page
shows. Those draws are the only Monte Carlo in the exact solver's output, so these carry a sampling
error, and the panel says how many draws they came from and whether those draws were independent
(the exact solver, or a Group Calibration) or an MCMC chain.
The reported ranges are quoted at a step that widens for a very long duration, rather than claiming
five-year precision the draws cannot support.

## Kernel Density Estimates

Off by default. Turn on **Kernel density estimate** under **Reporting** for a Group Calibration,
or **Kernel density estimates** under **Model settings** for a Bayesian analysis. To add them to an
analysis that has already run, **Edit** it, turn the option on, and run it again.

The plot shows how the dates are spread through time, as OxCal's `KDE_Plot` does. It is the
question a summed probability distribution (OxCal's `Sum`) is usually asked, without the Sum's
sharp peaks and cliffs, which mostly come from the wiggles in the calibration curve rather than
from anything the dated events did.

The line is the estimate; the shaded band is its ±1σ variability across draws. Read the band as
how robust the curve is to which calendar age each date really has and to the choice of kernel
width: where it is narrow, the shape is well supported. The kernel width the dates chose is printed
above the plot.

Two cautions:

- The estimate treats the dates as a **sample of one population of events**. Several dates from
  one context, or one well-dated site among sparsely dated ones, will raise the curve wherever they
  fall.
- It is **conservative**. Measurement uncertainty is not removed, so a KDE is somewhat wider than
  the underlying distribution of events, and its first and last edges sit too early and too late.
  Where the dates belong to a bounded phase, model them as one (a Bayesian analysis) and read the
  phase's Boundaries for its start and end.

In a **Bayesian analysis** there is one estimate per Phase holding two or more dates, drawn from
the *modelled* dates, as OxCal's `KDE_Plot` is when used in place of `Phase`. Where the Phase sits
between two Boundaries the curve stops at them rather than tailing off past them: the model does
not let its events fall outside, so neither does the estimate. This answers the second caution
above, and it is the better way to summarise a bounded phase.

A Group Calibration has one estimate of all its dates. Either way, at least two dates are needed.

## Wiggle Matching

For a series of dates whose separations are **counted** rather than inferred: blocks of tree rings
along one timber, varves in a lake core, samples taken at known intervals down a sequence. OxCal
calls it `D_Sequence`, or `V_Sequence` where the separations were measured and carry their own
uncertainty.

A wiggle match has **one** unknown — the calendar age at the reference point — because every other
member's age is that plus its own gap. RadiCal solves it in closed form: no sampler, nothing to
converge, and none of the Monte Carlo qualifications the other solvers carry. The Results page
labels it **Wiggle match**.

**Building one.** On the Model Structure page, **Build from data › Make it a wiggle match** puts every
date into one series. Then give each date its gap — or press **Set gaps from ring numbers** and enter the ring
number each sample came from, counting outwards from the pith. The app does the subtraction, which
is where the sign usually goes wrong.

The **Rings / Varves / Years** dropdown on the series says what the gaps were
counted in. It labels the axis and the results panel; the maths is the same either way.

**The convention, once:** a gap is years **before** the reference. It is 0 at the reference and
**increases into the past**, so a sample nearer the pith has the larger gap and the older age. The
reference defaults to the outermost sample, which is the one a felling date is quoted against.

**What it buys.** At a calibration plateau a single date is close to useless
and a long enough series is not: at 2500 cal BP, one date at ±25 gives a 285-year range in two
disjoint pieces, where eight samples over 250 rings give 20 years in one. But **length decides
whether the match resolves at all, and count only decides how tightly**. Twelve samples packed into
60 rings will not break a plateau; eight spread over 250 will. The app warns before the run when a
series is short, because that is a thing to know before the dates are commissioned rather than
after the answer comes back in two pieces.

**Reading the result.** The panel sits above the results table and gives the age at the reference
point, with its range at the confidence selected on the page. Every member's posterior is the *same
distribution moved by its own gap* — one answer read at several points on the timber, not several
independent results — so with counted gaps every member's range is the same width. A member whose gap was **measured** (entered with a ±) gets a width of its own — usually
wider, because its place in the series is uncertain, but narrower where its own measurement sits on
a steep stretch of curve and pins it more tightly than the series pins the reference.

The panel's figure places each laboratory measurement (±1σ) on the calibration curve at the
reference age plus its gap. A good match lies along the curve's wiggles all at once; a date that
sits off the curve is ringed, and usually has the wrong ring count. If the reference age splits into
several ranges, the series has not settled on one place on the curve: a longer series resolves it,
while more samples inside the same span usually do not.

The **combination agreement** is not a diagnostic beside the answer; it is the check that the match
happened. The gaps are asserted, so a sample misidentified by forty rings cannot be absorbed the way
a Phase would absorb it — it simply makes everything fit worse. RadiCal quotes the index and
deliberately quotes **no pass mark** against it: what OxCal compares a combination against depends on
how many dates are in the series, and the app does not print a threshold it has not checked against
a real OxCal run.

**No outlier model.** A wiggle match cannot apply one, and RadiCal refuses a member carrying one
rather than accepting the setting and ignoring it. It follows from the gaps being asserted: an
outlier model gives a date room to sit apart from the others, and a wiggle match has no room to
give — every member is the same calendar age plus a fixed number. The agreement index is what tells
you a member does not belong. (OxCal does allow `Outlier()` inside a `D_Sequence`; this is a
statement about this app.)

A wiggle match must currently be the **whole** model. One nested inside a larger structure is
refused by name rather than approximated.

## Age-Depth Modelling

For a core or a profile where the dates are separated by **depth**: peat, lake sediment, a
stratified section sampled at intervals. OxCal calls it `P_Sequence`, and it is what Bacon and clam
users expect.

Deposition is modelled as a Poisson process running down the depth axis, so the time between two
levels carries its own prior rather than being left uniform. This one *is* sampled — the ages are
coupled to each other by the deposition rate rather than only through their Boundaries, so it goes
to the MH/Gibbs solver, and the Results page says so.

**Building one.** **Build from data › Make it an age-depth model** on the Model Structure page puts
every date into one deposition sequence; each then needs its depth. Add an **Undated level** to the sequence and
give it a depth to ask "how old was this level" where nothing was dated — a tephra, a pollen
horizon, the base of a feature.

**The rigidity.** `k` is events per unit depth, and it decides how much the depths constrain the
ages: a large one drives them onto a straight line, a small one leaves little but the ordering. A
value chosen badly dominates the answer, so RadiCal **infers it from the dates** by default and
reports what it inferred — that is the **Rate inferred** setting on the sequence. **Rate fixed**
holds it at a value you give instead, which is what to use when reproducing a published
`P_Sequence(name, k)`. Read the band next to that number: a stiff model draws a straighter line
through a calibration plateau than the dates alone support. Where the inferred rate runs up against
the end of the range it was allowed, the panel says **Rate pinned at its limit** — the prior is then
shaping the answer as much as the data is, and the range should be widened.

**Reading the result.** The deliverable is the band — age against depth, with a median line and the
68.3% and 95.4% envelopes — not a stack of per-date posteriors. Beside it is the accumulation rate
between each pair of levels, in years per unit depth. Both are read off *joint* draws of the
posterior, for the same reason durations are: a rate is a function of two ages at once, and pairing
their separate posteriors gives an answer wrong in shape rather than merely in scale. Undated levels
are drawn with hollow markers, so it is visible which points carry a measurement.

## Geographic Analysis

Dates can carry the position they came from, and this allows three things: a reservoir
offset can be derived from where a marine sample is ([Marine Reservoir Offsets](#marine-reservoir-offsets)),
a model can be built one site at a time, and a Bayesian run can ask whether the dated sites
describe something *moving* across the landscape.

### Placing dates

A position is a **latitude** and **longitude** in decimal degrees, WGS84, minus for south and
west. Degrees and minutes are not read. Enter both halves or leave empty, do not enter just one side.

- **On the measurement form**, the two fields sit beside depth.
- **From a file**, a CSV with `latitude`/`longitude` columns (`lat`/`lon`, `x`/`y` and the
  common lab spellings are recognised).
- **On the map.** In a project's date collection, tick the dates from one site and press **Place
  on map**: click the map, or type the position, and it is written to every selected date — dates
  from one site share a coordinate. A click is
  rounded to about ten metres. Sorting the collection by **Position** puts every date at one
  coordinate in one run of rows.

### Basemaps

Every map in the app — placing dates, placing a dispersal origin, the spatial-order proposal and
the dispersal results — is drawn on one of two basemaps.

**The offline basemap is drawn by the app itself**, from coastline data it serves, and asks
nothing of anyone: no request leaves the browser. It is accurate to about a kilometre; paste a
recorded position for anything finer. This is the default for every project.

**The tiled basemap** is turned on per project with **Show a basemap with coastlines, rivers and
place names** (Edit Project). It is [OpenFreeMap](https://openfreemap.org)'s *Positron* — a
light-grey map drawn from OpenStreetMap data, with land one colour and the sea another, place
names in quiet grey, and roads only when zoomed close — trimmed of road names, airports and
vegetation fills so that an island is always an island and nothing on the map competes with the
marks drawn over it. Panning or zooming it tells OpenFreeMap's server which patch of ground is on
screen: not the coordinates of any date, but enough to locate a site. Worth it to drop a pin on a
field boundary; think twice for a restricted site. OpenFreeMap needs no account or key and every
request goes to that one host.

Once a project has turned tiles on, a small **OpenFreeMap** switch sits under each of its maps.
It flips between the two basemaps, applies to every map at once, and is remembered by the browser,
so a reader who prefers the offline map keeps it. Projects that have not turned tiles on show no
switch: the setting in Edit Project is where that decision is made.

The tiled basemap needs the internet. Before it is drawn the app checks that OpenFreeMap can be
reached; if it cannot, the map falls back to the offline basemap and says so underneath, then
checks again a few times and switches to tiles as soon as the server answers. Lines on the tiled
map — isochrons and spanning-tree links — are drawn along great circles, as the
offline map draws them, so the same figure reads the same way on either.

The Edit Project dialog also sets the project's **coordinate precision** — full, or rounded to
0.01°, 0.1° or 1° (about 1, 11 or 110 km) — which governs what leaves in exports and figures.
What is stored does not change, and the project's own table still shows the full value.

### Sites

For the models below, a **site** is an element opened by its own Boundary — a Sequence bracketed
by a start and an end, one per place — whose dates carry coordinates. Its position is the centroid
of its dates', and its arrival time is the posterior of the Boundary that opens it, not its oldest
date: you almost never excavate the genuinely first thing at a site, so the oldest date is a
*terminus ante quem* for arrival.

Placed dates sitting in the one starting phase every analysis begins with are therefore *not*
sites, and a run would refuse them. On the Model Structure page, **Build from data › Group dates
into sites** replaces the model with one bracketed Sequence per site, read off a column the dates
already carry — their **location** name, their **context**, or their **position** (dates within
5 km of one another). The menu says how many sites each key would make and how many dates carry
none; those go into an unassigned phase beside the sites. No order between the sites is asserted.

A Phase whose dates are more than 10 km apart is **refused** by name rather than averaged: two sites
in one phase is a modelling error, and quietly placing the phase in the field between them would
report a front that passed through somewhere nobody excavated.

### Proposing an order from the map

**Build from data › Propose an order from the map** builds a minimum spanning tree over the sites
by great-circle distance, walks it from one end, and shows the resulting order on the map beside
the model — as a **proposal** you accept or reject, never applied on its own. A spanning tree is
spatial adjacency, not chronology: accepting it asserts that everything in one site predates
everything in the next, which is a strong claim the data may refute. Its value is that accepting
it makes that claim *testable* — run the model and the sites that do not fit the spatial order
announce themselves in the agreement indices. Undo restores the previous structure.

### Spatial dispersal

On the New Analysis page, once the dates are placed, the Bayesian solver settings offer **Spatial
dispersal**. Off by default: a straight line through dated points returns a speed whether or not
there is a front. With it on, the run fits three things off the same *joint* posterior draws that
durations use — a speed is a function of many modelled ages at once, so it has to be read off draws
that were sampled together — and the Results page shows them above the results table, map first.

- **Regional gradient**, no origin assumed. A plane `t = a + b·x + c·y` fitted per draw in a local
  equal-area projection, reported as a **speed** in km/yr and a **direction of advance** with a
  circular 95% interval, and drawn as **isochrons** on the map. It is the look at the data with no
  hypothesis in it, which is why it comes first.
- **Where it could have started**: the wave-of-advance fit profiled over a grid of candidate
  origins, rendered as a region on the map rather than a point with false precision. The grid
  covers everywhere within about 700 km of a dated site — a disc around each, widened until the
  discs join into one zone — rather than a box around all of them, whose far corners were the
  cells furthest from everything and tended to win for it. If the best cell sits on the edge of
  the zone the panel says so: the fit was still improving where the search stopped, which usually
  means the dates fix the direction the front came from but not how far back it started.
- **Wave of advance** from a placed origin — `t = t₀ − d / v` per draw — reported as **front
  speed**, **arrival at the origin**, and the number of sites. Optional: press **Place an origin**
  under the switch to drop a pin (with the dates on the map for context) and the fit is added
  beside the two that assume none. Its map draws **isochrons as circles** round the origin — the
  model makes arrival a function of distance alone, so a line of equal arrival is a circle — and
  beneath it an **arrival against distance** plot puts every site at its distance and modelled
  arrival with the fitted front drawn through them, which is where a pause or a leapfrog shows.
  Beside the speed the panel states how widely the sites **scatter** about the front, set against
  the time the front takes to cross them: the speed's interval is how much the line moves between
  draws, not how far the sites sit from it. A negative speed is reported as negative: either the
  front ran the other way, the origin is not where it started, or these dates do not describe a
  front at all. Nothing flips a sign to make the answer look like a dispersal.

**Residuals are the finding.** Every site is coloured on the map by how far it departs from the
fit — orange where it is early, blue-grey where it is late, pale near zero — and the wave of
advance lists them in a table beside it: the signed residual in years — positive means the site
is *older* than the front predicts (**early**), negative *later* (**late**) — sorted by magnitude,
the ten largest shown first. A site within twice its own uncertainty of the front is marked
**fits** rather than given a direction its evidence does not support. Pointing at a row rings its
site on the map and the plot, and **Download CSV** saves the whole table. A plane's isochrons are always straight, parallel and evenly
spaced, so a front that curved, slowed or sped up shows as a patch of neighbouring sites all one
colour rather than in the lines. A single early outlier is where the
interesting archaeology usually is: a leapfrog, a maritime route, a redeposited sample, or a wrong
date. A fit that reported only a speed and an interval would hide it.

**Refusals.** Each fit needs at least **six** sites; with fewer, a line through them returns a
tidy speed with nothing left over for the data to contradict, so the number is withheld rather than
shown with a caveat. A trend surface also needs sites spread in two directions — for a transect,
place an origin and read the wave of advance instead — and a wave of advance needs the sites at
more than a few distinct distances from its origin. Each refusal names its reason on the panel.

**Reading the maps.** A plain scroll wheel over a map scrolls the page; hold **Ctrl** (**⌘** on a
Mac) to zoom, or use the **+**, **−** and **reset** buttons under it, which also work from the
keyboard. On a touch screen the read-only maps do not pan, so a swipe scrolls past them.

**Limits of the estimate.**

- The front is refitted on every posterior draw, so uncertainty in the *ages* carries into the
  speed. Uncertainty in the origin, in the coordinates, and in whether these sites are the true
  front (rather than the part of it that was excavated) is left out.
- Residuals are treated as independent. Neighbouring sites share whatever the model misses, so the
  interval on the speed is narrower than it should be — as for any least-squares fit to spatially
  correlated data. The **site scatter** figure beside the speed shows how far the sites actually
  sit from the front: the root-mean-square residual, and that as a share of the time the front
  takes to cross from the origin to the furthest site.
- The speed is a line refitted to each draw and summarised. A hierarchical model would estimate
  the front and each site's scatter together.
- For a group calibration, which has no Boundaries, sites are dated by their **earliest dated
  event**. The oldest date at a site is a *terminus ante quem* for arrival: the first thing at a
  site is rarely the thing excavated, so every arrival is late by an unknown amount, and later at
  the least-dug sites. The speed inherits that bias.

**Reading the origin search.** Shaded cells are candidate origins whose likelihood is at least the
shown share of the best cell's. Cells where sites get *older* with distance are where the front
went rather than where it started, so they are not scored. When the shaded region covers most of
the search area, the dates fix the direction of travel but not the starting point — the expected
result when sites lie along one axis, since sliding the start back along that axis fits almost as
well. A best cell on the edge of the search area means the same thing.

**Reading the regional gradient.** Its isochrons are straight, parallel and evenly spaced by
construction, so a curving or slowing front shows in the site colours rather than in the lines. The
direction's **concentration** runs from 0 (draws point every way) to 1 (all agree); below 0.5 the
panel marks the direction as uncertain.

**Negative speed.** Sites further from the origin are older. Either the front ran the other way,
the origin is elsewhere, or the sites do not form a front; the residuals tell these apart.

## Exporting Data

- **Export CSV** (on the Results page): the full results table, including the unmodelled
  comparison, in the currently-selected date format. Where a run produced durations and differences,
  they follow as a second block below it, after a blank line and their own header. They share no
  column with the results table, since every figure in them is a length rather than a date. A kernel
  density estimate follows in two more blocks: one row saying how it was made (dates, draws, kernel
  width), then the curve itself, one row per calendar year step with its density and ±1σ, so it can
  be redrawn in a spreadsheet.
- **JSON**: available via the API (`GET /api/results/{id}/export/json`) for anyone who wants the
  full result machine-readable; there's no button for it in the UI yet.
- **Curve data** (on a curve's browser page): **Export this window** saves the visible stretch of
  the curve as CSV (calendar age, radiocarbon age, error, local gradient); **Export profile** saves
  its whole expected-resolution profile. A curve itself can be downloaded from the Curves page, and
  an imported one comes back byte-for-byte as it was uploaded.
- **Export PDF** (on the Results page): a printable report of the whole analysis: what it is and
  when it was run, the curve(s) it was calibrated against with their references, which solver
  computed the posteriors, the results table in the model's own reading order, the durations and
  differences where there are any, the kernel density estimate where there is one, and a plot of every date and Boundary with its HPD ranges marked. Like the CSV it reads in the date format currently
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
by changing the analysis type you can instead do a group calibration. Choosing Single
Calibration for more than one date automatically stores it as a group calibration, since a single calibration is
one date on its own.

## Comparing Analyses

The same dates can be run in different analyses with different parameters. **Compare** puts them side by side and
computes the difference, which is otherwise a handful of numbers buried in two long tables.

Tick two to four finished analyses on a project page and press **Compare**. **The first one you
tick is the reference**: every shift and every ratio on the page is measured against it, and the
other runs are what is being held up to it. The comparison is an ordinary URL, so it can be
bookmarked or sent to a colleague.

**Reporting format** and **HPD confidence** are set once at the top and apply to every panel . A comparison whose
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
| Modelled date or Boundary | a 5-year grid, or the analysis's **Resolution** | independent of the solver's internal step, which varies with the model (see [A.4](#a4-summarising-a-posterior)) |
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

Curve values at non-grid calendar ages are linearly interpolated in both `mu` and `sigma_c`.
Beyond the curve's ends the likelihood is zero: the curve says nothing there, so neither does the
date, rather than carrying its last tabulated value outward.

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

**HPD regions.** Grid points are taken in order of decreasing probability *density* until the
*mass* they hold reaches the target coverage; the selected points are then grouped into maximal
runs, each reported as a range with its own share of the total probability. The two words matter on
the curve's own grid, which is not uniform: IntCal20 steps from 1 to 5 years at 5,000 cal BP, and a
5-year cell there holds five times the mass of a 1-year cell of the same density. Ranking by mass
would let a low-density cell in the coarse stretch outrank a high-density one in the fine, and the
region would come back a different shape either side of the step. Each point is therefore ranked by
its mass divided by the calendar width it stands for (`w(t)` of A.2), while what accumulates to the
level is the mass itself; on a uniform grid the two orderings coincide. Ties are broken toward the
lower index, so the same distribution always gives the same region.

Because calibration curves are not monotonic, a single date routinely produces several disjoint
ranges, and reporting only the outer envelope would claim probability in the near-empty gaps
between them. The percentages are shares of the entire distribution, so they sum to slightly more
than the level requested: the set is built from whole grid points and the last one taken overshoots.
This is standard radiocarbon reporting practice (Stuiver and Reimer 1993; Bronk Ramsey 2009a).

**Point estimates** are computed from the density, not from draws, so they carry no Monte Carlo
error even on the sampled path: mean and standard deviation as probability-weighted moments; median
by interpolating the cumulative mass at 0.5 across the cell edges, so it falls within a cell rather
than snapping to the first grid point past the half; mode as the highest-*density* grid point, again
mass divided by cell width, since on the curve's grid the heaviest cell is simply the widest.

**Rebinning, for modelled posteriors only.** A modelled posterior is multiplied by the model's own
constraints on top of an already-wiggly likelihood, and comes off a grid whose step the solver
chooses from the date range and the number of groups. Two problems follow. Reporting at that step
would make a published range depend on an implementation detail, and the same dates in a longer
sequence would come back with different widths. At a fine step the HPD picks out peak tops and
skips the dips between them.

Modelled ranges are therefore rebinned to the reporting step before the HPD is taken: five years,
or the **Resolution** set for the analysis. The
rebinning integrates the density: each grid point stands for the cell half a step either side of
it, the cumulative mass is read off the cell edges, and a reporting bin's mass is the difference
between the readings at its two edges. This is exact for a density that is constant within a cell,
and — unlike counting which grid *points* fall in each bin — it does not depend on how the two
grids happen to align. That alignment is not a detail: a solver step of 1.015 years puts four grid
points in one 5-year bin and five in the next, a ±20% sawtooth on the bin masses that the HPD then
selects on. The solver's step is also chosen to tile the five-year step exactly (a divisor of five
years below it, a multiple above), so a five-year bin is always a whole number of solver cells.

Setting a different **Resolution** changes only this rebinning. The solver's grid, and so every
posterior, draw and agreement index, is the same whatever it is set to. This differs from OxCal,
where `Resolution` is also the calculation grid. A step that does not divide five years, such as
1 or 2, does not nest in the solver's cells, which the integration above handles.

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
adjacent phases have no gap to measure. A deliberate gap is modelled as two Boundaries side by
side, conventionally with a Hiatus between them, and is reported as a Hiatus duration (see
[Durations and Differences](#durations-and-differences)).

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

**Exact integration** (chain-shaped models: a Sequence of Phases separated by Boundaries, or a
Sequence of such Sequences, each bracketed by its own Boundaries — the gap between one inner
Sequence's last Boundary and the next one's first is a group with no dates, contributing only the
ordering, so the model is the same chain written out flat). Every
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
calibration lobes, which single-site updating cannot do. A group in one of several parallel branches
is also offered the move together with the Boundary enclosing it on one side, since it cannot cross
to another lobe while that Boundary holds it in. Of the wide proposals, half jump by one of the
distances between the group's own calibration peaks, either way and blurred by a few years, rather
than by a draw from a fixed 120-year kernel: a fixed kernel proposes a 40-year jump often and an
80-year one rarely, wherever the lobes actually are. Each distance is proposed in both directions
from any state, so the proposal stays symmetric and the acceptance ratio is still the posterior
ratio alone. On a test model of several areas within a site, the two moves together brought a run that had stopped unconverged at
the 200,000-sweep ceiling to convergence in 50,000, and without the enclosure move a run converged
with the wrong weight on a group's two lobes (0.27 against the exact 0.40).

Two Boundaries never share a grid cell, a date may sit on one at half weight, and the sampler holds
to this exactly as the exact solver does. The span correction below divides by the number of ways
the interior Boundaries fit *strictly* inside a span, so letting two of them meet adds arrangements
the correction never divides out, most of all at the smallest spans. An earlier version let
Boundaries separated only by an empty Phase, or by a level of nesting, coincide; models with sterile
layers between dated ones, or Sequences inside a Phase, were then packed too tightly and could sit
in the wrong calibration lobe with every chain agreeing.

**Convergence** is OxCal's own diagnostic: the overlap integral between the distribution accumulated
over the first half of the run and over the second half, per parameter, targeting 0.95. The run
extends until the worst parameter meets the target or the sweep ceiling is reached, and the achieved
value is always reported.

**Several chains, one stopping rule.** By default four independent chains run at once, one per
processor core, each seeded differently (chain 0 with the run's own seed, so a single-chain run is
chain 0 of any wider one). Chain 0 starts with every date at its likelihood mode. Chain `c` of the
other `n − 1` starts with every date at the same quantile `q_c` of its own likelihood, `q_c` drawn
from the `c`-th of `n − 1` equal strata of 0.025–0.975, and the ordering is then repaired from the
oldest parameter down. One quantile for every date rather than one each, because the lobes that
matter belong to whole groups: a Phase's dates calibrate on the same stretch of curve, and a group
can only be found in its other lobe if its members start there together. They advance in step — the same burn-in, one round at a time — and the
convergence measure above is read off their rounds *pooled*, so the run stops when the pooled
histogram has converged rather than when each chain has on its own. The deficit in that measure is
mostly Monte Carlo noise, which falls as `1/sqrt(draws)`, so four chains reach the target in about a
quarter of the sweeps each: on the Nenthorn fixture, 54,000 sweeps on one chain against 12,000 each
on four, three times sooner in wall time for the same accuracy. Before it may stop, the run must also
bring the chains into agreement with one another on the Gelman–Rubin statistic (Gelman and Rubin
1992)

```
R-hat = sqrt( ((n-1)/n * W + B/n) / W )
```

with `W` the mean within-chain variance and `B` the between-chain variance of the chain means, both
read from the chains' histograms with `n` the draws each holds, held under 1.05 for every parameter.
That is the check a single chain cannot make on itself: a chain settled into one of a date's two
calendar ranges looks perfectly converged to itself, and only another chain that settled into the
other range can show it up, as `R-hat` in the tens. It follows that `R-hat` can only report a range
some chain has actually visited. Chains that all start from the same state can settle in the same
lobe, never leave it, and pass at 1.00, which is why the starts are spread. Spreading them makes
that failure less likely, but it does not rule it out. A run that hits the sweep ceiling with the chains
still apart is reported as unconverged whatever its pooled overlap says. The overlap of each chain's
own histogram with the pool is reported beside these for information, but not held to the 0.95
target: one chain holds a quarter of the pooled draws, so that overlap is noisier by construction and
sits near 0.93 when the chains agree perfectly by `R-hat`.

**Checking a sampled result.** Both convergence measures compare the sampler with itself: one half
of a run with the other, one chain with another. Neither can see a lobe no chain reached, and
neither can see a target that is wrong in the same way for every chain. So for two common shapes the
app also calculates the same model directly, without sampling, and compares the two:

- a chain of Phases and Boundaries written with nested Sequences, such as a Sequence of two
  Sequences;
- parallel chains between an outer pair, such as a site's Start and End Boundaries around a Phase
  holding one Sequence per area.

Given the outer pair, the branches are independent, so

```
p(S, E) ∝ C(S, E) · T0(S, E) · Π_b Z_b(S, E)
```

with `C` the span correction over the outer pair, `T0` the span factor and likelihoods of any dates
sitting directly in the Phase, and `Z_b(S, E)` branch `b` integrated with its ends strictly between
`S` and `E`. Each `Z_b` is a cumulative sum of the branch's end-to-end transfer product, and each
branch's own Boundaries then come from the chain's message passing with an end-to-end weight in place
of its span correction. A shared outlier scale is handled by conditioning on it: for every
combination of scale nodes the model is an ordinary one, so each is integrated and the results are
mixed by their evidence under the same uniform prior the sampler uses. Everything that defines the
target (likelihoods, Boundary limits, the span correction) is taken from the sampler's own model, so
the two calculate the same posterior and should agree up to Monte Carlo noise.

Each Boundary is compared on 5-year bins by total variation distance, the share of probability that
sits in a different place in the two answers. Over 0.15 is reported on the Results page as a
difference, naming the Boundaries. Runs that agree sit under about 0.08. Treat a difference as the
direct calculation being right: it has no lobe to miss. Other model shapes are not checked, and the
solver record says why.

The pooled histogram is the sum of the chains' and so assumes every chain has mixed, which is
exactly what `R-hat` checks. The chains run as separate processes and never touch the database; the
count is set in the analysis options, and one chain is the plain run.

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

The standard OxCal diagnostic (Bronk Ramsey 1995), comparing each date's modelled posterior `p`
against its own likelihood `L`, both as densities in calendar years:

```
A_i = 100 * integral(p * L) / integral(L^2)
```

That is the posterior expectation of the likelihood, scaled by the likelihood's overlap with itself.
A date the model leaves alone scores 100. One the model had to push off its own likelihood scores
low; one the model *sharpens* onto the peak of its likelihood scores above 100, so values over 100
are expected. Below about 60 is the conventional threshold for a second look.

`Amodel` is the same expectation over the **joint** posterior, every date at once, and `Aoverall`
combines the per-date indices as though they were independent:

```
Amodel   = 100 * (E[product of L_i(t_i) / integral(L_i^2)])^(1/sqrt(n))
Aoverall = 100 * (product of A_i/100)^(1/sqrt(n))
```

`Amodel` falls below `Aoverall` when the model's structure is doing work, because it sees the
correlation the ordering puts between the dates. It is a Monte Carlo average over the run's joint
draws, good to about ±0.5.

**Resolution.** The indices are ratios of integrals, and the right value is the one a finer grid
converges to. The likelihood is evaluated exactly at every posterior point, and `integral(L^2)` is
taken at one-year steps, because calibration curves carry structure at one year. OxCal's own
indices depend on its calculation resolution: the same model gives `Aoverall` 100.4 at OxCal's
default five years and 105.4 at `Options(){ Resolution=1; };`. This app matches OxCal run at one
year (per-date rms 0.23–0.30, `Aoverall` 105.5 against 105.4, `Amodel` 100.9 against 100.6). Against
a default OxCal run it reads about 0.7 higher per date, and several points higher overall.

**Outlier models.** A date under an outlier model is scored against the likelihood term the model
actually contains: the date fits either as it stands or moved by a shift, the second with the
outlier *prior* `p`. The expectation runs over the shift and the outlier scale as well as the date,
the scale taken from each posterior state where the sampler draws it. A date explained only as an
outlier therefore scores about `100 p`, near 5 at the usual 0.05, and never more. Each outlier model
in use also adds one term to `n`, as OxCal counts its scale parameter. Against OxCal at one year this
reproduces the per-date indices to rms 0.4–0.5 and the model indices to within about 1, flagged
dates included (Shag River Mouth's NZ7756: 5.2 against OxCal's 5.4).

Note the limits of the diagnostic: it detects a date the *model had to move*, which is not the same
as a date that does not belong. On an unconstrained phase, a date 600 radiocarbon years from its
neighbours scores 100.5, because the phase simply widens. Use an outlier model to find a date that
is merely wrong.

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

On the sampled path the draws are whole states of the chain, thinned evenly through the run and
pooled across every chain that ran (A.6). They are not independent: consecutive states of a Markov
chain are correlated, so a duration read off them converges more slowly than the marginals beside
it, and the run is not extended on its account. The report says which kind of draws it holds.

**The durations** are OxCal's quantities under the app's own names: the *Sequence duration*
(first Boundary minus last), the *Phase duration* (the interval a pair of Boundaries encloses,
OxCal's `Interval`), the *Hiatus duration* (the same between two adjacent Boundaries with no phase
between them), and the *dated-events* or sample duration (the range of the dates themselves,
OxCal's `Span`). The gap between the last two is how much of the phase the dates failed to sample.

**Differences** between two phases whose order the model leaves open are the signed years between
their modelled intervals, positive
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
5-year calendar step (or the analysis's **Resolution**, which replaces it as the finest step) would
claim a resolution the draws do not have. The histogram is capped at 40
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

### A.13 Wiggle matching

A series whose separations are counted has **one** free parameter. Writing `t` for the calendar age
at the reference point and `g_i` for member `i`'s gap,

```
p(t)  proportional to  product over i of  L_i(t + g_i)
```

with each `L_i` the ordinary calibration likelihood of A.2 — curve error, ΔR and any TPQ/TAQ
limiter included. Every member is evaluated on a shared grid shifted by its own gap, the logs are
summed, and the result exponentiated: `O(n·M)`, deterministic, and exact up to the grid step. There
is no sampler, so none of A.9's Monte Carlo qualifications apply.

**The sign.** `g_i` counts years *before* the reference, so it is 0 at the reference and increases
into the past: a member's calendar age is `t + g_i`, and a member nearer the pith has both the
larger gap and the larger cal BP. Rings count forward in time while cal BP counts backward, which
is why the direction is stated rather than left to be inferred — reversed, the model still produces
a tight answer, in the wrong place.

**Measured gaps** (OxCal's `V_Sequence`) give each offset an uncertainty `G_i ~ N(g_i, sigma_i)`.
Marginalising it convolves that member's shifted likelihood with the Gaussian, so the fixed-gap case
is exactly `sigma = 0` of the same expression. RadiCal carries **one uncertainty per member** —
that member's distance from the reference — and treats them as independent. OxCal's gaps form a
chain, so two members beyond a shared gap have correlated distances; importing an OxCal
`V_Sequence` accumulates its gap errors in quadrature and says that it did.

A measured gap also gives **its own member** a width of its own. Member `i`'s age is `t + G_i`, and marginalising the
reference age as well as every gap leaves

```
p(t_i)  proportional to  L_i(t_i) · (R_−i ⊛ N(0, sigma_i))(t_i − g_i)
```

where `R_−i` is the product of every other member's smeared term: the member's own measurement
stays sharp, and what the rest of the series says about it is blurred by how well its position in
the series is known. Usually that makes the member wider than the reference; where its own
measurement is sharp it can make it narrower. With `sigma_i = 0` this is the reference moved
bodily, as for a counted gap.

**The grid** is the series' own support plus a small fixed pad, at one year, located by a coarse
first pass. It is not the solver grid of A.6: that one pads by six times the dates' width so that
Boundaries have room, and a wiggle match has no Boundaries, so at a fixed point budget the padding
would be paid for in resolution — several years per cell on a long series, quantising an answer
whose whole point is a range ten years wide.

**Agreement, and no outlier model.** Each member's index is A.8's, comparing its modelled posterior
(the reference's shifted, or its own as above for a measured gap) against its own unmodelled
likelihood; the combination is the same
`100·(∏A_i/100)^(1/√n)` A.8 uses. It carries more weight here than elsewhere: the gaps are
asserted, so a misidentified member cannot be absorbed by the model widening — it can only make
everything fit worse. No threshold is quoted against it: the one OxCal uses for a combination scales
with n, and this app does not print a threshold it has not pinned against a real run.

The same rigidity is why an outlier model (A.7) is **refused** rather than applied here. Mixing in a
broad component would marginalise an offset scale independently per member — the approximation A.6
describes for the exact solver, not OxCal's shared scale — and on a series whose members are one
rigid distribution that is a stronger assumption than it appears. Refusing is the honest answer
until there is a real OxCal `D_Sequence` run with `Outlier()` in it to hold an implementation
against.

### A.14 Age-depth modelling

Deposition is modelled as a Poisson process along depth (Bronk Ramsey 2008). Between two levels
separated by `Δz` the process contributes `k·Δz` events, so over an ordered set of depths the
elapsed times are Dirichlet in the normalised increments:

```
p(t_1 ... t_n | k)  =  Gamma(A) / prod Gamma(a_i)
                       * prod (dt_i)^(a_i - 1)
                       * T^(-(A - 1))
```

with `a_i = k·Δz_i`, `A = Σ a_i`, `dt_i = t_i − t_(i+1) > 0` and `T = Σ dt_i`, levels ordered oldest
(deepest) first.

This couples adjacent ages *directly*, and `k` couples every level at once, so it cannot factorise
along the message passing of A.6's exact solver — the same obstruction that stops the exact solver
sharing an outlier scale. It is sampled instead. Holding everything else fixed, one level's full
conditional is its own likelihood times two power terms, one per neighbour: still one-dimensional
and still tabulated on the calendar grid.

**Coincident levels are forbidden** rather than floored. The exponent `a_i − 1` is negative wherever
`k·Δz_i < 1`, which is the ordinary case, so two levels at the same age is a genuine divergence of
the density and not a numerical inconvenience. Adjacent levels are required to differ by at least
one grid cell, and the remaining error is an ordinary discretisation error — checked by refining
the grid.

**`k` is a parameter, not a setting.** A fixed rigidity chosen badly dominates the answer, so it is
drawn from its own conditional over a log-uniform prior, exactly as the shared outlier scale of A.7
is. That is also why the normalising terms above are carried rather than dropped: with `k` fixed
they are constants, but with `k` free they are functions of it, and dropping them would let the data
choose a rigidity by the wrong rule. The inferred value is reported, and flagged when its posterior
has run up against the end of the prior's range.

**Undated levels** are parameters whose likelihood table is flat. A uniform table drawn over the
interval the neighbours allow, weighted by the two deposition terms, is precisely the right answer,
so the sampler needs no special case for them.

**One prior, not two.** A deposition sequence's levels must not also be treated as a uniform scatter
between the enclosing Boundaries (A.5): they are contained by them, and charged no `(span)^(-K)` on
top of their own interval prior. Two priors over the same intervals would give phase widths wrong by
a factor that nothing on screen would reveal.

**The band and the rates** are functions of several ages at once, so they come off joint draws, for
the reason and with the qualification A.9 gives. The bands are quantiles rather than HPD regions:
an envelope has to nest, and two HPD regions on a multi-peaked posterior need not.

### A.15 Deriving a reservoir offset from position

For a site at `(φ, λ)`, the *N* reservoir measurements nearest it by great-circle distance are
taken from the snapshot, less any with no published uncertainty and any the user excluded, and
combined as calib.org's Marine Reservoir Correction Database combines them:

```
w_i       =  1 / σ_i²
ΔR        =  Σ w_i ΔR_i / Σ w_i
internal  =  1 / √(Σ w_i)
scatter   =  √( Σ w_i (ΔR_i − ΔR)² / Σ w_i  ×  n / (n − 1) )      [ 0 when n = 1 ]
σ         =  max(internal, scatter)
```

The reported uncertainty is the *larger* of the weighted uncertainty in the mean and the weighted
standard deviation of the points (Bevington 1969), not the two in quadrature — the rule was
confirmed against the source's own page to the printed integer, and it is what lets a derived value
be reproduced from calib.org by a reviewer. Both are rounded to whole years on apply.

The same combination is run a second time over the suspension feeders alone, and the difference
between the two is what the panel reports as the effect of feeding habit. Measured across the
database, deposit feeders, carnivores and migratory animals sit some 35–55 years older than the
suspension feeders near them; grazers and unknown habits do not differ from them at any radius, so
they are neither marked nor excluded.

### A.16 Spatial dispersal

Sites are the elements of [A.5](#a5-the-bayesian-model) opened by their own Boundary, at the
centroid of their dates' coordinates; a site's arrival age is that Boundary's posterior. All three
fits are made **per joint draw** and pooled, so the chronological uncertainty is inside the
estimate rather than bolted on afterwards, for the reason [A.9](#a9-durations-and-differences)
gives.

**Wave of advance.** For site *i* at great-circle distance `d_i` from an origin,

```
t_i  =  t₀ − d_i / v  +  ε_i
```

fitted by ordinary least squares on each draw, keeping `v = −1 / slope` and `t₀`. Older is a
larger cal BP, so a front spreading outwards has ages that fall with distance and `v > 0`; a rising
slope is reported as a negative speed. The *J* values of `v` and `t₀` are summarised as any
duration is — median and 68.3% / 95.4% HPD — and each site's residual `t_i − (t₀ − d_i / v)` is
reported in years with its own spread over draws.

**Trend surface.** In a Lambert azimuthal equal-area projection centred on the sites' centroid
(chosen by the data, so high latitudes are not distorted by a global default),

```
t_i  =  a + b·x_i + c·y_i
```

per draw; speed is `1 / √(b² + c²)` and the direction of advance is the azimuth of the gradient,
pooled with a circular mean and a circular 95% interval, since a direction near due north cannot be
averaged as a number. Isochrons are contours of the median plane.

**Origin profile.** The wave-of-advance fit is repeated over a grid of candidate origins at roughly
one degree, keeping the residual sum of squares per candidate per draw; the result is drawn as a
likelihood region. The grid is the union of a disc around every site — 700 km, or half the longest
link of the sites' minimum spanning tree plus 150 km, whichever is larger, so the discs always join
into one zone — cut to whole-degree cells; a rectangle around the sites was tried first, and its
corners, being the cells furthest from every site, won on a surface that rises away from the front.
Candidates from which the sites get *older* with distance are where the front went, not where it
started, and are not scored; their number is reported. The report also says when the best cell
lies on the zone's rim, meaning the fit was still improving where the search stopped.

**Refusals.** Fewer than six sites, sites at too few distinct distances from the origin, or sites
too close to a straight line for a plane to be determined (measured as the ratio of the singular
values of the centred site coordinates), each withhold the fit and say why. Residuals are treated as
independent; spatial autocorrelation is not modelled, and the interval on `v` is optimistic to that
extent. Distances are great-circle.

### A.17 Kernel density estimates

The method is OxCal's `KDE_Plot` (Bronk Ramsey 2017). Each **draw** takes one calendar age
`t_i` for each of the `n` dates, from that date's own calibrated distribution, and builds an
ordinary Gaussian KDE from those `n` points:

```
f(t) = 1/(n h) sum_i K((t - t_i) / h),     K = N(0, 1)
```

The plotted curve is the mean of `f` over the draws, and the band is its standard deviation at
each calendar year. In a Group Calibration the draws are taken from each date's own calibration; in
a Bayesian analysis they are the solver's joint draws of the Phase's dates, 500 states thinned
evenly from the many thousands it keeps. Pooling every draw of every date under one kernel would not be the same: that
is a smoothed Sum, still carrying each date's full calibrated width.

**Kernel width.** Silverman's rule (Silverman 1986) is optimal when the underlying distribution is
normal, and over-smooths anything with more than one mode, so it is used as a ceiling:

```
h_S = (4/3)^(1/5) sd(t) n^(-1/5)
h   = g h_S,        g ~ U(0, 1)
```

`g` is weighted by how well each date is predicted by a KDE of all the others:

```
L(g | t) ∝ prod_i [ 1/((n-1) g h_S) sum_{j≠i} K((t_i - t_j) / (g h_S)) ] ^ ((n-2)/n)
```

The power `(n-2)/n` reflects that the mean and standard deviation have already been taken from the
`n` points. At `n = 2` it is zero, and `g` keeps its uniform prior.

**Inside a model** each state's kernels are reflected at the Boundaries bracketing the Phase in
that state (Bronk Ramsey 2017, eq. 8): a date near a Boundary gains its mirror image beyond it,
and the density is zero outside. The curve then stays flat up to the transition instead of
rounding off, and still integrates to one. The bandwidth likelihood above is left on plain kernels,
as the paper states it. The solvers work on a calendar grid, so their draws are grid years; each is
spread uniformly across its grid cell first, which is the continuous posterior the grid stands for.
Without this, the dates of a short phase would sometimes share one year and have no spread at all.

**How it is computed.** OxCal moves `g` by Metropolis-Hastings inside its MCMC. Here each joint
draw of the dates is taken as given (exact draws from each calibration, or the solver's joint
draws), and `g` is drawn directly from `L(g | t)` for that draw. That is evaluated on 32 cells
across (0, 1), and then 16 finer cells across the best of them. 500 draws are used. Every kernel sum is a histogram of the points,
linearly binned at an eighth of the kernel width and convolved with the kernel. The cost therefore
grows with the span of the dates rather than with `n²`, and the result is within about 0.1% of the
direct sum. A date the kernel leaves with no neighbour in reach is summed directly, so it lowers the
likelihood by the right amount rather than making it zero.

Not yet checked against an OxCal `KDE_Plot` export.

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

**Software.** Demján, P. `res14c` — reference implementation of the Svetlik et al. resolution
method, https://github.com/demjanp/res14c_online. OxCal (C. Bronk Ramsey), against whose 4.4 output
this app's agreement indices, outlier probabilities and modelled ages are validated:
https://c14.arch.ox.ac.uk/oxcal.html
