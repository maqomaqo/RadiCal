/**
 * The tiled basemap: OpenFreeMap's Positron, trimmed.
 *
 * Positron is the light-grey cartography most published site-distribution
 * maps end up resembling: pale land, a darker grey sea, thin coastlines,
 * quiet labels, and nothing on it that is a colour -- so the marks are the
 * only things that are. OpenStreetMap's own raster tiles were tried first,
 * plain and then desaturated, and failed on the land: OSM paints woodland
 * and scrub as fills of their own, and a small forested island came out the
 * colour of the sea around it. A raster tile is a picture; nothing in it
 * can be turned off. Positron is vector, so what is drawn is decided here.
 *
 * OpenFreeMap (openfreemap.org) serves it from one host, with no key, no
 * rate limit and no account -- a public-good project over OpenStreetMap
 * data. CARTO, whose design Positron is, now requires an API key for its
 * own hosting of the same style, which would have meant a key in every
 * installation's configuration and a quota to watch.
 *
 * The style is fetched once and tailored before it reaches MapLibre. The
 * fetch is part of the reachability probe (see utils/tileServer.ts), so a
 * map is never asked to draw on a style that has not arrived.
 */

/** Where the basemap comes from. Every request the tiled map makes goes here. */
export const BASEMAP_HOST = 'tiles.openfreemap.org'

/** How the provider is named to the reader, in captions and the manual. */
export const BASEMAP_PROVIDER = 'OpenFreeMap'

const STYLE_URL = `https://${BASEMAP_HOST}/styles/positron`

/**
 * A small raster tile on the same host, for the reachability probe: an
 * Image load, which the probe can check the arrival of even when the page
 * was too busy to deliver its load event. The style itself is JSON and
 * has no such tell.
 */
export const BASEMAP_PROBE_URL = `https://${BASEMAP_HOST}/natural_earth/ne2sr/0/0/0.png`

/**
 * The font the map's own labels -- the isochron ages -- are set in. One
 * the host serves glyphs for. Plotly maps any family it does not know
 * straight through to MapLibre, which is what is wanted here.
 */
export const BASEMAP_TEXT_FONT = 'Noto Sans Regular'

/**
 * Layers dropped from Positron before it is drawn.
 *
 * Road names and shields: a figure about sites is not a figure about
 * roads, and at the zooms a placing dialog reaches they are the loudest
 * thing on the tile. Airports likewise. Woodland and parks: the two land
 * fills Positron does keep, and the very thing that made the raster tiles
 * unusable -- land must be one colour so an island is always an island.
 */
const DROPPED_LAYERS = new Set([
  'highway-name-path',
  'highway-name-minor',
  'highway-name-major',
  'highway-shield-non-us',
  'highway-shield-us-interstate',
  'road_shield_us',
  'airport',
  'landcover_wood',
  'park',
])

type StyleObject = Record<string, unknown> & { layers?: Array<Record<string, unknown>> }

let loaded: StyleObject | null = null
let loading: Promise<StyleObject> | null = null

/**
 * Trim a fetched Positron style to what this app draws. Exported for the
 * test; the app goes through loadBasemapStyle.
 */
export function tailorBasemapStyle(style: StyleObject): StyleObject {
  const layers = (style.layers || []).filter(
    (layer) => !DROPPED_LAYERS.has(String(layer.id)),
  )
  const tailored: StyleObject = { ...style, layers }
  // With the shield layers gone nothing draws an icon, so the sprite sheet
  // -- two more requests -- is not needed. Plotly's own text layer names an
  // icon it does not have either way; see preventIconFetch.
  delete tailored.sprite
  return tailored
}

/**
 * The tailored style, fetched once per page and shared by every map.
 * Rejects when the host cannot be reached, which the probe turns into the
 * offline fallback.
 */
export function loadBasemapStyle(fetchImpl: typeof fetch = fetch): Promise<StyleObject> {
  if (loaded) return Promise.resolve(loaded)
  if (loading) return loading
  loading = fetchImpl(STYLE_URL, { mode: 'cors' })
    .then((response) => {
      if (!response.ok) throw new Error(`basemap style: HTTP ${response.status}`)
      return response.json() as Promise<StyleObject>
    })
    .then((style) => {
      loaded = tailorBasemapStyle(style)
      return loaded
    })
    .finally(() => {
      loading = null
    })
  return loading
}

/** The style if it has been loaded, for a map deciding what to draw now. */
export function loadedBasemapStyle(): StyleObject | null {
  return loaded
}
