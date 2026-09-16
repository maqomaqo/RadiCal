/**
 * Building a model of SITES from a column the dates already carry.
 *
 * The dispersal models read the model tree as a set of places: a site is an
 * element opened by its own Boundary, and its position is the centroid of the
 * coordinates on its dates (services/dispersal.py, derive_sites). A collection
 * of 224 dates from fourteen islands, deployed into the one starting phase
 * every analysis begins with, therefore has NO sites -- not because the dates
 * lack coordinates, which they carry, but because the tree has no structure
 * to hang them on. Hand-building fourteen bracketed Sequences is the tax this
 * removes: the dates already say where they are from, in `location`, in the
 * context they were filed under, or in the coordinates themselves.
 *
 * The shape built is the one derive_sites wants, and nothing looser:
 *
 *   Sequence "Site sequence"
 *     Boundary "Start of sequence"
 *     Phase "Sites"                     <- unordered: no claim about which
 *       Sequence "<site>"                  site came first, which is exactly
 *         Boundary "Start of <site>"       what the dispersal models are for
 *         Phase "<site>"                   finding out
 *         Boundary "End of <site>"
 *       ...
 *       Phase "Unassigned dates"        <- only when some date has no value
 *     Boundary "End of sequence"
 *
 * Each site is a Sequence with its own two Boundaries because that is what
 * makes it a site to derive_sites: the start Boundary's posterior is the
 * arrival estimate the whole feature is about (§6.2). The sites sit in a
 * Phase, not a Sequence, because a Sequence would assert an order between
 * them, and proposing that order from the map is a separate, refusable step.
 */
import { newElementId } from '@/utils/modelTreeDefaults'
import type { BootstrappedTree } from '@/utils/modelTreeDefaults'
import type { ModelElement } from '@/types'

/** What a date can be grouped on. */
export type SiteGroupingKey = 'location' | 'context' | 'position'

/** A date, as this needs it: the id and whichever columns might group it. */
export interface SiteGroupingDate {
  id: string
  location?: string | null
  default_context_name?: string | null
  latitude?: number | null
  longitude?: number | null
}

/**
 * A date this close to a group's first member joins the group. The same
 * figure as the server's SITE_SCATTER_KM, and it has to be: the server
 * refuses a phase whose dates are more than twice this apart -- the widest a
 * group built by this rule can be -- as two sites in one phase.
 */
export const SITE_RADIUS_KM = 5

const NO_VALUE_PHASE = 'Unassigned dates'

export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = Math.PI / 180
  const dLat = (lat2 - lat1) * r
  const dLon = (lon2 - lon1) * r
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2
  return 2 * 6371.0088 * Math.asin(Math.sqrt(a))
}

function element(type: ModelElement['element_type'], name: string): ModelElement {
  return { id: newElementId(), element_type: type, name, tpq_age_bp: null, taq_age_bp: null, children: [] }
}

function placed(date: SiteGroupingDate): date is SiteGroupingDate & { latitude: number; longitude: number } {
  return (
    typeof date.latitude === 'number' && Number.isFinite(date.latitude) &&
    typeof date.longitude === 'number' && Number.isFinite(date.longitude)
  )
}

interface Group {
  name: string
  ids: string[]
}

/**
 * The groups a key yields, in the order the dates introduce them -- the only
 * order available, and one that asserts nothing since the groups land in a
 * Phase. Dates with no value for the key come back separately.
 */
export function groupsFor(dates: SiteGroupingDate[], by: SiteGroupingKey): {
  groups: Group[]
  unassigned: string[]
} {
  const groups: Group[] = []
  const unassigned: string[] = []

  if (by === 'position') {
    // Greedy, first-member-anchored, exactly as the server groups a model-less
    // calibration (derive_sites_from_dates): a date joins the first group
    // whose anchor is within the radius. Named after the first location seen
    // in the group, since that is what a reader calls the place; the
    // coordinate is the fallback for dates that were placed but not named.
    const anchors: Array<{ lat: number; lon: number; group: Group }> = []
    for (const date of dates) {
      if (!placed(date)) {
        unassigned.push(date.id)
        continue
      }
      const home = anchors.find(
        (a) => haversineKm(date.latitude, date.longitude, a.lat, a.lon) <= SITE_RADIUS_KM,
      )
      if (home) {
        home.group.ids.push(date.id)
        if (!home.group.name && (date.location || '').trim()) home.group.name = date.location!.trim()
        continue
      }
      const group: Group = { name: (date.location || '').trim(), ids: [date.id] }
      groups.push(group)
      anchors.push({ lat: date.latitude, lon: date.longitude, group })
    }
    for (const [index, anchor] of anchors.entries()) {
      if (!anchor.group.name) {
        anchor.group.name = `Site ${index + 1} (${anchor.lat.toFixed(2)}, ${anchor.lon.toFixed(2)})`
      }
    }
    return { groups, unassigned }
  }

  const byName = new Map<string, Group>()
  for (const date of dates) {
    const raw = by === 'location' ? date.location : date.default_context_name
    const value = (raw || '').trim()
    if (!value) {
      unassigned.push(date.id)
      continue
    }
    let group = byName.get(value)
    if (!group) {
      group = { name: value, ids: [] }
      byName.set(value, group)
      groups.push(group)
    }
    group.ids.push(date.id)
  }
  return { groups, unassigned }
}

/**
 * How many distinct groups the key would make, and how many dates it would
 * leave out. For the menu, so a key that does nothing can say so before it
 * replaces the model.
 */
export function groupingPreview(dates: SiteGroupingDate[], by: SiteGroupingKey): {
  sites: number
  unassigned: number
} {
  const { groups, unassigned } = groupsFor(dates, by)
  return { sites: groups.length, unassigned: unassigned.length }
}

/** The site model described at the top of this file. */
export function siteModelTree(dates: SiteGroupingDate[], by: SiteGroupingKey): BootstrappedTree {
  const { groups, unassigned } = groupsFor(dates, by)
  const assignments: Record<string, string> = {}
  const sites: ModelElement[] = []

  for (const group of groups) {
    const phase = element('phase', group.name)
    for (const id of group.ids) assignments[id] = phase.id
    sites.push({
      ...element('sequence', group.name),
      children: [
        element('boundary', `Start of ${group.name}`),
        phase,
        element('boundary', `End of ${group.name}`),
      ],
    })
  }

  if (unassigned.length > 0) {
    // Not a site: it has no place, so it gets no Boundaries of its own and
    // the dispersal models leave it alone. Beside the sites so it is seen.
    const phase = element('phase', NO_VALUE_PHASE)
    for (const id of unassigned) assignments[id] = phase.id
    sites.push(phase)
  }

  return {
    tree: {
      ...element('sequence', 'Site sequence'),
      children: [
        element('boundary', 'Start of sequence'),
        { ...element('phase', 'Sites'), children: sites },
        element('boundary', 'End of sequence'),
      ],
    },
    assignments,
  }
}
