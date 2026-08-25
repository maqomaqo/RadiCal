// The Curve view's arithmetic.
//
// What these pin is the class of failure that renders perfectly: a lobe
// clipped by the frame, a measurement drawn off the left edge, or a date with
// no stated error drawn as an infinitely precise spike. None of those throw,
// and all of them read as data.
import { describe, expect, it } from 'vitest'

import {
  LOBE_FRACTION,
  MEASUREMENT_LOBE_FRACTION,
  NORMAL_SPAN_SIGMA,
  curveAxisRanges,
  curvePlotGeometry,
  lobeFraction,
  normalDensity,
  padSpan,
  spanOf,
  strokeMask,
} from './curvePlotGeometry'

describe('normalDensity', () => {
  it('peaks at the mean, at exactly 1 whatever the error is', () => {
    for (const sigma of [15, 25, 60, 200]) {
      const points = normalDensity(3000, sigma)
      const peak = Math.max(...points.map(([, p]) => p))
      expect(peak).toBeCloseTo(1, 10)
      const atPeak = points.find(([, p]) => p === peak)!
      expect(atPeak[0]).toBeCloseTo(3000, 10)
    }
  })

  it('spans the stated number of sigma either side, in ascending order', () => {
    const points = normalDensity(2500, 30)
    expect(points[0][0]).toBeCloseTo(2500 - NORMAL_SPAN_SIGMA * 30, 10)
    expect(points[points.length - 1][0]).toBeCloseTo(2500 + NORMAL_SPAN_SIGMA * 30, 10)
    const ascending = points.every((p, i) => i === 0 || p[0] > points[i - 1][0])
    expect(ascending).toBe(true)
  })

  it('is symmetric about the mean', () => {
    const points = normalDensity(0, 20)
    for (let i = 0; i < points.length; i++) {
      const mirrored = points[points.length - 1 - i]
      expect(points[i][1]).toBeCloseTo(mirrored[1], 10)
    }
  })

  it('has the shape of a normal density, not just the right ends', () => {
    // One sigma out is exp(-1/2) of the peak; two sigma out is exp(-2).
    const points = normalDensity(1000, 50)
    const at = (value: number) =>
      points.reduce((best, p) =>
        Math.abs(p[0] - value) < Math.abs(best[0] - value) ? p : best
      )[1]
    expect(at(1050)).toBeCloseTo(Math.exp(-0.5), 3)
    expect(at(1100)).toBeCloseTo(Math.exp(-2), 3)
  })

  it('draws nothing for a date with no usable error, rather than a spike', () => {
    expect(normalDensity(3000, 0)).toEqual([])
    expect(normalDensity(3000, -10)).toEqual([])
    expect(normalDensity(3000, NaN)).toEqual([])
    expect(normalDensity(NaN, 25)).toEqual([])
  })
})

describe('spanOf', () => {
  it('is the tightest interval containing every value', () => {
    expect(spanOf([3, 1, 2])).toEqual({ min: 1, max: 3 })
  })

  it('ignores values that are not finite rather than poisoning the span', () => {
    expect(spanOf([10, NaN, 20, Infinity])).toEqual({ min: 10, max: 20 })
  })

  it('is null when there is nothing to bound, rather than an inverted span', () => {
    expect(spanOf([])).toBeNull()
    expect(spanOf([NaN])).toBeNull()
  })
})

describe('padSpan', () => {
  it('widens by a fraction of the span', () => {
    expect(padSpan({ min: 0, max: 100 }, 0.1, 0)).toEqual({ min: -10, max: 110 })
  })

  it('falls back to the floor when a percentage would be too small to see', () => {
    // A twenty-year posterior: 4% of it is under a year.
    expect(padSpan({ min: 1000, max: 1020 }, 0.04, 25)).toEqual({ min: 975, max: 1045 })
  })
})

