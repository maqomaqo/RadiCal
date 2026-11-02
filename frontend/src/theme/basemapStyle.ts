import { formatOklch, parseOklch } from './oklch'

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
  // -- two more requests -- is not needed. Plotly's own text layer names no
  // icon for a text-only trace, so it does not need one either.
  delete tailored.sprite
  return tailored
}

/**
 * The tailored style re-grounded for the dark theme.
 *
 * OpenFreeMap serves no dark style, and a pale rectangle in the middle of a
 * dark page is the loudest thing on it. So Positron's own colours are moved,
 * property by property, by what each one IS -- not inverted wholesale, which
 * would put the sea lighter than the land:
 *
 *   - the background is the card the figure sits on, so the land is the plot
 *     surface and marks are checked against the same ground as on any figure;
 *   - fills keep their place relative to the land: the sea stays the darker
 *     surface, a glacier or a building stays the lighter one, at half the
 *     distance, since a dark ground leaves half the room;
 *   - lines and text are marks, so they swap sides -- coastline, borders and
 *     names are lighter than the land where they were darker -- and a place
 *     name is held to 4.5:1 on the land;
 *   - every hue is kept and every chroma is cut to what the warm ground
 *     carries, so the one tinted surface on the map is still the sea and
 *     nothing on it is a colour except the marks.
 *
 * The trimming that `tailorBasemapStyle` did is untouched: this only recolours
 * what is left.
 */
/**
 * The dark map's land: a step above the card, not the card itself.
 *
 * The first version put the land ON the card (`#231d19`) and halved every
 * fill's distance from it, which left the sea 1.1:1 from the land -- two
 * near-blacks, and the map read as one flat dark slab. Positron's own sea is
 * ~1.5:1 from its land, so that distance is kept (1.48:1 here): the land
 * rises above the band surface, and fills keep their FULL distance rather than
 * half -- the dark ground can afford it once the land is not at the bottom of
 * the range, and the sea stays dark but not black.
 */
export const LAND_DARK = '#3a342f'
const FILL_SCALE = 1.0

export function darkenBasemapStyle(style: StyleObject): StyleObject {
  const cached = darkened.get(style)
  if (cached) return cached

  const land = parseOklch(LAND_DARK)!
  const light = { l: 0.96 } // Positron's land, which is its `background`
  const MAX_CHROMA = 0.016

  const regrind = (value: string, role: 'background' | 'fill' | 'line' | 'text' | 'halo') => {
    const c = parseOklch(value)
    if (!c) return value
    if (role === 'background') return LAND_DARK
    if (role === 'halo') return formatOklch({ ...land, a: c.a })
    const hue = c.c < 0.02 ? land.h : c.h
    // A source grey (R=G=B) becomes the ground's own warm tint, not a cool neutral.
    const chroma = c.c < 0.02 ? 0.012 : Math.min(c.c, MAX_CHROMA)
    const base = { c: chroma, h: hue, a: c.a }
    if (role === 'fill') return formatOklch({ ...base, l: Math.max(0.06, land.l + (c.l - light.l) * FILL_SCALE) })
    if (role === 'line') {
      const l = Math.min(0.6, Math.max(0.12, land.l + (light.l - c.l) * 0.5))
      return formatOklch({ ...base, l })
    }
    // text: lifted, so a black name becomes the lightest and a grey one holds
    // its rank. Chroma is allowed to stay, so the water labels are still blue.
    return formatOklch({
      l: 0.66 + (1 - c.l) * 0.26,
      c: c.c < 0.02 ? 0.01 : Math.min(c.c, 0.08),
      h: c.c < 0.02 ? land.h : c.h,
      a: c.a,
    })
  }

  // A value is a colour string or an expression (an array whose strings are
  // stops); anything that does not parse as a colour -- "linear", "zoom" -- is
  // an operator and is left alone.
  const walk = (value: unknown, role: Parameters<typeof regrind>[1]): unknown =>
    typeof value === 'string'
      ? regrind(value, role)
      : Array.isArray(value)
        ? value.map((v) => walk(v, role))
        : value

  const ROLES: Record<string, Parameters<typeof regrind>[1]> = {
    'background-color': 'background',
    'text-halo-color': 'halo',
    'text-color': 'text',
    'fill-color': 'fill',
    'fill-outline-color': 'line',
    'line-color': 'line',
  }

  const layers = (style.layers || []).map((layer) => {
    const paint = layer.paint as Record<string, unknown> | undefined
    if (!paint) return layer
    const next: Record<string, unknown> = { ...paint }
    for (const [prop, value] of Object.entries(paint)) {
      const role = ROLES[prop]
      if (role) next[prop] = walk(value, role)
    }
    return { ...layer, paint: next }
  })

  const result = { ...style, layers }
  darkened.set(style, result)
  return result
}

const darkened = new WeakMap<StyleObject, StyleObject>()

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
