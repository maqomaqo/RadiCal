import { useMemo } from 'react'
import { alpha, useTheme } from '@mui/material/styles'
import { BOUNDARY_CORAL_INK, HIATUS_VIOLET, PHASE_GREEN, WARM_INK } from './diagramColors'

/**
 * How each analysis is drawn on the compare page.
 *
 * Colour by POSITION here, which everywhere else in this app would be a bug --
 * see curveColors.ts, where a curve's style has to be a property of the curve
 * so IntCal20 is never two different lines on two different figures. The
 * difference is what position means. A curve's slot in a list is an accident;
 * a run's slot in a comparison is its ROLE: the first is the reference every
 * figure on the page is measured against, and the others are what is being
 * held up to it. The reference is the app's primary blue on every comparison
 * ever drawn, which is the same stability, keyed to the thing that is actually
 * stable.
 *
 * The hues are the diagram palette's, and they are safe to borrow because a
 * comparison draws no model elements: there is no Boundary, Phase or Hiatus on
 * this page for the coral, green or violet to be confused with. If that ever
 * stops being true -- if the compare page grows a Harris matrix -- these have
 * to move, not the diagrams', since a hue there is a claim about what
 * something IS and this is only a claim about which run it came from.
 *
 * Four slots, because the paired-bar figure stops being readable past four
 * runs stacked in one row; the page refuses a fifth rather than reusing a hue.
 */
export const MAX_COMPARED_RUNS = 4

export interface RunStyle {
  /** Bar outline, the run's chip, and its name wherever it is written. */
  line: string
  /** The outer HPD band -- the level the reader selected. */
  fillOuter: string
  /** The inner 68.3% band, which sits inside the outer one. Same hue, stronger,
   *  as everywhere else in the app that draws two nested intervals of one
   *  quantity. */
  fillInner: string
}

/**
 * Styles for `count` runs, reference first.
 *
 * The primary comes from the theme so the reference run follows the brand
 * colour wherever that goes, exactly as the Sequence container does in
 * diagramColors.
 */
export function useRunStyles(count: number): RunStyle[] {
  const primary = useTheme().palette.primary.main
  return useMemo(() => {
    const hues = [primary, BOUNDARY_CORAL_INK, HIATUS_VIOLET, PHASE_GREEN]
    return Array.from({ length: Math.min(count, hues.length) }, (_, i) => ({
      line: hues[i],
      fillOuter: alpha(hues[i], 0.26),
      fillInner: alpha(hues[i], 0.68),
    }))
  }, [primary, count])
}

/**
 * Marks that are ABOUT the comparison rather than part of it: the hairline
 * dropped at the reference median, and the grid the bars are read against.
 * The app's warm ink, as the plots' point estimates are, for the same reason
 * -- an annotation should not take a hue and compete with the data it annotates.
 */
export function useCompareMarks() {
  return useMemo(
    () => ({
      /** Vertical rule at the reference run's median, through the whole row. */
      referenceRule: alpha(WARM_INK, 0.38),
      grid: alpha(WARM_INK, 0.1),
      /** The median tick inside a bar, drawn light so it reads on the fill. */
      medianTick: alpha(WARM_INK, 0.75),
      /** The rule standing in for a run that has no such quantity at all.
       *  Stronger than the grid on purpose: "this model derives nothing here"
       *  is a finding, and a mark too faint to see reads as a rendering
       *  failure instead. */
      absent: alpha(WARM_INK, 0.28),
    }),
    []
  )
}
