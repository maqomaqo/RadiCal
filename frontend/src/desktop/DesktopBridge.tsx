import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { invoke, isTauri } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'

/** A file the desktop app was asked to open, as `take_opened_files` hands it over. */
interface OpenedFile {
  name: string
  bytes: number[]
}

/**
 * Where the desktop app's own controls reach into the frontend: its menu,
 * and files it is asked to open (a double-clicked .oxcal, or one dropped on
 * its icon). Renders nothing, and does nothing outside the desktop app.
 *
 * Each of them lands on a page and a dialog the frontend already has, so the
 * desktop app adds no second way of doing anything.
 */
export default function DesktopBridge() {
  const navigate = useNavigate()

  useEffect(() => {
    if (!isTauri()) return
    let stopped = false
    const stops: UnlistenFn[] = []
    const keep = (stop: UnlistenFn) => (stopped ? stop() : stops.push(stop))

    // Pulled rather than pushed, so a file that opened the app is not lost
    // to an event sent before this page was listening.
    const openFiles = async () => {
      const files = await invoke<OpenedFile[]>('take_opened_files')
      // One import dialog at a time: the last file asked for wins.
      const last = files[files.length - 1]
      if (!last) return
      const importFile = new File([new Uint8Array(last.bytes)], last.name)
      navigate('/new-analysis', { state: { importFile } })
    }

    void listen<string>('radical://menu', (event) => {
      switch (event.payload) {
        case 'new-analysis':
          navigate('/new-analysis', { state: { fresh: true } })
          break
        case 'import-dates':
          navigate('/new-analysis', { state: { openImport: true } })
          break
        case 'import-project':
          navigate('/projects', { state: { openImport: true } })
          break
        case 'help':
          navigate('/help')
          break
      }
    }).then(keep)
    void listen('radical://opened', () => void openFiles()).then(keep)
    void openFiles()

    return () => {
      stopped = true
      stops.forEach((stop) => stop())
    }
  }, [navigate])

  return null
}
