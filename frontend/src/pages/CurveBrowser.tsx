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
  LinearProgress,
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
import DownloadIcon from '@mui/icons-material/Download'
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
import { usePlotColors, usePlotFont } from '@/theme/plotColors'
import {
  DATE_FORMAT_OPTIONS,
  DEFAULT_DATE_FORMAT,
  calendarAxisTicks,
  calendarAxisTitle,
  formatCalendarAge,
  type DateFormat,
} from '@/utils/dateFormat'
import { downloadPlotPng } from '@/utils/downloadPlot'
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
 * Laboratory uncertainties to offer, as the 1σ on a modern sample; the method
 * scales it with age from there.
 *
 * 25 by default, what a commercial date typically carries. 15 is what res14c
 * uses and roughly the best a good AMS lab offers -- right for reproducing the
 * published curves, wrong for "can I date this period well enough", where it
 * quotes a precision most samples will not get.
 */
const LAB_UNCERTAINTIES = [
  { value: 15, label: '±15 yr (best AMS)' },
  { value: 25, label: '±25 yr (typical)' },
  { value: 30, label: '±30 yr' },
  { value: 50, label: '±50 yr' },
]

/**
 * Look at a calibration curve. Three things:
 *
 * Its SHAPE, with its own error bounds. Every calibrated date this app
 * produces is this curve read backwards, so its wiggles are the reason a date
 * splits into two ranges or spreads over three centuries -- and they are
 * invisible in the calibrated output.
 *
 * WHERE you are in 55,000 years. The strip under the plot shows the whole
 * curve with the current view marked; panning inside a chart never answers
 * that.
 *
 * What the curve is DOING at a given moment. The readout reports the local
 * gradient, which governs how tightly anything can be dated there: near 1 the
 * curve tracks calendar time, near 0 one measurement covers centuries, below 0
 * it runs backwards and a measurement splits into disjoint ranges.
 */

/** Linear interpolation of a loaded series at an arbitrary calendar age. */
function sampleAt(points: CurvePoint[], cal: number): CurvePoint | null {
  if (points.length === 0) return null
  // Points arrive oldest-first or youngest-first, so search rather than assume.
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
 * The frame does real work: these panels share the curve's calendar axis and
 * sit directly beneath it, so without one they read as more of the same chart.
 * The strip along the top says which question the panel answers and holds the
 * settings belonging to it, next to the thing they change.
 */
function PanelSection({
  title,
  caption,
  controls,
  busy = false,
  overlay,
  children,
}: {
  title: string
  caption?: React.ReactNode
  controls?: React.ReactNode
  /**
   * Something is being recomputed for this panel and what is drawn is the
   * previous answer. Said twice over -- a bar across the top of the chart and
   * the chart itself faded -- because the two say different things: the bar
   * says work is happening, the fade says do not read this yet.
   */
  busy?: boolean
  /** Drawn over the chart, undimmed -- for saying why it is empty. */
  overlay?: React.ReactNode
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
        {busy && (
          <Typography variant="caption" color="textSecondary">
            recalculating…
          </Typography>
        )}
        <Box sx={{ flexGrow: 1 }} />
        {controls}
      </Box>
      <Box sx={{ position: 'relative' }}>
        {busy && (
          <LinearProgress
            sx={{ position: 'absolute', top: 0, left: 0, right: 0, height: 2, zIndex: 2 }}
          />
        )}
        <Box sx={{ opacity: busy ? 0.4 : 1, transition: 'opacity 120ms linear' }}>
          {children}
        </Box>
        {overlay && (
          <Box
            sx={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 1,
              pointerEvents: 'none',
            }}
          >
            {overlay}
          </Box>
        )}
      </Box>
    </Box>
  )
}

