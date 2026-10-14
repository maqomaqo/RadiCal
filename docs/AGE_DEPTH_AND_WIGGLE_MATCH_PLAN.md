# Wiggle matching and age-depth modelling — implementation plan

> **Built 2026-09-09.** Everything in §8's order of work is in the app:
> `services/wiggle_match.py`, the deposition model in `services/gibbs.py`, both
> importers, the new element types and their entry and results panels. Where
> what was built differs from what is written below, the code is the record and
> the differences are these:
>
> - **§4.1's sign.** The prose in §4.4 settles the UI convention — `ring_offset`
>   is years *before* the reference, increasing into the past — and the app is
>   built to it, so a member's calendar age is the reference's **plus** its
>   offset. §4.1's `L(t − g)` is the same statement with `g` running the other
>   way; the one in §4.4 is the one the field help, the importer and the solver
>   all say.
> - **§5.2's obstruction, and the normalisation §5.5 needs.** The deposition
>   density is carried NORMALISED (the Gamma terms and the `T` exponent), not
>   only up to a constant. With `k` fixed those are constants and could be
>   dropped; with `k` drawn from its own conditional they are functions of it,
>   and dropping them would let the data pick a rigidity by the wrong rule.
> - **§10.2's `k → 0` limit.** Measured rather than assumed. On the three-level
>   test core, `k·Δz = 0.02` moves the three modelled means 11.4, 4.2 and 5.3
>   years from the ordering-only model and their standard deviations by 2.0, 4.0
>   and 0.9 — close, and not identical. It is *not* the plain Sequence's
>   **uniform** interval prior, which is the `k·Δz = 1` case; what a vanishing
>   rigidity leaves is a scale-free prior on the gaps, which the depths no
>   longer inform. `TestTheRigidityLimits` asserts the closeness at the
>   tolerance that was measured rather than the identity that was expected.
> - **§10.2's interpolated depth.** A level at *exactly* a dated depth is a
>   zero interval, which has no deposition rate; it is refused, and the test
>   places the level a centimetre away instead.
> - **§4.7's composition.** The data model composes, as planned. The
>   *inference* does not yet: a wiggle sequence inside a larger model is
>   refused by name at both the importer and the run, because its members are
>   one age with fixed gaps rather than n free parameters and the surrounding
>   Boundaries would have to be told so. §12.3's reference member is recorded,
>   so adding it later is not a migration.
> - **The outlier model, which §4 does not mention.** A wiggle match REFUSES a
>   member carrying one rather than applying it, at the request, at the
>   importer and in the solver. It follows from §4.1's second bullet: the gaps
>   are hard, so there is nowhere for a date to sit apart from the rest, and
>   §4.5's agreement index is what says a member does not belong. Implementing
>   it would mean marginalising an offset scale independently per member,
>   which is `exact_chain`'s approximation rather than OxCal's shared scale;
>   OxCal does allow `Outlier()` in a `D_Sequence`, so this is open work and
>   wants a fixture before it is attempted.
> - **§4.2's V_Sequence.** Implemented as written — one uncertainty per member,
>   convolved independently. OxCal's gaps form a chain and share their errors,
>   so the importer accumulates them in quadrature and warns that it did.

Written 2026-09-02. Scope: OxCal's `D_Sequence` and `V_Sequence` (wiggle matching a series of
dates separated by known gaps — tree rings, varves, a sampled sequence of any kind) and
`P_Sequence` (a Poisson-process deposition model against depth, the age-depth curve that
Bacon and clam users expect).

Both are named in `services/oxcal_script.py`'s `UNSUPPORTED` table today, which is what
prompted this: an `.oxcal` file containing either is currently refused by name.

Everything measured in §3 was measured against the IntCal20 file this repo ships, with a
prototype of the §4 solver — not estimated. Two of those measurements changed the design.

---

## 1. Why these are one plan and not two

