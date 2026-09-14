import { describe, it, expect } from 'vitest'
import { linearTicks, niceStep } from './linearTicks'

describe('niceStep', () => {
  it('picks a 1/2/5 step covering the span in roughly the asked-for intervals', () => {
    expect(niceStep(100, 5)).toBe(20)
    expect(niceStep(93, 5)).toBe(20)
    expect(niceStep(1, 5)).toBe(0.2)
    expect(niceStep(4300, 5)).toBe(1000)
  })

  it('never returns zero or a negative step', () => {
    // A degenerate axis -- one level, or a span the caller has not measured
    // yet -- must not produce a tick loop that cannot terminate.
    expect(niceStep(0, 5)).toBe(1)
    expect(niceStep(-10, 5)).toBe(1)
    expect(niceStep(Number.NaN, 5)).toBe(1)
  })
})

describe('linearTicks', () => {
  it('graduates a depth range in round numbers', () => {
    expect(linearTicks(0, 100, 5)).toEqual([0, 20, 40, 60, 80, 100])
  })

  it('keeps every tick inside the range', () => {
    // An axis drawn to the data's own extent has no room past its ends, and
    // a label half off the plot reads as a value the figure does not hold.
    const ticks = linearTicks(37, 194, 5)
    expect(Math.min(...ticks)).toBeGreaterThanOrEqual(37)
    expect(Math.max(...ticks)).toBeLessThanOrEqual(194)
  })

  it('does not accumulate floating point error across a fractional step', () => {
    // Adding 0.1 repeatedly reaches 0.30000000000000004, which would be
    // drawn on the axis exactly as written.
    expect(linearTicks(0, 1, 10).every((t) => t.toString().length <= 4)).toBe(true)
  })

  it('returns nothing for a range that is not one', () => {
    expect(linearTicks(5, 5)).toEqual([])
    expect(linearTicks(10, 2)).toEqual([])
    expect(linearTicks(Number.NaN, 10)).toEqual([])
  })

  it('includes an endpoint that lands exactly on a step', () => {
    // The deepest level is the one the reader most wants labelled, and
    // floating point rounding must not drop it off the end.
    expect(linearTicks(0, 50, 5)).toContain(50)
  })
})
