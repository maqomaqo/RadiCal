# Spatial metadata, ΔR lookup, and spatial dispersal modelling — implementation plan

Written 2026-09-06. Scope: coordinates on a date; deriving ΔR for a marine date from the
CHRONO/calib.org Marine20 reservoir database; and inferring dispersal — timing, pace, direction —
from dated sites in space.

Written for a **fork**. Nothing here is designed to be merged back upstream unchanged; §12 names
the three files that will conflict if it ever is.

Everything in §2 was **measured against the live calib.org database on 2026-09-06**, not estimated.
Four of those measurements changed the design, and one of them is the reason kriging is second in
the order of work rather than first.

**Reviewed against the code on 2026-09-14**, eight days and five commits after it was written. The
measurements stand; four things in the codebase moved underneath the design and are corrected
inline, each marked *[rev. 2026-09-14]*:

1. **Users and ownership landed (2026-09-07).** Stored curves are now *private to their importer*,
   not install-wide reference data. The ΔR snapshot has to pick a side — §5.2 now does.
2. **Age-depth modelling landed (2026-09-09) and set the precedent §6.3 was guessing at.** A
   statistic computed from the joint draws is a *section of `Analysis.intervals`*, not a column of
   its own. Dispersal follows it; item 6 no longer needs a migration.
3. **Gibbs now runs parallel chains under one burn-in cut (b3721eb).** The joint draws a dispersal
   fit consumes are pooled across chains. Nothing in the estimator changes, but the coverage test in
   §10 must go through `solve_chains`, not a single chain.
4. **Plotly 2.35.3 deprecates `scattermapbox` for `scattermap`** (MapLibre, token-free). Both are in
   the bundle; the plan names the one that is not deprecated.

Line references and the Alembic head are updated where cited. §14 is new: the operation sequence
for executing this on the current branch.

---

## 1. Verdict

Both features are implementable, and they are not equally hard.

| | ΔR from coordinates | Spatial dispersal models |
|---|---|---|
| Blocked on anything? | No | No |
| New backend dependency | **none** | **none** (numpy/scipy are already in) |
| New frontend dependency | **none** (§7.1) | none |
| New science | none — reproduces a published method exactly (§2.3) | yes — the estimator is a design choice, not a lookup |
| Data it needs | 2060 rows, 193 KB (§2.1) | what the app already computes |
| Where the risk is | redistribution permission (§11.1) | the estimator being *wrong in a way that looks right* (§6.8) |
| Rough effort | ~1 week after §4 | ~2 weeks for the first model |

They share exactly one thing — a latitude and a longitude on a date — and that shared part (§4) is
worth shipping on its own, before either.

---

## 2. What was measured

### 2.1 The database is small, complete, and one request away

`POST http://calib.org/marine/query/query.php` is a public read-only SQL interface the site itself
offers (its "Generalized Query" link), reachable after a `GET` of `/marine/query/` to pick up the
PHP session that connects the DB user:

```
select mapno,lon,lat,deltar,deltarerr,reference,locality,
       c14age,c14err,labid,collectionyear,reservoirage from details
```

returned the **whole table in one request: 2060 rows, ~800 KB of HTML, 1.5 s**. Parsed to CSV it is
**193 KB** — smaller than any of the three calibration curves this repo already ships.

What is in it:

| | |
|---|---|
| Rows | 2060 |
| Distinct coordinates | 1217 |
| Distinct localities | 1130 |
| Distinct references | 201 |
| ΔR range | −547 … +2324 yr (median −78) |
| ΔR error | median 35 yr, min 5, max 183 |
| Rows with **no** ΔR error | **30** |
| Nearest-neighbour distance between sites | median 39 km, p90 223 km, max 1975 km |
| `max(last_changed)` | 2026-05-11 |

Three consequences the design has to carry:

- **30 rows have no uncertainty.** An inverse-variance weighted mean cannot weight them. They are
  excluded from the combination and *listed as excluded*, never silently dropped (§5.4).
- **1217 coordinates for 2060 rows** means co-located measurements are the norm, not the exception.
  That is what §2.4 is about.
- The freshness probe is cheap: `select count(*), max(last_changed) from details` is a **2 KB**
  response. It is the whole basis of §5.6.

### 2.2 The encoding is mixed, per row

The same response contains correctly-encoded UTF-8 (`Håkansson`) and raw cp1252 bytes
(`Thyborøn` arriving as `Thybor\xf8n`). **60 fields across 2060 rows** fail a strict UTF-8 decode,
every one of them valid cp1252. Decoding the whole document as cp1252 breaks the other kind.

*[corrected 2026-09-14, from a capture]* This said **54**, measured on 2026-09-06. Re-fetched and
counted on 2026-09-14 it is **60** — one field per affected row, all Danish and Norwegian place
names, all decoding cleanly under cp1252 with none failing both. The direction of the drift is the
thing to note rather than the number: the source is still being edited, and edits are still
arriving in mixed encodings, so this is a property to keep counting rather than one that was
measured once. The ingest report carries the count for exactly that reason.

The ingest therefore decodes **per field**: UTF-8 strict, falling back to cp1252 on that field
alone. A field that fails both is stored verbatim as bytes-escaped text and counted in the ingest
report, so a locality name is never quietly replaced by `�`.

### 2.3 calib.org's own combination rule, reverse-engineered and confirmed

`POST /marine/regioncalc.php` with `MapNo[]=…` returns the weighted mean the site's UI shows. Its
explanation page (`AverageDeltaR.html`) states the rule in one sentence — *the reported uncertainty
is the maximum of the standard deviation of ΔR and the weighted uncertainty in the mean* (citing
Bevington 1969) — and three test cases confirm the exact arithmetic:

    w_i      = 1 / σ_i²
    ΔR       = Σ w_i ΔR_i / Σ w_i
    internal = 1 / √(Σ w_i)
    scatter  = √( Σ w_i (ΔR_i − ΔR)² / Σ w_i  ×  n/(n−1) )      [ 0 when n = 1 ]
    σ        = max(internal, scatter)

| Points | calib.org | internal | scatter | max | quadrature |
|---|---|---|---|---|---|
| 995, 992 | **−239 ± 20** | 20.34 | 0.67 | **20** | 20 |
| 2368, 1470, 2350, 2024, 834 | **90 ± 247** | 13.15 | 246.91 | **247** | 247 |
| 2590, 1006, 2147 | **−165 ± 29** | 17.13 | 28.65 | **29** | 33 |

The third case exists only to discriminate: `max` gives 29 and adding in quadrature gives 33.
calib.org prints 29. **The rule is `max`.**

This matters more than it looks. It means the app can reproduce the number a reviewer will get from
calib.org's own page, to the printed integer, **without calling calib.org at analysis time** — which
is what makes the local mirror in §5 possible at all.

### 2.4 The measurement that changed the plan: the field is nugget-dominated

An empirical semivariogram over all 2060 points, in great-circle distance bins:

| Separation | pairs | γ(h) | √γ (yr) |
|---|---|---|---|
| < 1 km (co-located) | 2456 | **6 992** | **84** |
| 0–5 km | 2828 | 7 692 | 88 |
| 10–25 km | 792 | 24 706 | 157 |
| 25–50 km | 1156 | 12 070 | 110 |
| 50–100 km | 2340 | 12 901 | 114 |
| 100–200 km | 5454 | 17 239 | 131 |
| 200–400 km | 10 623 | 20 893 | 145 |
| 800–1600 km | 67 409 | 23 397 | 153 |
| *global variance (sill)* | | *43 309* | *208* |

And in a dense, oceanographically coherent box (45–62°N, 15°W–15°E, n=167) the variogram reaches
its local sill (14 855) by roughly 100–200 km and then goes **flat**.

Read the first row again. **Two shells from the same beach disagree by 84 yr RMS — 2.4× the median
reported measurement error of 35 yr.** That is not noise in the measurements; it is real ΔR
variability by species, feeding habit, and collection year, sitting at zero distance.

Three design consequences:

1. **The floor on any interpolated ΔR is ~84 yr, wherever you stand.** No interpolator can do
   better, because the disagreement is already there at h = 0.
2. **calib.org's `max(internal, scatter)` rule is not naive — it is the right rule.** Pooling
   co-located points makes `scatter` dominate automatically, so the reported σ inflates to the real
   local spread instead of shrinking as 1/√n toward a false precision. Reproducing it exactly (§2.3)
   is therefore also the *statistically* correct choice, not merely the compatible one.
3. **Kriging buys much less than it appears to.** Between the nugget (≈7 000) and the sill
   (≈43 000) globally — and between 4 234 and 14 855 in the NW Europe box — the structured,
   krigeable fraction is modest, and it is exhausted within ~100–200 km. A kriged global surface
   would mostly render a nugget-dominated local mean as a smooth, confident-looking field. That is
   the same number the nearest-N weighted mean gives, wearing a lab coat.

This is why the order of work puts nearest-N first and kriging behind it (§5.7), and why kriging,
if built, is **regional and user-bounded** rather than global and automatic.

### 2.5 N is not a setting to bury

The nearest-N weighted mean at six real coastlines, as N varies:

| Location | nearest pt | 10th pt | N=3 | N=5 | N=10 | N=20 | N=50 |
|---|---|---|---|---|---|---|---|
| Strangford Lough, N Ireland | 0 km | 144 km | −206 ± 53 | −209 ± 44 | −172 ± 76 | −176 ± 70 | −148 ± 58 |
| Cape Town, South Africa | 5 km | 413 km | 9 ± 62 | 18 ± 64 | 2 ± 54 | 5 ± 50 | 9 ± 67 |
| Callao, Peru (upwelling) | 16 km | 818 km | **173 ± 351** | 106 ± 246 | 51 ± 172 | 35 ± 166 | **−16 ± 126** |
| Hokkaido, Japan | 22 km | 389 km | 44 ± 174 | 45 ± 164 | 98 ± 158 | **148 ± 184** | **−57 ± 252** |
| Torres Strait, Australia | 3 km | 46 km | −136 ± **23** | −148 ± 31 | −184 ± 45 | −185 ± 41 | −202 ± **74** |
| Tampa, Gulf of Mexico | **163 km** | 269 km | −22 ± 36 | −41 ± 51 | −54 ± 52 | 16 ± 127 | −3 ± 199 |

Peru moves **189 years** between N=3 and N=50. Hokkaido moves 205 and changes sign twice. Torres
Strait's uncertainty **triples** as N grows, which is the formula behaving correctly: more distant
points bring real scatter, and the σ says so.

So N cannot be a preference hidden in a settings panel with a sensible default. The UI has to make
the answer's dependence on N visible at the moment of choosing (§7.3), and the stored provenance has
to record the exact N and the exact points used (§5.5) — otherwise a published ΔR cannot be
reproduced even by the person who made it.

### 2.6 Tampa is the other warning

Its nearest ΔR point is **163 km** away. The nearest-neighbour statistics in §2.1 (p90 = 223 km) say
that is not rare. Distance-to-nearest-point is the single most informative thing the app can show
next to a derived ΔR, and it must be shown before the value is accepted, not after.

### 2.7 Feeding habit is a real bias, on a dirty field, that usually does not matter — and occasionally matters a great deal

