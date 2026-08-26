// Putting a draft's dates in a deliberate order.
//
// Order is worth controlling because it SURVIVES to the output. Within a
// phase, both the results table and the CSV export walk that phase's dates in
// the order the measurement list holds them (see flattenStructure here and
// services/model_tree.flatten_structure on the backend), and the stacked plot
// follows the table. A phase entered in whatever order the dates came off a
// spreadsheet reads as exactly that.
//
// There is only ONE order to control, which is why this sorts a flat list and
// nothing here knows about phases. A phase's date list is a filter over the
// measurement array (ModelTreeEditor's datesIn), not a stored sequence of its
// own -- so sorting the array sorts every phase at once, and no phase can end
// up ordered by a different rule than its neighbour.
import type { MatrixMeasurement } from '@/types'

export type MeasurementSortKey = 'age_desc' | 'age_asc' | 'name_asc' | 'name_desc'

/**
 * The sort choices as the menus offer them.
 *
 * Worded as "oldest/youngest" rather than "ascending/descending": these are
 * uncalibrated radiocarbon ages BP, where a HIGHER number is OLDER, so
 * "ascending" would name the younger-first option and mean the opposite of
 * what a reader expects from a column of dates.
 */
export const MEASUREMENT_SORT_OPTIONS: { key: MeasurementSortKey; label: string }[] = [
  { key: 'age_desc', label: 'Oldest first (highest BP)' },
  { key: 'age_asc', label: 'Youngest first (lowest BP)' },
  { key: 'name_asc', label: 'Name A–Z' },
  { key: 'name_desc', label: 'Name Z–A' },
]

// Numeric-aware so a run of lab codes sorts the way it is read: plain string
// order puts OxA-10 before OxA-9, which is the one thing a tidy-up like this
// is expected to fix.
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/**
 * A copy of `measurements` in the requested order.
 *
 * Sorting by age uses the LABORATORY age, since at data entry there is no
 * calibrated result to sort by. That is a close proxy for calendar order but
 * not identical to it: the calibration curve is not monotonic, so two dates a
 * few decades apart in BP can calibrate the other way round. It orders the
 * entry list and the phases, not the conclusions.
 *
 * Stable: equal keys keep their existing relative order, so re-sorting by the
 * same key never shuffles anything. Dates with no usable age are held at the
 * end in both directions rather than being treated as age zero -- a row
 * missing its measurement should not be able to claim to be the oldest.
 */
export function sortMeasurements<T extends MatrixMeasurement>(
  measurements: T[],
  key: MeasurementSortKey
): T[] {
  const indexed = measurements.map((measurement, index) => ({ measurement, index }))

  indexed.sort((a, b) => {
    let ordering = 0

    if (key === 'age_desc' || key === 'age_asc') {
      const ageA = a.measurement.age_bp
      const ageB = b.measurement.age_bp
      const validA = Number.isFinite(ageA)
      const validB = Number.isFinite(ageB)
      if (!validA || !validB) {
        ordering = validA === validB ? 0 : validA ? -1 : 1
      } else {
        ordering = key === 'age_desc' ? ageB - ageA : ageA - ageB
      }
    } else {
      ordering = collator.compare(
        a.measurement.sample_name || '',
        b.measurement.sample_name || ''
      )
      if (key === 'name_desc') ordering = -ordering
    }

    return ordering !== 0 ? ordering : a.index - b.index
  })

  return indexed.map((entry) => entry.measurement)
}

/**
 * Whether `measurements` is already in this order, so a control can say that
 * pressing it would do nothing rather than appearing to fail.
 */
export function isSortedBy(
  measurements: MatrixMeasurement[],
  key: MeasurementSortKey
): boolean {
  const sorted = sortMeasurements(measurements, key)
  return sorted.every((m, index) => m.id === measurements[index].id)
}
