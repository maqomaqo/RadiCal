// Round tick positions on a plain linear scale.
//
// The calendar ruler in utils/dateFormat has always needed this, and now the
// age-depth figure needs it for its DEPTH axis too -- a scale with no calendar
// convention in it at all, just centimetres down a core. The rule for "what is
// a round number to put a tick on" is the same either way, so it lives here and
// dateFormat imports it rather than keeping a second copy that could drift into
// choosing different steps for the two axes of the same plot.

/** A round step (1/2/5 x 10^n) giving roughly `target` intervals across `span`. */
export function niceStep(span: number, target: number): number {
  if (!(span > 0)) return 1
  const rough = span / target
  const magnitude = Math.pow(10, Math.floor(Math.log10(rough)))
  for (const multiple of [1, 2, 5]) {
    if (multiple * magnitude >= rough) return multiple * magnitude
  }
  return 10 * magnitude
}

/**
 * Round values covering [min, max], roughly `target` of them.
 *
 * Only ticks INSIDE the range are returned: an axis drawn to the data's own
 * extent has no room to show a tick past its end, and a label half off the
 * plot reads as a value the figure does not contain.
 */
export function linearTicks(min: number, max: number, target = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return []
  const step = niceStep(max - min, target)
  const ticks: number[] = []
  for (let value = Math.ceil(min / step) * step; value <= max + step * 1e-9; value += step) {
    // Re-round: adding a fractional step repeatedly accumulates error, so
    // 0.1 + 0.1 + 0.1 would label a tick "0.30000000000000004".
    ticks.push(Number((Math.round(value / step) * step).toFixed(6)))
  }
  return ticks
}
