---
name: RadiCal
description: A calibrated instrument for radiocarbon dating and OxCal-style Bayesian chronological modelling.
colors:
  calibration-blue: "#3c70b8"
  burnt-coral: "#e14a56"
  aged-paper: "#faf7f4"
  paper-white: "#ffffff"
  warm-ink: "#2b2320"
  boundary-coral: "#f15d5f"
  boundary-coral-ink: "#d23f4a"
  phase-green: "#1f7a62"
  hiatus-violet: "#7a5c99"
  muted-surface: "#f6f0eb"
  context-shade-lightest: "#35826e"
  context-shade-darkest: "#04372a"
  state-error: "#a3231d"
  state-warning: "#a85c11"
  state-success: "#2e7d32"
typography:
  headline:
    fontFamily: "Roboto, sans-serif"
    fontSize: "2.5rem"
    fontWeight: 600
    lineHeight: 1.167
  title:
    fontFamily: "Roboto, sans-serif"
    fontSize: "2rem"
    fontWeight: 600
    lineHeight: 1.2
  body:
    fontFamily: "Roboto, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  base: "8px"
  cell: "2px"
spacing:
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.calibration-blue}"
    textColor: "{colors.paper-white}"
    rounded: "{rounded.base}"
    padding: "6px 16px"
  button-primary-hover:
    backgroundColor: "{colors.calibration-blue}"
  chip-status-positive:
    backgroundColor: "transparent"
    textColor: "#2e7d32"
    rounded: "16px"
  chip-neutral:
    backgroundColor: "transparent"
    textColor: "{colors.warm-ink}"
    rounded: "16px"
  table-header-cell:
    backgroundColor: "{colors.muted-surface}"
    textColor: "{colors.warm-ink}"
    padding: "6px 16px"
  matrix-cell:
    backgroundColor: "{colors.warm-ink}"
    rounded: "{rounded.cell}"
    size: "28px"
---

# Design System: RadiCal

## Overview

**Creative North Star: "The Calibrated Instrument"**

RadiCal reads like a well-made scientific instrument, not a generic SaaS dashboard: every color
carries a specific, load-bearing meaning, and nothing decorative is allowed to compete with the
data it is displaying. The palette was pulled directly from the app's own logo rather than left at
MUI's stock defaults — the theme file states outright that the original `#1976d2` primary /
`#dc004e` secondary were "verbatim restating" Material's out-of-the-box blue and pink, and replaces
them with colors that belong to this app specifically. Surfaces sit on a warm paper background
instead of clinical white, and every MUI shadow in the app is recolored from pure black to a warm
ink so elevation reads as lifted paper rather than a cold UI-kit default.

Confirmed anti-reference: MUI's un-customized default palette and its stock black shadow scale.
Both were the literal starting point and were deliberately replaced.

The instrument metaphor is most exact in the diagramming and plotting layer: a Boundary is coral
everywhere it is drawn (the model-tree editor, the Harris matrix, the posterior plot), a Phase is
always the same green, and paired quantities that are one thing at two strengths — the two nested
HPD bands, mean and median — are rendered as one hue at two opacities rather than as two unrelated
colors. `success`/`warning` are kept for genuine semantic state (a solved solver, a real warning on
the Results page) precisely because they used to be borrowed for Phase/Boundary and made the model
editor read as a status display.

**Key Characteristics:**
- paper-and-ink surface language instead of cold neutral grey or pure white/black.
- A small, deliberately restrained accent set: interactive chrome runs almost entirely on one
  primary blue; the brand coral is used sparingly, not splashed across every button.
- Color as encoding, not decoration — in the diagram and plot layer especially, a given hue is a
  claim about what something *is*, so it is never reused for something else.
- MUI's structural defaults (shape, elevation levels, component APIs) are kept; the craft is in
  which values they're fed, not in replacing the framework's behavior.

## Colors

A small, purposeful palette: one interactive blue does almost all of the work, a warm paper-and-ink
pair carries surfaces and depth, and a separate small set of hues is reserved entirely for encoding
meaning in the model-tree diagrams and posterior plots.

