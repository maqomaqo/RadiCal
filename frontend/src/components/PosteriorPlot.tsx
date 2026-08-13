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
import type { HPDRange } from '@/utils/dateFormat'

// Linear-interpolated probability at an arbitrary calBP position, from the
// plotted (calAges, probabilities) grid. HPD boundaries are computed by the
// backend against its own finer grid and rarely land exactly on one of the
// ~200 points this component plots, so shading a band right up to its true
// edge needs a value AT that edge, not just at the nearest plotted point.
function densityAt(calAges: number[], probabilities: number[], target: number): number {
  const n = calAges.length
  if (n === 0) return 0
  for (let i = 0; i < n - 1; i++) {
    const a = calAges[i]
    const b = calAges[i + 1]
    if ((target - a) * (target - b) <= 0) {
      if (a === b) return probabilities[i]
      const t = (target - a) / (b - a)
      return probabilities[i] + t * (probabilities[i + 1] - probabilities[i])
    }
  }
  // Outside the plotted range entirely -- clamp to whichever end is closer.
  return Math.abs(target - calAges[0]) <= Math.abs(target - calAges[n - 1])
    ? probabilities[0]
    : probabilities[n - 1]
}

/**
 * The polygon for shading an HPD band as the area under the curve itself
 * (clipped to [lo, hi]) rather than a box of some arbitrary fixed height.
 * A box has to pick one height for the whole interval, which is wrong
 * everywhere the curve isn't flat -- either short of the peak at the mode,
 * or floating above the curve out in the interval's own tails. Following
 * the curve is right at every x by construction, and it's what OxCal and
 * CALIB draw HPD shading as.
 *
 * calAges isn't assumed to be sorted in either direction (the interpolation
 * above only needs consecutive pairs to bracket the target); this collects
 * the interior points and re-sorts by x so the returned polygon is a clean
 * x-ascending sweep regardless of the source order.
 */
