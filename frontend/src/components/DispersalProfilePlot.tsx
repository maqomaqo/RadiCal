// Arrival against distance: the figure a wave-of-advance paper prints.
//
// The map says WHERE the sites that do not fit are. This says whether there is
// a front at all: every site at its distance from the origin and its modelled
// arrival, with the fitted line through them. A front that fits is a cloud
// hugging the line; a pause, a leapfrog or two fronts shows as the cloud
// leaving it -- a shape the speed's own interval cannot show, because that
// interval is how much the LINE moves between draws, not how far the sites sit
// from it.
//
// Every number here is the fit's own: arrival is utils/dispersalFigure's
// arrivalAges (the line plus the reported residual), and the error bar is the
// residual's own sd. Nothing is recomputed from coordinates.
import { useMemo, useRef } from 'react'
import { Box, Button, Typography } from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import type { Config, Data, Layout } from 'plotly.js-dist-min'
import Plot from '@/components/Plot'
import { usePlotColors, usePlotFont } from '@/theme/plotColors'
import { calendarAxisTicks, calendarAxisTitle, formatCalendarAge, type DateFormat } from '@/utils/dateFormat'
import { arrivalAges, residualReading } from '@/utils/dispersalFigure'
import { downloadPlotPng } from '@/utils/downloadPlot'
import type { DispersalFit } from '@/types'

interface Props {
  fit: DispersalFit
  dateFormat: DateFormat
  /** The site the reader is pointing at in the table, drawn ringed. */
  highlightId?: string | null
  height?: number
}

export default function DispersalProfilePlot({ fit, dateFormat, highlightId, height = 320 }: Props) {
  const plot = usePlotColors()
  const plotFont = usePlotFont()
  const box = useRef<HTMLDivElement | null>(null)

  const points = useMemo(() => {
    const arrival = arrivalAges(fit)
    return fit.residuals
      .filter((r) => arrival[r.element_id] !== undefined)
      .map((r) => ({
        ...r,
        arrival: arrival[r.element_id],
        reading: residualReading(r.residual_years, r.residual_sd),
      }))
  }, [fit])

  const reach = points.length > 0 ? Math.max(...points.map((p) => p.distance_km)) : 0

  const data = useMemo((): Data[] => {
    const traces: Data[] = []
    const t0 = fit.t0?.median
    const v = fit.speed?.median
    if (t0 != null && v && Number.isFinite(v) && v !== 0) {
      traces.push({
        type: 'scatter',
        mode: 'lines',
        x: [0, reach],
        y: [t0, t0 - reach / v],
        line: { color: plot.posteriorLine, width: 2 },
        hoverinfo: 'skip',
        showlegend: false,
      })
    }
    // One trace per reading, in the residual pair's colours and the ink for
    // "fits", so the three states are separately hoverable and the colour
    // means exactly what the table's chip says.
    const colourOf = { early: plot.residualEarly, late: plot.residualLate, fits: plot.mean }
    for (const reading of ['fits', 'late', 'early'] as const) {
      const group = points.filter((p) => p.reading === reading)
      if (group.length === 0) continue
      traces.push({
        type: 'scatter',
        mode: 'markers',
        x: group.map((p) => p.distance_km),
        y: group.map((p) => p.arrival),
        error_y: {
          type: 'data',
          array: group.map((p) => p.residual_sd),
          color: colourOf[reading],
          thickness: 1,
          width: 0,
        },
        marker: { size: 7, color: colourOf[reading], line: { color: plot.surface, width: 1 } },
        text: group.map(
          (p) =>
            `${p.name}<br>${Math.round(p.distance_km).toLocaleString()} km from the origin` +
            `<br>arrival ${formatCalendarAge(p.arrival, dateFormat)}` +
            `<br>residual ${p.residual_years > 0 ? '+' : ''}${Math.round(p.residual_years)} ± ${Math.round(p.residual_sd)} yr`
        ),
        hovertemplate: '%{text}<extra></extra>',
        showlegend: false,
      })
    }
    const highlighted = points.find((p) => p.element_id === highlightId)
    if (highlighted) {
      traces.push({
        type: 'scatter',
        mode: 'markers',
        x: [highlighted.distance_km],
        y: [highlighted.arrival],
        marker: { size: 16, symbol: 'circle-open', color: plot.posteriorLine, line: { width: 2 } },
        hoverinfo: 'skip',
        showlegend: false,
      })
    }
    return traces
  }, [fit, points, reach, plot, dateFormat, highlightId])

  const yRange = useMemo(() => {
    const ys = points.flatMap((p) => [p.arrival - p.residual_sd, p.arrival + p.residual_sd])
    if (fit.t0) ys.push(fit.t0.median)
    const lo = Math.min(...ys)
    const hi = Math.max(...ys)
    const pad = Math.max(10, (hi - lo) * 0.05)
    return { min: lo - pad, max: hi + pad }
  }, [points, fit.t0])

  const ticks = useMemo(
    () => calendarAxisTicks(yRange.min, yRange.max, dateFormat, 6),
    [yRange, dateFormat]
  )

  const layout = useMemo(
    (): Partial<Layout> => ({
      dragmode: false as const,
      height,
      font: plotFont,
      plot_bgcolor: plot.surface,
      paper_bgcolor: plot.surface,
      margin: { l: 72, r: 12, t: 12, b: 48 },
      hovermode: 'closest' as const,
      showlegend: false,
      xaxis: {
        title: { text: 'Distance from the origin (km)', standoff: 8 },
        range: [0, reach * 1.04 || 1],
        autorange: false as const,
        zeroline: false,
        gridcolor: plot.grid,
      },
      yaxis: {
        // Oldest at the top: the origin's arrival sits top-left and the
        // front runs down and out from it.
        title: { text: calendarAxisTitle(dateFormat) },
        range: [yRange.min, yRange.max],
        autorange: false as const,
        zeroline: false,
        gridcolor: plot.grid,
        ...(ticks ? { tickmode: 'array' as const, tickvals: ticks.tickvals, ticktext: ticks.ticktext } : {}),
      },
    }),
    [height, plotFont, plot, reach, yRange, dateFormat, ticks]
  )

  const config = useMemo(
    (): Partial<Config> => ({ responsive: true, displayModeBar: false as const, displaylogo: false }),
    []
  )

  if (points.length === 0) return null

  return (
    <Box>
      <Box
        ref={box}
        role="img"
        aria-label={`Arrival age against distance from the origin for ${points.length} sites, with the fitted front`}
      >
        <Plot
          data={data}
          layout={layout}
          config={config}
          useResizeHandler
          style={{ width: '100%', height: `${height}px` }}
        />
      </Box>
      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1, mt: 0.5 }}>
        <Typography variant="caption" color="text.secondary" sx={{ flex: 1, maxWidth: '72ch' }}>
          Line: fitted front. Orange: arrived early. Blue: arrived late. Dark: within 2 sd.
        </Typography>
        <Button
          size="small"
          startIcon={<DownloadIcon />}
          aria-label="Download the arrival against distance figure as PNG"
          onClick={() => downloadPlotPng(box.current, 'arrival-by-distance')}
          sx={{ flexShrink: 0 }}
        >
          Download PNG
        </Button>
      </Box>
    </Box>
  )
}
