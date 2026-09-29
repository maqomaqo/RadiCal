import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Box, Button, Paper, Typography, useMediaQuery, useTheme } from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import FitWidthIcon from '@mui/icons-material/SettingsEthernet'
import Plot from '@/components/Plot'
import type { Annotation, Config, Dash, Data, Layout, Shape } from 'plotly.js-dist-min'
import {
  DateFormat,
  DEFAULT_DATE_FORMAT,
  calendarAxisTitle,
  calendarScaleTicks,
  calendarTickMarkup,
  formatCalendarAge,
  type CalendarTick,
} from '@/utils/dateFormat'
import type { HPDBand } from '@/utils/hpd'
import { CURVE_HEIGHT, stackedPlotGeometry } from '@/utils/stackedPlotGeometry'
import { toUnitArea, trimTails } from '@/utils/distribution'
import { usePlotColors, usePlotFont } from '@/theme/plotColors'
import { useElementTypeColors } from '@/theme/diagramColors'
import type { ElementType } from '@/types'
import { downloadPlotPng } from '@/utils/downloadPlot'

// Same binding as PosteriorPlot -- see the note there for why the factory is
// bound to the packaged plotly.js rather than a CDN global.

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
  /**
   * What KIND of element the last step of that path is -- the container this
   * row belongs to, not the row itself. It is what lets the margin draw a
   * Phase in the Phase's own green and a Sequence in the Sequence blue, as
   * the results table and the Harris matrix do; without it the margin can
   * name a group but not say what kind of thing it is, and a Sequence orders
   * what it holds where a Phase does not. Absent for an unplaced date.
   */
  groupKind?: ElementType
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

// Row geometry, in y-axis data units where one row is exactly 1.0 tall. The
// curve occupies the upper part of its row (CURVE_HEIGHT, from
// stackedPlotGeometry, which derives the axis range and the pixel offset of
// every row from it) and the two HPD bars sit in the gap beneath it, so no row
// can ever paint into its neighbour.
// Named by position, not by level: which confidence each strip carries is the
// caller's decision.
const OUTER_BAR: [number, number] = [-0.09, -0.03]
const INNER_BAR: [number, number] = [-0.19, -0.13]
// Room for the row names, and where the phase labels sit within it. Shrinks
// with the viewport, since on a phone the wide version takes half the width;
// a truncated name still identifies its row against the table above.
const MARGIN_LEFT = 150
const MARGIN_LEFT_NARROW = 84
// Room at the right for the last tick label's own width.
const MARGIN_RIGHT = 20

// --- The left margin ------------------------------------------------------
//
// Three things share it, in this order from the figure's left edge: the group
// spine, the group's name, and the row names Plotly right-aligns against the
// axis. The spine and the name are one mark -- a labelled bracket -- so they
// start at the same place and the name clears the spine by a hair.
const SPINE_X = 6
const SPINE_X_NARROW = 4
const GROUP_NAME_X = 18
const GROUP_NAME_X_NARROW = 12
// The spine spans its group's rows, stopping short of the rules that separate
// one group from the next so the two marks do not meet.
const SPINE_TOP = 0.3
const SPINE_BOTTOM = 0.3
// Where a group's name sits above the first row it holds.
const GROUP_NAME_Y = 0.5

// The era on a calendar tick, one size below the number it qualifies. Same
// ink: it is repeated on every tick because a bare number on a BC/AD axis is
// ambiguous, and a qualifier that has to be readable cannot also be faint.
const ERA_SIZE = 9

// The app bar is sticky, so the pinned ruler hangs directly beneath it.
const APP_BAR_HEIGHT = { xs: 56, sm: 64 }
// How much taller than the viewport the figure has to be before the ruler is
// worth its band: room for the app bar, the page's own heading and the plot's
// controls above the figure.
const RULER_SLACK = 200

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

/** One run of consecutive date rows sharing a container in the model. */
interface RowGroup {
  /** The container's full path, so two "Phase J"s under different sequences
   *  are two groups rather than one. */
  key: string
  /** Its own name -- the last step of the path. The rest repeats the site
   *  name on every row. */
  name: string
  kind?: ElementType
  /** First and last DATE row it holds. A boundary between two groups belongs
   *  to neither: it is the transition between them, so the spine stops short
   *  of it rather than gathering it in. */
  first: number
  last: number
}

