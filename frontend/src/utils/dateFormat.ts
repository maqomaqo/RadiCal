/**
 * Shared utilities for presenting calendar ages in the user's chosen
 * reporting format: cal BP, BC/AD, or BCE/CE.
 *
 * All calibration results are computed and stored internally as cal BP
 * (calendar years Before Present, where "present" is fixed at 1950 by
 * convention -- this is what the calibration curves and the Bayesian model
 * both operate in). This module only affects *display*: it converts a cal
 * BP value into the requested format for showing to the user, without
 * changing anything about how ages are calculated.
 */

export type DateFormat = 'calBP' | 'bcad' | 'bce_ce'

export const DATE_FORMAT_OPTIONS: { value: DateFormat; label: string }[] = [
  { value: 'calBP', label: 'cal BP (years before 1950)' },
  { value: 'bcad', label: 'BC / AD' },
  { value: 'bce_ce', label: 'BCE / CE' },
]

export const DEFAULT_DATE_FORMAT: DateFormat = 'calBP'

// Radiocarbon "Present" is fixed at 1950 by international convention,
// regardless of when the measurement or analysis actually took place.
const RADIOCARBON_PRESENT = 1950

/**
 * Convert a cal BP value to a proleptic Gregorian calendar year, using the
 * conventional historical BC/AD numbering with no year zero (1 BC is
 * immediately followed by AD 1). BCE/CE use the same year numbers as
 * BC/AD -- only the era labels differ -- so this single conversion backs
 * both formats.
 */
function calBPToYear(calBP: number): { year: number; isCurrentEra: boolean } {
  const signedYear = RADIOCARBON_PRESENT - calBP
  if (signedYear > 0) {
    return { year: Math.round(signedYear), isCurrentEra: true }
  }
  // No year zero: a signedYear of 0 or below maps to 1 BC/BCE, -1, etc.
  return { year: Math.round(1 - signedYear), isCurrentEra: false }
}

/**
 * Format a single calendar age (in cal BP) according to the chosen
 * reporting format.
 */
export function formatCalendarAge(calBP: number, format: DateFormat): string {
  if (!Number.isFinite(calBP)) return 'N/A'

  if (format === 'calBP') {
    return `${Math.round(calBP)} cal BP`
  }

  const { year, isCurrentEra } = calBPToYear(calBP)

  if (format === 'bcad') {
    return isCurrentEra ? `AD ${year}` : `${year} BC`
  }

  // bce_ce
  return isCurrentEra ? `${year} CE` : `${year} BCE`
}

/**
 * Format a calendar age range (e.g. an HPD interval) according to the
 * chosen reporting format, always presented in chronological order
 * (older date first) regardless of the numeric order of the inputs.
 *
 * Note: a larger cal BP value is further in the past (an earlier calendar
 * year), so "older first" means descending cal BP / ascending calendar
 * year.
 */
export function formatCalendarRange(
  calBPA: number,
  calBPB: number,
  format: DateFormat
): string {
  if (!Number.isFinite(calBPA) || !Number.isFinite(calBPB)) return 'N/A'

  const older = Math.max(calBPA, calBPB)
  const younger = Math.min(calBPA, calBPB)

  if (format === 'calBP') {
    return `${Math.round(older)}–${Math.round(younger)} cal BP`
  }

  return `${formatCalendarAge(older, format)} – ${formatCalendarAge(younger, format)}`
}

export interface HPDRange {
  min: number
  max: number
  probability_percent: number
}

/**
 * Format a (possibly multimodal) HPD region as one or more calendar-age
 * ranges, each annotated with its own share of the total probability --
 * the standard OxCal/CALIB reporting style, e.g.:
 *   "3360–3300 BC (13.5%), 3270–3100 BC (81.9%)"
 *
 * Calibration curves are non-monotonic (plateaus, wiggles), so a sample's
 * HPD region often isn't one contiguous span; collapsing it to a single
 * min–max range would silently claim probability in the gap between
 * separate peaks. Falls back to 'N/A' if no ranges are available (e.g.
 * results computed before this reporting was added).
 */
export function formatHPDRanges(ranges: HPDRange[] | undefined | null, format: DateFormat): string {
  if (!ranges || ranges.length === 0) return 'N/A'

  return ranges
    .map((r) => `${formatCalendarRange(r.max, r.min, format)} (${r.probability_percent.toFixed(1)}%)`)
    .join(', ')
}

