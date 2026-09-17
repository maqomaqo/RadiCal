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
  hasImage?(id: string): boolean
  addImage?(id: string, image: { width: number; height: number; data: Uint8Array }): void
}

/**
 * The icon name plotly's scattermap puts on every text label -- `{symbol}-15`
 * with an empty symbol -- and, finding no such image, fetches from unpkg.com.
 * See preventIconFetch.
 */
export const PLOTLY_TEXT_ICON = '-15'

/**
 * Register a blank image under the name plotly's text layer asks for, so
 * the map never reports it missing and plotly never goes to unpkg for it.
 *
 * plotly 2.35's scattermap draws text on a MapLibre symbol layer whose
 * icon-image is `{symbol}-15`; a text-only trace has no symbol, so the
 * layer asks for an image called `-15`, and plotly's styleimagemissing
 * handler answers by fetching `https://unpkg.com/maki@2.1.0/icons/-15.svg`
 * -- a request to a third party that fails on CORS and would draw nothing
 * if it succeeded. The handler is inside plotly and cannot be unhooked, but
 * it only fires for an image the map does not have. A 1x1 transparent
 * pixel under that name is enough; it is never drawn at any visible size.
 *
 * Idempotent, and safe against an upgrade that removes the methods: it
 * does nothing rather than throw.
 */