calib.org's own guidance is that feeding habit should be considered when choosing ΔR values:
suspension feeders take carbon from the water column, some deposit feeders take up old carbon from
sediment or rock, and migratory animals may not represent the region they were found in. Three
questions decide how the app should act on that: is the field usable, is the bias real, and does it
change the answer.

**The field is dirty.** `taxa.feeding` holds **26 distinct spellings** across 2060 rows for what is
really six or seven classes *[corrected 2026-09-14: 25 as first measured, 26 in the capture — the
extra is `'\xa0deposit'`, a deposit feeder behind a non-breaking space. It folds in with the rest
because Python's `str.strip()` counts U+00A0 as whitespace, which is luck rather than design, so
`services/reservoir.py` has a test saying so out loud]* — case variants (`Deposit`/`deposit`, `Suspension`/`suspension`), a
synonym (`filter` = suspension), a literal string `NULL`, and — the one that matters —
**values truncated at 16 characters** by the source column width:

    'Deposit or suspe'   'Suspension/Depos'   'Deposit feeder o'
    'Herbivore/Deposi'   'Carnivore/scaven'

Those are compound judgements the original author deliberately hedged, cut off mid-word. They
cannot be assigned to one class, so they normalise to **`ambiguous`** and are never silently sorted
into either side of the thing they were hedging about. Normalised (raw string retained alongside):

| class | n | raw spellings folded in |
|---|---|---|
| suspension | 1295 | `suspension`, `Suspension`, `filter` |
| unknown | 293 | `unknown`, `Unknown`, `NULL`, empty |
| deposit | 176 | `Deposit`, `deposit` |
| carnivore | 111 | `Carnivore`, `carnivore`, `predator`, `Scavenger`, `Omnivore` |
| grazer | 86 | `herbivore`, `Herbivore`, `browser`, `Algal Grazer`, `commensalism` |
| migratory | 86 | `migratory` |
| ambiguous | 13 | the five truncated compounds above |

*[confirmed 2026-09-14]* `services/reservoir.py`'s table was written from this section's prose
before the source was reachable. Run against all 2060 rows of the capture it reproduces **every one
of these seven counts exactly**, with no spelling left unrecognised — so the fold above is not
just plausible, it is the one the database supports.

**The bias is real, and it is a population bias, not a per-sample offset.** Comparing raw class
means is worthless — between-region variation swamps everything — so the test is *paired*: each
non-suspension point against the suspension points near it. Bootstrap 95% CI on the mean
difference, at three pairing radii:

| class vs suspension | same coordinate | within 25 km | within 50 km |
|---|---|---|---|
| **deposit** | +42 [−0.5, +81] (n=45) | +35 [−3.6, +67] (n=96) | +35 [−2.3, +66] (n=107) |
| **migratory** | *no co-located pairs* | **+55 [+31, +80]** (n=26) | **+42 [+18, +65]** (n=36) |
| **carnivore** | +10 [−15, +35] (n=35) | +34 [+8.5, +65] (n=52) | +40 [+18, +65] (n=61) |
| grazer | +5 [−18, +31] (n=34) | +18 [−21, +70] (n=46) | +26 [−14, +78] (n=49) |
| unknown | +10 [−25, +45] (n=60) | +18 [−8.7, +43] (n=126) | +20 [−2.0, +42] (n=143) |

Read three things off that table.

1. **Deposit feeders sit older by roughly +35 to +42 yr, in the direction old-carbon uptake
   predicts, consistently at every radius** — but the interval grazes zero, and the *per-sample*
   standard deviation of those differences is **139–179 yr**, four times the mean effect. The class
   is biased; an individual member of it is unpredictable. Migratory and carnivore show the same
   sign, and migratory is the cleanest signal in the table (+55 [+31, +80]) with the *smallest*
   scatter (sd 66) — while having **no co-located pairs at all**, because whale, walrus, seal and
   penguin bone is never sampled from the same beach as a bivalve. Its case rests on the a-priori
   argument, not on this table.
2. **`unknown` is not a suspect class.** At +18 [−8.7, +43] it behaves like the suspension pool it
   is mostly drawn from. Excluding it by default would discard 293 rows — 14% of the database — to
   fix nothing.
3. **Grazers are indistinguishable from suspension feeders** at every radius. Algae and herbivorous
   gastropods eat the same recent carbon.

**It usually does not change the answer.** Jackknifing every site in the database as a query point,
taking its nearest 10 neighbours, and comparing the derived ΔR with and without the
deposit/migratory/carnivore points:

| | |
|---|---|
| Mean composition of a nearest-10 set | 63% suspension, 15% unknown, 9% deposit, 5% carnivore, 4% grazer, 3% migratory |
| Sets containing ≥1 deposit / migratory / carnivore point | **61.1%** |
| Median count of those, when present | 2 of 10 |
| Sets where they are half or more of the neighbours | 11.8% |
| **Median shift in ΔR from excluding them** | **8 yr** |
| p90 shift | 52 yr |
| Max shift | **465 yr** |
| Shifts exceeding the median reported error (35 yr) | 18.5% of affected sets |
| Shifts exceeding the co-located nugget (84 yr, §2.4) | 4.3% |
| Sets left with fewer than 2 usable points after exclusion | 40 |

**This is what kills the obvious design.** A dialog asking *"there are deposit feeders nearby —
exclude them?"* would fire on **three lookups in five**, and in four of those five the answer moves
by less than 8 years. That is a prompt trained to be dismissed, arriving in front of the 4% of cases
where the answer moves by more than the entire irreducible noise floor of the database. The
conclusion drives §5.4.1 and §7.3: **do not ask the question — compute the answer to it and show
the difference.**

---

## 3. Where the code stands today

### 3.1 What already exists and is directly reusable

- **`ProjectSample.delta_r` / `delta_r_error`** exist on both `ProjectSample` and
  `RadiocarbonSample` (`models.py`), because deploying is a field-for-field copy. Coordinates take
  exactly the same shape and the same reasoning: they are a property of *where the sample came
  from*, shared by dates from one site.
- **`MeasurementInput._delta_r_has_an_uncertainty`** (`schemas/sample.py`) already refuses a ΔR
  without its error, and calibration already combines that error in quadrature. **A derived ΔR
  needs no change to the calibration path at all** — it arrives as the same two numbers a typed one
  does.
- **`SampleSelectionBar`**'s ΔR popover (`components/SampleSelectionBar.tsx`) already applies one ΔR
  to a multi-date selection, with the "who it lands on" note. The lookup is a second tab in a
  control that already exists.
- **`date_import.COLUMN_ALIASES`** is header-driven and order-independent; adding coordinates is two
  entries plus two template columns.
- **`StoredCalibrationCurve`**'s discipline — `source_text` verbatim, `parse_options` recorded,
  `content_sha256` re-checked on every load, corrections made by *adding* rather than editing — is
  precisely the discipline a ΔR snapshot needs, for precisely the same reason (§5.2).
- **`intervals.report`** (`intervals.py:752`) turns joint draws into a JSON block on
  `Analysis.intervals`, with the "NULL means not asked for" convention the results page already
  understands. *[rev. 2026-09-14]* Since 2026-09-09 that block also carries `age_depth`, computed
  from the same draws by a run that "wants draws" for a reason of its own (`gibbs.py:2210`,
  `report(..., durations=False)`). **Dispersal statistics are the same shape of thing and take a
  section of the same block** (§6.3).
- **`ModelElement.posterior`** already stores a Boundary's posterior in the shape a per-date result
  uses. A site's modelled arrival time is a Boundary, so it needs no new column (§6.2).
- **`intervals.summarise`** (`intervals.py:214`) turns a vector of joint draws into the median and
  68/95 HPD block a duration reports as. *[rev. 2026-09-14]* Named here instead of
  `calibration.hpd_ranges_from_distribution` (now `calibration.py:59`, density-ranked on a grid
  since b3721eb): a per-draw fit yields a *sample* of speeds, not a density on a grid, and
  `summarise` is the function durations already take for exactly that input. A posterior for a
  *speed* in km/yr reads like every other quantity in the app if it goes through this.
- **`agreement.overall_agreement`** (`agreement.py:151`) is the app's existing idiom for "how badly
  does this one observation disagree with the model". A site's residual against a fitted front is
  the spatial analogue, and should be presented in the same register (§6.6).
- **plotly.js-dist-min (2.35.3) is already a dependency and already contains `scattergeo`,
  `scattermap`, `densitymapbox` and the token-free `open-street-map` / `carto-positron` styles** —
  verified in `frontend/node_modules`. See §7.1: the map costs no new dependency.

### 3.2 The one thing that does not exist, and constrains §6

`AnalysisResult.posterior_samples` holds **marginal** draws. `exact_chain.py:1159` says so outright:

> Those draws are from the MARGINALS and carry no joint correlation between dates. Nothing reads
> joint structure off them: `estimate_durations` is the one caller that needs it and takes its own
> draws from the joint posterior.

Joint draws (`intervals.DEFAULT_DRAWS = 20000`) exist **only inside a solver run**. So a dispersal
statistic — which is a function of many modelled ages at once, exactly like a duration — **cannot be
computed after the fact from stored results.** It is computed during the run or not at all.

Persisting the joint draws instead was considered and rejected: 20 000 draws × 40 parameters × 8
bytes is 6.4 MB per analysis, it creates a second source of truth for a posterior the results
already describe, and `intervals.py` established the alternative convention two features ago.

---

## 4. Part A — coordinates on a date

Useful on its own, and a prerequisite for both features. Ship it first and alone.

### 4.1 Schema

Four columns, two per table, mirroring the `delta_r` / `delta_r_error` pair exactly:

```python
# ProjectSample and RadiocarbonSample, both:
latitude  = Column(Float, nullable=True)   # decimal degrees, WGS84, −90..90
longitude = Column(Float, nullable=True)   # decimal degrees, WGS84, −180..180
```

One Alembic revision, all nullable, no backfill, no server default. Existing rows are unaffected and
every existing analysis reruns identically. *[rev. 2026-09-14]* The same revision carries the two
per-project settings this plan needs — `projects.coordinate_precision` (String, default `'full'`,
§4.5) and `projects.map_tiles_enabled` (Boolean, default false, §4.5 rule 4 / §7.1) — so item 2
needs no migration of its own.

**No `sites` table.** A site is *derived*, not stored: dates sharing a coordinate are one site, and
in a modelled analysis a site is the model element its dates belong to (§6.2). A first-class `Site`
entity is the tidier model and is listed as an open decision (§13.1), but it would fork the schema
much further from upstream for something the model tree and the collection already express.

**No elevation, no coordinate uncertainty, no CRS column.** WGS84 decimal degrees only. A file
carrying anything else is refused by name (§4.3) rather than assumed.

### 4.2 Validation

In `schemas/sample.py`, beside the existing ΔR validator and in the same voice:

- `latitude: Optional[float] = Field(default=None, ge=-90, le=90)`
- `longitude: Optional[float] = Field(default=None, ge=-180, le=180)`
- `_coordinates_come_in_pairs`: a latitude without a longitude (or the reverse) is an error, not a
  half-located date. Same shape as `_delta_r_has_an_uncertainty`, same reason: half a fact is not a
  usable fact.
- `(0, 0)` is accepted but **warned** on import — it is in the Gulf of Guinea and is overwhelmingly
  a spreadsheet's empty cell rather than a site.

