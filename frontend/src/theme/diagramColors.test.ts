// The Harris matrix nests boxes several levels deep, and the shade of any
// one of them is now stated once -- by `tint` -- rather than emerging from
// however many translucent ancestors happen to sit behind it. These hold
// that property: the same inputs give the same colour wherever the box is,
// and the result is always opaque so nothing underneath can show through.
import { describe, expect, it } from 'vitest'
import { decomposeColor } from '@mui/material/styles'

import { CONTEXT_PALETTE, tint } from './diagramColors'

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
