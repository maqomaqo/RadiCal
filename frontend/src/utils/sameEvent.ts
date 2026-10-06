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
