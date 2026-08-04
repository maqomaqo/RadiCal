// Client-side mirror of backend/app/services/stratigraphy.py's
// expand_relationships/find_cycle. The backend is the authoritative check
// (it rejects a cyclic request with a 422 before any MCMC run), but
// running the same check here gives instant feedback in the Stratigraphy
// UI -- exactly the "use the diagram as a check that relationships are
// specified correctly" workflow -- without a network round trip on every
// edit.
import type { DraftGroup, MatrixMeasurement, DraftRelationship } from '@/types'

export interface Edge {
  from: string
  to: string
}

/** Map each group id to the ids of the measurements in it. */
export function groupMembers(measurements: MatrixMeasurement[], groups: DraftGroup[]): Record<string, string[]> {
  const members: Record<string, string[]> = {}
  for (const g of groups) members[g.id] = []
  for (const m of measurements) {
    if (m.group_id) {
      if (!members[m.group_id]) members[m.group_id] = []
      members[m.group_id].push(m.id)
    }
  }
  return members
}

/**
 * Expand every relationship (which may reference a sample OR a group on
 * either side) into plain sample-to-sample pairs.
 *
 * Returns { olderYounger, equal }:
 *   - olderYounger: deduplicated edges meaning `from` is older than `to`.
 *   - equal: deduplicated pairs (order-independent) from 'equal' relationships.
 *
 * Direction follows the law of superposition: a sample stratigraphically
 * ABOVE another was deposited later and is therefore YOUNGER; a sample
 * BELOW another is OLDER. So "from above to" means from is younger (to is
 * older), and "from below to" means from is older.
 */
export function expandRelationships(
  measurements: MatrixMeasurement[],
  groups: DraftGroup[],
  relationships: DraftRelationship[]
): { olderYounger: Edge[]; equal: Edge[] } {
  const members = groupMembers(measurements, groups)
  const resolve = (id: string, type: 'sample' | 'group'): string[] => (type === 'group' ? members[id] || [] : [id])

  const olderYoungerSet = new Set<string>()
  const equalSet = new Set<string>()

  for (const r of relationships) {
    const fromMembers = resolve(r.from_sample_id, r.from_type)
    const toMembers = resolve(r.to_sample_id, r.to_type)
    for (const a of fromMembers) {
      for (const b of toMembers) {
        if (a === b) continue
        if (r.relationship === 'above') olderYoungerSet.add(`${b}|${a}`)
        else if (r.relationship === 'below') olderYoungerSet.add(`${a}|${b}`)
        else if (r.relationship === 'equal') {
          const [x, y] = a < b ? [a, b] : [b, a]
          equalSet.add(`${x}|${y}`)
        }
      }
    }
  }

  const toEdges = (set: Set<string>): Edge[] => Array.from(set).map((s) => {
    const [from, to] = s.split('|')
    return { from, to }
  })

  return { olderYounger: toEdges(olderYoungerSet), equal: toEdges(equalSet) }
}

/**
 * Detect a cycle in the "older than" directed graph via Kahn's algorithm.
 * Returns the node ids involved in (or feeding into) a cycle, or null if
 * the graph is a valid DAG.
 */
export function findCycle(edges: Edge[]): string[] | null {
  if (edges.length === 0) return null

  const adj = new Map<string, string[]>()
  const indeg = new Map<string, number>()
  const nodes = new Set<string>()

  for (const { from, to } of edges) {
    nodes.add(from)
    nodes.add(to)
    if (!adj.has(from)) adj.set(from, [])
    adj.get(from)!.push(to)
    indeg.set(to, (indeg.get(to) || 0) + 1)
    if (!indeg.has(from)) indeg.set(from, indeg.get(from) || 0)
  }

  const queue: string[] = Array.from(nodes).filter((n) => (indeg.get(n) || 0) === 0)
  let visited = 0
  while (queue.length > 0) {
    const n = queue.shift()!
    visited += 1
    for (const m of adj.get(n) || []) {
      indeg.set(m, (indeg.get(m) || 0) - 1)
      if (indeg.get(m) === 0) queue.push(m)
    }
  }

  if (visited === nodes.size) return null
  return Array.from(nodes).filter((n) => (indeg.get(n) || 0) > 0)
}
