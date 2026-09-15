import { useEffect, useMemo, useRef, useState } from 'react'
import { Box, Button, Paper, Typography } from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import Plotly from 'plotly.js-dist-min'
import createPlotlyComponent from 'react-plotly.js/factory'

import { CONTEXT_PALETTE } from '@/theme/diagramColors'
import { usePlotColors, usePlotFont } from '@/theme/plotColors'
import { downloadPlotPng } from '@/utils/downloadPlot'
import {
  frameFor,
  geoProjectionOf,
  groupIntoSites,
  latLonFromMapPoint,
  latLonFromPoint,
  mapLibreOf,
  roundPlacement,
  siteLabel,
  unplaced,
  zoomForFrame,
} from '@/utils/siteMapGeometry'
import type { MapPoint, MapSite } from '@/utils/siteMapGeometry'

const Plot = createPlotlyComponent(Plotly)

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
 */
const VALUE_RAMP = ['#e3f1ec', '#b4d9cd', ...CONTEXT_PALETTE]

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
   */
  tiles?: boolean
  /** Height of the plot area in pixels. */
  height?: number
  /** Used to name the downloaded PNG. */
  exportName?: string
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
   * A line drawn over the marks -- the fitted front, in the dispersal panel.
   * Ordered; drawn as given, with no interpolation of its own.
   */
  front?: Array<{ latitude: number; longitude: number }>
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
   * A likelihood region: candidate positions with a support in (0, 1].
   *
   * Drawn as translucent square cells rather than as a contour, because that
   * is what it IS -- a coarse grid of candidates, each evaluated -- and a
   * smooth contour drawn through them would imply a resolution the grid does
   * not have. Under everything else, since it is the backdrop a reader checks
   * the marks against.
   */
  region?: Array<{ lat: number; lon: number; support: number }>
  /** What one region cell spans, in degrees, so the cells read as the grid. */
  regionStepDeg?: number
  /**
   * Individual segments, each of which is a real link between two places --
   * the edges of a spanning tree.
   *
   * Deliberately NOT `front`, which is one polyline. A WALK of a tree visits
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
  tiles = false,
  height = 420,
  exportName = 'site-map',
  values,
  valueLabel,
  front,
  origin,
  isochrons,
  region,
  regionStepDeg = 1,
  links,
}: SiteMapProps) {
  const plot = usePlotColors()
  const plotFont = usePlotFont()
  const plotBox = useRef<HTMLDivElement>(null)
  const graphDiv = useRef<unknown>(null)
  // Null until the first click tells us. Not an error state on its own: it
  // only matters in edit mode, and the map is read-only by default.
  const [cannotPlace, setCannotPlace] = useState(false)

  const selected = useMemo(() => new Set(selectedIds), [selectedIds])

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
      ...(front || []),
      ...(links || []).flatMap((link) => [link.from, link.to]),
      ...(isochrons || []).flatMap((line) => line.points),
      ...(origin ? [origin] : []),
    ] as MapSite[]
    return { sites: grouped, missing: unplaced(points), frame: frameFor(framing) }
  }, [points, region, front, links, isochrons, origin])

  const traces = useMemo(() => {
    // Split by selection rather than coloured per mark, so the two sets are
    // separate traces and Plotly can hover them independently. A site counts
    // as selected when ANY date on it is: the pin is the position, and a
    // position is either in the selection's reach or not.
    const isSelected = (site: MapSite) => site.ids.some((id) => selected.has(id))
    const build = (subset: MapSite[], name: string, colour: string, size: number) => ({
      // scattermap is MapLibre's trace and scattergeo the offline one; they
      // take the same lat/lon/marker fields, so only the type changes.
      // scattermapbox is deprecated in 2.35 and is deliberately not used.
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
    const list: unknown[] = [
      { type: geoType, mode: 'markers' as const, lat: [], lon: [], hoverinfo: 'skip' as const },
    ]

    // The likelihood region underneath everything: it is the backdrop the
    // marks are read against, and drawn over them it would bury them.
    if (region && region.length > 0) {
      const last = VALUE_RAMP.length - 1
      list.push({
        type: geoType,
        mode: 'markers' as const,
        name: 'Origin region',
        lat: region.map((cell) => cell.lat),
        lon: region.map((cell) => cell.lon),
        text: region.map((cell) => `support ${cell.support.toFixed(2)}`),
        marker: {
          // Square, and sized to the grid step, so the cells read as the
          // coarse grid this is rather than as a set of observations.
          symbol: tiles ? undefined : ('square' as const),
          size: Math.max(8, 14 * regionStepDeg),
          color: region.map((cell) => cell.support),
          colorscale: VALUE_RAMP.map((hex, i) => [i / last, hex]),
          cmin: 0,
          cmax: 1,
          opacity: 0.45,
          line: { width: 0 },
        },
        hovertemplate: '%{text}<br>%{lat:.1f}, %{lon:.1f}<extra></extra>',
      })
    }

    // Isochrons next, so every mark and every other line sits over them.
    for (const line of isochrons || []) {
      if (line.points.length < 2) continue
      const [from, to] = [line.points[0], line.points[line.points.length - 1]]
      // Labelled at the MIDDLE, not at an end. The ends are where the lines
      // run off the frame -- an isochron is drawn past the sites on purpose --
      // so an end label is the one part of the figure most likely to be
      // clipped, which is what a first render showed. The middle is inside the
      // site cluster by construction.
      const middle = {
        latitude: (from.latitude + to.latitude) / 2,
        longitude: (from.longitude + to.longitude) / 2,
      }
      list.push({
        type: geoType,
        mode: 'lines+text' as const,
        name: `${Math.round(line.age)}`,
        lat: [from.latitude, middle.latitude, to.latitude],
        lon: [from.longitude, middle.longitude, to.longitude],
        line: { color: plot.axis, width: 1 },
        // One caption per line: a line captioned twice reads as two.
        text: ['', `${Math.round(line.age)}`, ''],
        textposition: 'top center' as const,
        textfont: { size: 10, color: plot.axis },
        hoverinfo: 'skip' as const,
      })
    }

    // Spanning-tree links, under the marks they join.
    if (links && links.length > 0) {
      list.push({
        type: geoType,
        mode: 'lines' as const,
        name: 'Links',
        // One trace with nulls between segments: Plotly breaks the line at a
        // null, so the whole tree draws in one pass without joining the end
        // of one edge to the start of the next.
        lat: links.flatMap((link) => [link.from.latitude, link.to.latitude, null]),
        lon: links.flatMap((link) => [link.from.longitude, link.to.longitude, null]),
        line: { color: plot.posteriorLine, width: 1.5 },
        hoverinfo: 'skip' as const,
      })
    }

    // The front next, so it lies UNDER the marks it was fitted to. A line
    // drawn over a site would hide the observation behind the model.
    if (front && front.length > 1) {
      list.push({
        type: geoType,
        mode: 'lines' as const,
        name: 'Fitted front',
        lat: front.map((p) => p.latitude),
        lon: front.map((p) => p.longitude),
        line: { color: plot.posteriorLine, width: 2, dash: 'dot' },
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
        const last = VALUE_RAMP.length - 1
        list.push({
          type: geoType,
          mode: 'markers' as const,
          name: valueLabel || 'Value',
          lat: graded.map((s) => s.latitude),
          lon: graded.map((s) => s.longitude),
          text: graded.map(siteLabel),
          marker: {
            size: 12,
            color: graded.map((s) => valueOf(s) as number),
            colorscale: VALUE_RAMP.map((hex, i) => [i / last, hex]),
            // Reversed so the OLDEST arrival is the strongest shade: the
            // front starts dark and fades as it spreads, which is the way
            // round a reader already expects a wave to be drawn.
            reversescale: false,
            line: { color: plot.surface, width: 1.5 },
            colorbar: {
              title: { text: valueLabel || '', side: 'right' as const },
              thickness: 12,
              len: 0.8,
              outlinewidth: 0,
            },
          },
          hovertemplate: '%{text}<br>%{marker.color:.0f}<extra></extra>',
        })
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
      list.push({
        type: geoType,
        mode: 'markers' as const,
        name: 'Origin',
        lat: [origin.latitude],
        lon: [origin.longitude],
        text: ['Assumed origin'],
        marker: {
          size: 15,
          color: plot.surface,
          symbol: tiles ? undefined : ('x' as const),
          line: { color: plot.posteriorLine, width: 3 },
        },
        hovertemplate: '%{text}<br>%{lat:.4f}, %{lon:.4f}<extra></extra>',
      })
    }

    return list
  }, [sites, selected, plot, tiles, values, valueLabel, front, origin, isochrons,
      region, regionStepDeg, links])

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

    const handle = (event: MouseEvent) => {
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

    container.addEventListener('click', handle, true)
    return () => container.removeEventListener('click', handle, true)
  }, [editable, onPlace, tiles])

  const layout = useMemo(() => {
    const common = {
      margin: { l: 0, r: 0, t: 0, b: 0 },
      paper_bgcolor: plot.surface,
      font: plotFont,
      showlegend: false,
      // dragmode false in edit mode: a click that also pans puts the pin down
      // a few degrees from where the reader let go of the mouse.
      dragmode: editable ? (false as const) : ('pan' as const),
      autosize: true,
    }

    if (tiles) {
      return {
        ...common,
        map: {
          // open-street-map, which needs no access token. The style name is
          // also the disclosure: choosing it is what sends a request naming
          // the square of the earth on screen to a third party, which is why
          // this branch is reached only for a project that has said yes.
          style: 'open-street-map',
          center: frame
            ? {
                lat: (frame.lat[0] + frame.lat[1]) / 2,
                lon: (frame.lon[0] + frame.lon[1]) / 2,
              }
            : { lat: 20, lon: 0 },
          // A tiled map takes a centre and a zoom where the offline one takes
          // two ranges, so the same frame has to be restated.
          zoom: frame ? zoomForFrame(frame) : 1,
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
        ...(frame
          ? { lonaxis: { range: frame.lon }, lataxis: { range: frame.lat } }
          : {}),
      },
    }
  }, [plot, plotFont, frame, editable, tiles])

  return (
    <Box>
      <Paper
        elevation={0}
        variant="outlined"
        ref={plotBox}
        sx={{
          p: 0,
          overflow: 'hidden',
          cursor: editable ? 'crosshair' : 'default',
        }}
      >
        <Plot
          data={traces}
          layout={layout}
          config={{
            displayModeBar: false,
            responsive: true,
            scrollZoom: !editable,
            // Served from this app. Without it Plotly fetches the world from
            // cdn.plot.ly -- see TOPOJSON_URL above.
            topojsonURL: TOPOJSON_URL,
          }}
          onInitialized={(_figure: unknown, gd: unknown) => {
            graphDiv.current = gd
          }}
          onUpdate={(_figure: unknown, gd: unknown) => {
            graphDiv.current = gd
          }}
          useResizeHandler
          style={{ width: '100%', height: `${height}px` }}
        />
      </Paper>

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.5 }}>
        <Typography variant="caption" color="textSecondary" sx={{ flex: 1 }}>
          {cannotPlace
            ? 'This map cannot read a click on this version of the plotting library. Type the position instead.'
            : editable
              ? 'Click the map to place every selected date at that position.'
              : tiles
                ? 'Basemap tiles are on for this project, so the tile server is told which area is on screen.'
                : 'Coastlines are drawn from data this app serves itself: no tiles are requested.'}
        </Typography>
        {/* The app's own download control, as on every other figure: Plotly's
            modebar renders anchors with no href, role or accessible name, none
            of which can take focus. */}
        <Button
          size="small"
          startIcon={<DownloadIcon />}
          // Outside plotBox, so the map's capture listener never sees this
          // click and saving a figure cannot also move a pin.
          onClick={() => downloadPlotPng(plotBox.current, exportName)}
        >
          Download PNG
        </Button>
      </Box>

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
