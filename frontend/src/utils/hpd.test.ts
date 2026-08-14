// Client-side HPD selection.
//
// These went in alongside making CalibrationCurve.get_display_distribution
// return a density rather than per-point masses. That change is right --
// the series is plotted against a calendar axis, where only a density means
// anything -- but it moved the goalposts here: summing the values was correct
// while they were masses, and stops being correct once they are densities on
// a grid that is not uniformly spaced. IntCal20 and SHCal20 step 1, 5, 10 and
// 20 years in different ranges, so this is reachable with real data.
//
// The property, then: rank by density (the region wanted is the
// highest-density one) but accumulate mass (what a confidence level is a
// share of). On a uniform grid the two coincide, which is why nothing caught
// it before.
import { describe, expect, it } from 'vitest'

import { hpdEnvelopeFromHistogram, hpdRangesFromCurve } from './hpd'

type Series = Array<[number, number]>

/** A flat density over [from, to] sampled at `step`. */
function flat(from: number, to: number, step: number, density: number): Series {
  const out: Series = []
  for (let x = from; x <= to + 1e-9; x += step) out.push([x, density])
  return out
}

describe('hpdEnvelopeFromHistogram', () => {
  it('returns the whole range at 100% confidence', () => {
    const envelope = hpdEnvelopeFromHistogram(flat(0, 100, 10, 0.01), 1)

    // Widened by half a bin at each end: a point is the centre of the width
    // it stands for, not its edge.
    expect(envelope).toEqual([-5, 105])
  })

  it('picks the peak, not the edges', () => {
    const series: Series = [
      [0, 0.001], [10, 0.001], [20, 0.05], [30, 0.05], [40, 0.001], [50, 0.001],
    ]
    const [min, max] = hpdEnvelopeFromHistogram(series, 0.8)!

    expect(min).toBeGreaterThanOrEqual(10)
    expect(max).toBeLessThanOrEqual(40)
  })

  it('is unchanged by rescaling the whole series', () => {
    // Confidence is a share, so a constant factor must not move the answer.
    const series: Series = [[0, 0.01], [10, 0.04], [20, 0.02], [30, 0.005]]
    const scaled: Series = series.map(([x, y]) => [x, y * 1000])

    expect(hpdEnvelopeFromHistogram(scaled, 0.68))
      .toEqual(hpdEnvelopeFromHistogram(series, 0.68))
  })

  it('weights a coarse stretch by the years it covers, not its point count', () => {
    // The regression the density change could have introduced. Two halves of
    // equal probability mass: the left sampled every 1 year, the right every
    // 10. Counting points would give the left ten times the weight and pull
    // the region entirely into it.
    const fine = flat(0, 99, 1, 0.005)      // mass 0.5 over 100 years
    const coarse = flat(100, 199, 10, 0.005) // mass 0.5 over 100 years
    const series = [...fine, ...coarse]

    const [min, max] = hpdEnvelopeFromHistogram(series, 0.9)!

    // 90% of a symmetric-mass distribution has to reach well into the
    // coarsely-sampled half.
    expect(max).toBeGreaterThan(150)
    expect(min).toBeLessThan(50)
  })

  it('returns null for a series too short to have an envelope', () => {
    expect(hpdEnvelopeFromHistogram([], 0.95)).toBeNull()
    expect(hpdEnvelopeFromHistogram([[10, 1]], 0.95)).toBeNull()
    expect(hpdEnvelopeFromHistogram(null, 0.95)).toBeNull()
    expect(hpdEnvelopeFromHistogram(flat(0, 50, 10, 0), 0.95)).toBeNull()
  })
})

describe('hpdRangesFromCurve', () => {
  it('splits a bimodal distribution into separate ranges', () => {
    // The reason this variant exists: a calibration curve is non-monotonic,
    // so one measurement can map to several disjoint calendar peaks, and
    // collapsing them into one span implies mass across the empty gap.
    const series: Series = [
      [0, 0.05], [10, 0.05],
      [20, 0.0001], [30, 0.0001], [40, 0.0001],
      [50, 0.05], [60, 0.05],
    ]
    const ranges = hpdRangesFromCurve(series, 0.9)

    expect(ranges).toHaveLength(2)
    expect(ranges.every((r) => r.probability_percent > 0)).toBe(true)
  })

  it('reports shares that add up', () => {
    const ranges = hpdRangesFromCurve(flat(0, 100, 10, 0.01), 0.95)
    const total = ranges.reduce((sum, r) => sum + r.probability_percent, 0)

    expect(total).toBeGreaterThan(94)
    expect(total).toBeLessThanOrEqual(100.001)
  })

  it('orders ranges youngest-last, as the results table reads them', () => {
    const series: Series = [
      [0, 0.05], [10, 0.05], [20, 0.0001], [30, 0.05], [40, 0.05],
    ]
    const ranges = hpdRangesFromCurve(series, 0.9)

    for (let i = 0; i < ranges.length - 1; i++) {
      expect(ranges[i].max).toBeGreaterThan(ranges[i + 1].max)
    }
  })

  it('is unchanged by rescaling the whole series', () => {
    const series: Series = [[0, 0.01], [10, 0.04], [20, 0.02], [30, 0.005]]
    const scaled: Series = series.map(([x, y]) => [x, y * 250])

    expect(hpdRangesFromCurve(scaled, 0.68)).toEqual(hpdRangesFromCurve(series, 0.68))
  })

  it('is empty for nothing to select from', () => {
    expect(hpdRangesFromCurve([], 0.95)).toEqual([])
    expect(hpdRangesFromCurve(null, 0.95)).toEqual([])
    expect(hpdRangesFromCurve(flat(0, 50, 10, 0), 0.95)).toEqual([])
  })
})
