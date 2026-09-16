// The collection table's ordering. Nothing here is written back; what is
// asserted is that a sort by position puts a site in one run of rows, and
// that a date with nothing in the column goes to the bottom whichever way
// the column is sorted.
import { describe, expect, it } from 'vitest'

import { sortSamples } from './sampleSort'
import type { SortableSample } from './sampleSort'

const date = (
  sample_name: string,
  overrides: Partial<SortableSample> = {},
): SortableSample => ({
  sample_name,
  age_bp: 3000,
  error_1sigma: 30,
  default_context_name: null,
  location: null,
  latitude: null,
  longitude: null,
  ...overrides,
})

const names = (samples: SortableSample[]) => samples.map((s) => s.sample_name)

describe('sortSamples', () => {
  it('returns the collection untouched with no sort key', () => {
    const samples = [date('b'), date('a')]
    expect(sortSamples(samples, null, 'asc')).toBe(samples)
  })

  it('collates lab codes numerically', () => {
    const samples = [date('OxA-12'), date('OxA-7'), date('OxA-100')]
    expect(names(sortSamples(samples, 'sample_name', 'asc'))).toEqual(['OxA-7', 'OxA-12', 'OxA-100'])
    expect(names(sortSamples(samples, 'sample_name', 'desc'))).toEqual(['OxA-100', 'OxA-12', 'OxA-7'])
  })

  it('puts every date at one position together, south to north', () => {
    const samples = [
      date('north', { latitude: 21.0, longitude: -157.0 }),
      date('south-a', { latitude: -21.2, longitude: -159.8 }),
      date('middle', { latitude: 0, longitude: 100 }),
      date('south-b', { latitude: -21.2, longitude: -159.8 }),
    ]
    expect(names(sortSamples(samples, 'position', 'asc'))).toEqual([
      'south-a', 'south-b', 'middle', 'north',
    ])
  })

  it('breaks a latitude tie west to east', () => {
    const samples = [
      date('east', { latitude: 10, longitude: 20 }),
      date('west', { latitude: 10, longitude: -20 }),
    ]
    expect(names(sortSamples(samples, 'position', 'asc'))).toEqual(['west', 'east'])
    expect(names(sortSamples(samples, 'position', 'desc'))).toEqual(['east', 'west'])
  })

  it('keeps unplaced dates at the bottom in both directions', () => {
    const samples = [
      date('unplaced'),
      date('north', { latitude: 50, longitude: 0 }),
      date('half', { latitude: 10, longitude: null }),
      date('south', { latitude: -50, longitude: 0 }),
    ]
    expect(names(sortSamples(samples, 'position', 'asc'))).toEqual(['south', 'north', 'unplaced', 'half'])
    expect(names(sortSamples(samples, 'position', 'desc'))).toEqual(['north', 'south', 'unplaced', 'half'])
  })

  it('sorts location names case-insensitively with the nameless last', () => {
    const samples = [
      date('none'),
      date('k-lower', { location: 'kealakekua bay' }),
      date('a', { location: 'Anakena' }),
      date('K-upper', { location: 'Kealakekua Bay' }),
    ]
    expect(names(sortSamples(samples, 'location', 'asc'))).toEqual(['a', 'k-lower', 'K-upper', 'none'])
    expect(names(sortSamples(samples, 'location', 'desc'))).toEqual(['k-lower', 'K-upper', 'a', 'none'])
  })

  it('is stable on ties', () => {
    const samples = [date('first', { age_bp: 100 }), date('second', { age_bp: 100 }), date('older', { age_bp: 200 })]
    expect(names(sortSamples(samples, 'age_bp', 'asc'))).toEqual(['first', 'second', 'older'])
    expect(names(sortSamples(samples, 'age_bp', 'desc'))).toEqual(['older', 'first', 'second'])
  })
})
