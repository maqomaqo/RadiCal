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
  FormControlLabel,
  Link,
  MenuItem,
  Paper,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import Plotly from 'plotly.js-dist-min'
import createPlotlyComponent from 'react-plotly.js/factory'
import CurveScrubber from '@/components/CurveScrubber'
import {
  fetchCurvePoints,
  fetchResolution,
  resolutionCsvUrl,
  type CurvePoint,
  type CurveSpec,
  type ResolutionPoint,
} from '@/api/curves'
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
import { resolutionStride } from '@/utils/resolutionStride'

const Plot = createPlotlyComponent(Plotly)

/**
 * Laboratory uncertainties to offer, as the 1σ on a modern sample -- the
 * method scales it with age from there.
 *
 * 15 is what res14c uses and roughly the best a good AMS laboratory offers,
 * which is what makes its answer the *best possible* resolution. It is kept
 * as the default for comparability with the published figures, with the
 * ±25–30 most commercial dates actually carry one click away.
 */
const LAB_UNCERTAINTIES = [
  { value: 15, label: '±15 yr (best AMS)' },
  { value: 25, label: '±25 yr (typical)' },
  { value: 30, label: '±30 yr' },
  { value: 50, label: '±50 yr' },
]

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
  // The resolution profile, on the same calendar axis as the curve above it.
  // Optional because it is a second request per window, and because a curve
  // is worth looking at without it.
  const [showResolution, setShowResolution] = useState(true)
  const [resolution, setResolution] = useState<ResolutionPoint[]>([])
  const [u0, setU0] = useState(15)
  const [resolutionLoading, setResolutionLoading] = useState(false)

  /**
   * How wide the plot is, for deciding how many points to ask for.
   *
   * Floored, because this is read at fetch time and a layout that is
   * momentarily collapsed -- a hidden tab, a panel mid-resize -- reads back a
   * few dozen pixels. That silently turns a 450-year window into 33 points: a
   * staircase where the curve's sharpest features are, drawn from a request
   * that looked perfectly successful.
   */
  const plotWidth = () => Math.max(plotBoxRef.current?.clientWidth ?? 900, 600)

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
    const width = plotWidth()
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

  // Fetched per window like the curve itself. The calculation is exact for a
  // window rather than an approximation of the whole-curve answer -- it pads
  // far enough that the two are identical -- so there is no reason to compute
  // 55,000 years to draw 400 of them.
  useEffect(() => {
    if (!window || !showResolution) return
    let active = true
    setResolutionLoading(true)
    const width = plotWidth()
    const timer = setTimeout(() => {
      fetchResolution(curveId, {
        calFrom: window.from,
        calTo: window.to,
        u0,
        stride: resolutionStride(window.to - window.from, width),
      })
        .then((data) => {
          if (active) setResolution(data.points)
        })
        .catch(() => {
          if (active) setResolution([])
        })
        .finally(() => {
          if (active) setResolutionLoading(false)
        })
    }, 200)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [curveId, window, u0, showResolution])

  const hovered = useMemo(
    () => (hover === null ? null : sampleAt(points, hover)),
    [hover, points]
  )

  const hoveredResolution = useMemo(() => {
    // Gated on the panel being shown, not just on having numbers: the fetch
    // is skipped while it is off, so whatever is still in `resolution` was
    // computed for whichever window was on screen when it was last on.
    if (!showResolution || hover === null || resolution.length === 0) return null
    let nearest = resolution[0]
    let best = Infinity
    for (const point of resolution) {
      const distance = Math.abs(point[0] - hover)
      if (distance < best) {
        best = distance
        nearest = point
      }
    }
    return nearest
  }, [hover, resolution, showResolution])

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

  /**
   * How tall the curve chart is, giving up exactly what the resolution panel
   * takes so the readout below never moves.
   *
   * Passed to Plotly's LAYOUT as well as the container's style. Setting only
   * the style grows the box while Plotly keeps drawing at its old computed
   * height -- the chart stays 270px inside a 380px container, and turning the
   * resolution panel off leaves a blank 110px strip where the curve should
   * have expanded. `useResizeHandler` does not catch it either: it listens
   * for WINDOW resizes, and nothing about the window changed.
   */
  const chartHeight = showResolution ? 270 : 380

  const layout = useMemo(() => {
    const ticks = window ? calendarAxisTicks(window.from, window.to, dateFormat) : null
    return {
      height: chartHeight,
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
  }, [spec, curveId, dateFormat, colors, window, chartHeight])

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
          style={{ width: '100%', height: `${chartHeight}px` }}
          onHover={(event: any) => setHover(event?.points?.[0]?.x ?? null)}
          onUnhover={() => setHover(null)}
        />

        {showResolution && (
          <Box sx={{ mt: 0.5 }}>
            <Plot
              data={
                [
                  {
                    // The 5th-95th band: how lucky or unlucky one date can be.
                    x: [...resolution.map((p) => p[0]), ...resolution.map((p) => p[0]).reverse()],
                    y: [
                      ...resolution.map((p) => p[3]),
                      ...resolution.map((p) => p[2]).reverse(),
                    ],
                    fill: 'toself',
                    fillcolor: colors.hpd95,
                    line: { width: 0, color: 'rgba(0,0,0,0)' },
                    mode: 'lines',
                    name: '5th–95th percentile',
                    hoverinfo: 'skip',
                  },
                  {
                    x: resolution.map((p) => p[0]),
                    y: resolution.map((p) => p[1]),
                    customdata: resolution.map((p) => formatCalendarAge(p[0], dateFormat)),
                    mode: 'lines',
                    line: { color: colors.hpd68, width: 1.5 },
                    name: 'Mean',
                    hovertemplate:
                      '<b>%{customdata}</b><br>about %{y:.0f} years (95.4%)<extra></extra>',
                  },
                ] as any
              }
              layout={
                {
                  xaxis: {
                    // Locked to the curve above rather than autoranged: the
                    // two are read together, and an axis that drifted by a few
                    // years would line a plateau up against the wrong stretch
                    // of curve.
                    range: [window.to, window.from],
                    matches: 'x',
                    showticklabels: false,
                    gridcolor: colors.grid,
                  },
                  yaxis: {
                    title: { text: 'Resolution (yr)', font: { size: 11 } },
                    gridcolor: colors.grid,
                    rangemode: 'tozero' as const,
                  },
                  showlegend: false,
                  hovermode: 'x unified' as const,
                  plot_bgcolor: colors.surface,
                  paper_bgcolor: colors.surface,
                  margin: { l: 70, r: 20, t: 4, b: 4 },
                  autosize: true,
                } as any
              }
              config={{ responsive: true, displayModeBar: false }}
              useResizeHandler
              style={{ width: '100%', height: '110px' }}
              onHover={(event: any) => setHover(event?.points?.[0]?.x ?? null)}
              onUnhover={() => setHover(null)}
            />
          </Box>
        )}

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
              ...(showResolution
                ? [[
                    'Expected resolution',
                    hoveredResolution
                      ? `${hoveredResolution[1].toFixed(0)} yr ` +
                        `(${hoveredResolution[2].toFixed(0)}–${hoveredResolution[3].toFixed(0)})`
                      : '—',
                  ] as [string, string]]
                : []),
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
              ? `${slope.detail}.` +
                (hoveredResolution
                  ? ` A ±${u0.toFixed(0)} yr measurement of a sample from this year` +
                    ` calibrates to about ${hoveredResolution[1].toFixed(0)} years at 95.4%.`
                  : '')
              : 'Point at the curve to read what it says there, how steeply it is running,' +
                ' and how tightly a date from that year could be known.'}
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
          <Box sx={{ flexGrow: 1 }} />
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={showResolution}
                onChange={(e) => setShowResolution(e.target.checked)}
              />
            }
            label={
              <Typography variant="body2">
                Expected resolution{resolutionLoading ? ' …' : ''}
              </Typography>
            }
          />
          {showResolution && (
            <>
              <TextField
                select
                size="small"
                label="Lab ±1σ"
                value={u0}
                onChange={(e) => setU0(Number(e.target.value))}
                sx={{ minWidth: 130 }}
              >
                {LAB_UNCERTAINTIES.map((option) => (
                  <MenuItem key={option.value} value={option.value}>
                    {option.label}
                  </MenuItem>
                ))}
              </TextField>
              <Button size="small" component="a" href={resolutionCsvUrl(curveId, u0)}>
                Export CSV
              </Button>
            </>
          )}
        </Stack>
      </Paper>

    </Container>
  )
}
