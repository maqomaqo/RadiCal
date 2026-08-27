import { useMemo } from 'react'
import { Box, Typography } from '@mui/material'
import Plotly from 'plotly.js-dist-min'
import createPlotlyComponent from 'react-plotly.js/factory'
import {
  DateFormat,
  calendarAxisTicks,
  calendarAxisTitle,
  formatCalendarAge,
} from '@/utils/dateFormat'
import { toUnitArea, trimTails, type Distribution } from '@/utils/distribution'
import { usePlotColors } from '@/theme/plotColors'
import type { RunStyle } from '@/theme/compareColors'

// Bound to the packaged plotly.js rather than a CDN global, as the other plots
// are -- see PosteriorPlot for why.
const Plot = createPlotlyComponent(Plotly)

/**
 * The distributions behind one row of the shift plot, drawn over each other.
 *
 * This exists because an HPD bar is a summary that misleads most exactly where
 * a comparison matters most. On a calibration plateau a date's posterior is
 * routinely two or three separate lobes, and a run that "moved the date 140
 * years" may in fact have moved probability MASS from one lobe to another --
 * a different claim about the chronology, and one no interval bar can make.
 * The bars rank the dates; this says what actually happened to the one you
 * opened.
 *
 * Every series is normalised to unit area before drawing, so the comparison is
 * by shape and spread rather than by whatever scale each run's distribution
 * arrived on: a tighter posterior is correctly TALLER here, not just narrower.
 */

interface CompareSeries {
  name: string
  distribution: Distribution
  style: RunStyle
}

interface ComparePosteriorPlotProps {
  sampleName: string
  series: CompareSeries[]
  /**
   * The date's unconstrained calibration -- what it says with no model on it
   * at all -- drawn in neutral grey behind the runs. Absent for a comparison
   * of analyses that report none, and never inferred from one of the runs.
   */
  priorDistribution?: Distribution | null
  dateFormat: DateFormat
  height?: number
}

export default function ComparePosteriorPlot({
  sampleName,
  series,
  priorDistribution,
  dateFormat,
  height = 280,
}: ComparePosteriorPlotProps) {
  const plot = usePlotColors()

  const { traces, range } = useMemo(() => {
    const drawn: Array<{ ages: number[]; probs: number[]; name: string; line: string; fill: string }> = []

    if (priorDistribution && priorDistribution.length > 1) {
      const trimmed = trimTails(toUnitArea(priorDistribution))
      drawn.push({
        ages: trimmed.ages,
        probs: trimmed.probs,
        name: 'Unconstrained',
        line: plot.priorLine,
        fill: plot.priorFill,
      })
    }

    for (const item of series) {
      if (!item.distribution || item.distribution.length < 2) continue
      const trimmed = trimTails(toUnitArea(item.distribution))
      drawn.push({
        ages: trimmed.ages,
        probs: trimmed.probs,
        name: item.name,
        line: item.style.line,
        fill: item.style.fillOuter,
      })
    }

    let min = Infinity
    let max = -Infinity
    for (const item of drawn) {
      for (const age of item.ages) {
        if (age < min) min = age
        if (age > max) max = age
      }
    }

    return {
      traces: drawn.map((item) => ({
        x: item.ages,
        y: item.probs,
        type: 'scatter' as const,
        mode: 'lines' as const,
        name: item.name,
        line: { color: item.line, width: 1.6 },
        fill: 'tozeroy' as const,
        fillcolor: item.fill,
        hovertemplate: `%{customdata}<extra>${item.name}</extra>`,
        customdata: item.ages.map((age) => formatCalendarAge(age, dateFormat)),
      })),
      range: Number.isFinite(min) && Number.isFinite(max) && max > min
        ? ([max, min] as [number, number])
        : null,
    }
  }, [series, priorDistribution, dateFormat, plot])

  if (traces.length === 0) {
    return (
      <Typography variant="body2" color="textSecondary">
        No distributions were stored for {sampleName}, so there is nothing to draw behind the
        intervals above.
      </Typography>
    )
  }

  // Ticks want the window the right way round; `range` is descending because
  // the axis is.
  const ticks = range ? calendarAxisTicks(range[1], range[0], dateFormat) : null

  return (
    <Box>
      <Plot
        data={traces}
        layout={{
          dragmode: false as const,
          xaxis: {
            title: { text: calendarAxisTitle(dateFormat), standoff: 8 },
            zeroline: false,
            gridcolor: plot.grid,
            // Oldest on the left, as everywhere else. The explicit range is
            // already descending, so no reversal on top of it.
            ...(range ? { range, autorange: false as const } : { autorange: 'reversed' as const }),
            ...(ticks
              ? { tickmode: 'array' as const, tickvals: ticks.tickvals, ticktext: ticks.ticktext }
              : {}),
          },
          yaxis: {
            // The height is a probability density with every series scaled to
            // unit area: real, but read by comparison rather than off a scale,
            // and a labelled axis would invite reading it as an absolute.
            showticklabels: false,
            showgrid: false,
            zeroline: false,
            fixedrange: true,
            rangemode: 'tozero' as const,
          },
          hovermode: 'closest' as const,
          plot_bgcolor: plot.surface,
          paper_bgcolor: plot.surface,
          margin: { l: 20, r: 20, t: 10, b: 76 },
          legend: { orientation: 'h' as const, y: -0.34, yanchor: 'top' as const },
          autosize: true,
        }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: '100%', height }}
        useResizeHandler
      />
    </Box>
  )
}
