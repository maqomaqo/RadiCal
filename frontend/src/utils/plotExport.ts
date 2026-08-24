import Plotly from 'plotly.js-dist-min'

// How the plots' "Download PNG" button behaves. Shared by every plot surface
// so an exported figure is the same quality whichever one it came from.

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
 * Save a plot as a PNG at PNG_EXPORT_SCALE.
 *
 * Exists because the app now draws its own download control rather than
 * showing Plotly's modebar. That bar rendered six <a> elements with no href,
 * no role and no accessible name, none of which could take focus -- so the
 * path a figure takes into a publication needed a mouse. A real button can be
 * labelled, reached by Tab, and put where the reader is already looking.
 *
 * Takes the container rather than the graph div: callers hold a ref to the
 * element they render, and Plotly's own div is created inside it.
 */
export function downloadPlotPng(
  container: HTMLElement | null,
  filename: string
): void {
  const graph = container?.querySelector<HTMLElement>('.js-plotly-plot')
  if (!graph) return
  // Cast: plotly.js-dist-min ships no types for downloadImage, and the
  // project's own d.ts (types/plotly.d.ts) deliberately keeps the surface
  // narrow rather than restating Plotly's API.
  const plotly = Plotly as unknown as {
    downloadImage(gd: HTMLElement, opts: Record<string, unknown>): Promise<string>
  }
  void plotly.downloadImage(graph, {
    format: 'png',
    scale: PNG_EXPORT_SCALE,
    filename: exportFilename(filename),
  })
}

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
