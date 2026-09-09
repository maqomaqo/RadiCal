/**
 * Presenting calendar ages in the user's chosen reporting format: cal BP,
 * BC/AD, or BCE/CE.
 *
 * Everything is computed and stored as cal BP, which is what the curves and
 * the model work in, so this module affects DISPLAY only.
 */
import { niceStep } from './linearTicks'

export type DateFormat = 'calBP' | 'bcad' | 'bce_ce'

export const DATE_FORMAT_OPTIONS: { value: DateFormat; label: string }[] = [
  { value: 'calBP', label: 'cal BP (years before 1950)' },
  { value: 'bcad', label: 'BC / AD' },
  { value: 'bce_ce', label: 'BCE / CE' },
]

export const DEFAULT_DATE_FORMAT: DateFormat = 'calBP'

// Radiocarbon "Present" is fixed at 1950 by convention, whenever the
// measurement was actually taken.
const RADIOCARBON_PRESENT = 1950

/**
 * cal BP to a proleptic Gregorian year in the conventional BC/AD numbering,
 * with no year zero: 1 BC is followed by AD 1. BCE/CE use the same numbers and
 * differ only in era label, so one conversion backs both formats.
 */
function calBPToYear(calBP: number): { year: number; isCurrentEra: boolean } {
  const signedYear = RADIOCARBON_PRESENT - calBP
  if (signedYear > 0) {
    return { year: Math.round(signedYear), isCurrentEra: true }
  }
  // No year zero: a signedYear of 0 or below maps to 1 BC/BCE, -1, etc.
  return { year: Math.round(1 - signedYear), isCurrentEra: false }
}

/** One calendar age, in the chosen reporting format. */
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
 * A calendar age range in the chosen format, always older date first
 * whatever the numeric order of the inputs. A larger cal BP is further in the
 * past, so "older first" is descending cal BP.
 *
 * `compact` names the era once rather than twice ("AD 1189–1408" rather than
 * "AD 1189 – AD 1408"), for places that repeat a range down a list. It applies
 * only when both ends share an era; see compactEraRange.
 */
export function formatCalendarRange(
  calBPA: number,
  calBPB: number,
  format: DateFormat,
  options: { compact?: boolean } = {}
): string {
  if (!Number.isFinite(calBPA) || !Number.isFinite(calBPB)) return 'N/A'

  const older = Math.max(calBPA, calBPB)
  const younger = Math.min(calBPA, calBPB)

  if (format === 'calBP') {
    // Already compact: cal BP names its unit once, at the end.
    return `${Math.round(older)}–${Math.round(younger)} cal BP`
  }

  if (options.compact) {
    const compact = compactEraRange(older, younger, format)
    if (compact) return compact
  }

  return `${formatCalendarAge(older, format)} – ${formatCalendarAge(younger, format)}`
}

/**
 * "AD 1189–1408" / "1250–850 BC" -- the era named once, where convention puts
 * it: before the number for AD/CE, after it for BC/BCE.
 *
 * Only defined when both ends share an era. A range straddling the boundary
 * has no single label to hoist, so this returns null and the caller spells
 * both ends out: compactness is never worth the wrong century.
 */
function compactEraRange(
  olderCalBP: number,
  youngerCalBP: number,
  format: DateFormat
): string | null {
  const older = calBPToYear(olderCalBP)
  const younger = calBPToYear(youngerCalBP)
  if (older.isCurrentEra !== younger.isCurrentEra) return null

  if (older.isCurrentEra) {
    const prefix = format === 'bcad' ? 'AD' : ''
    const years = `${older.year}–${younger.year}`
    return prefix ? `${prefix} ${years}` : `${years} CE`
  }

  const suffix = format === 'bcad' ? 'BC' : 'BCE'
  return `${older.year}–${younger.year} ${suffix}`
}

export interface HPDRange {
  min: number
  max: number
  probability_percent: number
}

