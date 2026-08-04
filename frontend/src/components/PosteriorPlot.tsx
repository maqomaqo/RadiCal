import React, { useMemo } from 'react'
import { Box, Paper, Typography } from '@mui/material'
import type { HPDRange } from '@/utils/dateFormat'

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
   * shown as if it were part of the HPD region.
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
   * Optional formatter for displaying a cal BP value in the Point
   * Estimates box (e.g. converting to BC/AD or BCE/CE). The chart itself
   * always plots in cal BP, since that's the unit the calibration curve
   * and posterior are computed in -- this only affects the text summary.
   * Defaults to `${age.toFixed(0)} cal BP` if not provided.
   */
  formatAge?: (age: number) => string
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
  formatAge = (age: number) => (Number.isFinite(age) ? `${age.toFixed(0)} cal BP` : 'N/A'),
}: PosteriorPlotProps) {
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
      return { data: [], layout: {} }
    }

    const data: any[] = []

    // Main posterior distribution (filled area)
    data.push({
      x: calAges,
      y: probabilities,
      fill: 'tozeroy',
      fillcolor: 'rgba(99, 110, 250, 0.2)',
      line: { color: 'rgb(99, 110, 250)', width: 2 },
      name: 'Posterior Distribution',
      hovertemplate: '<b>%{x:.0f} cal BP</b><br>Probability: %{y:.4f}<extra></extra>',
    })

    const bandHeight = probabilities.length > 0 ? Math.max(...probabilities) * 0.95 : 0

    // HPD 95% band(s) (light shading). Calibration curves are
    // non-monotonic, so the HPD region is often two or more disjoint
    // ranges rather than one span -- when hpd95Ranges is available, draw
    // each range as its own band (labeled with its probability share)
    // instead of one band across the full min/max envelope, which would
    // visually claim the gap between peaks as part of the HPD region.
    if (hpd95Ranges && hpd95Ranges.length > 0) {
      hpd95Ranges.forEach((range) => {
        data.push({
          x: [range.min, range.min, range.max, range.max],
          y: [0, bandHeight, bandHeight, 0],
          fill: 'toself',
          fillcolor: 'rgba(255, 127, 14, 0.1)',
          line: { color: 'rgba(255, 127, 14, 0)' },
          name: `95% HPD (${range.probability_percent.toFixed(1)}%)`,
          hovertemplate: '',
          showlegend: true,
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
      hpd68Ranges.forEach((range) => {
        data.push({
          x: [range.min, range.min, range.max, range.max],
          y: [0, bandHeight, bandHeight, 0],
          fill: 'toself',
          fillcolor: 'rgba(44, 160, 44, 0.2)',
          line: { color: 'rgba(44, 160, 44, 0)' },
          name: `68% HPD (${range.probability_percent.toFixed(1)}%)`,
          hovertemplate: '',
          showlegend: true,
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

    return { data }
  }, [calAges, probabilities, hpd68, hpd95, hpd68Ranges, hpd95Ranges, pointEstimates, showMean, showMedian])

  // Use dynamic import for Plotly to avoid SSR issues
  const [plotlyLoaded, setPlotlyLoaded] = React.useState(false)
  const plotContainerId = `posterior-plot-${Math.random().toString(36).substr(2, 9)}`

  React.useEffect(() => {
    if (typeof window === 'undefined') return

    // Load Plotly from CDN if not already loaded
    if (!window.Plotly) {
      const script = document.createElement('script')
      script.src = 'https://cdn.plot.ly/plotly-latest.min.js'
      script.onload = () => setPlotlyLoaded(true)
      document.body.appendChild(script)
    } else {
      setPlotlyLoaded(true)
    }
  }, [])

  React.useEffect(() => {
    if (!plotlyLoaded || !window.Plotly || plotData.data.length === 0) return

    const container = document.getElementById(plotContainerId)
    if (!container) return

    const layout = {
      title: sampleName ? `Posterior Distribution: ${sampleName}` : 'Posterior Distribution',
      xaxis: {
        title: 'Calendar Age (years BP)',
        zeroline: false,
      },
      yaxis: {
        title: 'Probability Density',
        zeroline: false,
      },
      hovermode: 'x unified',
      plot_bgcolor: 'rgba(240, 240, 240, 0.5)',
      paper_bgcolor: 'white',
      margin: { l: 60, r: 20, t: 50, b: 60 },
    }

    const config = {
      responsive: true,
      displayModeBar: true,
      displaylogo: false,
      modeBarButtonsToRemove: ['lasso2d', 'select2d'],
    }

    window.Plotly.newPlot(plotContainerId, plotData.data, layout, config)
  }, [plotlyLoaded, plotData, plotContainerId, sampleName])

  if (!plotData.data || plotData.data.length === 0) {
    return (
      <Paper sx={{ p: 2 }}>
        <Typography color="error">No data to display</Typography>
      </Paper>
    )
  }

  return (
    <Paper sx={{ p: 2, mb: 2 }}>
      <Box
        id={plotContainerId}
        sx={{
          width: '100%',
          height: `${height}px`,
          minHeight: '300px',
        }}
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

// Extend window type for Plotly
declare global {
  interface Window {
    Plotly?: any
  }
}