export default function CurveBrowser() {
  const { curveId = '' } = useParams<{ curveId: string }>()
  const { curves } = useCurves()
  const colors = usePlotColors()
  const plotFont = usePlotFont()
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
  // Set when a windowed refetch fails. The chart keeps its last load rather
  // than blanking mid-drag, so the plot and the scrubber can disagree about
  // where you are -- which has to be said.
  const [stale, setStale] = useState(false)
  // The resolution profile, on the same calendar axis as the curve above it.
  // Off to begin with: the page opens on the curve alone, one chart at full
  // height, and the first view costs one request rather than two.
  const [showResolution, setShowResolution] = useState(false)
  const [resolution, setResolution] = useState<ResolutionPoint[]>([])
  const [u0, setU0] = useState(25)
  const [resolutionLoading, setResolutionLoading] = useState(false)
  // Comparison against a second curve, and the difference between them, which
  // is the part worth looking at: two curves drawn over each other are
  // indistinguishable at any useful zoom.
  const [compareWith, setCompareWith] = useState('')
  const [comparison, setComparison] = useState<ComparePoint[]>([])
  // A hypothetical date from one calendar year, opened by clicking the curve.
  const [simulateAt, setSimulateAt] = useState<number | null>(null)

  /**
   * How wide the plot is, for deciding how many points to ask for.
   *
   * Floored, because this is read at fetch time and a momentarily collapsed
   * layout -- a hidden tab, a panel mid-resize -- reads back a few dozen
   * pixels, turning a 450-year window into 33 points: a staircase where the
   * curve's sharpest features are, from a request that looked successful.
   */
  const plotWidth = () => Math.max(plotBoxRef.current?.clientWidth ?? 900, 600)

  useEffect(() => {
    setSpec(curves.find((c) => c.id === curveId) ?? null)
  }, [curves, curveId])

  // The overview strip: the whole curve, coarsely, fetched once. Separate from
  // the windowed fetch below because it never changes as you move around.
  useEffect(() => {
    let active = true
    setLoading(true)
    fetchCurvePoints(curveId, { maxPoints: 400 })
      .then((data) => {
        if (!active) return
        setOverview(data.points)
        const bounds = { min: data.curve_cal_min, max: data.curve_cal_max }
        setExtent(bounds)
        // Opening on the whole curve says what there is; the Holocene is one
        // drag or one preset click away.
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

  // The window, refetched as it moves at the resolution the plot is drawn at,
  // so zooming in reveals real detail rather than stretching what was loaded.
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

  // Fetched per window, like the curve. The windowed calculation pads far
  // enough to be identical to the whole-curve answer, so there is no reason to
  // compute 55,000 years to draw 400 of them.
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

  /**
   * Hover, coalesced to one update per frame.
   *
   * Plotly emits a hover event per pointer sample, which on a trackpad is
   * faster than the screen redraws, and each one re-renders this page: the
   * readout, the scrubber, and three charts that then have to be diffed.
   * Dropping the events that a frame would never have shown costs nothing
   * visible and takes the work down to what the display can use.
   */
  const hoverFrame = useRef<number | null>(null)
  const pendingHover = useRef<number | null>(null)
  const noteHover = useCallback((value: number | null) => {
    pendingHover.current = value
    if (hoverFrame.current !== null) return
    hoverFrame.current = requestAnimationFrame(() => {
      hoverFrame.current = null
      setHover(pendingHover.current)
    })
  }, [])
  useEffect(
    () => () => {
      if (hoverFrame.current !== null) cancelAnimationFrame(hoverFrame.current)
    },
    []
  )

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
        // Just enough to say where the pointer is: the error and gradient are
        // in the readout under the chart, and a floating box repeating them
        // covers the curve. Pre-formatted because %{x} is always the cal BP
        // the point is plotted at, where the reader may be in BC/AD.
        customdata: labels,
        mode: 'lines',
        line: { color: colors.posteriorLine, width: 1.5 },
        name: spec?.name ?? curveId,
        // Named only when there are two lines to tell apart: alone it repeats
        // the chart's title into a box covering the curve, but with a
        // comparison overlaid, two unlabelled numbers are a guess.
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
   * Passed to Plotly's LAYOUT as well as the container's style: style alone
   * grows the box while Plotly keeps drawing at its old computed height,
   * leaving a blank strip where the curve should have expanded.
   * `useResizeHandler` does not catch it -- it listens for WINDOW resizes.
   */
  /**
   * Heights for the stack of panels under the chart.
   *
   * The curve gives up what the panels take, so the readout below stays put.
   * It cannot give up everything, though: at two panels a straight 110 each
   * leaves the curve 160px tall, too short to read. So the panels shrink a
   * little when there are two, and the chart floors at 200.
   */
  const panelCount = (showResolution ? 1 : 0) + (comparison.length > 0 ? 1 : 0)
  const panelHeight = panelCount > 1 ? 75 : 85
  /**
   * What a framed section costs beyond its chart: the border, the title strip
   * with its controls, and the margin above it. Measured rather than guessed --
   * an estimate 45px out moves the readout every time a panel opens.
   */
  const SECTION_CHROME = 91
  /**
   * With one panel this comes to 380 exactly, so the readout does not move.
   * With two it cannot: 330px between them can only be absorbed by a chart too
   * short to read, so the chart floors at 200 and the page grows instead.
   */
  const chartHeight = Math.max(380 - panelCount * (panelHeight + SECTION_CHROME), 200)

  const layout = useMemo(() => {
    const ticks = window ? calendarAxisTicks(window.from, window.to, dateFormat) : null
    return {
      height: chartHeight,
      title: { text: spec ? `${spec.name}` : curveId, font: { size: 16 } },
      xaxis: {
        // Dropped when panels sit below, where it strands halfway down a stack
        // of charts sharing one axis; a caption under the last panel carries it.
        ...(panelCount > 0 ? {} : { title: { text: calendarAxisTitle(dateFormat) } }),
        // The crosshair 'x unified' would draw, kept without it. See hovermode.
        showspikes: true,
        spikemode: 'across' as const,
        spikethickness: 1,
        spikedash: 'dot',
        spikecolor: colors.grid,
        // Older to the left, as in every plot here, so time reads left to right.
        autorange: 'reversed' as const,
        gridcolor: colors.grid,
        ...(ticks ? { tickvals: ticks.tickvals, ticktext: ticks.ticktext } : {}),
      },
      yaxis: {
        title: { text: 'Radiocarbon Age (BP)' },
        gridcolor: colors.grid,
      },
      /**
       * 'closest', not 'x unified'. Unified draws a header holding the raw x
       * value, which is always cal BP here -- so it prints the date twice in
       * cal BP, and in BC/AD prints a number in a convention the reader turned
       * off. It cannot be reformatted: no axis format turns 2502 into 553 BC.
       * The spike lines above keep the crosshair that made unified worth having.
       */
      hovermode: 'closest' as const,
      /**
       * No distance cutoff, so pointing anywhere finds the nearest point on
       * the curve rather than making the pointer track the line. The readout
       * underneath is driven by this hover, and with the default cutoff it
       * blanks whenever the pointer drifts thirty pixels off the curve.
       */
      hoverdistance: -1,
      showlegend: true,
      legend: { orientation: 'h' as const, y: 1.02, yanchor: 'bottom' as const },
      font: plotFont,
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
      // The bar goes; the CANVAS stays. Plotly's modebar duplicates zoom and
      // pan controls this page has as real buttons, and duplicates them badly:
      // it mutates the axis range directly while this page's `window` state --
      // which the buttons, presets, scrubber and export all read -- never
      // hears about it, so the app's own "Zoom in" then snaps back to the full
      // range and zooms OUT.
      //
      // Drag-to-zoom is worth keeping, being the best way to pick a plateau out
      // of a curve, so it stays and reports itself through onRelayout below.
      displayModeBar: false as const,
    }),
    []
  )

  /**
   * The panels' figures, built once per change rather than once per render.
   *
   * react-plotly.js compares `data`, `layout` and `config` by IDENTITY, so an
   * object literal written inside the JSX is a new figure on every render and
   * re-enters Plotly every time. These panels are under a chart that reports
   * hover, and this page re-renders on every hover: built inline, moving the
   * pointer along the curve rebuilt and re-diffed two more charts per frame,
   * which is what made the page feel heavy to scrub across.
   */
  const panelConfig = useMemo(
    () => ({ responsive: true, displayModeBar: false as const }),
    []
  )

  /** The axis and chrome both panels share: the curve's window, no labels. */
  const panelAxis = useMemo(
    () => ({
      // Locked to the curve above rather than autoranged: the two are read
      // together, and an axis drifting a few years would line a plateau up
      // against the wrong stretch.
      range: [window?.to ?? 0, window?.from ?? 0],
      showticklabels: false,
      gridcolor: colors.grid,
      showspikes: true,
      spikemode: 'across' as const,
      spikethickness: 1,
      spikedash: 'dot',
      spikecolor: colors.grid,
    }),
    [window, colors]
  )

  const comparisonData = useMemo(
    () =>
      [
        {
          x: [...comparison.map((p) => p[0]), ...comparison.map((p) => p[0]).reverse()],
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
          hovertemplate: '<b>%{customdata}</b><br>%{y:.0f} ¹⁴C yr apart<extra></extra>',
        },
      ] as any,
    [comparison, colors, dateFormat]
  )

  const comparisonLayout = useMemo(
    () =>
      ({
        height: panelHeight,
        xaxis: panelAxis,
        yaxis: {
          // Units only: a rotated title is limited by the panel's HEIGHT,
          // which is 75-85px here, and the section header already says what
          // is being plotted.
          title: { text: 'yr', font: { size: 11 } },
          gridcolor: colors.grid,
          zeroline: true,
          zerolinecolor: colors.grid,
        },
        showlegend: false,
        hovermode: 'closest' as const,
        hoverdistance: -1,
        font: plotFont,
        plot_bgcolor: colors.surface,
        paper_bgcolor: colors.surface,
        margin: { l: 52, r: 20, t: 4, b: 4 },
        autosize: true,
      }) as any,
    [panelAxis, panelHeight, colors]
  )

  const resolutionData = useMemo(
    () =>
      [
        {
          // The 5th-95th band: how lucky or unlucky one date can be.
          x: [...resolution.map((p) => p[0]), ...resolution.map((p) => p[0]).reverse()],
          y: [...resolution.map((p) => p[3]), ...resolution.map((p) => p[2]).reverse()],
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
          hovertemplate: '<b>%{customdata}</b><br>about %{y:.0f} years (95.4%)<extra></extra>',
        },
      ] as any,
    [resolution, colors, dateFormat]
  )

  const resolutionLayout = useMemo(
    () =>
      ({
        height: panelHeight,
        xaxis: { ...panelAxis, matches: 'x' },
        yaxis: {
          title: { text: 'yr', font: { size: 11 } },
          gridcolor: colors.grid,
          rangemode: 'tozero' as const,
        },
        showlegend: false,
        hovermode: 'closest' as const,
        hoverdistance: -1,
        font: plotFont,
        plot_bgcolor: colors.surface,
        paper_bgcolor: colors.surface,
        margin: { l: 52, r: 20, t: 4, b: 4 },
        autosize: true,
      }) as any,
    [panelAxis, panelHeight, colors]
  )

  /**
   * Feed Plotly's own drag-zoom back into the window this page owns.
   *
   * Without it the two disagree silently, and not only visually: "Export this
   * window" builds its URL from window.from/to, so box-zooming to a plateau
   * and exporting yields the PREVIOUS window's data in a file that looks like
   * the one on screen.
   *
   * Only acts on an explicit range: Plotly also emits relayout for autorange,
   * resizes and dragmode changes, where re-applying state would fight the
   * render that caused them.
   */
  const handleRelayout = useCallback(
    (event: Record<string, unknown>) => {
      if (!extent) return
      const lo = event['xaxis.range[0]']
      const hi = event['xaxis.range[1]']
      if (typeof lo !== 'number' || typeof hi !== 'number') return
      // The axis is reversed, so which end arrives first is not guaranteed.
      const next = clampWindow({ from: Math.min(lo, hi), to: Math.max(lo, hi) }, extent)
      setWindow((current) =>
        current && Math.abs(current.from - next.from) < 0.5 && Math.abs(current.to - next.to) < 0.5
          ? current
          : next
      )
    },
    [extent]
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
            label={<Typography variant="body2">Expected resolution</Typography>}
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
          onRelayout={handleRelayout}
          onHover={(event: any) => noteHover(event?.points?.[0]?.x ?? null)}
          onUnhover={() => noteHover(null)}
          // Clicking a year asks what a date from it would look like. In a
          // plateau the answer is three or four disjoint ranges, which says
          // what a plateau costs more plainly than any single number.
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
              data={comparisonData}
              layout={comparisonLayout}
              config={panelConfig}
              useResizeHandler
              style={{ width: '100%', height: `${panelHeight}px` }}
              onHover={(event: any) => noteHover(event?.points?.[0]?.x ?? null)}
              onUnhover={() => noteHover(null)}
            />
          </PanelSection>
        )}

        {showResolution && (
          <PanelSection
            title="Expected resolution"
            caption="how tightly a date from each year could be known, before measuring"
            busy={resolutionLoading}
            overlay={
              // Nothing drawn yet: either the first answer for this window is
              // still being worked out -- seconds, for the whole curve, since
              // every year of it is calibrated against every measurement it
              // could return -- or it could not be, which otherwise leaves a
              // pair of empty axes explaining nothing.
              resolution.length > 0 ? null : resolutionLoading ? (
                <>
                  <CircularProgress size={14} />
                  <Typography variant="caption" color="textSecondary">
                    Working out how tightly this stretch could be dated…
                  </Typography>
                </>
              ) : (
                <Typography variant="caption" color="textSecondary">
                  This window's resolution could not be worked out.
                </Typography>
              )
            }
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
              data={resolutionData}
              layout={resolutionLayout}
              config={panelConfig}
              useResizeHandler
              style={{ width: '100%', height: `${panelHeight}px` }}
              onHover={(event: any) => noteHover(event?.points?.[0]?.x ?? null)}
              onUnhover={() => noteHover(null)}
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
            backgroundColor: 'background.muted',
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
                  : ' running. Turn on Expected resolution above for how tightly a' +
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
          {/* Reset sits with the other view controls, and unlike Plotly's own
              autoscale it agrees with `window`. */}
          <Button size="small" onClick={() => setPreset(extent.min, extent.max)}>
            Reset view
          </Button>
          <Box sx={{ flexGrow: 1 }} />
          <Button
            size="small"
            startIcon={<DownloadIcon />}
            onClick={() => downloadPlotPng(plotBoxRef.current, `curve-${curveId}`)}
          >
            Download PNG
          </Button>
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
