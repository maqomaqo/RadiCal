import { RADIOCARBON_PRESENT, type DateFormat } from '@/utils/dateFormat'

/**
 * The results table's "P before" column: the probability each posterior puts
 * before a calendar age. Computed by the backend (services/statistics.p_before)
 * so the column and every export read one number; this module only turns what
 * the reader types into cal BP, prints the answer, and remembers the threshold.
 */

/** The probability each row puts before the threshold, by sample and element id. */
export interface PBeforeValues {
  cal_bp: number
  samples: Record<string, number | null>
  elements: Record<string, number | null>
}

const ERA = /^(ad|ce|bc|bce|bp|cal\s*bp)$/

/**
 * A calendar age typed by the reader, as cal BP, or null if it cannot be read.
 *
 * Takes the era named in the text -- "AD 1360", "1360 CE", "500 BC", "590 cal
 * BP" -- and otherwise reads a bare number in the page's own format: years BP
 * on a cal BP page, a year AD or CE on the other two. No year zero, as
 * dateFormat.calBPToYear: 1 BC is the year before AD 1.
 */
export function parseCalendarAge(text: string, format: DateFormat): number | null {
  const cleaned = text.trim().toLowerCase().replace(/\./g, '')
  if (!cleaned) return null
  const match = cleaned.match(/^(?:([a-z ]+?)\s*)?(\d+(?:\.\d+)?)\s*([a-z ]*)$/)
  if (!match) return null
  const [, before = '', digits, after = ''] = match
  const eraText = (before || after).trim()
  if (before && after) return null
  if (eraText && !ERA.test(eraText)) return null
  const value = Number(digits)
  if (!Number.isFinite(value)) return null

  const era = eraText
    ? eraText.startsWith('b') && eraText !== 'bp'
      ? 'bc'
      : eraText === 'ad' || eraText === 'ce'
        ? 'ad'
        : 'bp'
    : format === 'calBP'
      ? 'bp'
      : 'ad'

  if (era === 'bp') return value
  if (value < 1) return null
  return era === 'ad' ? RADIOCARBON_PRESENT - value : RADIOCARBON_PRESENT - 1 + value
}

/**
 * A probability to three places. The ends read "<0.001" and ">0.999" rather
 * than rounding to a certainty the posterior does not claim. Matches
 * services/statistics.format_probability, which prints the PDF's column.
 */
export function formatProbability(p: number | null | undefined): string {
  if (p == null || !Number.isFinite(p)) return ''
  if (p < 0.0005) return p === 0 ? '0' : '<0.001'
  if (p > 0.9995) return p === 1 ? '1' : '>0.999'
  return p.toFixed(3)
}

const PREFIX = 'radiocarbon:pBefore:'

/**
 * The threshold, remembered per analysis in cal BP -- not as typed, so
 * changing the page's date format re-labels it rather than re-reading "1360"
 * as a different year.
 */
export function saveThreshold(analysisId: string, calBP: number | null): void {
  try {
    if (calBP == null) window.localStorage.removeItem(PREFIX + analysisId)
    else window.localStorage.setItem(PREFIX + analysisId, String(calBP))
  } catch {
    // Unavailable storage: the threshold lasts for the visit only.
  }
}

export function loadThreshold(analysisId: string): number | null {
  try {
    const stored = window.localStorage.getItem(PREFIX + analysisId)
    const value = stored == null ? NaN : Number(stored)
    return Number.isFinite(value) ? value : null
  } catch {
    return null
  }
}
