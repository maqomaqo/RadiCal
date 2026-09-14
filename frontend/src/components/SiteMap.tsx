import { useEffect, useMemo, useRef, useState } from 'react'
import { Box, Button, Paper, Typography } from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import Plotly from 'plotly.js-dist-min'
import createPlotlyComponent from 'react-plotly.js/factory'

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
 * so this directory must contain `world_110m.json`; see public/geo/README.md
 * for why it is vendored rather than fetched. Left unset, Plotly goes to
 * cdn.plot.ly, and on a network that cannot reach it the plot does not degrade
 * -- it rejects, and there is no map at all.
 */
const TOPOJSON_URL = '/geo/'

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
    return { sites: grouped, missing: unplaced(points), frame: frameFor(grouped) }
  }, [points])

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

    const chosen = sites.filter(isSelected)
    const rest = sites.filter((site) => !isSelected(site))
    const list = []
    if (rest.length > 0) list.push(build(rest, 'Placed', plot.mean, 9))
    // Drawn after, so a selected pin is never hidden under an unselected one
    // sitting at the same place on screen.
    if (chosen.length > 0) list.push(build(chosen, 'Selected', plot.posteriorLine, 13))
    return list
  }, [sites, selected, plot, tiles])

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