function curveFillPolygon(
  calAges: number[],
  probabilities: number[],
  lo: number,
  hi: number
): { x: number[]; y: number[] } {
  const xMin = Math.min(lo, hi)
  const xMax = Math.max(lo, hi)
  const points: Array<[number, number]> = [[xMin, densityAt(calAges, probabilities, xMin)]]
  for (let i = 0; i < calAges.length; i++) {
    const x = calAges[i]
    if (x > xMin && x < xMax) points.push([x, probabilities[i]])
  }
  points.push([xMax, densityAt(calAges, probabilities, xMax)])
  points.sort((a, b) => a[0] - b[0])
  // Closed down to baseline at both ends -- 'toself' fill draws the
  // straight line from the last point back to the first, so this only
  // needs the top edge; the two baseline corners close the shape.
  return {
    x: [xMin, ...points.map((p) => p[0]), xMax],
    y: [0, ...points.map((p) => p[1]), 0],
  }
}

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
  /** Overall min/max envelope, used only if the corresponding *Ranges prop below isn't provided. */
  hpd68?: [number, number]
  hpd95?: [number, number]
  /**
   * Full HPD breakdown as one or more disjoint ranges (calibration curves
   * are non-monotonic, so a posterior is often multimodal). When provided,
   * each range is drawn as its own shaded band instead of one band
   * spanning the full envelope, so the gap between separate peaks isn't
   * shown as if it were part of the HPD region. Only the first band in
   * each level gets its own legend entry (see plotData below) -- a
   * heavily fragmented posterior would otherwise print one "68% HPD
   * (x.x%)" row per disjoint segment.
   */
  hpd68Ranges?: HPDRange[]
  hpd95Ranges?: HPDRange[]
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
  hpd68,
  hpd95,
  hpd68Ranges,
  hpd95Ranges,
  sampleName,
  pointEstimates,
  showMean = true,
  showMedian = true,
  height = 400,
  dateFormat = DEFAULT_DATE_FORMAT,
}: PosteriorPlotProps) {
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

  // Prepare data for Plotly
  const plotData = useMemo(() => {
    if (!calAges || !probabilities || calAges.length === 0) {
      return []
    }

    const data: any[] = []

    // Main posterior distribution (filled area)
    data.push({
      x: calAges,
      y: probabilities,
      // Pre-formatted, because %{x} is always the cal BP the point is
      // plotted at -- the hover should read in the chosen convention.
      customdata: calAges.map((age) => formatCalendarAge(age, dateFormat)),
      fill: 'tozeroy',
      fillcolor: 'rgba(99, 110, 250, 0.2)',
      line: { color: 'rgb(99, 110, 250)', width: 2 },
      name: 'Posterior Distribution',
      hovertemplate: '<b>%{customdata}</b><br>Probability: %{y:.4f}<extra></extra>',
    })

    // HPD 95% band(s) (light shading), each following the curve itself
    // (see curveFillPolygon) rather than a fixed-height box. Calibration
    // curves are non-monotonic, so the HPD region is often two or more
    // disjoint ranges rather than one span -- when hpd95Ranges is
    // available, draw each range as its own band instead of one band
    // across the full min/max envelope, which would visually claim the
    // gap between peaks as part of the HPD region. Only the first band
    // gets showlegend: true, so a fragmented posterior still shows one
    // "95% HPD" legend row rather than one per segment; every band still
    // renders on the chart either way.
    if (hpd95Ranges && hpd95Ranges.length > 0) {
      hpd95Ranges.forEach((range, idx) => {
        const { x, y } = curveFillPolygon(calAges, probabilities, range.min, range.max)
        data.push({
          x,
          y,
          fill: 'toself',
          fillcolor: 'rgba(255, 127, 14, 0.25)',
          line: { color: 'rgba(255, 127, 14, 0)' },
          name: '95% HPD',
          hovertemplate: '',
          showlegend: idx === 0,
          legendgroup: 'hpd95',
        })
      })
    } else if (hpd95) {
      const { x, y } = curveFillPolygon(calAges, probabilities, hpd95[0], hpd95[1])
      data.push({
        x,
        y,
        fill: 'toself',
        fillcolor: 'rgba(255, 127, 14, 0.25)',
        line: { color: 'rgba(255, 127, 14, 0)' },
        name: '95% HPD',
        hovertemplate: '',
        showlegend: true,
      })
    }

    // HPD 68% band(s) (darker shading), same curve-following treatment.
    if (hpd68Ranges && hpd68Ranges.length > 0) {
      hpd68Ranges.forEach((range, idx) => {
        const { x, y } = curveFillPolygon(calAges, probabilities, range.min, range.max)
        data.push({
          x,
          y,
          fill: 'toself',
          fillcolor: 'rgba(44, 160, 44, 0.35)',
          line: { color: 'rgba(44, 160, 44, 0)' },
          name: '68% HPD',
          hovertemplate: '',
          showlegend: idx === 0,
          legendgroup: 'hpd68',
        })
      })
    } else if (hpd68) {
      const { x, y } = curveFillPolygon(calAges, probabilities, hpd68[0], hpd68[1])
      data.push({
        x,
        y,
        fill: 'toself',
        fillcolor: 'rgba(44, 160, 44, 0.35)',
        line: { color: 'rgba(44, 160, 44, 0)' },
        name: '68% HPD',
        hovertemplate: '',
        showlegend: true,
      })
    }

    // Vertical line at mean
    if (showMean && isNum(pointEstimates?.mean)) {
      data.push({
        x: [pointEstimates.mean, pointEstimates.mean],
        y: [0, Math.max(...probabilities)],
        mode: 'lines',
        line: { color: 'rgb(214, 39, 40)', width: 2, dash: 'dash' },
        name: `Mean: ${pointEstimates.mean.toFixed(0)} cal BP`,
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
        line: { color: 'rgb(148, 103, 189)', width: 2, dash: 'dot' },
        name: `Median: ${pointEstimates.median.toFixed(0)} cal BP`,
        hovertemplate: '',
        showlegend: true,
      })
    }

    return data
  }, [calAges, probabilities, hpd68, hpd95, hpd68Ranges, hpd95Ranges, pointEstimates, showMean, showMedian, dateFormat])

  // Tick positions stay in cal BP (where the data is); only the labels are
  // in the chosen convention. Null for cal BP, leaving Plotly to auto-tick.
  const ticks = useMemo(() => {
    if (!calAges || calAges.length === 0) return null
    return calendarAxisTicks(Math.min(...calAges), Math.max(...calAges), dateFormat)
  }, [calAges, dateFormat])

  const layout = useMemo(
    () => ({
      title: sampleName ? `Posterior Distribution: ${sampleName}` : 'Posterior Distribution',
      xaxis: {
        title: calendarAxisTitle(dateFormat),
        zeroline: false,
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
      },
      hovermode: 'x unified',
      plot_bgcolor: 'rgba(240, 240, 240, 0.5)',
      paper_bgcolor: 'white',
      margin: { l: 60, r: 20, t: 50, b: 60 },
      autosize: true,
    }),
    [sampleName, dateFormat, ticks]
  )

  const config = useMemo(
    () => ({
      responsive: true,
      displayModeBar: true,
      displaylogo: false,
      modeBarButtonsToRemove: ['lasso2d', 'select2d'],
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

  return (
    <Paper sx={{ p: 2, mb: 2 }}>
      <Plot
        data={plotData}
        layout={layout}
        config={config}
        useResizeHandler
        style={{ width: '100%', height: `${height}px`, minHeight: '300px' }}
      />
      {pointEstimates && (showMean || showMedian) && (
        <Box sx={{ mt: 2, p: 1, backgroundColor: 'grey.100', borderRadius: 1 }}>
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