### 4.3 Import

Two new `COLUMN_ALIASES` entries:

```python
'latitude':  ('latitude', 'lat', 'y', 'dec_lat', 'decimal_latitude', 'lat_dd'),
'longitude': ('longitude', 'lon', 'long', 'lng', 'x', 'dec_long',
              'decimal_longitude', 'lon_dd'),
```

and both appended to `TEMPLATE_COLUMNS`, with the existing marine example row (`NZ-7756`,
marine20, ΔR −7 ± 45) given real coordinates so the template demonstrates the ΔR lookup as well as
the ΔR column.

**Degrees-minutes-seconds is refused, not parsed.** A cell matching `54°30'N`, `54 30 00 N`,
`N54.5` or similar fails the file with a message naming the format the app wants and showing the
offending cell. Guessing between `54°30'` and `54.30` is a 30-arc-minute error that no downstream
check would catch, and the importer's standing rule is to fail naming itself rather than read a
construct as the nearest thing implemented.

### 4.4 Export

`latitude` and `longitude` join `results.CSV_HEADER`, the JSON export, and the PDF's date table —
subject to §4.5. The app's own results export is already an accepted import format, so a table can
be exported, corrected in a spreadsheet, and imported back with its coordinates intact.

### 4.5 Coordinates are sensitive data

Archaeological site coordinates are restricted by many heritage bodies, landowners, and Indigenous
data-governance frameworks. The app treats them accordingly, and the rules are load-bearing on the
rest of this plan rather than decoration on it:

1. **Full precision is stored.** Degrading data at rest helps nobody and cannot be undone.
2. **A project carries a `coordinate_precision` setting** — `full` (default) | `0.01°` (≈1.1 km) |
   `0.1°` (≈11 km) | `1°`. It applies to **every export and every rendered figure**, and to nothing
   else. Rounding is round-half-to-even on the stored value, applied at serialisation, so two
   exports of the same project agree.
3. **No coordinate ever leaves the installation.** This is why the ΔR lookup must be a local
   mirror (§5) — the live-query design is not merely fragile, it is **forbidden by this rule**,
   because a nearest-N query to calib.org *is* a disclosure of a site location to a third party.
4. **The basemap is the one remaining leak, and it is opt-in.** A raster tile request tells a tile
   server which 256×256 square of the earth someone is looking at. The default basemap is therefore
   the **offline** one (§7.1); tiles are enabled per project, once, behind a notice that says what
   the request discloses. Defaulting to tiles and mentioning it in a footnote would be the same
   disclosure with better manners.

---

## 5. Part B — ΔR from coordinates

### 5.1 The shape of it

```
calib.org  ──(explicit, manual)──▶  snapshot file  ──▶  marine_reservoir_points
                                    (shipped +                    │
                                     re-fetchable)                ▼
                                                        services/reservoir.py
                                                     nearest(lat, lon, n, filters)
                                                     combine(points) → (ΔR, σ)
                                                                  │
                                                                  ▼
                                                    ProjectSample.delta_r,
                                                    delta_r_error,
                                                    delta_r_source  (provenance)
```

Nothing in the calibration path changes. A derived ΔR is the same two numbers as a typed one, plus
a record of where they came from.

### 5.2 The snapshot, and why it is versioned like a curve

Two tables:

```python
class MarineReservoirSnapshot(Base):
    id                  # 'calib-marine20-2026-05-11' — source + its own last_changed
    source              # 'calib.org/marine (CHRONO Marine20 Reservoir Database)'
    fetched_at
    source_last_changed # max(details.last_changed) at fetch time
    record_count
    content_sha256      # over the normalised rows, as StoredCalibrationCurve does
    citation            # Reimer & Reimer 2001, Radiocarbon 43:461-3
    note

class MarineReservoirPoint(Base):
    snapshot_id, map_no            # (composite key)
    latitude, longitude
    delta_r, delta_r_error         # error nullable — 30 rows have none (§2.1)
    reference, locality, subregion, notes
    c14_age, c14_error, lab_id, collection_year, reservoir_age
    genus, species, feeding        # needed for the filter in §5.4
```

The rules are `StoredCalibrationCurve`'s rules, and they are here for the same reason — **a
published result must not move underneath the person who published it**:

- A snapshot's numbers never change. A newer table is a **new snapshot**, not an edit.
- `content_sha256` is verified on load. A snapshot that does not hash to its recorded value refuses
  to serve rather than serving quietly-different numbers.
- A snapshot referenced by any stored `delta_r_source` cannot be deleted; it can be superseded.

`marine_reservoir_YYYY-MM-DD.csv` ships in `calibration_data/` beside the `.14c` files, so a fresh
install has a working ΔR lookup with no network, and two installs of the same version compute the
same ΔR. §11.1 is the permission question this raises.

*[rev. 2026-09-14]* **Who can see a snapshot.** Users landed on 2026-09-07 and a stored curve is now
private to its importer (`StoredCalibrationCurve.created_by`, and the reasoning on it: a curve
someone is still developing has nobody vouching for it). The snapshot takes the same split the
curves take:

- The **bundled** snapshot is a file in `calibration_data/`, served the way the three shipped
  `.14c` files are — no row, no owner, install-wide, read-only. It is the one the fresh install
  uses and the one two installs agree on.
- An **ingested** snapshot is a row with `created_by`, private to the account that ran the ingest,
  listed for nobody else — exactly the curve rule, for exactly the curve reason. `POST
  /api/reservoir/snapshots` requires `current_user` like every other write.
- `delta_r_source.snapshot_id` names either kind. An analysis whose provenance names a private
  snapshot is still reproducible by its owner, and by anyone else from the map numbers on
  calib.org's page — which is the property §5.5 was built for.

Note that `genus`/`species`/`feeding` come from the taxa tables, so the ingest query joins them:

```
select d.mapno, d.lon, d.lat, d.deltar, d.deltarerr, d.reference, d.locality,
       d.subregion, d.notes, d.collectionyear, d.reservoirage,
       d.c14age, d.c14err, d.labid, t.genus, t.species, t.feeding
from details d left join taxa t using (taxano)
```

### 5.3 Ingest

`POST /api/reservoir/snapshots` (and the same code behind `python -m app.services.reservoir_ingest`)
performs the whole fetch: session cookie, the §5.2 query, per-field decode (§2.2), parse, hash,
insert, report. It is:

- **Explicit.** Never on a timer, never on startup, never as a side effect of a lookup. It is the
  only code in the app that talks to the internet, and it does so because a person asked.
- **All-or-nothing**, in the exact sense `date_import` already means it: a snapshot that fails to
  parse is not written, and the report lists every problem rather than the first.
- **Honest about the source.** The report states the row count, `max(last_changed)`, the number of
  rows with no ΔR error, the number needing a cp1252 fallback, and the hash. It is a courtesy
  interface offered for interactive use on an Apache 2.4.61 / PHP 5.6.40 host — the ingest issues
  one query and does not retry in a loop.

### 5.4 The service

`services/reservoir.py`, no new dependencies:

```python
def nearest(lat, lon, n, snapshot_id, *, feeding=None, max_km=None) -> List[Point]
def combine(points) -> Tuple[float, float, CombineReport]
```

- **Distance is great-circle** (haversine, R = 6371.0088 km) — the same distance calib.org's
  "nearest points" ranks by, so the point list is the one their page would show.
- `combine` implements §2.3 **exactly**, including `max(internal, scatter)`, and its tests are the
  three validated cases in that table plus twenty random point sets checked against
  `regioncalc.php`.
- **A point with no ΔR error cannot be weighted.** It is excluded from the arithmetic and named in
  the `CombineReport` as excluded, with its map number. Thirty rows in 2060 will hit this.
- **Feeding habit is normalised, never filtered on by default.** See §5.4.1.
- The report also carries what §2.6 demands: **distance to the nearest point**, the distance to the
  farthest point included, and — always, whether or not the user asks — the **counterfactual**
  value with the biased-habit classes removed (§5.4.1).

### 5.4.1 Feeding habit — the recommendation

§2.7 measured three things: the field carries 25 spellings for 7 classes, deposit/migratory/
carnivore points are genuinely biased old by ~35–55 yr *on average* with per-sample scatter four
times that, and excluding them moves the derived ΔR by a median of **8 yr** while occasionally
moving it by **465**.

**Recommended: no prompt, no default exclusion, no down-weighting. Compute both values every time
and show the difference; escalate to a warning only when the difference is large enough to matter.**

Concretely, `combine` always returns two results — `included_all` and `suspension_equivalent`
(everything except `deposit`, `migratory`, `carnivore`) — plus the classification of every point.
The default applied value is `included_all`, and the UI states the other one (§7.3). The escalation
threshold is the median reported ΔR error, **35 yr** — decided 2026-09-06, not provisional: below
it the difference is a line of small type, at or above it the panel says so plainly before anything
is applied. That fires on roughly **11%** of lookups (18.5% of the 61% that contain such a point)
instead of on 61%.

35 yr rather than the 84 yr co-located nugget (§2.4) because the two thresholds answer different
questions. The nugget is the noise floor of the *database* — the disagreement two shells from one
beach show regardless of habit. The 35 yr median reported error is the precision an individual
published ΔR claims for itself, and therefore the point at which swapping one set of points for
another stops being absorbed by the value's own stated uncertainty. A habit-driven difference the
size of a typical point's error bar is a difference the archaeologist should see; setting the bar at
84 would stay silent on more than half the cases where the derived value moves by more than its own
σ. The cost of the lower threshold is warning on ~11% of lookups instead of ~4%, which is well
inside the range a warning can occupy without being trained away (§11.8).

The number is `FEEDING_SHIFT_WARN_YEARS` in `services/reservoir.py`, defined next to the §2.7
measurement that justifies it, so a later change is a change to one named constant with its
reasoning beside it.

Why each of the rejected alternatives is rejected:

- **Prompting on presence** — fires on 61% of lookups to change the answer materially in 11% of
  them. A dialog dismissed four times out of five is not a safeguard; it is training.
- **Excluding biased classes by default** — the app's default would then disagree with calib.org's
  published number for the same coordinates, which throws away the reproducibility §2.3 exists to
  buy. The reference implementation includes everything; so does the default here, visibly.
- **Down-weighting** (e.g. half weight for a deposit feeder) — a weight in this formula means
  *this measurement is less precise*, and that is false. A deposit feeder's ΔR is not noisier; it is
  **biased by an unpredictable amount** (sd 139–179 yr about a +35 yr mean). Any specific fractional
  weight would also be a number the app invented, which is the silent approximation this codebase
  refuses everywhere else.
- **Bias inflation** — keeping the point but adding the paired scatter in quadrature to its error is
  the most defensible of the rejected options, and it is still a correction the app made up on the
  user's behalf, applied to a per-sample effect the paired data says is not estimable per sample.
  Recorded here as the one worth revisiting if §13.6 is ever answered with real per-species data.

Rules the service enforces regardless of what the user chooses:

- **Exclusion never leaves fewer than 2 weighted points.** Forty query points in the jackknife would
  be reduced below that. The service refuses and says which classes it would have had to drop,
  rather than returning a "weighted mean" of one point whose σ is just that point's own error.
