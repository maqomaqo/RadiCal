import { useTheme } from '@mui/material/styles'
import { purple, blue, lightBlue, pink, brown, blueGrey, green, orange } from '@mui/material/colors'
import type { ElementType } from '@/types'

/**
 * Colour for each model-tree element type, shared by the read-only Harris
 * matrix diagram and the tree editor so the two can't silently drift apart
 * -- previously each defined its own copy of the same four hex values.
 *
 * Sequence/Phase/Boundary reuse the theme's own primary/success/warning
 * colours rather than restating them as literals: a Sequence IS the app's
 * primary structural container, and Phase/Boundary already had exactly
 * MUI's default success/warning colours, just spelled out as hex instead
 * of referencing them. Hiatus has no natural home in the theme's palette
 * (there's no fourth semantic slot for it), so it's the one entry that
 * names a Material swatch directly rather than a theme token.
 */
export function useElementTypeColors(): Record<ElementType, string> {
  const theme = useTheme()
  return {
    sequence: theme.palette.primary.main,
    phase: theme.palette.success.main,
    boundary: theme.palette.warning.main,
    hiatus: purple[500],
  }
}

/**
 * Categorical palette for distinguishing individual stratigraphic contexts
 * in the Harris matrix diagram. Needs more distinct hues than the four
 * element-type colours above serve, so it draws straight from Material's
 * standard swatches instead of the theme's semantic slots (there's no
 * "context 5 of 8" concept for the theme to have an opinion about).
 */
export const CONTEXT_PALETTE = [
  blue[700],
  purple[500],
  green[800],
  orange[800],
  lightBlue[700],
  pink[700],
  brown[700],
  blueGrey[700],
]