### Primary
- **Slate Blue** (`#3c70b8`): the one color interactive chrome runs on — primary buttons, filled
  chips, links, focus states, the "Sequence" element in the model tree, and the posterior line in
  every plot. Saturated up from the logo's pale tint (`#f0f0fc`), which is a wash, not a usable
  interactive color on its own, then darkened from `#4a7dc4` for AA: that value measured 4.18:1 on
  white and 3.89:1 on the canvas — under 4.5:1 in *both* directions, so every AppBar link (white on
  blue) and every text button (blue on paper) failed on one token. Same hue and saturation, one step
  down in lightness: 5.00:1 on white, 4.65:1 on the canvas.

### Secondary
- **Burnt Coral** (`#e14a56`): darkened from the logo's raw coral (`#f05460`). It measures
  **3.95:1** against white — enough for white-on-coral *large* text, not for body text; an earlier
  version of this document claimed body-text AA for it, which the value does not support. It now has
  **no use site at all**: the one it had was the date collection's "Deploy to New Analysis" button,
  a 13px white label on the coral at 3.95:1 — an AA failure this document had already predicted —
  and the loudest, reddest mark on a bar whose Delete button is drawn in plain ink, so the two read
  the wrong way round. Deploy is a constructive action and now takes the primary, like every other
  one in the app. The token stays in the palette as the brand's own colour and has to be darkened
  before it is spent on anything carrying body text.

### Neutral
- **Aged Paper** (`#faf7f4`): the app's canvas (`background.default`) — warm, at the same
  luminance as the cool `#f5f8fb` it replaced (0.934 against 0.935), so an elevated white Card lifts
  off it by exactly as much as before. Lift is a luminance difference; hue was never doing that
  work. What the cool tint *was* doing was inverting the app's temperature halfway up the stack —
  cool canvas, white card, warm band inside it — in an app whose ink, shadows and text are all warm.
  One family now runs the whole way, and the three surfaces are told apart by lightness, which is
  what was separating them in the first place. Defined in `theme/surfaces.ts` alongside Paper White
  and Muted Surface, because two of the three are also needed outside the `sx` prop (inside a
  `linear-gradient()` string, or a plain style object) where a palette token does not resolve; those
  sites used to hold their own copies of the hex values.
- **Paper White** (`#ffffff`): elevated surface color (`background.paper`) — Cards, Dialogs, Menus,
  and the plot area itself. Kept a known, uncolored white specifically so every translucent fill
  drawn on top of it (HPD bands, prior/posterior fills) composites against the same base rather than
  drifting muddier depending on what's behind it.
- **Muted Surface** (`#f6f0eb`, `background.muted`): a banded surface *inside* an elevated white
  one — table header rows, the stat strip under a chart. Warmer and a step darker than the canvas so it reads as
  a band on paper rather than a hole in it.
