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
// Every series passed in here is a probability DENSITY -- the solver's
// posterior from exact_chain._plottable, and a plain calibration's series
// from CalibrationCurve.get_display_distribution. That second one used to be
// per-point masses, which is why the helpers below originally just summed the
// values: on a uniform grid, and with masses, that was right.
//
// Neither assumption holds in general. The curves are not uniformly spaced
// (IntCal20 and SHCal20 step 1, 5, 10 and 20 years in different ranges), and
// a downsampled window can be uneven too. So these rank by density -- the
// region wanted is the highest-DENSITY one -- while accumulating mass, which
// is density times the calendar width each point stands for. On a uniform
// grid the two coincide, which is what kept it invisible.
//
// Two variants:
//  - hpdRangesFromCurve: the full multi-range breakdown, since a calibration
//    curve is non-monotonic and one measurement can map to several disjoint
//    calendar peaks. A selected run's first/last point is the boundary.
//  - hpdEnvelopeFromHistogram: a single overall envelope, widened by half a
//    bin at each end because a point is the centre of the width it stands
//    for, not its edge. This is the "as a single range" report used for
//    modelled results.
import type { HPDRange } from '@/types'

/**
 * The calendar width each point stands for: half the gap to each neighbour.
 * An edge point is given its one neighbouring half twice rather than half a
 * cell, so it is not systematically under-weighted out of the region.
 *
 * Multiplying a density by this gives the point's probability mass, which is
 * what a confidence level is a share of.
 */
function cellWidths(ages: number[]): number[] {
  return ages.map((_, i) => {
    const lo = i > 0 ? (ages[i] - ages[i - 1]) / 2 : 0
    const hi = i < ages.length - 1 ? (ages[i + 1] - ages[i]) / 2 : 0
    return Math.abs(lo || hi) + Math.abs(hi || lo)
  })
}

function normalizedWeights(weights: number[]): number[] | null {
  const total = weights.reduce((a, b) => a + b, 0)
  if (!(total > 0)) return null
  return weights.map((w) => w / total)
}

/** Indices of `weights`, highest-density first, kept until their
 * cumulative share reaches `confidence` (at least one index is always
 * included, even if its own weight alone already exceeds the target). */
function selectHighestDensity(
  weights: number[],
  confidence: number,
  /** Rank by this instead of by `weights`, when the two differ -- the region
   *  wanted is the HIGHEST-DENSITY one, but what accumulates to the
   *  confidence target is MASS. They coincide only on a uniform grid. */
  rankBy?: number[]
): number[] {
  const order = rankBy ?? weights
  const indices = weights.map((_, i) => i).sort((a, b) => order[b] - order[a])
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
  const densities = distribution.map(([, w]) => w)
  const widths = cellWidths(ages)
  const normWeights = normalizedWeights(densities.map((d, i) => d * widths[i]))
  if (!normWeights) return []

  const selected = selectHighestDensity(normWeights, confidence, densities)
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

  // Rank by density, accumulate by mass -- see the note at the top.
  const widths = cellWidths(ages)
  const densities = distribution.map(([, w]) => w)
  const normWeights = normalizedWeights(densities.map((d, i) => d * widths[i]))
  if (!normWeights) return null

  const selected = selectHighestDensity(normWeights, confidence, densities)
  const binWidth = ages[1] - ages[0]
  const halfBin = binWidth / 2

  const min = Math.min(...selected.map((i) => ages[i])) - halfBin
  const max = Math.max(...selected.map((i) => ages[i])) + halfBin
  return [min, max]
}
