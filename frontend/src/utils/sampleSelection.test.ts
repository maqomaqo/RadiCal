// Selecting a site rather than a date at a time: a run of rows between two
// clicks, and the widening of a selection to everything filed under the
// same name or lying within a radius.
import { describe, expect, it } from 'vitest'

import {
  datesAtSameLocation,
  datesWithinKm,
  locationKey,
  placedCount,
  rangeBetween,
  selectedLocations,
} from './sampleSelection'
import type { SiteDate } from './sampleSelection'

const ids = (dates: SiteDate[]) => dates.map((d) => d.id)

describe('rangeBetween', () => {
  const order = ['a', 'b', 'c', 'd', 'e']

  it('runs from anchor to target inclusive, either way round', () => {
    expect(rangeBetween(order, 'b', 'd')).toEqual(['b', 'c', 'd'])
    expect(rangeBetween(order, 'd', 'b')).toEqual(['b', 'c', 'd'])
  })

  it('is the one row when both ends are the same', () => {
    expect(rangeBetween(order, 'c', 'c')).toEqual(['c'])
  })

  it('is nothing when an end is not on the table', () => {
    expect(rangeBetween(order, 'gone', 'c')).toEqual([])
    expect(rangeBetween(order, 'c', 'gone')).toEqual([])
  })
})

describe('locationKey', () => {
  it('folds case and whitespace, and is null for nothing', () => {
    expect(locationKey('  Kealakekua   Bay ')).toBe('kealakekua bay')
    expect(locationKey('kealakekua bay')).toBe('kealakekua bay')
    expect(locationKey('')).toBeNull()
    expect(locationKey('   ')).toBeNull()
    expect(locationKey(null)).toBeNull()
    expect(locationKey(undefined)).toBeNull()
  })
})

describe('selectedLocations', () => {
  it('lists each distinct name once, first spelling, in order seen', () => {
    expect(
      selectedLocations([
        { id: '1', location: 'Kealakekua Bay' },
        { id: '2', location: null },
        { id: '3', location: 'kealakekua bay' },
        { id: '4', location: 'Anakena' },
      ]),
    ).toEqual(['Kealakekua Bay', 'Anakena'])
  })
})

describe('datesAtSameLocation', () => {
  const collection: SiteDate[] = [
    { id: 'k1', location: 'Kealakekua Bay' },
    { id: 'a1', location: 'Anakena' },
    { id: 'k2', location: 'kealakekua bay' },
    { id: 'n1', location: null },
    { id: 'n2', location: '' },
    { id: 'a2', location: 'Anakena' },
  ]

  it('widens to every date under the same name, in collection order', () => {
    expect(ids(datesAtSameLocation(collection, [collection[2]]))).toEqual(['k1', 'k2'])
  })

  it('takes the union over several selected names', () => {
    expect(ids(datesAtSameLocation(collection, [collection[0], collection[5]]))).toEqual([
      'k1', 'a1', 'k2', 'a2',
    ])
  })

  it('never matches nameless dates to each other, but keeps a nameless selected date', () => {
    expect(ids(datesAtSameLocation(collection, [collection[3]]))).toEqual(['n1'])
    expect(ids(datesAtSameLocation(collection, [collection[3], collection[1]]))).toEqual(['a1', 'n1', 'a2'])
  })
})

describe('datesWithinKm', () => {
  // Rarotonga, about 3 km apart, and Aitutaki 260 km north.
  const collection: SiteDate[] = [
    { id: 'raro-a', latitude: -21.2, longitude: -159.78 },
    { id: 'unplaced', latitude: null, longitude: null },
    { id: 'raro-b', latitude: -21.23, longitude: -159.78 },
    { id: 'aitutaki', latitude: -18.85, longitude: -159.79 },
    { id: 'raro-a-again', latitude: -21.2, longitude: -159.78 },
  ]

  it('widens to every placed date within the radius of any selected one', () => {
    expect(ids(datesWithinKm(collection, [collection[0]], 5))).toEqual(['raro-a', 'raro-b', 'raro-a-again'])
    expect(ids(datesWithinKm(collection, [collection[0]], 300))).toEqual([
      'raro-a', 'raro-b', 'aitutaki', 'raro-a-again',
    ])
  })

  it('treats zero as the same coordinate exactly', () => {
    expect(ids(datesWithinKm(collection, [collection[0]], 0))).toEqual(['raro-a', 'raro-a-again'])
  })

  it('keeps an unplaced selected date and matches nothing from it', () => {
    expect(ids(datesWithinKm(collection, [collection[1]], 5))).toEqual(['unplaced'])
    expect(ids(datesWithinKm(collection, [collection[1], collection[3]], 5))).toEqual(['unplaced', 'aitutaki'])
  })

  it('widens nothing on a radius that is not a distance', () => {
    expect(ids(datesWithinKm(collection, [collection[0]], Number.NaN))).toEqual(['raro-a'])
    expect(ids(datesWithinKm(collection, [collection[0]], -1))).toEqual(['raro-a'])
  })
})

describe('placedCount', () => {
  it('counts dates with both halves of a position', () => {
    expect(placedCount([
      { id: '1', latitude: 1, longitude: 2 },
      { id: '2', latitude: 1, longitude: null },
      { id: '3' },
    ])).toBe(1)
  })
})
