// How the browser's readout describes the curve's local gradient.
//
// The thresholds are the claim, not the wording: a "plateau" that included
// ordinary curve, or an "ordinary" band that swallowed the Hallstatt plateau,
// would make the readout confidently wrong about the one thing it exists to
// say. The numbers below are what services/curve_view.py actually measures on
// IntCal20 for those stretches (see its SLOPE_HALF_SPAN_YEARS table).
import { describe, expect, it } from 'vitest'

import { describeSlope } from './curveShape'

describe('describeSlope', () => {
  it('calls the Hallstatt plateau a plateau', () => {
    // curve_view measures 0.31 there at the 50-year half-span.
    expect(describeSlope(0.31).label).toBe('plateau')
  })

  it('does not call ordinary curve a plateau', () => {
    // 0.91 on an ordinary stretch, 1.05 in the early Holocene.
    expect(describeSlope(0.91).label).toBe('ordinary')
    expect(describeSlope(1.05).label).toBe('ordinary')
  })

  it('names a reversal, where one measurement splits into several ranges', () => {
    expect(describeSlope(-0.4).label).toBe('reversal')
    expect(describeSlope(-0.4).detail).toContain('separate calendar ranges')
  })

  it('treats a gradient of about zero as flat rather than reversed', () => {
    // Numerical wobble either side of zero should not flip the description
    // between two words that mean quite different things.
    expect(describeSlope(0.01).label).toBe('plateau')
    expect(describeSlope(-0.01).label).toBe('plateau')
  })

  it('marks a steep stretch, where dates calibrate unusually tightly', () => {
    // ~2.97 at the drop into the Hallstatt plateau, where resolution is best.
    expect(describeSlope(2.97).label).toBe('steep')
  })

  it('covers the whole line without a gap', () => {
    for (const slope of [-5, -0.05, 0, 0.35, 0.8, 1, 1.6, 3, 50]) {
      expect(describeSlope(slope).label).toBeTruthy()
      expect(describeSlope(slope).detail).toBeTruthy()
    }
  })
})
