// Sorting a draft's dates.
//
// The rules worth pinning are the ones that make a tidy-up trustworthy rather
// than merely reordered:
//
//  - "oldest first" means HIGHEST BP. Radiocarbon ages count backwards, so a
//    plain ascending sort would put the youngest date at the top of a list
//    labelled oldest-first, which is worse than no sort at all.
//  - Lab codes sort numerically. OxA-9 before OxA-10 is the specific thing a
//    user reaches for this to fix; plain string order gets it backwards.
//  - The sort is stable and idempotent, because it rewrites the order that
//    reaches the results table and the exports. Re-sorting by the same key
//    must be a no-op, not a reshuffle of ties.
//  - A date with no usable age goes last in BOTH directions. Treating a
//    missing measurement as zero would let a broken row claim to be the
//    youngest date in the model.
import { describe, expect, it } from 'vitest'

import { isSortedBy, sortMeasurements } from './sortMeasurements'

const date = (id: string, sample_name: string, age_bp: number) => ({
  id,
  sample_name,
  age_bp,
  error_1sigma: 30,
})

const names = (items: { sample_name: string }[]) => items.map((i) => i.sample_name)

describe('sortMeasurements', () => {
  const dates = [
    date('b', 'OxA-10', 2400),
    date('a', 'OxA-9', 3100),
    date('c', 'OxA-2', 1800),
  ]

  it('puts the highest BP first for oldest-first', () => {
    expect(names(sortMeasurements(dates, 'age_desc'))).toEqual(['OxA-9', 'OxA-10', 'OxA-2'])
  })

  it('puts the lowest BP first for youngest-first', () => {
    expect(names(sortMeasurements(dates, 'age_asc'))).toEqual(['OxA-2', 'OxA-10', 'OxA-9'])
  })

  it('sorts lab codes numerically, not as strings', () => {
    expect(names(sortMeasurements(dates, 'name_asc'))).toEqual(['OxA-2', 'OxA-9', 'OxA-10'])
    expect(names(sortMeasurements(dates, 'name_desc'))).toEqual(['OxA-10', 'OxA-9', 'OxA-2'])
  })

  it('does not mutate the input', () => {
    const input = [...dates]
    sortMeasurements(input, 'age_desc')
    expect(input).toEqual(dates)
  })

  it('keeps equal ages in their existing order', () => {
    const tied = [date('x', 'Beta-3', 2000), date('y', 'Beta-1', 2000), date('z', 'Beta-2', 2000)]
    expect(names(sortMeasurements(tied, 'age_desc'))).toEqual(['Beta-3', 'Beta-1', 'Beta-2'])
    expect(names(sortMeasurements(tied, 'age_asc'))).toEqual(['Beta-3', 'Beta-1', 'Beta-2'])
  })

  it('is idempotent', () => {
    const once = sortMeasurements(dates, 'age_desc')
    expect(names(sortMeasurements(once, 'age_desc'))).toEqual(names(once))
  })

  it('holds dates with no usable age at the end in both directions', () => {
    const withGap = [
      date('a', 'Good-1', 2000),
      { id: 'b', sample_name: 'Broken', age_bp: NaN, error_1sigma: 30 },
      date('c', 'Good-2', 3000),
    ]
    expect(names(sortMeasurements(withGap, 'age_desc'))).toEqual(['Good-2', 'Good-1', 'Broken'])
    expect(names(sortMeasurements(withGap, 'age_asc'))).toEqual(['Good-1', 'Good-2', 'Broken'])
  })

  it('handles empty and single-date lists', () => {
    expect(sortMeasurements([], 'age_desc')).toEqual([])
    expect(names(sortMeasurements([dates[0]], 'name_asc'))).toEqual(['OxA-10'])
  })
})

describe('isSortedBy', () => {
  const dates = [date('a', 'OxA-9', 3100), date('b', 'OxA-10', 2400), date('c', 'OxA-2', 1800)]

  it('recognises a list already in the requested order', () => {
    expect(isSortedBy(dates, 'age_desc')).toBe(true)
    expect(isSortedBy(dates, 'age_asc')).toBe(false)
  })

  it('treats an empty list as sorted', () => {
    expect(isSortedBy([], 'age_desc')).toBe(true)
  })

  // Ties make more than one arrangement correct, and the stable sort keeps
  // whichever one is in hand -- so a tied list must not read as "needs
  // sorting" and offer a button that would do nothing.
  it('counts a tied list as sorted whatever order the ties are in', () => {
    const tied = [date('x', 'B', 2000), date('y', 'A', 2000)]
    expect(isSortedBy(tied, 'age_desc')).toBe(true)
  })
})
