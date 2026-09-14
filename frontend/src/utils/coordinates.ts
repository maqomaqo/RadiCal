/**
 * Coordinates, and how precisely a project lets them leave.
 *
 * Site coordinates are restricted data under many heritage bodies',
 * landowners' and Indigenous data-governance frameworks. The app stores what
 * was entered -- degrading data at rest helps nobody and cannot be undone --
 * and a project's `coordinate_precision` decides what appears in an export or
 * a rendered figure. The rounding itself happens on the SERVER, in
 * api/results.py, so nothing here re-implements it: this file is the names and
 * the labels only.
 */

/** The four values the backend will store; see schemas/project.py. */
export type CoordinatePrecision = 'full' | '0.01' | '0.1' | '1'

/**
 * The selector's options, each distance rounded to what it means at the
 * equator. Named in kilometres because "0.1°" says nothing about how much of a
 * landscape it hides, and that is the whole question being asked.
 */
export const COORDINATE_PRECISION_OPTIONS: { value: CoordinatePrecision; label: string }[] = [
  { value: 'full', label: 'Full precision — as entered' },
  { value: '0.01', label: '0.01° — about 1 km' },
  { value: '0.1', label: '0.1° — about 11 km' },
  { value: '1', label: '1° — about 110 km' },
]

/**
 * A position as one string, "54.5, -5.6", or null when it is not placed.
 *
 * Both halves or neither: the API refuses half a coordinate, so a row
 * carrying one is a row something has gone wrong with, and showing a lone
 * number as if it were a position would hide that.
 */
export function formatPosition(
  latitude: number | null | undefined,
  longitude: number | null | undefined,
): string | null {
  if (latitude == null || longitude == null) return null
  return `${latitude}, ${longitude}`
}
