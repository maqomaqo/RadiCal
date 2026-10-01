// Whether a model has anything for a DURATION or a DIFFERENCE to be about.
//
// Both the Results page and the analysis form ask this, for the same reason
// and about the same models: Results decides whether to offer "turn the option
// on and run it again", and the form decides whether to offer the option at
// all. Two copies would drift into offering a re-run for a setting the other
// page had stopped showing.
//
// It mirrors services/intervals.plan_durations rather than restating it: a
// duration is the interval a pair of Boundaries encloses (a sequence's or a
// phase's), or the span of one phase's own dated events. A model with neither
// reports nothing however the option is set.
//
// A WIGGLE MATCH falls out of that rule rather than needing a case of its own:
// its dates sit in the wiggle sequence rather than in a phase, and it has no
// Boundaries at all. That is the right answer for the right reason -- the
// spacing of its members is an INPUT, the ring counts, not something the model
// infers, so its "duration" is the series span and is known before the run.
import type { MatrixMeasurement, ModelElement } from '@/types'

/**
 * `null` means "cannot tell yet" -- there is no model structure to inspect,
 * which on the New Analysis form is the ordinary state before one is built.
 * Callers should treat that as "offer it", never as "no".
 */
type Placed = Pick<MatrixMeasurement, 'id' | 'element_id' | 'group_id'>

function countDatesIn(measurements: Placed[]): Map<string, number> {
  const datesIn = new Map<string, number>()
  for (const m of measurements) {
    const holder = m.element_id || m.group_id
    if (holder) datesIn.set(holder, (datesIn.get(holder) || 0) + 1)
  }
  return datesIn
}

export function modelCanReportIntervals(
  tree: ModelElement | null | undefined,
  measurements: Placed[]
): boolean | null {
  if (!tree) return null

  const datesIn = countDatesIn(measurements)

  let boundaries = 0
  let phaseWithSeveralDates = false
  const walk = (element: ModelElement) => {
    if (element.element_type === 'boundary') boundaries += 1
    if (element.element_type === 'phase' && (datesIn.get(element.id) || 0) >= 2) {
      phaseWithSeveralDates = true
    }
    for (const child of element.children || []) walk(child)
  }
  walk(tree)

  // Two Boundaries are the fewest that enclose anything; one multi-dated phase
  // is the fewest that has a span of its own.
  return boundaries >= 2 || phaseWithSeveralDates
}

/**
 * Whether a model has anything for a per-phase kernel density estimate to be
 * of: a phase holding two or more dates, the same phases
 * services/kde.report_phases summarises. `null` as above when there is no
 * structure yet.
 */
export function modelCanReportKde(
  tree: ModelElement | null | undefined,
  measurements: Placed[]
): boolean | null {
  if (!tree) return null
  const datesIn = countDatesIn(measurements)
  const walk = (element: ModelElement): boolean =>
    (element.element_type === 'phase' && (datesIn.get(element.id) || 0) >= 2) ||
    (element.children || []).some(walk)
  return walk(tree)
}
