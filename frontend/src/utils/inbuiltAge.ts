/**
 * A date's inbuilt-age allowance (feature spec M1): how much older than its
 * event the dated material may be. Charcoal from heartwood dates the wood, not
 * the fire. Applied in Bayesian models (backend exact_chain.inbuilt_kernel).
 *
 *   uniform      any offset from 0 to `years`, equally likely, never more:
 *                a published MAXIMUM inbuilt age.
 *   exponential  offsets averaging `years`, small ones likeliest, no ceiling:
 *                a TYPICAL age with no maximum known.
 */
export type InbuiltShape = 'uniform' | 'exponential'

export const INBUILT_SHAPES: Array<{ value: InbuiltShape; label: string }> = [
  { value: 'uniform', label: 'Up to (uniform)' },
  { value: 'exponential', label: 'About (exponential)' },
]

/** What the allowance says, in one sentence for beside the field. */
export function describeInbuilt(years: number | null | undefined, shape?: string | null): string {
  if (!years || years <= 0) {
    return 'None. The material is taken to be the same age as its event.'
  }
  const n = Math.round(years * 10) / 10
  return shape === 'exponential'
    ? `The material is most likely young: on average ${n} years older than its event, occasionally much older.`
    : `The material may be up to ${n} years older than its event, any age in that range equally likely.`
}

/** A short form for a table cell: "≤ 30 yr" or "~ 15 yr". */
export function inbuiltCell(years: number | null | undefined, shape?: string | null): string {
  if (!years || years <= 0) return ''
  return `${shape === 'exponential' ? '~' : '≤'} ${Math.round(years * 10) / 10} yr`
}