- **Warm Ink** (`#2b2320`): the app's one neutral. It carries two jobs. First, **every line and
  wash of UI chrome**: `divider`, `action.hover`, `action.active`, `action.disabled`. These were
  still pure black long after the shadows and text were warmed, and between them they cover more
  painted area than the shadows do — `divider` alone reaches every `TableCell`, every
  `DialogContent dividers`, every Accordion border. The substitution costs nothing:
  `rgba(0,0,0,0.12)` composites to `#e0e0e0` on white, warm ink at `0.14` to `#e1e0e0`, the same
  1.32:1. The alphas are *not* MUI's own numbers, because warm ink is lighter than black and copying
  the number would quietly cost legibility — each is the alpha that matches the **contrast** of the
  value it replaces (`action.active` at 0.64 measures 4.75:1 against black-at-0.54's 4.61:1).
  Second, it is what the app draws with when a mark is *about* the data rather than a category of it — every recolored MUI shadow (`rgba(43, 35, 32, *)`
  in place of pure black), the mean/median point-estimate marks on posterior plots, and the ordering
  matrix, where overlap measures what the model could not resolve. Exported from `diagramColors.ts`
  so the plots and the matrix cannot drift to two different inks.

### Semantic state
`success`/`warning`/`error`/`info` were the last un-replaced Material defaults in the app, and two
of the three collided with hues RadiCal had already spent.

- **`info` is the primary blue** (`#3c70b8`), not a colour of its own. MUI's stock `#0288d1` sat
  **14° from the app's primary** — the same near-miss-blue failure this document names `#1976d2`
  for — and it was repeated on sixteen Alerts including `JobProgress`. No blue is both recognisably
  blue *and* far enough from the primary to be a different claim; the nearest usable candidate was
  still only 22° off. So `info` stops trying to be a second blue. An info alert is the app speaking
  about its own state, and reading as app chrome is correct.
- **`error` is a deep oxblood** (`#a3231d`, 7.46:1 on white). MUI's `#d32f2f` sat **4° from
  Boundary Coral Ink** — this document said a destructive action is deliberately *not* the Boundary
  coral, and at 4° a reader could not act on that distinction. Hue separation is unavailable in the
  red band, so the two separate by **depth** instead: the oxblood reads clearly darker than either
  coral token, and the bright red band is left to Boundary.
- **`warning` is a deep amber** (`#a85c11`, 5.00:1 white / 4.69:1 canvas, 34° from Boundary Coral).
  MUI's `#ed6c02` measured **2.92:1 on the canvas** — under the 3:1 a graphic mark needs, and it is
  used directly as one.
- **`success` stays MUI's `#2e7d32`**: 41° from the Phase green, two use sites, and no reason to
  spend a change on it.

### Diagram & Plot Palette (reserved for element-type encoding)
- **Boundary Coral** (`#f15d5f`, ink variant `#d23f4a`): a Boundary, wherever it is drawn — tree
  editor, Harris matrix, or posterior plot line/fill. The ink variant carries the 4.5:1 contrast
  cases (label text, plot lines); the base carries 3:1 graphic use (borders, rules, tints).
- **Deep Green** (`#1f7a62`): a Phase, everywhere. Also the base hue for HPD interval fills, at two
  alpha strengths for the two nested confidence levels (72% for the inner 1-sigma band, 30% for the
  outer) — one hue, two strengths, because they are one quantity, not two categories. The levels
  themselves are never written into a plot: a band carries its own label from `utils/hpd.hpdBands`,
  so a legend cannot name a level its geometry was not computed at.
- **Muted Violet** (`#7a5c99`): a Hiatus, everywhere.
- **Context shades** (`#35826e` → `#04372a`): one *stratigraphic context* against another in the
  Harris matrix. Six graded steps of the Phase hue, not six hues: hue held at 164°, only lightness
  and chroma moving, every step clearing 4.5:1 on white because these carry 10px node labels as well
  as borders. `contextShades(count)` spreads across the ramp rather than taking it in order, so a
  two-context model uses the extremes. This replaced eight Material swatches whose first entry was
  `blue[700]` = `#1976d2` — the anti-reference named below — and which also contained the Hiatus and
  Phase hues, so a Phase could be painted as a Hiatus. The cost was accepted deliberately: two
  contexts are no longer told apart at a glance the way two hues were. What it buys is that none of
  them can be mistaken for a Boundary, a Hiatus, or the Sequence.
- **Overlap ink** (Warm Ink at five strengths): how far the model is from being able to order two
  dates, in the ordering matrix. Ink rather than a hue because overlap is not an object type — it is
  the *absence* of information — and because every hue above is already a claim about what something
  is. Discrete, matching the five bands `services/intervals.py` classifies into
  (`none` → `extensive`), so the legend is a key a reader can match a square to.

### Named Rules
**The One Object, One Color Rule.** A Boundary, Phase, or Hiatus is the same hue in the tree editor,
the Harris matrix, and the posterior plot — never a per-view palette. Its corollary, learned the hard
way: a categorical palette for things that are *instances of one kind* — contexts, not element types
— must not be built from hues those element types already own. Distinguish instances by strength or
position, and leave hue to say what a thing is.

**The No Cool Neutral Rule.** Nothing in this app is drawn in a neutral R=G=B grey or in pure
black — not a divider, not a hover wash, not a disabled label, not a plot grid, not the greyed-out
prior behind a posterior. The one neutral is Warm Ink at an alpha. Where a Material default is
being replaced, match its measured contrast rather than copying its alpha number.

**The Selection-Is-Blue Rule.** `action.selected` and `action.focus` are the primary blue, not the
ink. The ordering matrix already reserved the primary for selection and focus on its own grid;
there was no reason that stopped at one component. A selected row reads as chosen rather than as
smudged.

**The Borrowed-Status Rule.** `success`/`warning`/`error` are reserved for genuine semantic state
(a solved solver, an actual warning, a destructive confirm action). They are never repurposed as a
categorical label for structural elements — that reads as a status display where none is intended.

## Typography

**Body & UI Font:** Roboto (with system-ui/sans-serif fallback), via `@fontsource/roboto`.

**Character:** A single, unadorned grotesque doing all the work — no display face, no accent
typeface. Precision comes from hierarchy and restraint (two explicit heading overrides, everything
else at MUI's Roboto defaults), not from typographic flourish.

### Hierarchy
- **Headline** (600, 2.5rem, line-height 1.167): page-level titles (`h1`) — e.g. "RadiCal
  Dashboard".
- **Title** (600, 2rem, line-height 1.2): section-level titles (`h2`).
- **Body** (400, 1rem, line-height 1.5): the MUI Roboto default, unmodified — the bulk of form
  labels, table content, and descriptive text.

### Named Rules
**The Two-Override Rule.** Only `h1` and `h2` carry explicit weight/size overrides. Every other
level is trusted to MUI's own Roboto scale rather than re-specified — restraint applied to the type
system itself, not just the palette.

## Layout

Every page is wrapped in a MUI `Container` (`maxWidth="lg"` is the standard content width) with
generous vertical breathing room (`py: 4` = 32px) and a `Paper` as the primary content surface
(`p: 3` = 24px internal padding). Spacing runs on MUI's default 8px unit throughout — `gap: 2` (16px)
is the common inline-control gap, `mb: 3`/`mb: 4` (24px/32px) separate major sections.

The primary navigation is a sticky `AppBar` that never scrolls out of view, since analyses and model
trees can run long. Below the `sm` breakpoint the three secondary nav links (Dashboard, Projects,
Curves) collapse into a hamburger menu rather than wrapping the bar to a second line — a wrapped
sticky bar would push all page content down a row on every load. The primary action, "New Analysis",
stays visible at every width; it never collapses into the menu.

### Data tables

Tables are the densest thing this app draws, and they follow one shape wherever they appear. A
table fills its card rather than shrinking to its content: content width was tried and reverted,
because on a `lg` container it leaves a quarter of the card empty, which reads worse than the gap
it removes.

Which column absorbs the surplus is the decision that matters. A label column — a sample name, a
context name — will take every spare pixel if allowed to, because it is usually the only column
that can wrap. Give it `width: '1%'` and it falls to its min-content width instead, and the
surplus spreads across the columns that carry numbers. On the results table that moved the name
column from 36% of the table to 27%, and halved the distance from a date to its own result.

Numeric cells are `white-space: nowrap`, so a dash-joined calendar range never breaks across two
lines and reads as two numbers. Every numeric column is `tabular-nums`, so digits line up down the
column rather than drifting with each digit's own width.

### Named Rules

**The Sticky-Where-It-Scrolls Rule.** A header sticks only where there is a container for it to
stick inside. `stickyHeader` plus a `maxHeight` on the container is a desktop affordance
(`{ xs: 'none', md: '70vh' }`); below `md` the header keeps `position: sticky` with `top: auto`, so
it stays in flow. A sticky header with no scrolling ancestor sticks to the VIEWPORT and slides
under the app bar, and a nested vertical scroller on touch fights the page scroll.

**The Band-On-Cells Rule.** A header's background goes on the `th`, never on the `tr`. A sticky
`tr` does not work: the row scrolls out from under its own colour. The same applies to a sticky
first column, which needs its own opaque background or whatever scrolls beneath shows through.

## Elevation & Depth

MUI's stock 25-step shadow scale is kept intact (the tuning per elevation level is preserved) but
globally recolored: every `rgba(0, 0, 0, *)` is replaced with `rgba(43, 35, 32, *)` — Warm Ink — so
every elevated surface (Card, Menu, Dialog, Snackbar, the sticky AppBar) picks up the same warm cast
as the rest of the app instead of a generic cool-black shadow. This is a single global substitution,
not a bespoke shadow per component.

The one deliberate flattening: a dense `Accordion` inside a dialog (`ImportDatesDialog`) drops to
`elevation={0}` with a 1px `divider`-colored border instead of a shadow — nested elevation inside an
already-elevated Dialog reads as noise, so the boundary is drawn with a line instead.

### Named Rules
**The Warm Shadow Rule.** No elevated surface in the app uses a black-tinted shadow. Shadows are
always the recolored warm-ink scale, applied by the one global substitution in the theme, never
overridden per component. The same holds for every other line and wash the theme owns — see The No
Cool Neutral Rule under Colors.

## Shapes

A single corner radius (`shape.borderRadius: 8`, 8px) applies globally through MUI's own component
defaults — Buttons, Cards, Dialogs, Chips, TextFields all inherit it rather than each carrying its
own radius override. There is no secondary "sharp" or "pill" family layered on top, except where
MUI's own component semantics already call for one (e.g. a `Chip`'s pill shape is MUI's default,
not a RadiCal-specific choice).

## Components

### Buttons
- **Shape:** 8px corner radius (theme default), MUI's standard contained/outlined/text padding.
- **Primary:** `variant="contained" color="primary"` — Slate Blue fill, white text. Used for the
  one primary action per view (e.g. "New Analysis" on the Dashboard).
- **Secondary actions:** `variant="outlined"`, typically without a color prop (inherits ink/border),
  or with an icon (`startIcon`) when it helps scanability ("Projects" with a folder icon).
- **On the AppBar:** `color="inherit"` rather than `primary`/`outlined` defaults, since the bar
  itself is already the brand color — buttons get a translucent white hover
  (`rgba(255,255,255,0.1)`) instead. The "New Analysis" nav button is the one exception, getting an
  explicit `variant="outlined"` with a white-tinted border so the primary action stays visually
  distinct even against the colored bar.

### Chips
- **Neutral/tag use** (default variant, no color prop): plain descriptive labels — "archived",
  "Assigned", a path breadcrumb. No semantic weight; these are metadata, not status.
- **Status use** (`color="success"`, outlined): reserved for a genuine diagnostic fact, e.g. "Exact
  solver" confirming which Bayesian solver actually ran. Color is spent only when it is true
  information, per the Borrowed-Status Rule above.

### Cards / Containers
- **Corner Style:** 8px (theme default).
- **Background:** Paper White (`#ffffff`), sitting on the Aged Paper canvas.
- **Shadow Strategy:** MUI's default elevation scale, warm-ink tinted (see Elevation & Depth).
- **Internal Padding:** 24px (`p: 3`) is the standard for a page's primary content Paper.

### Dialogs
- Standard MUI `Dialog` composition (`DialogTitle` / `DialogContent` / `DialogActions`).
- `ConfirmDialog` is the canonical confirmation pattern (`maxWidth="xs" fullWidth`), replacing the
  browser's native `confirm()`. Destructive actions use `color="error"` (MUI's standard error red,
  deliberately *not* the brand's Boundary Coral, which stays reserved for diagram meaning) on the
  confirm button; a `loading` state disables both actions and swaps the confirm label to an
  in-progress "Working…" state rather than leaving the dialog silently unresponsive.

### Navigation
- Sticky `AppBar`, primary-colored. Wordmark is the 36×36 logo mark plus "RadiCal" (h6, 600 weight)
  linking home. Links inline at `sm` and above; a hamburger `Menu` below `sm`. See Layout for the
  collapse behavior and why "New Analysis" never collapses.

### Progress & Status (signature component)
- `JobProgress` uses MUI's `Alert severity="info"` for a run in progress — deliberately not a
  bespoke color, so that `info`/`warning`/`error` stay meaningful elsewhere on the same page.
- The progress bar switches between `determinate` and `indeterminate` based on whether the current
  phase has a meaningful fraction to show: queued and burn-in phases show `indeterminate` rather
  than pinning at 0%, which would read as "stuck" instead of "started, no estimate yet."

### Data tables (signature pattern)

The page's most reused component and the one most worth copying exactly. See Layout for the width
and sticky rules; what follows is what a table *says*.

- **Element type is a coloured word**, in that type's own hue from the diagram palette — `sequence`
  in the Sequence blue, `phase` in Phase green, `boundary` in Boundary coral, at `0.75rem`/500
  beside the name. This is the only thing distinguishing a Sequence from a Phase in a tree table,
  and the two are not interchangeable: a Sequence orders what it contains and a Phase does not.
  Suppressed when the name already says it, so an element called "Boundary" is not labelled twice.
- **No type glyphs.** A coloured word and a symbol are two marks for one fact, and a symbol needs a
  legend the table does not have. The one glyph that survives is `⚠` for a date belonging to no
  element — a warning, not a type, with no colour of its own.
- **Headings are weight and indentation, not a band.** A tinted heading row paints colour across
  columns that carry nothing, and a deeply nested model stacks seven of them in twenty rows. A
  hairline above a top-level group gives it an edge without a slab.
- **The answer column carries weight (600), not a fill.** Flagged rows already carry a background
  tint; a column fill compositing under those would make a suspect row's answer a different colour
  from every other row's.
- **A range column shows a span and unfolds to its ranges**, from a control in its own header. One
  span per column is the scannable form and lets two columns be compared down the row; the full
  disjoint breakdown is a question a reader asks about one column at a time.

### Plots

- **No vendor chrome.** `displayModeBar: false` everywhere. The modebar rendered unlabelled `<a>`
  elements that could not take focus, and on the Curve browser it duplicated zoom controls the page
  already owned — while mutating axis state the page never learned about.
- **The app draws the controls**: a labelled `Download PNG` on every plot (at `PNG_EXPORT_SCALE`,
  3×, so a figure clears 300dpi in a paper), plus `Fit width` where a fixed axis can clip.
- **Lock the canvas unless the page can follow it.** `dragmode: false` where nothing tracks the
  view; where drag-to-zoom is worth keeping, an `onRelayout` handler must feed it back into the
  page's own state, or the two disagree and every control reading that state is wrong with them.
- **Controls sit above the figure**, with the toggles that govern it. Below a 3,000px plot they are
  an errand.
- **Every mark on a figure comes from `plotColors`**, including the ones that are not data. The
  unmodelled prior recedes in Warm Ink at `0.45` (`#a09c9b`, 2.72:1), not in the neutral
  `rgba(100,100,100,·)` it used to use — a dead grey sitting on white beside warm-ink point
  estimates. The stacked plot's phase rules and margin labels come from `phaseRule` and `label` for
  the same reason: drawn locally they were pure black, which put a second, cooler grid inside a
  figure whose every other mark is warm.
- **A figure is lettered in the app's own face.** `usePlotFont` sets `layout.font` on every plot:
  Roboto at 11px in the axis ink. Plotly's default is `"Open Sans", verdana, arial` at 12px, so
  until this was set every tick label, axis title and legend in the app was in a face the app uses
  nowhere else — the one place the design system stopped at the edge of a figure. 11px rather than
  the body's 16: a figure's lettering annotates the data, and at 12px the row names on a 40-row
  stack were the loudest thing in the margin.
- **Every calendar tick carries its era, one size down.** `calendarScaleTicks` graduates in round
  DISPLAY years (1000 BC, not the 1051 BC that rounding in cal BP would give), and
  `calendarTickMarkup` prints the era at 9px in the same ink as the number. Naming the era once at
  the left end was considered and rejected: an axis running 3000 BC to AD 500 then prints "500"
  twice, two millennia apart. The era is demoted, never dropped — cal BP is the exception, having
  one direction and its unit in the axis title.
- **One tick source per figure.** Where a figure is drawn twice — the stacked plot's axis and the
  ruler the app pins above it — both are built from the same tick array. Two scales graduated
  independently land a few years apart, and a ruler that disagrees with the figure under it is
  worse than no ruler.

#### Stacked plot (signature component)

Every posterior in one figure, one row each, on a shared calendar axis. Forty dates is three
thousand pixels, and everything below follows from that height.

- **The calendar scale is pinned.** A ruler sticks under the app bar for as long as the figure is on
  screen, graduated from the figure's own ticks and padded to the same margins, so reading the top
  row's date does not mean scrolling to the axis at the bottom, holding a number, and scrolling
  back. It carries the group the reader has scrolled into as well, since a row name says which date
  and not which Phase. Shown only when the figure is taller than the viewport; on a six-row stack
  the axis is already in view and a second copy of it is a band of chrome buying nothing. The
  figure's own axis stays where it is: the ruler is HTML and cannot travel into the exported PNG.
- **A group is a labelled spine, not a heading.** A vertical rule in the left margin spans exactly
  the rows a Sequence or Phase holds, in that element type's own hue from the diagram palette, with
  the group's name at its head in the same ink. A name at the first row alone says where a group
  starts and never where it ends. A Boundary sits outside the spine either side of it — it is the
  transition between two groups rather than a member of either, and the gap says so.
- **Row geometry lives in `stackedPlotGeometry`.** The curve height, the axis range, and the pixel
  offset of any row are one derivation. The pinned ruler turns a scroll offset back into a row to
  name its group, and that inverse is only right if it is the exact inverse of where the figure
  drew the row.

### Browser surfaces

The parts of the page the app does not draw ship in colours belonging to no design system, and the
text-selection highlight in particular was a *third* blue on screen beside the primary and the info
blue now merged into it. All three are set once, in the theme's `MuiCssBaseline` override:
`::selection` is the primary at `0.24` with the text left as it was, `caret-color` is Warm Ink, and
`:focus-visible` is a 2px primary ring at 2px offset — MUI v5 gives a focused Button a ripple and
very little else, so on most controls the visible focus ring was whatever the browser drew.

### Ordering matrix (signature component)

All-pairs date differences as a triangular grid: one cell per pair, rows and columns in the page's
chronological order. It exists because pairs grow as n(n-1)/2 and a list cannot show the finding —
a cluster of dates none of which can be ordered against each other is a solid block here and thirty
scattered rows in a table.

- **Drawn in Warm Ink at five discrete strengths** (0.06 / 0.20 / 0.38 / 0.58 / 0.80), matching the
  five bands the backend classifies into. Ink rather than a hue because overlap is not an object
  type — it is the absence of information — and every hue is already a claim about what something
  is. Discrete because a continuous ramp put a third of the cells within two decades of each other.
- **The legend is the key**: five labelled swatches at the exact fills they encode, in order, which
  is also the ranking. The words do not sort in English and the last one names the case where the
  model can say the least.
- **Primary blue is reserved for selection and focus** on this grid, drawn as a ring outside the
  cell so it reads on the palest and darkest fill alike.
- **Cells size from a budget handed down by the parent**, never from their own wrapper — a
  shrink-to-fit wrapper measured against its own content is circular, and silently caps the grid.
  Floor 28px so a cell stays a touch target; ceiling 56px so it stays a heatmap rather than tiles.

## Do's and Don'ts

### Do:
- **Do** keep a Boundary/Phase/Hiatus the same color everywhere it's drawn (tree editor, Harris
  matrix, posterior plot) — see the One Object, One Color Rule.
