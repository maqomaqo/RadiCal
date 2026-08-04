// Client-side HPD (highest posterior density) computation at an
// arbitrary, user-selected confidence level (68.2% / 95.4% / 99.7%),
// applied to a distribution the backend already sent in full -- so
// switching confidence in the Results table UI is instant, with no
// re-run or network round trip. Mirrors the selection logic in
// backend/app/services/calibration.py's hpd_ranges_from_distribution
// (rank points by density, take the highest-density ones first until
// their cumulative share reaches the target confidence), applied to
// whichever binned/gridded distribution the caller passes in.
//
// Two variants, because the two distributions this app sends have
// different point semantics:
//  - hpdRangesFromCurve: for the "unmodelled calibration" distribution
//    (backend's CalibrationCurve.get_display_distribution), whose points
//    are themselves actual curve grid points -- a genuinely discrete
//    series, so a selected run's first/last point IS the range boundary.
//    Returns the full multi-range breakdown (a calibration curve is
//    non-monotonic, so this can be several disjoint ranges).
//  - hpdEnvelopeFromHistogram: for a Bayesian posterior distribution
//    (bayesian.py's _summarize_posterior), whose points are centers of a
//    uniform-width histogram over raw MCMC draws -- using a center as a
//    boundary would silently shrink the range by half a bin on each
//    side, so this widens by half the bin width. Returns a single
//    overall envelope (the user's preferred "as a single range" report
//    for modelled/posterior results), not the segment-by-segment
//    breakdown.
import type { HPDRange } from '@/types'

function normalizedWeights(weights: number[]): number[] | null {
  const total = weights.reduce((a, b) => a + b, 0)
  if (!(total > 0)) return null
  return weights.map((w) => w / total)
}

/** Indices of `weights`, highest-density first, kept until their
 * cumulative share reaches `confidence` (at least one index is always
 * included, even if its own weight alone already exceeds the target). */
function selectHighestDensity(weights: number[], confidence: number): number[] {
  const indices = weights.map((_, i) => i).sort((a, b) => weights[b] - weights[a])
  const selected: number[] = []
  let cumsum = 0
  for (const i of indices) {
    selected.push(i)
    cumsum += weights[i]
    if (cumsum >= confidence) break
  }
  return selected
}

export function hpdRangesFromCurve(
  distribution: Array<[number, number]> | undefined | null,
  confidence: number
): HPDRange[] {
  if (!distribution || distribution.length === 0) return []
  const ages = distribution.map(([a]) => a)
  const weights = distribution.map(([, w]) => w)
  const normWeights = normalizedWeights(weights)
  if (!normWeights) return []

  const selected = selectHighestDensity(normWeights, confidence)
  const selectedSorted = [...selected].sort((a, b) => a - b)

  const segments: Array<[number, number]> = []
  let start = selectedSorted[0]
  let prev = selectedSorted[0]
  for (let k = 1; k < selectedSorted.length; k++) {
    const i = selectedSorted[k]
    if (i === prev + 1) {
      prev = i
      continue
    }
    segments.push([start, prev])
    start = prev = i
  }
  segments.push([start, prev])

  const ranges: HPDRange[] = segments.map(([s, e]) => {
    let segMass = 0
    for (let k = s; k <= e; k++) segMass += normWeights[k]
    return { min: ages[s], max: ages[e], probability_percent: segMass * 100 }
  })
  ranges.sort((a, b) => b.max - a.max)
  return ranges
}

export function hpdEnvelopeFromHistogram(
  distribution: Array<[number, number]> | undefined | null,
  confidence: number
): [number, number] | null {
  if (!distribution || distribution.length < 2) return null
  const ages = distribution.map(([a]) => a)
  const weights = distribution.map(([, w]) => w)
  const normWeights = normalizedWeights(weights)
  if (!normWeights) return null

  const selected = selectHighestDensity(normWeights, confidence)
  const binWidth = ages[1] - ages[0]
  const halfBin = binWidth / 2

  const min = Math.min(...selected.map((i) => ages[i])) - halfBin
  const max = Math.max(...selected.map((i) => ages[i])) + halfBin
  return [min, max]
}
