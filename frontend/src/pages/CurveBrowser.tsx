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
  Tooltip,
  Typography,
} from '@mui/material'
import Plotly from 'plotly.js-dist-min'
import createPlotlyComponent from 'react-plotly.js/factory'
import CurveScrubber from '@/components/CurveScrubber'
import {
  compareCurves,
  curveWindowCsvUrl,
  fetchCurvePoints,
  fetchResolution,
  resolutionCsvUrl,
  type ComparePoint,
  type CurvePoint,
  type CurveSpec,
  type ResolutionPoint,
} from '@/api/curves'
import SimulateDateDialog from '@/components/SimulateDateDialog'
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
import { interpolateAt } from '@/utils/seriesLookup'
import { compactGrid } from '@/utils/curveGrid'

const Plot = createPlotlyComponent(Plotly)

/**
 * Laboratory uncertainties to offer, as the 1σ on a modern sample -- the
 * method scales it with age from there.
 *
 * 25 by default, which is what a commercial date typically carries. 15 is
 * what res14c uses and roughly the best a good AMS laboratory offers -- the
 * right figure for reproducing the published curves, and the wrong one for
 * answering "can I date this period well enough", since it quotes a precision
 * most samples will not get.
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

/**
 * A panel under the curve, framed and titled.
 *
 * The frame is doing real work rather than decoration: these panels share the
 * curve's calendar axis and sit directly beneath it, so without one they read
 * as more of the same chart. The strip along the top says which question this
 * panel answers, and holds whatever settings belong to that question -- which
 * is where the resolution panel's laboratory error and CSV export live, next
 * to the thing they change, instead of in a row of eleven controls at the
 * bottom of the page where nothing said what they applied to.
 */
