// What a saved figure is called and how big it is. Shared by every plot
// surface so an exported figure is the same whichever one it came from.
//
// Deliberately holds no reference to Plotly. The act of saving lives next
// door in downloadPlot.ts, and the split is not tidiness -- plotly.js-dist-min
// is a browser bundle whose UMD wrapper reads `self` at module scope, so
// importing it in a Node test throws "self is not defined" before a single
// assertion runs. Both things below are pure and worth asserting; while they
// sat beside the import, plotExport.test.ts could not run at all. Same reason
// stackedPlotGeometry.ts and curvePlotGeometry.ts are their own modules.

/**
 * Multiplier applied to the on-screen size when a plot is saved as a PNG.
 *
 * Plotly's default is 1 -- the plot at exactly its on-screen pixel size,
 * which is a screenshot: fine on screen, visibly soft the moment it goes
 * into a document. These are figures people publish, so 3x is the default
 * instead, which clears 300dpi for a figure printed a few inches wide.
 * Layout is untouched by this; it is the same figure with more pixels, not
 * a re-laid-out one.
 */
export const PNG_EXPORT_SCALE = 3

/**
 * A saved-file name that survives every OS -- no spaces, slashes or
 * colons, all of which appear in real sample and boundary names.
 *
 * Without a filename Plotly saves every figure as "newplot.png", which on
 * a Results page offering one plot per date means a download folder full
 * of newplot(7).png.
 */
export function exportFilename(raw: string): string {
  return (
    raw
      .replace(/[^a-zA-Z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .toLowerCase() || 'plot'
  )
}
