/**
 * The surfaces the app paints on, in one place, for each colour mode.
 *
 * They live here rather than only inside `createTheme` because some sites need
 * a surface colour outside the `sx` prop -- inside a `linear-gradient()`
 * string, or in a plain style object -- where a palette token does not
 * resolve. Those sites read `theme.palette.background.*`, which is built from
 * the constants below, so a token and its duplicate cannot drift apart.
 */

export type ColorMode = 'light' | 'dark'

export interface Surfaces {
  /** The canvas (`background.default`). */
  canvas: string
  /**
   * Elevated surfaces (`background.paper`) -- Cards, Dialogs, and the plot
   * area itself. Deliberately a known, uncoloured surface: every fill drawn on
   * a plot is translucent, so tinting what they composite against would
   * desaturate all of them at once.
   */
  paper: string
  /** A banded surface INSIDE an elevated one (`background.muted`). */
  muted: string
  /**
   * A surface that floats over another elevated one -- a Menu, a Popover, a
   * Tooltip. In the light theme that is paper with a shadow under it; in the
   * dark theme a shadow is nearly invisible, so the lift is lightness.
   */
  raised: string
  /**
   * The app's one neutral: what it writes in, and draws with when a mark is
   * ABOUT the data rather than a category of it. Warm ink on paper; aged
   * paper on ink.
   */
  ink: string
}

/**
 * Light: aged paper under white cards, with a warm band for inside a card.
 *
 * The canvas is warm at the same luminance as the cool `#f5f8fb` it replaced
 * (0.934 against 0.935), so an elevated white card lifts off it by exactly as
 * much as before -- lift is a luminance difference, and hue was never doing
 * that work. What the cool tint DID do was invert the app's temperature
 * halfway up the stack: a cool canvas, a white card, then a warm band inside
 * it, in an app whose ink, shadows and text are all warm.
 */
const LIGHT: Surfaces = {
  canvas: '#faf7f4',
  paper: '#ffffff',
  muted: '#f1e8e0',
  raised: '#ffffff',
  ink: '#2b2320',
}

/**
 * Dark: the same paper-and-ink, read by lamplight.
 *
 * Every surface sits on one hue, 55 degrees -- warm ink, not a neutral grey
 * and not black -- and they are told apart by lightness alone, as the light
 * theme's are: the canvas is the darkest, a card is a step up from it (1.11:1),
 * a band inside a card another step (1.14:1), and a surface floating over a
 * card one more. The ink is the aged paper the light theme sits on, so the
 * two modes are one object turned over: what the light theme prints on, the
 * dark theme prints with.
 *
 * Chroma stays near 0.012 throughout. A dark ground has to be quiet enough
 * that the element hues -- each a claim about what something IS -- are the
 * only colour on it.
 */
const DARK: Surfaces = {
  canvas: '#17110e',
  paper: '#231d19',
  muted: '#2f2924',
  raised: '#38312b',
  ink: '#f7f3ee',
}

export const SURFACES: Record<ColorMode, Surfaces> = { light: LIGHT, dark: DARK }

/** The app bar in the dark theme: the brand blue, taken down to a band. */
export const APP_BAR_DARK = '#183f73'
