/**
 * The marine reservoir snapshots this account can use, and deriving a ΔR from
 * one.
 *
 * The derivation happens on the SERVER, against a local mirror of the CHRONO
 * database. That is not an implementation detail to be optimised away later:
 * asking calib.org "what measurements are near here" would send a site's
 * coordinates to a third party on every lookup, which is the disclosure the
 * whole coordinate-precision apparatus exists to prevent. Nothing in this file
 * talks to anyone but this app.
 */

import { formatApiError } from '@/utils/apiError'

/** How precisely a habit is known. services/reservoir.py's FEEDING_CLASSES. */
export type FeedingClass =
  | 'suspension'
  | 'deposit'
  | 'carnivore'
  | 'grazer'
  | 'migratory'
  | 'ambiguous'
  | 'unknown'

export interface SnapshotSpec {
  id: string
  /** 'bundled' ships with the app and belongs to nobody; 'ingested' is yours. */
  kind: 'bundled' | 'ingested'
  source: string
  record_count: number
  content_sha256: string
  citation: string | null
  note: string | null
  fetched_at: string | null
  /** What the SOURCE says about its own currency, not when we asked. */
  source_last_changed: string | null
  created_by: string | null
}

/** One reservoir measurement, as the point list shows it. */
export interface LookupPoint {
  map_no: number
  latitude: number
  longitude: number
  delta_r: number | null
  /** Null means no published uncertainty: the point cannot be weighted. */
  delta_r_error: number | null
  feeding: FeedingClass | null
  /** The source's own word, kept because the normalisation is a judgement. */
  feeding_raw: string | null
  locality: string | null
  reference: string | null
  distance_km: number | null
}

/** A weighted mean and what went into it. */
export interface Combination {
  delta_r: number | null
  delta_r_error: number | null
  n: number
  map_nos: number[]
  internal: number | null
  scatter: number | null
  excluded_no_error: number[]
}

export interface LookupResult {
  delta_r: number | null
  delta_r_error: number | null
  n: number
  snapshot_id: string | null
  centre: { lat: number; lon: number } | null
  /** §2.6: the single most informative thing to show beside a derived value. */
  nearest_km: number | null
  farthest_km: number | null
  excluded_no_error: number[]
  excluded_by_user: number[]
  feeding: Record<string, number>
  feeding_raw: Record<string, string>
  included_all: Combination | null
  /**
   * The same arithmetic without deposit, migratory and carnivore points.
   * Always computed, never applied unless asked -- see the plan's §5.4.1.
   * Null when dropping them would leave too few points to weight.
   */
  suspension_equivalent: Combination | null
  /** How far apart the two are, in years. Null when there is no comparison. */
  feeding_shift: number | null
  /** Whether that difference is big enough to say plainly. */
  feeding_shift_matters: boolean
  feeding_shift_warn_years: number
  points: LookupPoint[]
  /** The record to store if this value is applied. */
  delta_r_source: Record<string, unknown>
}

export interface LookupRequest {
  latitude: number
  longitude: number
  snapshot_id: string
  n?: number
  max_km?: number | null
  exclude_map_nos?: number[]
  suspension_only?: boolean
}

async function readOrThrow(response: Response): Promise<any> {
  const body = await response.json().catch(() => null)
  if (!response.ok || !body?.success) {
    throw new Error(formatApiError(body))
  }
  return body
}

/** Every snapshot this account may use: the bundled ones, plus its own. */
export async function fetchSnapshots(): Promise<{
  items: SnapshotSpec[]
  hasBundled: boolean
}> {
  const parsed = await readOrThrow(await fetch('/api/reservoir/snapshots'))
  return {
    items: (parsed.data.items ?? []) as SnapshotSpec[],
    // Worth knowing on its own: until the bundled snapshot ships, a fresh
    // install has no lookup at all until somebody ingests one.
    hasBundled: Boolean(parsed.data.has_bundled),
  }
}

/**
 * Derive a ΔR for a position.
 *
 * Throws on a refusal, which is the honest shape: the server answers 422 when
 * there is nothing near enough, or when an exclusion would leave fewer than
 * two points to weight, and the message says what it would have taken. A
 * resolved promise carrying no value would invite the caller to use one.
 */
export async function lookupDeltaR(request: LookupRequest): Promise<LookupResult> {
  const response = await fetch('/api/reservoir/lookup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
  const parsed = await readOrThrow(response)
  return parsed.data as LookupResult
}

export interface Freshness {
  source: { record_count: number; source_last_changed: string | null }
  mine: {
    snapshot_id: string
    record_count: number | null
    source_last_changed: string | null
  } | null
  /** Whether re-ingesting would gain anything. True when nothing is mirrored. */
  behind: boolean
}

/**
 * Ask calib.org what it holds now.
 *
 * Costs about 2 KB against the megabyte a full fetch costs, and it is called
 * only when somebody presses the button. Never on mount: a page that probed a
 * third party every time it was opened would be making a request its reader
 * did not ask for, and this app's whole position on that is that reaching the
 * network is a decision.
 */
export async function fetchFreshness(): Promise<Freshness> {
  const parsed = await readOrThrow(await fetch('/api/reservoir/freshness'))
  return parsed.data as Freshness
}

/**
 * Fetch the whole database and store it as a new snapshot.
 *
 * Never automatic, and never an update in place. A ΔR that changed because a
 * remote database changed, underneath an analysis somebody has already
 * published, is exactly the failure the snapshot rules exist to prevent -- so
 * this adds a snapshot beside the old one, which stays resolvable for as long
 * as anything cites it.
 */
export async function ingestSnapshot(): Promise<{
  snapshot: SnapshotSpec
  report: Record<string, unknown>
}> {
  const response = await fetch('/api/reservoir/snapshots', { method: 'POST' })
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(formatApiError(body))
  if (!body?.success) {
    // A 200 carrying problems: the request worked and the answer is what was
    // wrong with the source. The errors are the useful part.
    const errors = (body?.data?.errors ?? []) as string[]
    throw new Error(errors.join('; ') || formatApiError(body))
  }
  return body.data as { snapshot: SnapshotSpec; report: Record<string, unknown> }
}
