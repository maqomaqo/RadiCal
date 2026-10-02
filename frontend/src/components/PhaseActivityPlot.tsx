// Which phases were in progress when: one strip per phase on a shared calendar
// axis, shaded by the probability the phase was running in each year. Computed
// from the joint draws by services/intervals.evaluate_phase_activity. Along a
// Sequence the strips succeed one another; across parallel branches they show
// the overlap the difference table reports pair by pair. The PDF report draws
// the same figure (pdf_report._phase_activity_figure).
import { useMemo, useRef } from 'react'
import { Box, Button, Typography } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
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
import { PHASE_GREEN } from '@/theme/diagramColors'
import { usePlotColors, usePlotFont } from '@/theme/plotColors'
import { downloadPlotPng } from '@/utils/downloadPlot'
import type { PhaseActivity } from '@/types'

interface Props {
  activity: PhaseActivity
  dateFormat?: DateFormat
}

const ROW_HEIGHT = 26

/**
 * Probability to ink strength. Steeper than linear at the low end, because a
 * phase whose ends are uncertain is rarely more than 30-40% likely to be in
 * progress in any one year, and a linear ramp left those strips too faint to
 * read against each other. Still monotonic, so darker is always more
 * probable, and the hover gives the number itself.
 */
const RAMP: Array<[number, number]> = [
  [0, 0],
  [0.02, 0.08],
  [0.1, 0.3],
  [0.3, 0.6],
  [0.6, 0.85],
  [1, 1],
]

export default function PhaseActivityPlot({ activity, dateFormat = DEFAULT_DATE_FORMAT }: Props) {
  const theme = useTheme()
  const plot = usePlotColors()
  const plotFont = usePlotFont()
  const plotBox = useRef<HTMLDivElement | null>(null)
  const { rows } = activity

  const ages = useMemo(
    () => (rows[0]?.values ?? []).map((_, i) => activity.start - i * activity.step),
    [activity, rows]
  )

  const data = useMemo((): Data[] => {
    const ageLabels = ages.map((age) => formatCalendarAge(age, dateFormat))
    // Two traces so a single date, whose row is a relative density rather
    // than a probability of being in progress, is drawn in the dates' own
    // colour. Each trace leaves the other's rows empty.
    const trace = (relative: boolean, color: string, measure: string): Data => ({
      type: 'heatmap',
      x: ages,
      y: rows.map((_, i) => i),
      z: rows.map((row) => (Boolean(row.relative) === relative ? row.values : row.values.map(() => null))),
      customdata: rows.map((row) => ageLabels.map((label) => [row.name, label])) as unknown as number[][],
      zmin: 0,
      zmax: 1,
      ygap: 3,
      showscale: false,
      colorscale: RAMP.map(([at, strength]): [number, string] => [at, alpha(color, strength)]),
      hovertemplate: `<b>%{customdata[0]}</b><br>%{customdata[1]}<br>${measure}<extra></extra>`,
    })
    return [
      trace(false, PHASE_GREEN, '%{z:.0%} probability in progress'),
      trace(true, theme.palette.primary.main, 'relative probability %{z:.2f}'),
    ]
  }, [rows, ages, dateFormat, theme.palette.primary.main])

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
        // Oldest on the left, as every calendar axis in the app reads.
        autorange: 'reversed',
        ...(ticks ? { tickmode: 'array', tickvals: ticks.tickvals, ticktext: ticks.ticktext } : {}),
      },
      yaxis: {
        // Indices, not names: two phases may share a name, and a category
        // axis would draw them on one row.
        tickmode: 'array',
        tickvals: rows.map((_, i) => i),
        ticktext: rows.map((row) =>
          row.dates
            ? `${row.name.trim()} (${row.dates === 1 ? '1 date' : `${row.dates} dates`})`
            : row.name.trim()
        ),
        autorange: 'reversed',
        showgrid: false,
        zeroline: false,
        automargin: true,
      },
      font: plotFont,
      plot_bgcolor: plot.surface,
      paper_bgcolor: plot.surface,
      margin: { l: 20, r: 20, t: 10, b: 50 },
      autosize: true,
    }),
    [plot, plotFont, dateFormat, ticks, rows]
  )

  const config = useMemo(
    (): Partial<Config> => ({ responsive: true, displayModeBar: false as const, displaylogo: false }),
    []
  )

  const height = 70 + ROW_HEIGHT * rows.length
  return (
    <Box>
      <Box ref={plotBox}>
        <Plot
          data={data}
          layout={layout}
          config={config}
          useResizeHandler
          style={{ width: '100%', height: `${height}px` }}
        />
      </Box>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 2, mt: 0.5 }}>
        <Typography variant="body2" color="textSecondary" sx={{ flexGrow: 1 }}>
          Shading is the probability each phase was in progress. A single date shows its own
          probability, relative to its peak.
        </Typography>
        <Button
          size="small"
          startIcon={<DownloadIcon />}
          onClick={() => downloadPlotPng(plotBox.current, 'phase-overlap')}
          sx={{ flexShrink: 0 }}
        >
          Download PNG
        </Button>
      </Box>
    </Box>
  )
}
