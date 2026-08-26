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
   * Thin rules that project a value across a figure rather than carrying one
   * -- the Curve view's line from a laboratory measurement to where it meets
   * the calibration curve. Stronger than the grid, which it has to be
   * readable against, and weaker than anything that is data.
   */
  guide: string
  /** The plot area itself. */
  surface: string
  grid: string
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
      // The unmodelled calibration, behind its posterior. Neutral grey: it is
      // the thing being compared against, so it recedes rather than claiming a
      // colour of its own.
      priorLine: 'rgba(100, 100, 100, 0.5)',
      priorFill: 'rgba(100, 100, 100, 0.15)',
      hpd95: alpha(PHASE_GREEN, 0.3),
      hpd68: alpha(PHASE_GREEN, 0.72),
      mean: alpha(INK, 0.85),
      median: alpha(INK, 0.45),
      guide: alpha(INK, 0.3),
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
