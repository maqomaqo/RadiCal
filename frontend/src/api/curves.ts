/**
 * The calibration curves the backend can actually calibrate against.
 *
 * There used to be two separate hardcoded curve lists in this frontend --
 * one in MeasurementFields, one in ProjectSampleTable -- plus three more on
 * the backend, none of which knew about the others. `GET /api/curves` has
 * always existed but nothing called it, and its metadata had drifted so far
 * that it reported IntCal20 as having 5,141 points when the curve being
 * calibrated against has 9,501. The backend's services/curve_registry.py is
 * now the single source of truth and measures that metadata from the loaded
 * curve; this module is how the frontend asks it.
 *
 * Deliberately no local fallback list. If the request fails, the pickers say
 * so and keep whatever curve the date already had, rather than quietly
 * offering a list that might not match what the backend will accept -- which
 * is the failure mode this whole change exists to remove.
 */

import { formatApiError } from '@/utils/apiError'

export interface CurveSpec {
  id: string
  name: string
  /** Display grouping: 'terrestrial' | 'marine' | 'southern_hemisphere'. */
  type: string
  /** Which kind of sample this curve is appropriate for. */
  sample_type: 'terrestrial' | 'marine'
  description: string
  reference: string
  year: number
  doi: string | null
  kind: string
  aliases: string[]
  data_points: number
  cal_min: number
  cal_max: number
  c14_min: number
  c14_max: number
  /** Human-readable calendar grid summary, e.g. "1 yr (0-5,000), 5 yr (5,000-15,000)". */
  resolution: string
  content_sha256: string
  /**
   * Archived curves are hidden from the pickers but still resolve, so an
   * analysis that used one still reruns. They appear in a listing only when
   * it was asked for them.
   */
  archived: boolean
  /** The file an imported curve came from. Null for the bundled curves. */
  source_filename: string | null
}

/**
 * One point of a curve, as the browser draws it: calendar age (cal BP),
 * radiocarbon age, the curve's own 1σ there, and the local gradient in
 * radiocarbon years per calendar year.
 */
export type CurvePoint = [number, number, number, number]

export interface CurveWindow {
  curve_id: string
  cal_from: number
  cal_to: number
  points: CurvePoint[]
  point_count: number
  /** How many points the window holds before decimation. */
  points_in_window: number
  decimated: boolean
  /** The whole curve's extent, so a scrubber can be drawn without a second request. */
  curve_cal_min: number
  curve_cal_max: number
}

/** What the import preview endpoint reports about an uploaded file. */
export interface CurveImportPreview {
  ok: boolean
  errors: string[]
  warnings: string[]
  notes: string[]
  source_format: string
  parse_options: Record<string, unknown>
  suggested_name: string | null
  suggested_reference: string | null
  suggested_id: string
  /** Empty when the file could not be read. */
  summary: Partial<{
    data_points: number
    cal_min: number
    cal_max: number
    c14_min: number
    c14_max: number
  }>
  /** Downsampled [cal BP, 14C age, 1σ] triples, for drawing the curve. */
  preview: number[][]
  filename?: string
}

/** The parts of a stored curve a user can write. Its numbers are not among
 * them: a correction is imported as a new curve. */
export interface CurveMetadataInput {
  name: string
  description?: string | null
  reference?: string | null
  doi?: string | null
  type: string
  sample_type: 'terrestrial' | 'marine'
  aliases: string[]
}

/** What is already calibrated against a curve, and so what deleting it would
 * strand. */
export interface CurveUsage {
  analysis_ids: string[]
  project_ids: string[]
  mixed_curve_ids: string[]
  in_use: boolean
}

let pending: Promise<CurveSpec[]> | null = null

/**
 * Fetch the curve list, once per page load.
 *
 * Curves do not change while the app is open (adding one is a backend
 * operation that does not exist yet), so every caller shares one request
 * rather than each mounting component issuing its own. A failed request is
 * not cached -- the next caller retries.
 */
export function fetchCurves(): Promise<CurveSpec[]> {
  if (!pending) {
    pending = fetch('/api/curves')
      .then(async (response) => {
        if (!response.ok) throw new Error(`Curve list request failed (${response.status})`)
        const body = await response.json()
        return (body?.data?.items ?? []) as CurveSpec[]
      })
      .catch((error) => {
        pending = null
        throw error
      })
  }
  return pending
}

/**
 * Drop the cached curve list, so the next reader refetches.
 *
 * Called after importing, renaming, archiving or deleting a curve: the list
 * is shared by every picker on the page, and a stale one would offer a curve
 * that no longer exists or omit one just added.
 */
export function resetCurveCache(): void {
  pending = null
}

/**
 * Every curve including archived ones, uncached.
 *
 * The manager page wants both -- an archived curve is still listed there, so
 * it can be restored -- and wants what is true now rather than what was true
 * when some picker first asked. The pickers keep using the cached
 * `fetchCurves`, which leaves archived curves out.
 */
export async function fetchAllCurves(): Promise<CurveSpec[]> {
  const response = await fetch('/api/curves?include_archived=true')
  const parsed = await readOrThrow(response)
  return (parsed.data.items ?? []) as CurveSpec[]
}

