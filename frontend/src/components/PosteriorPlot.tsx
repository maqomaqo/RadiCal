import { useMemo, useRef } from 'react'
import { Box, Button, Paper, Typography } from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import Plot from '@/components/Plot'
import type { Config, Data, Layout } from 'plotly.js-dist-min'
import {
  DateFormat,
  DEFAULT_DATE_FORMAT,
  calendarAxisTicks,
  calendarAxisTitle,
  formatCalendarAge,
} from '@/utils/dateFormat'
import type { HPDBand } from '@/utils/hpd'
import { usePlotColors, usePlotFont } from '@/theme/plotColors'
import { downloadPlotPng } from '@/utils/downloadPlot'

/**
 * A filled rectangle in data coordinates, as a closed polygon trace -- same
 * technique StackedPosteriorPlot uses for its own HPD bars.
 */
function bar(
  x0: number,
  x1: number,
  y0: number,
  y1: number,
  color: string,
  name: string,
  showlegend: boolean,
  legendgroup: string
) {
  return {
    x: [x0, x0, x1, x1],
    y: [y0, y1, y1, y0],
    fill: 'toself',
    fillcolor: color,
    line: { width: 0, color: 'rgba(0,0,0,0)' },
    mode: 'lines',
    name,
    legendgroup,
    showlegend,
    hoverinfo: 'skip',
  }
}

// Bar geometry as fractions of the posterior's peak density; negative values
// sit below the curve's zero baseline. Top to bottom: curve, gap, outer bar,
// gap, inner bar, then AXIS_BOTTOM_MARGIN of clear space before the x-axis,
// which keeps the axis line and ticks off the inner bar. The explicit
// yaxis.range below is what stops Plotly's autorange shrinking that margin.
//
// The strip nearer the curve carries the band the reader selected, the one
// below it the fixed 1-sigma reference. Named by position, since the levels
// are the caller's to decide.
const OUTER_BAR: [number, number] = [-0.11, -0.04]
const INNER_BAR: [number, number] = [-0.22, -0.15]
const AXIS_BOTTOM_MARGIN = 0.07

// react-plotly.js's <Plot> wraps the imperative Plotly.js API: newPlot on
// mount, Plotly.react (a diffed in-place update) on every data/layout/config
// change, and purge on unmount. Bound to the plotly.js-dist-min package rather
// than a CDN global, so the version is pinned by package.json.

interface PosteriorPlotProps {
  calAges: number[]
  probabilities: number[]
  /**
   * The HPD bands to draw, outer first, computed at the confidence the page is
   * showing (utils/hpd.hpdBands). This component neither decides nor names a
   * level: it is handed the ranges and their label, so the legend cannot claim
   * a level the geometry was not computed at.
   *
   * Each band carries one or more disjoint ranges, curves being non-monotonic
   * and posteriors often multimodal. Each range is its own bar rather than one
   * spanning the envelope, which would claim the gap between peaks. Only the
   * first bar of a band gets a legend entry.
   */
  bands?: HPDBand[]
  /**
   * The same date's plain calibration -- what it would say alone -- as a grey
   * shadow behind the posterior when showPrior is on. Off by default: a
   * comparison aid, not part of every reading.
   */
  priorDistribution?: Array<[number, number]>
  showPrior?: boolean
  /**
   * A modelled transition rather than a dated thing, drawn in the boundary
   * coral as it is in the stacked plot and the diagrams: the same object must
   * not change colour with the view.
   */
  isBoundary?: boolean
  sampleName?: string
  pointEstimates?: {
    mean?: number
    median?: number
    std?: number
  }
  /** Which point estimates to render below the chart, so the caller can offer
   * mean/median as opt-in reporting choices. Both by default. */
  showMean?: boolean
  showMedian?: boolean
  height?: number
  /**
   * The reporting format to present calendar ages in -- axis ticks, the
   * hover readout and the point-estimate summary all follow it.
   *
   * The chart still PLOTS in cal BP, which is what the calibration curve
   * and the posterior are computed in; only the labelling changes. That
   * matters: switching format never moves a curve or a shaded band, so the
   * picture stays directly comparable between formats.
   */
  dateFormat?: DateFormat
}

