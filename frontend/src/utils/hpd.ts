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

/** Each contiguous run of selected indices, as [firstIndex, lastIndex] pairs.
 *  One run is one disjoint range of the HPD set. */
function runsOf(selected: number[]): Array<[number, number]> {
  const sorted = [...selected].sort((a, b) => a - b)
  const runs: Array<[number, number]> = []
  let start = sorted[0]
  let prev = sorted[0]
  for (let k = 1; k < sorted.length; k++) {
    const i = sorted[k]
    if (i === prev + 1) {
      prev = i
      continue
    }
    runs.push([start, prev])
    start = prev = i
  }
  runs.push([start, prev])
  return runs
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

  const ranges: HPDRange[] = runsOf(selected).map(([s, e]) => {
    let segMass = 0
    for (let k = s; k <= e; k++) segMass += normWeights[k]
    return { min: ages[s], max: ages[e], probability_percent: segMass * 100 }
  })
  ranges.sort((a, b) => b.max - a.max)
  return ranges
}

/**
 * Calendar step a MODELLED range is reported at by default.
 *
 * Mirrors REPORTING_STEP_YEARS in
 * desktop/crates/radical-core/src/exact_chain/mod.rs, which is where the
 * reasoning lives; both sides must agree, because this page re-selects a region from a distribution the backend computed and the stored
 * hpd_68_ranges/hpd_95_ranges beside it were built at that step. An analysis
 * that asked for another resolution records it as solver.resolution, and
 * reportingOf hands that through instead.
 */
export const REPORTING_STEP_YEARS = 5

/**
 * How one posterior's ranges are reported. `false` for an unmodelled
 * calibration, whose distribution sits on the curve's own grid. For a
 * modelled one, the calendar step in years its analysis reported it at, or
 * `true` for the default step -- an analysis run before the step could be
 * set, or a surface with no analysis to ask.
 */
export type RangeReporting = boolean | number

/** The reporting for an analysis's posteriors. See RangeReporting. */
export function reportingOf(analysis: {
  analysis_type?: string
  solver?: { resolution?: number | null } | null
} | null | undefined): RangeReporting {
  if (analysis?.analysis_type !== 'bayesian') return false
  return analysis.solver?.resolution ?? true
}

function modelledStep(reporting: RangeReporting): number | null {
  if (reporting === false) return null
  if (reporting === true) return REPORTING_STEP_YEARS
  return reporting > 0 ? reporting : REPORTING_STEP_YEARS
}

/**
 * Ranges for a modelled posterior, rebinned to its reporting step first --
 * REPORTING_STEP_YEARS unless the analysis asked for another.
 *
 * A mirror of exact_chain._reported_ranges, and it exists for the same two
 * reasons. The solver picks its internal grid step from the date
 * range and the number of groups, so reporting at that step would make a
 * published range depend on an implementation detail -- the same dates in a
 * longer sequence come back with different bounds. And a modelled posterior is
 * sharply multi-peaked, so selecting at that step takes the peak tops and
 * skips the dips: on a one-phase model, Ld10013's 95.4% region came
 * back as five pieces including two single grid points holding 0.3% each,
 * where the rebinned region is three.
 *
 * NOT for an unmodelled calibration, whose distribution sits on the curve's own
 * grid. There is no solver step to hide there, and that spacing is the honest
 * resolution of the answer -- see posteriorRanges, which makes the choice.
 */
