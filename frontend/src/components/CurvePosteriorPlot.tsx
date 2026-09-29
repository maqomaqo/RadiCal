import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Box, Button, CircularProgress, Paper, Typography } from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import Plot from '@/components/Plot'
import type { Config, Dash, Data, Layout } from 'plotly.js-dist-min'
import { fetchCurvePoints, type CurvePoint } from '@/api/curves'
import { useCurves } from '@/hooks/useCurves'
import type { StackedSeries } from '@/components/StackedPosteriorPlot'
import { usePlotColors, usePlotFont } from '@/theme/plotColors'
import { curveStyles } from '@/theme/curveColors'
import {
  DateFormat,
  DEFAULT_DATE_FORMAT,
  calendarAxisTicks,
  calendarAxisTitle,
  formatCalendarAge,
} from '@/utils/dateFormat'
import { toUnitArea, trimTails, type TrimmedDistribution } from '@/utils/distribution'
import {
  MEASUREMENT_LOBE_FRACTION,
  NORMAL_SPAN_SIGMA,
  curveAxisRanges,
  curvePlotGeometry,
  lobeFraction,
  normalDensity,
  padSpan,
  spanOf,
  strokeMask,
} from '@/utils/curvePlotGeometry'
import { downloadPlotPng } from '@/utils/downloadPlot'

// Same binding as the other two plots -- see PosteriorPlot for why the factory
// is bound to the packaged plotly.js rather than a CDN global.

/**
 * Every date drawn where it actually meets the calibration curve.
 *
 * The stacked and per-date views answer "what came out"; this one answers
 * "why". A posterior that is bimodal, three centuries wide, or unexpectedly
 * tight is almost always explained by the shape of the curve at that
 * radiocarbon age, which neither of the others shows.
 *
 * The figure is the classic calibration diagram, extended to a whole model:
 *
 *   left margin   each date's laboratory measurement, as a normal
 *                 distribution up the radiocarbon axis
 *   middle        the calibration curve with its own 1σ and 2σ envelope
 *   on the curve  each date's calibrated posterior, as a lobe rising from a
 *                 baseline at that date's own radiocarbon age
 *
 * A dotted rule ties the three together at each date's radiocarbon age: follow
 * it right from the measurement to where it crosses the curve, and the
 * posterior sits directly under the crossing. Where the curve is flat the rule
 * crosses it over a long stretch and the lobe is wide; where it doubles back
 * the rule crosses it two or three times and the lobe has two or three peaks.
 *
 * Each curve has its own fixed colour and dash, and its σ envelope in the same
 * hue (theme/curveColors.ts). Nothing else takes one of those hues, so a
 * coloured line is always a calibration curve.
 *
 * The dates are all one blue and what separates the stages is the EDGE: a
 * measurement is a bare wash, a number off a lab report resolved against
 * nothing, while a posterior has a drawn outline because it has been read
 * through the curve and is now a claim about calendar years. The unmodelled
 * prior keeps its own grey, being a different date's worth of information.
 *
 * Boundaries are not drawn: a modelled transition has no radiocarbon
 * measurement and no place on this axis, and inventing one would be the one
 * claim this figure must not make. The caller is told how many were left out.
 */

interface CurvePosteriorPlotProps {
  /** Oldest first, as the other views take them. Boundaries may be included
   *  and are filtered out here rather than by the caller. */
  series: StackedSeries[]
  showMean?: boolean
  showMedian?: boolean
  /** Overlay each date's unconstrained calibration behind its posterior, in
   *  the same grey the other views use. */
  showPrior?: boolean
  /** Whether a model actually ran -- one word in the legend. */
  isModelled?: boolean
  dateFormat?: DateFormat
  height?: number
}

const DEFAULT_HEIGHT = 560

/** How many curve points to ask for. Enough to keep a wiggle a wiggle. */
const CURVE_MAX_POINTS = 700

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

