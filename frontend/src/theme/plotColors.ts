import { useMemo } from 'react'
import { alpha, useTheme } from '@mui/material/styles'
import { BOUNDARY_CORAL_INK, PHASE_GREEN, WARM_INK } from './diagramColors'

/**
 * Colours for the posterior plots, shared by the single-date and stacked
 * views so the two cannot drift apart.
 *
 * The app's own palette rather than Plotly and matplotlib stock, so a Results
 * page showing a Harris matrix above a plot does not put a considered palette
 * beside a set of library defaults.
 *
 * Two pairings do the work:
 *
 *   A Boundary is coral, as in the tree editor and the matrix, so the same
 *   object is the same colour wherever it is drawn.
 *
 *   Two of a kind are one hue at two strengths, not two hues. The 95% and 68%
 *   HPD are nested intervals of ONE quantity, so the stronger green is the
 *   tighter interval and the weaker the broader. Mean and median pair the same
 *   way.
 */
export interface PlotColors {
  posteriorLine: string
  posteriorFill: string
  boundaryLine: string
  boundaryFill: string
  priorLine: string
  priorFill: string
  hpd95: string
  hpd68: string
  mean: string
  median: string
  /**
   * The two directions of a residual -- arrived EARLIER than a fitted front
   * predicts, or LATER -- on the map and in the table beside it.
   *
   * Used to be the primary blue and the ink, and on the offline basemap that
   * read. On OpenStreetMap it did not: the ocean there is a pale blue, and
   * an early site drawn in the app's blue sat on it like a wave on water.
   * So the pair is an orange and a deep teal-blue: opposite ends of the one
   * axis a colour-blind reader can still tell apart, both clear of the tile
   * ocean (2.9:1 and 4.4:1 against #aad3df) and of pale land, and both
   * dark enough to carry the mark's own outline. Neither is an element
   * hue: the wiggle amber and deposition teal are deliberately NOT reused,
   * since a site coloured in one would be claiming to be a wiggle match.
   */
  residualEarly: string
  residualLate: string
  /**
   * Thin rules that project a value across a figure rather than carrying one
   * -- the Curve view's line from a laboratory measurement to where it meets
   * the calibration curve. Stronger than the grid, which it has to be
   * readable against, and weaker than anything that is data.
   */
  guide: string
  /**
   * A rule INSIDE a figure that separates one group of rows from the next --
   * the stacked plot's phase breaks. A divider, so it is drawn at the theme's
   * divider strength rather than at the grid's: the grid marks out the plot
   * area, this states a boundary between two parts of the content.
   */
  phaseRule: string
  /**
   * Text the app writes onto a figure -- a phase name in the margin. The
   * app's own text ink, at the alpha `text.primary` uses, so a label on a plot
   * and a heading in the results table are the same colour.
   */
  label: string
  /**
   * Every word Plotly writes on a figure it was not given a colour for: tick
   * labels, axis titles, the legend. Plotly's own default is `#444`, a dead
   * neutral grey, which put a cool text colour on every axis in the app. This
   * is the alpha `text.secondary` uses, so a tick label and a caption beneath
   * the figure are the same ink at the same strength (6.57:1 on paper).
   */
  axis: string
  /** The plot area itself. */
  surface: string
  grid: string
}

/**
 * The face and size every figure is lettered in.
 *
 * Plotly's own default is `"Open Sans", verdana, arial, sans-serif` at 12px,
 * so every tick label, axis title and legend in the app was set in a face the
 * app does not use, beside body text in Roboto -- the one place the design
 * system stopped at the edge of a plot. The size is a step down from the
 * theme's body: a figure's lettering annotates the data rather than being
 * read as text, and at 12px the tick labels on a 40-row stack are the loudest
 * thing in the margin.
 *
 * A hook, and separate from the colours above, because it reads the theme's
 * own font stack rather than restating it.
 */
export interface PlotFont {
  family: string
  size: number
  color: string
}

export function usePlotFont(): PlotFont {
  const theme = useTheme()
  const color = usePlotColors().axis
  return useMemo(
    () => ({ family: theme.typography.fontFamily ?? 'Roboto, sans-serif', size: 11, color }),
    [theme.typography.fontFamily, color]
  )
}

// The app's warm ink, as the theme's shadows use. Point estimates are
// annotation over the data rather than data, so they read as pencil marks
// instead of taking a hue and competing with the curve they mark. Shared from
// diagramColors so the matrix and the plots cannot drift to two inks.
const INK = WARM_INK

export function usePlotColors(): PlotColors {
  const theme = useTheme()
  const primary = theme.palette.primary.main
  // Memoised so a caller can list it in a useMemo's dependencies; both plots
  // build their whole trace array in one.
  return useMemo(
    () => ({
      posteriorLine: primary,
      posteriorFill: alpha(primary, 0.22),
      boundaryLine: BOUNDARY_CORAL_INK,
      // Lighter than the posterior's fill: a boundary is a transition rather
      // than a dated thing, so it reads as an outline against the solid dates
      // either side.
      boundaryFill: alpha(BOUNDARY_CORAL_INK, 0.13),
      // The unmodelled calibration, behind its posterior. It recedes rather
      // than claiming a colour of its own -- but it recedes in the app's ink,
      // not in a neutral grey. rgba(100,100,100,·) is a dead R=G=B grey, the
      // one thing this palette exists to replace, and it was sitting on white
      // beside warm-ink point estimates. The ink is warmer AND more visible at
      // these strengths: 0.45 composites to #a09c9b at 2.72:1, against the
      // grey's #b2b2b2 at 2.12:1.
      priorLine: alpha(INK, 0.45),
      priorFill: alpha(INK, 0.14),
      hpd95: alpha(PHASE_GREEN, 0.3),
      hpd68: alpha(PHASE_GREEN, 0.72),
      mean: alpha(INK, 0.85),
      median: alpha(INK, 0.45),
      residualEarly: '#b85c14',
      residualLate: '#215f7a',
      guide: alpha(INK, 0.3),
      // Both were pure black at the one call site that drew them, which put a
      // second, cooler grid inside a figure whose every other mark is warm.
      phaseRule: alpha(INK, 0.14),
      label: alpha(INK, 0.92),
      axis: alpha(INK, 0.74),
      // White. Every fill on these plots is translucent, and has to be -- the
      // prior reads by showing through the posterior over it, and the stacked
      // curves draw over their own HPD bars -- so tinting the surface they
      // composite against desaturates all of them at once. White is what they
      // were picked against, and matches the card the plot sits on.
      surface: theme.palette.background.paper,
      // With the surface white, the grid is what marks out the plot area.
      // Warm, to sit with the rest of the app rather than reintroducing a cool
      // grey by the back door.
      grid: alpha(INK, 0.09),
    }),
    [primary, theme.palette.background.paper]
  )
}
