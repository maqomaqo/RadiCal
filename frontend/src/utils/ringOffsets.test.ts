import { describe, it, expect } from 'vitest'
import {
  offsetsFromRingNumbers,
  referenceFromOffsets,
  seriesSpan,
  shortSeriesWarning,
  SHORT_SERIES_SPAN,
} from './ringOffsets'

// The sign is the whole point of this module: an inverted offset produces a
// tight answer in the wrong place rather than an obviously wrong one, so the
// direction is asserted from both ends rather than only checked for
// consistency.
describe('offsetsFromRingNumbers', () => {
  const series = [
    { id: 'inner', ring: 1 },
    { id: 'middle', ring: 120 },
    { id: 'outer', ring: 250 },
  ]

  it('puts the outermost ring at zero and counts inwards into the past', () => {
    expect(offsetsFromRingNumbers(series)).toEqual({
      inner: 249,
      middle: 130,
      outer: 0,
    })
  })

  it('measures from whichever member is nominated', () => {
    expect(offsetsFromRingNumbers(series, 'middle')).toEqual({
      inner: 119,
      middle: 0,
      outer: -130,
    })
  })

  it('gives the innermost ring the largest offset, not the smallest', () => {
    const offsets = offsetsFromRingNumbers(series)
    // Ring 1 formed first, so it is the oldest, so its offset is the biggest:
    // a member's calendar age is the reference's PLUS its offset.
    expect(offsets.inner).toBeGreaterThan(offsets.middle)
    expect(offsets.middle).toBeGreaterThan(offsets.outer)
  })

  it('handles an empty series without inventing a reference', () => {
    expect(offsetsFromRingNumbers([])).toEqual({})
  })
})

describe('referenceFromOffsets', () => {
  it('finds the member at offset zero', () => {
    expect(
      referenceFromOffsets([
        { id: 'a', ring_offset: 40 },
        { id: 'b', ring_offset: 0 },
      ])
    ).toBe('b')
  })

  it('returns null when no member sits at the reference point', () => {
    // Legitimate: the solved parameter is the age AT offset zero whether or
    // not a sample was taken there.
    expect(referenceFromOffsets([{ id: 'a', ring_offset: 40 }])).toBeNull()
  })
})

describe('seriesSpan', () => {
  it('is the outermost gap minus the innermost', () => {
    expect(seriesSpan([0, 60, 250])).toBe(250)
    expect(seriesSpan([-40, 60])).toBe(100)
  })

  it('is zero when there is nothing to span', () => {
    expect(seriesSpan([10])).toBe(0)
    expect(seriesSpan([null, undefined])).toBe(0)
  })
})

describe('shortSeriesWarning', () => {
  it('warns about a series too short to break a plateau', () => {
    const warning = shortSeriesWarning([0, 20, 40, 60])
    expect(warning).toContain('60 rings')
    expect(warning).toContain('two or more separate ranges')
  })

  it('says that adding samples does not substitute for length', () => {
    // The finding the warning exists for: length decides whether the match
    // resolves at all, count only decides how tightly.
    expect(shortSeriesWarning([0, 10, 20, 30, 40, 50])).toContain(
      'does not fix that'
    )
  })

  it('stays quiet once the series is long enough', () => {
    expect(shortSeriesWarning([0, SHORT_SERIES_SPAN])).toBeNull()
  })

  it('stays quiet when there is no series yet', () => {
    expect(shortSeriesWarning([])).toBeNull()
    expect(shortSeriesWarning([0])).toBeNull()
  })

  it('names the units the gaps were counted in', () => {
    expect(shortSeriesWarning([0, 50], 'varves')).toContain('50 varves')
  })
})
