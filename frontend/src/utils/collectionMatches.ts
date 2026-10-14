/**
 * Which incoming dates a project's collection already holds.
 *
 * Matched by sample name, case and surrounding spaces ignored -- the rule
 * New Analysis uses when it stores a draft's dates in the collection, and the
 * one a lab code is written to satisfy. A name is not proof the values agree,
 * so each match also says which of the values that decide a calibration
 * differ: skipping a corrected age silently would keep the wrong one.
 */

export interface DateValues {
  sample_name: string
  age_bp: number
  error_1sigma: number
  curve_id: string
}

export interface CollectionMatch<T extends DateValues> {
  date: T
  existing: DateValues
  /** Which values differ, as words: 'age', 'error', 'curve'. */
  differs: string[]
}

export const nameKey = (name: string) => name.trim().toLowerCase()

export function splitByCollection<T extends DateValues>(
  dates: T[],
  collection: DateValues[],
): { fresh: T[]; matched: CollectionMatch<T>[] } {
  const byName = new Map(collection.map((s) => [nameKey(s.sample_name), s]))
  const fresh: T[] = []
  const matched: CollectionMatch<T>[] = []
  for (const date of dates) {
    const existing = byName.get(nameKey(date.sample_name))
    if (!existing) {
      fresh.push(date)
      continue
    }
    const differs: string[] = []
    if (existing.age_bp !== date.age_bp) differs.push('age')
    if (existing.error_1sigma !== date.error_1sigma) differs.push('error')
    if (existing.curve_id !== date.curve_id) differs.push('curve')
    matched.push({ date, existing, differs })
  }
  return { fresh, matched }
}

/** One match as a line: the name, and what differs from the stored date. */
export function describeMatch<T extends DateValues>(match: CollectionMatch<T>): string {
  const { date, existing, differs } = match
  if (differs.length === 0) return date.sample_name
  const parts = differs.map((field) =>
    field === 'age'
      ? `age ${date.age_bp} here, ${existing.age_bp} stored`
      : field === 'error'
        ? `error ${date.error_1sigma} here, ${existing.error_1sigma} stored`
        : `curve ${date.curve_id} here, ${existing.curve_id} stored`
  )
  return `${date.sample_name} (${parts.join('; ')})`
}
