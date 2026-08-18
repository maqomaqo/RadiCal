/**
 * Reading a value off a sampled series at an arbitrary x.
 *
 * Used by the curve browser's readout, and the reason it interpolates rather
 * than taking the nearest sample is that the readout has to agree with the
 * line the reader is looking at. The resolution profile is sampled at about
 * one point per pixel, so at a 55,000-year zoom the nearest sample can be
 * decades away — and across the Hallstatt plateau, where resolution swings by
 * 300 years in 150, the nearest sample can differ from the drawn line by a
 * fifth. A readout that disagrees with its own chart is worse than a coarse
 * one.
 */

/**
 * Linear interpolation of column `column` of `series` at `x`, where column 0
 * is the x value. Returns null for an empty series; clamps at both ends,
 * since asking beyond the data is asking about the edge.
 */
export function interpolateAt(
  series: number[][],
  x: number,
  column: number
): number | null {
  if (series.length === 0) return null
  if (series.length === 1) return series[0][column] ?? null

  // Named by which END OF THE X AXIS each row sits at, not by its position in
  // the array. Getting that the wrong way round reads a descending series --
  // which a reversed calendar axis produces -- backwards, returning the
  // youngest value for every age.
  const ascending = series[series.length - 1][0] >= series[0][0]
  const lowest = ascending ? series[0] : series[series.length - 1]
  const highest = ascending ? series[series.length - 1] : series[0]
  if (x <= lowest[0]) return lowest[column]
  if (x >= highest[0]) return highest[column]

  for (let i = 0; i < series.length - 1; i++) {
    const [x0] = series[i]
    const [x1] = series[i + 1]
    const between = ascending ? x >= x0 && x <= x1 : x <= x0 && x >= x1
    if (!between) continue
    const span = x1 - x0
    if (span === 0) return series[i][column]
    const fraction = (x - x0) / span
    return series[i][column] + fraction * (series[i + 1][column] - series[i][column])
  }
  return null
}
