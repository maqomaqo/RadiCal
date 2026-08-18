// The curve browser's scrubber arithmetic. Every failure these catch renders
// perfectly happily -- a window that drifts a few years per drag, one that
// slides off the end of the curve, one that inverts when its handles cross --
// which is why they are asserted as numbers rather than looked at.
import { describe, expect, it } from 'vitest'

import {
  MIN_WINDOW_YEARS,
  calToPixel,
  clampWindow,
  panByPixels,
  pixelToCal,
  pointsForWidth,
  windowRect,
  zoomAbout,
} from './curveScrubber'

const CURVE = { min: 0, max: 55000 }
const WIDTH = 1000

describe('cal <-> pixel', () => {
  it('puts the OLDEST end of the curve at the left, as both plots do', () => {
    // Not arbitrary: PosteriorPlot and StackedPosteriorPlot both set
    // autorange: 'reversed', so time reads left to right. A scrubber running
    // the other way would move the view backwards from the drag.
    expect(calToPixel(55000, CURVE, WIDTH)).toBe(0)
    expect(calToPixel(0, CURVE, WIDTH)).toBe(WIDTH)
    expect(calToPixel(27500, CURVE, WIDTH)).toBe(500)
  })

  it('round-trips', () => {
    for (const cal of [0, 137, 2750, 30000, 55000]) {
      expect(pixelToCal(calToPixel(cal, CURVE, WIDTH), CURVE, WIDTH)).toBeCloseTo(cal, 6)
    }
  })

  it('does not divide by zero on a strip that has not been measured yet', () => {
    expect(calToPixel(2000, CURVE, 0)).toBe(0)
    expect(pixelToCal(10, { min: 0, max: 0 }, WIDTH)).toBe(0)
  })

  it('moves right toward the present', () => {
    expect(pixelToCal(600, CURVE, WIDTH)).toBeLessThan(pixelToCal(400, CURVE, WIDTH))
  })
})

describe('clampWindow', () => {
  it('normalises a window whose handles crossed', () => {
    expect(clampWindow({ from: 3000, to: 1000 }, CURVE)).toEqual({ from: 1000, to: 3000 })
  })

  it('keeps a window inside the curve without changing its width', () => {
    const clamped = clampWindow({ from: -500, to: 1500 }, CURVE)
    expect(clamped.from).toBe(0)
    expect(clamped.to - clamped.from).toBe(2000)

    const atTheOldEnd = clampWindow({ from: 54000, to: 56000 }, CURVE)
    expect(atTheOldEnd.to).toBe(55000)
    expect(atTheOldEnd.to - atTheOldEnd.from).toBe(2000)
  })

  it('refuses to collapse below the minimum, growing about the middle', () => {
    const clamped = clampWindow({ from: 2000, to: 2001 }, CURVE)
    expect(clamped.to - clamped.from).toBe(MIN_WINDOW_YEARS)
    expect((clamped.from + clamped.to) / 2).toBeCloseTo(2000.5, 6)
  })

  it('never returns a window wider than the curve', () => {
    const clamped = clampWindow({ from: -10000, to: 90000 }, CURVE)
    expect(clamped).toEqual({ from: 0, to: 55000 })
  })
})

describe('panByPixels', () => {
  it('dragging right moves the window toward the present', () => {
    const moved = panByPixels({ from: 10000, to: 11000 }, CURVE, WIDTH, 100)
    expect(moved.from).toBeCloseTo(10000 - 5500, 6)
    expect(moved.to - moved.from).toBe(1000)
  })

  it('stops at the ends instead of squashing the window', () => {
    const atYoung = panByPixels({ from: 100, to: 1100 }, CURVE, WIDTH, 500)
    expect(atYoung).toEqual({ from: 0, to: 1000 })

    const atOld = panByPixels({ from: 54000, to: 55000 }, CURVE, WIDTH, -500)
    expect(atOld).toEqual({ from: 54000, to: 55000 })
  })

  it('does not drift when panned out and back', () => {
    const start = { from: 10000, to: 12000 }
    const there = panByPixels(start, CURVE, WIDTH, 37)
    const andBack = panByPixels(there, CURVE, WIDTH, -37)
    expect(andBack.from).toBeCloseTo(start.from, 6)
    expect(andBack.to).toBeCloseTo(start.to, 6)
  })
})

describe('zoomAbout', () => {
  it('leaves the anchored age where it was', () => {
    const zoomed = zoomAbout({ from: 2000, to: 3000 }, CURVE, 2750, 0.5)
    const positionBefore = (2750 - 2000) / 1000
    const positionAfter = (2750 - zoomed.from) / (zoomed.to - zoomed.from)
    expect(positionAfter).toBeCloseTo(positionBefore, 6)
    expect(zoomed.to - zoomed.from).toBeCloseTo(500, 6)
  })

  it('will not zoom out past the curve', () => {
    expect(zoomAbout({ from: 0, to: 55000 }, CURVE, 27500, 4)).toEqual({ from: 0, to: 55000 })
  })

  it('will not zoom in past the minimum window', () => {
    const zoomed = zoomAbout({ from: 2000, to: 2030 }, CURVE, 2015, 0.01)
    expect(zoomed.to - zoomed.from).toBeCloseTo(MIN_WINDOW_YEARS, 6)
  })
})

describe('windowRect', () => {
  it('is where the window is', () => {
    // The youngest 5,500 years sit at the RIGHT-hand end of the strip.
    const rect = windowRect({ from: 0, to: 5500 }, CURVE, WIDTH)
    expect(rect.x).toBeCloseTo(900, 6)
    expect(rect.width).toBeCloseTo(100, 6)
  })

  it('stays visible even for a window narrower than a pixel', () => {
    expect(windowRect({ from: 2000, to: 2001 }, CURVE, WIDTH).width).toBeGreaterThanOrEqual(1)
  })
})

describe('pointsForWidth', () => {
  it('asks for two points per pixel, matching the min/max decimation', () => {
    expect(pointsForWidth(600)).toBe(1200)
  })

  it('stays within what the endpoint accepts', () => {
    expect(pointsForWidth(0)).toBe(100)
    expect(pointsForWidth(99999)).toBe(5000)
  })
})
