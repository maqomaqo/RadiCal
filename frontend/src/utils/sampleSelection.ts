/**
 * Choosing dates in the collection by something other than one tick each.
 *
 * A collection with several hundred dates from a dozen sites is ticked a site
 * at a time -- to give the site its reservoir offset, its position, its
 * curve -- and one tick per date is the tax this removes. Two ways in: a run
 * of rows between two clicks, in whatever order the table is showing; and
 * every date that shares a site with the ones already ticked, by the name
 * it was filed under or by how far apart the positions are.
 *
 * Pure, so the table and the selection bar can both ask and a Node test can
 * assert the answers.
 */
import { haversineKm } from '@/utils/siteGrouping'

/** A date, as this needs it: its id and the two ways it says where it is from. */
export interface SiteDate {
  id: string
  location?: string | null
  latitude?: number | null
  longitude?: number | null
}

/**
 * The ids from `anchorId` to `targetId` inclusive, in the order given.
 *
 * The order is the TABLE's, sorted however the reader has sorted it: a range
 * down a column sorted by position is a site, and that is the point. When
 * either end is not in the list there is no range, and the caller falls back
 * to a single tick.
 */
export function rangeBetween(orderedIds: string[], anchorId: string, targetId: string): string[] {
  const from = orderedIds.indexOf(anchorId)
  const to = orderedIds.indexOf(targetId)
  if (from < 0 || to < 0) return []
  const [start, end] = from <= to ? [from, to] : [to, from]
  return orderedIds.slice(start, end + 1)
}

/**
 * A location name as a key: trimmed, inner whitespace collapsed, case-folded.
 *
 * "Kealakekua Bay", "kealakekua bay" and "Kealakekua  Bay" are one site typed
 * three ways, and a site is what the reader is trying to select. Null for a
 * date with no name, which matches nothing -- not even another nameless date.
 */
export function locationKey(location: string | null | undefined): string | null {
  const key = (location ?? '').trim().replace(/\s+/g, ' ').toLocaleLowerCase()
  return key === '' ? null : key
}

/**
 * The distinct location names among a selection, first spelling kept, in the
 * order first seen. What the "same location" option names.
 */
export function selectedLocations(selected: SiteDate[]): string[] {
  const seen = new Map<string, string>()
  for (const date of selected) {
    const key = locationKey(date.location)
    if (key !== null && !seen.has(key)) seen.set(key, (date.location ?? '').trim())
  }
  return [...seen.values()]
}

/** A date's position, or null when it does not have both halves of one. */
function positionOf(date: SiteDate): { latitude: number; longitude: number } | null {
  const { latitude, longitude } = date
  if (typeof latitude !== 'number' || !Number.isFinite(latitude)) return null
  if (typeof longitude !== 'number' || !Number.isFinite(longitude)) return null
  return { latitude, longitude }
}

/** How many of a selection carry a position at all. */
export function placedCount(selected: SiteDate[]): number {
  return selected.filter((d) => positionOf(d) !== null).length
}

/**
 * The selection widened to every date in the collection filed under the same
 * location name as any selected date. In collection order; the selected
 * dates themselves are always in, whether or not they have a name.
 */
export function datesAtSameLocation<T extends SiteDate>(collection: T[], selected: T[]): T[] {
  const keys = new Set<string>()
  for (const d of selected) {
    const key = locationKey(d.location)
    if (key !== null) keys.add(key)
  }
  const chosen = new Set(selected.map((d) => d.id))
  return collection.filter((d) => {
    if (chosen.has(d.id)) return true
    const key = locationKey(d.location)
    return key !== null && keys.has(key)
  })
}

/**
 * The selection widened to every placed date within `km` of any placed
 * selected date. Zero is exact: the same coordinate and nothing else. In
 * collection order; the selected dates themselves are always in, placed or
 * not.
 */
export function datesWithinKm<T extends SiteDate>(collection: T[], selected: T[], km: number): T[] {
  const centres = selected.map(positionOf).filter((p): p is NonNullable<typeof p> => p !== null)
  const chosen = new Set(selected.map((d) => d.id))
  if (!Number.isFinite(km) || km < 0) return collection.filter((d) => chosen.has(d.id))
  return collection.filter((d) => {
    if (chosen.has(d.id)) return true
    const here = positionOf(d)
    if (!here) return false
    return centres.some((c) => haversineKm(c.latitude, c.longitude, here.latitude, here.longitude) <= km)
  })
}