They are the same idea with two solvers behind it: **a sequence whose members are separated by
a measured distance on an external axis** — rings for a wiggle match, depth for age-depth.
Everything above the solver is shared: a new kind of ordered element in the model tree, an
ordinate per date, a results panel plotting age against that ordinate, and the same three
exports.

Below the solver they could hardly be more different, and the split is worth stating up front
because it drives the whole order of work:

| | Wiggle match (`D_Sequence`, `V_Sequence`) | Age-depth (`P_Sequence`) |
|---|---|---|
| Free parameters | **one** — the reference member's calendar age | n ages, plus the rigidity `k` |
| Method | closed form, shift-and-multiply | MCMC |
| Where it runs | a new `services/wiggle_match.py` | `services/gibbs.py`, extended |
| Convergence | none to reach | the existing extend-until-converged loop |
| Measured cost | **0.4 ms** (§3.3) | tens of seconds, per `ASYNC_JOBS_PLAN.md` §1.3 |
| Rough effort | days | weeks |

The consequence: **wiggle matching should ship first and alone.** It is the cheapest
substantial feature left in the app and it does not touch either existing solver.

---

## 2. Where the code stands today

### 2.1 The refusals already name both features

`services/oxcal_script.py:323-326` refuses four sequence variants, each with a reason:

```python
'v_sequence': 'a variable-rate sequence, which this app does not model.',
'd_sequence': 'a sequence with known gaps, which this app does not model.',
'p_sequence': 'a Poisson-process deposition model, which this app does not model.',
'u_sequence': 'a uniform-deposition sequence, which this app does not model.',
```

This plan deletes the first three. `u_sequence` stays refused (§9), and that is deliberate:
the importer's rule is to fail naming itself rather than read a construct as the nearest thing
implemented, and a `U_Sequence` read as a `P_Sequence` would change the posterior silently.

One more entry changes rather than goes, at `oxcal_script.py:298`:

```python
'date': ('an undated event parameter, which this app does not model as a '
         'free parameter of its own.'),
```

An age-depth model's whole point is answering *how old is the level at 47 cm, which nobody
dated*, and in OxCal that is `Date()` inside the `P_Sequence`. So this refusal narrows to
"outside a deposition sequence" (§5.6) instead of being lifted.

### 2.2 What already exists and can be reused

- **`ModelElement`** (`models.py`) is a flat parent/position tree with `element_type` from
  `model_tree.ELEMENT_TYPES` (`model_tree.py:42`). New element types are additive.
- **`ModelElement.posterior`** already stores a per-element posterior in the shape a per-date
  result uses. A wiggle sequence's single reference age fits it with no new column.
- **`depth_cm`** already exists on both `RadiocarbonSample` and `ProjectSample`, is validated
  (`ge=0, le=10000`), appears in the entry table and is understood by the CSV importer. The
  age-depth ordinate is therefore **free**.
- **`exact_chain.convolve_on_grid`** (`exact_chain.py:368`) convolves a likelihood with a
  kernel on the calendar grid, trimming lags that cannot reach a grid point. Written for the
  outlier kernel; a `V_Sequence` gap error is the same operation (§4.2).
- **`agreement.overall_agreement`** (`agreement.py:148`) already computes
  `100 · (∏ Aᵢ/100)^(1/√n)`, which is the form a wiggle match's combination index takes (§4.5).
- **`gibbs.Model.draw_scale`** (`gibbs.py:318`) draws a **shared hyperparameter** — the outlier
  models' offset scale — from a tabulated 1-D conditional. Sampling `k` is the same pattern
  (§5.5), which is most of why `P_Sequence` is tractable here at all.
- **`intervals.report`** (`intervals.py:624`) turns joint draws into a block stored on
  `Analysis.intervals`, with the "NULL means not asked for" rule the results page already
  understands. The age-depth band and accumulation rates fit that slot exactly (§5.7).
- **`exact_chain.layout_with_reason`** (`exact_chain.py:154`) returns a plain-English reason
  when a model is not a chain, and `api/analysis._run_bayesian` (`analysis.py:334`) puts it in
  the stored solver record. A model containing a deposition sequence routes to the sampler
  through machinery that already exists and already explains itself.

