// The curve id the import dialog offers has to be one the backend will
// accept: it is a primary key, it goes in URLs, and every date's curve_id
// stores it, so a mismatch between this rule and schemas/curve.py's would
// show up as a 422 after the user had filled in the whole form.
import { describe, expect, it } from 'vitest'

import { slugify } from './ImportCurveDialog'

// schemas/curve.py's SLUG_PATTERN, verbatim.
const BACKEND_PATTERN = /^[a-z0-9][a-z0-9_-]{1,63}$/

describe('slugify', () => {
  it.each([
    ['IntCal13', 'intcal13'],
    ['IntCal 13', 'intcal-13'],
    ['Marine20 (local)', 'marine20-local'],
    ['  Kudsk et al. 2020  ', 'kudsk-et-al-2020'],
    ['SHCal20.14c', 'shcal20-14c'],
  ])('%s -> %s', (input, expected) => {
    expect(slugify(input)).toBe(expected)
  })

  it('produces ids the backend pattern accepts', () => {
    for (const name of ['IntCal13', 'Marine20 (local)', 'a curve!!', 'Ünïcode Çurve 9']) {
      const slug = slugify(name)
      expect(slug, `${name} -> ${slug}`).toMatch(BACKEND_PATTERN)
    }
  })

  it('never leaves a leading or trailing separator, which the pattern rejects', () => {
    expect(slugify('---weird---')).toBe('weird')
    expect(slugify('(2020)')).toBe('2020')
  })

  it('stays within the column length', () => {
    expect(slugify('x'.repeat(200)).length).toBe(64)
  })
})