async function readOrThrow(response: Response): Promise<any> {
  const body = await response.json().catch(() => null)
  if (!response.ok || !body?.success) {
    throw new Error(formatApiError(body))
  }
  return body
}

/**
 * Read an uploaded curve file and report what it holds, storing nothing.
 *
 * A file that cannot be read still comes back as a resolved preview with
 * `ok: false` and the reasons in `errors` -- the request succeeded, and its
 * answer is what is wrong with the file. Only a failed request throws.
 */
export async function previewCurve(
  file: File,
  calendar: 'cal_bp' | 'bc_ad' = 'cal_bp'
): Promise<CurveImportPreview> {
  const body = new FormData()
  body.append('file', file)
  body.append('calendar', calendar)
  const response = await fetch('/api/curves/import/preview', { method: 'POST', body })
  const parsed = await readOrThrow(response)
  return parsed.data as CurveImportPreview
}

/** Store a previewed curve. Takes the file again rather than the parsed rows,
 * so the backend's parser stays the only one. */
export async function createCurve(
  file: File,
  metadata: CurveMetadataInput,
  curveId: string,
  calendar: 'cal_bp' | 'bc_ad' = 'cal_bp'
): Promise<CurveSpec> {
  const body = new FormData()
  body.append('file', file)
  body.append('metadata', JSON.stringify(metadata))
  body.append('curve_id', curveId)
  body.append('calendar', calendar)
  const response = await fetch('/api/curves', { method: 'POST', body })
  const parsed = await readOrThrow(response)
  resetCurveCache()
  return parsed.data as CurveSpec
}

/** Correct a stored curve's description, citation or aliases. */
export async function updateCurve(
  curveId: string,
  patch: Partial<CurveMetadataInput>
): Promise<CurveSpec> {
  const response = await fetch(`/api/curves/${curveId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  const parsed = await readOrThrow(response)
  resetCurveCache()
  return parsed.data as CurveSpec
}

/**
 * Delete a stored curve, or archive it.
 *
 * Deleting one that something has been calibrated against is refused by the
 * backend, and the refusal names what is using it -- which is what the caller
 * shows instead of "409".
 */
export async function deleteCurve(curveId: string, archive = false): Promise<void> {
  const response = await fetch(`/api/curves/${curveId}?archive=${archive}`, { method: 'DELETE' })
  await readOrThrow(response)
  resetCurveCache()
}

export async function restoreCurve(curveId: string): Promise<void> {
  const response = await fetch(`/api/curves/${curveId}/restore`, { method: 'POST' })
  await readOrThrow(response)
  resetCurveCache()
}

export async function fetchUsage(curveId: string): Promise<CurveUsage> {
  const response = await fetch(`/api/curves/${curveId}/usage`)
  const parsed = await readOrThrow(response)
  return parsed.data as CurveUsage
}

/**
 * A drawable window of a curve.
 *
 * Windowed and decimated by the backend rather than here: the decimation has
 * to keep each bucket's extremes (a curve's wiggles are the whole point of
 * looking at one) and that decision belongs with the curve, not with whichever
 * client is drawing it.
 */
export async function fetchCurvePoints(
  curveId: string,
  options: { calFrom?: number; calTo?: number; maxPoints?: number } = {}
): Promise<CurveWindow> {
  const params = new URLSearchParams()
  if (options.calFrom !== undefined) params.set('cal_from', String(Math.round(options.calFrom)))
  if (options.calTo !== undefined) params.set('cal_to', String(Math.round(options.calTo)))
  if (options.maxPoints !== undefined) params.set('max_points', String(options.maxPoints))
  const response = await fetch(`/api/curves/${curveId}/points?${params}`)
  const parsed = await readOrThrow(response)
  return parsed.data as CurveWindow
}

/** Where a curve can be downloaded from, as the file it was imported from. */
export function curveExportUrl(curveId: string): string {
  return `/api/curves/${curveId}/export`
}

/**
 * The curves appropriate for a sample type, in the order the backend offers
 * them -- so the first is the one selecting that sample type should switch
 * to. Replaces the hand-written CURVE_FOR_SAMPLE_TYPE map: marine carbon has
 * to be calibrated against a marine curve (IntCal20 would be wrong by
 * centuries), while a terrestrial sample picks between hemispheres.
 */
export function curvesForSampleType(curves: CurveSpec[], sampleType: string): CurveSpec[] {
  return curves.filter((curve) => curve.sample_type === sampleType)
}

/**
 * The options a curve picker should show, given what the date is currently
 * set to.
 *
 * Always includes the current value, even while the list is still loading or
 * if it failed: a Select whose value is not among its options silently blanks
 * itself, which would present an unset curve on a date that has one -- and
 * the next save would write whatever the blank resolved to.
 */
export function curveOptions(
  curves: CurveSpec[],
  currentId: string | undefined
): { id: string; label: string }[] {
  const options = curves.map((curve) => ({ id: curve.id, label: curve.name }))
  if (currentId && !options.some((option) => option.id === currentId)) {
    options.unshift({ id: currentId, label: currentId })
  }
  return options
}

/** A curve's name for display, falling back to its id if the list is unavailable. */
export function curveLabel(curves: CurveSpec[], curveId: string | undefined): string {
  if (!curveId) return ''
  return curves.find((curve) => curve.id === curveId)?.name ?? curveId
}
