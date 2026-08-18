/**
 * What a calibration curve is doing at a given calendar age, in words.
 *
 * The number behind this is the local gradient the backend computes over a
 * fixed calendar span (see services/curve_view.py), and it is the single most
 * useful thing to know about a stretch of curve: it governs how tightly
 * anything can be dated there, which is invisible in the calibrated output
 * and impossible to read off the curve's shape at a glance.
 *
 * Kept out of the page because the page imports Plotly, which needs a DOM --
 * the same reason every other piece of assertable arithmetic in this app
 * lives in utils/.
 */

/** How the local gradient reads, in the terms the browser's readout uses. */
export function describeSlope(slope: number): { label: string; detail: string } {

  if (slope < -0.05) {
    return {
      label: 'reversal',
      detail:
        'the curve runs backwards here, so one measurement matches several ' +
        'separate calendar ranges',
    }
  }
  if (slope < 0.35) {
    return {
      label: 'plateau',
      detail: 'nearly flat, so a measurement here spreads over a wide calendar span',
    }
  }
  if (slope < 0.8) {
    return { label: 'shallow', detail: 'flatter than calendar time, so dates here spread' }
  }
  if (slope <= 1.6) {
    return { label: 'ordinary', detail: 'tracking calendar time about one for one' }
  }
  return {
    label: 'steep',
    detail: 'rising faster than calendar time, so dates here calibrate unusually tightly',
  }
}

