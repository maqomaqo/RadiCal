/**
 * How finely to ask for a resolution profile across a window.
 *
 * The calculation is one-year internally whatever is asked for; `stride` only
 * thins what comes back. So this is purely about payload and drawing: a
 * 55,000-year view has no use for 55,000 points, and a 200-year view wants
 * every year it can get, because at that zoom the sharp features -- the
 * 33-year trough at 2,750 cal BP is 15 years wide -- are the whole point.
 */
export function resolutionStride(span: number, pixelWidth = 900): number {
  if (!(span > 0)) return 5
  // About one point per pixel: finer is invisible, coarser is a staircase.
  const perPixel = span / Math.max(pixelWidth, 1)
  if (perPixel <= 1) return 1
  return Math.min(200, Math.max(1, Math.round(perPixel)))
}