/**
 * A possibly multimodal HPD region as calendar-age ranges, each with its own
 * share of the probability, in OxCal/CALIB style:
 *   "3360–3300 BC (13.5%), 3270–3100 BC (81.9%)"
 *
 * Curves are non-monotonic, so an HPD region is often not one contiguous span,
 * and collapsing it to a single min–max range would claim probability in the
 * gap between peaks. 'N/A' when no ranges are available.
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
// The reporting format is a display preference rather than analytical data,
// so it is kept in localStorage keyed by analysis id rather than in the
// schema. Set on the New Analysis page and read back on Results, including
// after a reload or a direct link where router state would not survive.

const STORAGE_PREFIX = 'radiocarbon:dateFormat:'

export function saveDateFormatPreference(analysisId: string, format: DateFormat): void {
  try {
    window.localStorage.setItem(`${STORAGE_PREFIX}${analysisId}`, format)
  } catch {
    // localStorage can be unavailable (private browsing, storage full); the
    // preference simply will not persist.
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
// Posteriors are plotted against cal BP, which is what the curve and the model
// work in, so another convention is a labelling job: the points stay put and
// only the ticks change.

/** Axis title for the chosen reporting format. */
export function calendarAxisTitle(format: DateFormat): string {
  if (format === 'bcad') return 'Calendar Age (BC / AD)'
  if (format === 'bce_ce') return 'Calendar Age (BCE / CE)'
  return 'Calendar Age (cal BP)'
}

/**
 * A signed year in DISPLAY numbering: positive for AD/CE, negative for BC/BCE,
 * never zero. Not the astronomical year -- astronomical -1000 is 1001 BC, so
 * round astronomical years would produce ticks like "1001 BC".
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

/**
 * Tick positions in cal BP, where the data is plotted, with labels in the
 * chosen format. Null for cal BP, where Plotly's own tick selection is better.
 * For BC/AD and BCE/CE the ticks are round years in that convention -- "1000
 * BC", not the "1051 BC" rounding in cal BP would give -- mapped back to the
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

/**
 * Ticks for a calendar ruler drawn by the app itself, rather than by Plotly.
 *
 * calendarAxisTicks above returns null for cal BP, because Plotly picks
 * better cal BP ticks than anything imposed on it. A ruler the app draws has
 * no such fallback -- it needs positions and labels in every format -- so
 * this always returns both.
 *
 * Positions are cal BP because that is the space the marks are drawn in.
 * Only the labels change with the format, so switching the reporting format
 * relabels a ruler without moving anything under it.
 */
export function calendarRulerTicks(
  minCalBP: number,
  maxCalBP: number,
  format: DateFormat,
  target = 6
): { calBP: number; label: string }[] {
  if (!Number.isFinite(minCalBP) || !Number.isFinite(maxCalBP) || maxCalBP <= minCalBP) {
    return []
  }

  if (format === 'calBP') {
    const step = niceStep(maxCalBP - minCalBP, target)
    const ticks: { calBP: number; label: string }[] = []
    for (let value = Math.ceil(minCalBP / step) * step; value <= maxCalBP; value += step) {
      ticks.push({ calBP: value, label: `${Math.round(value)}` })
    }
    return ticks
  }

  // Round years in the display convention, not round cal BP values: a
  // reader looking for a century wants "1300", not the "1297" that rounding
  // in cal BP and converting would land on.
  const from = displayYearFromCalBP(maxCalBP)
  const to = displayYearFromCalBP(minCalBP)
  const step = niceStep(to - from, target)

  const ticks: { calBP: number; label: string }[] = []
  for (let year = Math.ceil(from / step) * step; year <= to; year += step) {
    if (year === 0) continue // no year zero
    const calBP = calBPFromDisplayYear(year)
    if (calBP < minCalBP || calBP > maxCalBP) continue
    ticks.push({ calBP, label: `${Math.abs(year)}` })
  }
  return ticks
}

// --- One calendar scale, two renderings --------------------------------
//
// A stacked figure taller than the screen needs its calendar scale twice: on
// the axis Plotly draws at the foot of the figure, and on the ruler the app
// pins above it while the reader scrolls. Those two must be the SAME scale --
// a ruler whose graduations sit a few years off the figure's is worse than no
// ruler at all -- so both are built from the ticks below rather than each
// choosing its own.

