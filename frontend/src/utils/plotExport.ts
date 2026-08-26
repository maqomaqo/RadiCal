// What a saved figure is called and how big it is. Shared by every plot
// surface so an exported figure is the same whichever one it came from.
//
// Holds no reference to Plotly. Saving lives next door in downloadPlot.ts, and
// the split is not tidiness: plotly.js-dist-min is a browser bundle whose UMD
// wrapper reads `self` at module scope, so importing it in a Node test throws
// "self is not defined" before a single assertion runs. Both things below are
// pure and worth asserting. Same reason stackedPlotGeometry.ts and
// curvePlotGeometry.ts are their own modules.

/**
 * Multiplier applied to the on-screen size when a plot is saved as a PNG.
 * Plotly's default of 1 is a screenshot: visibly soft in a document. These are
 * figures people publish, so 3x clears 300dpi at a few inches wide. Layout is
 * untouched -- the same figure with more pixels.
 */
export const PNG_EXPORT_SCALE = 3

/**
 * A saved-file name that survives every OS -- no spaces, slashes or colons,
 * all of which appear in real sample and boundary names. Without one, Plotly
 * saves every figure as "newplot.png".
 */
export function exportFilename(raw: string): string {
  return (
    raw
      .replace(/[^a-zA-Z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .toLowerCase() || 'plot'
  )
}
