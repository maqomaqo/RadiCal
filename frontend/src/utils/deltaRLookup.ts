/**
 * How a derived ΔR is presented: which habits get a marker, what the
 * counterfactual line says, what the selection's position is.
 *
 * Its own module, importing nothing, because these are the judgements the
 * panel is obliged to get right and they are worth asserting without a DOM.
 * The arithmetic is entirely the server's -- see services/reservoir.py -- and
 * nothing here recomputes any part of it.
 */

import type { FeedingClass, LookupPoint, LookupResult } from '@/api/reservoir'

/**
 * The habits that carry a marker in the point list.
 *
 * Measured, not assumed (plan §2.7): against the suspension points near them,
 * deposit feeders sit +35 to +42 yr older, carnivores +34 to +40, migratory
 * +42 to +55. Grazers and unknown are indistinguishable from the suspension
 * pool at every radius, so marking them would be inventing a suspicion the
 * data does not support -- and `unknown` is 14% of the database.
 */
export const MARKED_HABITS: FeedingClass[] = ['deposit', 'migratory', 'carnivore']

/**
 * Why a marked habit is marked, in one line.
 *
 * Migratory's reason is different in kind from the other two and is worth
 * saying separately: its case rests on an a-priori argument -- the animal may
 * not have lived where it was found -- rather than on the paired statistics,
 * because whale, walrus and seal bone is never sampled from the same beach as
 * a bivalve and it has no co-located pairs at all.
 */
export const HABIT_REASONS: Record<string, string> = {
  deposit: 'Deposit feeders can take up old carbon from sediment: about +35 yr on average, but unpredictably per sample.',
  migratory: 'A migratory animal may not have lived where it was found.',
  carnivore: 'Carnivores sit about +35 yr older than nearby suspension feeders.',
}

/** Whether this point's habit is one the panel marks. */
export function isMarkedHabit(habit: FeedingClass | null | undefined): boolean {
  return !!habit && MARKED_HABITS.includes(habit)
}

/**
 * Whether a point could enter the weighted mean at all.
 *
 * A point with no published uncertainty cannot be weighted -- 1/σ² is not a
 * number when σ is absent -- so it is shown struck through rather than
 * silently dropped: a nearest-5 that quietly became a nearest-4 would
 * misreport how much evidence is behind the value.
 */
export function isWeighable(point: LookupPoint): boolean {
  return point.delta_r !== null && !!point.delta_r_error
}

/**
 * What a point's habit cell says.
 *
 * `ambiguous` shows the source's own truncated string rather than the word
 * "ambiguous", because that string is the evidence: it is a compound judgement
 * the original author wrote and the source's column width cut off mid-word,
 * and a reader deciding whether to exclude the point needs to see it.
 */
export function habitLabel(point: LookupPoint): string {
  if (point.feeding === 'ambiguous') return point.feeding_raw || 'ambiguous'
  return point.feeding || 'unknown'
}

/**
 * The counterfactual, as the one line §7.3 asks for:
 *
 *     Suspension feeders only (7 of 10): −188 ± 61
 *
 * Null when there is nothing to compare -- either nothing marked was in the
 * set, or dropping it would have left too few points to weight, which is a
 * different sentence the panel says instead.
 */
export function counterfactualLine(result: LookupResult): string | null {
  const alternative = result.suspension_equivalent
  if (!alternative || alternative.delta_r === null) return null
  if (!result.included_all) return null
  if (alternative.n === result.included_all.n) return null
  return (
    `Suspension feeders only (${alternative.n} of ${result.included_all.n}): `
    + `${formatDeltaR(alternative.delta_r, alternative.delta_r_error)}`
  )
}

/** "−188 ± 61", rounded as calib.org prints it. */
export function formatDeltaR(
  value: number | null,
  error: number | null,
): string {
  if (value === null) return '—'
  const rounded = Math.round(value)
  if (error === null) return `${rounded}`
  return `${rounded} ± ${Math.round(error)}`
}

/**
 * Which marked habits are in the set, and how many points each contributes.
 *
 * What the warning names when the counterfactual differs by enough to matter:
 * "3 deposit, 1 migratory" is actionable where "some biased points" is not.
 */
export function markedBreakdown(points: LookupPoint[]): Array<[string, number]> {
  const counts = new Map<string, number>()
  for (const point of points) {
    if (!isMarkedHabit(point.feeding) || !isWeighable(point)) continue
    const habit = point.feeding as string
    counts.set(habit, (counts.get(habit) ?? 0) + 1)
  }
  // Migratory first, per §5.4.1: its reason is a-priori rather than
  // statistical, so it is the one a reader should weigh differently.
  const order = ['migratory', 'deposit', 'carnivore']
  return [...counts.entries()].sort(
    (a, b) => order.indexOf(a[0]) - order.indexOf(b[0]),
  )
}

/**
 * The position to look up for a selection of dates.
 *
 * Dates from one site share a coordinate, so a selection usually has exactly
 * one. When it has more than one it has no single answer, and the panel says
 * so rather than averaging: a centroid of two sites is a place neither of them
 * is, and deriving a reservoir offset for it would be deriving it for nowhere.
 */
export function selectionCentre(
  dates: Array<{ latitude: number | null; longitude: number | null }>,
): { latitude: number; longitude: number } | null {
  const placed = dates.filter(
    (d) => d.latitude !== null && d.longitude !== null,
  ) as Array<{ latitude: number; longitude: number }>
  if (placed.length === 0) return null

  const distinct = new Set(placed.map((d) => `${d.latitude},${d.longitude}`))
  if (distinct.size !== 1) return null
  return { latitude: placed[0].latitude, longitude: placed[0].longitude }
}

/**
 * Why a selection cannot be looked up, in the words the panel shows. Null when
 * it can be.
 */
export function centreProblem(
  dates: Array<{ latitude: number | null; longitude: number | null }>,
): string | null {
  const placed = dates.filter((d) => d.latitude !== null && d.longitude !== null)
  if (placed.length === 0) {
    return 'None of the selected dates has a position. Place them on the map, or type a coordinate, and the offset can be derived from where they are.'
  }
  if (placed.length !== dates.length) {
    return `${dates.length - placed.length} of the ${dates.length} selected dates has no position. Select only the placed ones, or place the rest.`
  }
  if (selectionCentre(dates) === null) {
    return 'The selected dates are at more than one position, so there is no single place to derive an offset for. Select one site at a time.'
  }
  return null
}

/**
 * Beyond this, the distance to the nearest point is worth flagging.
 *
 * §2.6: Tampa's nearest reservoir measurement is 163 km away, and the p90
 * nearest-neighbour distance across the database is 223 km, so this is not
 * rare. 100 km is about where a local offset stops being local -- near enough
 * that a reader should look at the distance rather than a number chosen to
 * fire rarely.
 */
export const FAR_NEAREST_KM = 100

export function nearestIsFar(result: LookupResult): boolean {
  return result.nearest_km !== null && result.nearest_km >= FAR_NEAREST_KM
}