function PanelSection({
  title,
  caption,
  controls,
  children,
}: {
  title: string
  caption?: React.ReactNode
  controls?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <Box
      sx={{
        mt: 1.5,
        border: 1,
        borderColor: 'divider',
        borderRadius: 1,
        overflow: 'hidden',
      }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 1.5,
          flexWrap: 'wrap',
          px: 1.5,
          py: 0.5,
          backgroundColor: 'action.hover',
        }}
      >
        <Typography variant="caption" sx={{ fontWeight: 600 }}>
          {title}
        </Typography>
        {caption && (
          <Typography variant="caption" color="textSecondary">
            {caption}
          </Typography>
        )}
        <Box sx={{ flexGrow: 1 }} />
        {controls}
      </Box>
      {children}
    </Box>
  )
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
  //
  // Off to begin with. The page opens on the curve alone -- one chart, full
  // height, which is what someone arriving here came to see; the second panel
  // is an answer to a question they have not asked yet. It also means the
  // first view costs one request rather than two.
  const [showResolution, setShowResolution] = useState(false)
  const [resolution, setResolution] = useState<ResolutionPoint[]>([])
  const [u0, setU0] = useState(25)
  const [resolutionLoading, setResolutionLoading] = useState(false)
  // Comparison against a second curve, and the difference between them --
  // which is the part worth looking at, since two curves drawn over each
  // other are indistinguishable at any useful zoom.
  const [compareWith, setCompareWith] = useState('')
  const [comparison, setComparison] = useState<ComparePoint[]>([])
  // A hypothetical date from one calendar year, opened by clicking the curve.
  const [simulateAt, setSimulateAt] = useState<number | null>(null)

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

  useEffect(() => {
    if (!window || !compareWith) {
      setComparison([])
      return
    }
    let active = true
    const width = plotWidth()
    const timer = setTimeout(() => {
      compareCurves(curveId, compareWith, {
        calFrom: window.from,
        calTo: window.to,
        maxPoints: pointsForWidth(width),
      })
        .then((data) => {
          if (active) setComparison(data.points)
        })
        .catch(() => {
          if (active) setComparison([])
        })
    }, 150)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [curveId, compareWith, window])

  const hovered = useMemo(
    () => (hover === null ? null : sampleAt(points, hover)),
    [hover, points]
  )

  /**
   * The resolution figures at the hovered year, read off the drawn line.
   *
   * Interpolated rather than snapped to the nearest computed year, so the
   * readout says what the panel above it shows. The profile is sampled at
   * about one point per pixel; at a wide zoom the nearest sample can be
   * decades away, and across a plateau — where resolution swings by 300 years
   * in 150 — that made the figure differ from its own chart by a fifth.
   *
   * Gated on the panel being shown, not just on having numbers: the fetch is
   * skipped while it is off, so whatever is still in `resolution` was computed
   * for whichever window was on screen when it was last on.
   */
  const hoveredResolution = useMemo(() => {
    if (!showResolution || hover === null || resolution.length === 0) return null
    const mean = interpolateAt(resolution, hover, 1)
    const low = interpolateAt(resolution, hover, 2)
    const high = interpolateAt(resolution, hover, 3)
    if (mean === null || low === null || high === null) return null
    return { mean, low, high }
  }, [hover, resolution, showResolution])

  const comparedSpec = curves.find((curve) => curve.id === compareWith)

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

    const traces: any[] = [
      band(2, colors.hpd95, '±2σ'),
      band(1, colors.hpd68, '±1σ'),
      {
        x: cal,
        y: c14,
        // Just enough to say where the pointer is. The curve's error and its
        // local gradient are both in the readout under the chart, and a
        // floating box that repeats them covers the curve to tell you what is
        // already on screen. Pre-formatted because %{x} is always the cal BP
        // the point is plotted at, where the reader may be in BC/AD.
        customdata: labels,
        mode: 'lines',
        line: { color: colors.posteriorLine, width: 1.5 },
        name: spec?.name ?? curveId,
        // Named only when there are two lines to tell apart. On its own the
        // name is the chart's title repeated into a box that covers the
        // curve; with a comparison overlaid, two unlabelled numbers are a
        // guess.
        hovertemplate: comparison.length > 0
          ? `<b>%{customdata}</b><br>${spec?.name ?? curveId}: %{y:.0f} ¹⁴C BP<extra></extra>`
          : '<b>%{customdata}</b><br>%{y:.0f} ¹⁴C BP<extra></extra>',
      },
    ]

    if (comparison.length > 0) {
      traces.push({
        x: comparison.map((p) => p[0]),
        y: comparison.map((p) => p[3]),
        mode: 'lines',
        line: { color: colors.priorLine, width: 1.5, dash: 'dot' },
        name: comparedSpec?.name ?? compareWith,
        hovertemplate:
          `${comparedSpec?.name ?? compareWith}: %{y:.0f} ¹⁴C BP<extra></extra>`,
      })
    }

    return traces
  }, [points, colors, dateFormat, spec, curveId, comparison, compareWith, comparedSpec])

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
  /**
   * Heights for the stack of panels under the chart.
   *
   * The curve gives up what the panels take, so the readout below stays where
   * it is and does not fall below the fold. But it cannot give up everything:
   * at two panels a straight 110 each left the curve 160px tall, which is too
   * short to read the thing the page is actually about. So the panels shrink
   * a little when there are two of them, and the chart will not go below 200.
   */
  const panelCount = (showResolution ? 1 : 0) + (comparison.length > 0 ? 1 : 0)
  const panelHeight = panelCount > 1 ? 75 : 85
  /**
   * What a framed section costs beyond its chart: the border, the title strip
   * with its controls, and the margin above it. Measured, not guessed -- a
   * first estimate of 46 left the readout moving 45px when a panel opened,
   * which is the whole thing the compensation exists to prevent.
   */
  const SECTION_CHROME = 91
  /**
   * With one panel this comes out at 380 exactly, so the readout does not
   * move. With two it cannot: two framed sections are 330px between them, and
   * the only way to absorb that is a chart too short to read. The chart floors
   * at 200 and the page grows instead -- which is a fair trade for having
   * asked to see two extra charts at once.
   */
  const chartHeight = Math.max(380 - panelCount * (panelHeight + SECTION_CHROME), 200)

  const layout = useMemo(() => {
    const ticks = window ? calendarAxisTicks(window.from, window.to, dateFormat) : null
    return {
      height: chartHeight,
      title: { text: spec ? `${spec.name}` : curveId, font: { size: 16 } },
      xaxis: {
        // Dropped when panels sit below, where it would be stranded halfway
        // down a stack of charts sharing one axis; a caption under the last
        // panel carries it instead.
        ...(panelCount > 0 ? {} : { title: { text: calendarAxisTitle(dateFormat) } }),
        // The crosshair that 'x unified' used to draw, kept without it. See
        // hovermode below.
        showspikes: true,
        spikemode: 'across' as const,
        spikethickness: 1,
        spikedash: 'dot',
        spikecolor: colors.grid,
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
      /**
       * 'closest', not 'x unified'.
       *
       * Unified draws a header line above the box holding the raw x value,
       * and the x value here is always cal BP because that is what the curve
       * is plotted in -- so in cal BP it printed the date twice, and in BC/AD
       * it printed a number in a convention the reader had explicitly turned
       * off. It cannot be reformatted: no axis format turns 2502 into 553 BC.
       * So it goes, and the spike lines above keep the crosshair that made
       * unified worth having.
       */
      hovermode: 'closest' as const,
      /**
       * No distance cutoff, so pointing anywhere in the plot finds the
       * nearest point on the curve rather than requiring the pointer to track
       * the line. That is what the readout underneath needs -- it is driven by
       * this hover, and with the default cutoff it blanked whenever the
       * pointer drifted thirty pixels off the curve.
       *
       * The alternatives both reintroduce the raw cal BP number this replaced:
       * 'x unified' prints it as a header above the box, and 'x' prints it in
       * a label on the axis. Neither can be reformatted, because the value
       * plotted IS cal BP -- no axis format turns 2502 into 553 BC.
       */
      hoverdistance: -1,
      showlegend: true,
      legend: { orientation: 'h' as const, y: 1.02, yanchor: 'bottom' as const },
      plot_bgcolor: colors.surface,
      paper_bgcolor: colors.surface,
      margin: { l: 70, r: 20, t: 50, b: 60 },
      autosize: true,
    }
  }, [spec, curveId, dateFormat, colors, window, chartHeight, panelCount])

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
              <Tooltip title={spec.resolution}>
                <Chip size="small" label={compactGrid(spec.resolution)} variant="outlined" />
              </Tooltip>
              {spec.reference && <Chip size="small" label={spec.reference} variant="outlined" />}
            </>
          )}
          <Box sx={{ flexGrow: 1 }} />
          {/* What is on screen, kept together and kept away from the
              navigation controls under the chart -- "which curves am I
              looking at" and "where am I looking" are different questions,
              and eleven controls in one row answered neither. */}
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
          <TextField
            select
            size="small"
            label="Compare with"
            value={compareWith}
            onChange={(e) => setCompareWith(e.target.value)}
            sx={{ minWidth: 160 }}
          >
            <MenuItem value="">
              <em>No comparison</em>
            </MenuItem>
            {curves
              .filter((curve) => curve.id !== curveId)
              .map((curve) => (
                <MenuItem key={curve.id} value={curve.id}>
                  {curve.name}
                </MenuItem>
              ))}
          </TextField>
          <TextField
            select
            size="small"
            label="Dates as"
            value={dateFormat}
            onChange={(e) => setDateFormat(e.target.value as DateFormat)}
            sx={{ minWidth: 170 }}
          >
            {DATE_FORMAT_OPTIONS.map((option) => (
              <MenuItem key={option.value} value={option.value}>
                {option.label}
              </MenuItem>
            ))}
          </TextField>
        </Stack>
      </Paper>

      <SimulateDateDialog
        open={simulateAt !== null}
        onClose={() => setSimulateAt(null)}
        curveId={curveId}
        curveName={spec?.name ?? curveId}
        calAge={simulateAt}
        dateFormat={dateFormat}
      />

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
          // Clicking a year asks what a date from it would actually look
          // like. In a plateau the answer is three or four disjoint ranges,
          // which says what a plateau costs far more plainly than any single
          // number for it can.
          onClick={(event: any) => {
            const clicked = event?.points?.[0]?.x
            if (typeof clicked === 'number') setSimulateAt(clicked)
          }}
        />

        {comparison.length > 0 && (
          <PanelSection
            title={`${spec?.name ?? curveId} − ${comparedSpec?.name ?? compareWith}`}
            caption={`how far apart the two curves are, with both their errors — above zero, ${
              spec?.name ?? curveId
            } reads older`}
          >
            <Plot
              data={
                [
                  {
                    x: [
                      ...comparison.map((p) => p[0]),
                      ...comparison.map((p) => p[0]).reverse(),
                    ],
                    y: [
                      ...comparison.map((p) => p[5] + p[6]),
                      ...comparison.map((p) => p[5] - p[6]).reverse(),
                    ],
                    fill: 'toself',
                    fillcolor: colors.hpd95,
                    line: { width: 0, color: 'rgba(0,0,0,0)' },
                    mode: 'lines',
                    hoverinfo: 'skip',
                  },
                  {
                    x: comparison.map((p) => p[0]),
                    y: comparison.map((p) => p[5]),
                    customdata: comparison.map((p) => formatCalendarAge(p[0], dateFormat)),
                    mode: 'lines',
                    line: { color: colors.posteriorLine, width: 1.5 },
                    hovertemplate:
                      '<b>%{customdata}</b><br>%{y:.0f} ¹⁴C yr apart<extra></extra>',
                  },
                ] as any
              }
              layout={
                {
                  height: panelHeight,
                  xaxis: {
                    range: [window.to, window.from],
                    showticklabels: false,
                    gridcolor: colors.grid,
                    showspikes: true,
                    spikemode: 'across' as const,
                    spikethickness: 1,
                    spikedash: 'dot',
                    spikecolor: colors.grid,
                  },
                  yaxis: {
                    // Units only. A rotated title is limited by the panel's
                    // HEIGHT, and these panels are 75-85px -- but more to the
                    // point, the section's own header already says what is
                    // being plotted, so repeating it sideways in the space
                    // where it does not fit says nothing twice.
                    title: { text: 'yr', font: { size: 11 } },
                    gridcolor: colors.grid,
                    zeroline: true,
                    zerolinecolor: colors.grid,
                  },
                  showlegend: false,
                  hovermode: 'closest' as const,
                  hoverdistance: -1,
                  plot_bgcolor: colors.surface,
                  paper_bgcolor: colors.surface,
                  margin: { l: 52, r: 20, t: 4, b: 4 },
                  autosize: true,
                } as any
              }
              config={{ responsive: true, displayModeBar: false }}
              useResizeHandler
              style={{ width: '100%', height: `${panelHeight}px` }}
              onHover={(event: any) => setHover(event?.points?.[0]?.x ?? null)}
              onUnhover={() => setHover(null)}
            />
          </PanelSection>
        )}

        {showResolution && (
          <PanelSection
            title="Expected resolution"
            caption="how tightly a date from each year could be known, before measuring"
            controls={
              <>
                <TextField
                  select
                  size="small"
                  variant="standard"
                  label="Lab ±1σ"
                  value={u0}
                  onChange={(e) => setU0(Number(e.target.value))}
                  sx={{ minWidth: 120 }}
                >
                  {LAB_UNCERTAINTIES.map((option) => (
                    <MenuItem key={option.value} value={option.value}>
                      {option.label}
                    </MenuItem>
                  ))}
                </TextField>
                <Button size="small" component="a" href={resolutionCsvUrl(curveId, u0)}>
                  Export profile
                </Button>
              </>
            }
          >
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
                  height: panelHeight,
                  xaxis: {
                    // Locked to the curve above rather than autoranged: the
                    // two are read together, and an axis that drifted by a few
                    // years would line a plateau up against the wrong stretch
                    // of curve.
                    range: [window.to, window.from],
                    matches: 'x',
                    showticklabels: false,
                    gridcolor: colors.grid,
                    showspikes: true,
                    spikemode: 'across' as const,
                    spikethickness: 1,
                    spikedash: 'dot',
                    spikecolor: colors.grid,
                  },
                  yaxis: {
                    title: { text: 'yr', font: { size: 11 } },
                    gridcolor: colors.grid,
                    rangemode: 'tozero' as const,
                  },
                  showlegend: false,
                  hovermode: 'closest' as const,
                  hoverdistance: -1,
                  plot_bgcolor: colors.surface,
                  paper_bgcolor: colors.surface,
                  margin: { l: 52, r: 20, t: 4, b: 4 },
                  autosize: true,
                } as any
              }
              config={{ responsive: true, displayModeBar: false }}
              useResizeHandler
              style={{ width: '100%', height: `${panelHeight}px` }}
              onHover={(event: any) => setHover(event?.points?.[0]?.x ?? null)}
              onUnhover={() => setHover(null)}
            />
          </PanelSection>
        )}

        {panelCount > 0 && (
          <Typography
            variant="caption"
            color="textSecondary"
            sx={{ display: 'block', textAlign: 'center', mt: 0.5 }}
          >
            {calendarAxisTitle(dateFormat)}
          </Typography>
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
                      ? `${hoveredResolution.mean.toFixed(0)} yr ` +
                        `(${hoveredResolution.low.toFixed(0)}–` +
                        `${hoveredResolution.high.toFixed(0)})`
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
                    ` calibrates to about ${hoveredResolution.mean.toFixed(0)} years at 95.4%.`
                  : '')
              : 'Point at the curve to read what it says there and how steeply it is' +
                (showResolution
                  ? ' running, and how tightly a date from that year could be known.'
                  : ' running. Turn on Expected resolution below for how tightly a' +
                    ' date from that year could be known.')}
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
          <Button
            size="small"
            component="a"
            href={curveWindowCsvUrl(curveId, window.from, window.to)}
          >
            Export this window
          </Button>
        </Stack>
      </Paper>

    </Container>
  )
}
