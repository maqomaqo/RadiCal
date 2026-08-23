import { useMemo } from 'react'
import { decomposeColor, recomposeColor, useTheme } from '@mui/material/styles'
import { blue, lightBlue, pink, brown, blueGrey } from '@mui/material/colors'
import type { ElementType } from '@/types'

/**
 * Diagram colours are their own small palette rather than borrowings from
 * the theme's semantic slots. Phase and Boundary used to be `success.main`
 * and `warning.main`, which meant the model editor read as a status
 * display -- a green "everything's fine" phase next to an orange "careful"
 * boundary -- when neither carries any such judgement. They're categories,
 * not states. Naming them here also frees `warning` to stay amber for the
 * Results page's genuine warnings, which would otherwise have had to move
 * in step with the diagrams.
 *
 * Hues are chosen as a set: the coral is the app's own logo coral, the
 * green sits at its complement (and part-way to the primary blue), and the
 * violet is desaturated to match -- Material's stock purple[500] was the
 * loudest thing on screen next to these.
 */
// Exported because the plots colour the same objects the diagrams do -- a
// Boundary is a Boundary whether it is a rule in the matrix or a curve in
// the stacked plot. See theme/plotColors.ts.
export const BOUNDARY_CORAL = '#f15d5f'
export const BOUNDARY_CORAL_INK = '#d23f4a'
export const PHASE_GREEN = '#1f7a62'
export const HIATUS_VIOLET = '#7a5c99'

/**
 * The app's warm ink -- the tint behind every recoloured MUI shadow.
 *
 * Not an element colour and deliberately not a hue: it is what the app draws
 * with when the mark is ABOUT the data rather than being a category of it.
 * The point estimates on a posterior plot are ink because they annotate the
 * curve; the ordering matrix is ink because overlap measures what the model
 * could not resolve, which is fog over a reading rather than another kind of
 * thing beside it.
 *
 * Lives here rather than in plotColors.ts, which held the only copy while two
 * other surfaces needed the same value.
 */
export const WARM_INK = '#2b2320'

export interface ElementTypeColor {
  /** Borders, rules and tints. Graphic use, which only needs 3:1. */
  main: string
  /**
   * Backgrounds beneath white text, and coloured label text -- both of
   * which need 4.5:1. Identical to `main` except for the coral, which at
   * 3.2:1 is right for a rule and too light to sit under a white label.
   */
  ink: string
}

const solid = (main: string): ElementTypeColor => ({ main, ink: main })

/**
 * Colour for each model-tree element type, shared by the read-only Harris
 * matrix diagram and the tree editor so the two can't silently drift apart
 * -- previously each defined its own copy of the same four hex values.
 *
 * Sequence is the one entry still taken from the theme: it IS the app's
 * primary structural container, so it should follow the brand colour
 * wherever that goes.
 */
export function useElementTypeColors(): Record<ElementType, ElementTypeColor> {
  const primary = useTheme().palette.primary.main
  // Memoised so callers can list this in a useMemo's dependencies without
  // a fresh object every render invalidating it -- the Harris diagram
  // rebuilds its whole node geometry off one such memo.
  return useMemo(
    () => ({
      sequence: solid(primary),
      phase: solid(PHASE_GREEN),
      boundary: { main: BOUNDARY_CORAL, ink: BOUNDARY_CORAL_INK },
      hiatus: solid(HIATUS_VIOLET),
    }),
    [primary]
  )
}

/**
 * `color` at `weight` strength over `base`, returned fully opaque.
 *
 * The Harris matrix nests boxes several levels deep, and filling each one
 * with a translucent tint meant a context four levels in was seen through
 * four washes of three different hues -- so the fill formula said one
 * thing and the screen showed another, muddier one, and the same box read
 * differently depending on where it sat. Compositing up front instead
 * means every box paints exactly the shade it asks for, whatever is
 * behind it.
 */
export function tint(color: string, weight: number, base: string): string {
  const fg = decomposeColor(color).values
  const bg = decomposeColor(base).values
  return recomposeColor({
    type: 'rgb',
    values: [0, 1, 2].map((i) => Math.round(fg[i] * weight + bg[i] * (1 - weight))) as [
      number,
      number,
      number,
    ],
  })
}

/**
 * Categorical palette for distinguishing individual stratigraphic contexts
 * in the Harris matrix diagram. Needs more distinct hues than the four
 * element-type colours above serve, so it mostly draws straight from
 * Material's standard swatches (there's no "context 5 of 8" concept for
 * the theme to have an opinion about).
 *
 * The two entries that aren't stock swatches are the ones that would
 * otherwise collide with an element type in the same diagram: a context
 * in Material's green or orange reads as a Phase or a Boundary at a
 * glance, so those slots take the Phase green itself and a browner orange
 * that can't be mistaken for the boundary coral.
 */
export const CONTEXT_PALETTE = [
  blue[700],
  HIATUS_VIOLET,
  PHASE_GREEN,
  '#b5651d',
  lightBlue[700],
  pink[700],
  brown[700],
  blueGrey[700],
]
