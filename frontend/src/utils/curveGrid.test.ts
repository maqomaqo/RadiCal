import { describe, expect, it } from 'vitest'

import { compactGrid } from './curveGrid'

describe('compactGrid', () => {
  it('reduces IntCal20s four-part grid to its range of steps', () => {
    expect(
      compactGrid('1 yr (0-5,000), 5 yr (5,000-15,000), 10 yr (15,000-25,000), 20 yr (25,000-55,000)')
    ).toBe('1–20 yr steps')
  })

  it('does not invent a range for a curve with one step', () => {
    expect(compactGrid('10 yr (0-55,000)')).toBe('10 yr steps')
  })

  it('handles the irregular form the backend uses for an imported curve', () => {
    expect(compactGrid('0.5-37 yr (irregular)')).toBe('0.5–37 yr steps')
  })

  it('passes anything it cannot read through unchanged, rather than blanking it', () => {
    expect(compactGrid('n/a')).toBe('n/a')
    expect(compactGrid('')).toBe('')
  })
})
