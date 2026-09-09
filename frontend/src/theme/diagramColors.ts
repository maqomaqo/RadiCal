import { useMemo } from 'react'
import { decomposeColor, recomposeColor, useTheme } from '@mui/material/styles'
import type { ElementType } from '@/types'

/**
 * Diagram colours are their own palette rather than borrowings from the
 * theme's semantic slots. Drawn from `success.main` and `warning.main`, the
 * model editor would read as a status display -- a green "fine" phase beside
 * an orange "careful" boundary -- where these are categories, not states.
 * Naming them here also frees `warning` to stay amber for the Results page's
 * genuine warnings.
 *
 * Hues are chosen as a set: the coral is the app's logo coral, the green sits
 * at its complement and part-way to the primary blue, and the violet is
 * desaturated to match -- Material's stock purple[500] is the loudest thing on
 * screen beside these.
 */
// Exported because the plots colour the same objects the diagrams do: a
// Boundary is a Boundary whether it is a rule in the matrix or a curve in the
// stacked plot. See theme/plotColors.ts.
export const BOUNDARY_CORAL = '#f15d5f'
export const BOUNDARY_CORAL_INK = '#d23f4a'
export const PHASE_GREEN = '#1f7a62'
export const HIATUS_VIOLET = '#7a5c99'

/**
 * The two sequences whose members are separated by a MEASURED distance, and
 * the undated level inside one.
 *
 * Their own hues, not borrowings. A hue here is a claim about what something
 * IS, and neither of these is a Phase or a Sequence: a wiggle match is one
 * calendar age with fixed gaps hung off it, and a deposition sequence prices
 * the intervals between its levels rather than leaving them uniform. Painting
 * either in the Phase green would say they are unordered piles of dates,
 * which is the opposite of what makes them worth having.
 *
 * Amber for the wiggle match and teal for the deposition sequence: both sit
 * clear of the app's blue, the Phase green and the Boundary coral at the
 * lightnesses these are used at, and both clear 4.5:1 on white so they can
 * carry a 10px label as well as a border. The undated level takes the
 * deposition teal desaturated -- it belongs to that sequence and is not a
 * different kind of thing from the levels beside it, which is the same rule
 * CONTEXT_PALETTE follows.
 */
export const WIGGLE_AMBER = '#a5651a'
export const DEPOSITION_TEAL = '#1c6a86'
export const UNDATED_LEVEL_SLATE = '#5b7f8d'

/**
 * The app's warm ink -- the tint behind every recoloured MUI shadow.
 *
 * Not an element colour and not a hue: it is what the app draws with when a
 * mark is ABOUT the data rather than a category of it. Point estimates are ink
 * because they annotate the curve; the ordering matrix is ink because overlap
 * measures what the model could not resolve, which is fog over a reading
 * rather than another kind of thing beside it.
 *
 * Here rather than in plotColors.ts, since three surfaces need the value.
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
 * matrix and the tree editor so the two cannot drift apart.
 *
 * Sequence is the one entry taken from the theme: it IS the app's primary
 * structural container, so it follows the brand colour wherever that goes.
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
      wiggle_sequence: solid(WIGGLE_AMBER),
      deposition_sequence: solid(DEPOSITION_TEAL),
      interpolated_depth: solid(UNDATED_LEVEL_SLATE),
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
 * Shades for telling one stratigraphic context from another in the Harris
 * matrix. One hue, graded -- not a categorical set of hues.
 *
 * This was eight Material swatches, and it broke the diagram's own premise.
 * Its first entry was `blue[700]` = #1976d2, the colour DESIGN.md names as the
 * literal anti-reference and a few degrees from the app's own #3c70b8, so the
 * first context in every model read as the Sequence container. The list also
 * held HIATUS_VIOLET and PHASE_GREEN, which meant a Phase could be painted in
 * the hue reserved for a Hiatus -- in a drawing whose whole premise is that a
 * hue is a claim about WHAT SOMETHING IS.
 *
 * A context is not a different KIND of thing from its neighbour; it is another
 * one of the same thing. So they share the Phase hue and differ only in
 * strength, and identity is carried by position and label rather than by
 * colour. The cost is real and was chosen deliberately: two contexts are no
 * longer told apart at a glance the way two hues would be. What is bought is
 * that no context can be mistaken for a Boundary, a Hiatus, or the Sequence.
 *
 * Hue is held at 164 degrees across every step; only lightness and chroma
 * move. Every step clears 4.5:1 on white, because these carry 10px labels as
 * well as borders, and adjacent steps sit ~1.2:1 apart -- the widest spacing
 * the contrast floor allows over six steps.
 */
export const CONTEXT_PALETTE = [
  '#35826e',
  '#26735f',
  '#196450',
  '#105442',
  '#084434',
  '#04372a',
]

/**
 * Which shades to use for `count` contexts, spread across the ramp.
 *
 * Taking them in order would give three contexts the three most similar
 * shades in the set and leave the rest of the range unused -- the worst
 * outcome for a ramp whose steps are only ~1.2:1 apart. Spreading means a
 * two-context model uses the extremes.
 *
 * Past the length of the ramp it repeats, which is the honest limit of one
 * hue: a model with seven contexts cannot have seven distinguishable shades
 * of one green, and pretending otherwise would be the categorical palette
 * again in disguise.
 */
export function contextShades(count: number): string[] {
  const last = CONTEXT_PALETTE.length - 1
  if (count <= 0) return []
  // One context takes the middle of the ramp -- the canonical Phase green --
  // rather than the palest end, which would read as a container tint.
  if (count === 1) return [CONTEXT_PALETTE[2]]
  if (count > CONTEXT_PALETTE.length) {
    return Array.from({ length: count }, (_, i) => CONTEXT_PALETTE[i % CONTEXT_PALETTE.length])
  }
  return Array.from({ length: count }, (_, i) =>
    CONTEXT_PALETTE[Math.round((i * last) / (count - 1))]
  )
}