- **`unknown` and `grazer` are never excluded by any preset** — §2.7 shows both behave like the
  suspension pool, and dropping `unknown` alone would discard 14% of the database.
- **`ambiguous` is its own class and is never folded into either side.** Those five values are
  truncated compound judgements (§2.7); the app shows the raw string it was given.
- **The raw `feeding` string is stored and displayed alongside the normalised class**, so the
  normalisation table can be corrected later without any stored provenance becoming a lie.
- **Migratory is presented first** in any list of flagged points, with its own one-line reason —
  *the animal may not have lived where it was found* — because that is an a-priori argument, not
  the statistical one behind the other two classes.

### 5.5 Provenance

One new nullable JSON column on `ProjectSample` and `RadiocarbonSample`:

```python
delta_r_source = Column(JSON, nullable=True)
# {'method': 'nearest_n',
#  'snapshot_id': 'calib-marine20-2026-05-11',
#  'centre': {'lat': 54.5, 'lon': -5.6},
#  'n': 5, 'map_nos': [995, 992, 1001, 987, 1004],
#  'excluded_no_error': [], 'excluded_by_user': [],
#  'filters': {'feeding_preset': None, 'max_km': None},
#  'feeding': {'suspension': 7, 'deposit': 2, 'migratory': 1},   # of the points USED
#  'feeding_raw': {995: 'suspension', 1001: 'Deposit', ...},     # verbatim source strings
#  'counterfactual_suspension_only': {'delta_r': -188.0, 'delta_r_error': 61.0, 'n': 7},
#  'nearest_km': 0.4, 'farthest_km': 144.2,
#  'computed_at': '2026-09-06T09:11:00Z'}
```

`NULL` means hand-entered, which is not the same as unknown. Editing ΔR by hand clears it —
provenance that survives an edit is a lie about the number beside it.

This is what makes a derived ΔR citable: given the snapshot id and the map numbers, anyone can
reproduce the value, including on calib.org's own page.

### 5.6 Freshness, never auto-update

The 2 KB probe from §2.1 backs one piece of UI: a line on the Curves/reference surface reading
*"ΔR snapshot: 2060 records to 2026-05-11. calib.org now reports 2078 records to 2026-08-02."* with
a button that runs §5.3.

Automatic refresh is refused. A ΔR that changes because a remote database changed, under an
analysis someone has already published, is exactly the failure `StoredCalibrationCurve` was built to
prevent.

### 5.7 Kriging — the second method, with conditions

§2.4 is the reason this is second, and the reason it is bounded when it comes. If built, it is
**ordinary kriging in ~150 lines of numpy** (no `pykrige`), and it must:

1. Be **regional**: fitted inside a bounding box the user chooses, never a global surface.
2. **Show the fitted variogram**, nugget included, as a figure the user reads before accepting a
   value. Given §2.4 the nugget will usually dominate, and the user should see that it does.
3. **Refuse to extrapolate** — outside the convex hull of the fitted points, or beyond the fitted
   range, it declines rather than returning a number with a confident-looking σ.
4. **Report combined error**: measurement + interpolation, as Ulm et al. (2023) do for the
   Australian atlas.
5. Carry, in the UI and in `delta_r_source`, the honest statement that great-circle distance
   crosses land. A kriged ΔR between two sides of a peninsula is interpolating across a barrier the
   model does not know exists. Over-water distance is the fix and is **not** in scope (§9).

Reference method: Ulm, S., O'Grady, D., Petchey, F., Hua, Q., Jacobsen, G., Linnenlucke, L., David,
B., Rosendahl, D., Bunbury, M.M.E., Bird, M.I., and Reimer, P.J. (2023) *Australian marine
radiocarbon reservoir effects: ΔR atlas and ΔR calculator for Australian mainland coasts and
near-shore islands.* Radiocarbon 65(5), 1139–1159. doi:10.1017/RDC.2023.95. Their calculator is
regional, hand-curated, and coastline-aware — which is the standard a global auto-kriger would have
to meet and would not.

---

## 6. Part C — spatial dispersal models

### 6.1 What is actually being asked

"When did *X* arrive here, how fast did it move, and which way." The app's advantage over every
summed-probability approach is that it does not have to summarise dates into a curve: it already
computes a **posterior for a modelled event**, with ordering constraints and outlier models applied.
The feature that follows from that advantage is: **propagate the full chronological posterior into
the dispersal estimate**, rather than regressing point estimates and reporting a standard error that
pretends calibration was exact.

### 6.2 The unit of observation is a site's Boundary, not its oldest date

This is the most important decision in Part C.

The naive estimator takes each site's earliest date as its arrival time. It is biased, in a known
direction and by an unknown amount: you almost never excavate the genuinely first thing at a site,
so **the oldest date is a *terminus ante quem* for arrival, not an estimate of it.** Regressing
those against distance gives a front that is systematically late everywhere, and — worse — late by
an amount that varies with how hard each site was dug.

OxCal's Boundary is the standard correction, and RadiCal already computes it. A site modelled as a
Phase has a start Boundary whose posterior extrapolates *beyond* the oldest dated member, by an
amount the span of the dates and their count imply. So:

- **Primary estimator**: a site is a model element (a Phase, or a Sequence of Phases) whose dates
  share coordinates; its arrival time is the posterior of the Boundary that opens it —
  `ModelElement.posterior`, already stored, already plotted, already exported.
- **The site's coordinate** is the centroid of its dates' coordinates, and the app **refuses**
  rather than averaging when they scatter more than a project-level threshold (default 5 km): dates
  from two sites in one Phase is a modelling error, and quietly placing the Phase between them
  would hide it.
- **Fallback for a group calibration** (no model, hence no Boundary): the site's *earliest dated
  event*, labelled exactly that in every panel, plot and export, with the bias named in the panel
  rather than in documentation. It is not called an arrival time, because it is not one.

### 6.3 Where it is computed, and where it is stored

§3.2 is binding: joint draws exist only inside the solver. So dispersal follows `intervals.py`
exactly —

- computed during the run, from the same joint draws `estimate_durations` takes;
- requested by `bayesian_config.estimate_dispersal`, so it costs nothing when not asked for;
- stored as a **`dispersal` section of `Analysis.intervals`**, beside `age_depth`, with the section
  absent meaning *not asked for* rather than *nothing to report* — the convention the results page
  already reads for durations and age-depth, down to offering a re-run rather than showing an empty
  table. *[rev. 2026-09-14]* The first draft of this plan gave dispersal a column of its own;
  age-depth landed three days later as a section of the existing block, computed by
  `intervals.report(..., durations=False)` when a run wants joint draws for a reason other than
  durations. Dispersal is the third such reason, `wants_draws` in `gibbs.py` grows one more `or`,
  and item 6 needs no migration.

### 6.4 Model 1 — wave of advance (ships first)

For a site *i* with arrival age *t_i* (cal BP) at great-circle distance *d_i* from an origin:

    t_i  =  t₀ − d_i / v  +  ε_i

Fitted **per joint draw**: for each of the *J* draws, take that draw's arrival age for every site,
run an ordinary least-squares fit of *t* on *d*, and keep `v = −1/slope` and `t₀`. The result is
*J* values of *v* — a posterior for the front speed with the chronological uncertainty already
inside it, not bolted onto it afterwards.

Reported through `intervals.summarise`, so a speed in **km/yr** reads with the same median and
68/95 HPD grammar as every duration in the app. A negative or non-significant *v* is reported as
such; there is no absolute value taken and no sign flipped to make the answer look like a dispersal.

**The origin.** Two modes, in this order:

1. **Placed.** The user drops a pin. Honest, and usually what a hypothesis actually says.
2. **Inferred.** Profile the fit over a coarse grid of candidate origins (≈1° over the sites'
   bounding box plus a margin), keeping the residual sum of squares per candidate per draw. The
   output is a *surface*, rendered as a likelihood region on the map, not a point with false
   precision. Cheap: a few thousand candidates × a linear fit is milliseconds, and it is the mode
   that answers "where does this look like it started" without being told.

### 6.5 Model 2 — trend surface and isochrons

    t_i  =  a + b·x_i + c·y_i

fitted per draw in a local equal-area projection (Lambert azimuthal, centred on the sites'
centroid — the projection is chosen by the data, not by a global default that distorts high
latitudes). The gradient gives **speed** `1/√(b²+c²)` and **azimuth** `atan2` of the gradient;
aggregated over draws, the azimuth gets a circular mean and a circular 95% interval, because a
direction near due north cannot be averaged as a number.

Renders as **isochrons** on the map — the classic representation, and the one a reader of Ammerman
& Cavalli-Sforza will recognise. It assumes no origin, which makes it the right first look at a
dataset before any hypothesis is imposed on it.

### 6.6 Reading a fit: residuals are the finding

A site far off the fitted front is the spatial analogue of a low agreement index, and the app should
present it in the register readers already have for that: per-site **residual in years**, signed,
with early and late distinguished, sorted by magnitude. A single early outlier is where the
interesting archaeology usually is — a leapfrog, a maritime route, a redeposited sample, or a wrong
date — and a fit that reports only *v* and R² hides it.

Presented as a table beside the map, with the residual also encoded on each site's marker.

### 6.7 Model 3 — spatial sequencing as a *proposal*

Build a minimum spanning tree over the sites by great-circle distance, walk it from the origin, and
offer the resulting order as a **Sequence in the model tree, for the user to accept or reject**.

It is never applied automatically, and the UI says why in one line: **a spanning tree is spatial
adjacency, not chronology.** Asserting it as a Sequence asserts that everything in one site predates
everything in the next, which is a strong claim that the data may refute. The proposal's value is
that accepting it makes the claim *testable* — run the model, read the agreement indices, and the
sites that do not fit the spatial order announce themselves.

This is last in the order of work, and is the cheapest of the three to build.

### 6.8 What this estimator is, and what it is not

Stated in the plan because it must also be stated in the UI:

- Fitting per draw and pooling propagates uncertainty in **the ages**. It does not propagate
  uncertainty in the **origin** (mode 1), in the **coordinates**, or in the **assumption that these
  sites are the front** rather than the excavated subset of it.
- It is a **plug-in Monte Carlo estimator, not a hierarchical model.** A full treatment would model
  site arrival as drawn from a front with a site-level random effect and estimate the front and the
  scatter jointly. That is a genuine v2 and is named as such rather than implied by the output.
- Residuals are assumed independent. They are not — nearby sites share whatever the model is missing.
  Spatial autocorrelation in the residuals is **not** modelled, and the reported interval on *v* is
  therefore optimistic in exactly the way an OLS interval on autocorrelated data always is. This is
  disclosed next to the number, not in a footnote.
- Distances are great-circle. See §9.

---

## 7. Design brief — the surfaces

Mode: **Operate** throughout. Nothing here is persuasion; every screen exists so a researcher
completes a task and can defend the result afterwards.

### 7.1 The map: one component, no new dependency

`plotly.js-dist-min` 2.35.3 — already a dependency — contains `scattergeo`, `scattermap`,
`densitymapbox`, and the token-free `open-street-map` and `carto-positron` styles (verified in
`frontend/node_modules`). *[rev. 2026-09-14]* `scattermap` (MapLibre) rather than `scattermapbox`,
which 2.35 deprecates; both are in the bundle. One `SiteMap` component covers every surface here:

