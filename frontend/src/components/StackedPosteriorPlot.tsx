import { useMemo } from 'react'
import { Paper, Typography, useMediaQuery, useTheme } from '@mui/material'
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
import { stackedPlotGeometry } from '@/utils/stackedPlotGeometry'
import { toUnitArea } from '@/utils/distribution'
import { usePlotColors } from '@/theme/plotColors'

// Same binding as PosteriorPlot -- see the note there for why the factory is
// bound to the packaged plotly.js rather than a CDN global.
const Plot = createPlotlyComponent(Plotly)

/**
 * One row of the stacked plot: a modelled posterior with its HPD region,
 * carrying enough structure context to label and group it.
 */
export interface StackedSeries {
  key: string
  kind: 'date' | 'boundary'
  title: string
  /** Where it sits in the structure ("Site > Phase J"); null if unplaced. */
  path: string | null
  distribution?: Array<[number, number]>
  hpd68?: [number, number]
  hpd95?: [number, number]
  hpd68Ranges?: HPDRange[]
  hpd95Ranges?: HPDRange[]
  /** Same date's plain calibration with no stratigraphic constraints -- see showPrior below. */
  priorDistribution?: Array<[number, number]>
  pointEstimates: { mean?: number; median?: number; std?: number }
}

interface StackedPosteriorPlotProps {
  /** Oldest first -- drawn top to bottom in that order, as OxCal lists a model. */
  series: StackedSeries[]
  showMean?: boolean
  showMedian?: boolean
  /**
   * Overlay each date's unconstrained calibration as a light grey shadow
   * behind its modelled posterior, scaled into the same row so the two are
   * directly comparable. Off by default -- a comparison aid, not something
   * every reading of the stack needs.
   */
  showPrior?: boolean
  dateFormat?: DateFormat
}

// Row geometry, in y-axis data units where one row is exactly 1.0 tall.
// The curve occupies the upper part of its row and the two HPD bars sit in
// the gap beneath it, so no row can ever paint into its neighbour.
const CURVE_HEIGHT = 0.78
const HPD95_BAR: [number, number] = [-0.09, -0.03]
const HPD68_BAR: [number, number] = [-0.19, -0.13]
// Room for the row names, and the offset the phase labels are placed at
// within it. On a phone the wide version took half the available width and
// left the curves squeezed into the rest, so it shrinks with the viewport --
// sample codes are short, and a truncated name still identifies its row
// against the table above.
const MARGIN_LEFT = 150
const MARGIN_LEFT_NARROW = 84

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/**
 * Trims a distribution's outer tails, where the density is indistinguishable
 * from zero at row scale -- left in, they draw as a flat rule running the
 * whole width of the row rather than as a curve. Only the OUTER tails go: a
 * multimodal posterior's flat gap between peaks is a real feature of the
 * calibration, not padding, so the slice stays contiguous.
 */
function trimTails(distribution: Array<[number, number]>) {
  const peak = Math.max(...distribution.map(([, p]) => p))
  const significant = distribution
    .map(([, p], i) => (p >= peak * 0.002 ? i : -1))
    .filter((i) => i >= 0)
  const from = significant.length > 0 ? Math.max(0, significant[0] - 1) : 0
  const to =
    significant.length > 0
      ? Math.min(distribution.length - 1, significant[significant.length - 1] + 1)
      : distribution.length - 1
  const visible = distribution.slice(from, to + 1)
  return { ages: visible.map(([age]) => age), probs: visible.map(([, p]) => p), peak }
}

/** The row's own baseline. Row 0 (the oldest) sits at the top. */
const baselineFor = (index: number) => -index

