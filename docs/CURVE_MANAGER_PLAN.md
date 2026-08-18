# Calibration curve management, browsing, and resolution — implementation plan

Written 2026-08-18. Scope: a curve manager (user import, mixing, provenance, lifecycle), a
visual curve browser (shape and error bounds, scrolled along the time axis), and the expected
temporal resolution of any part of a curve, after the method of Svetlik et al. (2019) as
implemented in [`demjanp/res14c_online`](https://github.com/demjanp/res14c_online).

---

## 1. Where the code stands today

Calibration curves are currently a closed set of three, hardcoded in five places that have no
way of knowing about each other:

| Place | What it hardcodes |
|---|---|
| `backend/app/services/calibration.py` | `_CURVE_LOADERS` — three `lru_cache`d file loaders |
| `backend/app/schemas/sample.py` | `VALID_CURVE_IDS`, as a Pydantic `Literal` |
| `backend/app/api/curves.py` | `AVAILABLE_CURVES` — metadata, unused by anything |
| `backend/app/services/oxcal_script.py` | `CURVE_IDS` — OxCal curve names → our ids |
| `frontend/.../MeasurementFields.tsx` + `ProjectSampleTable.tsx` | two separate `<MenuItem>` lists |

Three facts about that arrangement matter for this work:

- **`GET /api/curves` is dead and wrong.** Nothing in the frontend calls it, and its metadata
  is stale in every row: it claims IntCal20 and SHCal20 have 5,141 points and Marine20 1,341.
  The files actually loaded have **9,501 / 5,501 / 9,501** points. This is already logged as
  `REMAINING_WORK.md` item 6.
- **`get_curve(curve_id)` is called from 14 places**, several of them deep inside solvers
  (`exact_chain.py`, `agreement.py`) that have no database session and no business acquiring
  one. Any design that requires threading a `Session` down to the curve lookup will cost a
  wide, invasive diff. §3.1 avoids that.
- **Curve identity is already load-bearing for reproducibility.** `RadiocarbonSample.curve_id`
  is persisted per date, and a rerun rebuilds the model from those rows. A curve that can be
  edited or deleted by a user is therefore a curve that can silently invalidate a stored
  result — the exact failure mode `ProjectSample`'s docstring already argues against for dates.
  §3.3 answers it the same way: snapshots and immutability, not cascading edits.

`REMAINING_WORK.md` item 11 ("Calibration curve management UI: upload custom curves; mix
marine/terrestrial curves") is the seed of this plan; this document supersedes it.

---

## 2. What gets built

Four pieces, each usable on its own, in dependency order:

- **A. Curve registry** (§3) — one backend source of truth for "what curves exist and how do I
  load one". Kills all five hardcoded lists, fixes the wrong metadata, and is a prerequisite
  for everything below.
- **B. Curve manager** (§4) — import a curve file, define a mixed curve, see provenance and
  usage, archive or delete safely. Backend + a `/curves` page.
- **C. Curve browser** (§5) — the visual half: curve shape with its ±1σ/±2σ error bounds,
  scrolled and zoomed along the calendar axis, with a hover readout, curve-vs-curve difference
  mode, and simulate-a-date.
- **D. Resolution profiles** (§6) — expected temporal resolution as a function of calendar age,
  drawn on the browser's own time axis. Prototyped and validated already; see §6.3.

---

## 3. A. The curve registry

### 3.1 Loading without a database session

New module `backend/app/services/curve_registry.py`. The registry owns a process-level cache
and **opens its own short-lived `SessionLocal` on a cache miss**, so `get_curve(curve_id)` keeps
its present signature and none of the 14 call sites change:

```python
def resolve(curve_id: str) -> CalibrationCurve:
    hit = _CACHE.get(curve_id)
    if hit is not None:
        return hit
    if curve_id in _BUILTIN_LOADERS:          # today's three .14c files
        return _cache(curve_id, _BUILTIN_LOADERS[curve_id]())
    with SessionLocal() as db:                # miss: look in the curve table
        row = db.get(CalibrationCurveRow, curve_id)
    ...
```

This is only safe because **stored curve data is immutable** (§3.3): a cache keyed by id can
never go stale on the numbers, so multiple uvicorn workers cannot disagree about what
`mycurve` contains. Editable *metadata* (display name, notes) is read from the database on
every list/detail request and never cached, so a rename is visible immediately everywhere.

`calibration.get_curve()` becomes a thin delegation to `curve_registry.resolve()`, keeping its
existing "unknown curve raises rather than falling back to IntCal20" hardening as
defence-in-depth. The three `lru_cache`d loaders move behind the registry unchanged.

### 3.2 Metadata computed, not asserted

`CurveSpec` is derived from the curve that actually loaded — `n_points`, `cal_min`/`cal_max`,
`c14_min`/`c14_max`, the grid step structure (IntCal20: "1 yr (0-5,000), 5 yr (5,000-15,000),
10 yr (15,000-25,000), 20 yr (25,000-55,000)"),
plus editorial fields (`name`, `type`, `description`, `reference`, `citation`, `doi`). That single
change retires the 5,141-point fiction, and makes it structurally impossible to reintroduce:
nothing hardcodes a count any more.

`type` stays the existing vocabulary (`terrestrial`, `marine`, `southern_hemisphere`) plus
`mixed`, because `MeasurementFields.CURVE_FOR_SAMPLE_TYPE` — which decides what selecting
"Marine" switches the curve to — becomes driven by it instead of by its own literal map.

### 3.3 Immutability, archiving, and stored-result integrity

Decisions, with the reasoning that produced them:

- **A stored curve's numbers never change.** Re-uploading a corrected file creates a *new*
  curve, it does not mutate the old one. An analysis run last month must keep meaning what it
  said, and the alternative — editing a curve under an already-published result — is exactly
  the trap `ProjectSample` was designed to avoid for dates.
- **Deletion is refused while referenced.** `DELETE /api/curves/{id}` returns **409** naming the
  analyses and project date-collections that use it, and offers archiving instead. An archived
  curve disappears from every picker but still resolves, so a rerun of an old analysis still
  works.
- **Every run records what it ran against.** Each curve gets a `content_sha256` (over the
  normalised numeric rows, so a comment change is not a data change). A Bayesian/calibration run
  stores `{curve_id: sha}` for each curve it touched, alongside the existing `Analysis.solver`
  JSON. A rerun compares, and warns if a built-in curve file was swapped underneath it via
  `INTCAL_CURVE_PATH` and friends — currently that substitution is completely silent.

### 3.4 Frontend single source of truth

New `frontend/src/api/curves.ts` + a `useCurves()` hook (React Query is not in the stack; a small
context-cached fetch matching `NotificationContext`'s shape is enough). Both hardcoded
`<MenuItem>` lists are replaced by it, as is `ProjectSampleTable`'s client-side bulk-paste curve
validation and its help text ("intcal20, shcal20, or marine20" becomes the live list).

### 3.5 Import paths get the same list for free

`date_import._curve()` and `oxcal_script.CURVE_IDS` resolve through the registry, with the
registry carrying per-curve *aliases* (`"IntCal20"`, `"intcal"`, `"IntCal20.14c"`). Payoff: import
IntCal13 once, and an OxCal file written against IntCal13 — which today fails the import
outright, by design, rather than being silently read as IntCal20 — imports correctly.

**Backend endpoints (A):**

```
GET    /api/curves                     registry-backed, real metadata, ?include_archived=
GET    /api/curves/{id}                one curve's full spec
GET    /api/curves/type/{type}         kept, registry-backed
```

---

## 4. B. The curve manager

### 4.1 Storage

One new table, `calibration_curves`:

| Column | Notes |
|---|---|
| `id` | slug, primary key (`intcal13`, `mix-north-marine-50`) — this is what `curve_id` on a date points at |
| `kind` | `imported` \| `mixed` (built-ins stay on disk, unrowed) |
| `name`, `description`, `reference`, `doi` | editable metadata |
| `type` | `terrestrial` \| `marine` \| `southern_hemisphere` \| `mixed` |
| `source_text` | **the uploaded file, verbatim** — `imported` only |
| `source_filename`, `uploaded_at` | provenance |
| `recipe` | JSON — `mixed` only: `{a, b, percent_a, percent_a_error}` |
| `content_sha256` | over normalised rows; see §3.3 |
| `aliases` | JSON list, for import matching (§3.5) |
| `archived` | bool |

Storing **the file's own text** rather than a parsed array of numbers is deliberate: it is
smaller (~220 KB for an IntCal-sized curve), it preserves the header comments that carry the
citation, it makes "download this curve exactly as supplied" trivial, and parsing is ~10 ms and
cached anyway. `_load_curve_from_file` is refactored into `_parse_curve_text` + a thin file
wrapper so uploads and built-ins go through one parser.

Curves are **global, not per-project** — a curve is a property of the science, not of one dig,
and the app has no authentication to scope them by anyway.

### 4.2 Import: parse and store are separate steps

Mirrors the existing dates-import contract exactly (`api/imports.py`'s module docstring is the
spec): a preview endpoint that writes nothing and answers `200` even for a bad file, with
`errors` / `warnings` / `notes` as three separate channels, then a store endpoint.

Accepted input:

- `.14c` — the IntCal family format: `#` comments, comma-separated, 3 or 5 columns
  (cal BP, ¹⁴C age, σ, [Δ¹⁴C, σ]), either sort direction. All three bundled curves are this.
- `.csv`/`.tsv` with a header, column names aliased the way `date_import.py` already aliases
  date columns (`cal_bp`/`calbp`/`cal BP`, `c14_age`/`14C age`/`conv_bp`, `error`/`sigma`).
- A calendar column in **BC/AD** instead of cal BP, behind an explicit toggle, converted on
  the way in (spreadsheet-authored regional curves usually come this way).

Rejected, each naming itself rather than being coerced: fewer than two points; non-finite or
non-positive σ; duplicate or non-monotonic calendar ages after sorting; a ¹⁴C column that looks
like F¹⁴C (values < 10) rather than an age; **negative cal BP** (post-bomb) — see §8.

The preview returns the computed `CurveSpec` plus a downsampled series, so the dialog can draw
the curve you are about to accept before you accept it.

### 4.3 Mixed curves

The mixing math is not implemented anywhere in the app today. OxCal's `Mix_Curves(name, A, B, p, σp)`
is the model to match: for a sample whose carbon is `p` percent from curve A and the rest from B,

```
c14_mix(t) = p·A(t) + (1−p)·B(t)
σ_mix(t)²  = p²σ_A(t)² + (1−p)²σ_B(t)² + ((A(t) − B(t))·σ_p)²
```

on the union grid over the two curves' overlapping calendar range. The third term is the one
that matters archaeologically: a poorly-known marine dietary fraction widens the calibrated
range rather than merely shifting it — the same argument `MeasurementInput.delta_r_error`
already makes for ΔR.

A mixed curve is stored as a **recipe, not as data**, so it stays honest about its parents (and
an ingredient curve therefore counts as "referenced" for the deletion rules in §3.3).

Payoff beyond the feature itself: `oxcal_script.py` can accept `Mix_Curves(...)` — today an
`.oxcal` file using it fails to import — and marine-diet human bone becomes expressible at all.

### 4.4 The `/curves` page

Route `/curves`, linked from `Navigation`'s `NAV_LINKS`. A table grouped built-in / imported /
mixed: name, type, calendar range, points, citation, "used by N analyses", and row actions
**Browse · Compare · Export · Archive/Delete**. Header actions: **Import curve…** (dialog modelled
on `ImportDatesDialog`) and **New mixed curve…** (pick two curves, a percentage and its
uncertainty, with a live preview of the resulting curve against both parents).

**Backend endpoints (B):**

```
POST   /api/curves/import/preview      parse an upload, store nothing
POST   /api/curves                     store a previewed curve
POST   /api/curves/mixed               define a mixed curve from a recipe
PATCH  /api/curves/{id}                metadata only (never the numbers)
DELETE /api/curves/{id}                409 if referenced; ?archive=true
GET    /api/curves/{id}/export         the curve as .14c, mixed ones synthesised
GET    /api/curves/{id}/usage          analyses + project collections referencing it
```

---

## 5. C. The curve browser

Route `/curves/:curveId/browse`. One page, four linked panels on a **shared calendar axis**,
which is what makes the resolution readout legible against the curve shape that causes it.

1. **The curve.** ¹⁴C age against calendar age, with ±1σ and ±2σ envelope bands drawn as filled
   polygons (the technique `PosteriorPlot.bar()` already uses). Plotly, `react-plotly.js`,
   `usePlotColors()` — no new charting dependency.
2. **The time scrubber.** A short full-range strip (0–55,000) under the main plot with the
   current window shown as a draggable highlight. This is the "scroll along the time axis"
   control: drag to pan, drag its handles to zoom, and keep a sense of where in 55,000 years
   the current window sits — which raw Plotly pan alone never gives you.
3. **Resolution** (§6), on the same x-axis: mean expected resolution with its 5th–95th
   percentile band, plus optional shading of **plateaus** (|d¹⁴C/dcal| below a threshold) and
   **inversions** (slope < 0, where a single measurement necessarily splits into disjoint ranges).
4. **Readout / probe.** Hovering a calendar year reports: the curve's ¹⁴C age ± σ there, the
   local slope in ¹⁴C years per calendar year, the expected resolution, and — the sentence most
   users actually want — *"a ±25 yr measurement of a sample from this year calibrates to about
   N years (95.4%)"*, computed live by calibrating the curve's own value at that year.

Two modes on top of that:

- **Compare.** Overlay a second curve and add a difference panel (A − B with a combined error
  band). Hemispheric offset, marine reservoir, and IntCal13→IntCal20 revisions all become one
  glance. This is also the honest way to present a mixed curve against its parents.
- **Simulate a date.** Click a calendar year, choose a lab σ: the app takes the curve's ¹⁴C age
  at that year and calibrates it, drawing the result with HPD bars in `PosteriorPlot` itself —
  OxCal's `C_Simulate`, and the concrete counterpart to the aggregate statistic in §6.

Axis labelling follows the app's existing cal BP / BC-AD / BCE-CE preference via
`utils/dateFormat.ts`, and PNG export reuses `PNG_EXPORT_SCALE` so a figure lifted from here is
already 300 dpi.

**Backend endpoints (C):**

```
GET  /api/curves/{id}/points?cal_from&cal_to&max_points
GET  /api/curves/compare?a&b&cal_from&cal_to
POST /api/curves/{id}/simulate           {cal_age, error} -> a calibrated distribution
```

`/points` decimates with **min/max-per-bucket**, not by taking every Nth row: a curve's wiggles
are its whole point, and stride sampling erases exactly the features being examined.

---

## 6. D. Expected temporal resolution

### 6.1 The method

From Svetlik et al. (2019), *Radiocarbon* 61(6):1729–1740, as implemented in `res14c_online`.
For each ¹⁴C age `a` a sample could plausibly return:

1. Scale the lab uncertainty with age — `u(a) = u₀ · exp(a / (2·8033))`, `u₀` defaulting to 15 yr
   at 0 BP — because counting statistics degrade with the amount of ¹⁴C left.
2. Calibrate `a ± u(a)` against the curve.
3. Take the **width of the 95.45% HPD envelope** (first to last selected grid point, gaps
   included) as that measurement's resolution.

Then for each calendar year `t`, average those widths over the ¹⁴C ages a sample of that true age
could yield, weighting each by its likelihood at `t` — giving a **mean** resolution and a
**5th–95th percentile** band per calendar year.

### 6.2 Implementation shape

New `backend/app/services/resolution.py`. The core is a windowed, vectorised loop; steps 2 and 3
reuse the app's own `calibrate()` and `hpd_ranges_from_distribution()` rather than a second
implementation of either:

```python
for a in c14_ages:                       # integer 14C ages spanning the window
    u  = u0 * np.exp(a / (2 * 8033))
    s2 = u * u + sigma_curve ** 2
    d  = a - c14_curve
    m  = np.abs(d) <= 5 * np.sqrt(s2)    # everything beyond 5σ is negligible
    lik = np.exp(-d[m]**2 / (2*s2[m])) / np.sqrt(s2[m])
    width = hpd_envelope(cal[m], lik)    # 95.45%, first-to-last selected point
    # accumulate width into each output year, weighted by lik there:
    #   running Σw and Σw·width for the mean,
    #   a (year × width-bin) histogram for the 5th/95th percentiles
```

That `5σ` mask is what makes this affordable — without it the calculation is a 50,000 × 55,000
dense problem, which is why the reference implementation needs multiprocessing and ships its
answer as a precomputed CSV. Memory stays bounded by accumulating into a
`(output years × width bins)` histogram instead of retaining every distribution.

**Windowed requests are computed with padding.** A window's own edges would clip distributions
that spill past them, so the calculation runs over a padded calendar domain (enough that every
contributing ¹⁴C age is covered at 5σ) and reports only the requested window.

### 6.3 This is already prototyped, and it agrees with the reference

Run against IntCal20 at `u₀ = 15`, 1-yr calibration grid:

| Check | Result |
|---|---|
| Full 0–55,000 cal BP, 5-yr output stride | **9.9 s**, single-threaded, one core |
| 0–12,000 cal BP | **0.5 s** |
| A 1,200-yr browser window | **~0.05 s** — synchronous, no job queue needed |
| Mean absolute difference vs `res14c_online`'s published output, 1,000–4,000 cal BP | **0.34 %** |
| 0 cal BP | 290.5 yr (reference: 290.9) |
| 2,600 cal BP, mid-Hallstatt plateau | 294.3 yr (reference: 294.4) |
| 2,750 cal BP, the steep drop at its start | 33.7 yr (reference: 33.7) |

Two consequences for the plan. First, **no background job infrastructure is needed**: a browser
window computes inside a normal request, and only whole-curve export takes ten seconds — cache
that one per `(curve_sha, u₀, stride)` on first request. Second, the numbers above are a real
validation of the method against its authors' own implementation, including the sharp
2,750 cal BP feature that a subtly wrong implementation would smear away.

### 6.4 Licensing boundary

`res14c_online` is **GPL-3.0**; RadiCal is **CC0**. The implementation must therefore come from
the *published method* (the paper's uncertainty-scaling law and likelihood-weighted aggregation)
composed with RadiCal's existing calibration and HPD code — not from that repository's source.
The prototype above was written that way and is not a translation of it.

For the regression fixture (§7) the same care applies: rather than committing the reference CSV,
`backend/tests/fixtures/res14c/README.md` documents how to regenerate it, and the parity test
**skips when the file is absent** so a clean checkout still runs green. If a committed fixture is
preferred, a decimated slice (a few hundred rows) with explicit attribution is the version to
commit — that is program output, not code, but it is worth a deliberate decision rather than a
drive-by one.

**Backend endpoints (D):**

```
GET  /api/curves/{id}/resolution?cal_from&cal_to&u0&stride       JSON, windowed
GET  /api/curves/{id}/resolution.csv?u0&stride                   full-range export
```

---

## 7. Tests

Matching the existing suite's habits (`backend/tests/`, pytest; `frontend/`, vitest on pure
helpers only). `curves.py` currently has **no test coverage at all** — that is fixed here as a
side effect.

**`test_curves.py`**
- Metadata is computed, not asserted: IntCal20/SHCal20 report 9,501 points and Marine20 5,501,
  read from the loaded curve. (This test fails against today's `AVAILABLE_CURVES`.)
- Import preview: accepts 3- and 5-column `.14c` in both sort directions, header CSV with aliased
  columns, and a BC/AD calendar column; rejects non-monotonic, duplicate, non-positive σ,
  F¹⁴C-looking, negative-cal-BP and one-point files, each with its own message.
- Preview writes nothing; store-then-get round-trips; `content_sha256` is stable across a
  comment-only edit and changes when a number does.
- `DELETE` on a referenced curve is 409 and names the referrer; `?archive=true` succeeds and the
  archived curve still resolves for a rerun but is absent from the default list.
- A mixed curve's `c14` and `σ` at a sampled year match the §4.3 formulae computed by hand, and
  a mixed curve at 100 % reproduces its first parent exactly.
- An `.oxcal` file naming an imported curve by alias imports; one naming an unknown curve still
  fails by name (the existing guarantee must not regress).

**`test_resolution.py`**
- The Hallstatt plateau reads worse than its surroundings, and the steep 2,750 cal BP drop reads
  better — the shape, not just a number.
- A windowed profile with padding matches the full-range profile over the same window within
  tolerance (this is the correctness condition for §6.2's padding).
- Larger `u₀` never yields a narrower mean resolution at any year.
- Parity against the `res14c_online` reference within 1 %, skipped when the fixture is absent (§6.4).

**Frontend (vitest)** — the pure helpers only, per the existing convention: min/max decimation
preserves each bucket's extremes, the scrubber's window↔pixel mapping round-trips, and plateau
detection flags a synthetic flat segment and not a steep one.

---

## 8. Deliberately not in scope

- **Post-bomb (negative cal BP) curves** — NH1/NH2/NH3 and SH1-2/SH3. These need an F¹⁴C
  likelihood rather than a conventional-age one, and `MeasurementInput.age_bp` is `ge=0`
  throughout. The import validator therefore *rejects* negative cal BP with a message saying so,
  rather than accepting a curve the calibration engine would then quietly mishandle.
- **A ΔR reference database** (Marine Reservoir Database import, map-based lookup). It belongs
  next to the curve manager conceptually — ΔR is curve-relative — but it is a separate piece of
  work with its own data-licensing questions.
- **Curve *construction*** (fitting a new curve from raw measurements). Out of scope by a wide
  margin; this is a manager and a viewer.

---

## 9. Sequencing and effort

Each phase leaves the app working and is independently useful.

| # | Phase | Depends on | Effort |
|---|---|---|---|
| 1 | ~~Registry + wire the frontend to it; retire the five hardcoded lists; fix the metadata (`REMAINING_WORK` item 6)~~ — **done 2026-08-18**, see §9.1 | — | 3–4 h |
| 2 | ~~`calibration_curves` table, import preview/store, lifecycle rules, export~~ — **done 2026-08-18**, see §9.2 | 1 | 4–5 h |
| 3 | ~~`/curves` manager page + import dialog~~ — **done 2026-08-18**, see §9.3 | 2 | 4–5 h |
| 4 | ~~Curve browser: curve panel, error bands, time scrubber, readout~~ — **done 2026-08-18**, see §9.4 | 1 (3 for the entry point) | 6–8 h |
| 5 | ~~Resolution service, endpoints, cache; resolution panel~~ — **done 2026-08-18**, see §9.5 | 4 | 4–5 h |
| 6 | ~~Mixed curves: math, endpoint, builder UI, `Mix_Curves` in the OxCal importer~~ — **done 2026-08-18**, see §9.6 | 2, 4 | 3–4 h |
| 7 | ~~Compare mode, simulate-a-date, CSV exports~~ — **done 2026-08-18**, see §9.7 | 4, 5 | 3–4 h |
| 8 | ~~Docs: README features + API overview, `USER_MANUAL.md` section, `REMAINING_WORK.md` items 6 and 11~~ — **done 2026-08-18**, see §9.8 | all | 2 h |

**Total ≈ 30–37 h. All eight phases are done.**

### 9.1 What phase 1 actually landed

- `backend/app/services/curve_registry.py` — `CurveSpec`, `resolve()`, `list_specs()`,
  `resolve_alias()`. Editorial metadata is written by hand because it cannot be derived;
  everything countable is measured from the loaded curve.
- `calibration.get_curve()` is now a delegation to it, so all fourteen call sites are unchanged.
  `schemas/sample.VALID_CURVE_IDS`, `date_import._curve()` and `oxcal_script._curve_id()` read
  the registry instead of their own copies of the list.
- `api/curves.py` rewritten over the registry; its hand-written metadata table is gone, and with
  it the 5,141 / 1,341 / 5,141 point counts. The list endpoint's default limit went from 10 to
  100, so growing the curve list cannot silently truncate a picker.
- `frontend/src/api/curves.ts` + `hooks/useCurves.ts`; both `<MenuItem>` lists,
  `CURVE_FOR_SAMPLE_TYPE`, `isMarineMeasurement`'s `'marine20'` literal and
  `parsePastedDates`'s validation set now come from the backend. Deliberately no local fallback
  list: a failed request says so and keeps the curve each date already has, rather than offering
  a list the backend might not accept.
- `backend/tests/test_curves.py` (29 tests) and `frontend/src/api/curves.test.ts` +
  `components/ProjectSampleTable.test.ts` (14 tests). Backend suite 509 → 538, frontend 81 → 95.
- Verified in the running app: the picker lists all three curves from the API, selecting Marine
  switches to Marine20 and reveals the ΔR fields, and the date table shows curve names rather
  than raw ids.

### 9.2 What phase 2 actually landed

Backend only; the `/curves` page is phase 3.

- `models.StoredCalibrationCurve` (`calibration_curves`) — the uploaded file verbatim plus the
  `parse_options` it was read with, not a parsed array. §4.1's reasoning, with one addition the
  implementation forced: because the curve then depends on the parser, `content_sha256` is
  **re-checked against the re-parsed rows on every load**, so a parser change years from now
  fails loudly instead of moving a curve underneath an analysis run against it.
- `services/curve_import.py` — `.14c` (3- or 5-column, either sort direction), headed CSV/TSV
  with columns matched by name in any order, whitespace-separated files, and a BC/AD calendar
  column converted on the way in. Name and citation are suggested from the file's own header
  comments, which is where a curve's citation actually lives. Refuses, each naming itself:
  non-numeric cells, non-positive σ, duplicate calendar ages, negative cal BP (post-bomb —
  §8), an F14C-looking radiocarbon column, fewer than two points, a missing column.
- `api/curves.py` — preview (writes nothing, 200 even for a bad file), store (takes the file
  again rather than the client's parsed rows, so there is one parser and no second path into the
  database), PATCH for editorial fields only, DELETE refused with a 409 naming the analyses using
  the curve, `?archive=true`, restore, usage, and export.
- `schemas/sample.py` — `curve_id` was a Pydantic `Literal` over a fixed tuple, which stops
  working the moment a user can add a curve. Now a validator against the registry; builtin ids
  are answered without a query, so a payload of forty IntCal20 dates still costs none.
- The registry gained stored-curve resolution, `known_ids()`, `usage()` and `invalidate()`, and
  was hardened so that **builtin curves never require a database** — an unreachable curve table
  is reported once and calibration continues with the bundled three.
- `tests/test_curve_import.py` (40 tests) and 1 more in `test_curves.py`. `conftest.py` now
  creates the schema for the whole session, rather than as a side effect of whichever test first
  built the `client` fixture.
- Verified against the running app with a real file: SHCal20 imported as a second curve hashes
  **identically** to the builtin, calibrates a date to the identical HPD ranges, exports
  byte-for-byte as uploaded, refuses deletion of a builtin (403), and deletes cleanly when
  unused.

### 9.3 What phase 3 actually landed

- `pages/Curves.tsx` at `/curves`, linked from the nav. One row per curve, bundled and imported
  alike, showing what was *measured* from each — points, range, calendar grid — beside its
  citation. Bundled curves offer download only; imported ones add edit, archive/restore and
  delete.
- `components/ImportCurveDialog.tsx` — drop a file, see it read: point count, calendar and
  radiocarbon range, the parser's own notes, and the curve drawn with its ±1σ band. The sketch is
  an inline SVG, not a Plotly figure: it answers "does this rise left to right with wiggles", and
  pulling a plotting library into a dialog to draw sixty segments would be the wrong trade. Name,
  citation and type are pre-filled from the file's header comments and all editable.
- Two things the plan did not anticipate. The delete button asks `/usage` *before* opening its
  confirmation, so the dialog says "this curve is used by 3 analyses, so it can only be archived"
  instead of the user meeting a 409 after clicking. And `utils/apiError.ts` learned to read a
  structured `detail` — the curve endpoints return a message plus the parse errors or the usage
  behind a refusal, which previously rendered as raw JSON in a toast.
- The shared curve cache is reset after every write, so a curve imported on this page appears in
  the New Analysis picker without a reload. That is what `resetCurveCache` exists for.
- `components/ImportCurveDialog.test.ts` (8 tests) holds the dialog's slug rule against
  `schemas/curve.py`'s `SLUG_PATTERN` verbatim — a mismatch would surface as a 422 after the user
  had filled in the whole form.
- Verified in the running app end to end: Marine20 re-imported through the dialog as a second
  curve (5,501 points, 10 yr grid, drawn correctly), the duplicate-id attempt refused with a
  readable message, the imported curve then offered in the New Analysis picker, and deleted again
  through the confirmation dialog — which correctly reported that nothing was calibrated against
  it.

### 9.4 What phase 4 actually landed

- `GET /api/curves/{id}/points` + `services/curve_view.py`. Decimation is min/max per bucket, as
  §5 required. Local gradient is computed alongside, and this is where the plan needed
  correcting: a per-grid-step gradient is unusable, because IntCal20's 1-year stretch makes it
  swing between +4 and −3 across ground that is flat at any scale a person cares about. Measured
  over a fixed **50-year half-span** instead, chosen against three known stretches of IntCal20 —
  the Hallstatt plateau reads 0.31, ordinary curve 0.91, the early Holocene 1.05 — with the
  measurements written into the constant's comment.
- `pages/CurveBrowser.tsx` at `/curves/:curveId/browse`, reachable from the curve name and a
  chart icon in the library. Plotly curve with ±1σ/±2σ bands, `autorange: 'reversed'` to match
  every other plot in the app, the calendar axis following the cal BP / BC-AD / BCE-CE
  preference, and presets for the whole curve, the Holocene and the Hallstatt plateau.
- `components/CurveScrubber.tsx` + `utils/curveScrubber.ts` — the full-range strip, as an SVG
  rather than a second Plotly figure: it needs a drag that moves a window, not one that pans a
  chart. The geometry is pure and tested (18 tests) because every way it can fail — drift on
  repeated drags, sliding off the end of the curve, inverting when handles cross — renders
  perfectly happily.
- Three things worth recording, none of them anticipated:
  - The scrubber was written **mirrored**. Both existing plots reverse the calendar axis, so
    older is on the left; a scrubber running the other way moves the view backwards from the
    drag. Caught by checking `PosteriorPlot`'s axis config rather than by luck, and now asserted.
  - A window containing exactly *one* grid point draws nothing, just as an empty one does.
    `curve_view` widens for **fewer than two**, not for zero — and a window landing exactly on a
    grid point is the common way to get one.
  - The windowed refetch swallowed its errors, which left the plot showing one window while the
    scrubber showed another, silently. It now says so without blanking the chart.
- Verified in the running app: the Hallstatt preset resolves the plateau's real structure, the
  readout calls 2780 cal BP "ordinary" (1.47), 2600 and 2560 "reversal" (−0.50, −0.29 — the
  genuine inversions that split dates there into disjoint ranges) and 2430 "plateau" (0.11);
  BC/AD switching relabels the axis in round years (800 BC, 700 BC…) and the readout to 481 BC;
  and dragging the scrubber 100 px moved the window by exactly the expected 4,911 years with its
  width preserved.
- The readout was reworked after use: it sat below the scrubber and the buttons, grew by 68 px on
  hover (79 → 147), and its bottom landed at 853 px in a 720 px viewport — so hovering moved the
  page under the pointer that had just triggered it, and the sentence you were hovering to read
  was below the fold. It is now directly under the chart, laid out in the same shape whether or
  not the pointer is on the curve (measured: **77 px in every state**), with the description
  closed up under the figures rather than a flex gap away, and the chart trimmed to 380 px so the
  whole panel ends at 709 px.

### 9.5 What phase 5 actually landed

- `services/resolution.py`, written from the paper and this app's own `hpd_ranges_from_distribution`
  rather than translated from res14c (§6.4). **Validated against res14c's published output: 0.36%
  mean absolute difference over 1,000–4,000 cal BP, 1.39% maximum**, including the 33-year trough
  at 2,750 cal BP.
- Two performance claims from §6.3 held up: a browser window computes in **0.05 s**, and the whole
  curve in **12–19 s**, so only the whole-curve path is cached — keyed on the curve's *content
  hash*, since the builtin curve files are all overridable by environment variable.
- §6.2's padding is not merely adequate but **exact**: a windowed profile returns numbers
  identical to the same span of a wider run (asserted at `rtol=1e-9`). Slicing the cached
  whole-curve profile for a wide request is therefore not an approximation.
- One improvement on the plan's sketch: percentiles are computed from the *exact* weighted
  distribution of widths per calendar year, gathered sparsely, rather than from a histogram. A
  histogram puts a floor on the precision of the very numbers being reported — the 5th percentile
  at the 2,750 trough is 24 years, which a 50-year bin cannot express.
- `GET /api/curves/{id}/resolution` and `/resolution.csv` (the CSV in res14c's own column order,
  so the two can be compared directly). The panel sits under the curve on a locked shared x-axis,
  with the laboratory error selectable — 15 stays the default for comparability with the
  published figures, with the ±25–30 most commercial dates carry one click away.
- `tests/test_resolution.py` (16 tests, 1 skipped) asserts the *shape*: the plateau worse than
  both its neighbours, the trough at its start better by a factor of three, monotonicity in u0,
  and the windowing identity. The res14c parity test skips unless the fixture is generated —
  `tests/fixtures/res14c/README.md` documents how, and why it is not committed.
- Two things found by using it. Reading the plot's width at fetch time can return a few dozen
  pixels if the layout is momentarily collapsed, which silently turned a 450-year window into 33
  points — a staircase across exactly the sharpest features, from a request that looked perfectly
  successful; the width is now floored. And the panel would have pushed the readout back below the
  fold, so the curve gives up exactly the height the panel takes: measured at 713 px with it on,
  709 px with it off.
- Verified in the running app: over the Hallstatt window the panel reports **312 years at 2,605
  cal BP and 33 at 2,748** — a ninefold change across 150 years — and the readout reads "a ±15 yr
  measurement of a sample from this year calibrates to about 312 years at 95.4%".
- Turning the panel off is supposed to give its height back to the curve, and did not: setting
  only the container's CSS height grew the box while Plotly went on drawing at its old computed
  height, leaving a blank 110px strip where the curve should have expanded. `useResizeHandler`
  does not catch this — it listens for WINDOW resizes, and nothing about the window changed. The
  height is now part of the Plotly *layout*, so the chart is redrawn at it: container, layout and
  rendered SVG all read 380 with the panel off and 270 with it on.

### 9.6 What phase 6 actually landed

- `services/curve_mix.py`, the §4.3 formulae, on the **union** of the two grids restricted to
  their overlap — resampling onto the coarser of the two (Marine20's 10 years against IntCal20's
  1) would throw away detail the mixture can carry perfectly well.
- `POST /api/curves/mixed`, stored as a recipe rather than as data, so a mixture stays honest
  about its parents and they count as in use while it exists (the 409 on deleting an ingredient
  was already there from phase 2 and is now exercised).
- `Mix_Curves` in the OxCal importer, which previously failed the whole import. It matches a
  file's recipe against the mixtures this app already has rather than creating one: parsing must
  not write curves — that contract is the whole of `api/imports.py`'s docstring — and a curve
  quietly created by opening a file is a curve nobody chose to add. When nothing matches, the
  error names exactly what to create, so one visit to the Curves page makes the file importable.
  Both orderings match, with the percentage flipped: 30% IntCal20 IS 70% Marine20.
- One design decision the plan did not foresee, and my own tests found it: **two stored mixtures
  with the same recipe are refused**. They would be the same numbers under two names, and they
  make matching an `.oxcal` file's `Mix_Curves` to one of them arbitrary — which is exactly how a
  test of mine failed, matching a mixture created three tests earlier. The recipe matcher moved
  into `curve_registry.find_mixed` so the importer and the create endpoint use one rule.
- The suite-wide lesson from phase 2 was generalised: the "delete any curve a test stored"
  fixture now lives in `conftest.py` and is requested by both curve test modules, because a
  stored curve changes what the whole application will accept.
- `tests/test_curve_mix.py` (21 tests). The load-bearing ones are about the uncertainty on the
  proportion: that it widens the curve at all, and that it widens it *most where the two curves
  are furthest apart*.
- Verified in the running app: a 3000 ± 30 BP bone calibrates to **3294–3329 cal BP on IntCal20,
  2440–2740 on Marine20, and 2653–2945 on a 30/70 mixture** — between the two, nearer the marine
  end, and wider for the diet uncertainty.

### 9.7 What phase 7 actually landed

- **Compare**: `GET /api/curves/compare` puts two curves on the union grid with their difference
  and its combined error, decimated on the *difference* since that is the series being examined.
  The browser overlays the second curve and draws the difference on its own panel. This is what
  the comparison is for: over each other, two curves are indistinguishable at any zoom fitting a
  millennium on screen — IntCal20 − SHCal20 reads **−38 ¹⁴C years** immediately.
- **Simulate**: `POST /api/curves/{id}/simulate` and a click on the curve. The curve's own
  radiocarbon age at that year, calibrated back — OxCal's `C_Simulate` **without** its random
  scatter, deliberately: a drawn measurement answers "what might one sample give", a different
  answer every time it is asked, where this answers "how well does this year date". Inside the
  plateau a ±25 yr measurement returns **four disjoint ranges over 337 years**, and the dialog
  says so in those words.
- **CSV**: `GET /api/curves/{id}/points.csv` for the visible window, with the local gradient
  alongside.
- **The limitation flagged above is fixed**, and the fix is a different one than expected. The
  readout does not need to be more precise than the sampling — it needs to *agree with the line
  the reader is looking at*. It now interpolates between the drawn samples rather than snapping
  to the nearest, so hovering across a plateau at wide zoom reads 257 → 263 → 268 rather than
  the same figure three times. `utils/seriesLookup.ts`, tested — and its first version read
  descending series backwards, which a reversed calendar axis produces on every page here.
- Three presentation bugs, all found by looking rather than by reading: `/curves/compare` was
  a **404**, because `/curves/{curve_id}` is declared first and "compare" is a perfectly good
  curve id as far as the path is concerned; a rotated y-axis title is limited by the panel's
  *height*, so "IntCal20 − SHCal20" clipped to "ntCal20 − SHCal2" in a 95px panel; and the x-axis
  title ended up stranded halfway down a stack of charts sharing one axis.
- Panel heights: the curve gives up what the panels take, but not without limit — two panels at
  110 left the curve 160px, too short to read the thing the page is about. Panels shrink to 95
  when there are two, and the chart floors at 200.
- The controls were regrouped after the page was used. Eleven of them sat in one wrapping row
  under the scrubber, doing three unrelated jobs, with nothing saying what any applied to. They
  now split by what they act on: **what is shown** (compare-with, the resolution toggle, date
  format) went up to the header bar; **where you are looking** (zoom, presets, export this
  window) stayed under the scrubber; and each panel became a **framed section with a title strip
  that carries its own settings** — the laboratory error and the profile export now sit inside
  the resolution panel, next to the thing they change.
  - Moving controls into the header wrapped it to 120px, which was the same crowding one floor
    up. The space was going to the calendar-grid chip (80 characters of it), now compacted to
    "1–20 yr steps" with the full text in a tooltip: header back to 72px, one line.
  - The frame costs height, so the chart's budget had to learn about it. The first estimate of
    46px left the readout moving 45px when a panel opened — the exact thing the compensation
    exists to prevent — so the constant is now the measured 91. One panel comes out at 380 total
    and the readout does not move; two cannot, since two framed sections are 330px between them,
    so the chart floors at 200 and the page grows instead.
  - The panels' rotated y-axis titles are units only ("yr"). A rotated title is limited by the
    panel's height, and the section header above it already names the quantity.
- The hover tooltip was cut back to what indexes the pointer's position — the calendar age and
  the radiocarbon age — after it was pointed out that it repeated the readout underneath. The
  curve's error and its local gradient were in both places at once, and the floating box was
  covering the curve to say what was already on screen. The curve is named in the tooltip only
  when a comparison is overlaid, where two unlabelled numbers would otherwise be a guess.
  - Removing the raw cal BP that Plotly prints alongside took three attempts, because the value
    plotted **is** cal BP and no axis format turns 2502 into 553 BC. `x unified` prints it as a
    header above the box; plain `x` moves the same number into a label on the axis; `closest`
    removes it but, at the default distance cutoff, stopped firing the moment the pointer drifted
    thirty pixels off the line — which blanked the readout, since that is driven by the same
    hover. `closest` with `hoverdistance: -1` is the combination that works: no cutoff, so
    pointing anywhere in the plot finds the nearest point on the curve. Explicit spike lines
    restore the crosshair that unified had been drawing.
- The resolution panel now starts **off**. Landing on three stacked charts is an answer to a
  question nobody has asked yet; the page opens on the curve alone at full height, and the first
  view costs one request rather than two. The readout's placeholder changes with it — with the
  panel off it says where to turn it on rather than promising a figure that is not there.

### 9.8 What phase 8 actually landed

- `docs/USER_MANUAL.md` gains a **Calibration Curves** section: the library, importing (with what
  is refused and why), mixed curves, browsing, and the expected-resolution panel — written for
  someone deciding whether a period can be dated finely enough to answer their question, which is
  what the resolution panel is actually for.
- **Every number in it was checked against the running app rather than recalled**: IntCal20's grid
  structure, the −42 ¹⁴C year hemispheric offset, 312 years of resolution at 2,605 cal BP against
  33 at 2,748, and the four-ranges-over-337-years simulation at 2,600. The gradient table's
  thresholds were wrong on the first pass — written from memory of the wording rather than from
  `curveShape.ts`, and missing the "shallow" band entirely — which is exactly the drift this
  document's own preamble warns about.
- Two existing entries were corrected rather than appended to: the measurement form's curve field
  no longer claims three curves are all there is, and `REMAINING_WORK.md`'s "What's Actually
  Working" now lists the manager and browser. Best Practices gains the point the whole resolution
  feature exists to make — check what the curve can give you *before* commissioning the dates.

---

## 10. Open decisions

1. **The `res14c` parity fixture** — regenerate-locally-and-skip (recommended, §6.4) versus a
   committed decimated slice with attribution.
2. ~~**`u₀` presets.**~~ **Decided 2026-08-18: the default is ±25**, front and back, with 15 one
   click away. It is what a commercial date carries, and the question anyone opens this panel
   with is "can I date this period well enough", not "what would the best laboratory in the world
   manage". Parity with the published figures is kept where it belongs — the res14c test now
   asks for `u0=15` explicitly, rather than inheriting a default it silently depended on.
3. **Where the browser is reachable from.** `/curves` is certain; whether the curve chip beside a
   date on the Results page should deep-link into the browser at that date's own calendar range
   is a nice touch with a small amount of plumbing behind it.