/**
 * The calendar scale, pinned under the app bar for as long as the figure is on
 * screen.
 *
 * A stack of forty dates is three thousand pixels tall and its axis is at the
 * bottom of it, so reading the top row's date meant scrolling to the axis,
 * holding a number in your head, and scrolling back. The figure's own axis
 * stays where it is -- it is what travels into the exported PNG, which this
 * band cannot -- and this is the on-screen copy of it, built from the same
 * ticks so the two cannot disagree.
 *
 * It carries the group as well as the scale: at that height the row names are
 * on screen but the heading that says which Phase they sit in has long since
 * scrolled past.
 */
function CalendarRuler({
  ticks,
  range,
  marginLeft,
  title,
  group,
  groupColor,
  innerRef,
}: {
  ticks: CalendarTick[]
  /** [older, younger] -- the axis range, in the order the figure draws it. */
  range: [number, number]
  marginLeft: number
  title: string
  group: string | null
  groupColor: string
  innerRef: React.Ref<HTMLDivElement>
}) {
  const span = range[0] - range[1]
  const position = (calBP: number) => ((range[0] - calBP) / span) * 100

  return (
    <Box
      ref={innerRef}
      sx={{
        position: 'sticky',
        top: APP_BAR_HEIGHT,
        zIndex: 2,
        // Opaque: the curves scroll underneath it. A translucent band over a
        // figure blends two readings of the same axis into one picture.
        backgroundColor: 'background.paper',
        borderBottom: 1,
        borderColor: 'divider',
        pt: 0.5,
        pl: `${marginLeft}px`,
        pr: `${MARGIN_RIGHT}px`,
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1 }}>
        <Typography variant="caption" color="textSecondary">
          {title}
        </Typography>
        <Typography variant="caption" color="textSecondary">
          ← older
        </Typography>
        {group && (
          <Typography
            variant="caption"
            noWrap
            sx={{ ml: 'auto', fontWeight: 600, color: groupColor, minWidth: 0 }}
          >
            {group}
          </Typography>
        )}
      </Box>
      {/* Graduations point down, at the figure they measure. */}
      <Box sx={{ position: 'relative', height: 22 }}>
        {ticks.map((tick) => (
          <Box
            key={tick.calBP}
            sx={{
              position: 'absolute',
              left: `${position(tick.calBP)}%`,
              transform: 'translateX(-50%)',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              fontSize: 11,
              color: 'text.secondary',
              whiteSpace: 'nowrap',
            }}
          >
            <Box component="span">
              {tick.era && tick.eraFirst && (
                <Box component="span" sx={{ fontSize: ERA_SIZE, mr: '3px' }}>
                  {tick.era}
                </Box>
              )}
              {tick.value}
              {tick.era && !tick.eraFirst && (
                <Box component="span" sx={{ fontSize: ERA_SIZE, ml: '3px' }}>
                  {tick.era}
                </Box>
              )}
            </Box>
            <Box sx={{ width: '1px', height: 5, mt: '2px', backgroundColor: 'divider' }} />
          </Box>
        ))}
      </Box>
    </Box>
  )
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
  const plotFont = usePlotFont()
  // The same map the results table, the tree editor and the Harris matrix
  // colour their elements from, so a Phase is one green across the app.
  const elementTypeColors = useElementTypeColors()
  const narrow = useMediaQuery(theme.breakpoints.down('sm'))
  const marginLeft = narrow ? MARGIN_LEFT_NARROW : MARGIN_LEFT

  // The figure's own box, measured: the group spines are placed in the left
  // margin as a fraction of the plot area's width (see spinePaperX), and the
  // pinned ruler needs the figure's position on the page to say which group
  // the reader has scrolled to.
  const figureRef = useRef<HTMLDivElement | null>(null)
  const rulerRef = useRef<HTMLDivElement | null>(null)
  const [figureWidth, setFigureWidth] = useState(0)
  // Measured in a layout effect and then watched: the observer alone leaves
  // the spines undrawn until it first fires, and a browser that is not
  // painting the page (a background tab) does not fire it at all -- so the
  // first measurement is taken synchronously, off the same box.
  useLayoutEffect(() => {
    const element = figureRef.current
    if (!element) return
    setFigureWidth(element.getBoundingClientRect().width)
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver((entries) => {
      setFigureWidth(entries[0].contentRect.width)
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

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
    const data: Data[] = []
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
      const marker = (value: number, color: string, dash: Dash, name: string, key: string) =>
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

  // The groups the rows fall into: a run of consecutive dates sharing one
  // container in the model.
  //
  // Driven by the DATE rows only. A boundary has a path of its own, being the
  // transition between two phases rather than a member of either, so grouping
  // on all rows alike would break the run at every boundary.
  const groups = useMemo(() => {
    const out: RowGroup[] = []
    drawable.forEach((s, index) => {
      if (s.kind !== 'date' || !s.path) return
      const open = out[out.length - 1]
      if (open && open.key === s.path) {
        open.last = index
        return
      }
      out.push({
        key: s.path,
        // Last segment only: the full path repeats the site name on every row.
        name: s.path.split('>').pop()!.trim(),
        kind: s.groupKind,
        first: index,
        last: index,
      })
    })
    return out
  }, [drawable])

  /**
   * Where the group spine sits, as a fraction of the plot area's width --
   * negative, being out in the left margin.
   *
   * Plotly measures a shape either in data coordinates, which are clipped to
   * the plot area, or in paper units, which are not but are a fraction of a
   * width only the browser knows. So the figure's width is measured, and the
   * spines wait for that first measurement rather than being drawn at a
   * guessed offset and jumping.
   */
  const spineX = narrow ? SPINE_X_NARROW : SPINE_X
  const plotAreaWidth = figureWidth - marginLeft - MARGIN_RIGHT
  const spinePaperX = plotAreaWidth > 40 ? -(marginLeft - spineX) / plotAreaWidth : null

  // What the margin draws for each group: a rule where one group gives way to
  // the next, a spine spanning the rows the group holds, and its name at the
  // head of that spine -- so a reader sees a group's EXTENT rather than only
  // the row it starts at, and can tell a Phase from a Sequence by the hue the
  // rest of the app already draws each in.
  const { shapes, annotations } = useMemo(() => {
    const shapes: Partial<Shape>[] = []
    const annotations: Partial<Annotation>[] = []

    groups.forEach((group, order) => {
      const ink = group.kind ? elementTypeColors[group.kind].ink : plot.label
      if (order > 0) {
        shapes.push({
          type: 'line',
          xref: 'paper',
          x0: 0,
          x1: 1,
          yref: 'y',
          y0: baselineFor(group.first) + 0.5,
          y1: baselineFor(group.first) + 0.5,
          line: { color: plot.phaseRule, width: 1 },
        })
      }
      if (spinePaperX != null) {
        shapes.push({
          type: 'line',
          xref: 'paper',
          x0: spinePaperX,
          x1: spinePaperX,
          yref: 'y',
          y0: baselineFor(group.first) + SPINE_TOP,
          y1: baselineFor(group.last) - SPINE_BOTTOM,
          line: { color: ink, width: 2 },
        })
      }
      // In the left margin with the row names, not inside the plot, where it
      // would land on whatever curve reached that far.
      annotations.push({
        xref: 'paper',
        x: 0,
        xanchor: 'left',
        xshift: -marginLeft + (narrow ? GROUP_NAME_X_NARROW : GROUP_NAME_X),
        yref: 'y',
        y: baselineFor(group.first) + GROUP_NAME_Y,
        yanchor: 'middle',
        text: group.name,
        showarrow: false,
        // The weight and the hue the results table gives a Sequence/Phase
        // heading, so a group reads as the same kind of thing in both places.
        font: { size: 11, color: ink, weight: 600 },
      })
    })
    return { shapes, annotations }
  }, [groups, marginLeft, narrow, plot, elementTypeColors, spinePaperX])

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
    return [max + pad, min - pad] as [number, number]
  }, [drawable, fitWidth, showPrior])

  // Ticks span whatever the axis shows, so a clipped view does not label years
  // left off it. The app picks them in every reporting format, cal BP
  // included, because the pinned ruler is graduated from the same array: left
  // to choose its own, Plotly would put the figure's ticks a few years off the
  // ruler's, and two scales that disagree are worse than one that is scrolled
  // away from.
  const ticks = useMemo(() => {
    if (xRange) return calendarScaleTicks(xRange[1], xRange[0], dateFormat)
    const all = drawable.flatMap((s) => s.distribution!.map(([age]) => age))
    if (all.length === 0) return []
    return calendarScaleTicks(Math.min(...all), Math.max(...all), dateFormat)
  }, [drawable, dateFormat, xRange])

  const tickText = useMemo(
    () => ticks.map((tick) => calendarTickMarkup(tick, { size: ERA_SIZE, color: plotFont.color })),
    [ticks, plotFont.color]
  )

  // Pixel budget for everything below the axis -- see stackedPlotGeometry.
  const geometry = useMemo(() => stackedPlotGeometry(drawable.length), [drawable.length])

  const plotBox = useRef<HTMLDivElement | null>(null)

  // --- The pinned ruler -----------------------------------------------
  //
  // Only for a figure taller than the screen it is read on. On a six-date
  // stack the axis is already in view, and a second copy of it is a band of
  // chrome buying nothing.
  const [viewportHeight, setViewportHeight] = useState(() =>
    typeof window === 'undefined' ? 0 : window.innerHeight
  )
  useEffect(() => {
    const onResize = () => setViewportHeight(window.innerHeight)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  const showRuler =
    xRange != null && ticks.length >= 2 && geometry.height > viewportHeight - RULER_SLACK

  // Which group the reader is currently inside: the row that has just passed
  // under the ruler. Its name is the one thing the row labels cannot supply --
  // a name says which date, not which Phase it sits in, and the heading that
  // said so is several screens above by the time it matters.
  const [pinnedGroup, setPinnedGroup] = useState<RowGroup | null>(null)
  useEffect(() => {
    if (!showRuler) {
      setPinnedGroup(null)
      return
    }
    let frame = 0
    const measure = () => {
      frame = 0
      const figure = figureRef.current
      const ruler = rulerRef.current
      if (!figure || !ruler) return
      // Depth into the plot area of the ruler's lower edge -- the first row
      // still visible below it.
      const depth =
        ruler.getBoundingClientRect().bottom -
        (figure.getBoundingClientRect().top + geometry.marginTop)
      if (depth <= 0) {
        // The figure has not reached the ruler yet: its own group headings are
        // still on screen, and naming one here would only repeat them.
        setPinnedGroup(null)
        return
      }
      const row = geometry.rowAtPx(depth)
      // The group that row belongs to. A boundary row belongs to neither side,
      // being the transition between them, so it takes the name of the group
      // BELOW it: what is on screen under the band is that group's dates, and
      // naming the one just scrolled past would label them wrongly.
      const found =
        groups.find((group) => group.first <= row && row <= group.last) ??
        groups.find((group) => group.first >= row) ??
        groups[groups.length - 1] ??
        null
      setPinnedGroup((previous) => (previous?.key === found?.key ? previous : found))
    }
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(measure)
    }
    measure()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [showRuler, groups, geometry])

  const layout = useMemo(
    (): Partial<Layout> => ({
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
        ...(ticks.length >= 2
          ? { tickmode: 'array', tickvals: ticks.map((t) => t.calBP), ticktext: tickText }
          : {}),
      },
      yaxis: {
        // Rows are positions, not quantities: label each with its date and fix
        // the range, so adding a date never rescales the figure.
        tickmode: 'array',
        tickvals: drawable.map((_, index) => baselineFor(index)),
        ticktext: drawable.map((s) => s.title),
        tickfont: { size: narrow ? 9 : 11 },
        range: geometry.yRange,
        zeroline: false,
        showgrid: false,
        fixedrange: true,
      },
      shapes,
      annotations,
      // Per-row: two rows at the same calendar age are two different dates,
      // which a unified readout would merge into one tooltip.
      hovermode: 'closest',
      // The app's own face, at the app's own size: Plotly's default is Open
      // Sans at 12px, which set every word on the figure in a face this app
      // does not otherwise use.
      font: plotFont,
      plot_bgcolor: plot.surface,
      paper_bgcolor: plot.surface,
      margin: { l: marginLeft, r: 20, t: geometry.marginTop, b: geometry.marginBottom },
      legend: { orientation: 'h', y: geometry.legendY, yanchor: 'top' },
      autosize: true,
    }),
    [
      plot,
      plotFont,
      dateFormat,
      ticks,
      tickText,
      drawable,
      shapes,
      annotations,
      xRange,
      marginLeft,
      narrow,
      geometry,
    ]
  )

  const config = useMemo(
    (): Partial<Config> => ({
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
      {showRuler && xRange && (
        <CalendarRuler
          innerRef={rulerRef}
          ticks={ticks}
          range={xRange}
          marginLeft={marginLeft}
          title={calendarAxisTitle(dateFormat)}
          group={pinnedGroup?.name ?? null}
          groupColor={
            pinnedGroup?.kind ? elementTypeColors[pinnedGroup.kind].ink : plot.label
          }
        />
      )}
      <Box ref={figureRef}>
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
      </Box>
      {/* This is the figure most likely to end up in a paper, so it is the one
          that most needs a control a keyboard can reach -- see PNG_EXPORT_SCALE. */}
    </Box>
  )
}
