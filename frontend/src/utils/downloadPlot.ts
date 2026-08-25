import Plotly from 'plotly.js-dist-min'

import { PNG_EXPORT_SCALE, exportFilename } from './plotExport'

/**
 * Saving a plot to a file -- the one part of exporting that needs Plotly, and
 * therefore needs a browser.
 *
 * It sits on its own rather than beside the naming and scale it uses, because
 * plotly.js-dist-min is a browser UMD bundle: its wrapper is invoked as
 * `}(self, ...)`, evaluated at module scope, and Node has no `self`. Any test
 * that imports a module importing Plotly dies with "self is not defined"
 * before it collects a single test. Keeping the import quarantined here means
 * the pure half stays assertable -- see plotExport.ts.
 *
 * So: do not move `exportFilename` or `PNG_EXPORT_SCALE` into this file, and
 * do not import this file from one that a unit test reaches.
 */

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
