import { useMemo } from 'react'
import { Box, Typography } from '@mui/material'
import Plot from '@/components/Plot'
import type { ComparedRow } from '@/utils/comparePairing'
import { POOR_AGREEMENT } from '@/utils/diagnostics'
import { usePlotColors } from '@/theme/plotColors'
import type { RunStyle } from '@/theme/compareColors'


/**
 *
 * The agreement index says how far a run had to move a date away from its own
 * unconstrained calibration to satisfy the model. Plotted against the
 * reference run's, the diagonal is "this date fits no better and no worse than
 * before".
 *
 * This is the half of a comparison the shift plot cannot show. There, a date
 * that a second phase pulled 140 years and halved the interval on reads as a
 * clean win; here the same date can be sitting well below the diagonal,
 * because the precision was bought by overruling the date rather than by
 * learning something about it. Both figures are true and they answer different
 * questions, which is why the page carries both.
 */

interface AgreementScatterProps {
  runs: Array<{ id: string; name: string }>
  runStyles: RunStyle[]
  rows: ComparedRow[]
  /** Which runs report an agreement index at all. */
  reports: boolean[]
}

export default function AgreementScatter({
  runs,
  runStyles,
  rows,
  reports,
}: AgreementScatterProps) {
  const plot = usePlotColors()

  const { traces, bound } = useMemo(() => {
    const series: Array<Record<string, unknown>> = []
    const values: number[] = []

    for (let runIndex = 1; runIndex < runs.length; runIndex++) {
      if (!reports[0] || !reports[runIndex]) continue
      const x: number[] = []
      const y: number[] = []
      const labels: string[] = []
      for (const row of rows) {
        const reference = row.cells[0]?.agreementIndex
        const other = row.cells[runIndex]?.agreementIndex
        if (reference == null || other == null) continue
        x.push(reference)
        y.push(other)
        labels.push(row.label)
        values.push(reference, other)
      }
      if (x.length === 0) continue
      series.push({
        x,
        y,
        text: labels,
        type: 'scatter',
        mode: 'markers',
        name: runs[runIndex].name,
        marker: {
          size: 9,
          color: runStyles[runIndex].fillInner,
          line: { color: runStyles[runIndex].line, width: 1 },
        },
        hovertemplate: `%{text}<br>${runs[0].name}: %{x:.0f}<br>${runs[runIndex].name}: %{y:.0f}<extra></extra>`,
      })
    }

    if (values.length === 0) return { traces: [], bound: null }

    const low = Math.max(0, Math.min(...values) - 8)
    const high = Math.max(...values) + 8
    return { traces: series, bound: [low, high] as [number, number] }
  }, [rows, runs, runStyles, reports])

  if (traces.length === 0 || !bound) {
    return (
      <Typography variant="body2" color="textSecondary">
        An agreement index needs a model for a date to agree or disagree with, and at least two of
        these runs to have computed one. Nothing here to plot.
      </Typography>
    )
  }

  const withGuides = [
    {
      x: bound,
      y: bound,
      type: 'scatter' as const,
      mode: 'lines' as const,
      name: 'unchanged',
      line: { color: plot.guide, width: 1, dash: 'dash' as const },
      hoverinfo: 'skip' as const,
    },
    ...traces,
  ]

  const threshold = (axis: 'x' | 'y') => ({
    type: 'line' as const,
    xref: axis === 'x' ? ('x' as const) : ('paper' as const),
    yref: axis === 'x' ? ('paper' as const) : ('y' as const),
    x0: axis === 'x' ? POOR_AGREEMENT : 0,
    x1: axis === 'x' ? POOR_AGREEMENT : 1,
    y0: axis === 'x' ? 0 : POOR_AGREEMENT,
    y1: axis === 'x' ? 1 : POOR_AGREEMENT,
    line: { color: plot.boundaryLine, width: 1, dash: 'dot' as const },
  })

  return (
    <Box sx={{ maxWidth: 560 }}>
      <Plot
        data={withGuides}
        layout={{
          dragmode: false as const,
          xaxis: {
            title: { text: `Agreement under ${runs[0].name}`, standoff: 8 },
            range: bound,
            zeroline: false,
            gridcolor: plot.grid,
          },
          yaxis: {
            title: { text: 'Agreement under the compared run', standoff: 8 },
            range: bound,
            zeroline: false,
            gridcolor: plot.grid,
          },
          shapes: [threshold('x'), threshold('y')],
          annotations: [
            {
              x: POOR_AGREEMENT,
              y: 1,
              xref: 'x' as const,
              yref: 'paper' as const,
              text: `A = ${POOR_AGREEMENT}`,
              showarrow: false,
              yanchor: 'bottom' as const,
              font: { size: 10, color: plot.boundaryLine },
            },
          ],
          plot_bgcolor: plot.surface,
          paper_bgcolor: plot.surface,
          // Chosen so the plot area comes out square at the box's full width
          // (560 - 80 across, 600 - 120 down), which puts the diagonal at 45
          // degrees. Plotly's scaleanchor would do it by stretching the x
          // RANGE to match the pixels instead, which left the points in one
          // corner of an axis running to 250.
          margin: { l: 60, r: 20, t: 20, b: 100 },
          legend: { orientation: 'h' as const, y: -0.22, yanchor: 'top' as const },
          autosize: true,
        }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: '100%', height: 600 }}
        useResizeHandler
      />
      <Typography variant="caption" color="textSecondary" component="div" sx={{ maxWidth: '78ch' }}>
        Above the dashed diagonal, the compared run fits the date better than the reference does;
        below it, worse. The dotted rules mark the threshold of {POOR_AGREEMENT}, under which a
        date is conventionally treated as not belonging to the model it was placed in.
      </Typography>
    </Box>
  )
}
