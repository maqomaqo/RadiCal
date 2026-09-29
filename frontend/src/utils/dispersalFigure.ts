/**
 * Turning a wave-of-advance fit into the things a figure needs.
 *
 * Separate from DispersalPanel for the reason siteMapGeometry is separate from
 * SiteMap: this is arithmetic with right and wrong answers, and arithmetic
 * that can be wrong should be testable without rendering anything.
 *
 * Everything here DERIVES FROM WHAT THE FIT REPORTED rather than recomputing
 * it. A panel that re-derived a site's arrival age from its coordinates would
 * be a second implementation of the model, free to disagree with the number
 * printed beside it -- and the disagreement would show up as a colour slightly
 * off rather than as anything anyone would notice.
 */
import type { DispersalFit } from '@/types'
import { niceStep } from '@/utils/linearTicks'

/**
 * A speed at a resolution the draws support.
 *
 * Three decimals below 1 km/yr, two up to 10, none above: 0.004 km/yr is a
 * real distinction between a slow front and a stationary one, and 12.34 km/yr
 * is spurious precision on a number whose interval is a kilometre wide.
 */
export function speedText(value: number): string {
  if (!Number.isFinite(value)) return 'N/A'
  const magnitude = Math.abs(value)
  const digits = magnitude >= 10 ? 0 : magnitude >= 1 ? 2 : 3
  return value.toFixed(digits)
}

/**
 * Each site's modelled arrival age, cal BP, keyed by element id.
 *
 * Reconstructed from the fit's own pieces -- t0, the speed, and the site's
 * distance and residual -- which is exactly the line the fit drew plus exactly
 * the offset it reported. So the ramp on the map and the residual table can
 * never tell different stories: they are the same two numbers.
 *
 * Empty when there is no fit to reconstruct from, which is a refusal.
 */
export function arrivalAges(fit: DispersalFit): Record<string, number> {
  const ages: Record<string, number> = {}
  if (!fit.t0 || !fit.speed || fit.speed.median === 0) return ages
  for (const residual of fit.residuals) {
    ages[residual.element_id] =
      fit.t0.median - residual.distance_km / fit.speed.median + residual.residual_years
  }
  return ages
}

const EARTH_RADIUS_KM = 6371.0088

/** Longitude brought into [-180, 180). */
function wrapLon(lon: number): number {
  return ((((lon + 180) % 360) + 360) % 360) - 180
}

/** The point `distanceKm` from (lat, lon) along `bearingDeg`, on a sphere. */
function destination(lat: number, lon: number, bearingDeg: number, distanceKm: number) {
  const phi1 = (lat * Math.PI) / 180
  const lambda1 = (lon * Math.PI) / 180
  const theta = (bearingDeg * Math.PI) / 180
  const delta = distanceKm / EARTH_RADIUS_KM
  const phi2 = Math.asin(
    Math.sin(phi1) * Math.cos(delta) + Math.cos(phi1) * Math.sin(delta) * Math.cos(theta)
  )
  const lambda2 =
    lambda1 +
    Math.atan2(
      Math.sin(theta) * Math.sin(delta) * Math.cos(phi1),
      Math.cos(delta) - Math.sin(phi1) * Math.sin(phi2)
    )
  return { latitude: (phi2 * 180) / Math.PI, longitude: (lambda2 * 180) / Math.PI }
}

/**
 * Lines of equal arrival age for a wave of advance: circles round the origin.
 *
 * The model is t = t0 - d / v with d the great-circle distance from the
 * origin, so arrival depends on distance and on nothing else -- every site at
 * one distance arrives at one age, whatever its bearing. Its isochrons are
 * therefore circles, and a circle is what is drawn. This replaced a single
 * line from the origin to the furthest site, which on a Polynesian map read as
 * a voyage to Rapa Nui: a route the model says nothing about.
 *
 * At round calendar ages between the origin's arrival and the furthest
 * site's, ages derived from the fit's own t0 and speed -- the same two numbers
 * printed beside the map. Each circle is kept only where it crosses the sites'
 * own region (their extent and the origin's, padded by a fifth), because a
 * full circle a thousand kilometres out would drag the map's frame across an
 * ocean nobody reached; the longest such arc is the one returned.
 *
 * Empty for a negative or missing speed: a front that arrives EARLIER further
 * out has no outward isochrons to draw, and the panel says why in words.
 */