/** A filled rectangle in data coordinates, as a closed polygon trace. */
function bar(
  x0: number,
  x1: number,
  yBase: number,
  span: [number, number],
  color: string,
  name: string,
  showlegend: boolean,
  legendgroup: string
) {
  return {
    x: [x0, x0, x1, x1],
    y: [yBase + span[0], yBase + span[1], yBase + span[1], yBase + span[0]],
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

/**
 * Every posterior in one figure, one row each, sharing a single calendar
 * axis -- the OxCal multiplot idiom.
 *
 * The point of the shared axis is comparison: whether two dates overlap, and
 * by how much, is the question a sequence is built to answer, and it cannot
 * be read off a stack of separate charts each auto-ranged to its own data.
 *
 * Each row is scaled to its OWN peak, so heights are not comparable between
 * rows -- a tall narrow curve is a precise date, not a more probable one.
 * That is what OxCal does too, and the alternative (a shared density scale)
 * would flatten every precise date into an unreadable spike.
 */
export default function StackedPosteriorPlot({
  series,
  showMean = false,
  showMedian = false,
  showPrior = false,
  dateFormat = DEFAULT_DATE_FORMAT,
}: StackedPosteriorPlotProps) {
  const theme = useTheme()
  const plot = usePlotColors()
  const narrow = useMediaQuery(theme.breakpoints.down('sm'))
  const marginLeft = narrow ? MARGIN_LEFT_NARROW : MARGIN_LEFT

  const drawable = useMemo(
    () => series.filter((s) => s.distribution && s.distribution.length > 0),
    [series]
  )

  const plotData = useMemo(() => {
    const data: any[] = []
    // One legend entry per kind of mark, taken from whichever row happens to
    // carry it first -- otherwise a 15-date model prints 15 identical
    // "95% HPD" rows down the side of the figure.
    const legendUsed = new Set<string>()
    const claimLegend = (key: string) => {
      if (legendUsed.has(key)) return false
      legendUsed.add(key)
      return true
    }

    drawable.forEach((s, index) => {
      const yBase = baselineFor(index)
      // Both series are put on unit area before anything is measured off
      // them. They arrive in different units -- the posterior is the solver's
      // density, the prior is the curve's probabilities downsampled by index
      // without rescaling -- so comparing their raw peaks was comparing two
      // different quantities. See utils/distribution.ts.
      const { ages, probs, peak } = trimTails(toUnitArea(s.distribution!))
      const priorPoints =
        showPrior && s.priorDistribution && s.priorDistribution.length > 0
          ? trimTails(toUnitArea(s.priorDistribution))
          : null

      // One scale for the row, taken from whichever of the two is taller, so
      // neither can paint outside its own band. Scaling by the posterior
      // alone let a prior that is sharper than its posterior -- an outlier
      // the model has spread out, or a date on a steep part of the curve --
      // overflow into the row above.
      //
      // Equal area plus a shared scale is what makes the overlay readable:
      // shorter and wider genuinely means the model narrowed this date,
      // rather than meaning the two were normalised differently.
      const rowPeak = Math.max(peak, priorPoints ? priorPoints.peak : 0)
      const scale = rowPeak > 0 ? CURVE_HEIGHT / rowPeak : 0
      const isBoundary = s.kind === 'boundary'

      // HPD bars first, so the curve draws over them rather than under.
      // Each disjoint range gets its own bar: a multimodal posterior's HPD
      // is not the span between its outermost peaks, and drawing it as one
      // bar would claim the gap between them.
      const ranges95: Array<[number, number]> =
        s.hpd95Ranges && s.hpd95Ranges.length > 0
          ? s.hpd95Ranges.map((r) => [r.min, r.max])
          : s.hpd95
          ? [s.hpd95]
          : []
      const ranges68: Array<[number, number]> =
        s.hpd68Ranges && s.hpd68Ranges.length > 0
          ? s.hpd68Ranges.map((r) => [r.min, r.max])
          : s.hpd68
          ? [s.hpd68]
          : []

      ranges95.forEach(([min, max]) =>
        data.push(
          bar(min, max, yBase, HPD95_BAR, plot.hpd95, '95% HPD', claimLegend('hpd95'), 'hpd95')
        )
      )
      ranges68.forEach(([min, max]) =>
        data.push(
          bar(min, max, yBase, HPD68_BAR, plot.hpd68, '68% HPD', claimLegend('hpd68'), 'hpd68')
        )
      )

      // The prior (unmodelled) shadow, scaled by this row's OWN scale factor
      // rather than its own peak -- so it reads directly against the
      // posterior above it: shorter and wider means the model narrowed this
      // date, not that the prior is somehow smaller. Drawn before the
      // posterior so the posterior's fill sits on top of it.
      if (priorPoints) {
        const priorTrimmed = priorPoints
        const priorY = priorTrimmed.probs.map((p) => yBase + p * scale)
        data.push({
          x: [priorTrimmed.ages[0], ...priorTrimmed.ages, priorTrimmed.ages[priorTrimmed.ages.length - 1]],
          y: [yBase, ...priorY, yBase],
          fill: 'toself',
          fillcolor: plot.priorFill,
          line: { width: 1, color: plot.priorLine },
          mode: 'lines',
          name: 'Prior (unmodelled)',
          legendgroup: 'prior',
          showlegend: claimLegend('prior'),
          hoverinfo: 'skip',
        })
      }

      // The density as one closed-polygon trace, fill and stroke together --
      // same treatment as the prior shadow above, so its legend entry shows
      // the same fill+line swatch instead of a bare line. The stroke does
      // trace the flat closing edge at the row's baseline along with the
      // curve itself, but at this width and alongside the HPD bars already
      // sitting there it doesn't read as a range bar in practice.
      const curveY = probs.map((p) => yBase + p * scale)
      const hoverText = ages.map(
        (age, i) => `${formatCalendarAge(age, dateFormat)}<br>Probability: ${probs[i].toFixed(4)}`
      )
      data.push({
        x: [ages[0], ...ages, ages[ages.length - 1]],
        y: [yBase, ...curveY, yBase],
        customdata: [hoverText[0], ...hoverText, hoverText[hoverText.length - 1]],
        fill: 'toself',
        fillcolor: isBoundary ? plot.boundaryFill : plot.posteriorFill,
        line: {
          color: isBoundary ? plot.boundaryLine : plot.posteriorLine,
          width: 1.5,
          dash: isBoundary ? 'dot' : 'solid',
        },
        mode: 'lines',
        name: isBoundary ? 'Boundary (modelled transition)' : 'Posterior (modelled)',
        legendgroup: isBoundary ? 'boundary' : 'posterior',
        showlegend: claimLegend(isBoundary ? 'boundary' : 'posterior'),
        // Hover on the curve's own points, not on the area it encloses. A
        // filled trace defaults to 'points+fills', and the fill hover is a
        // separate mechanism that ignores hovertemplate: it prints the
        // trace name, in the fill colour, alongside the real label -- which
        // read as the row's name escaping the tooltip box.
        hoveron: 'points',
        hovertemplate: `<b>${s.title}</b><br>%{customdata}<extra></extra>`,
      })

      // Point estimates as a tick within the row, not a line across the
      // figure: at this density a full-height rule per date would be the
      // only thing anyone could see.
      const marker = (value: number, color: string, dash: string, name: string, key: string) =>
        data.push({
          x: [value, value],
          y: [yBase, yBase + CURVE_HEIGHT],
          mode: 'lines',
          line: { color, width: 1.5, dash },
          name,
          legendgroup: key,
          showlegend: claimLegend(key),
          hoverinfo: 'skip',
        })
      if (showMean && isNum(s.pointEstimates.mean)) {
        marker(s.pointEstimates.mean, plot.mean, 'dash', 'Mean', 'mean')
      }
      if (showMedian && isNum(s.pointEstimates.median)) {
        marker(s.pointEstimates.median, plot.median, 'dot', 'Median', 'median')
      }
    })

    return data
  }, [drawable, plot, showMean, showMedian, showPrior, dateFormat])

  // Phase groupings: a light rule where the dates change phase, with the
  // phase named once beside its first date. The table above says the same
  // thing in words; the point here is that a reader scanning the figure can
  // see where one phase ends without counting rows against it.
  //
  // Driven by the DATE rows only. Every boundary has a path of its own (it
  // is the transition between two phases, not a member of either), so
  // grouping on all rows alike would break the run at every boundary and
  // print a label against nearly every row -- the noise it exists to remove.
  const { shapes, annotations } = useMemo(() => {
    const shapes: any[] = []
    const annotations: any[] = []
    // Last segment only: the full path repeats the site name on every row.
    const phaseOf = (s: StackedSeries) =>
      s.kind === 'date' && s.path ? s.path.split('>').pop()!.trim() : null

    let currentPhase: string | null = null
    drawable.forEach((s, index) => {
      const phase = phaseOf(s)
      if (!phase || phase === currentPhase) return
      currentPhase = phase
      if (index > 0) {
        shapes.push({
          type: 'line',
          xref: 'paper',
          x0: 0,
          x1: 1,
          yref: 'y',
          y0: baselineFor(index) + 0.5,
          y1: baselineFor(index) + 0.5,
          line: { color: 'rgba(0,0,0,0.12)', width: 1 },
        })
      }
      // In the left margin with the row names, not inside the plot: at the
      // left edge of the axis it landed on whatever curve happened to reach
      // that far and read as a stray character.
      annotations.push({
        xref: 'paper',
        x: 0,
        xanchor: 'left',
        xshift: -marginLeft + 4,
        yref: 'y',
        y: baselineFor(index) + 0.5,
        yanchor: 'middle',
        text: phase,
        showarrow: false,
        font: { size: 11, color: 'rgba(0,0,0,0.75)' },
      })
    })
    return { shapes, annotations }
  }, [drawable, marginLeft])

  // The axis is set from the HPD regions, not from the full extent of every
  // curve. A posterior's tails run far wider than anything it says: on a real
  // sequence they stretched the shared axis to three times the span the dates
  // actually occupy, squeezing every curve into the middle third. Padding
  // keeps the tails visible where they matter, and Autoscale on the modebar
  // still restores the full extent.
  const xRange = useMemo(() => {
    const bounds: number[] = []
    drawable.forEach((s) => {
      if (s.hpd95Ranges && s.hpd95Ranges.length > 0) {
        s.hpd95Ranges.forEach((r) => bounds.push(r.min, r.max))
      } else if (s.hpd95) {
        bounds.push(s.hpd95[0], s.hpd95[1])
      }
    })
    if (bounds.length === 0) return null
    const min = Math.min(...bounds)
    const max = Math.max(...bounds)
    const pad = Math.max((max - min) * 0.35, 25)
    // Reversed axis: Plotly takes the range in axis order, so the older
    // (larger cal BP) end comes first.
    return [max + pad, min - pad]
  }, [drawable])

  // Ticks span whatever the axis actually shows, so a clipped view doesn't
  // label years that were left off it.
  const ticks = useMemo(() => {
    if (xRange) return calendarAxisTicks(xRange[1], xRange[0], dateFormat)
    const all = drawable.flatMap((s) => s.distribution!.map(([age]) => age))
    if (all.length === 0) return null
    return calendarAxisTicks(Math.min(...all), Math.max(...all), dateFormat)
  }, [drawable, dateFormat, xRange])

  // Pixel budget for everything below the axis -- see stackedPlotGeometry.
  const geometry = useMemo(() => stackedPlotGeometry(drawable.length), [drawable.length])

  const layout = useMemo(
    () => ({
      xaxis: {
        // An explicit standoff pins the title just under the tick labels.
        // Left to itself Plotly places it against the bottom of whatever
        // margin it is given, so a taller figure left it stranded low.
        title: { text: calendarAxisTitle(dateFormat), standoff: geometry.titleStandoff },
        zeroline: false,
        gridcolor: plot.grid,
        // Oldest on the left, as a chronology is read -- see PosteriorPlot
        // for why plotting cal BP needs the axis reversed to get that. An
        // explicit range is already given in that (descending) order.
        ...(xRange ? { range: xRange, autorange: false } : { autorange: 'reversed' }),
        ...(ticks ? { tickmode: 'array', tickvals: ticks.tickvals, ticktext: ticks.ticktext } : {}),
      },
      yaxis: {
        // Rows are positions, not quantities: label each one with its date
        // and fix the range, so adding a date never rescales the figure.
        tickmode: 'array',
        tickvals: drawable.map((_, index) => baselineFor(index)),
        ticktext: drawable.map((s) => s.title),
        tickfont: { size: narrow ? 9 : 11 },
        range: [baselineFor(drawable.length - 1) - 0.35, CURVE_HEIGHT + 0.35],
        zeroline: false,
        showgrid: false,
        fixedrange: true,
      },
      shapes,
      annotations,
      // Per-row, because two rows at the same calendar age are two different
      // dates -- a unified readout would merge them into one tooltip.
      hovermode: 'closest',
      plot_bgcolor: plot.surface,
      paper_bgcolor: plot.surface,
      margin: { l: marginLeft, r: 20, t: geometry.marginTop, b: geometry.marginBottom },
      legend: { orientation: 'h', y: geometry.legendY, yanchor: 'top' },
      autosize: true,
    }),
    [plot, dateFormat, ticks, drawable, shapes, annotations, xRange, marginLeft, narrow, geometry]
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

  if (drawable.length === 0) {
    return (
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography color="textSecondary">No posterior distributions to plot.</Typography>
      </Paper>
    )
  }

  return (
    <Plot
      data={plotData}
      layout={layout}
      config={config}
      useResizeHandler
      style={{
        width: '100%',
        height: `${geometry.height}px`,
      }}
    />
  )
}