/** Sum of probability_percent across all ranges in an HPD region (e.g. 95.4 for a "95%" request). */
export function totalHPDPercent(ranges: HPDRange[] | undefined | null): number | null {
  if (!ranges || ranges.length === 0) return null
  return ranges.reduce((sum, r) => sum + r.probability_percent, 0)
}

// --- Per-analysis persistence -----------------------------------------
//
// The reporting format is a display preference, not analytical data, so
// rather than adding a backend/schema change just to store it, it's kept
// client-side in localStorage keyed by analysis ID. This is set on the
// "New Analysis" page and read back on the Results page (including after
// a full page reload or a direct link, when React Router navigation state
// wouldn't otherwise survive).

const STORAGE_PREFIX = 'radiocarbon:dateFormat:'

export function saveDateFormatPreference(analysisId: string, format: DateFormat): void {
  try {
    window.localStorage.setItem(`${STORAGE_PREFIX}${analysisId}`, format)
  } catch {
    // localStorage can be unavailable (private browsing, storage full,
    // etc.) -- the preference just won't persist across reloads.
  }
}

export function loadDateFormatPreference(analysisId: string): DateFormat {
  try {
    const stored = window.localStorage.getItem(`${STORAGE_PREFIX}${analysisId}`)
    if (stored === 'calBP' || stored === 'bcad' || stored === 'bce_ce') {
      return stored
    }
  } catch {
    // ignore
  }
  return DEFAULT_DATE_FORMAT
}

// --- Plot axis ---------------------------------------------------------
//
// Posterior distributions are plotted against cal BP, because that is what
// the calibration curve and the model work in. Presenting them in another
// convention is a labelling job: the points stay where they are and only
// the ticks change, so switching format never moves a curve.

/** Axis title for the chosen reporting format. */
export function calendarAxisTitle(format: DateFormat): string {
  if (format === 'bcad') return 'Calendar Age (BC / AD)'
  if (format === 'bce_ce') return 'Calendar Age (BCE / CE)'
  return 'Calendar Age (cal BP)'
}

/**
 * A signed year in *display* numbering -- positive for AD/CE, negative for
 * BC/BCE, and never zero, because the conventional calendar has no year
 * zero. This is not the astronomical year: astronomical -1000 is 1001 BC,
 * so labelling round astronomical years would produce ticks like "1001 BC".
 */
function displayYearFromCalBP(calBP: number): number {
  const signedYear = RADIOCARBON_PRESENT - calBP
  return signedYear > 0 ? signedYear : -(1 - signedYear)
}

function calBPFromDisplayYear(displayYear: number): number {
  return displayYear > 0
    ? RADIOCARBON_PRESENT - displayYear
    : RADIOCARBON_PRESENT - 1 - displayYear
}

/** A round step (1/2/5 x 10^n) giving roughly `target` intervals across `span`. */
function niceStep(span: number, target: number): number {
  if (!(span > 0)) return 1
  const rough = span / target
  const magnitude = Math.pow(10, Math.floor(Math.log10(rough)))
  for (const multiple of [1, 2, 5]) {
    if (multiple * magnitude >= rough) return multiple * magnitude
  }
  return 10 * magnitude
}

/**
 * Tick positions (in cal BP, where the data is plotted) and their labels in
 * the chosen format.
 *
 * Returns null for cal BP: the values already are cal BP, so Plotly's own
 * tick selection is better than anything imposed here. For BC/AD and BCE/CE
 * the ticks are chosen as round years in that convention -- "1000 BC", not
 * the "1051 BC" that rounding in cal BP would give -- and mapped back to the
 * cal BP positions they sit at.
 */
export function calendarAxisTicks(
  minCalBP: number,
  maxCalBP: number,
  format: DateFormat,
  target = 7
): { tickvals: number[]; ticktext: string[] } | null {
  if (format === 'calBP') return null
  if (!Number.isFinite(minCalBP) || !Number.isFinite(maxCalBP) || maxCalBP <= minCalBP) return null

  // Older cal BP is a smaller display year, so the range flips.
  const from = displayYearFromCalBP(maxCalBP)
  const to = displayYearFromCalBP(minCalBP)
  const step = niceStep(to - from, target)

  const tickvals: number[] = []
  const ticktext: string[] = []
  for (let year = Math.ceil(from / step) * step; year <= to; year += step) {
    if (year === 0) continue // no year zero
    const calBP = calBPFromDisplayYear(year)
    if (calBP < minCalBP || calBP > maxCalBP) continue
    tickvals.push(calBP)
    ticktext.push(formatCalendarAge(calBP, format))
  }
  return tickvals.length >= 2 ? { tickvals, ticktext } : null
}