---

## 3. What was measured, and what it changed

A prototype of §4 — load IntCal20, shift each date's likelihood by its ring offset, multiply,
read HPD ranges with the app's own `hpd_ranges_from_distribution` — was run against the
shipped curve. All figures are the **median 95.4% HPD width over 200 simulated draws** at
±25 laboratory error, 8 samples evenly spaced across the stated ring span.

### 3.1 What a wiggle match buys, at the plateau it exists for

At 2500 cal BP, inside the Hallstatt plateau:

| Series | Median 95.4% width | Came back split into >1 range |
|---|---|---|
| one date alone | 263.5 yr | **100%** of draws |
| 8 samples over 60 rings | 79.0 yr | 92% |
| 8 samples over 140 rings | 49.0 yr | 92% |
| **8 samples over 250 rings** | **14.0 yr** | **8%** |
| 8 samples over 400 rings | 25.0 yr | 16% |
| 8 samples over 600 rings | 23.0 yr | 17% |

At 3200 cal BP, on a steep run, the same series give 183.0 yr for one date against 24.0 yr for
8 samples over 250 rings.

**The headline is a 19× sharpening at the plateau** — 263 years down to 14 — which is a bigger
effect than anything else in the app produces, and it is produced by a solver with no MCMC in it.

### 3.2 The finding that changes the UI: length beats sample count

The plateau rows above are not monotonic in an obvious way, and the reason is the interesting
part. A **short** series stays split — 92% of the time at 140 rings, barely better than one
date alone — because the series has to span enough of the curve's structure to tell the
plateau's repeated wiggles apart. Past that threshold the answer collapses to a single range
and stays there.

Sample count does not substitute for it. Holding the span at 300 rings and varying the count
gave 77 / 27 / 20 / 14 years at 3 / 5 / 8 / 12 samples: a real improvement, but every one of
those was already a single range. **Length decides whether the match resolves at all; count
decides how tightly.** Twelve samples packed into 60 rings will not break a plateau.

That is a statement worth making *before* anyone commissions the dates, and this app already
has the place to make it — the curve browser's resolution panel is exactly the
"can this period be dated finely enough" instrument. §7.2 puts it there.

### 3.3 Cost

0.4 ms per solve, 12 samples on a 3000-year window at 1-year resolution. It runs inside the
request; it does not need `services/jobs.py`.

### 3.4 The finding that changes the solver: `_build_grid` is wrong for this

The first draft of this plan assumed a wiggle match could borrow `exact_chain._build_grid`.
It cannot, and the reason is specific rather than a general resolution worry.

`_build_grid` pads the dates' own likelihood support by `max(800, 6 × support)`
(`BOUNDARY_WINDOW_FACTOR`, `exact_chain.py:91`). That padding is correct and deliberately
generous **because boundaries genuinely range far beyond the outermost date** — it is what
Boundaries are for. A wiggle match has no boundaries at all, so the padding is pure waste, and
at a fixed 2000-point budget it is paid for in resolution:

| Series | Likelihood support | Padded window | Step |
|---|---|---|---|
| 140 rings | 422 yr | 5,486 yr | 2.74 yr/cell |
| 300 rings | 726 yr | 9,438 yr | 4.72 yr/cell |
| 600 rings | 1,013 yr | 13,169 yr | **6.58 yr/cell** |

A 6.6-year cell quantising an answer whose selling point is ±11 years would give away most of
what §3.1 measured. So `wiggle_match` builds **its own** grid: support plus a modest fixed pad,
floored at `MIN_GRID_STEP_YEARS`. At 0.4 ms a solve there is room to do it in two passes —
coarse to locate support, fine to resolve it — and §4.3 does.

---

## 4. A. Wiggle matching

### 4.1 The maths

