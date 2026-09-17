import { useEffect, useReducer, useState } from 'react'

/**
 * Settings read once per project and shared by every page that asks. A
 * project's tile setting changes only through its own edit dialog, which
 * writes the cache on save, so the rest of the app can trust this for the
 * session.
 */
const cache = new Map<string, boolean>()
const inflight = new Map<string, Promise<boolean>>()

function fetchMapTilesEnabled(projectId: string): Promise<boolean> {
  const cached = cache.get(projectId)
  if (cached !== undefined) return Promise.resolve(cached)
  const pending = inflight.get(projectId)
  if (pending) return pending
  const request = fetch(`/api/projects/${projectId}`)
    .then((r) => r.json())
    .then((data) => {
      if (!data?.success) throw new Error('project not read')
      const enabled = !!data.data?.map_tiles_enabled
      cache.set(projectId, enabled)
      return enabled
    })
    // Off on any failure: the private basemap is the default, and a project
    // that cannot be read has not said yes to anything. Not CACHED, though:
    // a failure is about this moment, and the next page to ask should ask.
    .catch(() => false)
    .finally(() => {
      inflight.delete(projectId)
    })
  inflight.set(projectId, request)
  return request
}

/**
 * Record a setting this session already knows -- the project page has the
 * whole project loaded and has just saved an edit to it, so nobody should
 * have to fetch it again to draw the next map.
 */
export function rememberProjectMapTiles(projectId: string, enabled: boolean): void {
  cache.set(projectId, enabled)
}

/**
 * Whether the given project has opted into the tiled basemap.
 *
 * Null while the answer is on its way, so a map can wait for it rather than
 * draw the offline basemap and then tear it down for tiles a moment later.
 * False for no project at all: an analysis filed nowhere draws on the
 * private basemap, since there is no project to have said otherwise. Pages
 * that already hold the project should read the field off it rather than
 * mount this.
 */
export function useProjectMapTiles(projectId: string | null | undefined): boolean | null {
  // Read straight from the cache on every render, with state used only to
  // re-render once the fetch has filled it: a state copy of the answer would
  // start one render behind, and that render is a map drawn on the wrong
  // basemap.
  const [, rerender] = useReducer((n: number) => n + 1, 0)
  // A read that failed, so the map draws the private default rather than
  // waiting forever for an answer that is not coming.
  const [failedFor, setFailedFor] = useState<string | null>(null)

  useEffect(() => {
    if (!projectId || cache.has(projectId)) return
    let active = true
    fetchMapTilesEnabled(projectId).then(() => {
      if (!active) return
      if (!cache.has(projectId)) setFailedFor(projectId)
      rerender()
    })
    return () => {
      active = false
    }
  }, [projectId])

  if (!projectId) return false
  const known = cache.get(projectId)
  if (known !== undefined) return known
  return failedFor === projectId ? false : null
}
