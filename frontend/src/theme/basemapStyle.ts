/**
 * The tiled basemap's style: OpenStreetMap's own tiles, quietened.
 *
 * Plotly's built-in `open-street-map` draws the tiles as they come, and as
 * they come they are a street map -- coloured land use, a saturated blue
 * ocean, road casings, labels at every zoom -- drawn to be read on its own.
 * Under a figure it is not on its own: the marks are the figure, and every
 * hue on the tile competes with a hue that means something. The residual
 * map's early/late pair had to be moved off blue because of the ocean alone.
 *
 * So the same tiles, from the same server, with the same request and the
 * same disclosure, are drawn nearly grey and a little faded over the app's
 * own surface. Coastlines, islands, rivers and the shape of the land all
 * survive -- that is what a basemap is for -- but nothing on it is a colour
 * any more, so the marks are the only things that are. MapLibre applies the
 * paint on the GPU, so this costs nothing over the plain style.
 *
 * A style OBJECT rather than a name, which is the whole trick: plotly hands
 * an object straight to MapLibre, so any raster paint property is available
 * and no second style source or tile host is involved.
 */

/** The tile URL plotly's own `open-street-map` style uses. */
export const OSM_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'

/**
 * The glyph source MapLibre needs for any text on the map -- the isochron
 * labels. The same one plotly's own styles use; a symbol layer with no
 * glyphs source draws no text at all.
 */
const GLYPHS_URL = 'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf'

export interface QuietBasemapOptions {
  /** The app's surface colour, showing through the faded tiles. */
  surface: string
  /**
   * How much of the tiles' colour to drain, 0 (as served) to 1 (grey).
   * Nearly all of it: a trace of hue keeps water reading as water.
   */
  desaturate?: number
  /** How solid the tiles are over the surface, 0 to 1. */
  opacity?: number
}

/**
 * A MapLibre style for the quiet OSM basemap, in the shape plotly passes
 * through untouched.
 */
export function quietOsmStyle({
  surface,
  desaturate = 0.85,
  opacity = 0.7,
}: QuietBasemapOptions): Record<string, unknown> {
  return {
    version: 8,
    sources: {
      'osm-tiles': {
        type: 'raster',
        tiles: [OSM_TILE_URL],
        tileSize: 256,
        attribution:
          '© <a target="_blank" href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      },
    },
    glyphs: GLYPHS_URL,
    layers: [
      {
        id: 'surface',
        type: 'background',
        paint: { 'background-color': surface },
      },
      {
        id: 'osm-tiles',
        type: 'raster',
        source: 'osm-tiles',
        minzoom: 0,
        maxzoom: 22,
        paint: {
          'raster-saturation': -desaturate,
          'raster-opacity': opacity,
          // Tiles fade in as they arrive by default; over a figure that is
          // marks appearing on nothing and then a map sliding in under
          // them, so they are drawn as soon as they are here.
          'raster-fade-duration': 0,
        },
      },
    ],
  }
}