export function waveIsochrons(
  fit: DispersalFit,
  target = 4
): Array<{ age: number; points: Array<{ latitude: number; longitude: number }> }> {
  const speed = fit.speed?.median
  const t0 = fit.t0?.median
  if (!speed || !(speed > 0) || t0 == null || fit.residuals.length === 0) return []
  const reach = Math.max(...fit.residuals.map((r) => r.distance_km))
  if (!(reach > 0)) return []

  // The sites' region, in longitudes unwrapped about the origin so a spread
  // across the antimeridian is one interval rather than two.
  const { lat: lat0, lon: lon0 } = fit.origin
  const unwrap = (lon: number) => lon0 + wrapLon(lon - lon0)
  const placed = fit.sites.filter((s) => s.latitude !== null && s.longitude !== null)
  const lats = [lat0, ...placed.map((s) => s.latitude as number)]
  const lons = [lon0, ...placed.map((s) => unwrap(s.longitude as number))]
  const padLat = Math.max(1, (Math.max(...lats) - Math.min(...lats)) / 5)
  const padLon = Math.max(1, (Math.max(...lons) - Math.min(...lons)) / 5)
  const box = {
    latMin: Math.min(...lats) - padLat,
    latMax: Math.max(...lats) + padLat,
    lonMin: Math.min(...lons) - padLon,
    lonMax: Math.max(...lons) + padLon,
  }

  const crossing = reach / speed
  const step = niceStep(crossing, target)
  const lines: Array<{ age: number; points: Array<{ latitude: number; longitude: number }> }> = []
  for (let age = Math.floor(t0 / step) * step; age >= t0 - crossing; age -= step) {
    const radius = speed * (t0 - age)
    if (!(radius > 0)) continue
    const ring = Array.from({ length: 145 }, (_, k) => {
      const p = destination(lat0, lon0, (k * 360) / 144, radius)
      return { latitude: p.latitude, longitude: unwrap(p.longitude) }
    })
    const inside = ring.map(
      (p) =>
        p.latitude >= box.latMin && p.latitude <= box.latMax &&
        p.longitude >= box.lonMin && p.longitude <= box.lonMax
    )
    // The longest contiguous run inside the box, treating the ring as closed.
    let best: number[] = []
    let run: number[] = []
    for (let k = 0; k < ring.length * 2; k++) {
      const index = k % ring.length
      if (inside[index] && run.length < ring.length) {
        run.push(index)
        if (run.length > best.length) best = [...run]
      } else {
        run = []
      }
    }
    if (best.length < 2) continue
    lines.push({
      age,
      points: best.map((index) => ({
        latitude: ring[index].latitude,
        longitude: wrapLon(ring[index].longitude),
      })),
    })
  }
  return lines
}

/**
 * A bearing as the eight-point name a reader thinks in.
 *
 * Beside the number rather than instead of it. "112°" is the answer and "ESE"
 * is not — eight points is 45° of slop — but a reader checking a fit against
 * what they know of the region thinks in compass points, and making them do
 * the conversion is making them do arithmetic to read a figure.
 */
export function compassPoint(degrees: number): string {
  const names = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
  if (!Number.isFinite(degrees)) return ''
  const wrapped = ((degrees % 360) + 360) % 360
  return names[Math.round(wrapped / 45) % 8]
}

/**
 * A position as a reader writes one: "2.0° S, 154.0° E", not "-2.0, 154.0".
 *
 * A bare signed pair makes the reader remember which number is which and
 * which sign is south; every published site table spells the hemisphere out.
 */
export function formatLatLon(lat: number, lon: number, digits = 1): string {
  const wrapped = ((((lon + 180) % 360) + 360) % 360) - 180
  const ns = lat < 0 ? 'S' : 'N'
  const ew = wrapped < 0 ? 'W' : 'E'
  return `${Math.abs(lat).toFixed(digits)}° ${ns}, ${Math.abs(wrapped).toFixed(digits)}° ${ew}`
}

/**
 * How widely the sites scatter about the fitted front, set against how much
 * time the front itself spans.
 *
 * The speed's interval comes from refitting the line on every draw, and says
 * nothing about how far the sites sit from that line: a front whose sites
 * scatter by a millennium still returns a tight speed. So the page prints this
 * beside the speed rather than leaving the residual table to contradict it
 * three hundred rows further down.
 *
 * `rms` is the root-mean-square residual in years; `span` is the time the
 * fitted front takes to cross from the origin to the furthest site, |d/v|;
 * `share` is rms / span. Null when there is nothing to measure.
 */
export function residualSpread(
  fit: DispersalFit
): { rms: number; span: number | null; share: number | null } | null {
  if (fit.residuals.length === 0) return null
  const rms = Math.sqrt(
    fit.residuals.reduce((sum, r) => sum + r.residual_years ** 2, 0) / fit.residuals.length
  )
  const furthest = Math.max(...fit.residuals.map((r) => r.distance_km))
  const speed = fit.speed?.median
  const span = speed && Number.isFinite(speed) && speed !== 0 ? Math.abs(furthest / speed) : null
  return { rms, span, share: span ? rms / span : null }
}

/**
 * A residual's reading: early, late, or within its own uncertainty of the
 * front. Positive residual_years is a site OLDER than the front predicts.
 *
 * The third state is the point. A chip that flips on the sign of the median
 * called "-3 ± 644" late and "+32 ± 1,080" early -- a verdict the site's own
 * uncertainty does not support. Two sd is the line: inside it the site is
 * indistinguishable from the front on its own evidence.
 */
export type ResidualReading = 'early' | 'late' | 'fits'

export function residualReading(residualYears: number, residualSd: number): ResidualReading {
  if (Math.abs(residualYears) <= 2 * Math.max(residualSd, 0)) return 'fits'
  return residualYears > 0 ? 'early' : 'late'
}
