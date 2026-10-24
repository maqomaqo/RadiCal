import { isTauri } from '@tauri-apps/api/core'
import Plotly from 'plotly.js-dist-min'
import type { DownloadImgopts, ToImgopts } from 'plotly.js-dist-min'

import { saveBlob } from './saveFile'

import { PNG_EXPORT_SCALE, exportFilename } from './plotExport'

/**
 * Saving a plot to a file -- the one part of exporting that needs Plotly, and
 * therefore a browser.
 *
 * On its own rather than beside the naming and scale it uses, because
 * plotly.js-dist-min's UMD wrapper reads `self` at module scope and Node has
 * none: any test importing a module that imports Plotly dies with "self is not
 * defined" before collecting a test. Quarantining the import here keeps the
 * pure half assertable (see plotExport.ts).
 *
 * So do not move `exportFilename` or `PNG_EXPORT_SCALE` here, and do not
 * import this file from one a unit test reaches.
 */

/**
 * Save a plot as a PNG at PNG_EXPORT_SCALE, for the app's own download control
 * -- Plotly's modebar renders six <a> elements with no href, role or
 * accessible name, none of which can take focus, so the path a figure takes
 * into a publication needed a mouse.
 *
 * Takes the container rather than the graph div: callers hold a ref to the
 * element they render, and Plotly's div is created inside it.
 */
export function downloadPlotPng(
  container: HTMLElement | null,
  filename: string
): void {
  const graph = container?.querySelector<HTMLElement>('.js-plotly-plot')
  if (!graph) return
  // Plotly's declaration of downloadImage's options leaves out `scale`,
  // though it passes every option through to toImage, which takes one.
  const options: DownloadImgopts & Pick<ToImgopts, 'scale'> = {
    format: 'png',
    // Null: the graph's own size, as drawn.
    width: null,
    height: null,
    scale: PNG_EXPORT_SCALE,
    filename: exportFilename(filename),
  }
  if (isTauri()) {
    // Plotly's own download is a blob URL clicked with `download` set, which
    // the desktop app's webview ignores: render the image, then save it the
    // way every other download in the app is saved.
    void Plotly.toImage(graph, { ...options, format: 'png' })
      .then((dataUrl) => fetch(dataUrl))
      .then((response) => response.blob())
      .then((blob) => saveBlob(blob, `${options.filename}.png`))
    return
  }
  void Plotly.downloadImage(graph, options)
}
