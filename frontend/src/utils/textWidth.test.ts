// The estimator is calibrated against canvas measureText, run in the app's
// own browser at 10px on the strings the Harris matrix actually draws -- the
// boundary names and their formatted ranges. Those measurements are
// the numbers in `ROBOTO_10PX` below.
//
// What is held to is not exactness, which a class-based estimate cannot have,
// but the tolerance the caller was written around: close enough that a box
// sized from it fits, and never so far under that the label escapes the stub of
// rule either side.
import { describe, expect, it } from 'vitest'

import { estimateTextWidth } from './textWidth'

/** [text, measured px at 10px Roboto, bold]. */
const ROBOTO_10PX: Array<[string, number, boolean]> = [
  ['End of Fire_Feature_J', 95.6, false],
  ['End of Fire_Feature_J', 96.7, true],
  ['End of Fire_Feature_A', 96.6, false],
  ['Start of sequence', 78.7, false],
  ['End of sequence', 74.0, false],
  ['Site sequence', 62.4, false],
  ['Fire_Feature_K', 65.4, false],
  ['Boundary', 42.3, false],
  ['642–560 cal BP (95.4%)', 107.1, false],
  ['1450–1520 cal AD (95.4%)', 118.9, false],
  ['12345–11000 cal BC (99.7%)', 129.8, false],
  ['1200–1350 CE (68.3%)', 102.4, false],
]

describe('estimateTextWidth', () => {
  it('lands within a tenth of what the browser measures', () => {
    for (const [text, measured, bold] of ROBOTO_10PX) {
      const estimate = estimateTextWidth(text, 10, bold)
      const error = Math.abs(estimate / measured - 1)
      expect(error, `${text}: estimated ${estimate}, measured ${measured}`).toBeLessThan(0.1)
    }
  })

  it('never underestimates by more than the rule stub absorbs', () => {
    // RULE_STUB is 24px each side. An underestimate eats into that stub
    // rather than pushing the label out of the box, so what matters is that
    // no single label is short by anything like that much.
    for (const [text, measured, bold] of ROBOTO_10PX) {
      expect(measured - estimateTextWidth(text, 10, bold)).toBeLessThan(8)
    }
  })

  it('scales with the font size', () => {
    expect(estimateTextWidth('Boundary', 20)).toBeGreaterThan(estimateTextWidth('Boundary', 10))
  })

  it('grows with the text, so a longer name always asks for a wider box', () => {
    expect(estimateTextWidth('End of Fire_Feature_J', 10)).toBeGreaterThan(
      estimateTextWidth('End of', 10)
    )
  })

  it('is zero for nothing to draw', () => {
    expect(estimateTextWidth('', 10)).toBe(0)
  })
})