`D_Sequence` has **one** unknown. Given gaps `gᵢ` measured from a reference member (ring 0 of
the series, conventionally the outermost or the felling ring), every member's calendar age is
`t − gᵢ` for a single `t`, so

    p(t) ∝ ∏ᵢ Lᵢ(t − gᵢ)

Each `Lᵢ` is the existing calibration likelihood, curve error and ΔR included — the same
expression `exact_chain._date_likelihood` builds. Evaluate every one on a shared grid shifted
by its own gap, sum the logs, exponentiate. O(n·M), deterministic, exact up to the grid step.

Two things fall out that are worth having in the docstring, because they are the reason this
is not just "a Sequence with tight constraints":

- There is no sampler and nothing to converge, so none of the qualifications
  `intervals.py` carries about Monte Carlo error apply here.
- The gaps are **hard**. A `D_Sequence` asserts the ring counts are right. If they are not,
  the model has no way to say so except through the agreement index — which is why §4.5
  treats the combination index as a required output rather than a nicety.

### 4.2 `V_Sequence` is a convolution away

`V_Sequence` gives each gap an uncertainty: `Gᵢ ~ N(gᵢ, σᵢ)`. Marginalising it turns each
shifted likelihood into a convolution of that likelihood with the gap's Gaussian, which is
`convolve_on_grid(Lᵢ_shifted, gaussian_kernel(σᵢ))` — the function already there, used the
same way `_with_outlier_component` uses it. Everything else is unchanged, `p(t)` is still
one-dimensional, and `D_Sequence` is the σᵢ = 0 case of the same code path.

Recommendation: implement both together. The delta is a kernel builder and one branch, and
splitting them means writing the same fixtures twice.

### 4.3 A new service, not a branch inside `exact_chain`

`backend/app/services/wiggle_match.py`. Kept apart from `exact_chain.py` because it shares
none of that module's machinery — no transfer matrices, no message passing, no span density,
no boundaries — and folding a one-parameter closed form into a 1,357-line chain integrator
would obscure both.

```python
def solve(measurements: List[Dict], sequence: Dict,
          num_samples: int = 0) -> Dict:
    """
    A D_Sequence/V_Sequence: one reference calendar age constrained by every
    member of the series at its own gap.

    Returns the reference age's posterior in _summarize's shape, plus each
    member's own posterior (the reference shifted by its gap -- they are the
    SAME distribution translated, not n independent results, and the results
    page has to say so), each member's agreement index, and the combination
    index over all of them.
    """
```

Grid, per §3.4: pass one over the members' combined support at a coarse step to find where the
product has mass; pass two at `MIN_GRID_STEP_YEARS` over that support plus a fixed pad. The pad
is fixed rather than proportional because there is no boundary to accommodate — the only reason
to reach beyond the support is so a reported range is not clipped at the window edge.

The grid must cover `[min(t) − max(g), max(t)]`, i.e. it is *wider than the reference's own
support by the series length*, since each member is evaluated at `t − gᵢ`. Getting this wrong
truncates the oldest member's likelihood silently, so it is asserted rather than assumed.

### 4.4 Sign convention — the one thing most likely to be wrong

Rings count **forward** in time; cal BP counts **backward**. A gap is therefore *subtracted*
from a cal BP reference age to reach an earlier-formed ring, and a flipped sign produces a
result that is wrong but entirely plausible-looking — a tight range in the wrong place.

The test for this must use an **asymmetric segment of the curve**, so a reversed series lands
somewhere clearly wrong rather than merely shifted. A symmetric test region would pass with the
sign inverted, which is the failure mode worth designing the fixture against.

The UI convention is settled once, here, and stated in both the field help and the docstring:
**`ring_offset` is years before the reference member, so it is 0 on the reference and
increases into the past.** The importer, the API and the plot all read it that way.

### 4.5 Agreement, and the fact that it is load-bearing here

A `D_Sequence` asserts the ring counts are correct. If a sample is misidentified by 40 rings,
the model cannot widen to absorb it the way a Phase can — it just fits everything else worse.
So the agreement index is not a diagnostic here, it is *the* check that the match is real.

