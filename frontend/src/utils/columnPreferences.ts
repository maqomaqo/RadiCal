/**
 * Which optional columns a table shows, remembered per project.
 *
 * A column choice is a display preference, not analytical data, so it lives in
 * localStorage keyed by project id rather than in the schema -- the same
 * arrangement the reporting format uses (see utils/dateFormat).
 *
 * Only columns the reader has explicitly turned ON or OFF are stored. A column
 * they have never touched keeps following the data, so a collection that gains
 * its first marine date still gains a ΔR column on its own. An override is a
 * decision; an empty record is not a decision to hide everything.
 */

const DATE_PREFIX = 'radiocarbon:dateColumns:'
// A separate prefix rather than a shared one keyed by id: an analysis id and a
// project id come from different tables and could collide, and the two tables
// do not offer the same columns, so one record read as the other would be a
// set of overrides for columns that do not exist.
const RESULT_PREFIX = 'radiocarbon:resultColumns:'

export type ColumnOverrides = Record<string, boolean>

function write(key: string, overrides: ColumnOverrides): void {
  try {
    if (Object.keys(overrides).length === 0) {
      window.localStorage.removeItem(key)
    } else {
      window.localStorage.setItem(key, JSON.stringify(overrides))
    }
  } catch {
    // localStorage can be unavailable (private browsing, storage full); the
    // choice simply will not outlive the visit.
  }
}

function read(key: string): ColumnOverrides {
  try {
    const stored = window.localStorage.getItem(key)
    if (stored) return parseColumnOverrides(stored)
  } catch {
    // ignore
  }
  return {}
}

/** The date collection's table, remembered per project. */
export function saveColumnOverrides(projectId: string, overrides: ColumnOverrides): void {
  write(`${DATE_PREFIX}${projectId}`, overrides)
}

export function loadColumnOverrides(projectId: string): ColumnOverrides {
  return read(`${DATE_PREFIX}${projectId}`)
}

/**
 * The results table, remembered per analysis rather than per project.
 *
 * Per analysis because the choice is about this run: whether the posterior
 * mean is worth a column depends on what was modelled, and two analyses in one
 * project routinely differ on that. It is the same reasoning that keeps the
 * reporting format per analysis (see utils/dateFormat).
 */
export function saveResultColumnOverrides(analysisId: string, overrides: ColumnOverrides): void {
  write(`${RESULT_PREFIX}${analysisId}`, overrides)
}

export function loadResultColumnOverrides(analysisId: string): ColumnOverrides {
  return read(`${RESULT_PREFIX}${analysisId}`)
}

/**
 * Booleans only, from whatever was in storage.
 *
 * Parsed defensively because the value is old data from another visit and
 * possibly another version of the app: a renamed column, a hand-edited entry
 * or a half-written string must degrade to "no opinion on that column" rather
 * than throw on mount and take the whole table with it.
 */
export function parseColumnOverrides(raw: string): ColumnOverrides {
  const parsed: unknown = JSON.parse(raw)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
  const result: ColumnOverrides = {}
  for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (typeof value === 'boolean') result[key] = value
  }
  return result
}

/**
 * Whether a column is drawn: the reader's choice if they made one, and
 * otherwise whether the data earns it.
 */
export function columnVisible(overrides: ColumnOverrides, id: string, auto: boolean): boolean {
  return overrides[id] ?? auto
}