function reportedRangesFromPosterior(
  distribution: Array<[number, number]> | undefined | null,
  confidence: number,
  step: number
): HPDRange[] {
  if (!distribution || distribution.length === 0) return []
  const ages = distribution.map(([a]) => a)
  const densities = distribution.map(([, w]) => w)
  const widths = cellWidths(ages)
  const mass = densities.map((d, i) => d * widths[i])

  // The mass in a reporting bin is the cumulative mass between its two
  // edges, read off the CELL edges by interpolation: each grid point stands
  // for the cell half a solver step either side of it. Exact for a density
  // constant within a cell and, unlike counting which grid points fall in
  // each bin, independent of how the two grids align -- a 1.015-year solver
  // step put four points in one bin and five in the next, a +/-20% sawtooth
  // the HPD then selected on. Mirrors the backend's _reported_ranges; bins
  // holding no mass are dropped rather than carried as zeros, as there.
  const cellEdges = [ages[0] - widths[0] / 2]
  ages.forEach((age, i) => cellEdges.push(age + widths[i] / 2))
  const cdf = [0]
  mass.forEach((m) => cdf.push(cdf[cdf.length - 1] + m))
  const cdfAt = (x: number): number => {
    if (x <= cellEdges[0]) return 0
    const last = cellEdges.length - 1
    if (x >= cellEdges[last]) return cdf[last]
    let hi = 1
    while (cellEdges[hi] < x) hi++
    const span = cellEdges[hi] - cellEdges[hi - 1]
    const frac = span > 0 ? (x - cellEdges[hi - 1]) / span : 1
    return cdf[hi - 1] + frac * (cdf[hi] - cdf[hi - 1])
  }
  const lo = Math.floor(cellEdges[0] / step) * step
  const hi = Math.ceil(cellEdges[cellEdges.length - 1] / step) * step
  const kept: Array<[number, number]> = []
  for (let edge = lo, bin = 0; edge < hi; edge += step, bin++) {
    const m = Math.max(0, cdfAt(edge + step) - cdfAt(edge))
    if (m > 0) kept.push([bin, m])
  }
  if (kept.length === 0) return []

  const centres = kept.map(([bin]) => lo + (bin + 0.5) * step)
  const normWeights = normalizedWeights(kept.map(([, m]) => m))
  if (!normWeights) return []

  // No separate density to rank by: the bins are all one step wide, so mass
  // and density order identically.
  const selected = selectHighestDensity(normWeights, confidence)

  const ranges: HPDRange[] = runsOf(selected).map(([s, e]) => {
    let segMass = 0
    for (let k = s; k <= e; k++) segMass += normWeights[k]
    // Centres back out to edges: a bin is the width it stands for, not a point.
    return {
      min: centres[s] - step / 2,
      max: centres[e] + step / 2,
      probability_percent: segMass * 100,
    }
  })
  ranges.sort((a, b) => b.max - a.max)
  return ranges
}

/**
 * The HPD ranges for one result's posterior at the reader's confidence -- the
 * single place the app decides which resolution a range is reported at.
 *
 * `modelled` is whether the distribution came out of a solver. Every surface
 * that shows a posterior goes through here, so the table, the plots and the
 * Harris matrix cannot disagree with each other or with the backend.
 */
export function posteriorRanges(
  distribution: Array<[number, number]> | undefined | null,
  confidence: number,
  modelled: RangeReporting
): HPDRange[] {
  const step = modelledStep(modelled)
  return step !== null
    ? reportedRangesFromPosterior(distribution, confidence, step)
    : hpdRangesFromCurve(distribution, confidence)
}

/**
 * The outer span of those ranges. For a modelled posterior this is exactly the
 * backend's stored hpd_68/hpd_95 pair, which is min-of-mins to max-of-maxes
 * over the same rebinned ranges (see exact_chain._summarize).
 */
export function posteriorEnvelope(
  distribution: Array<[number, number]> | undefined | null,
  confidence: number,
  modelled: RangeReporting
): [number, number] | null {
  const step = modelledStep(modelled)
  if (step === null) return hpdEnvelopeFromHistogram(distribution, confidence)
  const ranges = reportedRangesFromPosterior(distribution, confidence, step)
  if (ranges.length === 0) return null
  return [
    Math.min(...ranges.map((r) => r.min)),
    Math.max(...ranges.map((r) => r.max)),
  ]
}

/**
 * The three coverages this app reports HPD regions at, and their labels.
 * "1 sigma" is the two-sided normal coverage of one standard deviation,
 * erf(1/sqrt(2)) = 0.6826894921, which ROUNDS to 68.3%. Likewise 95.4%, 99.7%.
 *
 * `confidence` is the threshold the HPD set is accumulated to, so the digits
 * change the reported range. Mirrors SIGMA_1/2/3 in
 * desktop/crates/radical-core/src/calibration.rs; JavaScript has no erf, so
 * they are written out here and pinned by a test. Both sides must agree,
 * since the page re-selects a region from a backend-computed distribution.
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
  confidenceLabel: string,
  /** Whether the distribution came out of a solver, and at what step, which
   *  decides the resolution its ranges are reported at -- see posteriorRanges. */
  modelled: RangeReporting = false
): HPDBand[] {
  const outer = posteriorRanges(distribution, confidence, modelled)
  if (outer.length === 0) return []
  const bands: HPDBand[] = [{ ranges: outer, label: confidenceLabel, tone: 'outer' }]
  if (confidence > INNER_CONFIDENCE + 1e-6) {
    const inner = posteriorRanges(distribution, INNER_CONFIDENCE, modelled)
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