Per member: `agreement.py`'s existing formula, comparing each member's modelled posterior
(the reference posterior shifted by its gap) against its own unmodelled likelihood — unchanged
code, new caller. Combined: `overall_agreement` already implements
`100 · (∏ Aᵢ/100)^(1/√n)`.

**Threshold** (§12.1, resolved): the combination is quoted against OxCal's
`A_n = 100/√(2n)`, confirmed from observed OxCal 4.4 behaviour (`agreement.combination_threshold`).

### 4.6 Data model

- **`RadiocarbonSample.ring_offset`** (Float, nullable) — new. Not `depth_cm` reused: rings and
  depth are different units running in opposite directions, and overloading one column would
  guarantee the §4.4 bug in the importers as well as the solver.
- **`ProjectSample.ring_offset`** — the same field, so deploying stays a field-for-field copy.
- **`wiggle_sequence`** added to `ELEMENT_TYPES`. Children are dates only. Carries
  `gap_units` ('rings' | 'years') and, for `V_Sequence`, per-date `ring_offset_error`.
- Element-level settings go in a new **`ModelElement.params`** (JSON), shared with §5. Five
  nullable columns each used by exactly one element type would be worse; this is a column whose
  contents are documented per `element_type` in `model_tree.py`, the way `Analysis.solver` and
  `Analysis.intervals` already work.

### 4.7 Composition — why it is an element and not a fourth analysis type

A wiggle-matched timber is usually *evidence for something else*: the match gives a felling
date, and the felling date dates the building. Making it a tree element means it composes — a
`wiggle_sequence` sits inside a Phase, contributes one tightly-constrained age, and the
surrounding model uses it.

The standalone case is still the common one, so New Analysis gets a **one-click "Wiggle match"
preset** that builds the trivial tree. That is a UI shortcut over the same data model, not a
second code path. Making someone hand-build a tree to match eight rings from one timber would
be a tax with no payoff.

Where the whole model *is* one wiggle sequence, `_run_bayesian` dispatches to
`wiggle_match.solve` and records `{'method': 'wiggle_match'}` in the solver record — the same
place, and the same shape, as `exact` and `gibbs`.

---

## 5. B. Age-depth modelling

### 5.1 The maths

`P_Sequence` (Bronk Ramsey 2008) models deposition as a Poisson process along depth. Between
two levels separated by Δz the process contributes `k·Δz` events, and the elapsed time carries
a Gamma-shaped prior with that shape parameter — so over an ordered set of depths the joint is
Dirichlet in the normalised increments:

    p(t₁ … tₙ | z) ∝ ∏ᵢ (Δtᵢ)^(k·Δzᵢ − 1),   Δtᵢ = tᵢ − tᵢ₊₁ ≥ 0

`k` is the rigidity. Large `k` drives the ages onto a straight line in depth (constant
accumulation); `k → 0` leaves nothing but the ordering, i.e. a plain Sequence. Both limits are
testable, cheaply and without OxCal — §10.2.

### 5.2 Why the exact solver cannot take it

`exact_chain` integrates by message passing along a chain of boundaries, which needs each date
to enter through **its own likelihood alone**. A `P_Sequence` interval term couples *adjacent
dates directly*, and `k` couples every date in the sequence at once. That is the same
obstruction the module docstring already records for the outlier scale, and it has the same
resolution: `layout_with_reason` returns

> "this model contains a deposition sequence, whose ages are coupled to each other by the
> deposition rate rather than only through their Boundaries, so it cannot be integrated exactly"

and `_run_bayesian` falls back to the sampler, records the reason, and the results page shows
it. No new plumbing — that path exists and is exercised today.

### 5.3 What actually changes in `gibbs.py`

Less than the maths suggests. Hold every other parameter fixed and one date's full conditional
under a `P_Sequence` is

    Lᵢ(tᵢ) · (tᵢ₋₁ − tᵢ)^(k·Δzᵢ₋₁ − 1) · (tᵢ − tᵢ₊₁)^(k·Δzᵢ − 1)

