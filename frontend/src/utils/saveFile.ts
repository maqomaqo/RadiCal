import { invoke, isTauri } from '@tauri-apps/api/core'

/**
 * Hand a file to the person: a browser download, or, in the desktop app, its
 * native save dialog.
 *
 * The desktop app's window is the system webview, where a blob URL clicked
 * with `download` set is unreliable -- WebKit on macOS does nothing at all.
 * So there the bytes go to the app (`save_file` in radical-desktop), which
 * asks where to put them and writes them. Every download in the app comes
 * through here, so none can be left behind on the browser's path.
 *
 * Resolves to false when the person cancels the save dialog.
 */
export async function saveBlob(blob: Blob, filename: string): Promise<boolean> {
  if (isTauri()) {
    const bytes = new Uint8Array(await blob.arrayBuffer())
    // Headers are ASCII; a project name in a filename need not be.
    try {
      const saved = await invoke<string | null>('save_file', bytes, {
        headers: { 'X-Filename': encodeURIComponent(filename) },
      })
      return saved !== null
    } catch (err) {
      // The app answers a refusal with a bare string, which every caller
      // would otherwise report as "undefined".
      throw err instanceof Error ? err : new Error(String(err))
    }
  }
  const href = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(href)
  return true
}
