import { alpha, decomposeColor, getContrastRatio, recomposeColor } from '@mui/material/styles'
import { SURFACES, type ColorMode } from './surfaces'

/**
 * The app's ink at a strength, in either colour mode.
 *
 * Every line and wash of chrome -- divider, hover, grid, the greyed prior
 * behind a posterior, the overlap matrix -- is the one neutral at an alpha,
 * and each alpha in the light theme was chosen for the CONTRAST it composites
 * to, not for its number (DESIGN.md: match the measured contrast of what is
 * replaced). Copying the number onto a dark ground would silently change that
 * contrast: ink is lighter than black, and aged paper over ink is a different
 * distance from its ground than warm ink over white.
 *
 * So the dark theme is not a second table of alphas to keep in step. A
 * strength is stated once, in the light theme's terms, and this finds the
 * alpha that gives the same contrast against the dark paper. Light is the
 * identity, so nothing in the light theme moves.
 */

/** `fg` at `strength` over `bg`, as an opaque rgb() string. */
function over(fg: string, strength: number, bg: string): string {
  const f = decomposeColor(fg).values
  const b = decomposeColor(bg).values
  return recomposeColor({
    type: 'rgb',
    values: [0, 1, 2].map((i) => Math.round(f[i] * strength + b[i] * (1 - strength))) as [
      number,
      number,
      number,
    ],
  })
}

const cache = new Map<number, number>()

/**
 * The alpha that, over `mode`'s paper, is as far from that paper as `strength`
 * of the light theme's ink is from white.
 */
export function inkAlpha(mode: ColorMode, strength: number): number {
  if (mode === 'light') return strength
  const hit = cache.get(strength)
  if (hit !== undefined) return hit

  const light = SURFACES.light
  const dark = SURFACES.dark
  const target = getContrastRatio(over(light.ink, strength, light.paper), light.paper)

  // Contrast rises with strength, so bisect. 24 rounds is far past what an
  // 8-bit channel can tell apart.
  let lo = 0
  let hi = 1
  for (let i = 0; i < 24; i++) {
    const mid = (lo + hi) / 2
    if (getContrastRatio(over(dark.ink, mid, dark.paper), dark.paper) < target) lo = mid
    else hi = mid
  }
  const found = Math.round(hi * 1000) / 1000
  cache.set(strength, found)
  return found
}

/** The ink of `mode` at the light theme's `strength`, ready to paint with. */
export function inkAt(mode: ColorMode, strength: number): string {
  return alpha(SURFACES[mode].ink, inkAlpha(mode, strength))
}
