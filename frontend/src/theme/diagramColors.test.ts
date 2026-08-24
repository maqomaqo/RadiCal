// The Harris matrix nests boxes several levels deep, and the shade of any
// one of them is now stated once -- by `tint` -- rather than emerging from
// however many translucent ancestors happen to sit behind it. These hold
// that property: the same inputs give the same colour wherever the box is,
// and the result is always opaque so nothing underneath can show through.
import { describe, expect, it } from 'vitest'
import { decomposeColor } from '@mui/material/styles'

import {
  BOUNDARY_CORAL,
  BOUNDARY_CORAL_INK,
  CONTEXT_PALETTE,
  HIATUS_VIOLET,
  PHASE_GREEN,
  contextShades,
  tint,
} from './diagramColors'

const WHITE = '#ffffff'

describe('tint', () => {
  it('returns an opaque colour, never a translucent one', () => {
    // The whole point: a fill that lets its ancestors through is what this
    // replaced. A context four levels in was seen through four washes.
    for (const color of CONTEXT_PALETTE) {
      expect(tint(color, 0.1, WHITE)).toMatch(/^rgb\(\d+, \d+, \d+\)$/)
    }
  })

  it('mixes towards the colour as the weight rises', () => {
    expect(tint('#1f7a62', 0, WHITE)).toBe('rgb(255, 255, 255)')
    expect(tint('#1f7a62', 0.1, WHITE)).toBe('rgb(233, 242, 239)')
    expect(tint('#1f7a62', 1, WHITE)).toBe('rgb(31, 122, 98)')
  })

  it('composites against whatever it is painted over, not just white', () => {
    expect(tint('#ffffff', 0.5, '#000000')).toBe('rgb(128, 128, 128)')
    expect(tint('#1f7a62', 1, '#000000')).toBe('rgb(31, 122, 98)')
  })

  it('darkens step by step as depth grows, which is what depth shading reads as', () => {
    // The caller shades containers with 0.04 + depth * 0.02. Every level
    // has to land on a shade distinct from the one outside it, or nesting
    // stops reading as nesting. Steps are within a rounding unit of each
    // other rather than identical -- 8-bit channels can't do otherwise.
    const shade = (depth: number) =>
      decomposeColor(tint('#4a7dc4', 0.04 + depth * 0.02, WHITE)).values[0]
    const steps = [0, 1, 2, 3].map((d) => shade(d) - shade(d + 1))

    expect(steps.every((s) => s > 0)).toBe(true)
    expect(Math.max(...steps) - Math.min(...steps)).toBeLessThanOrEqual(1)
  })

  it('takes an rgb() string as readily as a hex one', () => {
    expect(tint('rgb(31, 122, 98)', 0.1, WHITE)).toBe(tint('#1f7a62', 0.1, WHITE))
  })
})

// The context palette's two load-bearing properties, neither of which the
// eight-Material-swatch version held. It led with blue[700] (#1976d2), the
// colour DESIGN.md names as the literal anti-reference, and it also contained
// HIATUS_VIOLET -- so a Phase could be drawn in the hue reserved for a Hiatus
// in a diagram whose premise is that a hue says what something IS.
describe('CONTEXT_PALETTE', () => {
  const channels = (hex: string) => decomposeColor(hex).values as number[]

  /** Relative luminance, for the contrast checks below. */
  const luminance = (hex: string) => {
    const [r, g, b] = channels(hex).map((v) => {
      const c = v / 255
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
    })
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
  }
  const contrast = (a: string, b: string) => {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
    return (hi + 0.05) / (lo + 0.05)
  }

  it('never reuses a colour that already means something else', () => {
    // The reserved hues: a context is not a Boundary, a Hiatus, or the
    // Sequence -- and MUI's stock blue is the palette this theme exists to
    // replace, so no entry may drift back onto it.
    const reserved = [BOUNDARY_CORAL, BOUNDARY_CORAL_INK, HIATUS_VIOLET, '#1976d2', '#3c70b8']
    for (const shade of CONTEXT_PALETTE) {
      expect(reserved).not.toContain(shade.toLowerCase())
    }
  })

  it('holds one hue across every step, so the family reads as Phase', () => {
    // Chroma and lightness carry the difference; hue does not move. Computed
    // from RGB directly rather than trusting the literals to have been picked
    // carefully -- that is the mistake being guarded against.
    const hue = (hex: string) => {
      const [r, g, b] = channels(hex).map((v) => v / 255)
      const max = Math.max(r, g, b)
      const min = Math.min(r, g, b)
      if (max === min) return 0
      const d = max - min
      const h =
        max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4
      return ((h * 60) + 360) % 360
    }
    const hues = CONTEXT_PALETTE.map(hue)
    const phaseHue = hue(PHASE_GREEN)
    for (const h of hues) {
      expect(Math.abs(h - phaseHue)).toBeLessThan(6)
    }
  })

  it('keeps every step legible as label text, not just as a border', () => {
    // These carry 10px node labels as well as rules, so 3:1 is not enough.
    for (const shade of CONTEXT_PALETTE) {
      expect(contrast(shade, '#ffffff')).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('separates adjacent steps enough to be told apart', () => {
    for (let i = 1; i < CONTEXT_PALETTE.length; i++) {
      expect(contrast(CONTEXT_PALETTE[i], CONTEXT_PALETTE[i - 1])).toBeGreaterThan(1.15)
    }
  })
})

describe('contextShades', () => {
  it('spreads across the ramp rather than taking the head of it', () => {
    // Three contexts in order would be the three MOST similar shades in a set
    // whose steps are only ~1.2:1 apart -- the worst available outcome.
    expect(contextShades(2)).toEqual([CONTEXT_PALETTE[0], CONTEXT_PALETTE[5]])
    expect(contextShades(3)).toEqual([CONTEXT_PALETTE[0], CONTEXT_PALETTE[3], CONTEXT_PALETTE[5]])
  })

  it('gives a lone context the canonical Phase green, not the palest end', () => {
    expect(contextShades(1)).toEqual([CONTEXT_PALETTE[2]])
  })

  it('returns one shade per context, always', () => {
    for (const n of [0, 1, 2, 5, 6, 9, 20]) {
      expect(contextShades(n)).toHaveLength(n)
    }
  })

  it('repeats past the ramp rather than inventing a hue', () => {
    const many = contextShades(9)
    expect(new Set(many).size).toBe(CONTEXT_PALETTE.length)
    for (const shade of many) expect(CONTEXT_PALETTE).toContain(shade)
  })
})
