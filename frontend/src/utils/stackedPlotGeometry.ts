// Vertical geometry of the stacked posterior plot: how tall the figure is for
// a given number of rows, and where everything below the axis sits.
//
// Lives here rather than in the component so it can be imported without
// plotly.js, which needs browser globals and cannot load in a node test. See
// StackedPosteriorPlot.test.ts for the properties it has to hold.

// Pixels per row: enough for a name and a readable curve, putting 15 dates on
// roughly one screen.
const ROW_PIXELS = 46

// --- Vertical geometry below the axis -------------------------------------
//
// Everything under the plot area is stacked in PIXELS: tick labels, axis
// title, legend. Plotly positions a legend in PAPER units, though -- a
// fraction of the plot area's height -- so a constant `legend.y` gives a gap
// that grows with the row count while the axis title and bottom margin stay
// put, and the two collide differently at different sizes.
//
// stackedPlotGeometry converts the pixel budget back into the fraction Plotly
// wants, using the height the figure is actually given, so the gap is the same
// at every row count. Exported and unit-tested for that.
const MARGIN_TOP = 10
// Measured from a rendered figure: tick labels ~15px, axis title ~17px,
// horizontal legend ~29px.
const TICK_LABEL_BAND = 18
const AXIS_TITLE_STANDOFF = 8
const AXIS_TITLE_BAND = 22
const LEGEND_BAND = 30
const BOTTOM_SLACK = 6
// Where the legend's top edge sits, in pixels below the plot area.
const LEGEND_GAP_PIXELS = TICK_LABEL_BAND + AXIS_TITLE_STANDOFF + AXIS_TITLE_BAND
const MARGIN_BOTTOM = LEGEND_GAP_PIXELS + LEGEND_BAND + BOTTOM_SLACK
// Breathing room inside the plot area, on top of the rows. Kept separate from
// the margins so the two cannot drift apart.
const PLOT_AREA_PADDING = 60

/**
 * The figure's vertical geometry for a given number of rows: the pixel height
 * for the div, the margins, and the legend's `y` in Plotly's paper units,
 * derived so the legend always sits LEGEND_GAP_PIXELS below the plot area
 * whatever the row count. Pure, so the spacing can be asserted without Plotly.
 */
export function stackedPlotGeometry(rowCount: number) {
  const plotAreaHeight = Math.max(1, rowCount) * ROW_PIXELS + PLOT_AREA_PADDING
  const height = plotAreaHeight + MARGIN_TOP + MARGIN_BOTTOM
  return {
    height,
    plotAreaHeight,
    marginTop: MARGIN_TOP,
    marginBottom: MARGIN_BOTTOM,
    titleStandoff: AXIS_TITLE_STANDOFF,
    // Negative: below the plot area. yanchor 'top' anchors the legend's own
    // top edge here, so the gap is measured to the top of the legend.
    legendY: -(LEGEND_GAP_PIXELS / plotAreaHeight),
  }
}
