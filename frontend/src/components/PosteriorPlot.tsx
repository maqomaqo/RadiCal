import { useMemo } from 'react'
import { Box, Paper, Typography } from '@mui/material'
import Plotly from 'plotly.js-dist-min'
import createPlotlyComponent from 'react-plotly.js/factory'
import {
  DateFormat,
  DEFAULT_DATE_FORMAT,
  calendarAxisTicks,
  calendarAxisTitle,
  formatCalendarAge,
} from '@/utils/dateFormat'
import type { HPDBand } from '@/utils/hpd'
import { usePlotColors } from '@/theme/plotColors'
import { PNG_EXPORT_SCALE, exportFilename } from '@/utils/plotExport'

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

// Bar geometry, as fractions of the posterior's own peak density -- negative
// values sit below the curve's zero baseline. Ordered top to bottom: curve,
// a gap, the outer bar, a gap, the inner bar, then AXIS_BOTTOM_MARGIN of clear
// space before the x-axis itself. That last margin is what keeps the axis
// line/ticks/title from landing on top of the inner bar -- an explicit
// yaxis.range (below) is what makes this margin real rather than something
// Plotly's autorange could shrink away.
// The strip nearer the curve carries the band the reader selected; the one
// below it carries the fixed 1-sigma reference. Named by position rather than
// by level, because the levels are now the caller's to decide.
const OUTER_BAR: [number, number] = [-0.11, -0.04]
const INNER_BAR: [number, number] = [-0.22, -0.15]
const AXIS_BOTTOM_MARGIN = 0.07

// react-plotly.js's <Plot> wraps the imperative Plotly.js API so this
// component doesn't have to: it calls Plotly.newPlot on mount,
// Plotly.react (a diffed, in-place update -- not a full teardown/rebuild)
// on every subsequent data/layout/config change, and Plotly.purge on
// unmount. Previously this component drove that API by hand via
// useEffect + document.getElementById + a CDN-loaded window.Plotly,
// which used newPlot for every update (discarding any zoom/pan the user
// had done and causing a visible flash on something as small as a
// date-format switch), never purged on unmount (leaking a live Plotly
// instance per plot on a Results page showing several samples), and
// pulled in Plotly from a CDN script tag pointed at a frozen "latest"
// alias (the console's "no longer the latest release" warning seen
// throughout this app). Binding the factory to the plotly.js-dist-min
// package already in package.json instead removes all three at once.
const Plot = createPlotlyComponent(Plotly)

interface PosteriorPlotProps {
  calAges: number[]
  probabilities: number[]
  /**
   * The HPD bands to draw, outer first, already computed at the confidence
   * the page is showing -- see utils/hpd.hpdBands. This component no longer
   * decides or names a level: it is handed the ranges and the label that
   * belongs to them, so the legend cannot claim a level the geometry was not
   * computed at. That is exactly what used to go wrong, with '95% HPD'
   * hardcoded here while the reader had selected 99.7%.
   *
   * Each band carries one or more disjoint ranges, because calibration curves
   * are non-monotonic and a posterior is often multimodal; each range is drawn
   * as its own bar rather than one bar spanning the full envelope, which would
   * claim the gap between peaks as part of the region. Only the first bar of a
   * band gets a legend entry, so a fragmented posterior prints one row rather
   * than one per segment.
   */
  bands?: HPDBand[]
  /**
   * The same date's plain calibration with no stratigraphic constraints --
   * "what it would say alone" -- drawn as a light grey shadow behind the
   * posterior when showPrior is on. Off by default: it's a comparison aid,
   * not something every reading of the plot needs.
   */
  priorDistribution?: Array<[number, number]>
  showPrior?: boolean
  /**
   * A modelled transition rather than a dated thing. Drawn in the boundary
   * coral, as it is in the stacked plot and in the diagrams -- the same
   * object should not change colour depending on which view you opened.
   */
  isBoundary?: boolean
  sampleName?: string
  pointEstimates?: {
    mean?: number
    median?: number
    std?: number
  }
  /** Which point estimates to actually render below the chart -- lets the
   * caller offer mean/median as opt-in reporting choices rather than
   * always showing both. Defaults to both shown (existing behavior) when
   * not provided. */
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
  const formatAge = (age: number) =>
    Number.isFinite(age) ? formatCalendarAge(age, dateFormat) : 'N/A'
  // The backend legitimately sends `null` (not `undefined`) for point
  // estimates that don't apply to a given result -- e.g. every
  // single-calibration result has posterior_mean/median/std = null, since
  // those are Bayesian-only fields. `value !== undefined` is true for
  // `null`, so checks written that way (as this component previously had)
  // pass a null straight into `.toFixed(0)`, throwing "Cannot read
  // properties of null (reading 'toFixed')" and crashing the whole Results
  // page for any analysis that includes a single-calibration result. Use a
  // real finite-number check everywhere instead.
  const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

  // The posterior's own peak -- what the HPD bars (and the yaxis range
  // below) are scaled against, regardless of whether the prior shadow is
  // taller or shorter.
  const peak = useMemo(
    () => (probabilities && probabilities.length > 0 ? Math.max(...probabilities) : 0),
    [probabilities]
  )