- **Do** render paired/nested quantities (the two nested HPD bands, mean/median) as one hue at two opacities,
  never as two different hues.
- **Do** apply the Warm Ink shadow substitution to any new elevated MUI surface rather than writing
  a bespoke shadow.
- **Do** reserve `success`/`warning`/`error` chip and alert colors for genuine semantic state; use
  the neutral default chip variant for plain descriptive tags.
- **Do** keep the plot/diagram surface a known, uncolored white so translucent fills composite
  predictably.
- **Do** theme the surfaces the browser owns — selection, caret, focus ring — from this palette
  rather than leaving them at their defaults. The same goes for a MUI component that hardcodes a
  colour instead of reading a token: `MuiOutlinedInput`'s notched outline is the one known case,
  and it is overridden in the theme rather than left black.
- **Do** match the *measured contrast* of a Material default you are replacing, not its alpha
  number. Warm ink is lighter than black.
- **Do** let a label column fall to `width: '1%'` so the columns carrying numbers share the surplus
  — see The Sticky-Where-It-Scrolls Rule and the Layout notes on tables.
- **Do** give a band or a sticky offset to the cells, never the row.
- **Do** draw the app's own plot controls, labelled and focusable, rather than shipping a vendor's
  toolbar.
- **Do** hand a component the space it may use. A component that measures its own wrapper measures
  a number it produced.

