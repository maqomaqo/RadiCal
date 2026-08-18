/**
 * The geometry behind the curve browser's time scrubber: the strip under the
 * main plot that shows the whole 55,000-year curve with the current window
 * highlighted, and that is dragged to move it.
 *
 * Kept out of the component and made pure for the same reason
 * `harrisLayout.ts` is: this is arithmetic whose failures are invisible in the
 * DOM. A window that drifts by a few years each time it is dragged, or that
 * can be dragged past the end of the curve, or that inverts when its handles
 * cross, all render perfectly happily. Only asserting the numbers catches them.
 *
 * Everything here works in two coordinate systems and converts between them:
 *
 *   cal BP   what the curve is in, increasing into the past
 *   pixels   left to right across the strip, OLDEST on the left
 *
 * so the two run in OPPOSITE directions. That is not a preference: both
 * existing plots in this app set `autorange: 'reversed'` on the calendar
 * axis, which puts older on the left and lets time read left to right, and a
 * scrubber running the other way would move the view backwards from the
 * direction it was dragged. It is also the source of every
 * off-by-a-reflection mistake these functions exist to prevent, which is why
 * the direction is stated here and asserted in the tests.
 */

export interface Extent {
  /** The whole curve, in cal BP. */
  min: number
  max: number
}

export interface Window {
  from: number
  to: number
}

/** The narrowest window the browser will zoom to, in calendar years. Below
 * this a calibration curve has nothing left to show -- even the 1-year part
 * of IntCal20 is scatter at this scale -- and the axis labels collide. */
export const MIN_WINDOW_YEARS = 20

/** Where a cal BP value sits across a strip of the given width, oldest left. */
export function calToPixel(cal: number, extent: Extent, width: number): number {
  const span = extent.max - extent.min
  if (!(span > 0) || !(width > 0)) return 0
  return ((extent.max - cal) / span) * width
}

/** The cal BP value at a pixel position, oldest left. */
export function pixelToCal(pixel: number, extent: Extent, width: number): number {
  const span = extent.max - extent.min
  if (!(span > 0) || !(width > 0)) return extent.min
  return extent.max - (pixel / width) * span
}

/**
 * Force a window to be a usable one: inside the curve, at least
 * MIN_WINDOW_YEARS wide, and the right way round.
 *
 * Ordering is normalised rather than rejected because dragging a handle past
 * its partner is a thing people do, and the sensible reading of it is that
 * the two swapped, not that the gesture was invalid.
 */
export function clampWindow(window: Window, extent: Extent): Window {
  let from = Math.min(window.from, window.to)
  let to = Math.max(window.from, window.to)

  const widest = extent.max - extent.min
  const narrowest = Math.min(MIN_WINDOW_YEARS, widest)

  if (to - from < narrowest) {
    // Grow around the middle, so a window collapsed by dragging one handle
    // does not also jump to where the other one is.
    const middle = (from + to) / 2
    from = middle - narrowest / 2
    to = middle + narrowest / 2
  }

  const span = Math.min(to - from, widest)
  if (from < extent.min) {
    from = extent.min
    to = from + span
  }
  if (to > extent.max) {
    to = extent.max
    from = to - span
  }
  return { from: Math.max(from, extent.min), to: Math.min(to, extent.max) }
}

/**
 * Move a window by a pixel drag across the strip, keeping its width.
 *
 * Sliding into the end of the curve stops rather than squashing: a pan is a
 * pan, and silently narrowing the view because it hit the edge would change
 * the zoom level without being asked to.
 */
export function panByPixels(
  window: Window,
  extent: Extent,
  width: number,
  deltaPixels: number
): Window {
  const span = window.to - window.from
  // Negated: dragging right moves toward the present, which is DOWN in cal BP.
  const deltaCal = -(deltaPixels / width) * (extent.max - extent.min)
  let from = window.from + deltaCal
  if (from < extent.min) from = extent.min
  if (from + span > extent.max) from = extent.max - span
  return { from, to: from + span }
}

/**
 * Zoom about a fixed calendar age -- the point under the cursor stays put.
 *
 * `factor` below 1 zooms in. Anchoring on the cursor rather than the centre
 * is what makes wheel-zoom feel like it is examining the thing being pointed
 * at instead of the middle of the screen.
 */
export function zoomAbout(
  window: Window,
  extent: Extent,
  anchorCal: number,
  factor: number
): Window {
  const span = window.to - window.from
  const nextSpan = Math.min(span * factor, extent.max - extent.min)
  const position = span > 0 ? (anchorCal - window.from) / span : 0.5
  const from = anchorCal - position * nextSpan
  return clampWindow({ from, to: from + nextSpan }, extent)
}

/** The highlighted rectangle for a window, in pixels across the strip. */
export function windowRect(
  window: Window,
  extent: Extent,
  width: number
): { x: number; width: number } {
  const left = calToPixel(window.from, extent, width)
  const right = calToPixel(window.to, extent, width)
  return { x: Math.min(left, right), width: Math.max(1, Math.abs(right - left)) }
}

/**
 * How many points to ask the backend for, given how wide the plot is on
 * screen.
 *
 * Two per horizontal pixel, because the decimation returns a minimum and a
 * maximum per bucket: one bucket per pixel is exactly enough to draw every
 * pixel column's true extent, and asking for more only makes the payload
 * bigger than the screen can show.
 */
export function pointsForWidth(pixelWidth: number): number {
  return Math.max(100, Math.min(5000, Math.round(pixelWidth * 2)))
}
