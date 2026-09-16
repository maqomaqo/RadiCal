/**
 * What the date collection's table can be ordered by, and how.
 *
 * Display only: the collection is a store, not a sequence -- an analysis owns
 * the order its own dates are modelled in, which is what `SortDatesButton`
 * edits elsewhere. Nothing here is written back.
 *
 * Its own module rather than a function in the table because the table
 * reaches Plotly through the map dialog, and anything a Node test needs has
 * to live outside it.
 */

export type SortKey =
  | 'sample_name'
  | 'age_bp'
  | 'error_1sigma'
  | 'default_context_name'
  | 'location'
  | 'position'

export type SortDirection = 'asc' | 'desc'

/** A date, as sorting needs it: the columns that can be sorted on. */
export interface SortableSample {
  sample_name: string
  age_bp: number
  error_1sigma: number
  default_context_name: string | null
  location: string | null
  latitude: number | null
  longitude: number | null
}

// Numeric collation so a lab code ending in 7 sorts before one ending in 12,
// which is the whole reason anyone sorts a list of lab codes; base sensitivity
// so "kealakekua" and "Kealakekua" -- the same site, typed twice -- sit
// together rather than a case-fold apart.
const collate = (a: string, b: string) =>
  a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })

/**
 * Whether a date has nothing in the column being sorted on. These go to the
 * bottom whichever way the column is sorted: an unplaced date is not "south
 * of everything" in one direction and "north of everything" in the other, it
 * is unplaced.
 */
function missing(sample: SortableSample, key: SortKey): boolean {
  switch (key) {
    case 'default_context_name':
      return !sample.default_context_name
    case 'location':
      return !sample.location
    case 'position':
      return sample.latitude == null || sample.longitude == null
    default:
      return false
  }
}

/** Compare two dates that both HAVE the column. */
function compare(a: SortableSample, b: SortableSample, key: SortKey): number {
  switch (key) {
    case 'age_bp':
    case 'error_1sigma':
      return a[key] - b[key]
    case 'sample_name':
      return collate(a.sample_name, b.sample_name)
    case 'default_context_name':
      return collate(a.default_context_name ?? '', b.default_context_name ?? '')
    case 'location':
      return collate(a.location ?? '', b.location ?? '')
    case 'position':
      // South to north, then west to east. The order itself matters less than
      // the effect: every date at one position lands in one run of rows,
      // and neighbouring sites land near each other, which is what makes a
      // range selection down the column select a site.
      return (
        (a.latitude as number) - (b.latitude as number) ||
        (a.longitude as number) - (b.longitude as number)
      )
  }
}

/**
 * The collection in display order. Stable, so dates that tie on the sort
 * column keep the order they arrived in; unchanged when there is no sort.
 */
export function sortSamples<T extends SortableSample>(
  samples: T[],
  key: SortKey | null,
  direction: SortDirection,
): T[] {
  if (!key) return samples
  const factor = direction === 'asc' ? 1 : -1
  return [...samples].sort((a, b) => {
    const aMissing = missing(a, key)
    const bMissing = missing(b, key)
    if (aMissing !== bMissing) return aMissing ? 1 : -1
    if (aMissing) return 0
    return factor * compare(a, b, key)
  })
}
