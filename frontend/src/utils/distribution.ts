// Putting two plotted distributions on the same footing.
//
// A stacked row draws a date's modelled posterior and, optionally, its
// unmodelled calibration behind it. The two arrive from different places and
// -- this is the bug these exist for -- in different units:
//
//   posterior  services/exact_chain._plottable: the solver's density on its
//              own uniform grid, integrating to 1.
//   prior      services/calibration.get_display_distribution: the curve's
//              probabilities windowed and then DOWNSAMPLED BY INDEX, which
//              changes the spacing between points without rescaling their
//              values. Its integral therefore depends on the curve's grid
//              where that date happens to fall and on whether downsampling
//              kicked in at all.
//
// Measured on the SRM SM/C Dune analysis, integrating each series over its
// own grid: every posterior came to 1.000, while the priors came to 1.000 on
// some rows and 10.000 on others. Scaling the prior by the posterior's peak
// -- as the plot did -- therefore drew 16 of its 22 rows up to 1.69 row
// heights tall, painting into the row above.
//
// Normalising both to unit area here makes the comparison mean what the plot
// says it means (equal area, so a shorter curve really is a wider one) and
// makes it independent of whatever convention the backend used. Doing it at
// the point of drawing is deliberate: a plot that has to be told the units of
// its inputs is a plot that will be wrong again the next time one changes.

/** A [calendar age, probability] series. */
export type Distribution = Array<[number, number]>

/**
 * The integral of a series over its own grid, by the trapezium rule.
 *
 * Trapezium rather than sum x step because the grid is not guaranteed
 * uniform: a downsampled series can carry uneven spacing, and cal BP runs
 * descending in places, so the spacing is taken as an absolute value.
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
 * The same series rescaled to unit area, so it can be compared with another
 * regardless of the grid or normalisation it arrived on.
 *
 * A series that cannot be normalised -- fewer than two points, or no positive
 * area -- is returned unchanged rather than blanked: something visible and
 * unscaled beats a row that silently loses its curve.
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