- **Default basemap is `scattergeo`**: coastlines and country outlines, **no network request, no
  tile server, no leak** (§4.5 rule 4), working in an air-gapped install. Adequate to regional
  scale, which is the scale every figure in Part C is drawn at.

  *[corrected 2026-09-14, op 2a]* This was written as "rendered from data inside the bundle",
  **and that is false**. `plotly.js-dist-min` ships the `scattergeo` renderer and none of the
  geometry: left alone it fetches `https://cdn.plot.ly/world_110m.json` at render time, and where
  that request cannot be made `Plotly.newPlot` **rejects** — the map does not degrade to a blank
  basemap, it does not appear. Verified in Chromium against the 2.35.3 bundle. The property above
  is therefore something the app must *do*, not something it gets: `world_110m.json` (278 KB,
  Natural Earth via Plotly, MIT) is served from `frontend/public/geo/` and `config.topojsonURL`
  points at it. See `public/geo/README.md` for provenance and how to refresh it.
- **Tiles are opt-in per project** (`scattermap`, `open-street-map`; `projects.map_tiles_enabled`), for the one job the offline
  map cannot do: dropping a pin at site precision. Enabled once, behind a notice naming what a tile
  request discloses.
- Consequence: every map inherits the app's existing plot discipline for free —
  `displayModeBar: false`, the app-drawn `Download PNG` at `PNG_EXPORT_SCALE` (3×, so a figure
  clears 300 dpi in a paper), `usePlotFont`, and every mark from `plotColors`.

**Leaflet + react-leaflet was the obvious alternative and is rejected**: it adds a dependency, it
cannot render without tiles (so it cannot be the private default), and a figure drawn in Leaflet
would be the one figure in the app outside the Plotly export path and outside the palette.

### 7.2 Entering coordinates

Three routes to the same two numbers, all of which write the same fields:

1. **Typed**, in the date table's inline form — a `lat, lon` column pair beside `depth_cm` and
   `location`, following `columnPreferences`' existing rule: the column appears on its own once any
   date has coordinates, and an explicit show/hide overrides that.
2. **Imported**, per §4.3.
3. **Dropped**, on the map: select dates → open the map → click → the pin lands on the whole
   selection. This is the bulk case and the common one, because dates from one site share a
   coordinate. It reuses `SampleSelectionBar`'s existing selection model rather than inventing a
   second one.

Pins are draggable while the map is in edit mode and read-only otherwise; a date with no coordinate
appears in an "unplaced" list beside the map, which is also the empty state.

### 7.3 The ΔR lookup, where §2.5 lands

A second tab in the ΔR popover that already exists in `SampleSelectionBar`. It shows, before
anything is applied:

- the derived **ΔR ± σ**, large;
- the **N control**, with the value **updating live as N changes** — §2.5 is the whole reason: at
  Callao the number moves 189 years across the range, and a user who never sees it move will not
  know their answer was a choice;
- **distance to the nearest point** (§2.6), stated in km, and flagged when it exceeds a threshold;
- the **point list** — map number, distance, ΔR ± σ, locality, **feeding habit**, reference — each
  row with a checkbox, so the archaeologist can exclude a point the way they would on calib.org's
  own page. Excluding re-derives the value immediately. Habit is a column, always shown, never a
  hidden facet: `deposit`, `migratory` and `carnivore` carry a quiet marker in the app's warning
  ink (not the diagram palette, which encodes model element types), `ambiguous` shows the raw
  truncated string it came from, and `suspension`, `unknown` and `grazer` are unmarked because
  §2.7 says they behave alike;
- **the counterfactual, always, as one line**: *"Suspension feeders only (7 of 10): −188 ± 61."*
  Clicking it applies that set. This is the whole recommendation of §5.4.1 in a single control — it
  answers the question instead of asking it, and it costs one extra `combine` over ten in-memory
  rows;
- **an inline warning when, and only when, that line differs by ≥35 yr** — the median reported ΔR
  error, and the point at which the choice stops being cosmetic. It names the classes responsible
  and how many points each contributes. Expected on ~11% of lookups, against the 61% a
  presence-triggered prompt would fire on;
- a **"suspension feeders only" toggle** as the one preset, with no others: §2.7 gives no evidence
  for excluding `unknown` or `grazer`, so offering it would be offering a choice the data does not
  support;
- when exclusion would leave fewer than 2 weighted points, the toggle is **disabled with the reason
  shown**, not silently absent;
- points with **no ΔR error** shown struck through with "no published uncertainty — excluded";
- the snapshot id and date, in small type, as the thing that makes the number citable.

Applying writes `delta_r`, `delta_r_error` and `delta_r_source` to every selected date, and the
existing "applied to N dates" confirmation says how many.

### 7.4 Dispersal results

A panel on the Results page, alongside `DurationsPanel` and `AgeDepthPanel`, following their
conventions exactly
(including "not asked for → offer a re-run" rather than an empty table):

- **The map is the headline**: sites as markers, marker colour = modelled arrival age on a
  sequential ramp, isochrons or the fitted front drawn over it, the origin marked, and — in inferred
  mode — the origin likelihood region.
- **The speed, in the app's own grammar**: median and 68/95 HPD, in km/yr, from
  `hpd_ranges_from_distribution`.
- **The residual table** (§6.6), sorted by magnitude, early and late distinguished.
- **The caveats from §6.8 in the panel**, not in the manual.

Colour: arrival age is a *quantity*, so it takes one hue at graded strength — the context-shade ramp
the Harris matrix already uses for graded quantities — never a categorical palette, and never a hue
the diagram palette has already spent on an element type. An early residual and a late residual are
the only categorical marks on the figure and they take the two directions of one axis, not two
unrelated colours.

### 7.5 Scope and anti-goals

- **Untouched**: the solvers, the calibration path, the curve registry, the model tree editor, the
  Harris matrix, every existing export column.
- **Anti-goal**: a GIS. No layers, no shapefiles, no digitising, no basemap styling controls. The
  map is an input device and a figure, and nothing else.
- **Anti-goal**: a second place where ΔR is decided. The lookup writes into the fields that already
  exist and are already applied by the existing code path.

---

## 8. Order of work

| # | Ships | Depends on | Rough effort |
|---|---|---|---|
| 1 | **Coordinates**: schema, validation, import aliases, template, export, precision setting (§4) | — | days |
| 2 | **`SiteMap` + placement**: offline basemap, pin-drop on selection, unplaced list (§7.1–7.2) | 1 | days |
| 3 | **ΔR mirror**: snapshot tables, ingest, bundled snapshot, hash verification (§5.2–5.3) | — | ~3 days |
| 4 | **ΔR lookup**: `reservoir.py`, `combine` validated against `regioncalc.php`, provenance, the popover tab (§5.4–5.5, §7.3) | 1, 3 | ~4 days |
| 5 | **Freshness probe** and the ingest button (§5.6) | 3 | hours |
| 6 | **Wave of advance**: site derivation from elements, per-draw fit, `intervals.dispersal`, panel, map, residuals (§6.2–6.4, §6.6, §7.4) | 1, 2 | ~2 weeks |
| 7 | **Trend surface / isochrons** (§6.5) | 6 | ~3 days |
| 8 | **Origin inference** surface (§6.4 mode 2) | 6 | ~3 days |
| 9 | **MST sequence proposal** (§6.7) | 6 | ~2 days |
| — | *Deferred*: regional kriging (§5.7); over-water distance (§9) | | |

Items 1–5 are a complete, shippable feature on their own: coordinates plus a citable ΔR. Item 6 is
where the new science starts and should not be started before 1–5 are done, because it consumes
their data model.

---

## 9. What stays refused

Named here so it is refused by name rather than approximated — the rule the OxCal importer already
follows:

- **Over-water / least-cost distance.** Every distance in this plan is great-circle. Two sites on
  opposite coasts of a peninsula are "near" and a maritime route around it is "far", both wrongly.
  Fixing it means a cost surface and a fast-marching solve, which is a plan of its own. Until then
  the app says it uses great-circle distance, in the panel, every time.
- **Time-varying or anisotropic speed.** One *v*, constant in time and direction.
- **Taphonomic and sampling bias.** The Boundary estimator (§6.2) corrects the *within-site* part of
  it. It does nothing about which sites were dug, or which got dated.
- **Spatial autocorrelation in residuals** (§6.8).
- **Coordinate reference systems other than WGS84 decimal degrees**, and DMS input (§4.3).
- **Geocoding a placename.** `location` stays a free-text placename; resolving it to coordinates
  would send a site name to a third party, which §4.5 rule 3 forbids.
- **Automatic ΔR refresh** (§5.6) and **live ΔR queries** (§4.5 rule 3).

---

## 10. Validation

**ΔR combination.** The three cases in §2.3 become unit tests with the expected integers hardcoded,
plus twenty random point sets checked against `regioncalc.php` in a manual test (in
`backend/tests/manual/`, since it needs the network) — reproducing calib.org's printed value
exactly, or the test fails.

**Ingest.** Two fetches of an unchanged table produce the same `content_sha256`. Row count matches
`select count(*) from details`. All 54 cp1252 rows (§2.2) decode to the expected strings, with a
fixture holding the raw bytes so the test does not need the network.

**Snapshot immutability.** A tampered snapshot file fails its hash check and refuses to serve —
mirroring the existing curve-registry test.

**Feeding-habit normalisation.** A fixture holding all 25 raw spellings found in the 2026-05-11
snapshot maps to the seven classes in §2.7's table, with the five truncated compounds landing in
`ambiguous` and nothing landing in `deposit` or `suspension` that was hedged in the source. An
unrecognised spelling in a future snapshot normalises to `unknown` **and is counted in the ingest
report**, so a new vocabulary is visible rather than absorbed. The counterfactual is checked against
a hand-computed case, and the "fewer than 2 points left" refusal has its own test.

**Coordinates.** Round-trip through CSV export → import. Half a coordinate is refused. DMS is
refused with the format named. `(0,0)` warns.

**Dispersal — the coverage test, which is the real one.** Simulate a front: place *n* sites at known
distances from a known origin, give each a true arrival at `t₀ − d/v`, draw dates around it, run
them through calibration and the modelled solver, and fit. Over ≥200 simulations the recovered 95%
interval on *v* must contain the true *v* about 95% of the time. Anything much below that means the
estimator is overconfident, and §6.8 predicts the direction it will fail in.

**Dispersal — a landmark.** The Neolithic wave of advance is the textbook case at roughly 1 km/yr
(Ammerman & Cavalli-Sforza 1971). A published dataset run through the feature should land in that
neighbourhood; a result an order of magnitude away is a bug, not a discovery.

---

