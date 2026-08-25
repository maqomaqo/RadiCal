/**
 * The words for an analysis type, in one place.
 *
 * `analysis_type` is stored as the solver's own identifier -- 'calibration',
 * 'group_calibration', 'bayesian' -- and those are the right values to keep
 * in the database and on the wire. They are not words to show anybody: the
 * Dashboard was rendering "Type: group_calibration", underscore and all, on
 * the first page a user sees, for an analysis the New Analysis form had
 * called "Group Calibration" when they created it.
 *
 * An unrecognised type falls back to its raw value rather than to a generic
 * "Unknown": if a new solver ships and this map is not updated, a slightly
 * ugly label is far better than a screen that refuses to say which of two
 * analyses is which.
 */
export const ANALYSIS_TYPE_LABELS: Record<string, string> = {
  calibration: 'Single calibration',
  group_calibration: 'Group calibration',
  bayesian: 'Bayesian model',
}

export function analysisTypeLabel(analysisType: string): string {
  return ANALYSIS_TYPE_LABELS[analysisType] ?? analysisType
}