export function preventIconFetch(map: MapLibreMap): boolean {
  if (typeof map.hasImage !== 'function' || typeof map.addImage !== 'function') return false
  try {
    if (map.hasImage(PLOTLY_TEXT_ICON)) return true
    map.addImage(PLOTLY_TEXT_ICON, { width: 1, height: 1, data: new Uint8Array(4) })
    return true
  } catch {
    return false
  }
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
 * A longitude moved by whole turns to within half a turn of `centre`.
 *
 * For LINES on the tiled map. The offline map rotates its projection to the
 * frame's centre, so a segment from 170 to -170 is drawn the short way
 * across the dateline. MapLibre keeps the world's seam at 180 and draws that
 * same segment the long way round -- 340 degrees across every ocean but the
 * one it belongs to, which is what a Pacific spanning tree looked like on
 * tiles. MapLibre accepts a longitude past 180 and draws it in the adjacent
 * copy of the world, so a Pacific frame's -170 becomes 190 and the segment
 * is 20 degrees again. Marks are left alone: a point has no long way round,
 * and its hover should show the longitude the reader typed.
 */
export function unwrapLongitude(longitude: number, centre: number): number {
  return longitude + 360 * Math.round((centre - longitude) / 360)
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
 * The longitude window, worked out on the circle rather than on the line.
 *
 * Longitude wraps, and a min/max over the raw values does not know it: sites
 * from the Bismarcks (150 E) to Samoa (172 W) span 38 degrees of the Pacific
 * and 322 degrees of everything else, and min/max picks the everything else
 * -- the whole world, with the sites at its two edges. So the window is the
 * arc that leaves out the LARGEST gap between neighbouring sites, whichever
 * side of the antimeridian that gap falls on.
 *
 * A window that crosses the antimeridian is expressed as one that runs past
 * 180, since Plotly rotates the projection to the range's centre and draws
 * straight across the dateline (see fitFrameToAspect). It is normalised so
 * the centre lies within a turn of zero, and can never exceed a full turn.
 */
function lonSpan(values: number[]): [number, number] {
  const sorted = [...new Set(values)].sort((a, b) => a - b)
  // Start with the gap that crosses the antimeridian: from the easternmost
  // site round to the westernmost. If that is the largest, the window is the
  // ordinary min-to-max and nothing wraps.
  let low = sorted[0]
  let high = sorted[sorted.length - 1]
  let largest = low + 360 - high
  for (let i = 1; i < sorted.length; i++) {
    const gap = sorted[i] - sorted[i - 1]
    if (gap > largest) {
      largest = gap
      // The window leaves this gap out: it runs east from the site after the
      // gap, across the antimeridian, to the site before it.
      low = sorted[i]
      high = sorted[i - 1] + 360
    }
  }
  const padded = Math.min(
    180,
    Math.max((high - low) * PADDING, (MINIMUM_SPAN - (high - low)) / 2, 0),
  )
  low -= padded
  high += padded
  const turn = Math.round((low + high) / 2 / 360) * 360
  return [low - turn, high - turn]
}

/**
 * The window to frame the placed sites in, or null when nothing is placed --
 * in which case the caller shows the whole world, since there is nothing to
 * zoom to and an arbitrary starting view would be a guess.
 *
 * Latitude is clamped to the real world: a site at latitude 89 must not open
 * a map running to 91, which Plotly draws as an empty band above the pole.
 * Longitude is not, on purpose -- see lonSpan.
 */
export function frameFor(sites: MapSite[]): MapFrame | null {
  if (sites.length === 0) return null
  return {
    lon: lonSpan(sites.map((s) => s.longitude)),
    lat: span(sites.map((s) => s.latitude), 90),
  }
}

/**
 * The Natural Earth projection's scale, since that is what the offline map is
 * drawn in: how wide a radian of longitude draws at a latitude, and how far up
 * the page a latitude sits. The polynomial is the projection's definition
 * (Šavrič et al. 2011), as d3-geo carries it and as Plotly draws it.
 */
function naturalEarthWidth(phi: number): number {
  const p2 = phi * phi
  const p4 = p2 * p2
  return 0.8707 - 0.131979 * p2 + p4 * (-0.013791 + p4 * (0.003971 * p2 - 0.001529 * p4))
}

function naturalEarthHeight(phi: number): number {
  const p2 = phi * phi
  const p4 = p2 * p2
  return phi * (1.007226 + p2 * (0.015085 + p4 * (-0.044475 + 0.028874 * p2 - 0.005916 * p4)))
}

/** d/dφ of naturalEarthHeight: how tall a radian of latitude draws at φ. */
function naturalEarthSlope(phi: number): number {
  const p2 = phi * phi
  const p4 = p2 * p2
  const p6 = p4 * p2
  const p8 = p4 * p4
  return (
    1.007226 +
    3 * 0.015085 * p2 -
    7 * 0.044475 * p6 +
    9 * 0.028874 * p8 -
    11 * 0.005916 * p8 * p2
  )
}

const RADIANS = Math.PI / 180

/**
 * The frame widened, or heightened, to fill a box of the given aspect
 * (width over height).
 *
 * Plotly fits the two ranges into whatever box it is given and letterboxes
 * the rest, so a north-south run of sites in a landscape map drew as a strip
 * of sea down the middle of a white panel. The sites are still framed; the
 * blank is filled with more map on either side, which is what a reader would
 * have panned to see anyway.
 *
 * Measured in the Natural Earth projection the offline map draws in, where a
 * degree of longitude is narrower than one of latitude everywhere -- by
 * 0.87 at the equator and more towards the poles -- rather than by the
 * cosine a cylindrical map would use, which left a strip of letterbox on
 * either side. The tiled map takes only this frame's centre and a zoom from
 * its width, for which the same figure is close enough.
 *
 * Longitude is allowed past 180: Plotly rotates the projection to the
 * range's centre and draws across the antimeridian, which is exactly what a
 * Pacific frame needs -- clamping at 180 left the box letterboxed on the
 * dateline side (confirmed in the browser with a range of [-205, -95]).
 * Latitude has no such wrap, so a window that would run past a pole is
 * shifted back towards the equator instead, and the sites stay in view
 * though no longer centred.
 */
export function fitFrameToAspect(frame: MapFrame, aspect: number): MapFrame {
  if (!Number.isFinite(aspect) || aspect <= 0) return frame
  const midLat = (frame.lat[0] + frame.lat[1]) / 2
  const midLon = (frame.lon[0] + frame.lon[1]) / 2
  const width = (frame.lon[1] - frame.lon[0]) * RADIANS * naturalEarthWidth(midLat * RADIANS)
  const height =
    naturalEarthHeight(frame.lat[1] * RADIANS) - naturalEarthHeight(frame.lat[0] * RADIANS)
  if (width < aspect * height) {
    const half = Math.min(
      180,
      ((aspect * height) / naturalEarthWidth(midLat * RADIANS) / RADIANS) / 2,
    )
    return { lon: [midLon - half, midLon + half], lat: frame.lat }
  }
  const half = Math.min(90, (width / aspect / naturalEarthSlope(midLat * RADIANS) / RADIANS) / 2)
  const shift = Math.max(0, midLat + half - 90) - Math.max(0, -90 - (midLat - half))
  return { lon: frame.lon, lat: [midLat - half - shift, midLat + half - shift] }
}
