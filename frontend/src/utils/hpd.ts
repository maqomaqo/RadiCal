// Client-side HPD (highest posterior density) computation at the reader's
// selected confidence level, applied to a distribution the backend already
// sent in full -- so switching confidence in the Results table is instant.
// Mirrors the selection in calibration.py's hpd_ranges_from_distribution.
//
// Every series passed in is a probability DENSITY. The curves are not
// uniformly spaced and a downsampled window can be uneven too, so these rank
// by density -- the region wanted is the highest-DENSITY one -- while
// accumulating MASS, which is density times the calendar width each point
// stands for. The two coincide only on a uniform grid.
//
// Two variants: hpdRangesFromCurve gives the full multi-range breakdown, a
// non-monotonic curve mapping one measurement to several disjoint peaks, and
// hpdEnvelopeFromHistogram a single envelope widened by half a bin at each end
// because a point is the centre of the width it stands for, not its edge.
import type { HPDRange } from '@/types'

/**
 * The calendar width each point stands for: half the gap to each neighbour, an
 * edge point taking its one neighbouring half twice so it is not
 * systematically under-weighted out of the region. Density times this is the
 * point's probability mass, which is what a confidence level is a share of.
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

/** Indices of `weights`, highest-density first, kept until their cumulative
 * share reaches `confidence`. At least one is always included. */
function selectHighestDensity(
  weights: number[],
  confidence: number,
  /** Rank by this instead of `weights` when the two differ: the region wanted
   *  is the highest-DENSITY one, but what accumulates to the target is MASS. */
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

/**
 * The three coverages this app reports HPD regions at, and their labels.
 * "1 sigma" is the two-sided normal coverage of one standard deviation,
 * erf(1/sqrt(2)) = 0.6826894921, which ROUNDS to 68.3%. Likewise 95.4%, 99.7%.
 *
 * `confidence` is the threshold the HPD set is accumulated to, so the digits
 * change the reported range. Mirrors SIGMA_1/2/3 in
 * backend/app/services/calibration.py, where they are derived; JavaScript has
 * no erf, so they are written out here and pinned by a test. Both sides must
 * agree, since the page re-selects a region from a backend-computed
 * distribution.
 */
export const SIGMA_1 = 0.6826894921370859
export const SIGMA_2 = 0.9544997361036416
export const SIGMA_3 = 0.9973002039367398

export const CONFIDENCE_LEVELS = [
  { value: SIGMA_1, label: '68.3%' },
  { value: SIGMA_2, label: '95.4%' },
  { value: SIGMA_3, label: '99.7%' },
] as const

/**
 * The tighter of the two intervals a posterior plot draws: 1 sigma, fixed. The
 * outer band follows the reader's selector; this stays put as a constant
 * reference to read the selected level against.
 */
export const INNER_CONFIDENCE = SIGMA_1
export const INNER_CONFIDENCE_LABEL = '68.3%'

/**
 * One HPD band on a plot: the ranges to draw, and the level to call them. The
 * label travels WITH the ranges rather than living in the plot components, so
 * a legend cannot name a level the geometry was not computed at.
 */
export interface HPDBand {
  /** The disjoint calendar ranges holding this share of the posterior. */
  ranges: HPDRange[]
  /** How the level is written on screen. The legend says this and nothing else. */
  label: string
  /** 'outer' is the level the reader selected; 'inner' is the fixed 1-sigma
   *  reference beneath it. Drives strip and alpha only, never the label. */
  tone: 'outer' | 'inner'
}

/**
 * The bands to draw for one posterior at the page's selected confidence, outer
 * first. Same distribution and selection logic the results table reads, so a
 * figure and the row above it cannot report different numbers.
 *
 * Selecting 68.3% collapses to a single band rather than drawing 1 sigma twice
 * at two strengths, which would claim a nesting that is not there.
 */
export function hpdBands(
  distribution: Array<[number, number]> | undefined | null,
  confidence: number,
  confidenceLabel: string
): HPDBand[] {
  const outer = hpdRangesFromCurve(distribution, confidence)
  if (outer.length === 0) return []
  const bands: HPDBand[] = [{ ranges: outer, label: confidenceLabel, tone: 'outer' }]
  if (confidence > INNER_CONFIDENCE + 1e-6) {
    const inner = hpdRangesFromCurve(distribution, INNER_CONFIDENCE)
    if (inner.length > 0) {
      bands.push({ ranges: inner, label: INNER_CONFIDENCE_LABEL, tone: 'inner' })
    }
  }
  return bands
}

/**
 * Bands from the backend's stored fixed-level ranges, for preview surfaces
 * with no confidence selector to follow. Labelled 68.3%/95.4%, which is what
 * the backend computes them at, so they compare directly with a Results page
 * that re-selects at the same coverages.
 */
export function storedHpdBands(
  hpd68Ranges: HPDRange[] | undefined | null,
  hpd95Ranges: HPDRange[] | undefined | null,
  hpd68?: [number, number] | null,
  hpd95?: [number, number] | null
): HPDBand[] {
  const asRanges = (
    ranges: HPDRange[] | undefined | null,
    envelope?: [number, number] | null
  ): HPDRange[] => {
    if (ranges && ranges.length > 0) return ranges
    if (envelope) return [{ min: envelope[0], max: envelope[1], probability_percent: 0 }]
    return []
  }
  const bands: HPDBand[] = []
  const outer = asRanges(hpd95Ranges, hpd95)
  if (outer.length > 0) bands.push({ ranges: outer, label: '95.4%', tone: 'outer' })
  const inner = asRanges(hpd68Ranges, hpd68)
  if (inner.length > 0) bands.push({ ranges: inner, label: '68.3%', tone: 'inner' })
  return bands
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
