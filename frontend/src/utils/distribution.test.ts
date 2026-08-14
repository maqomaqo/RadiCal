// Normalising two distributions onto a common footing.
//
// The bug these pin, measured on the 'SRM SM/C Dune' analysis: the stacked
// plot drew a date's unmodelled calibration behind its posterior and scaled
// both by the POSTERIOR's peak, on the assumption they shared units. They did
// not. Integrating each series over its own grid gave 1.000 for every
// posterior but 1.000 on some prior rows and 10.000 on others, because the
// prior is downsampled by index without rescaling. 16 of the 22 rows drew
// their prior taller than a row, up to 1.69 row heights, painting into the
// row above.
import { describe, expect, it } from 'vitest'

import { integrate, peakOf, toUnitArea, type Distribution } from './distribution'

/** A triangular bump of unit area on a uniform grid of the given step. */
function bump(step: number, points = 21): Distribution {
  const mid = (points - 1) / 2
  const raw: Distribution = Array.from({ length: points }, (_, i) => [
    1000 + i * step,
    Math.max(0, mid - Math.abs(i - mid)),
  ])
  return toUnitArea(raw)
}

describe('integrate', () => {
  it('measures area rather than summing values', () => {
    // A flat 0.1 over 10 units of width is an area of 1, whatever the
    // spacing it is sampled at -- which is the whole distinction the plot
    // was missing.
    const fine: Distribution = Array.from({ length: 11 }, (_, i) => [i, 0.1])
    const coarse: Distribution = [[0, 0.1], [5, 0.1], [10, 0.1]]

    expect(integrate(fine)).toBeCloseTo(1, 10)
    expect(integrate(coarse)).toBeCloseTo(1, 10)
  })

  it('handles a descending axis, as cal BP can be', () => {
    const ascending: Distribution = [[100, 0.2], [110, 0.2]]
    const descending: Distribution = [[110, 0.2], [100, 0.2]]

    expect(integrate(descending)).toBeCloseTo(integrate(ascending), 10)
  })

  it('handles an uneven grid', () => {
    // A downsampled series can carry uneven spacing.
    expect(integrate([[0, 1], [1, 1], [10, 1]])).toBeCloseTo(10, 10)
  })

  it('is zero for a series too short to have width', () => {
    expect(integrate([])).toBe(0)
    expect(integrate([[5, 1]])).toBe(0)
  })
})

describe('toUnitArea', () => {
  it('makes two series comparable regardless of their grid', () => {
    // The same shape sampled at 1-year and 10-year spacing. Before
    // normalising, the coarse one's values were 10x too large relative to
    // the fine one; after, their peaks agree.
    const fine = bump(1)
    const coarse = bump(10)

    expect(integrate(fine)).toBeCloseTo(1, 10)
    expect(integrate(coarse)).toBeCloseTo(1, 10)
  })

  it('reproduces the failure it was written for', () => {
    // A prior normalised as per-point mass on a 10-year grid, against a
    // posterior that is a proper density -- the NZ7806 row.
    const posterior: Distribution = Array.from({ length: 51 }, (_, i) => [
      500 + i * 4.45,
      0.02,
    ])
    const priorAsMass: Distribution = Array.from({ length: 99 }, (_, i) => [
      100 + i * 10,
      0.0435,
    ])

    // As shipped: the prior's peak dwarfs the posterior's, so scaling the
    // prior by the posterior's peak drew it more than twice the row height.
    expect(peakOf(priorAsMass) / peakOf(posterior)).toBeGreaterThan(2)

    // Normalised, the prior is SHORTER, which is right: it is spread over
    // roughly four times the calendar range.
    const ratio = peakOf(toUnitArea(priorAsMass)) / peakOf(toUnitArea(posterior))
    expect(ratio).toBeLessThan(1)
  })

  it('leaves an already-normalised series alone', () => {
    const density = bump(1)
    const again = toUnitArea(density)

    density.forEach(([, p], i) => expect(again[i][1]).toBeCloseTo(p, 12))
  })

  it('returns a degenerate series untouched rather than blanking the row', () => {
    // Something visible and unscaled beats a row that silently loses its
    // curve.
    const single: Distribution = [[100, 5]]
    const zeros: Distribution = [[100, 0], [110, 0]]

    expect(toUnitArea(single)).toEqual(single)
    expect(toUnitArea(zeros)).toEqual(zeros)
    expect(toUnitArea([])).toEqual([])
  })

  it('preserves shape, changing only the scale', () => {
    const raw: Distribution = [[0, 1], [1, 4], [2, 2], [3, 0.5]]
    const normalised = toUnitArea(raw)

    for (let i = 0; i < raw.length; i++) {
      expect(normalised[i][1] / raw[i][1]).toBeCloseTo(normalised[0][1] / raw[0][1], 12)
      expect(normalised[i][0]).toBe(raw[i][0])
    }
  })
})

describe('the row never overflows once both are normalised', () => {
  // What the plot does: scale by whichever of the two is taller, so the
  // taller one exactly fills the band and the other is proportionally
  // shorter. Asserted here because it is the property that stops a prior
  // painting into the row above.
  const CURVE_HEIGHT = 0.78
  const drawnHeights = (posterior: Distribution, prior: Distribution) => {
    const p = toUnitArea(posterior)
    const q = toUnitArea(prior)
    const rowPeak = Math.max(peakOf(p), peakOf(q))
    const scale = rowPeak > 0 ? CURVE_HEIGHT / rowPeak : 0
    return { posterior: peakOf(p) * scale, prior: peakOf(q) * scale }
  }

  it('fits both curves when the prior is broader', () => {
    const drawn = drawnHeights(bump(1, 21), bump(10, 21))

    expect(drawn.posterior).toBeLessThanOrEqual(CURVE_HEIGHT + 1e-9)
    expect(drawn.prior).toBeLessThanOrEqual(CURVE_HEIGHT + 1e-9)
    expect(drawn.posterior).toBeCloseTo(CURVE_HEIGHT, 9)
  })

  it('fits both curves when the prior is SHARPER than the posterior', () => {
    // The case scaling by the posterior alone could never handle: a date the
    // model has spread out, so its unmodelled calibration is the taller of
    // the two.
    const drawn = drawnHeights(bump(10, 21), bump(1, 21))

    expect(drawn.posterior).toBeLessThanOrEqual(CURVE_HEIGHT + 1e-9)
    expect(drawn.prior).toBeCloseTo(CURVE_HEIGHT, 9)
  })

  it('keeps the two areas comparable, which is what the overlay claims', () => {
    // Equal area under a shared scale: that is what makes "shorter and
    // wider" mean the model narrowed the date.
    const p = toUnitArea(bump(1, 41))
    const q = toUnitArea(bump(4, 41))

    expect(integrate(p)).toBeCloseTo(integrate(q), 9)
  })
})
