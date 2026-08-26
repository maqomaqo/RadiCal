// The one definition of "this date's diagnostics object to it", shared by the
// results table that reports it and the edit form that acts on it.
//
// It lives here rather than on Results.tsx because the two pages must agree:
// a date chipped "Suspect" in the table is the date the editor has to point
// at, and a threshold changed in one copy would have the editor flagging a
// different set from the table the reader came from.
import type { DateDiagnostics } from '@/types'

// Below this, OxCal's convention is that a date is worth re-examining
// (Bronk Ramsey 1995).
export const POOR_AGREEMENT = 60
// How far above the PRIOR the posterior outlier probability must rise to
// count as the model having found something. Not an absolute threshold: a
// date at prior 1 comes back at posterior 1 by construction, so judging the
// posterior alone would flag it for the analyst's own setting rather than for
// anything the data said. At prior 0 the two are the same number.
const OUTLIER_LIFT_SUSPECT = 0.5
// ...and a rise it is unsure about. Separate rather than folded in, because
// "probably wrong" and "worth a look" are different messages.
const OUTLIER_LIFT_CHECK = 0.2
// A prior at or above this is a deliberate, elevated suspicion, worth saying
// even when the posterior did not move past it, rather than OxCal's routine 5%
// default -- which an analyst commonly turns on across a whole phase and which
// would put a chip on every clean row. Also the line below which
// agreement_index is still independent evidence; see concernFor.
export const NOTABLE_OUTLIER_PRIOR = 0.2

export type Concern = { level: 'suspect' | 'check'; reasons: string[] }

/**
 * Whether a date's own diagnostics say something is wrong with it, and what.
 *
 * The outlier check compares the posterior to the PRIOR the analyst assigned
 * (see OUTLIER_LIFT_SUSPECT), so a date sitting close to its own prior is not
 * flagged here; StructureTableRow shows that as a non-alarming "Assigned" chip,
 * being a fact about configuration rather than a finding.
 *
 * The reasons appear as text beside the colour, since a red row says something
 * is wrong without saying what, and the two diagnostics differ: agreement says
 * the model had to MOVE the date, the outlier probability that it does not
 * BELONG. Above NOTABLE_OUTLIER_PRIOR the two stop being independent -- a date
 * pulled toward its own outlier kernel is expected to drift from its
 * unmodelled calibration -- so agreement counts as evidence only below it.
 */
export function concernFor(result?: DateDiagnostics, outlierPrior?: number | null): Concern | null {
  if (!result) return null
  const reasons: string[] = []
  let level: 'suspect' | 'check' | null = null
  const prior = outlierPrior ?? 0

  const outlier = result.outlier_probability
  if (outlier != null) {
    const lift = outlier - prior
    const withPrior = (message: string) =>
      prior > 0 ? `${message} -- up from the ${Math.round(prior * 100)}% prior it was given` : message
    if (lift >= OUTLIER_LIFT_SUSPECT) {
      level = 'suspect'
      reasons.push(withPrior(`${Math.round(outlier * 100)}% likely an outlier`))
    } else if (lift >= OUTLIER_LIFT_CHECK) {
      level = 'check'
      reasons.push(withPrior(`${Math.round(outlier * 100)}% chance of being an outlier`))
    }
  }

  const agreement = result.agreement_index
  if (agreement != null && agreement < POOR_AGREEMENT && prior < NOTABLE_OUTLIER_PRIOR) {
    level = 'suspect'
    reasons.push(`agreement ${agreement.toFixed(0)}, below ${POOR_AGREEMENT}`)
  }

  return level ? { level, reasons } : null
}

/**
 * Why this date was flagged, as the sentence its chip carries.
 *
 * The reasons used to be printed only in the banner above the table, which
 * meant a reader who found the red row first had a coloured label and no
 * explanation. Said on the chip instead, so the answer is where the question
 * is asked, and the banner is left to do the counting.
 */
export function concernTitle(concern: Concern): string {
  const lead =
    concern.level === 'suspect'
      ? 'The model does not fit this date'
      : 'This date is worth a second look'
  return `${lead}: ${concern.reasons.join('; ')}.`
}
