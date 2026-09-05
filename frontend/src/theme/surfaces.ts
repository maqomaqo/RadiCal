/**
 * The three surfaces the app paints on, in one place.
 *
 * They live here rather than only inside `createTheme` because two of them are
 * also needed outside the `sx` prop -- inside a `linear-gradient()` string, or
 * in a plain style object -- where a palette token does not resolve. Those
 * sites used to hold their own copies of the hex values, which is how a token
 * and its duplicate drift apart. Anything needing a surface colour without a
 * theme in hand imports it from here, and the theme reads the same constants.
 */

/**
 * The canvas (`background.default`).
 *
 * Warm, at the same luminance as the cool `#f5f8fb` it replaces (0.934 against
 * 0.935), so an elevated white Card lifts off it by exactly as much as before
 * -- lift is a luminance difference, and hue was never doing that work. What
 * the cool tint DID do was invert the app's temperature halfway up the stack:
 * a cool canvas, a white card, then a warm band inside it, in an app whose
 * ink, shadows and text are all warm. One family now runs the whole way, and
 * the surfaces are told apart by lightness, which is what was separating them
 * in the first place.
 */
export const CANVAS = '#faf7f4'

/**
 * Elevated surfaces (`background.paper`) -- Cards, Dialogs, Menus, and the
 * plot area itself.
 *
 * Deliberately a known, uncoloured white: every fill drawn on a plot is
 * translucent, so tinting what they composite against would desaturate all of
 * them at once.
 */
export const PAPER = '#ffffff'

/**
 * A banded surface INSIDE an elevated one (`background.muted`) -- table header
 * rows, the stat strip under a chart.
 *
 * A step darker than both the canvas and paper, and now of the same warm
 * family as both, so it reads as a band on paper rather than as a hole in it.
 */
export const MUTED = '#f6f0eb'