— its own likelihood times **two power terms**, one per neighbour. Still one-dimensional, still
tabulated on the calendar grid, still drawn by one `searchsorted`. That is structurally the
same conditional `draw_boundary` (`gibbs.py:620`) already evaluates as the product of at most
two span factors.

Concretely:

- `_build_constraints` (`gibbs.py:357`) today produces **hard interval truncations**. A
  deposition sequence produces a **soft weight over the same interval**. Both are the same
  precomputed-table shape, so `Model.allowed` is unchanged and only the weighting differs.
- `draw_date` (`gibbs.py:581`) gains a branch multiplying in the two neighbour terms before the
  inverse-CDF draw. The endpoint half-weighting stays exactly as it is — the module docstring's
  warning about that rule applies here too and for the same reason.
- `log_posterior` gains the matching term, or the shift moves in §5.4 will accept wrongly.
- `move_sets` should offer a whole-sequence shift. A deposition sequence is *more* tightly
  coupled than a phase, so the sticky behaviour that motivated group moves is worse here, not
  better.

### 5.4 The span-prior collision — the thing most likely to be quietly wrong

A `P_Sequence` supplies its **own** prior on the intervals between its members. The uniform
scatter density `(older − younger)^(−K)` must not also apply to them, and neither must
`_span_priors`' `UniformSpanPrior` correction (`gibbs.py:427`), which exists to undo the
subdivision-dependence of *that* model.

So `model_tree.plan_model` must **not** put a deposition sequence's dates in a `SpanGroup`, and
the boundary classification must not count them. Two priors over the same intervals would not
crash, would not look wrong, and would give phase widths that are wrong by a factor nobody
could eyeball.

This deserves its own test — a deposition sequence with `k` pinned near zero must reproduce the
equivalent plain Sequence — which is §10.2's first limit, doing double duty.

### 5.5 `k` as a parameter, not a setting

A fixed `k` chosen badly dominates the answer, which is why OxCal treats it as a variable with a
log-uniform prior and lets the data pick the rigidity. That is a **shared hyperparameter over
the whole sequence** — exactly what `draw_scale` (`gibbs.py:318`) already does for the outlier
models' offset scale: tabulate the conditional over a grid of `log₁₀ k` nodes, accumulate each
member's contribution at its current position, draw by inverse CDF.

Ship fixed `k` first so the interval term can be validated against OxCal in isolation, then
make it variable. The two land as separate, separately-testable changes.

**Report `k`'s posterior on the results page, not just its effect.** A high-`k` model will
interpolate confidently straight through a plateau and report a precision the dates do not
support; the honest instrument shows the rigidity it inferred and flags when the posterior is
pinned against the prior's upper bound, because that means the prior is doing the work.

### 5.6 Undated depths

An `interpolated_depth` node — a depth with **no measurement** — is a parameter whose likelihood
table is flat. `draw_date` needs no change to handle it: a uniform table draws uniformly over
the allowed interval, weighted by the neighbour terms, which is precisely the right answer.

This is what makes "how old is 47 cm?" answerable, and it is the reason `Date()` inside a
`P_Sequence` becomes importable (§2.1) while `Date()` elsewhere stays refused.

### 5.7 What comes out

Not a stack of posteriors. The deliverable is an **age-depth band**: depth on y, calendar age on
x, a median line with 68.3% / 95.4% envelopes, each dated sample's own posterior drawn at its
depth. Plus accumulation rate (yr/cm) per interval, and a depth query — "the age at 47 cm".

Every one of those is a function of **several ages at once**, so they come off *joint* draws via
`gibbs.run`'s `collect_draws`, not off the marginals. That is the identical argument
`intervals.py` opens with for durations, and the identical trap: pairing marginals gives an
answer that is wrong in shape rather than scale, which no eyeball check catches. They belong in
`Analysis.intervals`' block, under its existing `independent: false` flag and its "NULL means not
asked for" convention.

