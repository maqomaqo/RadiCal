// Putting two plotted distributions on the same footing.
//
// A stacked row draws a date's modelled posterior and, optionally, its
// unmodelled calibration behind it. The two come from different places and
// need not integrate to the same thing: a prior windowed and then downsampled
// by index has its point spacing changed without its values being rescaled, so
// its integral depends on where on the curve the date falls.
//
// Normalising both to unit area here makes the comparison mean what the plot
// says it means -- equal area, so a shorter curve really is a wider one -- and
// makes it independent of whatever convention the backend used. Done at the
// point of drawing, because a plot that has to be told the units of its inputs
// will be wrong again the next time one changes.

/** A [calendar age, probability] series. */
export type Distribution = Array<[number, number]>

/**
 * The integral of a series over its own grid, by the trapezium rule -- the
 * grid is not guaranteed uniform, and cal BP runs descending in places, so the
 * spacing is taken as an absolute value.
 */
export function integrate(points: Distribution): number {
  if (points.length < 2) return 0
  let total = 0
  for (let i = 0; i < points.length - 1; i++) {
    const width = Math.abs(points[i + 1][0] - points[i][0])
    total += ((points[i][1] + points[i + 1][1]) / 2) * width
  }
  return total
}

/**
 * The same series rescaled to unit area, so it compares with another whatever
 * grid or normalisation it arrived on. One that cannot be normalised -- fewer
 * than two points, or no positive area -- comes back unchanged rather than
 * blanked, since something unscaled beats a row that loses its curve.
 */
export function toUnitArea(points: Distribution): Distribution {
  const area = integrate(points)
  if (!Number.isFinite(area) || area <= 0) return points
  return points.map(([age, p]) => [age, p / area])
}

/** The tallest value in a series, or 0 for an empty one. */
export function peakOf(points: Distribution): number {
  let peak = 0
  for (const [, p] of points) if (p > peak) peak = p
  return peak
}

/** A distribution reduced to what is worth drawing, with its peak. */
export interface TrimmedDistribution {
  ages: number[]
  probs: number[]
  peak: number
}

/**
 * Trims a distribution's outer tails, where the density is indistinguishable
 * from zero at drawing scale and would draw as a flat rule across the figure.
 * Only the OUTER tails: a multimodal posterior's flat gap between peaks is a
 * real feature of the calibration, so the slice stays contiguous.
 *
 * Here rather than in a plot component because the stacked view and the curve
 * view draw the same lobes and must agree where a distribution visibly starts
 * and stops.
 */
export function trimTails(distribution: Distribution): TrimmedDistribution {
  const peak = Math.max(...distribution.map(([, p]) => p))
  const significant = distribution
    .map(([, p], i) => (p >= peak * 0.002 ? i : -1))
    .filter((i) => i >= 0)
  const from = significant.length > 0 ? Math.max(0, significant[0] - 1) : 0
  const to =
    significant.length > 0
      ? Math.min(distribution.length - 1, significant[significant.length - 1] + 1)
      : distribution.length - 1
  const visible = distribution.slice(from, to + 1)
  return { ages: visible.map(([age]) => age), probs: visible.map(([, p]) => p), peak }
}
