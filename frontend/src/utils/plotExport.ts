// How the plots' "download as PNG" button behaves. Shared by the
// single-date and stacked views so an exported figure is the same quality
// whichever one it came from.

/**
 * Multiplier applied to the on-screen size when the modebar's camera
 * button saves a PNG.
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