---

## 6. Migration

One Alembic revision, all additive:

| Table | Column | Type |
|---|---|---|
| `radiocarbon_samples` | `ring_offset` | Float, nullable |
| `radiocarbon_samples` | `ring_offset_error` | Float, nullable |
| `project_samples` | `ring_offset` | Float, nullable |
| `project_samples` | `ring_offset_error` | Float, nullable |
| `model_elements` | `params` | JSON, nullable |

`element_type` is a plain string column with no CHECK constraint, so the new types need no DDL —
`model_tree.ELEMENT_TYPES` and the frontend's `ElementType` union (`types/index.ts:26`) are the
only two places that enumerate them, and `test_model_tree.py` should assert they agree.

No existing row changes meaning. An analysis saved before this migration reruns identically,
which is the standard `test_migrations.py` already enforces.

---

## 7. Frontend

### 7.1 Model tree editor

Two new element types in the same palette as Sequence / Phase / Boundary / Hiatus, with the
existing drag-and-nest rules. `ModelTreeEditor`'s depth clamp already prevents nesting inside a
non-container; both new types accept **dates only** (and, for a deposition sequence,
interpolated depths), which is the same rule expressed with a different child whitelist.

Colours come from `DESIGN.md`'s encoding discipline: a hue is a claim about what something *is*,
so these need their own, not a borrowed Phase green or Boundary coral.

### 7.2 Entering the ordinate

- **Depth** is free — `depth_cm` is already a column in the entry table, the bulk paste and the
  CSV importer.
- **Ring offsets** need a new column plus a helper, because people have **ring numbers**, not
  offsets from a reference, and converting by hand is where §4.4's sign error will actually
  happen. The helper takes ring numbers and a nominated reference and computes the offsets.
- Validation: strictly monotonic; warn when the implied series span exceeds the material's
  plausible length; and — from §3.2 — **warn when the span is short relative to local curve
  structure**, since a 140-ring series at the Hallstatt plateau came back split 92% of the time
  and the user should know that before running, not after.

### 7.3 Results

- **Wiggle match**: the combined reference posterior, each member's shifted posterior on a
  shared axis, per-member agreement, and the combination index. The panel must say plainly that
  the member posteriors are *one distribution translated*, not n independent results — otherwise
  n identically-shaped curves read as n independent confirmations.
- **Age-depth**: the §5.7 band, an accumulation-rate table, the depth query, and `k`'s posterior.
- **Harris matrix**: a deposition sequence has no meaningful Harris rendering. Render the
  age-depth plot in its place and say why, rather than drawing a column of boxes that implies a
  stratigraphic reading the model is not making.

### 7.4 Exports

`README.md` commits to one assembly behind CSV, JSON and the PDF, so the three downloads of one
analysis cannot disagree, and to a column appearing only when the run has something to put in
it. The wiggle-match combination and the age-depth band go through that same assembly. A plain
calibration must not grow an empty accumulation-rate section.

---

## 8. Order of work

1. **`D_Sequence` + `V_Sequence`** — `services/wiggle_match.py`, its own grid, the two new
   columns, the tree element, the preset, the results panel. Self-contained; touches neither
   existing solver. **Ship this alone.**
2. **OxCal import for both** — `Gap()`, `V_Sequence`'s per-gap errors; delete two `UNSUPPORTED`
   entries.
3. **`P_Sequence` at fixed `k`** — `plan_model` exclusion (§5.4), the `draw_date` branch,
   `log_posterior`, the whole-sequence move. The real work.
4. **Variable `k`** — one more shared-hyperparameter conditional, mirroring `draw_scale`.
5. **Interpolated depths, the age-depth band, accumulation rates, the depth query.**
6. **OxCal import for `P_Sequence`** — `z=`, `k=`, `Interpolate`, and `Date()` narrowed rather
   than lifted.

Steps 1–2 are a coherent release on their own and deliver §3.1's 19× on their own.

---

