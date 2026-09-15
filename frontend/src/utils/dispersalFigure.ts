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

/**
 * Two points for the fitted front: the origin, and the site furthest from it.
 *
 * A single great-circle segment rather than a set of isochrons, because model
 * 1 fits ONE gradient along one bearing. Drawing arcs would claim a
 * two-dimensional structure this model does not estimate -- isochrons belong
 * to the trend surface, which is a different model with a different output.
 *
 * "Furthest" is measured on the fit's own reported distances where it has
 * them, so the line ends at the site the regression actually reached.
 */
export function frontLine(
  fit: DispersalFit
): Array<{ latitude: number; longitude: number }> {
  const placed = fit.sites.filter(
    (s) => s.latitude !== null && s.longitude !== null && !s.refusal
  )
  if (placed.length === 0) return []

  const distanceOf = new Map(
    fit.residuals.map((r) => [r.element_id, r.distance_km])
  )
  let farthest = placed[0]
  let best = -Infinity
  for (const site of placed) {
    const reported = distanceOf.get(site.element_id)
    // No reported distance means the site was not in the fit; fall back to a
    // plain degree separation, which only has to ORDER them.
    const measure =
      reported ??
      Math.hypot(
        (site.latitude as number) - fit.origin.lat,
        (site.longitude as number) - fit.origin.lon
      )
    if (measure > best) {
      best = measure
      farthest = site
    }
  }

  return [
    { latitude: fit.origin.lat, longitude: fit.origin.lon },
    {
      latitude: farthest.latitude as number,
      longitude: farthest.longitude as number,
    },
  ]
}
