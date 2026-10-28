import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import {
  Box,
  Button,
  FormControlLabel,
  IconButton,
  Paper,
  Switch,
  Tooltip,
  Typography,
  useMediaQuery,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import RemoveIcon from '@mui/icons-material/Remove'
import ZoomOutMapIcon from '@mui/icons-material/ZoomOutMap'
import DownloadIcon from '@mui/icons-material/Download'
import { alpha } from '@mui/material/styles'
import Plotly from 'plotly.js-dist-min'
import type { ColorScale, Config, Data, Layout } from 'plotly.js-dist-min'

import { useBasemapPreference } from '@/hooks/useBasemapPreference'
import { useTileServerReachable } from '@/hooks/useTileServer'
import { CONTEXT_PALETTE } from '@/theme/diagramColors'
import { BASEMAP_PROVIDER, BASEMAP_TEXT_FONT, loadedBasemapStyle } from '@/theme/basemapStyle'
import { usePlotColors, usePlotFont } from '@/theme/plotColors'
import { downloadPlotPng } from '@/utils/downloadPlot'
import {
  fitFrameToAspect,
  densifyGreatCircle,
  frameFor,
  geoProjectionOf,
  groupIntoSites,
  latLonFromMapPoint,
  latLonFromPoint,
  mapLibreOf,
  roundPlacement,
  siteLabel,
  unplaced,
  unwrapLongitude,
  zoomForFrame,
} from '@/utils/siteMapGeometry'
import type { MapPoint, MapSite } from '@/utils/siteMapGeometry'

/**
 * The map's own binding to Plotly, in place of react-plotly.js.
 *
 * react-plotly.js queues every Plotly.react on one promise chain per
 * component instance, and purges the div when the instance unmounts. Under
 * React's StrictMode -- development only, but that is where the app is
 * tested -- a mount is immediately followed by a simulated unmount and
 * remount of the SAME instance. The first Plotly.react has by then created
 * its MapLibre map and is waiting for that map to load; the purge removes
 * the map, so it never does, and the remount's Plotly.react queues behind
 * a promise that will never settle. The result was a tiled map that drew
 * nothing, about three times in four, and only on a page whose maps mount
 * from a fetch rather than a click. The offline map never showed it
 * because a geo subplot draws synchronously.
 *
 * So: no chain. Each render calls Plotly.react on the div and forgets the
 * promise, other than to report the graph div once it is drawn; the purge
 * on unmount is the only other thing that touches the div. A Plotly.react
 * abandoned by a purge is left to hang, harmlessly.
 */
function PlotlyFigure({
  data,
  layout,
  config,
  onDrawn,
  style,
}: {
  data: Data[]
  layout: Partial<Layout>
  config: Partial<Config>
  /** Called with the graph div each time Plotly has finished drawing it. */
  onDrawn: (graphDiv: HTMLElement) => void
  style?: CSSProperties
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    let current = true
    Plotly
      .react(el, data, layout, config)
      .then(() => {
        if (current) onDrawn(el)
      })
      // A rejection here is a plot that could not be drawn; the caption
      // under the map already covers the one cause a reader can act on.
      .catch(() => {})
    return () => {
      current = false
    }
  }, [data, layout, config, onDrawn])

  useEffect(() => {
    const el = ref.current
    return () => {
      if (el) Plotly.purge(el)
    }
  }, [])

  return <div ref={ref} style={style} />
}

/**
 * Where the basemap geometry is served from -- this app, not a CDN.
 *
 * Plotly builds the request as `${topojsonURL}${scope}_${resolution}m.json`,
 * so this directory must contain `world_${GEO_RESOLUTION}m.json`; see
 * public/geo/README.md for why it is vendored rather than fetched. Left unset, Plotly goes to
 * cdn.plot.ly, and on a network that cannot reach it the plot does not degrade
 * -- it rejects, and there is no map at all.
 */
const TOPOJSON_URL = '/geo/'

/**
 * Natural Earth 1:50m rather than Plotly's default 1:110m. The map opens on
 * a six-degree window at its tightest, and at that scale the 110m geometry
 * drew a coastline as visible straight segments and an island as a polygon;
 * 50m holds up there. It is six times the download (1.7 MB against 285 kB),
 * fetched once per page load and cached by the browser thereafter.
 */
const GEO_RESOLUTION = 50

/**
 * How far the pointer may travel between going down and the click for that
 * click to count as placing a pin rather than as the end of a pan.
 *
 * Dragging is always on -- see the layout's `dragmode` -- so in edit mode a
 * click and a pan start the same way, and the only thing that tells them apart
 * is whether the pointer moved. A few pixels of slop, since nobody's hand is
 * perfectly still on a mouse button.
 */
const CLICK_SLOP_PX = 4

/**
 * The tallest the map may be, as a fraction of the viewport, so a landscape
 * figure on a wide window still leaves room for its caption and, in the
 * placing dialog, the typed boxes under it.
 */
const MAX_HEIGHT = '65vh'

/**
 * The basemap could not say where the click landed -- distinct from "that
 * click was not on the earth", which is null and perfectly ordinary.
 */
const UNAVAILABLE = Symbol('placement unavailable')

type Placement = { latitude: number; longitude: number } | null | typeof UNAVAILABLE

/**
 * Where a click landed on the offline basemap.
 *
 * The projection's frame is the `.main-svg` box, margins included --
 * established by projecting a known coordinate forward and finding the
 * rendered mark at exactly that offset from the SVG's origin, not the plot
 * area's.
 */
function placeFromOfflineMap(
  graphDiv: unknown,
  container: HTMLElement,
  event: MouseEvent,
): Placement {
  const projection = geoProjectionOf(graphDiv)
  const svg = container.querySelector<SVGElement>('.main-svg')
  if (!projection || !svg) return UNAVAILABLE
  const box = svg.getBoundingClientRect()
  return latLonFromPoint(projection, event.clientX - box.left, event.clientY - box.top)
}

/**
 * Where a click landed on the tiled basemap.
 *
 * A different frame from the offline map's: MapLibre measures from its own
 * container, not from Plotly's SVG. Confirmed the same way, against a
 * tile-free style so the check did not need a tile server.
 */
