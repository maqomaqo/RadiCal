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

    const bandHeight = probabilities.length > 0 ? Math.max(...probabilities) * 0.95 : 0

    // HPD 95% band(s) (light shading). Calibration curves are
    // non-monotonic, so the HPD region is often two or more disjoint
    // ranges rather than one span -- when hpd95Ranges is available, draw
    // each range as its own band instead of one band across the full
    // min/max envelope, which would visually claim the gap between peaks
    // as part of the HPD region. Only the first band gets showlegend:
    // true, so a fragmented posterior still shows one "95% HPD" legend
    // row rather than one per segment; every band still renders on the
    // chart either way.
    if (hpd95Ranges && hpd95Ranges.length > 0) {
      hpd95Ranges.forEach((range, idx) => {
        data.push({
          x: [range.min, range.min, range.max, range.max],
          y: [0, bandHeight, bandHeight, 0],
          fill: 'toself',
          fillcolor: 'rgba(255, 127, 14, 0.1)',
          line: { color: 'rgba(255, 127, 14, 0)' },
          name: '95% HPD',
          hovertemplate: '',
          showlegend: idx === 0,
          legendgroup: 'hpd95',
        })
      })
    } else if (hpd95) {
      data.push({
        x: [hpd95[0], hpd95[0], hpd95[1], hpd95[1]],
        y: [0, bandHeight, bandHeight, 0],
        fill: 'toself',
        fillcolor: 'rgba(255, 127, 14, 0.1)',
        line: { color: 'rgba(255, 127, 14, 0)' },
        name: '95% HPD',
        hovertemplate: '',
        showlegend: true,
      })
    }

    // HPD 68% band(s) (darker shading), same multi-range treatment.
    if (hpd68Ranges && hpd68Ranges.length > 0) {
      hpd68Ranges.forEach((range, idx) => {
        data.push({
          x: [range.min, range.min, range.max, range.max],
          y: [0, bandHeight, bandHeight, 0],
          fill: 'toself',
          fillcolor: 'rgba(44, 160, 44, 0.2)',
          line: { color: 'rgba(44, 160, 44, 0)' },
          name: '68% HPD',
          hovertemplate: '',
          showlegend: idx === 0,
          legendgroup: 'hpd68',
        })
      })
    } else if (hpd68) {
      data.push({
        x: [hpd68[0], hpd68[0], hpd68[1], hpd68[1]],
        y: [0, bandHeight, bandHeight, 0],
        fill: 'toself',
        fillcolor: 'rgba(44, 160, 44, 0.2)',
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
        <Box sx={{ mt: 2, p: 1, backgroundColor: '#f5f5f5', borderRadius: 1 }}>
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