export default function PosteriorPlot({
  calAges,
  probabilities,
  bands = [],
  priorDistribution,
  showPrior = false,
  isBoundary = false,
  sampleName,
  pointEstimates,
  showMean = true,
  showMedian = true,
  height = 400,
  dateFormat = DEFAULT_DATE_FORMAT,
}: PosteriorPlotProps) {
  const plot = usePlotColors()
  const plotFont = usePlotFont()
  const formatAge = (age: number) =>
    Number.isFinite(age) ? formatCalendarAge(age, dateFormat) : 'N/A'
  // The backend sends `null`, not `undefined`, for point estimates that do not
  // apply -- a single-calibration result has posterior_mean/median/std null,
  // those being Bayesian-only. `value !== undefined` is true for null, so a
  // check written that way passes null into `.toFixed(0)` and crashes the
  // whole page. A real finite-number check everywhere instead.
  const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

  // The posterior's own peak, which the HPD bars and the yaxis range below
  // scale against whether the prior shadow is taller or shorter.
  const peak = useMemo(
    () => (probabilities && probabilities.length > 0 ? Math.max(...probabilities) : 0),
    [probabilities]
  )

  // Prepare data for Plotly
  const plotData = useMemo(() => {
    if (!calAges || !probabilities || calAges.length === 0) {
      return []
    }

    const data: Data[] = []

    // The unmodelled distribution, first so the posterior draws over it: what
    // shows through is where the model moved the date from its plain
    // calibration.
    if (showPrior && priorDistribution && priorDistribution.length > 0) {
      const priorAges = priorDistribution.map(([age]) => age)
      const priorProbs = priorDistribution.map(([, prob]) => prob)
      data.push({
        x: priorAges,
        y: priorProbs,
        customdata: priorAges.map((age) => formatCalendarAge(age, dateFormat)),
        fill: 'tozeroy',
        fillcolor: plot.priorFill,
        line: { color: plot.priorLine, width: 1 },
        name: 'Prior (unmodelled)',
        hovertemplate: '<b>%{customdata}</b><br>Prior probability: %{y:.4f}<extra></extra>',
      })
    }

    // Main posterior distribution (filled area)
    data.push({
      x: calAges,
      y: probabilities,
      // Pre-formatted: %{x} is always the cal BP the point is plotted at, and
      // the hover should read in the chosen convention.
      customdata: calAges.map((age) => formatCalendarAge(age, dateFormat)),
      fill: 'tozeroy',
      fillcolor: isBoundary ? plot.boundaryFill : plot.posteriorFill,
      line: {
        color: isBoundary ? plot.boundaryLine : plot.posteriorLine,
        width: 2,
        dash: isBoundary ? 'dot' : 'solid',
      },
      name: isBoundary ? 'Boundary (modelled transition)' : 'Posterior (modelled)',
      hovertemplate: '<b>%{customdata}</b><br>Probability: %{y:.4f}<extra></extra>',
    })

    // HPD regions as solid bars in their own strip below the curve rather than
    // fills under it, which would stack translucent colours three deep with
    // the prior shadow on and go muddy. The two levels are one hue at two
    // strengths, being nested intervals of the same quantity, so the stronger
    // is the tighter interval.
    //
    // Each disjoint range gets its own bar: one spanning the min/max envelope
    // would claim the gap between peaks. Only the first bar at each level sets
    // showlegend, so a fragmented posterior prints one legend row.
    //
    // A lone band is the tightest interval on the plot, so it takes the
    // stronger fill even in the outer strip: the alphas encode "tighter", not
    // "which slot".
    const soleBand = bands.length === 1
    bands.forEach((band) => {
      const slot = band.tone === 'outer' ? OUTER_BAR : INNER_BAR
      const fill = soleBand || band.tone === 'inner' ? plot.hpd68 : plot.hpd95
      const [y0, y1] = [slot[0] * peak, slot[1] * peak]
      band.ranges.forEach((range, idx) =>
        data.push(
          bar(range.min, range.max, y0, y1, fill, `${band.label} HPD`, idx === 0, `hpd-${band.tone}`)
        )
      )
    })

    // Vertical line at mean
    if (showMean && isNum(pointEstimates?.mean)) {
      data.push({
        x: [pointEstimates.mean, pointEstimates.mean],
        y: [0, Math.max(...probabilities)],
        mode: 'lines',
        line: { color: plot.mean, width: 2, dash: 'dash' },
        name: `Mean: ${formatCalendarAge(pointEstimates.mean, dateFormat)}`,
        hovertemplate: '',
        showlegend: true,
      })
    }

    // Vertical line at median
    if (showMedian && isNum(pointEstimates?.median)) {
      data.push({
        x: [pointEstimates.median, pointEstimates.median],
        y: [0, Math.max(...probabilities)],
        mode: 'lines',
        line: { color: plot.median, width: 2, dash: 'dot' },
        name: `Median: ${formatCalendarAge(pointEstimates.median, dateFormat)}`,
        hovertemplate: '',
        showlegend: true,
      })
    }

    return data
  }, [
    calAges,
    probabilities,
    peak,
    plot,
    bands,
    priorDistribution,
    showPrior,
    isBoundary,
    pointEstimates,
    showMean,
    showMedian,
    dateFormat,
  ])

  // Tick positions stay in cal BP, where the data is; only the labels take the
  // chosen convention. Null for cal BP, leaving Plotly to auto-tick. Includes
  // the prior's ages when shown, since the axis ranges to whatever is plotted
  // and a wider prior would leave its edges unlabelled.
  const ticks = useMemo(() => {
    if (!calAges || calAges.length === 0) return null
    const priorAges = showPrior && priorDistribution ? priorDistribution.map(([age]) => age) : []
    const allAges = priorAges.length > 0 ? [...calAges, ...priorAges] : calAges
    return calendarAxisTicks(Math.min(...allAges), Math.max(...allAges), dateFormat)
  }, [calAges, dateFormat, priorDistribution, showPrior])

  // Explicit range rather than autorange: the bars and their bottom margin
  // need guaranteed room below zero, which a range fitted to the tallest trace
  // would shrink away. Floats the curve up and pulls the x-axis down with it;
  // see OUTER_BAR/INNER_BAR/AXIS_BOTTOM_MARGIN for the geometry.
  const yRange = useMemo((): [number, number] | null => {
    if (!(peak > 0)) return null
    let top = peak
    if (showPrior && priorDistribution && priorDistribution.length > 0) {
      const priorPeak = Math.max(...priorDistribution.map(([, prob]) => prob))
      if (priorPeak > top) top = priorPeak
    }
    return [(INNER_BAR[0] - AXIS_BOTTOM_MARGIN) * peak, top * 1.08]
  }, [peak, showPrior, priorDistribution])

  const plotBox = useRef<HTMLDivElement | null>(null)

  const layout = useMemo(
    (): Partial<Layout> => ({
      // A layout property, not a config one: hiding the modebar does not stop
      // drag-to-box-zoom, which would leave a reader zoomed in with no way out.
      dragmode: false as const,
      // Plotly 3 dropped a bare string here; a title is an object with text.
      title: { text: sampleName ? `Posterior Distribution: ${sampleName}` : 'Posterior Distribution' },
      xaxis: {
        title: { text: calendarAxisTitle(dateFormat) },
        zeroline: false,
        gridcolor: plot.grid,
        // Time runs left to right, oldest to youngest, as a chronology reads.
        // cal BP counts BACKWARDS, so an ascending axis would put the present
        // on the left -- the reverse of every published calibration plot.
        // Reversing also suits BC/AD, where the calendar year ascends.
        autorange: 'reversed',
        // Null for cal BP: the values already are cal BP, so Plotly's own tick
        // choice beats anything imposed here.
        ...(ticks ? { tickmode: 'array', tickvals: ticks.tickvals, ticktext: ticks.ticktext } : {}),
      },
      yaxis: {
        title: { text: 'Probability Density' },
        zeroline: false,
        gridcolor: plot.grid,
        ...(yRange ? { range: yRange, autorange: false } : {}),
      },
      hovermode: 'x unified',
      font: plotFont,
      plot_bgcolor: plot.surface,
      paper_bgcolor: plot.surface,
      margin: { l: 60, r: 20, t: 50, b: 60 },
      autosize: true,
    }),
    [plot, sampleName, dateFormat, ticks, yRange]
  )

  const config = useMemo(
    (): Partial<Config> => ({
      responsive: true,
      // No modebar, and the canvas is locked. The bar renders six <a> elements
      // with no href, role or accessible name, none focusable -- 98 of them on
      // a 12-date Individual view -- and the PNG export behind it is how a
      // figure reaches a paper, so the app draws that control itself.
      // dragmode:false because hiding the bar does NOT disable zooming.
      displayModeBar: false as const,
      displaylogo: false,
    }),
    []
  )

  if (plotData.length === 0) {
    return (
      <Paper sx={{ p: 2 }}>
        <Typography color="error">No data to display</Typography>
      </Paper>
    )
  }

  // elevation={0} with a hairline instead of a shadow: this card renders
  // INSIDE the Posterior Distributions card, which on a 63-date model is
  // sixty-five stacked shadows. Nested elevation reads as noise; see DESIGN.md.
  return (
    <Paper elevation={0} variant="outlined" sx={{ p: 2, mb: 2 }} ref={plotBox}>
      <Plot
        data={plotData}
        layout={layout}
        config={config}
        useResizeHandler
        style={{ width: '100%', height: `${height}px`, minHeight: '300px' }}
      />
      {/* The control that replaced the modebar's camera icon -- labelled,
          in the tab order, and next to the figure it saves. */}
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 0.5 }}>
        <Button
          size="small"
          startIcon={<DownloadIcon />}
          onClick={() =>
            downloadPlotPng(plotBox.current, sampleName ? `posterior-${sampleName}` : 'posterior')
          }
        >
          Download PNG
        </Button>
      </Box>
      {pointEstimates && (showMean || showMedian) && (
        <Box sx={{ mt: 2, p: 1, backgroundColor: 'background.muted', borderRadius: 1 }}>
          <Typography variant="body2" color="textSecondary" gutterBottom>
            <strong>Point estimates:</strong>
          </Typography>
          {showMean && (
            <Typography variant="body2">
              Mean: <strong>{isNum(pointEstimates.mean) ? formatAge(pointEstimates.mean) : 'N/A'}</strong>
              {isNum(pointEstimates.std) && ` (± ${pointEstimates.std.toFixed(0)} yr)`}
            </Typography>
          )}
          {showMedian && (
            <Typography variant="body2">
              Median: <strong>{isNum(pointEstimates.median) ? formatAge(pointEstimates.median) : 'N/A'}</strong>
            </Typography>
          )}
        </Box>
      )}
    </Paper>
  )
}
