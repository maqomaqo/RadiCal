import { describe, expect, it } from 'vitest'

import { resolutionStride } from './resolutionStride'

describe('resolutionStride', () => {
  it('asks for every year when the window is small enough to show them', () => {
    // A 400-year window across 900px: sub-pixel already, so 1.
    expect(resolutionStride(400)).toBe(1)
    expect(resolutionStride(900)).toBe(1)
  })

  it('thins in proportion to the span, not in fixed steps', () => {
    expect(resolutionStride(9000)).toBe(10)
    expect(resolutionStride(45000)).toBe(50)
  })

  it('stays inside what the endpoint accepts', () => {
    expect(resolutionStride(10_000_000)).toBe(200)
    expect(resolutionStride(0)).toBe(5)
    expect(resolutionStride(-1)).toBe(5)
  })

  it('follows the plot width, since that is what it is drawing into', () => {
    expect(resolutionStride(9000, 1800)).toBe(5)
    expect(resolutionStride(9000, 450)).toBe(20)
  })
})
