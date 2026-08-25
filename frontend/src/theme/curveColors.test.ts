// A calibration curve's appearance is a property of the curve, not of where it
// happens to sit in a list.
//
// The bug these pin: styles were handed out by position, so SHCal20 was the
// solid blue line in an analysis that ran it alone and the grey dashed one in
// an analysis that also ran Marine20. Nothing about the curve had changed. The
// properties worth holding are therefore about STABILITY -- same curve, same
// style, whatever else is on the figure -- and about the one case where
// stability has to give way, which is two curves that would otherwise be drawn
// identically.
import { describe, expect, it } from 'vitest'

import { CURVE_PALETTE_SIZE, curveSlot, curveStyles, styleForSlot } from './curveColors'

const BUILTINS = ['intcal20', 'shcal20', 'marine20']

describe('curveStyles', () => {
  it('gives a curve the same style whatever else is drawn with it', () => {
    const alone = curveStyles(['shcal20']).shcal20
    const withMarine = curveStyles(['shcal20', 'marine20']).shcal20
    const afterMarine = curveStyles(['marine20', 'shcal20']).shcal20
    const withBoth = curveStyles(['intcal20', 'marine20', 'shcal20']).shcal20

    expect(withMarine).toEqual(alone)
    expect(afterMarine).toEqual(alone)
    expect(withBoth).toEqual(alone)
  })

  it('does not care what order the curves arrive in', () => {
    const forward = curveStyles(['intcal20', 'marine20'])
    const backward = curveStyles(['marine20', 'intcal20'])
    expect(forward).toEqual(backward)
  })

  it('draws the built-in curves distinguishably from each other', () => {
    const styles = curveStyles(BUILTINS)
    const lines = BUILTINS.map((id) => styles[id].line)
    expect(new Set(lines).size).toBe(BUILTINS.length)
  })

  it('separates a marine curve from a terrestrial one by pattern as well as hue', () => {
    // The pairing that actually happens -- a coastal site dating shell and
    // charcoal together -- has to survive a greyscale print.
    const styles = curveStyles(['shcal20', 'marine20'])
    expect(styles.shcal20.dash).not.toBe(styles.marine20.dash)
    expect(styles.intcal20).toBeUndefined()
  })

  it('ties each envelope to its own curve line', () => {
    for (const id of BUILTINS) {
      const style = curveStyles([id])[id]
      // The bands are the line colour at two strengths, so a reader connects
      // an envelope to its curve without reading the legend.
      expect(style.band1).toContain('rgba')
      expect(style.band2).toContain('rgba')
      expect(style.band1).not.toBe(style.band2)
    }
  })

  it('gives an imported curve the same style every time it is asked', () => {
    const first = curveStyles(['my-local-curve'])['my-local-curve']
    const again = curveStyles(['my-local-curve'])['my-local-curve']
    expect(again).toEqual(first)
  })

  it('never gives an imported curve a built-in curve’s style', () => {
    const builtinLines = new Set(BUILTINS.map((id) => curveStyles([id])[id].line))
    for (const id of ['abc', 'intcal13', 'curve-2024-07', 'x', 'a-very-long-imported-id']) {
      expect(builtinLines.has(curveStyles([id])[id].line)).toBe(false)
    }
  })

  it('splits two imported curves that would otherwise be drawn identically', () => {
    // Find a genuine hash collision rather than asserting on invented ids.
    const ids = Array.from({ length: 400 }, (_, i) => `imported-${i}`)
    const bySlot = new Map<number, string[]>()
    for (const id of ids) {
      const slot = curveSlot(id)
      bySlot.set(slot, [...(bySlot.get(slot) ?? []), id])
    }
    const colliding = [...bySlot.values()].find((group) => group.length >= 2)!
    const [a, b] = colliding
    expect(curveSlot(a)).toBe(curveSlot(b))

    const styles = curveStyles([a, b])
    expect(styles[a].line === styles[b].line && styles[a].dash === styles[b].dash).toBe(false)
  })

  it('handles a repeated id without spending two slots on it', () => {
    const styles = curveStyles(['intcal20', 'intcal20'])
    expect(styles.intcal20).toEqual(curveStyles(['intcal20']).intcal20)
    expect(Object.keys(styles)).toEqual(['intcal20'])
  })

  it('returns something drawable for more curves than there are slots', () => {
    const many = Array.from({ length: 20 }, (_, i) => `curve-${i}`)
    const styles = curveStyles(many)
    for (const id of many) {
      expect(styles[id].line).toMatch(/^#[0-9a-f]{6}$/i)
    }
  })
})

describe('styleForSlot', () => {
  it('wraps rather than falling off the end of the palette', () => {
    expect(styleForSlot(999).line).toMatch(/^#[0-9a-f]{6}$/i)
    expect(styleForSlot(-1).line).toMatch(/^#[0-9a-f]{6}$/i)
  })
})

// --- palette separation -----------------------------------------------------
//
// The module makes specific claims about how far apart these colours are, and
// those claims are the whole reason the palette stops at five. Asserting them
// means a sixth slot added later fails here rather than shipping as a curve
// that looks like a mistake.

/** Relative luminance, per WCAG 2.1. */
function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2)
}

function contrastOnWhite(hex: string): number {
  return (1 + 0.05) / (luminance(hex) + 0.05)
}

/** Hue in degrees. */
function hue(hex: string): number {
  const [r, g, b] = [0, 1, 2].map((i) => parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255)
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  if (max === min) return 0
  const d = max - min
  const h =
    max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4
  return h * 60
}

/** The shorter way round the wheel between two hues. */
function hueGap(a: string, b: string): number {
  const d = Math.abs(hue(a) - hue(b))
  return Math.min(d, 360 - d)
}

const PALETTE = Array.from({ length: CURVE_PALETTE_SIZE }, (_, i) => styleForSlot(i).line)

/** theme.palette.primary.main -- the dates, which share the figure with these. */
const DATES_BLUE = '#3c70b8'

describe('palette separation', () => {
  it('holds no two colours a reader would have to compare side by side to tell apart', () => {
    for (let i = 0; i < PALETTE.length; i++) {
      for (let j = i + 1; j < PALETTE.length; j++) {
        expect(hueGap(PALETTE[i], PALETTE[j])).toBeGreaterThanOrEqual(50)
      }
    }
  })

  it('keeps every curve clear of the blue the dates are drawn in', () => {
    // The one collision that makes this figure unreadable: a curve in the same
    // blue as the lobes drawn over it. Not asserted against the Boundary coral,
    // which is close to two of these -- a Boundary is never drawn on the same
    // figure as a curve, so the two never have to be told apart at a glance.
    for (const colour of PALETTE) {
      expect(hueGap(colour, DATES_BLUE)).toBeGreaterThanOrEqual(50)
    }
  })

  it('draws every curve dark enough to read as a line and as a printed swatch', () => {
    for (const colour of PALETTE) {
      expect(contrastOnWhite(colour)).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('gives no two slots the same colour', () => {
    expect(new Set(PALETTE).size).toBe(PALETTE.length)
  })
})

describe('where the contrast is spent', () => {
  const styles = curveStyles(BUILTINS)
  const marineVsSH = hueGap(styles.marine20.line, styles.shcal20.line)
  const marineVsInt = hueGap(styles.marine20.line, styles.intcal20.line)
  const terrestrialPair = hueGap(styles.shcal20.line, styles.intcal20.line)

  it('puts the marine curve further from each terrestrial curve than they are from each other', () => {
    // The two pairings that actually occur are Marine20 + one terrestrial
    // curve; IntCal20 beside SHCal20 is a site in two hemispheres. So the
    // palette's narrowest gap belongs on the pair nobody sees, and this is
    // the assertion that stops a future reshuffle from moving it back onto a
    // pair people do.
    expect(marineVsSH).toBeGreaterThan(terrestrialPair)
    expect(marineVsInt).toBeGreaterThan(terrestrialPair)
  })

  it('keeps every real pairing clear of the marine curve by a wide margin', () => {
    expect(Math.min(marineVsSH, marineVsInt)).toBeGreaterThanOrEqual(100)
  })

  it('dashes the marine curve and leaves both terrestrial curves solid', () => {
    // The marine curve is the one that shares a figure, so it carries the
    // second channel too -- pattern separates two curves where they cross,
    // and survives a greyscale print.
    expect(styles.marine20.dash).not.toBe('solid')
    expect(styles.intcal20.dash).toBe('solid')
    expect(styles.shcal20.dash).toBe('solid')
  })
})
