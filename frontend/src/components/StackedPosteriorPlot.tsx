import { useMemo, useRef, useState } from 'react'
import { Box, Button, Paper, Typography, useMediaQuery, useTheme } from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import FitWidthIcon from '@mui/icons-material/SettingsEthernet'
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
import { stackedPlotGeometry } from '@/utils/stackedPlotGeometry'
import { toUnitArea, trimTails } from '@/utils/distribution'
import { usePlotColors } from '@/theme/plotColors'
import { downloadPlotPng } from '@/utils/downloadPlot'

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
  /** The HPD bands to draw, outer first, computed at the page's selected
   *  confidence (utils/hpd.hpdBands). The level's name travels with its ranges,
   *  so the legend cannot outlive the selection it describes. */
  bands?: HPDBand[]
  /** The same date's plain calibration, unconstrained. See showPrior below. */
  priorDistribution?: Array<[number, number]>
  pointEstimates: { mean?: number; median?: number; std?: number }
  /**
   * The measurement this date was calibrated FROM, and the curve it was
   * calibrated against. Absent for a boundary, and for any date whose
   * measurement the page could not match to its result.
   *
   * Only the Curve view reads it -- the other two work in calendar years and
   * have no radiocarbon axis -- but it is declared here so one array of series
   * feeds all three.
   */
  measurement?: SeriesMeasurement
}

/**
 * A date's radiocarbon measurement, reduced to what a plot needs.
 *
 * `ageBp` and `error1Sigma` are reservoir-CORRECTED where a marine offset
 * applies: a marine sample of calendar age t is expected to measure
 * curve(t) + Delta-R, so what reads against the drawn curve is the measured
 * age minus Delta-R, with the offset's uncertainty joining in quadrature.
 * Correcting where the series is built keeps the plot free of the convention.
 */
export interface SeriesMeasurement {
  ageBp: number
  error1Sigma?: number
  /** Which calibration curve this date was calibrated against. */
  curveId?: string
  /** True when ageBp/error1Sigma above are not the numbers on the lab report. */
  reservoirCorrected?: boolean
}

interface StackedPosteriorPlotProps {
  /** Oldest first, drawn top to bottom, as OxCal lists a model. */
  series: StackedSeries[]
  showMean?: boolean
  showMedian?: boolean
  /**
   * Overlay each date's unconstrained calibration as a grey shadow behind its
   * modelled posterior, scaled into the same row so the two compare directly.
   * Off by default: a comparison aid, not part of every reading.
   */
  showPrior?: boolean
  /** Whether a model ran, for one word in the legend: a plain calibration has
   *  a calibrated distribution, not a modelled posterior. */
  isModelled?: boolean
  dateFormat?: DateFormat
}

// Row geometry, in y-axis data units where one row is exactly 1.0 tall.
// The curve occupies the upper part of its row and the two HPD bars sit in
// the gap beneath it, so no row can ever paint into its neighbour.
const CURVE_HEIGHT = 0.78
// Named by position, not by level: which confidence each strip carries is the
// caller's decision.
const OUTER_BAR: [number, number] = [-0.09, -0.03]
const INNER_BAR: [number, number] = [-0.19, -0.13]
// Room for the row names, and where the phase labels sit within it. Shrinks
// with the viewport, since on a phone the wide version takes half the width;
// a truncated name still identifies its row against the table above.
const MARGIN_LEFT = 150
const MARGIN_LEFT_NARROW = 84

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

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
 * Every posterior in one figure, one row each, on a single calendar axis --
 * the OxCal multiplot idiom.
 *
 * The shared axis is what makes comparison possible: whether two dates overlap
 * cannot be read off separate charts each auto-ranged to its own data.
 *
 * Each row is scaled to its OWN peak, so heights are not comparable between
 * rows -- a tall narrow curve is a precise date, not a more probable one. OxCal
 * does the same; a shared density scale would flatten every precise date into
 * an unreadable spike.
 */