function placeFromTiledMap(graphDiv: unknown, event: MouseEvent): Placement {
  const map = mapLibreOf(graphDiv)
  const element = map?.getContainer()
  if (!map || !element) return UNAVAILABLE
  const box = element.getBoundingClientRect()
  return latLonFromMapPoint(map, event.clientX - box.left, event.clientY - box.top)
}

/**
 * The ramp a quantity is drawn on: the Harris matrix's context hue, extended
 * at the pale end.
 *
 * CONTEXT_PALETTE alone was tried first and does not work here, for a reason
 * that only showed up in a browser. Its six steps span #35826e to #04372a --
 * a lightness range deliberately kept narrow so every step clears 4.5:1 on
 * white while carrying a 10px label. On labelled boxes that is right. On 12px
 * marks with no label it is not: the whole range renders as eight dots of
 * approximately one green, and a reader cannot see the wave that is the point
 * of the figure.
 *
 * So the two pale anchors are the SAME hue (164 degrees) continued past where
 * a labelled box could go, since nothing is written on a mark and the contrast
 * floor that shaped the original ramp does not apply. It stays one hue at
 * graded strength, which is the rule; it is not added to CONTEXT_PALETTE,
 * because the matrix's steps are constrained by something this is not.
 *
 * Not continued as far as it first was, though. The first pair were #e3f1ec
 * and #b4d9cd -- 1.2:1 and 1.5:1 against white -- and on a basemap whose
 * land is white or a tile's pale grey, the youngest sites simply were not
 * there. These sit at 1.8:1 and 2.4:1: still plainly the pale end of the
 * same green, but a mark at the pale end is a mark. The rest of the pop
 * comes from the outline -- see VALUE_MARK_OUTLINE.
 */
const VALUE_RAMP = ['#8fcdb7', '#6cb59d', ...CONTEXT_PALETTE]

/** VALUE_RAMP as a Plotly colour scale: its stops evenly spaced over 0-1. */
const VALUE_SCALE: ColorScale = VALUE_RAMP.map(
  (hex, i): [number, string] => [i / (VALUE_RAMP.length - 1), hex],
)

/**
 * How the marks that carry a value are outlined.
 *
 * The plain pins take a white halo, which separates two solid dark marks
 * that overlap. A pale mark on a pale basemap gets nothing from a white
 * halo, and on a tile's pale land it vanished into it, halo and all;
 * a thin ink edge is what makes a pale fill read as a filled shape rather
 * than as a faint stain. Ink, not the mark's own hue darkened, so every mark
 * on the figure has the same edge whatever value it carries.
 */
const VALUE_MARK_OUTLINE = { width: 1, color: 'axis' as const }

/** How wide a value mark is drawn, before its outline. */
const VALUE_MARK_SIZE = 12

/** The key that turns a wheel over a map into a zoom, named as the keyboard names it. */
const ZOOM_MODIFIER =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent) ? '⌘' : 'Ctrl'

interface SiteMapProps {
  /** Every date the map is about, placed or not. */
  points: MapPoint[]
  /** Ids drawn as selected. Empty is a perfectly good state: nothing is. */
  selectedIds?: string[]
  /**
   * Whether clicking the map places a pin. Read-only otherwise, which is the
   * default: a map is a figure first and an input second.
   */
  editable?: boolean
  /**
   * Called with a position when the reader clicks the map in edit mode.
   * Rounded by roundPlacement first -- see there for why a click does not get
   * to claim ten centimetres.
   */
  onPlace?: (latitude: number, longitude: number) => void
  /**
   * Whether to draw a tiled basemap instead of the offline one.
   *
   * OFF unless a project has turned it on, and that is the whole design: a
   * raster tile request tells the tile server which square of the earth is
   * being looked at, which for a restricted site is the disclosure everything
   * else here exists to prevent. Turning it on is a per-project decision made
   * once, behind a notice that says what the request gives away.
   *
   * A wish, not a guarantee: when the tile server cannot be reached -- no
   * internet, or a network that blocks it -- the map falls back to the
   * offline basemap and says so in its caption, rather than drawing the
   * marks on the blank grey square MapLibre leaves when its tiles fail.
   *
   * Null means the project's answer is still on its way (see
   * useProjectMapTiles), and the map waits for it rather than draw one
   * basemap and then swap it for the other.
   */
  tiles?: boolean | null
  /**
   * The SHORTEST the map is allowed to be, in pixels.
   *
   * Not its height. The map fills the width it is given and takes a landscape
   * 16:9 from that, capped at MAX_HEIGHT of the viewport so it never pushes
   * its own caption off screen; this is the floor for the narrow case, where
   * 16:9 of a phone's width is a strip too short to read.
   */
  height?: number
  /** Used to name the downloaded PNG. */
  exportName?: string
  /**
   * What this map shows, in a few words ("Wave of advance map"). Its
   * accessible name and the name its controls announce: a page with three
   * maps otherwise had three regions called "Map", three "Download PNG"
   * buttons and three switches, with nothing to say which was which.
   */
  label?: string
  /**
   * A quantity per point id, drawn as graded strength of ONE hue rather than
   * as a categorical palette -- the same rule the Harris matrix follows, and
   * for the same reason: an arrival age is a quantity, and a set of unrelated
   * hues would say these sites are different KINDS of thing.
   *
   * Supersedes the selected/unselected split while it is set: two
   * simultaneous colour meanings on one figure is one too many.
   */
  values?: Record<string, number>
  /** What the colour bar is measuring. Required to mean anything by `values`. */
  valueLabel?: string
  /**
   * How `values` are coloured. `graded` (the default) is one hue at graded
   * strength, for a quantity that runs from less to more. `signed` is for a
   * quantity with a meaningful zero and two directions -- a residual, early
   * or late -- drawn as the two hues this app uses for those directions
   * (see PlotColors.residualEarly and residualLate) meeting at a pale
   * neutral, on a range symmetric about zero so equal departures either way
   * read equally strong. Still not a categorical palette: it is one axis
   * with a sign, and the sign is what the second hue carries.
   */
  valueScale?: 'graded' | 'signed'
  /**
   * A single distinguished position: where a front is hypothesised to have
   * started. Marked differently from a site because it is not one -- nothing
   * was excavated there, and it is an assumption rather than an observation.
   */
  origin?: { latitude: number; longitude: number } | null
  /**
   * Lines of equal arrival age across a fitted trend surface.
   *
   * Drawn under the marks and labelled by age, in one hue at one strength:
   * they are all the same KIND of thing and differ only in which age they
   * stand for, which the labels carry. Grading them would say the lines
   * themselves were a quantity.
   */
  isochrons?: Array<{ age: number; points: Array<{ latitude: number; longitude: number }> }>
  /**
   * How an isochron's age is written on the map -- the page's date format,
   * with its unit. A bare "3050" said neither which convention nor which unit,
   * and stayed cal BP when the reader had asked for BC/AD.
   */
  formatIsochron?: (age: number) => string
  /**
   * A likelihood region: candidate positions with a support in (0, 1].
   *
   * Drawn as translucent square cells rather than as a contour, because that
   * is what it IS -- a coarse grid of candidates, each evaluated -- and a
   * smooth contour drawn through them would imply a resolution the grid does
   * not have. Under everything else, since it is the backdrop a reader checks
   * the marks against.
   */
  region?: Array<{ lat: number; lon: number; support: number }>
  /** Point ids drawn ringed, over the value marks -- the reader's pointer elsewhere. */
  highlightIds?: string[]
  /** What one region cell spans, in degrees, so the cells read as the grid. */
  regionStepDeg?: number
  /**
   * Individual segments, each of which is a real link between two places --
   * the edges of a spanning tree.
   *
   * Segments, not one polyline. A WALK of a tree visits
   * every node once but jumps between branches to do it, so drawing the visit
   * order as a polyline puts a line between two sites the tree never joined
   * and the figure claims an adjacency that does not exist. Segments cannot
   * make that mistake.
   */
  links?: Array<{
    from: { latitude: number; longitude: number }
    to: { latitude: number; longitude: number }
  }>
}

