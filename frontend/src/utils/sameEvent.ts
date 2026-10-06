import type { DraftMeasurement } from '@/types'
import type { HPDRange } from '@/utils/dateFormat'

/**
 * One Ward and Wilson same-event test, as backend services/statistics.py
 * reports it: over a group calibration's dates (element_id null), or over one
 * Phase's. The statistics are null when the dates could not be pooled, and
 * `refused` says why.
 */
export interface SameEventTest {
  element_id: string | null
  name: string | null
  n: number
  df: number
  level: number
  mean_bp: number | null
  error_bp: number | null
  t: number | null
  critical: number | null
  consistent: boolean | null
  /** The pooled age calibrated, one entry per reported confidence level.
   *  Present only when the dates are consistent with one event. */
  pooled_hpd_ranges: Array<{ confidence: number; ranges: HPDRange[] }> | null
  refused: string | null
}

/** The verdict in words. */
export function sameEventVerdict(test: SameEventTest): string {
  if (test.refused || test.consistent == null) return test.refused ?? ''
  return test.consistent ? 'Consistent with one event' : 'More scatter than one event allows'
}

/** The test's figures in one line: "T = 5.7 · 5% critical 14.07 · df 7". */
export function sameEventFigures(test: SameEventTest): string {
  if (test.t == null || test.critical == null) return ''
  return `T = ${test.t.toFixed(1)} · ${Math.round(test.level * 100)}% critical ${test.critical.toFixed(2)} · df ${test.df}`
}

/** "656 ± 8 BP", the pooled radiocarbon age. */
export function pooledAge(test: SameEventTest): string {
  if (test.mean_bp == null || test.error_bp == null) return ''
  return `${Math.round(test.mean_bp)} ± ${Math.round(test.error_bp)} BP`
}

/** The pooled age's calibrated ranges at `confidence`, or [] if absent. */
export function pooledRanges(test: SameEventTest, confidence: number): HPDRange[] {
  const match = test.pooled_hpd_ranges?.find(
    (entry) => Math.abs(entry.confidence - confidence) < 1e-6
  )
  return match ? match.ranges : []
}

const NAME_LIMIT = 200
const NOTES_LIMIT = 2000

/** The value every date shares, or null when they differ. */
function shared<T>(dates: DraftMeasurement[], pick: (m: DraftMeasurement) => T): T | null {
  const first = pick(dates[0])
  return dates.every((m) => pick(m) === first) ? first : null
}

/**
 * One date standing for `dates`, which `test` found consistent with one event:
 * OxCal's R_Combine, entered as the date it produces. The pooled age and error
 * are kept to a tenth of a year, well inside the error itself.
 *
 * The curve and ΔR are the dates' own -- the test refuses dates that do not
 * share them. Anything else they share (material, place, context, limits) is
 * kept; anything they do not is left blank rather than taken from one of them.
 * The notes say what was pooled, so the date can be traced back.
 */
export function pooledMeasurement(
  dates: DraftMeasurement[],
  test: SameEventTest,
  id: string
): DraftMeasurement {
  if (dates.length < 2 || !test.consistent || test.mean_bp == null || test.error_bp == null) {
    throw new Error('Only dates that pass the same-event test can be pooled')
  }
  const names = dates.map((m) => m.sample_name)
  let sample_name = `Pooled: ${names.join(' + ')}`
  if (sample_name.length > NAME_LIMIT) {
    const suffix = ` … (${dates.length} dates)`
    sample_name = sample_name.slice(0, NAME_LIMIT - suffix.length).trimEnd() + suffix
  }
  const sources = dates.map((m) => `${m.sample_name} ${m.age_bp} ± ${m.error_1sigma} BP`).join('; ')
  let notes =
    `Pooled from ${dates.length} dates (Ward and Wilson 1978; ${sameEventFigures(test)}): ${sources}.`
  if (notes.length > NOTES_LIMIT) notes = notes.slice(0, NOTES_LIMIT - 1) + '…'

  const first = dates[0]
  return {
    id,
    sample_name,
    age_bp: Math.round(test.mean_bp * 10) / 10,
    error_1sigma: Math.round(test.error_bp * 10) / 10,
    material_type: shared(dates, (m) => m.material_type) ?? 'mixed',
    sample_type: first.sample_type,
    curve_id: first.curve_id,
    delta_r: first.delta_r ?? null,
    delta_r_error: first.delta_r_error ?? null,
    element_id: shared(dates, (m) => m.element_id ?? null),
    default_context_name: shared(dates, (m) => m.default_context_name ?? null),
    tpq_age_bp: shared(dates, (m) => m.tpq_age_bp ?? null),
    taq_age_bp: shared(dates, (m) => m.taq_age_bp ?? null),
    latitude: shared(dates, (m) => m.latitude ?? null),
    longitude: shared(dates, (m) => m.longitude ?? null),
    location: shared(dates, (m) => m.location ?? null),
    notes,
    // A fresh date: an outlier setting on one of its sources says nothing
    // about the pool, and the analyst sets one on it if they want one.
    outlier_prior: 0,
    outlier_model: 'general',
  }
}
