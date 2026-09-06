// Which optional columns a table draws. The rule worth pinning down is that an
// untouched column keeps following the data, so a collection that gains its
// first marine date gains a ΔR column without anyone asking for one.
import { describe, expect, it } from 'vitest'

import { columnVisible, parseColumnOverrides } from './columnPreferences'

describe('columnVisible', () => {
  it('follows the data when the reader has expressed no opinion', () => {
    expect(columnVisible({}, 'delta_r', true)).toBe(true)
    expect(columnVisible({}, 'notes', false)).toBe(false)
  })

  it('lets the reader show a column the data does not earn', () => {
    expect(columnVisible({ notes: true }, 'notes', false)).toBe(true)
  })

  it('lets the reader hide a column the data does earn', () => {
    expect(columnVisible({ delta_r: false }, 'delta_r', true)).toBe(false)
  })

  it('keeps each column independent', () => {
    const overrides = { notes: true }
    expect(columnVisible(overrides, 'location', false)).toBe(false)
  })
})

describe('parseColumnOverrides', () => {
  it('reads a stored record', () => {
    expect(parseColumnOverrides('{"notes":true,"location":false}')).toEqual({
      notes: true,
      location: false,
    })
  })

  // Stored values come from an earlier visit and possibly an earlier version of
  // the app, so anything unexpected has to degrade to "no opinion" rather than
  // reach the component.
  it('drops entries that are not booleans', () => {
    expect(parseColumnOverrides('{"notes":"yes","depth_cm":1,"location":true}')).toEqual({
      location: true,
    })
  })

  it('returns nothing for a value that is not an object', () => {
    expect(parseColumnOverrides('[1,2,3]')).toEqual({})
    expect(parseColumnOverrides('null')).toEqual({})
    expect(parseColumnOverrides('"notes"')).toEqual({})
  })
})
