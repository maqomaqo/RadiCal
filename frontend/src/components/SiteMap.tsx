import { useMemo, useRef, useState } from 'react'
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
  latLonFromPoint,
  roundPlacement,
  siteLabel,
  unplaced,
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
      type: 'scattergeo' as const,
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
  }, [sites, selected, plot])

  const handleClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!editable || !onPlace) return
    const projection = geoProjectionOf(graphDiv.current)
    const svg = plotBox.current?.querySelector<SVGElement>('.main-svg')
    if (!projection || !svg) {
      // The map cannot answer where the click was. Said out loud rather than
      // swallowed: a map that silently ignores clicks reads as broken, and the
      // typed boxes beside it still work.
      setCannotPlace(true)
      return
    }
    const box = svg.getBoundingClientRect()
    const place = latLonFromPoint(
      projection,
      event.clientX - box.left,
      event.clientY - box.top,
    )
    // Off the globe -- the corners of a rectangular plot are not on the earth.
    // Ignored rather than clamped to the nearest coast.
    if (!place) return
    setCannotPlace(false)
    onPlace(roundPlacement(place.latitude), roundPlacement(place.longitude))
  }

  const layout = useMemo(
    () => ({
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
      margin: { l: 0, r: 0, t: 0, b: 0 },
      paper_bgcolor: plot.surface,
      font: plotFont,
      showlegend: false,
      // dragmode false in edit mode: a click that also pans puts the pin down
      // a few degrees from where the reader let go of the mouse.
      dragmode: editable ? (false as const) : ('pan' as const),
      autosize: true,
    }),
    [plot, plotFont, frame, editable],
  )

  return (
    <Box>
      <Paper
        elevation={0}
        variant="outlined"
        ref={plotBox}
        onClick={handleClick}
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
              : 'Coastlines are drawn from data this app serves itself: no tiles are requested.'}
        </Typography>
        {/* The app's own download control, as on every other figure: Plotly's
            modebar renders anchors with no href, role or accessible name, none
            of which can take focus. */}
        <Button
          size="small"
          startIcon={<DownloadIcon />}
          onClick={(event) => {
            // The map's own click handler is on the Paper, not here, but a
            // click on this button still bubbles through the Box above it.
            event.stopPropagation()
            downloadPlotPng(plotBox.current, exportName)
          }}
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
