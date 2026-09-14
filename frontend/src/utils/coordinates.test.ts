// Coordinates as the app shows them. The ROUNDING is the server's -- see
// api/results._rounded_coordinate -- so what is checked here is the naming
// and the both-or-neither rule the UI has to honour to match it.
import { describe, expect, it } from 'vitest'

import {
  COORDINATE_PRECISION_OPTIONS,
  formatPosition,
  type CoordinatePrecision,
} from './coordinates'

describe('formatPosition', () => {
  it('writes a placed date as one string', () => {
    expect(formatPosition(54.5, -5.6)).toBe('54.5, -5.6')
  })

  it('shows nothing for an unplaced date', () => {
    expect(formatPosition(null, null)).toBeNull()
    expect(formatPosition(undefined, undefined)).toBeNull()
  })

  it('shows nothing for half a coordinate', () => {
    // The API refuses half a position, so a row carrying one is a row
    // something has gone wrong with. Printing the lone number as if it were a
    // position would hide that.
    expect(formatPosition(54.5, null)).toBeNull()
    expect(formatPosition(null, -5.6)).toBeNull()
  })

  it('keeps a zero, which is a place and not an absence', () => {
    expect(formatPosition(0, 0)).toBe('0, 0')
  })
})

describe('the precision options', () => {
  it('offer the four values the backend stores', () => {
    const values = COORDINATE_PRECISION_OPTIONS.map((o) => o.value)
    const expected: CoordinatePrecision[] = ['full', '0.01', '0.1', '1']
    expect(values).toEqual(expected)
  })

  it('name each one in distance, not only in degrees', () => {
    // "0.1 degrees" says nothing about how much of a landscape it hides,
    // which is the whole question the setting asks.
    for (const option of COORDINATE_PRECISION_OPTIONS.slice(1)) {
      expect(option.label).toMatch(/km/)
    }
  })
})
