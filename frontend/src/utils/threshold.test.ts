import { describe, expect, it } from 'vitest'
import { formatCalendarAge } from './dateFormat'
import { formatProbability, parseCalendarAge } from './threshold'

describe('parseCalendarAge', () => {
  it('reads the era named in the text', () => {
    expect(parseCalendarAge('AD 1360', 'calBP')).toBe(590)
    expect(parseCalendarAge('1360 ce', 'calBP')).toBe(590)
    expect(parseCalendarAge('590 cal BP', 'bcad')).toBe(590)
    expect(parseCalendarAge('590 BP', 'bcad')).toBe(590)
    expect(parseCalendarAge('500 BC', 'calBP')).toBe(2449)
    expect(parseCalendarAge('500 B.C.E.', 'calBP')).toBe(2449)
  })

  it("reads a bare number in the page's format", () => {
    expect(parseCalendarAge('1360', 'bcad')).toBe(590)
    expect(parseCalendarAge('1360', 'bce_ce')).toBe(590)
    expect(parseCalendarAge('590', 'calBP')).toBe(590)
  })

  it('round-trips through the formatter', () => {
    for (const format of ['calBP', 'bcad', 'bce_ce'] as const) {
      for (const calBP of [590, 2449, 1949, 1950, 5000]) {
        expect(parseCalendarAge(formatCalendarAge(calBP, format), format)).toBe(calBP)
      }
    }
  })

  it('refuses what it cannot read', () => {
    for (const text of ['', 'soon', 'AD', 'AD 1360 BC', '0 BC', 'AD 0', '1360 xyz']) {
      expect(parseCalendarAge(text, 'bcad')).toBeNull()
    }
  })
})

describe('formatProbability', () => {
  it('prints three places without rounding to certainty', () => {
    expect(formatProbability(0.947)).toBe('0.947')
    expect(formatProbability(0.9996)).toBe('>0.999')
    expect(formatProbability(0.0002)).toBe('<0.001')
    expect(formatProbability(1)).toBe('1')
    expect(formatProbability(0)).toBe('0')
    expect(formatProbability(null)).toBe('')
  })
})
