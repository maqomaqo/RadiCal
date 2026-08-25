import { useMemo } from 'react'
import { alpha, useTheme } from '@mui/material/styles'
import { BOUNDARY_CORAL_INK, PHASE_GREEN, WARM_INK } from './diagramColors'

/**
 * Colours for the posterior plots, shared by the single-date and stacked
 * views so the two can't drift -- they previously kept their own copies of
 * the same constants, and the single-date plot had already fallen behind.
 *
 * Everything here used to be Plotly and matplotlib stock: the posterior was
 * Plotly's default #636EFA rather than the app's own blue, and the two HPD
 * bars were tab10's orange and green -- the same pair the diagrams dropped
 * for reading as a warning next to a success. On a Results page showing a
 * Harris matrix above a plot, that meant a considered palette and a set of
 * library defaults side by side.
 *
 * Two pairings do the work:
 *
 *   A Boundary is coral, as it is in the tree editor and the matrix, so the
 *   same object is the same colour wherever it is drawn.
 *
 *   Things that are two of a kind are one hue at two strengths, not two
 *   hues. The 95% and 68% HPD are nested intervals of ONE quantity, and
 *   orange-against-green stated them as two unrelated categories; the
 *   stronger green is now the tighter interval and the weaker the broader
 *   one, which is what they mean. Mean and median are a pair the same way.
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

// The app's warm ink, as used by the theme's shadows. Point estimates are
// annotation over the data rather than data themselves, so they read as
// pencil marks instead of taking a hue of their own and competing with the
// curve they mark. Shared from diagramColors so the matrix and the plots
// cannot drift to two different inks.
const INK = WARM_INK

export function usePlotColors(): PlotColors {
  const theme = useTheme()
  const primary = theme.palette.primary.main
  // Memoised so a caller can list it in a useMemo's dependencies -- both
  // plots build their entire trace array in one.
  return useMemo(
    () => ({
      posteriorLine: primary,
      posteriorFill: alpha(primary, 0.22),
      boundaryLine: BOUNDARY_CORAL_INK,
      // Lighter than the posterior's fill: a boundary is a transition
      // rather than a dated thing, so it reads as an outline against the
      // solid dates either side of it.
      boundaryFill: alpha(BOUNDARY_CORAL_INK, 0.13),
      // The unmodelled calibration, shown behind its posterior. Neutral
      // grey on purpose -- it is the thing being compared against, so it
      // should recede rather than claim a colour of its own.
      priorLine: 'rgba(100, 100, 100, 0.5)',
      priorFill: 'rgba(100, 100, 100, 0.15)',
      hpd95: alpha(PHASE_GREEN, 0.3),
      hpd68: alpha(PHASE_GREEN, 0.72),
      mean: alpha(INK, 0.85),
      median: alpha(INK, 0.45),
      guide: alpha(INK, 0.3),
      // White, not the grey this used to be. Every fill on these plots is
      // translucent -- and has to be, since the prior reads by showing
      // through the posterior drawn over it, and in the stacked view the
      // curves draw over their own HPD bars. Tinting the surface those
      // fills composite against therefore desaturates all of them at once,
      // which is what made the colours look muddy. White is what they were
      // picked against, and it matches the card the plot sits on.
      surface: theme.palette.background.paper,
      // With the surface no longer marking out the plot area, the grid is
      // what does. Warm, to sit with the rest of the app rather than
      // reintroducing a cool grey by the back door.
      grid: alpha(INK, 0.09),
    }),
    [primary, theme.palette.background.paper]
  )
}