export default function StackedPosteriorPlot({
  series,
  showMean = false,
  showMedian = false,
  showPrior = false,
  isModelled = true,
  dateFormat = DEFAULT_DATE_FORMAT,
}: StackedPosteriorPlotProps) {
  const theme = useTheme()
  const plot = usePlotColors()
  const narrow = useMediaQuery(theme.breakpoints.down('sm'))
  const marginLeft = narrow ? MARGIN_LEFT_NARROW : MARGIN_LEFT

  // Off by default: the cropped range is almost always the better read -- see
  // the note on xRange for the exception.
  const [fitWidth, setFitWidth] = useState(false)

  const seriesSignature = series
    .map((s) => {
      const band = s.bands?.[0]
      return `${s.key}~${s.distribution?.length ?? 0}~${band ? `${band.label}:${band.ranges.length}` : ''}~${
        s.priorDistribution?.length ?? 0
      }`
    })
    .join('|')

  /**
   * A content signature for the rows being drawn, used as the memo key instead
   * of the `series` array itself.
   *
   * The caller rebuilds that array on every render, so every memo downstream
   * missed and Plotly re-diffed all 65 traces on interactions that do not touch
   * this figure -- collapsing a phase in the results table measured 116ms.
   *
   * The pieces below are exactly what the traces are built from: which rows, in
   * which order, and the band objects carrying the HPD geometry and its label
   * (referentially stable per confidence level; see Results' bandsFor cache).
   */
  const drawable = useMemo(
    () => series.filter((s) => s.distribution && s.distribution.length > 0),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [seriesSignature]
  )

  const plotData = useMemo(() => {
    const data: any[] = []
    // One legend entry per kind of mark, from whichever row carries it first;
    // otherwise a 15-date model prints 15 identical "95.4% HPD" rows.
    const legendUsed = new Set<string>()
    const claimLegend = (key: string) => {
      if (legendUsed.has(key)) return false
      legendUsed.add(key)
      return true
    }

    drawable.forEach((s, index) => {
      const yBase = baselineFor(index)
      // Both series go on unit area before anything is measured off them: they
      // arrive in different units, so comparing raw peaks compares two
      // different quantities. See utils/distribution.ts.
      const { ages, probs, peak } = trimTails(toUnitArea(s.distribution!))
      const priorPoints =
        showPrior && s.priorDistribution && s.priorDistribution.length > 0
          ? trimTails(toUnitArea(s.priorDistribution))
          : null

      // One scale for the row, from whichever of the two is taller, so neither
      // paints outside its band: scaling by the posterior alone lets a sharper
      // prior overflow into the row above.
      //
      // Equal area plus a shared scale is what makes the overlay readable --
      // shorter and wider means the model narrowed this date, not that the two
      // were normalised differently.
      const rowPeak = Math.max(peak, priorPoints ? priorPoints.peak : 0)
      const scale = rowPeak > 0 ? CURVE_HEIGHT / rowPeak : 0
      const isBoundary = s.kind === 'boundary'

      // HPD bars first, so the curve draws over them. Each disjoint range gets
      // its own bar: a multimodal HPD is not the span between its outermost
      // peaks, and one bar would claim the gap between them. A lone band is the
      // tightest interval in the row, so it takes the stronger fill -- the two
      // alphas encode "tighter", not "which slot".
      const bands = s.bands ?? []
      const soleBand = bands.length === 1
      bands.forEach((band) => {
        const slot = band.tone === 'outer' ? OUTER_BAR : INNER_BAR
        const fill = soleBand || band.tone === 'inner' ? plot.hpd68 : plot.hpd95
        band.ranges.forEach((r) =>
          data.push(
            bar(r.min, r.max, yBase, slot, fill, `${band.label} HPD`, claimLegend(band.tone), `hpd-${band.tone}`)
          )
        )
      })

      // The unmodelled shadow, scaled by the ROW's factor rather than its own
      // peak, so it reads directly against the posterior above it. Drawn first
      // so the posterior's fill sits on top.
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

      // One closed-polygon trace, fill and stroke together, as the prior
      // shadow is, so its legend swatch shows fill+line rather than a bare
      // line. The stroke traces the flat closing edge at the baseline too, but
      // at this width and beside the HPD bars it does not read as a range bar.
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
        // "Modelled" is what separates a Bayesian posterior from a raw
        // calibration, so a group calibration must not print it.
        name: isBoundary
          ? 'Boundary (modelled transition)'
          : isModelled
          ? 'Posterior (modelled)'
          : 'Calibrated',
        legendgroup: isBoundary ? 'boundary' : 'posterior',
        showlegend: claimLegend(isBoundary ? 'boundary' : 'posterior'),
        // Hover on the curve's own points, not the area it encloses: a filled
        // trace defaults to 'points+fills', and the fill hover ignores
        // hovertemplate, printing the trace name in the fill colour beside the
        // real label -- which reads as the row's name escaping the tooltip.
        hoveron: 'points',
        hovertemplate: `<b>${s.title}</b><br>%{customdata}<extra></extra>`,
      })

      // Point estimates as a tick within the row, not a line across the
      // figure: at this density a full-height rule per date is all you see.
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
  }, [drawable, plot, showMean, showMedian, showPrior, dateFormat, isModelled])

  // Phase groupings: a light rule where the dates change phase, the phase
  // named once beside its first date, so a reader scanning the figure sees
  // where one ends without counting rows.
  //
  // Driven by the DATE rows only. A boundary has a path of its own, being the
  // transition between two phases rather than a member of either, so grouping
  // on all rows alike would break the run at every boundary.
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
      // In the left margin with the row names, not inside the plot, where it
      // would land on whatever curve reached that far.
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
        // The weight the results table gives a Sequence/Phase heading, so a
        // phase name reads as the same kind of thing in both places.
        font: { size: 11, color: 'rgba(0,0,0,0.75)', weight: 600 },
      })
    })
    return { shapes, annotations }
  }, [drawable, marginLeft])

  /**
   * The axis is set from the HPD regions, not the full extent of every curve:
   * a posterior's tails run far wider than anything it says, and on a real
   * sequence stretch the shared axis to three times the span the dates occupy.
   *
   * That clips one case, which is what Fit width exists for: with the prior
   * shadow shown, an OUTLIER's unmodelled calibration is much wider than the
   * posterior the model pulled it into and runs off both ends. Fit width fits
   * to what is DRAWN rather than to the raw data, which would re-admit the
   * tails this range crops.
   */
  const xRange = useMemo(() => {
    const bounds: number[] = []
    if (fitWidth) {
      drawable.forEach((s) => {
        const curve = trimTails(toUnitArea(s.distribution!))
        if (curve.ages.length > 0) {
          bounds.push(curve.ages[0], curve.ages[curve.ages.length - 1])
        }
        if (showPrior && s.priorDistribution && s.priorDistribution.length > 0) {
          const prior = trimTails(toUnitArea(s.priorDistribution))
          if (prior.ages.length > 0) {
            bounds.push(prior.ages[0], prior.ages[prior.ages.length - 1])
          }
        }
      })
    } else {
      drawable.forEach((s) => {
        // The widest band in the row: the outer one when there are two, the
        // only one at 1 sigma.
        const widest = s.bands?.[0]
        if (widest) widest.ranges.forEach((r) => bounds.push(r.min, r.max))
      })
    }
    if (bounds.length === 0) return null
    const min = Math.min(...bounds)
    const max = Math.max(...bounds)
    // Fit width already includes everything on screen and needs only enough
    // room not to clip a stroke; the HPD-based range needs enough to keep the
    // tails either side of the interval legible.
    const pad = fitWidth ? Math.max((max - min) * 0.02, 5) : Math.max((max - min) * 0.35, 25)
    // Reversed axis: Plotly takes the range in axis order, older end first.
    return [max + pad, min - pad]
  }, [drawable, fitWidth, showPrior])

  // Ticks span whatever the axis shows, so a clipped view does not label years
  // left off it.
  const ticks = useMemo(() => {
    if (xRange) return calendarAxisTicks(xRange[1], xRange[0], dateFormat)
    const all = drawable.flatMap((s) => s.distribution!.map(([age]) => age))
    if (all.length === 0) return null
    return calendarAxisTicks(Math.min(...all), Math.max(...all), dateFormat)
  }, [drawable, dateFormat, xRange])

  // Pixel budget for everything below the axis -- see stackedPlotGeometry.
  const geometry = useMemo(() => stackedPlotGeometry(drawable.length), [drawable.length])

  const plotBox = useRef<HTMLDivElement | null>(null)

  const layout = useMemo(
    () => ({
      // A layout property, not a config one: hiding the modebar does not stop
      // drag-to-box-zoom, which would leave a reader zoomed in with no way out.
      dragmode: false as const,
      xaxis: {
        // An explicit standoff pins the title under the tick labels; left to
        // itself Plotly puts it against the bottom of whatever margin it has,
        // stranding it low on a taller figure.
        title: { text: calendarAxisTitle(dateFormat), standoff: geometry.titleStandoff },
        zeroline: false,
        gridcolor: plot.grid,
        // Oldest on the left, as a chronology reads. See PosteriorPlot for why
        // cal BP needs a reversed axis; the explicit range is already
        // descending.
        ...(xRange ? { range: xRange, autorange: false } : { autorange: 'reversed' }),
        ...(ticks ? { tickmode: 'array', tickvals: ticks.tickvals, ticktext: ticks.ticktext } : {}),
      },
      yaxis: {
        // Rows are positions, not quantities: label each with its date and fix
        // the range, so adding a date never rescales the figure.
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
      // Per-row: two rows at the same calendar age are two different dates,
      // which a unified readout would merge into one tooltip.
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

  if (drawable.length === 0) {
    return (
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography color="textSecondary">No posterior distributions to plot.</Typography>
      </Paper>
    )
  }

  return (
    <Box ref={plotBox}>
      {/* Above the figure, not below it. The Results page renders Show prior
          and Show boundary plots immediately above this component, and Fit
          width is the answer to what Show prior reveals -- a reader who ticks
          the prior, sees an outlier's calibration run off both ends, and has
          to scroll three thousand pixels to find the control that fixes it
          has been sent on an errand. All the plot's controls sit together. */}
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1, mb: 0.5 }}>
        {/* The one control the removed modebar was genuinely carrying. Named
            for what it does to the figure, not for the gesture: "Autoscale"
            described Plotly's internals. */}
        <Button
          size="small"
          aria-pressed={fitWidth}
          startIcon={<FitWidthIcon />}
          onClick={() => setFitWidth((v) => !v)}
        >
          {fitWidth ? 'Fit to ranges' : 'Fit width'}
        </Button>
        <Button
          size="small"
          startIcon={<DownloadIcon />}
          onClick={() => downloadPlotPng(plotBox.current, 'posterior-stack')}
        >
          Download PNG
        </Button>
      </Box>
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
      {/* This is the figure most likely to end up in a paper, so it is the one
          that most needs a control a keyboard can reach -- see PNG_EXPORT_SCALE. */}
    </Box>
  )
}
