/**
 * The geometry behind the curve browser's time scrubber: the strip under the
 * main plot showing the whole curve with the current window highlighted.
 *
 * Pure and out of the component for the same reason `harrisLayout.ts` is --
 * arithmetic whose failures are invisible in the DOM. A window that drifts a
 * few years per drag, or can be dragged past the end of the curve, or inverts
 * when its handles cross, all render perfectly happily.
 *
 * Two coordinate systems, running in OPPOSITE directions: cal BP increases
 * into the past, while pixels run left to right with OLDEST on the left. Both
 * plots in this app set `autorange: 'reversed'` on the calendar axis so time
 * reads left to right, and a scrubber running the other way would move the
 * view backwards from the drag. That reflection is the source of every
 * mistake these functions exist to prevent.
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
 * this a curve has nothing left to show and the axis labels collide. */
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
 * Force a window to be usable: inside the curve, at least MIN_WINDOW_YEARS
 * wide, and the right way round. Ordering is normalised rather than rejected,
 * since dragging a handle past its partner reads as the two swapping.
 */
export function clampWindow(window: Window, extent: Extent): Window {
  let from = Math.min(window.from, window.to)
  let to = Math.max(window.from, window.to)

  const widest = extent.max - extent.min
  const narrowest = Math.min(MIN_WINDOW_YEARS, widest)

  if (to - from < narrowest) {
    // Grow around the middle, so a window collapsed by dragging one handle
    // does not also jump to where the other is.
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
 * Move a window by a pixel drag across the strip, keeping its width. Sliding
 * into the end of the curve stops rather than squashing: narrowing the view
 * because it hit the edge would change the zoom level unasked.
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
 * `factor` below 1 zooms in. Anchoring on the cursor rather than the centre is
 * what makes wheel-zoom examine the thing being pointed at.
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
 * How many points to ask the backend for, given the plot's width on screen.
 * Two per horizontal pixel, because decimation returns a minimum and a maximum
 * per bucket: one bucket per pixel draws every column's true extent, and more
 * only makes the payload bigger than the screen can show.
 */
export function pointsForWidth(pixelWidth: number): number {
  return Math.max(100, Math.min(5000, Math.round(pixelWidth * 2)))
}