/**
 * A lobe: a filled polygon with no stroke, plus -- for anything that has been
 * through the calibration -- the density's outline drawn on its own.
 *
 * Split across two traces because a closed, stroked polygon strokes the
 * straight run along its foot too, and here that foot is a horizontal rule at
 * the date's radiocarbon age: a dozen dates fill the figure with heavy rules
 * that read as data and bury the curve. Stroking only the density keeps each
 * date's shape visible and the curve legible through the fills.
 *
 * The fill still has to be a closed polygon, so the close back to the baseline
 * happens here: `base` pins whichever coordinate the foot is constant in -- y
 * for a posterior on the curve, x for a measurement at the frame's edge -- and
 * NaN leaves the other to follow the outline's own ends.
 *
 * `line` is OPTIONAL, and its absence is a statement: a raw measurement is a
 * bare wash, a calibrated distribution gets an outline. Same blue for both,
 * being the same date.
 */
function lobeTraces(
  outline: { x: number[]; y: number[] },
  base: { x: number; y: number },
  options: {
    fillcolor: string
    /** Outline colour. Omit to draw the lobe as a bare fill -- see above. */
    line?: string
    dash?: Dash
    width?: number
    name: string
    legendgroup: string
    showlegend: boolean
    legendrank: number
    customdata?: string[]
    hovertemplate?: string
    /** Which outline points to stroke; the rest become gaps. See
     *  strokeMask. Absent means stroke the whole outline. */
    stroke?: boolean[]
  }
) {
  const closeX = (i: number) => (Number.isFinite(base.x) ? base.x : outline.x[i])
  const closeY = (i: number) => (Number.isFinite(base.y) ? base.y : outline.y[i])
  const last = outline.x.length - 1
  const shared = {
    name: options.name,
    legendgroup: options.legendgroup,
    legendrank: options.legendrank,
  }
  const hover = options.hovertemplate
    ? { hovertemplate: options.hovertemplate, ...(options.customdata ? { customdata: options.customdata } : {}) }
    : { hoverinfo: 'skip' as const }

  const traces: Data[] = [
    {
      ...shared,
      x: [closeX(0), ...outline.x, closeX(last)],
      y: [closeY(0), ...outline.y, closeY(last)],
      fill: 'toself',
      fillcolor: options.fillcolor,
      line: { width: 0, color: 'rgba(0,0,0,0)' },
      mode: 'lines',
      showlegend: false,
      // With no outline trace to carry it, the fill answers the pointer, on
      // its own vertices rather than the area it encloses: a fill hover
      // ignores hovertemplate and prints the trace name in the fill colour.
      ...(options.line
        ? { hoverinfo: 'skip' }
        : { hoveron: 'points', ...hover, customdata: options.customdata
              ? [options.customdata[0], ...options.customdata, options.customdata[last]]
              : undefined }),
    },
  ]

  if (options.line) {
    traces.push({
      ...shared,
      x: outline.x,
      // Nulls are gaps to Plotly, which is what is wanted: the fill above
      // stays whole while the stroke lifts off the baseline.
      y: options.stroke ? outline.y.map((v, i) => (options.stroke![i] ? v : null)) : outline.y,
      mode: 'lines',
      line: { color: options.line, width: options.width ?? 1.1, dash: options.dash ?? 'solid' },
      showlegend: false,
      ...hover,
    })
  }

  if (options.showlegend) traces.push(legendSwatch(options))
  return traces
}

/**
 * A data-less trace that exists only to put one entry in the legend.
 *
 * A legend swatch is drawn from its trace's OWN style, and neither trace above
 * can carry the right one: the fill trace is strokeless by necessity, so its
 * swatch is a plain block whatever the lobe looks like, and the outline trace
 * has no fill, so its swatch is a bare line.
 *
 * A trace with no points draws nothing and still gets its legend entry, so
 * this carries fill and stroke together and the key ends up showing what the
 * figure shows: a bare wash for the measurement, a wash with an edge for the
 * distributions that have been calibrated.
 */
function legendSwatch(options: {
  fillcolor: string
  line?: string
  dash?: Dash
  width?: number
  name: string
  legendgroup: string
  legendrank: number
}) {
  return {
    x: [null],
    y: [null],
    fill: 'toself',
    fillcolor: options.fillcolor,
    line: options.line
      ? { color: options.line, width: options.width ?? 1.1, dash: options.dash ?? 'solid' }
      : { width: 0, color: 'rgba(0,0,0,0)' },
    mode: 'lines',
    name: options.name,
    legendgroup: options.legendgroup,
    legendrank: options.legendrank,
    showlegend: true,
    hoverinfo: 'skip',
  }
}

