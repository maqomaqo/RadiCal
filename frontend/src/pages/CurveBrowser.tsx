import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link as RouterLink, useParams } from 'react-router-dom'
import {
  Alert,
  Box,
  Breadcrumbs,
  Button,
  Chip,
  CircularProgress,
  Container,
  Link,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import Plotly from 'plotly.js-dist-min'
import createPlotlyComponent from 'react-plotly.js/factory'
import CurveScrubber from '@/components/CurveScrubber'
import { fetchCurvePoints, type CurvePoint, type CurveSpec } from '@/api/curves'
import { useCurves } from '@/hooks/useCurves'
import { usePlotColors } from '@/theme/plotColors'
import {
  DATE_FORMAT_OPTIONS,
  DEFAULT_DATE_FORMAT,
  calendarAxisTicks,
  calendarAxisTitle,
  formatCalendarAge,
  type DateFormat,
} from '@/utils/dateFormat'
import { PNG_EXPORT_SCALE, exportFilename } from '@/utils/plotExport'
import {
  clampWindow,
  pointsForWidth,
  zoomAbout,
  type Extent,
  type Window,
} from '@/utils/curveScrubber'
import { describeSlope } from '@/utils/curveShape'

const Plot = createPlotlyComponent(Plotly)

/**
 * Look at a calibration curve.
 *
 * The three things this page is for, and why each is here:
 *
 * The curve's SHAPE, with its own error bounds. Every calibrated date this
 * app produces is this curve read backwards, so its wiggles are not detail --
 * they are the reason a date splits into two ranges, or spreads over three
 * centuries, and they are invisible in the calibrated output.
 *
 * WHERE you are in 55,000 years. The strip under the plot shows the whole
 * curve with the current view marked; drag it to move. Panning inside a chart
 * alone never answers that question.
 *
 * What the curve is DOING at a given moment. The readout reports the local
 * gradient, which is what governs how tightly anything can be dated there: on
 * a slope of about 1 the curve tracks calendar time and a date calibrates
 * roughly as tightly as it was measured; near 0 it is flat and one
 * measurement covers centuries; below 0 the curve runs backwards and a single
 * measurement necessarily splits into disjoint ranges.
 */

/** Linear interpolation of a loaded series at an arbitrary calendar age. */
function sampleAt(points: CurvePoint[], cal: number): CurvePoint | null {
  if (points.length === 0) return null
  // Points come oldest-first or youngest-first depending on nothing in
  // particular, so search rather than assume.
  let nearest = points[0]
  let best = Infinity
  for (const point of points) {
    const distance = Math.abs(point[0] - cal)
    if (distance < best) {
      best = distance
      nearest = point
    }
  }
  return nearest
}

export default function CurveBrowser() {
  const { curveId = '' } = useParams<{ curveId: string }>()
  const { curves } = useCurves()
  const colors = usePlotColors()
  const plotBoxRef = useRef<HTMLDivElement>(null)

  const [spec, setSpec] = useState<CurveSpec | null>(null)
  const [extent, setExtent] = useState<Extent | null>(null)
  const [overview, setOverview] = useState<CurvePoint[]>([])
  const [points, setPoints] = useState<CurvePoint[]>([])
  const [window, setWindow] = useState<Window | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dateFormat, setDateFormat] = useState<DateFormat>(DEFAULT_DATE_FORMAT)
  const [hover, setHover] = useState<number | null>(null)
  // Set when a windowed refetch fails. The chart keeps showing what it last
  // loaded rather than blanking mid-drag, which means the plot and the
  // scrubber can disagree about where you are -- so it has to say so.
  const [stale, setStale] = useState(false)

  useEffect(() => {
    setSpec(curves.find((c) => c.id === curveId) ?? null)
  }, [curves, curveId])

  // The overview strip, fetched once: the whole curve, coarsely. Separate
  // from the windowed fetch below because it never changes as you move
  // around, and refetching it on every pan would be the largest cost on the
  // page for the one thing that is always the same.
  useEffect(() => {
    let active = true
    setLoading(true)
    fetchCurvePoints(curveId, { maxPoints: 400 })
      .then((data) => {
        if (!active) return
        setOverview(data.points)
        const bounds = { min: data.curve_cal_min, max: data.curve_cal_max }
        setExtent(bounds)
        // Opening on the whole curve says what there is; the Holocene, where
        // most archaeology sits, is one drag away and a preset click away.
        setWindow({ from: bounds.min, to: bounds.max })
        setError(null)
      })
      .catch((err) => {
        if (active) setError((err as Error).message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [curveId])

  // The window, refetched as it moves, at the resolution the plot is actually
  // being drawn at -- so zooming in reveals real detail rather than
  // stretching what was already loaded.
  useEffect(() => {
    if (!window) return
    let active = true
    const width = plotBoxRef.current?.clientWidth ?? 900
    const timer = setTimeout(() => {
      fetchCurvePoints(curveId, {
        calFrom: window.from,
        calTo: window.to,
        maxPoints: pointsForWidth(width),
      })
        .then((data) => {
          if (!active) return
          setPoints(data.points)
          setStale(false)
        })
        .catch(() => {
          if (active) setStale(true)
        })
    }, 120) // debounced: a drag emits a window per pointer move
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [curveId, window])

  const hovered = useMemo(
    () => (hover === null ? null : sampleAt(points, hover)),
    [hover, points]
  )

  const plotData = useMemo(() => {
    if (points.length === 0) return []
    const cal = points.map((p) => p[0])
    const c14 = points.map((p) => p[1])
    const sigma = points.map((p) => p[2])
    const labels = cal.map((c) => formatCalendarAge(c, dateFormat))

    const band = (multiple: number, color: string, name: string) => ({
      x: [...cal, ...cal.slice().reverse()],
      y: [
        ...c14.map((v, i) => v + multiple * sigma[i]),
        ...c14.map((v, i) => v - multiple * sigma[i]).reverse(),
      ],
      fill: 'toself',
      fillcolor: color,
      line: { width: 0, color: 'rgba(0,0,0,0)' },
      mode: 'lines',
      name,
      hoverinfo: 'skip',
      showlegend: true,
    })

    return [
      band(2, colors.hpd95, '±2σ'),
      band(1, colors.hpd68, '±1σ'),
      {
        x: cal,
        y: c14,
        customdata: points.map((p, i) => [labels[i], p[2], p[3]]),
        mode: 'lines',
        line: { color: colors.posteriorLine, width: 1.5 },
        name: spec?.name ?? curveId,
        hovertemplate:
          '<b>%{customdata[0]}</b><br>' +
          '¹⁴C age: %{y:.0f} ± %{customdata[1]:.0f} BP<br>' +
          'slope: %{customdata[2]:.2f} ¹⁴C yr / cal yr<extra></extra>',
      },
    ]
  }, [points, colors, dateFormat, spec, curveId])

  const layout = useMemo(() => {
    const ticks = window ? calendarAxisTicks(window.from, window.to, dateFormat) : null
    return {
      title: { text: spec ? `${spec.name}` : curveId, font: { size: 16 } },
      xaxis: {
        title: { text: calendarAxisTitle(dateFormat) },
        // Older to the left, as in every other plot in this app, so time
        // reads left to right.
        autorange: 'reversed' as const,
        gridcolor: colors.grid,
        ...(ticks ? { tickvals: ticks.tickvals, ticktext: ticks.ticktext } : {}),
      },
      yaxis: {
        title: { text: 'Radiocarbon Age (BP)' },
        gridcolor: colors.grid,
      },
      hovermode: 'x unified' as const,
      showlegend: true,
      legend: { orientation: 'h' as const, y: 1.02, yanchor: 'bottom' as const },
      plot_bgcolor: colors.surface,
      paper_bgcolor: colors.surface,
      margin: { l: 70, r: 20, t: 50, b: 60 },
      autosize: true,
    }
  }, [spec, curveId, dateFormat, colors, window])

  const config = useMemo(
    () => ({
      responsive: true,
      displaylogo: false,
      displayModeBar: true,
      modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'],
      toImageButtonOptions: {
        format: 'png' as const,
        scale: PNG_EXPORT_SCALE,
        filename: exportFilename(`curve-${curveId}`),
      },
    }),
    [curveId]
  )

  const setPreset = useCallback(
    (from: number, to: number) => {
      if (!extent) return
      setWindow(clampWindow({ from, to }, extent))
    },
    [extent]
  )

  const zoom = (factor: number) => {
    if (!window || !extent) return
    const middle = (window.from + window.to) / 2
    setWindow(zoomAbout(window, extent, hover ?? middle, factor))
  }

  if (loading) {
    return (
      <Container maxWidth="lg" sx={{ py: 6, display: 'flex', justifyContent: 'center' }}>
        <CircularProgress />
      </Container>
    )
  }

  if (error || !window || !extent) {
    return (
      <Container maxWidth="lg" sx={{ py: 4 }}>
        <Alert severity="error">{error ?? 'This curve could not be loaded.'}</Alert>
      </Container>
    )
  }

  const slope = hovered ? describeSlope(hovered[3]) : null

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Breadcrumbs sx={{ mb: 2 }}>
        <Link component={RouterLink} to="/curves" underline="hover" color="inherit">
          Calibration Curves
        </Link>
        <Typography color="text.primary">{spec?.name ?? curveId}</Typography>
      </Breadcrumbs>

      <Paper sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
          {spec && (
            <>
              <Chip size="small" label={`${spec.data_points.toLocaleString()} points`} />
              <Chip size="small" label={spec.resolution} variant="outlined" />
              {spec.reference && <Chip size="small" label={spec.reference} variant="outlined" />}
            </>
          )}
          <Box sx={{ flexGrow: 1 }} />
          <TextField
            select
            size="small"
            label="Dates as"
            value={dateFormat}
            onChange={(e) => setDateFormat(e.target.value as DateFormat)}
            sx={{ minWidth: 200 }}
          >
            {DATE_FORMAT_OPTIONS.map((option) => (
              <MenuItem key={option.value} value={option.value}>
                {option.label}
              </MenuItem>
            ))}
          </TextField>
        </Stack>
      </Paper>

      <Paper sx={{ p: 2, mb: 2 }} ref={plotBoxRef}>
        {stale && (
          <Alert severity="warning" sx={{ mb: 1 }}>
            Showing the last view that loaded — the curve could not be re-read for
            the window below, so the two do not currently agree.
          </Alert>
        )}
        <Plot
          data={plotData as any}
          layout={layout as any}
          config={config}
          useResizeHandler
          // 380 rather than a rounder 400: with the readout sitting under the
          // chart, this is what keeps the whole panel -- chart, readout,
          // scrubber -- inside a 720px viewport, so the line you hovered to
          // read is not below the fold.
          style={{ width: '100%', height: '380px' }}
          onHover={(event: any) => setHover(event?.points?.[0]?.x ?? null)}
          onUnhover={() => setHover(null)}
        />

        {/* Directly under the chart, and laid out identically whether or not
            the pointer is on the curve. Both matter: hovering used to grow
            this box by 68px, which moved the page under the pointer that had
            just triggered it, and sat the readout below the fold on a laptop
            so the thing you were hovering to read needed a scroll to see. */}
        <Box
          sx={{
            mt: 1.5,
            px: 1.5,
            py: 1,
            borderRadius: 1,
            backgroundColor: 'action.hover',
          }}
        >
          <Box sx={{ display: 'flex', columnGap: 3, rowGap: 0.5, flexWrap: 'wrap' }}>
            {[
              ['Calendar age', hovered ? formatCalendarAge(hovered[0], dateFormat) : '—'],
              [
                'Curve says',
                hovered ? `${hovered[1].toFixed(0)} ± ${hovered[2].toFixed(0)} ¹⁴C BP` : '—',
              ],
              [
                'Local gradient',
                hovered && slope
                  ? `${hovered[3].toFixed(2)} ¹⁴C yr / cal yr — ${slope.label}`
                  : '—',
              ],
            ].map(([label, value]) => (
              <Box key={label}>
                <Typography variant="caption" color="textSecondary" display="block" lineHeight={1.4}>
                  {label}
                </Typography>
                <Typography variant="body2" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                  {value}
                </Typography>
              </Box>
            ))}
          </Box>
          <Typography variant="caption" color="textSecondary" display="block" sx={{ mt: 0.5 }}>
            {hovered && slope
              ? `${slope.detail}.`
              : 'Point at the curve to read what it says there, and how steeply it is running.'}
          </Typography>
        </Box>

        <Box sx={{ mt: 1.5 }}>
          <CurveScrubber
            overview={overview}
            extent={extent}
            window={window}
            onChange={setWindow}
            dateFormat={dateFormat}
          />
        </Box>

        <Stack direction="row" spacing={1} sx={{ mt: 2 }} flexWrap="wrap" useFlexGap>
          <Button size="small" variant="outlined" onClick={() => zoom(0.5)}>
            Zoom in
          </Button>
          <Button size="small" variant="outlined" onClick={() => zoom(2)}>
            Zoom out
          </Button>
          <Button size="small" onClick={() => setPreset(extent.min, extent.max)}>
            Whole curve
          </Button>
          <Button size="small" onClick={() => setPreset(0, 12000)}>
            Holocene
          </Button>
          <Button size="small" onClick={() => setPreset(2350, 2800)}>
            Hallstatt plateau
          </Button>
        </Stack>
      </Paper>

    </Container>
  )
}
