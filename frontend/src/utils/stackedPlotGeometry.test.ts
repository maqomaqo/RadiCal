// Vertical spacing of the stacked posterior plot.
//
// The bug these pin: Plotly places a legend in PAPER units -- a fraction of
// the plot area's height -- while the axis title and the bottom margin are
// fixed pixels. A constant `legend.y` therefore meant a gap that grew with
// the number of rows, and the same figure was wrong in two opposite ways
// depending on how many dates it held. Measured on the running app before the
// fix, in pixels from the top of the figure:
//
//   7 rows   tick labels 384-399   legend 384-413   title 421-438
//            -> the legend sat ON the tick labels, title crushed at the edge
//   10 rows  tick labels 512-527   title 549-566    legend 551-580
//            -> the legend sat ON the axis title
//
// So the property worth holding is not "the numbers are these numbers" but
// "the pixel gap below the axis does not depend on the row count". That is
// what stackedPlotGeometry exists to guarantee and what these assert.
import { describe, expect, it } from 'vitest'

import { stackedPlotGeometry } from './stackedPlotGeometry'

/** Where the legend's top edge lands, in pixels below the plot area. */
function legendGapPixels(rowCount: number): number {
  const g = stackedPlotGeometry(rowCount)
  // legendY is negative and expressed as a fraction of the plot area height.
  return -g.legendY * g.plotAreaHeight
}

const ROW_COUNTS = [1, 2, 3, 5, 7, 10, 16, 25, 40]

describe('spacing below the axis', () => {
  it('puts the legend the same distance below the plot at every row count', () => {
    const gaps = ROW_COUNTS.map(legendGapPixels)

    for (const gap of gaps) {
      expect(gap).toBeCloseTo(gaps[0], 6)
    }
  })

  it('leaves room for the tick labels and the axis title above the legend', () => {
    // The legend must clear both bands stacked under the axis, or it lands on
    // top of one of them -- which is exactly what happened at 7 rows.
    for (const rows of ROW_COUNTS) {
      const g = stackedPlotGeometry(rows)
      expect(legendGapPixels(rows)).toBeGreaterThanOrEqual(g.titleStandoff + 30)
    }
  })

  it('keeps the legend inside the bottom margin', () => {
    // Past the margin the legend is clipped, or Plotly shunts it back up over
    // the labels. A legend is about 30px tall.
    for (const rows of ROW_COUNTS) {
      const g = stackedPlotGeometry(rows)
      expect(legendGapPixels(rows) + 30).toBeLessThanOrEqual(g.marginBottom)
    }
  })

  it('pins the axis title rather than letting it float', () => {
    // Left to itself Plotly drops the title to the bottom of whatever margin
    // it is given, so a taller figure stranded it low. A standoff fixes it a
    // set distance under the tick labels.
    for (const rows of ROW_COUNTS) {
      expect(stackedPlotGeometry(rows).titleStandoff).toBeGreaterThan(0)
    }
  })
})

describe('figure height', () => {
  it('grows by exactly one row height per row', () => {
    for (let rows = 2; rows < 20; rows++) {
      const step = stackedPlotGeometry(rows).height - stackedPlotGeometry(rows - 1).height
      expect(step).toBe(46)
    }
  })

  it('accounts for both margins, so the plot area is not silently squeezed', () => {
    // The margins used to be a separate constant from the total height, so
    // changing one without the other quietly shrank the drawing area.
    for (const rows of ROW_COUNTS) {
      const g = stackedPlotGeometry(rows)
      expect(g.height).toBe(g.plotAreaHeight + g.marginTop + g.marginBottom)
    }
  })

  it('stays sane for a degenerate row count', () => {
    for (const rows of [0, -1]) {
      const g = stackedPlotGeometry(rows)
      expect(g.height).toBeGreaterThan(0)
      expect(g.plotAreaHeight).toBeGreaterThan(0)
      expect(Number.isFinite(g.legendY)).toBe(true)
    }
  })
})
