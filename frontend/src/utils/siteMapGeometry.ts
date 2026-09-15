/**
 * What the map draws, worked out before Plotly is involved.
 *
 * Its own module, and importing nothing, for the reason plotExport.ts and
 * curvePlotGeometry.ts are their own modules: plotly.js-dist-min's UMD wrapper
 * reads `self` at module scope, so a Node test importing anything that reaches
 * it dies with "self is not defined" before collecting a single assertion.
 * Everything here is pure and worth asserting, so it lives where a test can
 * reach it.
 */

/** One dated sample, as the map needs it. */
export interface MapPoint {
  id: string
  name: string
  latitude: number | null
  longitude: number | null
}

/**
 * One position, and every date at it.
 *
 * A SITE, in the only sense this app has one: there is no sites table, and
 * dates sharing a coordinate ARE a site (see the plan's §4.1). So the map
 * draws one pin per position rather than one per date, and a midden with
 * fourteen dates on it is one mark rather than fourteen stacked on the same
 * pixel — which would look like one mark anyway, but could not be hovered,
 * counted, or told apart from a single date.
 */
export interface MapSite {
  latitude: number
  longitude: number
  /** Every date at this position, in the order they were given. */
  ids: string[]
  names: string[]
}

/** The lon/lat window the map opens on. */
export interface MapFrame {
  lon: [number, number]
  lat: [number, number]
}

/**
 * Positions are grouped by their exact stored value.
 *
 * Rounding first — treating anything within 100 m as one site — was the
 * alternative, and it is wrong here: two dates a user placed on separate pins
 * would silently merge into one, and un-merging them would be impossible from
 * the map. Exact equality means what the map shows is what is stored.
 */
function key(latitude: number, longitude: number): string {
  return `${latitude},${longitude}`
}

/**
 * The placed dates, gathered into sites. Unplaced dates are skipped — they are
 * the "unplaced" list beside the map, not a mark on it.
 *
 * Half a coordinate is skipped too. The API refuses one, so a point carrying
 * one is a point something has gone wrong with, and guessing the other half
 * from nothing would put a date in the sea.
 */
export function groupIntoSites(points: MapPoint[]): MapSite[] {
  const sites = new Map<string, MapSite>()
  for (const point of points) {
    if (point.latitude == null || point.longitude == null) continue
    const id = key(point.latitude, point.longitude)
    const existing = sites.get(id)
    if (existing) {
      existing.ids.push(point.id)
      existing.names.push(point.name)
    } else {
      sites.set(id, {
        latitude: point.latitude,
        longitude: point.longitude,
        ids: [point.id],
        names: [point.name],
      })
    }
  }
  return [...sites.values()]
}

/** The dates with no position: the list beside the map, and its empty state. */
export function unplaced(points: MapPoint[]): MapPoint[] {
  return points.filter((p) => p.latitude == null || p.longitude == null)
}

/**
 * A site's hover label: the sample name, or a count once there is more than
 * one date at the position.
 *
 * Named rather than counted at n = 1 because that is the case where the name
 * is the useful thing; at n = 14 a list of fourteen names in a tooltip is not.
 */
export function siteLabel(site: MapSite): string {
  if (site.names.length === 1) return site.names[0]
  return `${site.names.length} dates`
}

/**
 * The part of Plotly's geo subplot that turns a pixel back into a position.
 *
 * `d3.geo.projection`'s shape, narrowed to the one method used.
 */
export interface GeoProjection {
  invert(point: [number, number]): [number, number] | null | undefined
}

/**
 * Dig the projection out of a Plotly graph div, or return null.
 *
 * This reads Plotly INTERNALS -- `_fullLayout.geo._subplot.projection` -- and
 * there is no supported alternative: `plotly_click` on a geo subplot fires
 * only when a MARK is hit, never on empty map, so the public API cannot answer
 * "where did they click?", which is the whole of placing a pin. (Verified
 * against 2.35.3, the bundled version.)
 *
 * So it is written to fail soft. Every step is checked, and a Plotly upgrade
 * that moves any of them makes this return null rather than throw -- the map
 * stops accepting clicks and the typed latitude/longitude boxes, which are
 * always there, go on working. A caller must treat null as "this map cannot
 * place a pin" and say so, not as an error.
 */
export function geoProjectionOf(graphDiv: unknown): GeoProjection | null {
  const layout = (graphDiv as { _fullLayout?: unknown } | null)?._fullLayout
  const geo = (layout as { geo?: unknown } | undefined)?.geo
  const subplot = (geo as { _subplot?: unknown } | undefined)?._subplot
  const projection = (subplot as { projection?: unknown } | undefined)?.projection
  if (!projection || typeof (projection as GeoProjection).invert !== 'function') return null
  return projection as GeoProjection
}

/**
 * MapLibre's map object, as the tiled basemap exposes it, narrowed to what is
 * used here. Reached the same guarded way as the geo projection above, and for
 * the same reason -- it hangs off Plotly internals.
 */
export interface MapLibreMap {
  unproject(point: [number, number]): { lat: number; lng: number } | null | undefined
  getContainer(): Element | null | undefined
}

