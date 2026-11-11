import { useMemo } from 'react'
import { decomposeColor, recomposeColor, useTheme } from '@mui/material/styles'
import type { ElementType } from '@/types'
import type { ColorMode } from './surfaces'

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
 * The same hues for the dark theme.
 *
 * A hue is a claim about what something IS, so none of these moved: each is
 * the light value's own hue and (barely reduced) chroma, taken UP in
 * lightness until it clears 4.6:1 on `background.muted` -- the dimmest surface
 * a label can sit on -- which is 5.4:1 on a card and 6:1 on the canvas. The
 * 4.5:1 that the light theme asked of its inks on white is therefore still
 * asked of them here, for the same reason: they carry a 10px label as well as
 * a border.
 *
 * One hue was moved on purpose. The light theme tells the Boundary coral from
 * the `error` oxblood by DEPTH, because hue separation is unavailable in the
 * red band -- and depth is exactly what a dark ground takes away, since both
 * have to be light to be read. So the pair separates the other way round here:
 * the coral leans 15 degrees towards rose and stays the stronger of the two,
 * and `error` leans towards orange-red and is the lighter and softer of them
 * (see theme/appTheme.ts).
 */
export const BOUNDARY_CORAL_DARK = '#f66b73'
export const BOUNDARY_CORAL_INK_DARK = '#f0666f'
export const PHASE_GREEN_DARK = '#56a089'
export const HIATUS_VIOLET_DARK = '#a287c0'
export const WIGGLE_AMBER_DARK = '#c2864a'
export const DEPOSITION_TEAL_DARK = '#5b9ab5'
export const UNDATED_LEVEL_SLATE_DARK = '#5b7f8d'

export interface ElementHues {
  boundary: { main: string; ink: string }
  phase: string
  hiatus: string
  wiggle: string
  deposition: string
  undated: string
}

/** The element hues for a colour mode. */
export function elementHues(mode: ColorMode): ElementHues {
  return mode === 'dark'
    ? {
        boundary: { main: BOUNDARY_CORAL_DARK, ink: BOUNDARY_CORAL_INK_DARK },
        phase: PHASE_GREEN_DARK,
        hiatus: HIATUS_VIOLET_DARK,
        wiggle: WIGGLE_AMBER_DARK,
        deposition: DEPOSITION_TEAL_DARK,
        undated: UNDATED_LEVEL_SLATE_DARK,
      }
    : {
        boundary: { main: BOUNDARY_CORAL, ink: BOUNDARY_CORAL_INK },
        phase: PHASE_GREEN,
        hiatus: HIATUS_VIOLET,
        wiggle: WIGGLE_AMBER,
        deposition: DEPOSITION_TEAL,
        undated: UNDATED_LEVEL_SLATE,
      }
}

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
  const { palette } = useTheme()
  const primary = palette.primary.main
  const mode = palette.mode
  // Memoised so callers can list this in a useMemo's dependencies without
  // a fresh object every render invalidating it -- the Harris diagram
  // rebuilds its whole node geometry off one such memo.
  return useMemo(() => {
    const hues = elementHues(mode)
    return {
      sequence: solid(primary),
      phase: solid(hues.phase),
      boundary: hues.boundary,
      hiatus: solid(hues.hiatus),
      wiggle_sequence: solid(hues.wiggle),
      deposition_sequence: solid(hues.deposition),
      interpolated_depth: solid(hues.undated),
    }
  }, [primary, mode])
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
 * The same ramp for the dark theme: hue 171 held, chroma easing down as the
 * steps get light, and the six steps still ~1.2:1 apart -- the widest spacing
 * the floor allows, since the first has to clear 4.6:1 on `background.muted`
 * to carry a 10px label.
 *
 * Index order means the same thing in both modes: index 0 is the quietest
 * step and index 5 the strongest. On paper that is the lightest green and the
 * darkest; on ink it is the dimmest and the palest. What a reader learns --
 * "stronger step, higher in the ramp" -- survives the change of ground.
 */
export const CONTEXT_PALETTE_DARK = [
  '#57a088',
  '#6bae98',
  '#80bda8',
  '#95cdb9',
  '#abdecc',
  '#c2f0df',
]

/** The context ramp for a colour mode. */
export function contextPalette(mode: ColorMode = 'light'): string[] {
  return mode === 'dark' ? CONTEXT_PALETTE_DARK : CONTEXT_PALETTE
}

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
export function contextShades(count: number, mode: ColorMode = 'light'): string[] {
  const palette = contextPalette(mode)
  const last = palette.length - 1
  if (count <= 0) return []
  // One context takes the middle of the ramp -- the canonical Phase green --
  // rather than the quietest end, which would read as a container tint.
  if (count === 1) return [palette[2]]
  if (count > palette.length) {
    return Array.from({ length: count }, (_, i) => palette[i % palette.length])
  }
  return Array.from({ length: count }, (_, i) => palette[Math.round((i * last) / (count - 1))])
}