/**
 * The app's one map.
 *
 * Drawn on `scattergeo` -- coastlines and country outlines from a topojson
 * this app serves itself -- rather than on a tiled basemap, and that is a
 * privacy decision rather than an aesthetic one. A raster tile request tells
 * whoever serves the tiles which 256x256 square of the earth someone is
 * looking at, which for a restricted site is the disclosure the whole
 * coordinate-precision apparatus exists to prevent. So the default map makes
 * no network request at all, and tiles are opt-in per project.
 *
 * Leaflet was the obvious alternative and is not used: it adds a dependency,
 * it cannot render without tiles -- so it could not be the private default --
 * and a figure drawn in it would be the one figure in the app outside the
 * Plotly export path and outside the palette.
 *
 * One pin per POSITION, not per date. There is no sites table in this app;
 * dates sharing a coordinate are a site, and fourteen dates from one midden
 * are one mark that says so rather than fourteen marks on the same pixel.
 */
export default function SiteMap({
  points,
  selectedIds = [],
  editable = false,
  onPlace,
  tiles: tilesWanted = false,
  height = 320,
  exportName = 'site-map',
  label = 'Site map',
  values,
  valueLabel,
  valueScale = 'graded',
  origin,
  isochrons,
  formatIsochron,
  region,
  regionStepDeg = 1,
  links,
  highlightIds,
}: SiteMapProps) {
  const plot = usePlotColors()
  const plotFont = usePlotFont()
  const plotBox = useRef<HTMLDivElement>(null)
  const graphDiv = useRef<unknown>(null)
  // Tiles are drawn only once the tile server has answered a probe. Until
  // it has, nothing is drawn: drawing the offline map and then replacing it
  // a moment later is a flash of coastline for every map on a connected
  // installation, and the wait is one small image. Not probed at all when
  // the project has not asked for tiles.
  // The project allows tiles AND the reader has not switched them off --
  // see useBasemapPreference for why those are two different things.
  const [preferTiles, setPreferTiles] = useBasemapPreference()
  const wantTiles = tilesWanted === true && preferTiles
  const reachable = useTileServerReachable(wantTiles)
  // The style arrives with a positive verdict -- the probe fetches it -- so
  // this is a guard, not a second wait.
  const basemapStyle = reachable === true ? loadedBasemapStyle() : null
  const tiles = wantTiles && reachable === true && basemapStyle !== null
  const deciding = tilesWanted === null || (wantTiles && reachable === null)
  const fellBack = wantTiles && reachable === false
  // Null until the first click tells us. Not an error state on its own: it
  // only matters in edit mode, and the map is read-only by default.
  const [cannotPlace, setCannotPlace] = useState(false)
  // Bumped by "Reset view", which redraws the figure at the frame it opened on.
  const [resetCount, setResetCount] = useState(0)
  // A plain wheel over the map scrolls the PAGE; see the wheel effect below.
  const [wheelHint, setWheelHint] = useState(false)
  // On a touch screen a one-finger drag is how the page scrolls, and three
  // stacked maps that each swallowed it left no way past them. There the
  // read-only maps do not pan; the zoom buttons still work, and placing a pin
  // (which needs a pan to aim) keeps it.
  const coarsePointer = useMediaQuery('(pointer: coarse)')
  const panEnabled = editable || !coarsePointer

  const selected = useMemo(() => new Set(selectedIds), [selectedIds])
  const highlightKey = (highlightIds || []).join('|')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const highlighted = useMemo(() => new Set(highlightIds || []), [highlightKey])

  const { sites, missing, frame } = useMemo(() => {
    const grouped = groupIntoSites(points)
    // Framed on EVERYTHING drawn, not only on the sites. A figure whose
    // content is a likelihood region and no marks at all -- the origin profile
    // is exactly that -- would otherwise fall back to the whole globe, which
    // is what a first render showed: a 15-cell region rendered as a smudge
    // over western Europe on a world map.
    const framing = [
      ...grouped,
      ...(region || []).map((cell) => ({ latitude: cell.lat, longitude: cell.lon })),
      ...(links || []).flatMap((link) => [link.from, link.to]),
      ...(isochrons || []).flatMap((line) => line.points),
      ...(origin ? [origin] : []),
    ] as MapSite[]
    return { sites: grouped, missing: unplaced(points), frame: frameFor(framing) }
  }, [points, region, links, isochrons, origin])

  // In edit mode the window is fixed at what it opened on. Every click moves
  // the preview pin, which moves the frame, and re-framing on each one threw
  // the reader's own zoom away just after they had used it to aim: the map
  // opens on the collection, and from then on the view is theirs.
  const openingFrame = useRef(frame)
  const dataFrame = editable ? openingFrame.current : frame

  // The box's size, measured by the observer below; null until first layout.
  const [box, setBox] = useState<{ width: number; height: number } | null>(null)
  // What Plotly took from the box for itself -- the upright colour bar's
  // margin -- measured once it has drawn. The map is drawn in what is left,
  // so that is what the frame must fill: fitted to the whole box, a map with
  // a colour bar came out too wide for its plot area and was letterboxed
  // above and below.
  const [reserved, setReserved] = useState({ x: 0, y: 0 })
  // The CSS aspect the box is given below, until it has been measured.
  const boxAspect = box ? box.width / box.height : 16 / 9
  const aspect = box
    ? Math.max(1, box.width - reserved.x) / Math.max(1, box.height - reserved.y)
    : boxAspect
  // Portrait or near it: a phone's column, where the colour bar lies flat.
  // Decided on the box, not the plot area, since it decides the colour bar.
  const narrowFigure = boxAspect < 1.25
  const viewFrame = useMemo(
    () => (dataFrame ? fitFrameToAspect(dataFrame, aspect) : null),
    [dataFrame, aspect],
  )

  const traces = useMemo((): Data[] => {
    // Every LINE's longitudes, brought within half a turn of the frame's
    // centre on the tiled map -- see unwrapLongitude for why the offline
    // map needs nothing of the kind.
    const centre = dataFrame ? (dataFrame.lon[0] + dataFrame.lon[1]) / 2 : 0
    const lineLon = (longitude: number) => (tiles ? unwrapLongitude(longitude, centre) : longitude)

    // Split by selection rather than coloured per mark, so the two sets are
    // separate traces and Plotly can hover them independently. A site counts
    // as selected when ANY date on it is: the pin is the position, and a
    // position is either in the selection's reach or not.
    const isSelected = (site: MapSite) => site.ids.some((id) => selected.has(id))
    const build = (subset: MapSite[], name: string, colour: string, size: number) => ({
      // scattermap is MapLibre's trace and scattergeo the offline one; they
      // take the same lat/lon/marker fields, so only the type changes.
      // (scattermapbox, the older tiled trace, is gone as of Plotly 4.)
      type: (tiles ? 'scattermap' : 'scattergeo') as 'scattermap' | 'scattergeo',
      mode: 'markers' as const,
      name,
      lat: subset.map((s) => s.latitude),
      lon: subset.map((s) => s.longitude),
      text: subset.map(siteLabel),
      customdata: subset.map((s) => s.ids.length),
      marker: {
        size,
        color: colour,
        line: { color: plot.surface, width: 1.5 },
        opacity: 0.9,
      },
      hovertemplate: '%{text}<br>%{lat:.4f}, %{lon:.4f}<extra></extra>',
    })

    const geoType = (tiles ? 'scattermap' : 'scattergeo') as
      'scattermap' | 'scattergeo'
    // Always one geo trace, even with nothing to draw. The subplot exists
    // because a trace asks for it: given zero traces Plotly draws its default
    // cartesian grid, and the placing dialog opened on that -- a collection
    // with nothing placed yet had no trace, so no basemap, no projection, and
    // every click reported as unreadable. This is the ordinary first state
    // of that dialog, not an edge case.
    const list: Data[] = [
      { type: geoType, mode: 'markers' as const, lat: [], lon: [], hoverinfo: 'skip' as const },
    ]

    // The likelihood region underneath everything: it is the backdrop the
    // marks are read against, and drawn over them it would bury them.
    if (region && region.length > 0) {
      const cells = {
        mode: 'markers' as const,
        name: 'Origin region',
        lat: region.map((cell) => cell.lat),
        lon: region.map((cell) => cell.lon),
        text: region.map((cell) => `support ${cell.support.toFixed(2)}`),
        hovertemplate: '%{text}<br>%{lat:.1f}, %{lon:.1f}<extra></extra>',
      }
      const marker = {
        size: Math.max(8, 14 * regionStepDeg),
        color: region.map((cell) => cell.support),
        colorscale: VALUE_SCALE,
        cmin: 0,
        cmax: 1,
        opacity: 0.45,
      }
      list.push(
        tiles
          ? { ...cells, type: 'scattermap', marker }
          : {
              ...cells,
              type: 'scattergeo',
              // Square, and sized to the grid step, so the cells read as the
              // coarse grid this is rather than as a set of observations.
              // Offline only: scattermap marks are circles and take no
              // outline.
              marker: { ...marker, symbol: 'square', line: { width: 0 } },
            },
      )
    }

    // Isochrons next, so every mark and every other line sits over them.
    for (const line of isochrons || []) {
      if (line.points.length < 2) continue
      // Labelled at the MIDDLE point, not at an end. The ends are where the
      // lines run off the frame -- an isochron is drawn past the sites on
      // purpose -- so an end label is the one part of the figure most likely
      // to be clipped, which is what a first render showed. The middle is
      // inside the site cluster by construction.
      //
      // The middle POINT the server sent, not the mean of the endpoints. A
      // mean of two longitudes either side of the antimeridian lands on the
      // far side of the world -- 167 and -91 average to 38, the Indian Ocean
      // -- and drawing the line through it sent every isochron on a
      // Polynesian map out across Asia and back.
      const middle = Math.floor(line.points.length / 2)
      const label = formatIsochron ? formatIsochron(line.age) : `${Math.round(line.age)}`
      const textfont = { size: 10, color: plot.axis }
      if (tiles) {
        // Two traces on the tiled map where the offline one takes one. A
        // `lines+text` trace has no marker, and plotly's scattermap works its
        // label offset out from the marker size all the same -- NaN, which
        // MapLibre rejects ("text-offset[1]: number expected, NaN found").
        // On 2.35 that took the WHOLE map down; on 4.1.1, re-tested, the
        // rest of the map draws but every isochron label is lost and the
        // map raises an error. So the line goes in unlabelled and the label
        // rides on a one-point marker trace whose marker is there only to be
        // measured, drawn at zero opacity.
        list.push({
          type: geoType,
          mode: 'lines' as const,
          name: label,
          lat: line.points.map((p) => p.latitude),
          lon: line.points.map((p) => lineLon(p.longitude)),
          line: { color: plot.axis, width: 1 },
          hoverinfo: 'skip' as const,
        })
        list.push({
          type: geoType,
          mode: 'markers+text' as const,
          name: label,
          lat: [line.points[middle].latitude],
          lon: [lineLon(line.points[middle].longitude)],
          text: [label],
          textposition: 'top center' as const,
          // A face the basemap host serves glyphs for: MapLibre draws no
          // text it has no glyphs for, and says so only in the console.
          textfont: { ...textfont, family: BASEMAP_TEXT_FONT },
          marker: { size: 6, opacity: 0 },
          hoverinfo: 'skip' as const,
        })
        continue
      }
      list.push({
        type: geoType,
        mode: 'lines+text' as const,
        name: label,
        lat: line.points.map((p) => p.latitude),
        lon: line.points.map((p) => p.longitude),
        line: { color: plot.axis, width: 1 },
        // One caption per line: a line captioned twice reads as two.
        text: line.points.map((_p, i) => (i === middle ? label : '')),
        textposition: 'top center' as const,
        textfont,
        hoverinfo: 'skip' as const,
      })
    }

    // Spanning-tree links, under the marks they join.
    if (links && links.length > 0) {
      const edges = links.map((link) =>
        tiles ? densifyGreatCircle([link.from, link.to]) : [link.from, link.to]
      )
      list.push({
        type: geoType,
        mode: 'lines' as const,
        name: 'Links',
        // One trace with nulls between segments: Plotly breaks the line at a
        // null, so the whole tree draws in one pass without joining the end
        // of one edge to the start of the next.
        // On tiles each edge is walked as its great circle, which Mercator
        // would otherwise draw as a straight chord; offline, Plotly's geo
        // subplot draws the arc between two points itself.
        lat: edges.flatMap((edge) => [...edge.map((p) => p.latitude), null]),
        lon: edges.flatMap((edge) => [...edge.map((p) => lineLon(p.longitude)), null]),
        line: { color: plot.posteriorLine, width: 1.5 },
        hoverinfo: 'skip' as const,
      })
    }

    if (values) {
      // Graded strength of one hue. See the `values` prop for why this
      // replaces the selection colouring rather than joining it.
      const valueOf = (site: MapSite) => {
        for (const id of site.ids) {
          if (values[id] !== undefined) return values[id]
        }
        return null
      }
      const graded = sites.filter((site) => valueOf(site) !== null)
      if (graded.length > 0) {
        const magnitudes = graded.map((s) => Math.abs(valueOf(s) as number))
        const reach = Math.max(...magnitudes, 1)
        // See the `valueScale` prop. The neutral at zero is a pale warm
        // grey -- the app's surface tinted with its own ink -- so a site the
        // front predicts exactly reads as "on the surface" rather than as a
        // third colour, and so it is a neutral on the tiled basemap too,
        // where the ramp's own pale green would have been a fourth.
        const scale =
          valueScale === 'signed'
            ? {
                colorscale: [
                  [0, plot.residualLate],
                  // WARM_INK at 13% over white, flattened: Plotly's colour
                  // bar cannot composite an rgba stop.
                  [0.5, '#e3e2e2'],
                  [1, plot.residualEarly],
                ] as Array<[number, string]>,
                cmin: -reach,
                cmax: reach,
              }
            : { colorscale: VALUE_SCALE }
        if (tiles) {
          // The outline, as a disc UNDER each mark. scattermap has no
          // marker.line -- MapLibre's circle layer takes no stroke through
          // plotly -- so the ink edge the offline map draws in one trace is
          // drawn here in two: an ink disc one outline-width larger on every
          // side, then the coloured mark over it. Same size, same
          // positions, so they cannot drift apart.
          list.push({
            type: geoType,
            mode: 'markers' as const,
            name: 'Outline',
            lat: graded.map((s) => s.latitude),
            lon: graded.map((s) => s.longitude),
            marker: {
              size: VALUE_MARK_SIZE + 2 * VALUE_MARK_OUTLINE.width,
              color: plot[VALUE_MARK_OUTLINE.color],
            },
            hoverinfo: 'skip' as const,
          })
        }
        const marks = {
          mode: 'markers' as const,
          name: valueLabel || 'Value',
          lat: graded.map((s) => s.latitude),
          lon: graded.map((s) => s.longitude),
          text: graded.map(siteLabel),
          hovertemplate:
            valueScale === 'signed'
              ? '%{text}<br>%{marker.color:+.0f} yr<extra></extra>'
              : '%{text}<br>%{marker.color:.0f}<extra></extra>',
        }
        const marker = {
          size: VALUE_MARK_SIZE,
          color: graded.map((s) => valueOf(s) as number),
          ...scale,
          // Reversed so the OLDEST arrival is the strongest shade: the
          // front starts dark and fades as it spreads, which is the way
          // round a reader already expects a wave to be drawn.
          reversescale: false,
          // Upright beside a landscape map; laid flat across the top of a
          // portrait one, where an upright bar and its title took a third of
          // a phone's width from the map it keys.
          colorbar: narrowFigure
            ? {
                orientation: 'h' as const,
                title: { text: valueLabel || '', side: 'top' as const },
                thickness: 8,
                len: 0.7,
                x: 0.5,
                xanchor: 'center' as const,
                y: 0.99,
                yanchor: 'top' as const,
                bgcolor: alpha(plot.surface, 0.85),
                outlinewidth: 0,
              }
            : {
                title: { text: valueLabel || '', side: 'right' as const },
                thickness: 12,
                len: 0.8,
                outlinewidth: 0,
              },
        }
        // The outline is the marker's own offline; on the tiled map it is
        // the disc trace pushed just above.
        list.push(
          tiles
            ? { ...marks, type: 'scattermap', marker }
            : {
                ...marks,
                type: 'scattergeo',
                marker: {
                  ...marker,
                  line: { color: plot[VALUE_MARK_OUTLINE.color], width: VALUE_MARK_OUTLINE.width },
                },
              },
        )
      }
      // A ring round the site the reader is pointing at elsewhere -- a row
      // of the residual table -- so the row and its mark are found together.
      const ringed = sites.filter((site) => site.ids.some((id) => highlighted.has(id)))
      if (ringed.length > 0) {
        const ring = {
          mode: 'markers' as const,
          name: 'Highlighted',
          lat: ringed.map((s) => s.latitude),
          lon: ringed.map((s) => s.longitude),
          hoverinfo: 'skip' as const,
        }
        // MapLibre's circle layer has no open symbol, so on tiles the ring
        // is a translucent disc over the mark instead.
        list.push(
          tiles
            ? {
                ...ring,
                type: 'scattermap',
                marker: { size: VALUE_MARK_SIZE + 12, color: plot.posteriorLine, opacity: 0.35 },
              }
            : {
                ...ring,
                type: 'scattergeo',
                marker: {
                  size: VALUE_MARK_SIZE + 10,
                  symbol: 'circle-open',
                  color: plot.posteriorLine,
                  line: { color: plot.posteriorLine, width: 2.5 },
                },
              },
        )
      }
    } else {
      const chosen = sites.filter(isSelected)
      const rest = sites.filter((site) => !isSelected(site))
      if (rest.length > 0) list.push(build(rest, 'Placed', plot.mean, 9))
      // Drawn after, so a selected pin is never hidden under an unselected one
      // sitting at the same place on screen.
      if (chosen.length > 0) list.push(build(chosen, 'Selected', plot.posteriorLine, 13))
    }

    // Last, so it is never hidden: it is the assumption the whole figure is
    // measured from, and a reader who cannot find it cannot read the rest.
    if (origin) {
      if (tiles) {
        // The ring, as a disc under the mark -- scattermap has no
        // marker.line, as above. Without it the origin was a white dot on a
        // cream basemap: the one mark the figure is measured from, invisible.
        list.push({
          type: geoType,
          mode: 'markers' as const,
          name: 'Origin ring',
          lat: [origin.latitude],
          lon: [origin.longitude],
          marker: { size: 15 + 2 * 3, color: plot.posteriorLine },
          hoverinfo: 'skip' as const,
        })
      }
      const pin = {
        mode: 'markers' as const,
        name: 'Origin',
        lat: [origin.latitude],
        lon: [origin.longitude],
        text: ['Assumed origin'],
        hovertemplate: '%{text}<br>%{lat:.4f}, %{lon:.4f}<extra></extra>',
      }
      const marker = { size: 15, color: plot.surface }
      // An x ringed in the line colour offline; on the tiled map a plain
      // disc over the ring trace above.
      list.push(
        tiles
          ? { ...pin, type: 'scattermap', marker }
          : {
              ...pin,
              type: 'scattergeo',
              marker: { ...marker, symbol: 'x', line: { color: plot.posteriorLine, width: 3 } },
            },
      )
    }

    return list
  }, [sites, selected, plot, tiles, values, valueLabel, valueScale, origin, isochrons, narrowFigure,
      formatIsochron, region, regionStepDeg, links, dataFrame, highlighted])

  /**
   * Clicks are taken in the CAPTURE phase, on a native listener, and both
   * halves of that are load-bearing.
   *
   * Plotly covers the drawn map with its own drag rect, which stops the event:
   * an ordinary bubble-phase handler (React's onClick included) fires only for
   * clicks in the letterboxed margin AROUND the map, which is to say never
   * where anybody aims. Capturing on the container gets the event on the way
   * down, before that rect sees it. Verified in Chromium against 2.35.3:
   * clicking the mark drawn at a known coordinate returns that coordinate to
   * within a pixel.
   *
   * A native listener rather than onClickCapture because React's delegated
   * capture happens to work here for reasons internal to React's root
   * listener, and this does not depend on them.
   */
  useEffect(() => {
    const container = plotBox.current
    if (!editable || !onPlace || !container) return

    // Where the pointer went down, so the click that ends a pan can be told
    // from one that places a pin. Null between gestures.
    let downAt: { x: number; y: number } | null = null
    const down = (event: PointerEvent) => {
      downAt = event.button === 0 ? { x: event.clientX, y: event.clientY } : null
    }

    const handle = (event: MouseEvent) => {
      const start = downAt
      downAt = null
      if (!start) return
      if (
        Math.abs(event.clientX - start.x) > CLICK_SLOP_PX ||
        Math.abs(event.clientY - start.y) > CLICK_SLOP_PX
      ) {
        // The pointer moved: that was a pan, and it ends where the reader let
        // go of the mouse, which is not where they want a date placed.
        return
      }
      const place = tiles
        ? placeFromTiledMap(graphDiv.current, event)
        : placeFromOfflineMap(graphDiv.current, container, event)
      if (place === UNAVAILABLE) {
        // The map cannot say where the click was. Announced rather than
        // swallowed: a map that silently ignores clicks reads as broken, and
        // the typed boxes beside it still work.
        setCannotPlace(true)
        return
      }
      // Off the globe. The corners of a rectangular plot are not on the earth,
      // so this is ignored rather than clamped to the nearest coast.
      if (!place) return
      setCannotPlace(false)
      onPlace(roundPlacement(place.latitude), roundPlacement(place.longitude))
    }

    container.addEventListener('pointerdown', down, true)
    container.addEventListener('click', handle, true)
    return () => {
      container.removeEventListener('pointerdown', down, true)
      container.removeEventListener('click', handle, true)
    }
  }, [editable, onPlace, tiles])

  /**
   * Resize the plot when its BOX changes size, not only when the window does.
   *
   * Plotly's own `responsive` config listens to the window alone, and the box
   * changes size without it: the dialog it sits in opens at its full width
   * after the first paint, a panel beside it collapses, the drawer closes.
   * Each of those left the map drawn at its old size inside a box that had
   * moved on, until the next window resize happened to fix it.
   */
  useEffect(() => {
    const container = plotBox.current
    if (!container || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver((entries) => {
      const { width, height: tall } = entries[0].contentRect
      if (width > 0 && tall > 0) setBox({ width, height: tall })
      const gd = graphDiv.current as HTMLElement | null
      if (gd && gd.isConnected) Plotly.Plots.resize(gd)
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  const config = useMemo(
    (): Partial<Config> => ({
      displayModeBar: false,
      responsive: true,
      // Wheel zoom in every mode, for the same reason dragmode is pan -- but
      // only with Ctrl or Cmd held, which the wheel effect below enforces.
      scrollZoom: true,
      // Served from this app. Without it Plotly fetches the world from
      // cdn.plot.ly -- see TOPOJSON_URL above.
      topojsonURL: TOPOJSON_URL,
    }),
    [],
  )

  const onDrawn = useCallback((gd: HTMLElement) => {
    graphDiv.current = gd
    const full = (gd as HTMLElement & {
      _fullLayout?: { width: number; height: number; _size: { w: number; h: number } }
    })._fullLayout
    if (full) {
      const x = full.width - full._size.w
      const y = full.height - full._size.h
      // Unchanged to the pixel is left alone, or every draw would ask for
      // another.
      setReserved((prev) =>
        Math.abs(prev.x - x) < 0.5 && Math.abs(prev.y - y) < 0.5 ? prev : { x, y },
      )
    }
    // MapLibre names its canvas "Map", whichever map it is.
    const canvas = (mapLibreOf(gd) as { getCanvas?: () => HTMLElement } | null)?.getCanvas?.()
    canvas?.setAttribute('aria-label', label)
  }, [label])

  const layout = useMemo((): Partial<Layout> => {
    const common = {
      margin: { l: 0, r: 0, t: 0, b: 0 },
      paper_bgcolor: plot.surface,
      font: plotFont,
      showlegend: false,
      // Pan in every mode, edit included. Placing a pin at site scale needs
      // a zoomed-in map, and the click that ends a pan is kept from placing
      // anything by the pointer-slop check in the click handler above.
      dragmode: panEnabled ? ('pan' as const) : (false as const),
      autosize: true,
      // The reader's zoom and pan survive a re-render. Without this every new
      // trace array -- a hover colour, a preview pin -- redrew the map at the
      // frame it opened on. Keyed on the DATA frame, not the fitted one, so a
      // genuinely new window -- which in read-only mode means new data --
      // reframes, and a resize does not.
      uirevision: JSON.stringify(dataFrame),
    }

    if (tiles) {
      return {
        ...common,
        map: {
          // Positron from OpenFreeMap, trimmed -- see theme/basemapStyle.ts.
          // The tile request is the disclosure: choosing this is what sends
          // a request naming the square of the earth on screen to a third
          // party, which is why this branch is reached only for a project
          // that has said yes.
          style: basemapStyle,
          center: viewFrame
            ? {
                lat: (viewFrame.lat[0] + viewFrame.lat[1]) / 2,
                lon: (viewFrame.lon[0] + viewFrame.lon[1]) / 2,
              }
            : { lat: 20, lon: 0 },
          // A tiled map takes a centre and a zoom where the offline one takes
          // two ranges, so the same frame has to be restated.
          zoom: viewFrame ? zoomForFrame(viewFrame) : 1,
        },
      }
    }

    return {
      ...common,
      geo: {
        scope: 'world' as const,
        resolution: GEO_RESOLUTION,
        projection: { type: 'natural earth' as const },
        showcoastlines: true,
        coastlinecolor: plot.axis,
        coastlinewidth: 0.6,
        showcountries: true,
        countrycolor: plot.grid,
        showland: true,
        landcolor: plot.surface,
        showocean: true,
        // The one tinted surface on any figure in this app. A map with no
        // water reads as a set of shapes; the sea has to be the thing the
        // coast is a coast of. At the grid's strength, so it stays quieter
        // than any mark drawn on it.
        oceancolor: plot.grid,
        showlakes: true,
        lakecolor: plot.grid,
        showframe: false,
        bgcolor: plot.surface,
        // Plotly 4 fits a geo subplot to its data unless told not to, which
        // would override the ranges below -- the frame this component chose,
        // margins and the reader's own zoom included.
        fitbounds: false as const,
        ...(viewFrame
          ? { lonaxis: { range: viewFrame.lon }, lataxis: { range: viewFrame.lat } }
          : {}),
      },
    }
  }, [plot, plotFont, viewFrame, dataFrame, tiles, basemapStyle, panEnabled])

  /**
   * The wheel scrolls the page unless Ctrl or Cmd is held.
   *
   * With scrollZoom on and three maps stacked in one column, scrolling down
   * the Results page zoomed whichever map passed under the pointer -- the
   * origin map ended up on the Philippines -- and the page stopped moving.
   * Caught on the way DOWN to the plot, in the capture phase, so neither
   * Plotly's geo zoom nor MapLibre's ever sees a plain wheel; the browser
   * scrolls the page as it would over any figure. A trackpad pinch arrives
   * as a wheel with ctrlKey set, so pinch-to-zoom keeps working.
   */
  useEffect(() => {
    const box = plotBox.current
    if (!box) return
    let timer: ReturnType<typeof setTimeout> | undefined
    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.metaKey) return
      event.stopPropagation()
      setWheelHint(true)
      clearTimeout(timer)
      timer = setTimeout(() => setWheelHint(false), 1400)
    }
    box.addEventListener('wheel', onWheel, { capture: true, passive: true })
    return () => {
      box.removeEventListener('wheel', onWheel, { capture: true })
      clearTimeout(timer)
    }
  }, [])

  /** Zoom about the centre, by button: the keyboard and touch path. */
  const zoomBy = useCallback((factor: number) => {
    const gd = graphDiv.current as
      | (HTMLElement & {
          _fullLayout?: { map?: { zoom?: number }; geo?: { projection?: { scale?: number } } }
        })
      | null
    if (!gd || !gd._fullLayout) return
    if (tiles) {
      const zoom = gd._fullLayout.map?.zoom ?? 1
      Plotly.relayout(gd, { 'map.zoom': zoom + Math.log2(factor) } as unknown as Partial<Layout>)
    } else {
      const scale = gd._fullLayout.geo?.projection?.scale ?? 1
      Plotly.relayout(gd, { 'geo.projection.scale': Math.max(1, scale * factor) } as unknown as Partial<Layout>)
    }
  }, [tiles])

  return (
    <Box>
      <Paper
        elevation={0}
        variant="outlined"
        ref={plotBox}
        role="group"
        aria-label={label}
        sx={{
          p: 0,
          overflow: 'hidden',
          cursor: editable ? 'crosshair' : 'default',
          // Landscape, and as wide as it is given: a map is the one figure
          // whose subject is wider than it is tall, and at a fixed 380px in a
          // 1200px column it was a strip. Floored for narrow screens and
          // capped against the viewport -- see the `height` prop.
          width: '100%',
          aspectRatio: '16 / 9',
          minHeight: height,
          maxHeight: MAX_HEIGHT,
          // The plot is absolutely sized to this box rather than the reverse,
          // so Plotly's autosize measures a box whose size the box decided.
          position: 'relative',
        }}
      >
        {!deciding && (
          <PlotlyFigure
            // A fresh plot when the basemap changes kind, rather than asking
            // Plotly.react to turn a geo subplot into a MapLibre one in place.
            // "Reset view" is a fresh plot too: the one way back to the
            // frame it opened on that does not depend on which subplot it is.
            key={`${tiles ? 'tiled' : 'offline'}-${resetCount}`}
            data={traces}
            layout={layout}
            config={config}
            onDrawn={onDrawn}
            style={{ position: 'absolute', inset: 0 }}
          />
        )}
        {/* What a plain wheel did instead, said where the reader is looking.
            The caption under the map says it for anyone who reads first. */}
        <Box
          aria-hidden
          sx={{
            position: 'absolute',
            inset: 0,
            display: 'grid',
            placeItems: 'center',
            pointerEvents: 'none',
            bgcolor: (theme) => alpha(theme.palette.background.paper, 0.72),
            opacity: wheelHint ? 1 : 0,
            transition: 'opacity 180ms cubic-bezier(0.16, 1, 0.3, 1)',
          }}
        >
          <Typography variant="body2" sx={{ fontWeight: 500 }}>
            Hold {ZOOM_MODIFIER} and scroll to zoom the map
          </Typography>
        </Box>
      </Paper>

      {/* Controls first, on one line, and the caption under them at full
          width: beside four controls on a phone the caption wrapped to four
          lines in a column a third of the width. */}
      <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', columnGap: 1, mt: 0.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center' }}>
          <Tooltip title="Zoom in">
            <span>
              <IconButton
                size="small"
                aria-label={`Zoom in on the ${label.toLowerCase()}`}
                onClick={() => zoomBy(2)}
                disabled={deciding}
              >
                <AddIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Zoom out">
            <span>
              <IconButton
                size="small"
                aria-label={`Zoom out on the ${label.toLowerCase()}`}
                onClick={() => zoomBy(0.5)}
                disabled={deciding}
              >
                <RemoveIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Reset view">
            <span>
              <IconButton
                size="small"
                aria-label={`Reset the ${label.toLowerCase()} to its opening view`}
                onClick={() => setResetCount((count) => count + 1)}
                disabled={deciding}
              >
                <ZoomOutMapIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        </Box>
        <Box sx={{ flex: 1 }} />
        {/* Only where the project allows tiles. A project that has not opted
            in gets no switch, since the opt-in is the privacy decision and
            this is not the place it is made. One preference for every map,
            which the accessible name says: flipping one flips them all. */}
        {tilesWanted === true && (
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={preferTiles}
                onChange={(event) => setPreferTiles(event.target.checked)}
                inputProps={{ 'aria-label': `${BASEMAP_PROVIDER} tiles, on every map` }}
              />
            }
            label={`${BASEMAP_PROVIDER} tiles`}
            slotProps={{ typography: { variant: 'caption', color: 'text.secondary' } }}
            sx={{ mr: 0.5, whiteSpace: 'nowrap' }}
          />
        )}
        {/* The app's own download control, as on every other figure: Plotly's
            modebar renders anchors with no href, role or accessible name, none
            of which can take focus. */}
        <Button
          size="small"
          startIcon={<DownloadIcon />}
          aria-label={`Download the ${label.toLowerCase()} as PNG`}
          // Outside plotBox, so the map's capture listener never sees this
          // click and saving a figure cannot also move a pin.
          onClick={() => downloadPlotPng(plotBox.current, exportName)}
        >
          Download PNG
        </Button>
      </Box>
      <Typography variant="caption" color="textSecondary" component="p" sx={{ maxWidth: '72ch' }}>
        {cannotPlace
          ? 'This map cannot read a click on this version of the plotting library. Type the position instead.'
          : deciding
            ? tilesWanted === null
              ? 'Loading the map…'
              : `Checking whether ${BASEMAP_PROVIDER} can be reached…`
            : [
                editable ? 'Click the map to place every selected date at that position.' : '',
                panEnabled
                  ? `Drag to pan; hold ${ZOOM_MODIFIER} and scroll, or use the buttons, to zoom.`
                  : 'Use the buttons to zoom.',
                // Which basemap is drawn is said only when it is not the
                // one asked for: the switch beside this says the rest.
                fellBack
                  ? `${BASEMAP_PROVIDER} is unreachable, so the offline map is shown.`
                  : '',
              ].filter(Boolean).join(' ')}
      </Typography>

      {missing.length > 0 && (
        <Box sx={{ mt: 1.5 }}>
          <Typography variant="subtitle2" gutterBottom>
            Not placed ({missing.length})
          </Typography>
          <Typography variant="body2" color="textSecondary">
            {missing.map((p) => p.name).join(', ')}
          </Typography>
        </Box>
      )}

      {sites.length === 0 && missing.length > 0 && (
        <Typography variant="body2" color="textSecondary" sx={{ mt: 1 }}>
          Nothing in this collection has a position yet
          {editable ? ', so the map opens on the whole world.' : '.'}
        </Typography>
      )}
    </Box>
  )
}