/** The MapLibre map behind a tiled basemap, or null. */
export function mapLibreOf(graphDiv: unknown): MapLibreMap | null {
  const layout = (graphDiv as { _fullLayout?: unknown } | null)?._fullLayout
  const subplot = (layout as { map?: { _subplot?: unknown } } | undefined)?.map?._subplot
  const map = (subplot as { map?: unknown } | undefined)?.map
  if (!map) return null
  const candidate = map as MapLibreMap
  if (typeof candidate.unproject !== 'function' || typeof candidate.getContainer !== 'function') {
    return null
  }
  return candidate
}

/** Whether an inverted pair is a place on the earth. */
function validPlace(latitude: number, longitude: number): boolean {
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return false
  return latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180
}

/**
 * A position from a point in the TILED map's frame, which is its own container
 * rather than the SVG the offline map draws into -- confirmed by projecting a
 * known coordinate forward and finding it at that offset inside
 * `getContainer()`'s box.
 *
 * MapLibre spells it lng; everything else in this app spells it longitude, and
 * the translation happens here so nothing downstream has to remember which.
 */
export function latLonFromMapPoint(
  map: MapLibreMap,
  x: number,
  y: number,
): { latitude: number; longitude: number } | null {
  let inverted: { lat: number; lng: number } | null | undefined
  try {
    inverted = map.unproject([x, y])
  } catch {
    return null
  }
  if (!inverted) return null
  const { lat, lng } = inverted
  return validPlace(lat, lng) ? { latitude: lat, longitude: lng } : null
}

/**
 * A starting zoom for the tiled map from the window the offline one would
 * open on.
 *
 * An approximation, and knowingly: the exact figure depends on the viewport's
 * pixel width, which a pure function has no business knowing. 360 degrees
 * across one 512-pixel world tile is the standard slippy-map relation, and at
 * the sizes this map is rendered at it lands within a zoom level of right --
 * close enough for an opening view someone can then scroll.
 */
export function zoomForFrame(frame: MapFrame): number {
  const degrees = Math.max(frame.lon[1] - frame.lon[0], 0.01)
  return Math.min(12, Math.max(1, Math.log2(360 / degrees)))
}

/**
 * A position from a point in the map's own pixel frame, or null if that point
 * is not on the earth.
 *
 * The frame is the `.main-svg` element's box, margins included -- established
 * by projecting a known coordinate forward and finding the rendered mark at
 * exactly that offset from the SVG's origin, not from the plot area's.
 *
 * Null for a click off the globe, which is a real case rather than a defensive
 * flourish: an interrupted projection leaves the corners of a rectangular plot
 * outside the map, and inverting there gives NaN or a latitude past the pole.
 * Placing a date at NaN would be worse than ignoring the click.
 */
export function latLonFromPoint(
  projection: GeoProjection,
  x: number,
  y: number,
): { latitude: number; longitude: number } | null {
  let inverted: [number, number] | null | undefined
  try {
    inverted = projection.invert([x, y])
  } catch {
    // A projection can throw on a point it cannot inverse rather than
    // returning one. Same answer either way: not a place.
    return null
  }
  if (!inverted) return null
  const [longitude, latitude] = inverted
  return validPlace(latitude, longitude) ? { latitude, longitude } : null
}

/**
 * A placed coordinate, rounded to the precision a click can honestly claim.
 *
 * Six decimals is about 10 cm and is absurd for a pin dropped on a world map;
 * it would also write a position that LOOKS surveyed into a field that an
 * export then labels as the site's recorded location. Four is about 10 m,
 * still finer than the offline basemap can resolve, and it keeps the stored
 * value readable in the table beside dates that were typed in.
 */
export function roundPlacement(value: number): number {
  return Math.round(value * 10000) / 10000
}

/** How much of a margin to leave around the placed sites, as a fraction. */
const PADDING = 0.25

/**
 * The smallest window the map will open on, in degrees.
 *
 * A single site has no extent, and a zero-width range is not a map. Six
 * degrees is about the width of Britain, and it is chosen to match what the
 * basemap can honestly draw rather than what a reader might like: the vendored
 * geometry is Natural Earth 50m, which holds up at a window this size and
 * not much tighter -- at a county-sized one it renders a coastline as visible
 * straight-line segments and an island as a polygon. A map zoomed past its
 * own data invites a precision it does not have -- and placing a pin at site
 * precision is exactly the job the offline basemap is not for, which is why
 * tiles exist as a per-project opt-in.
 */
const MINIMUM_SPAN = 6

function span(values: number[], limit: number): [number, number] {
  const low = Math.min(...values)
  const high = Math.max(...values)
  const padded = Math.max((high - low) * PADDING, (MINIMUM_SPAN - (high - low)) / 2, 0)
  return [Math.max(-limit, low - padded), Math.min(limit, high + padded)]
}

/**
 * The window to frame the placed sites in, or null when nothing is placed --
 * in which case the caller shows the whole world, since there is nothing to
 * zoom to and an arbitrary starting view would be a guess.
 *
 * Ranges are clamped to the real world: a site at latitude 89 must not open a
 * map running to 91, which Plotly draws as an empty band above the pole.
 */
export function frameFor(sites: MapSite[]): MapFrame | null {
  if (sites.length === 0) return null
  return {
    lon: span(sites.map((s) => s.longitude), 180),
    lat: span(sites.map((s) => s.latitude), 90),
  }
}