## 11. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 11.1 | **Redistribution permission.** The bundled snapshot redistributes Reimer & Reimer's compilation. Their About page asks that original authors be cited and gives a contact. | medium | high — blocks the bundle | Email p.j.reimer@qub.ac.uk before shipping it. If declined, fall back to "no bundle, user runs the ingest" (§5.3 already supports it unchanged); only the shipped CSV goes away. |
| 11.2 | **calib.org changes or disappears.** Apache 2.4.61 / PHP 5.6.40, HTML scraping, no API, no versioning. | medium | low | The mirror is the mitigation. A broken ingest breaks *refresh*, never *analysis*. The parser must fail loudly on an unexpected table shape rather than importing garbage. *[2026-09-14: written and tested. `extract_records` checks the response's header row against `EXPECTED_COLUMNS` and refuses anything else, and the ingest cross-checks the page's declared row count against the rows parsed.]* |
| 11.3 | **A derived ΔR is trusted more than it deserves.** §2.4 says the floor is ~84 yr, §2.5 says the answer moves with N, §2.7 says it can move 465 yr with feeding habit. | **high** | high | The number never appears without its σ, its N, its nearest-point distance, its point list with habits, and the suspension-only counterfactual (§7.3). This is a UI obligation, not a documentation one. |
| 11.8 | **Alert fatigue defeats the habit warning.** A prompt that fires on 61% of lookups is dismissed reflexively, including on the 4% where it mattered. | high *if prompted* | high | §5.4.1: never prompt on presence. Show the counterfactual always, warn only above the settled **35 yr** threshold (~11% of lookups). Worth re-measuring against real usage once the feature has been in hand for a while. |
| 11.4 | **A dispersal fit that is confident and wrong.** OLS on autocorrelated residuals with an assumed origin will always return a tidy *v*. | **high** | high | §6.6 residuals, §6.8 disclosures in the panel, and the §10 coverage test run before the feature ships. Consider refusing to report *v* below a minimum site count (~6) rather than fitting 3 points and printing an interval. |
| 11.5 | **A coordinate leaks.** | low | severe and irreversible | §4.5: local-only lookup, offline default basemap, opt-in tiles, export precision control. |
| 11.6 | **Site derivation surprises the user** — a Phase holding dates from two sites (§6.2). | medium | medium | Refuse and name both coordinates and the distance between them, rather than using the centroid. |
| 11.7 | **The mixed-encoding rows regress** on a future ingest (§2.2). | low | low | Fixture test on the raw bytes; the ingest report counts fallbacks, so a jump is visible. *[2026-09-14: the fixture IS a capture — 85 rows of the source's own bytes, carrying all 60 fields that need a fallback. The count went 54 → 60 between two measurements eight days apart, which is the drift this row was written about.]* |

---

## 12. Fork notes

Written for a fork, so nothing here needs upstream's agreement. If it is ever merged back, three
files carry essentially all the conflict risk, and all three are additive here on purpose:

- **`backend/app/models.py`** — four columns plus two tables.
- **`backend/app/services/date_import.py`** — two `COLUMN_ALIASES` entries, two `TEMPLATE_COLUMNS`.
- **`backend/app/api/results.py`** — two `CSV_HEADER` entries.

Alembic revisions branch from the current head, `20260909_1200_wiggle_match_and_deposition_elements`
(`f3b81c60d5a7`) *[rev. 2026-09-14; was `20260826_2140_sample_sort_order`]*. Keep items 1 and 3 in
§8 to their own revisions, so the fork can take the coordinate schema without the ΔR mirror if
upstream ever wants only that. Item 6 no longer needs one (§6.3).

---

## 13. Open decisions

1. ~~**A `sites` table, or derived sites?**~~ **Decided 2026-09-14: derived**, as planned. The model
   tree and the date collection already express what a site is, and a `Site` entity would fork the
   schema much further for no gain feature 6 needs. A first-class entity would be tidier and would
   give a site one coordinate instead of *n* equal ones, and it would make §11.6 structurally
   impossible rather than caught by a threshold — that is the cost accepted here, and op 6a's
   descent rule is what pays it. Revisit if a second Part C model wants a site to carry anything
   of its own.
2. **Where does the ΔR snapshot live in the UI?** It behaves like a calibration curve (versioned,
   immutable, citable), which argues for the Curves page. It is used from the date collection, which
   argues for the project. Currently planned: managed on Curves, used from the collection.
3. ~~**Minimum site count for a dispersal fit.**~~ **Decided 2026-09-14: 6**, as
   `MIN_DISPERSAL_SITES`. The reasoning, now in the constant's own comment: OLS through three points
   returns a slope, a speed and an interval, all tidy and none worth anything, because with *n* − 2
   = 1 residual degree of freedom there is nothing left with which the data could contradict the
   line. Six is where the residual table starts being able to say *this is not a front* — which is
   the diagnostic §6.6 leans on, and the reason the number is a refusal rather than a warning.
4. ~~**Does dispersal belong to an analysis or to a project?**~~ **Decided 2026-09-14:
   per-analysis**, as planned — it consumes the run's joint draws (§6.3). A dispersal question
   usually spans several analyses of one region, which the current model still has no place for;
   that is the known cost. A project-level fit built from several analyses' stored boundaries would
   have to fall back to marginals (§3.2) and lose the joint structure between sites, which is
   precisely what makes this estimator better than regressing point estimates — so the trade was
   refused rather than accepted.
5. **Coordinate precision default.** Planned as `full`. An argument exists for defaulting to `0.01°`
   and making full precision the deliberate choice. *§14 op 1 ships `full`: an export that silently
   rounds is a surprise, and a project owner choosing to round is the deliberate act.*
6. **Per-species rather than per-class habit correction.** §2.7 could only measure the bias at class
   level, because that is all the paired data supports. A published per-species offset — *Austrovenus*
   against *Amphibola*, say — would let the app inflate a point's error by a defensible amount
   instead of offering only include-or-exclude (§5.4.1's rejected "bias inflation"). Needs a source;
   the database itself does not contain one.
7. ~~**The escalation threshold.**~~ **Decided 2026-09-06: 35 yr**, the median reported ΔR error,
   firing on ~11% of lookups. The 84 yr co-located nugget was the alternative; §5.4.1 records why it
   was not chosen.

---

## 14. Execution sequence

*Added 2026-09-14.* §8 is the order of features; this is the order of operations, each small enough
to be one commit with its own tests, each naming the files it touches and what "done" is. Branch:
the current one, from Alembic head `f3b81c60d5a7`. The two migrations are ops 1a and 3a; nothing
else changes the schema.

Operations within a feature run in order. Features 1–2 and 3 are independent and can interleave;
4 waits for both; 6 waits for 1–2 and for the three §13 decisions marked below.

*Features 1–6 are done (2026-09-15) apart from op 3d, which op 0 still gates. Alembic head is
`b7c4e0a13d92`. What op 0 still buys is the
BUNDLED snapshot — a fresh install with a working lookup and no network, and two installs agreeing
on what they compute; until then the lookup runs on a snapshot somebody ingested, which feature 5's
card is now the way to get. §13's three open decisions for feature 6 were settled on 2026-09-14,
all three as proposed. Features 7–9 are next and each waits only on 6d, which is in.*

### Op 0 — the permission request *(human; start now, longest lead time)*

Email p.j.reimer@qub.ac.uk asking to redistribute the Marine20 reservoir compilation as a bundled
snapshot, citing Reimer & Reimer 2001 and naming the app. Blocks **only op 3d** (the shipped CSV).
Every other operation proceeds; if the answer is no, op 3d is dropped and the ingest (op 3c) is the
whole story, unchanged. *The app cannot send this; a person does.*

### Feature 1 — coordinates (§4) — **done 2026-09-14**

Shipped in five commits rather than six: 1b and 1c share the bulk-update and deploy paths in
`api/project_samples.py` and could not be usefully split. Three things were done differently from
the text below, each for a reason found in the code:

- **1b lands on `ProjectSampleBulkUpdate`, not `ProjectSampleBulkInput`.** The line reference
  (`:285`) and feature 2b's "place on map writes the whole selection" both point at the
  apply-one-value-across-a-selection path; `ProjectSampleBulkInput` is a list of
  `ProjectSampleInput` and inherits the rule already. The both-or-neither check is a schema rule
  there and a **merged-row** rule on the single-date PATCH, where a request carrying only a
  longitude is legitimately correcting one half of a complete position.
- **1d also refuses a decimal comma.** `_number` strips thousands separators, so `5,6` would have
  been read as 56 — in range for a longitude, fifty degrees out, silent. Same failure class as DMS,
  and a European locale's Excel writes exactly that.
- **1e appends Latitude/Longitude to `CSV_HEADER` rather than filing them after `Curve`.** Every
  column before them has held its position since the export existed. Also pulled forward from 1f:
  `coordinate_precision` had to become *settable* (`schemas/project.py`'s Literal, which is what
  keeps it to the four values the exporter can apply) for 1e's guarantee to mean anything.

1f put the precision selector in the existing project dialog, retitled *Project Settings*, rather
than adding a second dialog beside it.

**1a. Schema.** `backend/app/models.py`: `latitude`, `longitude` (Float, nullable) on
`ProjectSample` and `RadiocarbonSample`, beside `delta_r`/`delta_r_error` and in their voice;
`coordinate_precision` (String, `'full'`) and `map_tiles_enabled` (Boolean, false) on `Project`.
One revision `20260914_HHMM_coordinates_on_samples` from `f3b81c60d5a7`; `tests/test_migrations.py`
learns the new columns. *Done when* the migration test passes up and down and every existing test
still passes untouched.

**1b. Validation.** `backend/app/schemas/sample.py`: the two `Field`s on `MeasurementInput`,
`_coordinates_come_in_pairs` beside `_delta_r_has_an_uncertainty` (`:157`), the same on
`ProjectSampleBulkInput` (`:285`) across a selection. `tests/test_validation.py`: half a coordinate
refused, both accepted, out-of-range refused.

**1c. Deploy copy.** `backend/app/api/project_samples.py:450` copies the two fields alongside
`delta_r`. `tests/test_project_samples.py`: a deployed date carries its coordinates.

**1d. Import.** `backend/app/services/date_import.py`: two `COLUMN_ALIASES` entries (`:146`), two
`TEMPLATE_COLUMNS` (`:726`), the template's `NZ-7756` row given real coordinates, a DMS detector
that fails the file naming the cell and the format wanted, and the `(0, 0)` warning through the
existing `result.warnings`. `tests/test_imports.py`: aliases resolve in any order, DMS in each of
the four spellings refused, `(0,0)` warns, template round-trips.

**1e. Export.** `backend/app/api/results.py`: `CSV_HEADER` (`:321`), the JSON export, and the
PDF's date table (`services/pdf_report.py`), all through one `_rounded_coordinate(value,
precision)` applied at serialisation, round-half-to-even. `tests/test_exports.py`: export → import
round-trip at `full`; `0.01°` rounds identically on two exports; nothing below the precision leaks
into any of the three downloads.

**1f. Frontend.** `ProjectSampleTable.tsx`: `latitude`/`longitude` columns beside `depth_cm`,
governed by `utils/columnPreferences.ts`'s existing "appears once any date has it" rule;
`MeasurementFields.tsx` inline form; `ProjectDetail.tsx` gains a small project-settings control
(there is none today) holding the precision selector, its four values named in km, and later 2c's
tiles switch. `ProjectSampleTable.test.ts`,
`columnPreferences.test.ts` extended. *Done when* a coordinate typed in the table survives export,
edit-in-spreadsheet, and re-import.

### Feature 2 — the map (§7.1–7.2) — **done 2026-09-14**

Three commits, one per op. Four things were done differently from the text below:

- **The offline basemap needed a vendored topojson.** §7.1's "no network request" was not a
  property of `scattergeo`; it is now a property of this app. See the correction in §7.1.
- **Clicks are taken in the capture phase.** `plotly_click` on a geo subplot fires only when a
  MARK is hit, so the public API cannot answer where an empty-map click landed, and Plotly's own
  drag rect stops a bubble-phase handler over the whole drawn map. Built the obvious way this
  feature would have looked finished and worked only in the letterbox margin. The internals it
  reads instead (`_fullLayout.geo._subplot.projection`, and MapLibre's `unproject` for tiles) are
  behind guarded accessors that return null rather than throw, so a Plotly upgrade costs the
  click and not the screen.
- **Pins are not individually draggable** (§7.2 asked for it). A pin is a position, every date at
  it shares that position, and the editing unit is the selection — so dragging one would have to
  rewrite dates that may not be selected. Clicking again moves the selection's pin: the same
  gesture, with a scope that can be stated.
- **Placement is a dialog**, breaking `SampleSelectionBar`'s rule that a bulk edit keeps the
  selection on screen. A map needs the space, and the other sites it shows are worth more than
  the rows it covers.

Also: `parsePastedDates` moved to `utils/pastedDates.ts`, because `ProjectSampleTable` now reaches
Plotly through the map dialog and its test died on the import.

**2a. `SiteMap.tsx`.** One component on `scattergeo`, `displayModeBar: false`, `usePlotFont`,
`plotColors`, the app-drawn Download PNG at `PNG_EXPORT_SCALE` via `utils/plotExport.ts`. Props:
points, selection, `editable`, `onPlace(lat, lon)`. No tiles yet.

**2b. Placement.** `SampleSelectionBar.tsx` gains "Place on map": opens `SiteMap` in edit mode, a
click writes the same `{latitude, longitude}` to the whole selection through the bulk-update path
1b validated; draggable pins re-write. The unplaced list beside the map is the empty state.

**2c. Tiles, opt-in.** `scattermap` + `open-street-map` behind `project.map_tiles_enabled`, set
once per project behind the notice naming what a tile request discloses (§4.5 rule 4). *Done when*
the network tab shows no request until the switch is on.

### Feature 3 — the ΔR mirror (§5.2–5.3) — **3a–3c done; 3d still gated on op 0**

*2026-09-14.* Built in two passes. The first assumed calib.org was unreachable — https to it is
blocked — and left the fetch unwritten behind a documented seam. **Plain http reaches it**, so the
second pass captured the real response and finished the job.

What the capture changed:

- `fetch_records` and `extract_records` are **written against the real interface**:
  `POST http://calib.org/marine/query/query.php` with `query=SOURCE_QUERY`, which answered 200 with
  2060 records. One session GET and one query, as §5.3 requires.
- **The fixture is real** — 28 KB of the source's own bytes, 85 rows chosen to carry all 60 cp1252
  fields, four correct-UTF-8 rows, all 26 feeding spellings and three rows with no uncertainty.
  The whole 1.0 MB response is not committed; nothing in the rest of it is exercised by a test.
- **Two of §2's numbers were stale** and are corrected in place: 60 fields fail strict UTF-8 rather
  than 54, and there are 26 feeding spellings rather than 25.
- **§2.7's fold is confirmed.** `services/reservoir.py`'s table, written from the prose before the
  source was reachable, reproduces all seven class counts exactly across all 2060 rows with nothing
  unrecognised.

Additions the plan did not call for, each forced by the code: `in_use()` matches the
`delta_r_source` JSON as text, because SQLite and PostgreSQL disagree about JSON operators and a
check that silently returned zero on one of them would let a cited snapshot be deleted; the
snapshot list reports `has_bundled`, because until op 0 is answered a fresh install has no lookup
at all; the ingest cross-checks the page's own `N records` line against the rows parsed, since a
mirror quietly missing forty measurements gives quietly wrong averages; and `requests` is now a
declared dependency rather than a transitive one.

**3a. Schema.** `MarineReservoirSnapshot` (with `created_by`, §5.2 rev.) and `MarineReservoirPoint`
in `models.py`; `delta_r_source` (JSON, nullable) on both sample tables, since it is this feature's
provenance. Revision `20260914_HHMM_marine_reservoir_snapshots`, chained after 1a.

**3b. Ingest and normalisation.** `backend/app/services/reservoir_ingest.py`: session `GET`, the
§5.2 join query, per-field UTF-8→cp1252 decode (§2.2), parse, `content_sha256` over normalised
rows, insert, `IngestReport` (rows, `max(last_changed)`, no-error count, cp1252-fallback count,
unrecognised feeding spellings, hash). `FEEDING_CLASSES` normalisation table from §2.7 in
`services/reservoir.py`, raw string kept beside the class. Fixtures in `tests/fixtures/calib_marine/`:
the raw bytes of an HTML response containing all 54 cp1252 rows and all 25 feeding spellings — no
network in the test suite. `tests/test_reservoir_ingest.py`: the 54 decode to the expected
strings; 25 spellings → 7 classes with the five truncated compounds in `ambiguous`; a novel
spelling → `unknown` *and counted*; an unexpected table shape fails loudly; two parses of one
fixture hash the same.

**3c. Loader and API.** `services/reservoir_registry.py` on the `curve_registry.py` pattern:
builtin-from-file and stored-from-row behind one interface, hash verified on every load, a
mismatch refusing to serve. `backend/app/api/reservoir.py`: `POST /snapshots` (auth, runs 3b,
returns the report), `GET /snapshots` (builtin plus the caller's own), snapshot deletion refused
while any `delta_r_source` names it. `tests/test_reservoir_registry.py`: tampered file refuses,
private snapshot invisible to another account (mirror `test_curve_privacy.py`).

**3d. Bundled snapshot** *(gated on op 0)*: `calibration_data/marine_reservoir_2026-05-11.csv`,
193 KB, produced by 3b's parser and committed with its hash recorded in the registry.

### Feature 4 — the lookup (§5.4–5.5, §7.3) — **done 2026-09-14**

All four ops. The arithmetic is confirmed twice over: the three §2.3 cases are asserted to the
printed integer, and `tests/manual/calib_regioncalc.py` was run once against calib.org's own
calculator — **20/20 exact on both integers**, over sets whose uncertainties span 22 to 447 years.
4d's acceptance figure is now an assertion rather than a note: **Strangford Lough at N=5 reads
−209 ± 44**, read off the captured database through extract, decode, normalise, nearest and
combine.

Two things the code decided that the plan did not:

- **`FAR_NEAREST_KM = 100`**, the distance at which the panel flags how far the nearest point is.
  §2.6 required the distance to be shown and said Tampa's is 163 km, but named no threshold. 100 km
  is about where a local offset stops being local; it is one named constant with its reasoning
  beside it, like `FEEDING_SHIFT_WARN_YEARS`.
- **A selection spanning two positions is refused, not averaged.** §7.3 assumed the selection has a
  position; it does not say what to do when the ticked dates disagree about it. A centroid of two
  sites is a place neither of them is, so the panel says so and asks for one site at a time.

**A bug in op 3b, surfaced by running the ingest against all 2060 rows for the first time** (op 4a
needed them): three rows carry a longitude outside ±180 — the Chatham Islands at 183.5, Fiji at
−181 — and the validation refused them, rejecting the whole fetch over three good rows. Longitude
is modular and 183.5 has exactly one reading, so it is now wrapped and counted; latitude has no
such identity and 91 is still refused.

**4a. `services/reservoir.py`.** `nearest(lat, lon, n, snapshot_id, *, max_km)` by haversine
(R = 6371.0088); `combine(points)` implementing §2.3 exactly; `lookup(...)` returning
`included_all`, `suspension_equivalent`, per-point classification, `nearest_km`, `farthest_km`,
`excluded_no_error`, and the refusal when exclusion leaves < 2 weighted points.
`FEEDING_SHIFT_WARN_YEARS = 35` defined beside a comment carrying the §2.7 numbers.
`tests/test_reservoir.py`: the three §2.3 cases to the printed integer; a hand-computed
counterfactual; the < 2 refusal naming the classes; `unknown`/`grazer` never excluded; a no-error
point excluded and listed.

**4b. Manual validation.** `tests/manual/calib_regioncalc.py` beside `oxcal_reference.py`: twenty
random point sets against `regioncalc.php`, exact integer match or fail. Run once before 4d ships
and recorded in its docstring.

**4c. API and provenance.** `POST /api/reservoir/lookup` (auth) returns 4a's result for
`{lat, lon, n, snapshot_id, exclude_map_nos, suspension_only, max_km}`. The bulk update in
`api/project_samples.py` accepts `delta_r_source` and — the rule from §5.5 — **clears it whenever
`delta_r` or `delta_r_error` arrives without one**. `tests/test_project_samples.py`: hand edit
clears provenance; a lookup-applied value carries it; deploy copies it.

**4d. The popover tab.** `SampleSelectionBar.tsx`'s ΔR popover (`:251`) gains the second tab laid
out in §7.3: value large, N live, nearest-km, the point list with the habit column and checkboxes,
the counterfactual line, the ≥ 35 yr warning, the one preset, the snapshot id. Applying writes the
three fields to the selection through 4c. *Done when* Strangford Lough at N=5 reads −209 ± 44 in
the UI and the stored `delta_r_source` reproduces it.

### Feature 5 — freshness (§5.6) — **done 2026-09-14**

Two things were done differently from the text below:

- **The line lives in its own card, not in the curve list.** `ReservoirSnapshotCard.tsx`, mounted
  under the curves table. A snapshot is the same *kind* of thing as a calibration curve — versioned
  reference data an already-published result depends on, immutable once stored, citable by id — but
  it is not one, and only marine dates ever touch it. Weaving it into the list would have made it
  read as a curve. It carries three states the list has no room for: nothing mirrored, mirrored and
  current, mirrored and behind.
- **The probe is a second query, not a parse of the big one.** `FRESHNESS_QUERY` is `select
  count(*), max(last_changed) from details` against the same Generalized Query endpoint, and
  `extract_freshness` reads the single aggregate row. It costs 2.2 KB measured against calib.org,
  versus about a megabyte for the records. `_query` is now shared by `fetch_records` and
  `fetch_freshness`; that was the whole of the backend change beyond the route, since the ingest
  the button calls is op 3c's `POST /reservoir/snapshots`, unchanged.

Nothing in the card reaches calib.org on mount. Opening the page is one local request for the
snapshot list; asking calib.org what it holds is a button, and re-ingesting is a second button.
The reason is not politeness about somebody's server: a ΔR that changed because a remote database
changed, underneath an analysis already published, is exactly the failure a frozen snapshot exists
to prevent, so refreshing has to be a decision a person takes at a moment they chose.

**5a.** `GET /api/reservoir/freshness` (auth, explicit, the 2 KB probe) and one line plus the
ingest button on `Curves.tsx`, in the register the curve list already uses.

### Feature 6 — wave of advance (§6.2–6.4, §6.6, §7.4) — **done 2026-09-15**

*Confirmed 2026-09-14, all three as proposed:* §13.1 stays derived; §13.3 `MIN_DISPERSAL_SITES = 6`;
§13.4 stays per-analysis.

Three things in 6a were settled in the code rather than by the text above:

- **"A site per opening Boundary" needed a descent rule.** Taken literally it gives a Sequence of
  three phases at one place three sites, tripling that place's weight in the regression while adding
  no information. `derive_sites` instead walks outermost-first and descends only where a candidate's
  dates scatter beyond `SITE_SCATTER_KM` — so a region modelled as one Sequence of site-Sequences
  splits into its sites, and a site modelled as a Sequence of Phases does not. The threshold
  therefore does **two** jobs: higher up it separates places, and at the bottom of the descent,
  where there is no structure left to split, it reports §11.6's modelling error. That is why the
  refusal reads the way it does only in the second position.
- **A wrapper is not a site.** A root Sequence containing one site Sequence is coherent at the root,
  but the root's opening Boundary is the start of the *model* — older than the site's own, and a
  different parameter, so dating the site by it reports an arrival earlier than the model says.
  The walk descends past a single covering child *when that child opens on a different Boundary*.
  The second condition is load-bearing: a named Sequence holding one anonymous Phase shares its
  opening Boundary, so they are the same arrival, and the element the analyst named is the one to
  report.
- **`intervals.summarise` could not report a speed as written.** Its bin width floors at five
  *years* (`_reporting_step`), and a front whose whole posterior spans a quarter of a km/yr came
  back in a single bin with an HPD of nonsense. `summarise` and `_step_for` gained a `base_step`
  override, used by nothing but the speed; every quantity actually measured in years is untouched
  and they all stay on one grid. `_band` also became public as `calendar_band`, because *t₀* is a
  calendar age in exactly its sense and reporting it in the duration grammar would tell the UI not
  to format it as a date.

Also in 6a rather than later: `derive_sites_from_dates`, §6.2's group-calibration fallback. It
takes each place's earliest dated event — decided once on the draws' median, not per draw, since a
per-draw minimum is biased low and would let the representative date change between draws — and
labels it `basis='earliest_dated_event'` so no panel can show it as an arrival time.

**Measured coverage (2026-09-15).** The exact path: **94.5% of 200 simulated fronts** had the true
speed inside their 95% interval, against a nominal 95% and a band of 90.4–99.6%. The sampled path:
**92.5% of 40 fronts**, which is one front either side of nominal at that count. So the interval
this feature reports means approximately what it says — which is the claim §11.4 doubted, and the
only way to have settled it. Re-run with `RADICAL_COVERAGE_FRONTS=200
RADICAL_COVERAGE_SAMPLED_FRONTS=40`; about 13 minutes across four cores.

Three notes on 6c, which is the operation that changed most in the writing:

- **It runs in the ordinary suite, not behind a deselection.** The plan said "marked slow", and it
  is — `pytest.ini` registers the marker — but the marker deselects nothing. A coverage test nobody
  runs is a coverage test nobody can rely on, and this is the *only* check that can retire §11.4.
  Size is tuned by `RADICAL_COVERAGE_FRONTS` instead: 32 by default (about a minute across four
  cores) and 200 for the recorded figure.
- **The tolerance is binomial, not a typed-in number.** Coverage is a proportion, so the band is
  three standard errors either side of 0.95 and scales with the number of fronts — ±4.6 points at
  200, ±11.6 at 32. Seeds are fixed, so a failure is a real change in the pipeline and never a
  flake, and the band is against *simulation* noise rather than run-to-run noise.
- **The exact solver carries it, not `solve_chains`.** An eight-phase chain model is exactly
  solvable, so `auto` integrates it — the path the test should exercise is the path production
  takes. The sampled path gets its own smaller run (12 fronts by default) checked for gross
  miscalibration rather than to the tight band, because a Gibbs front costs several times an exact
  one and `test_intervals.py` already cross-checks the two solvers against each other.

Two guards sit beside the coverage figure, because a coverage test can pass either because the
pipeline is calibrated or because the simulation is too easy to fail: one requires the interval to
*exclude* a speed twice the truth (an interval wide enough to contain anything would score 100%),
and one requires each site's opening Boundary to sit older than its own oldest date — the §6.2
premise the whole estimator rests on, and if the simulation ever stopped satisfying it the naive
estimator would work too and the file would be testing nothing anybody disputes.

On 6d: the `SiteMap` gained `values`/`valueLabel`, `front` and `origin` rather than growing a
second map component. The ramp is `CONTEXT_PALETTE` as a Plotly colorscale — one hue at graded
strength, §7.4's rule and the Harris matrix's — and it *supersedes* the selection colouring while
set, since two colour meanings on one figure is one too many. The front is drawn first so it lies
under the marks it was fitted to, and the origin last so it is never hidden. The panel's arithmetic
lives in `utils/dispersalFigure.ts` (`arrivalAges`, `frontLine`, `speedText`) for the reason
`siteMapGeometry.ts` is separate from `SiteMap`: it has right and wrong answers and should be
testable without rendering. `arrivalAges` *reconstructs* each site's age from what the fit reported
— t₀, the speed, the site's distance and its residual — rather than recomputing it from
coordinates, so the ramp on the map and the residual table are the same two numbers and cannot
drift apart. The panel is mounted with tiles **off**: `Results.tsx` has no project loaded, and the
offline basemap is the private default. Passing a project's tile setting through is the one thing
left if that is ever wanted.

Two notes on 6b:

- **The flag is an origin, not a boolean, everywhere below the schema.** `intervals.report` and
  both solvers take `dispersal_origin`, because a front speed is meaningless without the point it
  was measured from and a pair of parameters that can disagree is a pair that eventually will.
  `BayesianConfigInput` keeps both — `estimate_dispersal` is what the analyst toggles — and a
  `model_validator` refuses the combination that has no meaning. `api/analysis._dispersal_origin`
  collapses the two back into one for the solvers, and returns None when the flag is off *even
  where an origin is still stored*: a rerun reads the saved config, and an origin left behind by a
  fit the analyst switched off is not a request.
- **`measurement_dicts` gained `latitude`/`longitude`.** Without them `derive_sites` saw no
  coordinates on a real run and every fit refused for having nothing to place — the one wiring
  failure that unit tests against the solvers could not have caught, so it has an API-level test
  that asserts six sites came back from a saved analysis.

`derive_sites_from_dates` is reachable through `report_dates(..., dispersal_origin=...)` but the
group-calibration request does not offer it yet; that is a schema addition on
`GroupCalibrationRequest` and not in feature 6's path.

**6a. `services/dispersal.py`.** `derive_sites(root, measurements)`: a site per opening Boundary,
centroid of its dates' coordinates, refusal above `SITE_SCATTER_KM = 5` naming both coordinates and
the distance; the earliest-dated-event fallback for a group calibration, labelled as such in the
returned spec. `fit_wave(sites, draws, origin)`: per-draw OLS of *t* on great-circle *d*, `v =
−1/slope`, no sign flip; `summarise` on *v* and on *t₀*; signed per-site residuals in years, sorted;
refusal below `MIN_DISPERSAL_SITES` with the §11.4 reason. `tests/test_dispersal.py`: a synthetic
front recovers *v* and *t₀*; a two-site Phase 6 km apart refuses; five sites refuse; a negative
slope reports negative.

**6b. Wiring.** `BayesianConfigInput` (`schemas/sample.py:418`): `estimate_dispersal: bool = False`,
`dispersal_origin: Optional[{lat, lon}]`. `intervals.report(..., dispersal=spec)` adds
`block['dispersal']`; `wants_draws` in `gibbs.py:2215` and the `estimate_durations` branch in
`exact_chain.py:1289` grow the third reason; `api/analysis.py:506` threads the flag.
`tests/test_intervals.py` and `test_solver_reporting.py`: the section is present only when asked,
on both solvers, through `solve_chains`.

**6c. Coverage test.** `tests/test_dispersal_coverage.py`, marked slow: ≥ 200 simulated fronts
through calibration and `solve_chains`, the 95% interval on *v* containing the truth ~95% of the
time. Its measured coverage is recorded in the feature's Done entry in `REMAINING_WORK.md` when it
runs, as the age-depth and wiggle-match entries record theirs.

**6d. `DispersalPanel.tsx`.** Beside `DurationsPanel`/`AgeDepthPanel` on `Results.tsx`, reading
`analysis.intervals.dispersal` the way `:1266` reads `age_depth`: `SiteMap` headline with the
sequential arrival ramp, the front, the origin; the speed block; the residual table; the §6.8
caveats in the panel. Origin placement reuses 2b's pin. *Done when* a Neolithic-scale synthetic
lands near 1 km/yr in the UI (§10 landmark).

### Features 7–9 — **7 done 2026-09-15**

**7.** `fit_trend(sites, draws)` in `dispersal.py`: Lambert azimuthal at the centroid, per-draw
plane fit, speed and azimuth with circular mean/interval; isochrons on `SiteMap`. **8.** Origin
profile over a ~1° grid, rendered as a likelihood region. **9.** MST walk → proposed Sequence in
`ModelTreeEditor.tsx`, accept/reject, never applied. Each waits on 6d and is one commit.

Four things in 7 that the text above did not settle:

- **A rank check is not enough to catch a transect.** A line of sites along a MERIDIAN is exactly
  rank-deficient and would be caught; a line along a PARALLEL is not, because a parallel is a curve
  in this projection — so the design matrix has full rank and the cross-gradient is estimated from
  the curvature of the map rather than from anything anybody excavated. It looks like an answer.
  `MIN_TREND_ASPECT = 0.05` refuses on the SHAPE of the layout instead, measured by SVD: a 480 km
  transect along a parallel at 50°N scores 0.013 and a deliberately squat 2×6 grid scores 0.29, so
  the threshold sits in a wide empty gap and is not a judgement call. **This was found by a test I
  wrote expecting it to pass.**
- **The origin became optional, and `estimate_dispersal` alone now means the trend.** §6.5 says the
  trend surface assumes no origin, and that this is what makes it the right first look at a
  dataset — so op 6b's rule that a dispersal request needs a pin was wrong as soon as feature 7
  existed. The schema validator is gone; `_dispersal_spec` returns `{'origin': None}` for the flag
  alone, and `intervals.report` takes a `dispersal` spec rather than an origin. An origin buys the
  wave IN ADDITION, and both models are always computed when either is: they answer different
  questions off the same draws, and a tidy wave with a trend pointing elsewhere means the ORIGIN is
  wrong, which is a finding neither makes alone. `Analysis.intervals.dispersal` is therefore
  `{wave, trend}` rather than one fit.
- **The trend is rendered FIRST, above the wave.** It is the look at the data with no hypothesis in
  it, and a reader who meets the wave first has already accepted somebody's origin before seeing
  whether the dates point that way at all.
- **Isochrons are labelled at their midpoint, not at an end.** They are drawn past the sites on
  purpose, so the ends are the part of the figure most likely to be clipped — which is what the
  first browser render showed, with "6100" cut off at the frame edge.

Verified in Chromium on a real solve of a north-east front: trend **0.681 km/yr at 54° (NE), 95%
49°–61°, R = 0.999**, against a wave of **0.688 km/yr** from a placed origin — the two models
agreeing to within 1%, which is the cross-check having both on the page buys. Isochrons run NW–SE,
perpendicular to the advance. Still zero external requests.

### What this sequence does not schedule

Regional kriging (§5.7) and over-water distance (§9) stay deferred. §13.2 (snapshot on the Curves
page, used from the collection) is taken as written and needs no decision before op 5a.
