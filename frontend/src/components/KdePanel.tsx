// The kernel density estimates on the Results page: how a group calibration's
// dates, or each phase's modelled dates, are spread through time, as OxCal's
// KDE_Plot draws it -- the mean of many KDEs, each of one joint draw of the
// dates, with the ±1σ of those KDEs as a band. Computed by services/kde.py;
// what it means is in the manual.
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
import { usePlotColors, usePlotFont } from '@/theme/plotColors'
import { downloadPlotPng } from '@/utils/downloadPlot'
import type { KdeSummary } from '@/types'

interface KdePanelProps {
  estimates: KdeSummary[]
  dateFormat?: DateFormat
}

/** One card for every estimate the run produced: one for a group calibration,
 *  one per phase for a model. */
export default function KdePanel({ estimates, dateFormat = DEFAULT_DATE_FORMAT }: KdePanelProps) {
  return (
    <Paper sx={{ p: 3, mb: 3 }}>
      <Typography variant="h6" component="h2" gutterBottom>
        {estimates.length > 1 ? 'Kernel density estimates' : 'Kernel density estimate'}
      </Typography>
      {estimates.map((kde, index) => (
        <KdePlot key={kde.element_id ?? index} kde={kde} dateFormat={dateFormat} />
      ))}
    </Paper>
  )
}

interface KdePlotProps {
  kde: KdeSummary
  dateFormat?: DateFormat
  height?: number
}

function KdePlot({ kde, dateFormat = DEFAULT_DATE_FORMAT, height = 360 }: KdePlotProps) {
  const plot = usePlotColors()
  const plotFont = usePlotFont()
  const plotBox = useRef<HTMLDivElement | null>(null)

  const ages = useMemo(() => kde.distribution.map(([age]) => age), [kde])

  const data = useMemo((): Data[] => {
    const mean = kde.distribution.map(([, m]) => m)
    const upper = kde.distribution.map(([, m, sd]) => m + sd)
    const lower = kde.distribution.map(([, m, sd]) => Math.max(m - sd, 0))
    const labels = ages.map((age) => formatCalendarAge(age, dateFormat))
    return [
      // The band as one closed polygon: up the upper edge, back down the
      // lower, so it fills between them rather than down to zero.
      {
        x: [...ages, ...[...ages].reverse()],
        y: [...upper, ...[...lower].reverse()],
        fill: 'toself',
        fillcolor: plot.posteriorFill,
        line: { width: 0, color: 'rgba(0,0,0,0)' },
        mode: 'lines',
        name: '±1σ',
        hoverinfo: 'skip',
      },
      {
        x: ages,
        y: mean,
        customdata: labels,
        mode: 'lines',
        line: { color: plot.posteriorLine, width: 2 },
        name: 'KDE',
        hovertemplate: '<b>%{customdata}</b><br>Density: %{y:.5f}<extra></extra>',
      },
    ]
  }, [kde, ages, plot, dateFormat])

  // Tick positions stay in cal BP, where the data is; only the labels take
  // the chosen convention, as on every other calendar axis in the app.
  const ticks = useMemo(
    () => (ages.length ? calendarAxisTicks(Math.min(...ages), Math.max(...ages), dateFormat) : null),
    [ages, dateFormat]
  )

  const layout = useMemo(
    (): Partial<Layout> => ({
      dragmode: false as const,
      xaxis: {
        title: { text: calendarAxisTitle(dateFormat) },
        zeroline: false,
        gridcolor: plot.grid,
        // Oldest on the left, as every calibration plot in the app reads.
        autorange: 'reversed',
        ...(ticks ? { tickmode: 'array', tickvals: ticks.tickvals, ticktext: ticks.ticktext } : {}),
      },
      yaxis: {
        title: { text: 'Probability Density' },
        zeroline: false,
        gridcolor: plot.grid,
        rangemode: 'tozero',
      },
      hovermode: 'x unified',
      font: plotFont,
      plot_bgcolor: plot.surface,
      paper_bgcolor: plot.surface,
      margin: { l: 60, r: 20, t: 20, b: 60 },
      autosize: true,
    }),
    [plot, plotFont, dateFormat, ticks]
  )

  const config = useMemo(
    (): Partial<Config> => ({ responsive: true, displayModeBar: false as const, displaylogo: false }),
    []
  )

  const bandwidth = kde.bandwidth
  return (
    <Box sx={{ '& + &': { mt: 3 } }}>
      {kde.name && (
        <Typography variant="subtitle1" component="h3">
          {kde.name}
        </Typography>
      )}
      <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
        {kde.dates} dates, {kde.draws.toLocaleString()} draws. Kernel width{' '}
        {Math.round(bandwidth.median)} years (68%: {Math.round(bandwidth.range_68[0])}–
        {Math.round(bandwidth.range_68[1])}).
      </Typography>
      <Box ref={plotBox}>
        <Plot
          data={data}
          layout={layout}
          config={config}
          useResizeHandler
          style={{ width: '100%', height: `${height}px`, minHeight: '280px' }}
        />
      </Box>
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 0.5 }}>
        <Button
          size="small"
          startIcon={<DownloadIcon />}
          onClick={() => downloadPlotPng(plotBox.current, kde.name ? `kde-${kde.name}` : 'kde')}
        >
          Download PNG
        </Button>
      </Box>
    </Box>
  )
}
