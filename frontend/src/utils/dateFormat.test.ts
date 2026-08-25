// Compact era ranges.
//
// The Dashboard lists several analyses at once, each with its own calendar
// range, and "AD 1189 – AD 1408" spends half its width saying "AD" twice.
// Compact hoists the era out -- but only when there is a single era to
// hoist, which is the case worth pinning down: a range that crosses the
// BC/AD boundary has none, and abbreviating it anyway would rewrite the
// range into a different set of centuries.
import { describe, expect, it } from 'vitest'

import { formatCalendarRange } from './dateFormat'

// cal BP is years before 1950, so AD year = 1950 - calBP.
const calBPForAD = (year: number) => 1950 - year
// No year zero: 1 BC is 1949 cal BP + 1.
const calBPForBC = (year: number) => 1950 + year - 1

describe('formatCalendarRange compact', () => {
  it('names AD once, oldest end first', () => {
    const range = formatCalendarRange(calBPForAD(1189), calBPForAD(1408), 'bcad', {
      compact: true,
    })
    expect(range).toBe('AD 1189–1408')
  })

  it('puts BC after the numbers, where the convention puts it', () => {
    const range = formatCalendarRange(calBPForBC(1250), calBPForBC(850), 'bcad', {
      compact: true,
    })
    expect(range).toBe('1250–850 BC')
  })

  it('uses CE as a suffix rather than a prefix', () => {
    const range = formatCalendarRange(calBPForAD(1189), calBPForAD(1408), 'bce_ce', {
      compact: true,
    })
    expect(range).toBe('1189–1408 CE')
  })

  it('spells both ends out when the range crosses the era boundary', () => {
    // Compacting this would have to pick one era and silently move the
    // other end by two millennia.
    const range = formatCalendarRange(calBPForBC(50), calBPForAD(150), 'bcad', {
      compact: true,
    })
    expect(range).toBe('50 BC – AD 150')
  })

  it('leaves cal BP alone -- it already names its unit once', () => {
    const compact = formatCalendarRange(761, 542, 'calBP', { compact: true })
    expect(compact).toBe('761–542 cal BP')
    expect(compact).toBe(formatCalendarRange(761, 542, 'calBP'))
  })

  it('is opt-in: the default output is unchanged', () => {
    expect(formatCalendarRange(calBPForAD(1189), calBPForAD(1408), 'bcad')).toBe(
      'AD 1189 – AD 1408'
    )
  })

  it('orders oldest-first regardless of argument order', () => {
    const a = formatCalendarRange(600, 400, 'bcad', { compact: true })
    const b = formatCalendarRange(400, 600, 'bcad', { compact: true })
    expect(a).toBe(b)
    expect(a).toBe('AD 1350–1550')
  })
})