  // Prepare data for Plotly
  const plotData = useMemo(() => {
    if (!calAges || !probabilities || calAges.length === 0) {
      return []
    }

    const data: any[] = []

    // Prior (unmodelled) distribution, pushed first so the posterior draws
    // over it -- what's left showing through is exactly where the model
    // moved the date away from its own plain calibration.
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
      // Pre-formatted, because %{x} is always the cal BP the point is
      // plotted at -- the hover should read in the chosen convention.
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

    // HPD region(s), as solid range bars in their own strip below the curve
    // rather than shaded fills under the curve itself. A fill there has to
    // sit on top of the posterior's own fill -- and, with the prior shadow
    // on, the prior's grey fill too -- and translucent colours stacked
    // three deep go muddy and hard to read. A bar in its own space stays
    // one flat colour regardless of what else is showing. The two levels
    // are one hue at two strengths: they are nested intervals of the same
    // quantity, so the stronger is the tighter interval rather than the
    // two being different things. Calibration curves are non-monotonic, so the
    // HPD region is often two or more disjoint ranges rather than one span
    // -- when *Ranges is available, each range gets its own bar instead of
    // one spanning the full min/max envelope, which would visually claim
    // the gap between peaks as part of the HPD region. Only the first bar
    // at each level gets showlegend: true, so a fragmented posterior still
    // shows one legend row rather than one per segment.
    // A lone band is the tightest interval on the plot with nothing nested
    // inside it, so it takes the stronger fill even though it sits in the
    // outer strip -- the two alphas encode "tighter", not "which slot".
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

  // Tick positions stay in cal BP (where the data is); only the labels are
  // in the chosen convention. Null for cal BP, leaving Plotly to auto-tick.
  // Includes the prior's own ages when it's shown, since the axis
  // auto-ranges to whatever's plotted and a wider prior otherwise leaves
  // its edges unlabelled.
  const ticks = useMemo(() => {
    if (!calAges || calAges.length === 0) return null
    const priorAges = showPrior && priorDistribution ? priorDistribution.map(([age]) => age) : []
    const allAges = priorAges.length > 0 ? [...calAges, ...priorAges] : calAges
    return calendarAxisTicks(Math.min(...allAges), Math.max(...allAges), dateFormat)
  }, [calAges, dateFormat, priorDistribution, showPrior])

  // Explicit range rather than Plotly's autorange: the bars and their
  // bottom margin need real, guaranteed room below zero, which autorange
  // (fit tightly to the tallest/widest trace) would otherwise shrink back
  // down to nothing whenever a plot happened to have no bars taller than
  // the curve itself. Floats the curve up and pulls the x-axis down with
  // it -- see OUTER_BAR/INNER_BAR/AXIS_BOTTOM_MARGIN above for the geometry.
  const yRange = useMemo(() => {
    if (!(peak > 0)) return null
    let top = peak
    if (showPrior && priorDistribution && priorDistribution.length > 0) {
      const priorPeak = Math.max(...priorDistribution.map(([, prob]) => prob))
      if (priorPeak > top) top = priorPeak
    }
    return [(INNER_BAR[0] - AXIS_BOTTOM_MARGIN) * peak, top * 1.08]
  }, [peak, showPrior, priorDistribution])

  const layout = useMemo(
    () => ({
      title: sampleName ? `Posterior Distribution: ${sampleName}` : 'Posterior Distribution',
      xaxis: {
        title: calendarAxisTitle(dateFormat),
        zeroline: false,
        gridcolor: plot.grid,
        // Time runs left to right, oldest to youngest, as a chronology is
        // conventionally read. The data is plotted in cal BP, which counts
        // BACKWARDS from the present, so plotting it on an ascending axis
        // put the present on the left and the oldest date on the right --
        // the reverse of every published calibration plot. Reversing the
        // axis is also what BC/AD needs, since an ascending calendar year is
        // the same direction.
        autorange: 'reversed',
        // Null for cal BP -- the values already are cal BP, so Plotly's own
        // tick choice beats anything imposed here.
        ...(ticks ? { tickmode: 'array', tickvals: ticks.tickvals, ticktext: ticks.ticktext } : {}),
      },
      yaxis: {
        title: 'Probability Density',
        zeroline: false,
        gridcolor: plot.grid,
        ...(yRange ? { range: yRange, autorange: false } : {}),
      },
      hovermode: 'x unified',
      plot_bgcolor: plot.surface,
      paper_bgcolor: plot.surface,
      margin: { l: 60, r: 20, t: 50, b: 60 },
      autosize: true,
    }),
    [plot, sampleName, dateFormat, ticks, yRange]
  )

  const config = useMemo(
    () => ({
      responsive: true,
      displayModeBar: true,
      displaylogo: false,
      modeBarButtonsToRemove: ['lasso2d', 'select2d'],
      toImageButtonOptions: {
        format: 'png' as const,
        scale: PNG_EXPORT_SCALE,
        filename: exportFilename(sampleName ? `posterior-${sampleName}` : 'posterior'),
      },
    }),
    [sampleName]
  )

  if (plotData.length === 0) {
    return (
      <Paper sx={{ p: 2 }}>
        <Typography color="error">No data to display</Typography>
      </Paper>
    )
  }

  // elevation={0} with a hairline instead of a shadow: this card renders
  // INSIDE the Posterior Distributions card, and on a 63-date model that is
  // sixty-five stacked shadows. Same call DESIGN.md records for the dense
  // Accordion in ImportDatesDialog -- nested elevation reads as noise, so the
  // boundary is drawn with a line.
  return (
    <Paper elevation={0} variant="outlined" sx={{ p: 2, mb: 2 }}>
      <Plot
        data={plotData}
        layout={layout}
        config={config}
        useResizeHandler
        style={{ width: '100%', height: `${height}px`, minHeight: '300px' }}
      />
      {pointEstimates && (showMean || showMedian) && (
        <Box sx={{ mt: 2, p: 1, backgroundColor: 'background.muted', borderRadius: 1 }}>
          <Typography variant="body2" color="textSecondary" gutterBottom>
            <strong>Point Estimates:</strong>
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