### Don't:
- **Don't** fall back to MUI's stock blue (`#1976d2`) or pink (`#dc004e`) — both were the literal
  starting point and were explicitly replaced with colors pulled from the app's own logo. `#1976d2`
  in particular sits a few degrees from the app's own primary, so a context painted in it read as the
  Sequence container; it reached the Harris matrix once via a categorical palette built from Material
  swatches, and was removed.
- **Don't** reuse `success`/`warning` as categorical labels for structural model-tree elements — it
  was tried, and it made the editor read as a status display instead of a diagram.
- **Don't** give Boundary Coral to a "danger"/destructive UI action — that hue is reserved for
  diagram meaning; destructive confirms use the `error` oxblood instead, which is deliberately far
  darker than either coral token because hue alone cannot separate them.
- **Don't** spend the brand coral (Burnt Coral) on interactive chrome — it has no use site in the
  app today, and that scarcity is deliberate, not an oversight to "fix." Its last one put white body
  text on it below AA.
- **Don't** leave a Material default in the palette because it is "just the semantic colour".
  `info`, `error` and `warning` were stock for a long time, and two of the three were sitting within
  a few degrees of a hue this app had already spent — see Semantic state.
- **Don't** write a raw `rgba(43, 35, 32, ·)` or a copied surface hex at a use site. `alpha(WARM_INK, ·)`
  and `theme/surfaces.ts` exist so a token and its duplicate cannot drift apart.
- **Don't** reach for MUI's `grey.*` for a banded surface inside a card — `background.muted` exists
  for that, and a neutral R=G=B grey on the warmest canvas in the app is the one palette this theme
  was written to replace.
- **Don't** encode a measurement in a hue that already names an object. An overlap, a duration, a
  confidence — these are quantities, and they take one hue (or the ink) at graded strength.
- **Don't** mark an element's type twice. A coloured word or a glyph, not both — and prefer the
  word, because a glyph needs a legend the surface rarely has.
- **Don't** tint a heading row across columns that carry nothing. Weight and indentation say
  "heading"; a full-width band on a nested tree says "broken table".
- **Don't** write a confidence level into a plot legend or an axis label. A band carries its own
  label with its geometry, so the two cannot disagree.
- **Don't** leave a vendor control interacting with state the app also owns. Either the app follows
  it or the canvas is locked.