## 9. What stays refused

- **`U_Sequence`** — uniform deposition. A different prior from `P_Sequence`; reading one as the
  other would change the posterior silently, which is the exact substitution the importer's
  design refuses to make.
- **`Date()` outside a deposition sequence** — still an undated free parameter with nothing
  bounding it.
- **`Tau_Boundary` / `Sigma_Boundary` / `Zero_Boundary` / trapezoidal transitions** — untouched
  by this plan.

---

## 10. Validation

The standard this repo already holds solvers to is real OxCal 4.4 runs plus closed forms where
one exists (`REMAINING_WORK.md`, `test_oxcal_agreement.py`, `test_charcoal_outlier.py`).

### 10.1 Wiggle matching

- **Ground truth, which nothing else in this app has.** A dendrochronologically dated series has
  a *known* felling date, independent of any model. The test asserts the match recovers it —
  stronger evidence than agreement with another implementation.
- **The sign fixture** (§4.4), on an asymmetric stretch of curve.
- **Degenerate cases**: a one-member sequence must equal that date's plain calibration exactly;
  all-zero gaps must equal the pooled combination of the members.
- **`V_Sequence` → `D_Sequence`** as every σᵢ → 0.
- **Grid independence**: halving the step must not move the reported range — the check §3.4 says
  would have failed on the borrowed `_build_grid`.

### 10.2 Age-depth

Two limits, cheap and needing no OxCal:

- **`k → 0`** must reproduce the equivalent plain ordered Sequence to within Monte Carlo error.
  This is also the §5.4 double-prior test.
- **Large `k`** must put the posterior ages on a straight line in depth — assert on residual
  scale, not by eye.

Then:

- **Brute force**, three dates: the 3-D integral is directly computable on a grid, so the
  sampler can be pinned against an exact answer without needing `exact_chain` to support the
  model.
- **OxCal 4.4 fixtures**, fixed `k` first, then variable `k`, in `tests/fixtures/` alongside the
  existing ones.
- **Interpolated depth**: a level placed exactly at a dated depth must reproduce that date's
  modelled posterior.

---

## 11. Risk register

| Risk | Where | Mitigation |
|---|---|---|
| Ring/cal BP sign inversion | §4.4 | asymmetric-curve fixture; one stated convention |
| Grid too coarse for a ±10 yr answer | §3.4 | own grid builder; step-halving test |
| Double interval prior on a deposition sequence | §5.4 | `k → 0` reproduces a plain Sequence |
| `k` prior doing the work, silently | §5.5 | report `k`'s posterior; flag when pinned |
| Member posteriors read as n independent results | §7.3 | the panel states they are one distribution translated |
| A short series read as a resolved match | §3.2, §7.2 | pre-run warning driven by series span |

---

## 12. Open decisions

1. **The combination agreement threshold.** Resolved: OxCal's `A_n = 100/√(2n)`, confirmed
   from observed OxCal 4.4 behaviour and printed beside the index.
2. **`gap_units`.** Rings are the overwhelming case, but varve counts and sampled sequences are
   the same maths in different units. Carrying a `gap_units` field costs almost nothing and
   makes the axis label honest; the alternative is calling everything "rings" and being wrong
   in three literatures.
3. **Where a wiggle match's felling date goes when the sequence sits inside a Phase.** The
   sequence contributes one age to the surrounding model — but is that the reference member's
   age, or the youngest ring's, or a felling date offset by an estimated sapwood allowance?
   Sapwood estimation is a real archaeological step with its own literature, and the choice is
   between modelling it (another gap with an error, so `V_Sequence` machinery already covers it)
   and leaving it to the user to enter. Recommended: leave it out of v1 and revisit — but make
   the *reference member* explicit in the data model now, so adding it later is not a migration
   of everything.
4. **`k`'s prior range.** OxCal's default log-uniform span is a choice, not a law. Whatever is
   picked should be visible in the UI and recorded on the analysis, for the same reason
   `Analysis.solver` records which solver ran.
