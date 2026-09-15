/**
 * Asking the server for a spatial order.
 *
 * The one thing in the dispersal feature that needs no solver run: an order is
 * a question about POSITIONS, and the answer is the same before a model has
 * been solved as after. That is why it can be offered in the tree editor,
 * where there is a draft and no results at all.
 *
 * It is a proposal and the server says so. Nothing here applies anything.
 */
import { formatApiError } from '@/utils/apiError'
import type { MatrixMeasurement, ModelElement } from '@/types'

export interface ProposedSite {
  element_id: string
  name: string
  latitude: number
  longitude: number
}

export interface ProposedEdge {
  from: string
  to: string
  from_point: { latitude: number; longitude: number }
  to_point: { latitude: number; longitude: number }
  km: number
}

export interface SequenceProposal {
  order: ProposedSite[]
  /** The spanning tree the order was read off, so the map can draw it. */
  edges: ProposedEdge[]
  /** Why there is no proposal, in the user's own words. */
  refused: string | null
  n_sites: number
  total_km: number | null
  rooted_at: string | null
  origin: { lat: number; lon: number } | null
}

export async function proposeSpatialSequence(
  tree: ModelElement,
  measurements: MatrixMeasurement[],
  origin?: { lat: number; lon: number } | null
): Promise<SequenceProposal> {
  const response = await fetch('/api/dispersal/propose-sequence', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model_tree: tree,
      measurements,
      origin: origin ?? null,
    }),
  })
  const body = await response.json().catch(() => null)
  if (!response.ok || !body?.success) {
    throw new Error(formatApiError(body))
  }
  return body.data as SequenceProposal
}