describe('curveAxisRanges', () => {
  const ranges = curveAxisRanges({
    calendar: { min: 2000, max: 3000 },
    radiocarbon: { min: 2400, max: 2900 },
  })

  it('leaves room above the highest measurement for its lobe', () => {
    // The lobe rises from the topmost radiocarbon age, so the axis has to
    // extend past it by at least the lobe's own height.
    const ySpan = ranges.y.max - ranges.y.min
    expect(ranges.y.max - 2900).toBeGreaterThanOrEqual(ySpan * LOBE_FRACTION)
  })

  it('leaves room on the older side for the measurement lobes', () => {
    const xSpan = ranges.x.max - ranges.x.min
    expect(ranges.x.max - 3000).toBeGreaterThanOrEqual(xSpan * MEASUREMENT_LOBE_FRACTION)
  })

  it('contains everything it was given', () => {
    expect(ranges.x.min).toBeLessThan(2000)
    expect(ranges.y.min).toBeLessThan(2400)
  })

  it('returns a usable frame with nothing to draw, rather than throwing', () => {
    const empty = curveAxisRanges({ calendar: null, radiocarbon: null })
    expect(empty.x.max).toBeGreaterThan(empty.x.min)
    expect(empty.y.max).toBeGreaterThan(empty.y.min)
  })

  it('does not collapse when every date sits at one age', () => {
    const flat = curveAxisRanges({
      calendar: { min: 2500, max: 2500 },
      radiocarbon: { min: 2600, max: 2600 },
    })
    expect(flat.x.max).toBeGreaterThan(flat.x.min)
    expect(flat.y.max).toBeGreaterThan(flat.y.min)
  })
})

describe('curvePlotGeometry', () => {
  const HEIGHTS = [320, 460, 560, 720, 1000]

  it('puts the legend the same distance below the plot at every height', () => {
    const gaps = HEIGHTS.map((h) => {
      const g = curvePlotGeometry(h)
      return -g.legendY * g.plotAreaHeight
    })
    for (const gap of gaps) {
      expect(gap).toBeCloseTo(gaps[0], 6)
    }
  })

  it('leaves room for the tick labels and the axis title above the legend', () => {
    const g = curvePlotGeometry(560)
    const legendTop = -g.legendY * g.plotAreaHeight
    expect(legendTop).toBeGreaterThan(g.titleStandoff)
    // And the legend itself fits inside the bottom margin rather than being
    // clipped by the edge of the figure.
    expect(g.marginBottom).toBeGreaterThan(legendTop)
  })

  it('never gives the plot area a non-positive height, however short the figure', () => {
    expect(curvePlotGeometry(10).plotAreaHeight).toBeGreaterThan(0)
  })
})

describe('strokeMask', () => {
  it('drops the stretches where a lobe is flat on its baseline', () => {
    const probs = [0, 0, 0.5, 1, 0.5, 0, 0, 0]
    expect(strokeMask(probs, 1)).toEqual([false, true, true, true, true, true, false, false])
  })

  it('keeps the point either side of a visible run, so the stroke reaches the foot', () => {
    // Index 1 is below the threshold but adjoins the peak, so it stays.
    const mask = strokeMask([0, 0.001, 1, 0.001, 0], 1)
    expect(mask).toEqual([false, true, true, true, false])
  })

  it('keeps both peaks of a bimodal posterior and drops the gap between them', () => {
    const probs = [0, 1, 0, 0, 0, 0, 0, 1, 0]
    const mask = strokeMask(probs, 1)
    expect(mask[1]).toBe(true)
    expect(mask[7]).toBe(true)
    expect(mask.slice(3, 6)).toEqual([false, false, false])
  })

  it('strokes nothing for a lobe with no height at all', () => {
    expect(strokeMask([0, 0, 0], 0)).toEqual([false, false, false])
  })
})

describe('lobeFraction', () => {
  it('gives a small model the full lobe height', () => {
    for (const count of [1, 4, 12, 20]) {
      expect(lobeFraction(count)).toBe(LOBE_FRACTION)
    }
  })

  it('shrinks the lobes rather than letting a large model fill the figure', () => {
    expect(lobeFraction(63)).toBeLessThan(LOBE_FRACTION)
    expect(lobeFraction(200)).toBeLessThan(lobeFraction(63))
  })

  it('never spends more than the crowding budget in total', () => {
    for (const count of [1, 5, 20, 40, 63, 150]) {
      expect(count * lobeFraction(count)).toBeLessThanOrEqual(2.5 + 1e-9)
    }
  })

  it('never returns a height of zero or less, however many dates there are', () => {
    expect(lobeFraction(5000)).toBeGreaterThan(0)
    expect(lobeFraction(0)).toBe(LOBE_FRACTION)
  })
})
