import { describe, expect, it } from 'vitest'

import { interpolateAt } from './seriesLookup'

// [x, a, b]
const SERIES = [
  [0, 10, 100],
  [10, 20, 200],
  [20, 40, 400],
]

describe('interpolateAt', () => {
  it('reads a value between samples rather than snapping to one', () => {
    // The whole point: the nearest sample would say 20, the line says 30.
    expect(interpolateAt(SERIES, 15, 1)).toBe(30)
  })

  it('reads any column', () => {
    expect(interpolateAt(SERIES, 15, 2)).toBe(300)
  })

  it('is exact on the samples themselves', () => {
    for (const [x, a] of SERIES) {
      expect(interpolateAt(SERIES, x, 1)).toBe(a)
    }
  })

  it('clamps rather than extrapolating past the ends', () => {
    expect(interpolateAt(SERIES, -50, 1)).toBe(10)
    expect(interpolateAt(SERIES, 500, 1)).toBe(40)
  })

  it('handles a series running the other way, as a reversed axis produces', () => {
    const descending = [...SERIES].reverse()
    expect(interpolateAt(descending, 15, 1)).toBe(30)
    expect(interpolateAt(descending, -50, 1)).toBe(10)
    expect(interpolateAt(descending, 500, 1)).toBe(40)
  })

  it('copes with degenerate input instead of returning NaN', () => {
    expect(interpolateAt([], 5, 1)).toBeNull()
    expect(interpolateAt([[3, 7]], 5, 1)).toBe(7)
    expect(interpolateAt([[0, 1], [0, 9]], 0, 1)).toBe(1)
  })
})
