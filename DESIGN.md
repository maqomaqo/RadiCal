---
name: RadiCal
description: A calibrated instrument for radiocarbon dating and OxCal-style Bayesian chronological modelling.
colors:
  calibration-blue: "#3c70b8"
  burnt-coral: "#e14a56"
  aged-paper: "#faf6f3"
  paper-white: "#ffffff"
  warm-ink: "#2b2320"
  boundary-coral: "#f15d5f"
  boundary-coral-ink: "#d23f4a"
  phase-green: "#1f7a62"
  hiatus-violet: "#7a5c99"
  muted-surface: "#f4ede7"
  context-shade-lightest: "#35826e"
  context-shade-darkest: "#04372a"
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
always the same green, and paired quantities that are one thing at two strengths — the 95%/68% HPD
intervals, mean and median — are rendered as one hue at two opacities rather than as two unrelated
colors. `success`/`warning` are kept for genuine semantic state (a solved solver, a real warning on
the Results page) precisely because they used to be borrowed for Phase/Boundary and made the model
editor read as a status display.

**Key Characteristics:**
- Warm paper-and-ink surface language instead of cold neutral grey or pure white/black.
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
- **Burnt Coral** (`#e14a56`): darkened from the logo's raw coral (`#f05460`) for AA contrast as
  white-on-color text. Used sparingly and almost never for interactive chrome (one confirmed use
  site in the codebase) — the logo's exact coral is reserved for illustration, and even this
  darkened version stays mostly out of the way so it doesn't collide with Boundary Coral's distinct
  diagram meaning below.

### Neutral
- **Aged Paper** (`#faf6f3`): the app's canvas (`background.default`) — warm-tinted on purpose, so
  elevated white Cards and Paper actually read as lifted off the page instead of blending into a
  plain white background.
- **Paper White** (`#ffffff`): elevated surface color (`background.paper`) — Cards, Dialogs, Menus,
  and the plot area itself. Kept a known, uncolored white specifically so every translucent fill
  drawn on top of it (HPD bands, prior/posterior fills) composites against the same base rather than
  drifting muddier depending on what's behind it.
- **Muted Surface** (`#f4ede7`, `background.muted`): a banded surface *inside* an elevated white
  one — table header rows, the stat strip under a chart. Every one of these used MUI's `grey.100`
  (`#f5f5f5`), a perfectly neutral R=G=B sitting on the warmest background in the app, which is the
  one palette this theme exists to replace. Warmer and a step darker than the canvas so it reads as
  a band on paper rather than a hole in it.
- **Warm Ink** (`#2b2320`): not a UI-chrome color. It is what the app draws with when a mark is
  *about* the data rather than a category of it — every recolored MUI shadow (`rgba(43, 35, 32, *)`
  in place of pure black), the mean/median point-estimate marks on posterior plots, and the ordering
  matrix, where overlap measures what the model could not resolve. Exported from `diagramColors.ts`
  so the plots and the matrix cannot drift to two different inks.

### Diagram & Plot Palette (reserved for element-type encoding)
- **Boundary Coral** (`#f15d5f`, ink variant `#d23f4a`): a Boundary, wherever it is drawn — tree
  editor, Harris matrix, or posterior plot line/fill. The ink variant carries the 4.5:1 contrast
  cases (label text, plot lines); the base carries 3:1 graphic use (borders, rules, tints).
- **Deep Green** (`#1f7a62`): a Phase, everywhere. Also the base hue for HPD interval fills, at two
  alpha strengths for the two nested confidence levels (72% for 68% HPD, 30% for 95% HPD) — one hue,
  two strengths, because they are one quantity, not two categories.
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
overridden per component.

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

## Do's and Don'ts

### Do:
- **Do** keep a Boundary/Phase/Hiatus the same color everywhere it's drawn (tree editor, Harris
  matrix, posterior plot) — see the One Object, One Color Rule.
- **Do** render paired/nested quantities (68%/95% HPD, mean/median) as one hue at two opacities,
  never as two different hues.
- **Do** apply the Warm Ink shadow substitution to any new elevated MUI surface rather than writing
  a bespoke shadow.
- **Do** reserve `success`/`warning`/`error` chip and alert colors for genuine semantic state; use
  the neutral default chip variant for plain descriptive tags.
- **Do** keep the plot/diagram surface a known, uncolored white so translucent fills composite
  predictably.

### Don't:
- **Don't** fall back to MUI's stock blue (`#1976d2`) or pink (`#dc004e`) — both were the literal
  starting point and were explicitly replaced with colors pulled from the app's own logo. `#1976d2`
  in particular sits a few degrees from the app's own primary, so a context painted in it read as the
  Sequence container; it reached the Harris matrix once via a categorical palette built from Material
  swatches, and was removed.
- **Don't** reuse `success`/`warning` as categorical labels for structural model-tree elements — it
  was tried, and it made the editor read as a status display instead of a diagram.
- **Don't** give Boundary Coral to a "danger"/destructive UI action — that hue is reserved for
  diagram meaning; destructive confirms use MUI's standard `error` red instead.
- **Don't** spend the brand coral (Burnt Coral) freely on interactive chrome — it is used almost
  nowhere in the app today, and that scarcity is deliberate, not an oversight to "fix."
- **Don't** reach for MUI's `grey.*` for a banded surface inside a card — `background.muted` exists
  for that, and a neutral R=G=B grey on the warmest canvas in the app is the one palette this theme
  was written to replace.
- **Don't** encode a measurement in a hue that already names an object. An overlap, a duration, a
  confidence — these are quantities, and they take one hue (or the ink) at graded strength.