/**
 * Legend order, fixed independently of drawing order. The two disagree here
 * deliberately: the curve is drawn LAST so a dozen translucent lobes cannot
 * bury the thing they are read against, but belongs at the head of the legend.
 */
const RANK = {
  // Each curve takes a block of three -- its line, then its own ±1σ and ±2σ --
  // so a mixed-curve figure keeps each curve's envelope with its line rather
  // than interleaving them. Ties are what interleave: a tie is not an ordering.
  curve: 10,
  band1: 11,
  band2: 12,
  /** Stride between one curve's block and the next. */
  perCurve: 3,
  measurement: 50,
  guide: 60,
  prior: 70,
  posterior: 80,
  mean: 90,
  median: 100,
}

/**
 * Above this many dates the projection rules are dropped.
 *
 * Each rule runs from a date's measurement to the far side of its posterior,
 * which on a plateau is most of the figure's width. Up to about a dozen that
 * is the line a reader follows; past it they cross into a hatched band that
 * hides the curve. Nothing is lost that cannot be read another way -- a
 * measurement and its posterior still sit at the same height.
 */
const GUIDE_LIMIT = 12

export default function CurvePosteriorPlot({
  series,
  showMean = false,
  showMedian = false,
  showPrior = false,
  isModelled = true,
  dateFormat = DEFAULT_DATE_FORMAT,
  height = DEFAULT_HEIGHT,
}: CurvePosteriorPlotProps) {
  const plot = usePlotColors()
  const plotFont = usePlotFont()
  const { curves } = useCurves()
  const plotBox = useRef<HTMLDivElement | null>(null)

  // As the stacked plot does: the caller rebuilds `series` on every render, so
  // memoising on the array itself never hits.
  const seriesSignature = series
    .map(
      (s) =>
        `${s.key}~${s.kind}~${s.distribution?.length ?? 0}~${s.priorDistribution?.length ?? 0}~${
          s.measurement ? `${s.measurement.ageBp}:${s.measurement.error1Sigma}:${s.measurement.curveId}` : ''
        }`
    )
    .join('|')

  /**
   * The dates this figure can place: a distribution to draw and a radiocarbon
   * age to draw it at. Anything else -- a boundary, a date whose measurement
   * did not come back -- is counted and reported rather than drawn at a
   * position it does not have.
   *
   * A stated error is NOT required. Without one there is no measurement lobe
   * (normalDensity refuses to fake one), but the posterior still belongs on
   * the curve at its own radiocarbon age.
   */
  const drawable = useMemo(
    () =>
      series.filter(
        (s) =>
          s.kind === 'date' &&
          s.distribution &&
          s.distribution.length > 0 &&
          s.measurement &&
          isNum(s.measurement.ageBp)
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [seriesSignature]
  )
  /**
   * What was left out, split by why. A boundary being absent is expected and
   * worth one calm sentence; a DATE being absent means the analysis came back
   * without that measurement, and lumping the two together would report a gap
   * in the data as a property of the view.
   */
  const omitted = useMemo(() => {
    const drawn = new Set(drawable.map((s) => s.key))
    const rest = series.filter((s) => !drawn.has(s.key))
    return {
      boundaries: rest.filter((s) => s.kind === 'boundary').length,
      dates: rest.filter((s) => s.kind === 'date').length,
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawable, seriesSignature])

  /** Each drawable date's posterior and prior, trimmed and put on unit area. */
  const lobes = useMemo(
    () =>
      drawable.map((s) => {
        const posterior: TrimmedDistribution = trimTails(toUnitArea(s.distribution!))
        const prior =
          s.priorDistribution && s.priorDistribution.length > 0
            ? trimTails(toUnitArea(s.priorDistribution))
            : null
        return { series: s, posterior, prior }
      }),
    [drawable]
  )

  /**
   * The calendar window to draw, and to fetch curve for.
   *
   * Taken from the posteriors rather than the curve, which runs 55,000 years
   * where the dates occupy a few centuries. Includes the priors when shown,
   * since an outlier's unmodelled calibration is routinely much wider than the
   * posterior the model pulled it into.
   *
   * Rounded to 10 years so a change too small to see does not refetch.
   */
  const window = useMemo(() => {
    const bounds: number[] = []
    lobes.forEach(({ posterior, prior }) => {
      if (posterior.ages.length > 0) {
        bounds.push(posterior.ages[0], posterior.ages[posterior.ages.length - 1])
      }
      if (showPrior && prior && prior.ages.length > 0) {
        bounds.push(prior.ages[0], prior.ages[prior.ages.length - 1])
      }
    })
    const span = spanOf(bounds)
    if (!span) return null
    const padded = padSpan(span, 0.08, 50)
    return {
      from: Math.max(0, Math.floor(padded.min / 10) * 10),
      to: Math.ceil(padded.max / 10) * 10,
    }
  }, [lobes, showPrior])

  /**
   * Which curves to draw. One analysis can mix them, and a date's posterior is
   * only explained by the curve it was calibrated against, so every curve used
   * is drawn rather than one standing in for all of them.
   */
  const curveIds = useMemo(
    () => [...new Set(drawable.map((s) => s.measurement!.curveId).filter((id): id is string => !!id))],
    [drawable]
  )

  const [curveData, setCurveData] = useState<Record<string, CurvePoint[]>>({})
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const curveKey = `${curveIds.join('|')}@${window?.from}-${window?.to}`
  useEffect(() => {
    if (!window || curveIds.length === 0) return
    let active = true
    setLoading(true)
    setLoadError(null)
    Promise.all(
      curveIds.map((id) =>
        fetchCurvePoints(id, {
          calFrom: window.from,
          calTo: window.to,
          maxPoints: CURVE_MAX_POINTS,
        }).then((w) => [id, w.points] as const)
      )
    )
      .then((loaded) => {
        if (!active) return
        setCurveData(Object.fromEntries(loaded))
      })
      .catch((err) => {
        if (!active) return
        // Named, not swallowed: without the curve this figure is a scatter of
        // lobes with nothing to read them against.
        setLoadError((err as Error).message || 'Could not load the calibration curve.')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curveKey])

  /**
   * How each curve is drawn -- fixed by which curve it is, not by the order it
   * arrived in. See theme/curveColors.ts.
   */
  const curveStyle = useMemo(() => curveStyles(curveIds), [curveIds])

  const curveNameFor = useMemo(() => {
    const byId = new Map(curves.map((c) => [c.id, c.name]))
    return (id: string) => byId.get(id) ?? id
  }, [curves])

  /**
   * Both axis ranges, computed before any trace is built, because the lobes
   * are sized as a fraction of them.
   *
   * The radiocarbon axis must hold the curve's 2σ envelope over the drawn
   * window AND every measurement out to NORMAL_SPAN_SIGMA: a curve that leaves
   * the frame is the one thing this figure cannot do without, and a Gaussian
   * clipped at the edge reads as a truncated distribution rather than a
   * cropped picture.
   */
  const ranges = useMemo(() => {
    const calendar: number[] = []
    const radiocarbon: number[] = []
    lobes.forEach(({ series: s, posterior, prior }) => {
      calendar.push(...posterior.ages)
      if (showPrior && prior) calendar.push(...prior.ages)
      const m = s.measurement!
      const sigma = isNum(m.error1Sigma) && m.error1Sigma > 0 ? m.error1Sigma : 0
      radiocarbon.push(m.ageBp - NORMAL_SPAN_SIGMA * sigma, m.ageBp + NORMAL_SPAN_SIGMA * sigma)
    })
    // Only the curves currently in play: a render between a change of curve
    // set and the response still holds the previous curve, and one two
    // thousand radiocarbon years away would set the axis for a figure that no
    // longer draws it.
    curveIds.forEach((id) => {
      ;(curveData[id] ?? []).forEach(([cal, c14, sigma]) => {
        if (!window || cal < window.from || cal > window.to) return
        radiocarbon.push(c14 - 2 * sigma, c14 + 2 * sigma)
      })
    })
    return curveAxisRanges({
      calendar: spanOf(calendar),
      radiocarbon: spanOf(radiocarbon),
    })
  }, [lobes, curveIds, curveData, showPrior, window])

  const plotData = useMemo(() => {
    const data: Data[] = []
    const legendUsed = new Set<string>()
    const claimLegend = (key: string) => {
      if (legendUsed.has(key)) return false
      legendUsed.add(key)
      return true
    }

    const xSpan = ranges.x.max - ranges.x.min
    const ySpan = ranges.y.max - ranges.y.min
    // Not a flat fraction: a large model shrinks its lobes rather than filling
    // the figure with them (see lobeFraction).
    const lobeHeight = ySpan * lobeFraction(lobes.length)
    const measurementWidth = xSpan * MEASUREMENT_LOBE_FRACTION
    // The frame's OLDER edge, drawn on the left by the reversed axis. The
    // measurement lobes grow inward from it, towards SMALLER cal BP -- that
    // sign is why this is named rather than inlined.
    const leftEdge = ranges.x.max

    // --- every curve's error envelope, under everything --------------------
    // Each in its OWN curve's hue, so an envelope reads back to its line by
    // colour rather than by guessing -- and a marine curve keeps the envelope
    // carrying exactly the uncertainty that makes a marine date wide.
    curveIds.forEach((id, index) => {
      const points = curveData[id]
      if (!points || points.length === 0) return
      const style = curveStyle[id]
      const cal = points.map((p) => p[0])
      const c14 = points.map((p) => p[1])
      const sigma = points.map((p) => p[2])
      const band = (multiple: number, color: string, suffix: string, rank: number) => ({
        x: [...cal, ...cal.slice().reverse()],
        y: [
          ...c14.map((v, i) => v + multiple * sigma[i]),
          ...c14.map((v, i) => v - multiple * sigma[i]).reverse(),
        ],
        fill: 'toself',
        fillcolor: color,
        line: { width: 0, color: 'rgba(0,0,0,0)' },
        mode: 'lines',
        // Named for its curve only when there is more than one to tell apart:
        // "IntCal20 ±1σ" beneath "IntCal20" says the name twice for no gain.
        name: `${curveIds.length > 1 ? curveNameFor(id) : 'Curve'} ${suffix}`,
        legendgroup: `curve-${id}-${suffix}`,
        legendrank: rank,
        showlegend: true,
        hoverinfo: 'skip',
      })
      // Outer first, so the ±1σ sits inside it rather than under it.
      data.push(band(2, style.band2, '±2σ', RANK.band2 + index * RANK.perCurve))
      data.push(band(1, style.band1, '±1σ', RANK.band1 + index * RANK.perCurve))
    })

    // --- one date at a time ------------------------------------------------
    lobes.forEach(({ series: s, posterior, prior }) => {
      const m = s.measurement!
      const base = m.ageBp
      const sigma = isNum(m.error1Sigma) && m.error1Sigma > 0 ? m.error1Sigma : 0

      // The projection rule at this date's radiocarbon age: followed right
      // from the measurement it crosses the curve wherever this date could
      // sit, with the posterior directly beneath. It stops at the younger end
      // of that posterior rather than running to the frame. See GUIDE_LIMIT
      // for why a crowded figure goes without.
      if (lobes.length <= GUIDE_LIMIT) {
        const youngest = Math.min(
          posterior.ages[0],
          posterior.ages[posterior.ages.length - 1],
          ...(showPrior && prior ? [prior.ages[0], prior.ages[prior.ages.length - 1]] : [])
        )
        data.push({
          x: [leftEdge, youngest],
          y: [base, base],
          mode: 'lines',
          line: { color: plot.guide, width: 1, dash: 'dot' },
          name: 'Measured age',
          legendgroup: 'guide',
          legendrank: RANK.guide,
          showlegend: claimLegend('guide'),
          hoverinfo: 'skip',
        })
      }

      // The laboratory measurement, up the radiocarbon axis at the left edge.
      // The same blue as this date's posterior because it IS the same date --
      // the figure's argument is that the wash on the left becomes the lobe on
      // the curve. What separates them is the edge: a bare fill here, a drawn
      // outline below. Raw against resolved, in one channel.
      const normal = normalDensity(base, sigma)
      if (normal.length > 0) {
        data.push(
          ...lobeTraces(
            {
              x: normal.map(([, density]) => leftEdge - density * measurementWidth),
              y: normal.map(([value]) => value),
            },
            { x: leftEdge, y: NaN },
            {
              fillcolor: plot.posteriorFill,
              // No `line`: a measurement has been resolved against nothing
              // yet, so it is drawn without an edge. See lobeTraces.
              name: 'Measurement (¹⁴C)',
              legendgroup: 'measurement',
              legendrank: RANK.measurement,
              showlegend: claimLegend('measurement'),
              customdata: normal.map(
                () =>
                  `${base.toFixed(0)} ± ${sigma.toFixed(0)} ¹⁴C BP${
                    m.reservoirCorrected ? '<br>(corrected for ΔR)' : ''
                  }`
              ),
              hovertemplate: `<b>${s.title}</b><br>%{customdata}<extra></extra>`,
            }
          )
        )
      }

      // One scale for this date, from whichever of posterior and prior is
      // taller -- the stacked view's rule. Equal area plus a shared scale is
      // what makes "shorter and wider" mean the model narrowed this date
      // rather than that the two were normalised differently.
      const peak = Math.max(posterior.peak, showPrior && prior ? prior.peak : 0)
      const scale = peak > 0 ? lobeHeight / peak : 0

      if (showPrior && prior) {
        data.push(
          ...lobeTraces(
            { x: prior.ages, y: prior.probs.map((p) => base + p * scale) },
            { x: NaN, y: base },
            {
              fillcolor: plot.priorFill,
              line: plot.priorLine,
              width: 1,
              name: 'Prior (unmodelled)',
              legendgroup: 'prior',
              legendrank: RANK.prior,
              showlegend: claimLegend('prior'),
              // Masked against the ROW's peak, not its own: both lobes draw
              // at `scale`, so a prior the model flattened is genuinely low
              // here, and thresholding against its own peak would stroke a
              // line half a pixel off the baseline.
              stroke: strokeMask(prior.probs, peak),
            }
          )
        )
      }

      data.push(
        ...lobeTraces(
          { x: posterior.ages, y: posterior.probs.map((p) => base + p * scale) },
          { x: NaN, y: base },
          {
            fillcolor: plot.posteriorFill,
            line: plot.posteriorLine,
            name: isModelled ? 'Posterior (modelled)' : 'Calibrated',
            legendgroup: 'posterior',
            legendrank: RANK.posterior,
            showlegend: claimLegend('posterior'),
            stroke: strokeMask(posterior.probs, peak),
            customdata: posterior.ages.map(
              (age, i) =>
                `${formatCalendarAge(age, dateFormat)}<br>Probability: ${posterior.probs[i].toFixed(4)}`
            ),
            hovertemplate: `<b>${s.title}</b><br>%{customdata}<extra></extra>`,
          }
        )
      )

      // Point estimates as a tick inside the date's own lobe, not a rule
      // across the figure: at this density a full-height line per date would
      // be the only thing visible.
      const marker = (value: number, color: string, dash: Dash, name: string, key: 'mean' | 'median') =>
        data.push({
          x: [value, value],
          y: [base, base + lobeHeight],
          mode: 'lines',
          line: { color, width: 1.5, dash },
          name,
          legendgroup: key,
          legendrank: RANK[key],
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

    // --- the curve line, ON TOP of the dates --------------------------------
    // Drawing order and legend order disagree deliberately (see RANK): a dozen
    // translucent lobes over a 1.5px line leave the curve visible only in the
    // gaps between dates. It goes last so it is never buried, and first in the
    // legend beside its own envelope.
    curveIds.forEach((id, index) => {
      const points = curveData[id]
      if (!points || points.length === 0) return
      const style = curveStyle[id]
      const cal = points.map((p) => p[0])
      data.push({
        x: cal,
        y: points.map((p) => p[1]),
        customdata: cal.map((c) => formatCalendarAge(c, dateFormat)),
        mode: 'lines',
        line: {
          // Fixed by which curve it is rather than where it landed in the
          // list -- see curveColors.ts.
          color: style.line,
          dash: style.dash,
          // Heavier than a lobe's outline: drawing it last is not enough on a
          // crowded figure, where at equal weight it reads as one more lobe
          // edge among sixty.
          width: 2,
        },
        name: curveNameFor(id),
        legendgroup: `curve-${id}`,
        legendrank: RANK.curve + index * RANK.perCurve,
        showlegend: true,
        hovertemplate: `<b>%{customdata}</b><br>${curveNameFor(id)}: %{y:.0f} ¹⁴C BP<extra></extra>`,
      })
    })

    return data
  }, [
    lobes,
    curveIds,
    curveData,
    curveStyle,
    curveNameFor,
    ranges,
    plot,
    showPrior,
    showMean,
    showMedian,
    isModelled,
    dateFormat,
  ])

  const ticks = useMemo(
    () => calendarAxisTicks(ranges.x.min, ranges.x.max, dateFormat),
    [ranges, dateFormat]
  )

  // Pixel budget for everything below the axis -- see curvePlotGeometry.
  const geometry = useMemo(() => curvePlotGeometry(height), [height])

  const layout = useMemo(
    (): Partial<Layout> => ({
      // As on the other two plots: hiding the modebar does not disable
      // drag-to-box-zoom, which would leave a reader zoomed in with no way out.
      dragmode: false as const,
      height,
      xaxis: {
        // An explicit standoff pins the title under the tick labels; left to
        // itself Plotly drops it against the bottom margin, where the legend is.
        title: { text: calendarAxisTitle(dateFormat), standoff: geometry.titleStandoff },
        // Oldest on the left, as everywhere else in this app, which on a cal
        // BP axis means giving the range largest-first.
        range: [ranges.x.max, ranges.x.min],
        autorange: false as const,
        zeroline: false,
        gridcolor: plot.grid,
        ...(ticks ? { tickmode: 'array', tickvals: ticks.tickvals, ticktext: ticks.ticktext } : {}),
      },
      yaxis: {
        title: { text: 'Radiocarbon Age (¹⁴C BP)' },
        range: [ranges.y.min, ranges.y.max],
        autorange: false as const,
        zeroline: false,
        gridcolor: plot.grid,
      },
      hovermode: 'closest' as const,
      font: plotFont,
      plot_bgcolor: plot.surface,
      paper_bgcolor: plot.surface,
      margin: { l: 70, r: 20, t: geometry.marginTop, b: geometry.marginBottom },
      legend: { orientation: 'h' as const, y: geometry.legendY, yanchor: 'top' as const },
      autosize: true,
    }),
    [plot, dateFormat, ranges, ticks, height, geometry]
  )

  const config = useMemo(
    (): Partial<Config> => ({ responsive: true, displayModeBar: false as const, displaylogo: false }),
    []
  )

  if (drawable.length === 0) {
    return (
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography color="textSecondary">
          No dates can be drawn against a curve here. This view needs each date's radiocarbon
          measurement, which a boundary does not have.
        </Typography>
      </Paper>
    )
  }

  return (
    <Box ref={plotBox}>
      {curveIds.length === 0 && (
        <Alert severity="info" sx={{ mb: 1 }}>
          These dates don’t record which calibration curve they were run against, so only the
          distributions are drawn.
        </Alert>
      )}
      {loadError && (
        <Alert severity="warning" sx={{ mb: 1 }}>
          {loadError} The distributions below are drawn without it.
        </Alert>
      )}
      {omitted.boundaries > 0 && (
        <Typography variant="body2" color="textSecondary" sx={{ mb: 1 }}>
          {omitted.boundaries} modelled{' '}
          {omitted.boundaries === 1 ? 'transition is' : 'transitions are'} not shown: a boundary
          has no radiocarbon measurement to read against the curve.
        </Typography>
      )}
      {omitted.dates > 0 && (
        <Alert severity="warning" sx={{ mb: 1 }}>
          {omitted.dates} {omitted.dates === 1 ? 'date is' : 'dates are'} missing from this view —
          their radiocarbon measurements didn’t come back with the analysis. The other views show
          all of them.
        </Alert>
      )}
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 1, mb: 0.5 }}>
        {loading && <CircularProgress size={16} />}
        <Button
          size="small"
          startIcon={<DownloadIcon />}
          onClick={() => downloadPlotPng(plotBox.current, 'posterior-on-curve')}
        >
          Download PNG
        </Button>
      </Box>
      <Plot
        data={plotData}
        layout={layout}
        config={config}
        useResizeHandler
        style={{ width: '100%', height: `${height}px` }}
      />
    </Box>
  )
}
