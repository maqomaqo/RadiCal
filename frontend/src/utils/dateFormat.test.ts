// Compact era ranges.
//
// The Dashboard lists several analyses at once, each with its own calendar
// range, and "AD 1189 – AD 1408" spends half its width saying "AD" twice.
// Compact hoists the era out -- but only when there is a single era to
// hoist, which is the case worth pinning down: a range that crosses the
// BC/AD boundary has none, and abbreviating it anyway would rewrite the
// range into a different set of centuries.
import { describe, expect, it } from 'vitest'

import { calendarScaleTicks, calendarTickMarkup, formatCalendarRange } from './dateFormat'

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

describe('calendarScaleTicks', () => {
  it('graduates BC/AD in round display years, not round cal BP', () => {
    // 3000-2000 cal BP is 1051-51 BC. Rounding in cal BP and converting would
    // put the ticks on 1051 BC and 951 BC; a reader looks for the century.
    const ticks = calendarScaleTicks(2000, 3000, 'bcad')
    expect(ticks.length).toBeGreaterThan(2)
    for (const tick of ticks) {
      expect(Number(tick.value) % 50).toBe(0)
    }
  })

  it('names the era on every tick, which is what makes a number readable', () => {
    const ticks = calendarScaleTicks(2000, 3000, 'bcad')
    expect(ticks.every((tick) => tick.era === 'BC')).toBe(true)
  })

  it('switches era at the era boundary, and never prints a year zero', () => {
    // 2100-1500 cal BP straddles 1 BC / AD 1.
    const ticks = calendarScaleTicks(1500, 2100, 'bcad')
    expect(ticks.some((tick) => tick.era === 'BC')).toBe(true)
    expect(ticks.some((tick) => tick.era === 'AD')).toBe(true)
    expect(ticks.some((tick) => tick.value === '0')).toBe(false)
  })

  it('puts AD before the number and CE after it, as convention does', () => {
    const bcad = calendarScaleTicks(500, 1000, 'bcad')
    const bce = calendarScaleTicks(500, 1000, 'bce_ce')
    expect(bcad.every((tick) => tick.era === 'AD' && tick.eraFirst)).toBe(true)
    expect(bce.every((tick) => tick.era === 'CE' && !tick.eraFirst)).toBe(true)
  })

  it('leaves cal BP unqualified -- the axis title names the unit', () => {
    const ticks = calendarScaleTicks(500, 1500, 'calBP')
    expect(ticks.length).toBeGreaterThan(2)
    expect(ticks.every((tick) => tick.era === null)).toBe(true)
  })

  it('stays inside the range it was asked for, so a clipped axis labels only what it shows', () => {
    for (const format of ['calBP', 'bcad', 'bce_ce'] as const) {
      for (const tick of calendarScaleTicks(620, 880, format)) {
        expect(tick.calBP).toBeGreaterThanOrEqual(620)
        expect(tick.calBP).toBeLessThanOrEqual(880)
      }
    }
  })

  it('returns nothing for a range that is not one', () => {
    expect(calendarScaleTicks(500, 500, 'bcad')).toEqual([])
    expect(calendarScaleTicks(NaN, 900, 'calBP')).toEqual([])
  })
})

describe('calendarTickMarkup', () => {
  const style = { size: 9, color: 'rgba(43,35,32,0.74)' }

  it('demotes the era rather than dropping it', () => {
    const [tick] = calendarScaleTicks(2000, 3000, 'bcad')
    const markup = calendarTickMarkup(tick, style)
    expect(markup).toContain(tick.value)
    expect(markup).toContain('BC')
    expect(markup).toContain('font-size:9px')
  })

  it('leaves a tick with no era as a bare number', () => {
    const [tick] = calendarScaleTicks(500, 1500, 'calBP')
    expect(calendarTickMarkup(tick, style)).toBe(tick.value)
  })
})
