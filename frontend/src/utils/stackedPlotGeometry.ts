// Vertical geometry of the stacked posterior plot: how tall the figure is for
// a given number of rows, and where everything below the axis sits.
//
// Lives here rather than in the component so it can be imported without
// pulling in plotly.js, which needs browser globals and cannot be loaded in a
// node test. Keeping the arithmetic separate is the point -- see
// StackedPosteriorPlot.test.ts for the properties it has to hold.

// Pixels per row. Enough for a name and a readable curve; 15 dates then come
// to roughly one screen instead of the seven-screen scroll one plot per date
// produced.
const ROW_PIXELS = 46

// --- Vertical geometry below the axis -------------------------------------
//
// Everything under the plot area is stacked in PIXELS: tick labels, then the
// axis title, then the legend. Plotly positions a legend in PAPER units
// though -- a fraction of the plot area's height -- so a constant `legend.y`
// means a gap that grows with the number of rows, while the axis title and
// the bottom margin stay put in pixels. The two collide differently at
// different sizes, which is why the same figure could be wrong in two
// opposite ways: at 7 rows the legend landed on top of the tick labels
// (both at 384px), and at 10 rows it landed on the axis title (549 vs 551).
//
// stackedPlotGeometry below converts the pixel budget back into the fraction
// Plotly wants, using the height the figure is actually given, so the gap is
// the same at every row count. It is exported and unit-tested for that.
const MARGIN_TOP = 10
// Measured from a rendered figure rather than guessed: tick labels occupy
// ~15px, the axis title ~17px, the horizontal legend ~29px.
const TICK_LABEL_BAND = 18
const AXIS_TITLE_STANDOFF = 8
const AXIS_TITLE_BAND = 22
const LEGEND_BAND = 30
const BOTTOM_SLACK = 6
// Where the legend's top edge sits, in pixels below the plot area.
const LEGEND_GAP_PIXELS = TICK_LABEL_BAND + AXIS_TITLE_STANDOFF + AXIS_TITLE_BAND
const MARGIN_BOTTOM = LEGEND_GAP_PIXELS + LEGEND_BAND + BOTTOM_SLACK
// Breathing room inside the plot area, on top of the rows themselves. Kept
// separate from the margins so the two cannot drift apart -- the previous
// single CHROME_PIXELS constant had to be remembered whenever a margin
// changed, and silently shrank the plot area when it wasn't.
const PLOT_AREA_PADDING = 60

/**
 * The figure's vertical geometry for a given number of rows.
 *
 * Returns the pixel height to give the div, the margins, and the legend's
 * `y` in the paper units Plotly wants -- derived so that the legend always
 * sits LEGEND_GAP_PIXELS below the plot area whatever the row count. Pure,
 * so the spacing can be asserted without rendering Plotly; see
 * StackedPosteriorPlot.test.ts.
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