/** One graduation on a calendar scale: where it sits, and what it says. */
export interface CalendarTick {
  /** Position in cal BP, the space everything is plotted in. */
  calBP: number
  /** The number alone, in the reporting convention, never signed. */
  value: string
  /**
   * The era qualifying that number, or null where the scale needs none. cal BP
   * needs none: it has one direction and its unit is named in the axis title,
   * where BC and AD are two directions from a point in the middle of the axis
   * and cannot be inferred from a number.
   */
  era: string | null
  /** Whether convention puts the era before the number (AD 1000) or after it
   *  (1000 BC). */
  eraFirst: boolean
}

/**
 * Ticks for a calendar scale, in every reporting format.
 *
 * Round years in the DISPLAY convention rather than round cal BP values, so a
 * BC/AD axis is graduated at "1000 BC" and not at the "1051 BC" that rounding
 * in cal BP and converting would land on. For cal BP the two are the same
 * thing, so it graduates in round cal BP.
 *
 * Every BC/AD tick carries its era. Naming it once and leaving the rest bare
 * was tried on paper and rejected: an axis running from 3000 BC to AD 500
 * would then print "500" twice, two millennia apart, and the reader has to
 * find the last era word to their left to know which one they are looking at.
 * The era is demoted typographically instead -- see calendarTickMarkup.
 */
export function calendarScaleTicks(
  minCalBP: number,
  maxCalBP: number,
  format: DateFormat,
  target = 7
): CalendarTick[] {
  if (!Number.isFinite(minCalBP) || !Number.isFinite(maxCalBP) || maxCalBP <= minCalBP) {
    return []
  }

  if (format === 'calBP') {
    const step = niceStep(maxCalBP - minCalBP, target)
    const ticks: CalendarTick[] = []
    for (let value = Math.ceil(minCalBP / step) * step; value <= maxCalBP; value += step) {
      ticks.push({ calBP: value, value: `${Math.round(value)}`, era: null, eraFirst: false })
    }
    return ticks
  }

  const from = displayYearFromCalBP(maxCalBP)
  const to = displayYearFromCalBP(minCalBP)
  const step = niceStep(to - from, target)

  const ticks: CalendarTick[] = []
  for (let year = Math.ceil(from / step) * step; year <= to; year += step) {
    if (year === 0) continue // no year zero
    const calBP = calBPFromDisplayYear(year)
    if (calBP < minCalBP || calBP > maxCalBP) continue
    const currentEra = year > 0
    ticks.push({
      calBP,
      value: `${Math.abs(year)}`,
      era: currentEra ? (format === 'bcad' ? 'AD' : 'CE') : format === 'bcad' ? 'BC' : 'BCE',
      // "AD 1000", but "1000 CE" -- BCE/CE are suffixes in both directions.
      eraFirst: currentEra && format === 'bcad',
    })
  }
  return ticks
}

/**
 * A tick as Plotly should letter it: the number at full strength, the era a
 * size down and lighter.
 *
 * The era repeats on every tick because dropping it makes a number ambiguous
 * (see calendarScaleTicks), but repeated at the number's own weight it doubles
 * the ink on the busiest line of the figure and reads as eight two-word labels
 * rather than a scale. Demoted, the numbers line up as a ruler and the era is
 * still on every graduation for anyone reading one tick on its own.
 *
 * Plotly's SVG text supports a small subset of HTML; `span` with `font-size`
 * and `color` is in it. Lives here, beside the spelling, so the figure's axis
 * and the app's own ruler cannot letter one tick two ways.
 */
export function calendarTickMarkup(
  tick: CalendarTick,
  era: { size: number; color: string }
): string {
  if (!tick.era) return tick.value
  const mark = `<span style="font-size:${era.size}px;color:${era.color}">${tick.era}</span>`
  return tick.eraFirst ? `${mark} ${tick.value}` : `${tick.value} ${mark}`
}
