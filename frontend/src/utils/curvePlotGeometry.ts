/**
 * The arithmetic behind the Curve view of the posteriors -- the figure that
 * draws every date where it actually meets the calibration curve, rather than
 * on a calendar axis with the curve left implicit.
 *
 * That figure has two axes in different units and three things to fit inside
 * them at once: the curve with its error envelope, each date's laboratory
 * measurement (a normal distribution in radiocarbon years, running UP the y
 * axis), and each date's calibrated posterior (a density in calendar years,
 * running ALONG the x axis from a baseline at its own radiocarbon age). Get
 * the ranges wrong and the figure still renders -- a lobe clipped at the top
 * of the frame, or a measurement whose Gaussian runs off the left edge, look
 * like data rather than like a bug.
 *
 * Kept out of the component for the same reason stackedPlotGeometry is: it
 * can then be asserted without loading plotly.js, which needs a DOM.
 *
 * Coordinates throughout:
 *
 *   x   calendar age in cal BP, increasing into the past. The axis is drawn
 *       reversed (see the plots) so time reads left to right, but nothing
 *       here reverses anything -- ranges are returned low-to-high and the
 *       component flips them when it hands them to Plotly.
 *   y   radiocarbon age in 14C BP, increasing upward.
 */

/** How far out a laboratory measurement's normal distribution is drawn, in σ. */
export const NORMAL_SPAN_SIGMA = 4

/**
 * A normal density sampled over ±`span` σ, peak-normalised to 1.
 *
 * Peak-normalised rather than a true density because every use of it here is
 * a shape drawn to a fixed size on screen: the caller multiplies by however
 * many axis units it has decided to give the lobe. Returning the analytic
 * density instead would make that scale depend on σ, so a ±15 yr date would
 * be drawn four times taller than a ±60 yr one for no reason the reader could
 * act on.
 *
 * An unusable σ (absent, zero, negative, not finite) returns no points rather
 * than a spike of infinite height -- a date with no stated error is a date
 * this figure cannot say anything about, and drawing it as an infinitely
 * precise one would be the worst available lie.
 */
export function normalDensity(
  mean: number,
  sigma: number,
  span: number = NORMAL_SPAN_SIGMA,
  steps = 81
): Array<[number, number]> {
  if (!Number.isFinite(mean) || !Number.isFinite(sigma) || sigma <= 0) return []
  const points: Array<[number, number]> = []
  for (let i = 0; i < steps; i++) {
    const z = -span + (2 * span * i) / (steps - 1)
    points.push([mean + z * sigma, Math.exp(-0.5 * z * z)])
  }
  return points
}

/**
 * Below this fraction of its own peak, a lobe's outline is not stroked.
 *
 * The gap it leaves is not a gap in the data. A calibrated posterior on a
 * plateau is genuinely wide and genuinely near zero for most of that width,
 * and stroking it there draws a flat rule at the date's radiocarbon age
 * running the whole width of the figure. One of those is tolerable; the
 * stacked view lives with it because each row has a row to itself. Twelve of
 * them in one frame, crossing each other and the calibration curve, is the
 * figure's worst failure mode -- and every one of those rules is a line
 * saying "about zero here", which is the least informative thing on the page.
 *
 * The fill is left continuous, so the shape is still complete; only the
 * stroke breaks, and only where it is within about a pixel of the baseline.
 */
export const STROKE_VISIBILITY = 0.015

/**
 * Which points of a lobe are worth stroking -- see STROKE_VISIBILITY.
 *
 * Endpoints of a visible run are kept even when they fall below the
 * threshold, so a stroked segment still comes down to the baseline instead of
 * ending in mid-air.
 */
export function strokeMask(probs: number[], peak: number): boolean[] {
  if (!(peak > 0)) return probs.map(() => false)
  const above = probs.map((p) => p >= peak * STROKE_VISIBILITY)
  return above.map((v, i) => v || above[i - 1] === true || above[i + 1] === true)
}

/** A closed interval, low first. Empty inputs give null rather than ±Infinity. */
export interface Span {
  min: number
  max: number
}

/** The tightest span containing every value given, or null if there are none. */
export function spanOf(values: number[]): Span | null {
  const finite = values.filter((v) => Number.isFinite(v))
  if (finite.length === 0) return null
  return { min: Math.min(...finite), max: Math.max(...finite) }
}

/**
 * A span widened by a fraction of its own width, never by less than `minPad`.
 *
 * The floor matters: a single date's posterior can be twenty years wide, and
 * a percentage of twenty years is not enough room to keep a stroke off the
 * frame.
 */
export function padSpan(span: Span, fraction: number, minPad: number): Span {
  const pad = Math.max((span.max - span.min) * fraction, minPad)
  return { min: span.min - pad, max: span.max + pad }
}

/**
 * How much of the y axis one calibrated posterior's lobe is given, as a
 * fraction of the axis span.
 *
 * Each lobe is scaled to its own peak, exactly as the stacked view scales its
 * rows: heights say how precise a date is, not how probable it is. Two dates
 * with nearby radiocarbon ages therefore overlap, which is honest -- their
 * measurements really are that close, and that is usually the point being
 * made.
 */
export const LOBE_FRACTION = 0.11

/** The same, for the laboratory measurement's lobe along the x axis. */
export const MEASUREMENT_LOBE_FRACTION = 0.09

/**
 * Total lobe height the figure is willing to spend, in axis heights.
 *
 * Lobes are allowed to overlap -- two dates measured five radiocarbon years
 * apart genuinely belong five years apart, and separating them would be a
 * lie about the data. But at LOBE_FRACTION each, sixty dates ask for six and
 * a half axis heights of lobe stacked into whatever radiocarbon range they
 * happen to occupy, and on a real model (Wairau, 59 terrestrial dates inside
 * 160 radiocarbon years) that buried the calibration curve completely: the
 * one mark the whole view exists to show.
 *
 * So the per-lobe height is capped at this budget divided by the number of
 * lobes. Up to about twenty dates nothing changes; past that they shrink
 * rather than the figure filling in.
 */
const CROWDING_BUDGET = 2.5

/**
 * How much of the y axis each posterior lobe gets, for a figure holding
 * `count` of them. See LOBE_FRACTION and CROWDING_BUDGET.
 */
export function lobeFraction(count: number): number {
  if (count <= 0) return LOBE_FRACTION
  return Math.min(LOBE_FRACTION, CROWDING_BUDGET / count)
}

export interface CurveAxisInput {
  /** Calendar extent of everything being drawn on the x axis, in cal BP. */
  calendar: Span | null
  /** Radiocarbon extent of the curve envelope and the measurements, in 14C BP. */
  radiocarbon: Span | null
}

export interface CurveAxisRanges {
  /** cal BP, low first. */
  x: Span
  /** 14C BP, low first. */
  y: Span
}

/**
 * The two axis ranges for the figure, with room made for what is drawn ON the
 * axes rather than merely within them.
 *
 * The headroom is the part worth stating. A posterior lobe rises from its own
 * radiocarbon age, so the topmost date needs LOBE_FRACTION of the axis above
 * it or its lobe is cut off by the frame; a measurement lobe grows inward from
 * the left edge, so the calendar axis needs the matching allowance on its
 * older side. Both are computed from the span BEFORE the allowance is added,
 * so the arithmetic terminates instead of chasing its own tail.
 *
 * Falls back to a nominal frame when there is nothing to draw, so a caller
 * that renders before its data arrives gets an empty chart rather than an
 * exception.
 */
export function curveAxisRanges({ calendar, radiocarbon }: CurveAxisInput): CurveAxisRanges {
  const x = calendar ?? { min: 0, max: 1 }
  const y = radiocarbon ?? { min: 0, max: 1 }
  const xSpan = Math.max(x.max - x.min, 1)
  const ySpan = Math.max(y.max - y.min, 1)
  return {
    x: {
      min: x.min - xSpan * 0.04,
      // The older (left) side carries the measurement lobes, plus the same
      // margin the younger side gets.
      max: x.max + xSpan * (MEASUREMENT_LOBE_FRACTION + 0.04),
    },
    y: {
      min: y.min - ySpan * 0.04,
      max: y.max + ySpan * (LOBE_FRACTION + 0.04),
    },
  }
}

// --- Vertical geometry below the axis ---------------------------------------
//
// The same trap stackedPlotGeometry documents at length: everything under the
// plot area stacks in PIXELS -- tick labels, then the axis title, then the
// legend -- but Plotly positions a legend in PAPER units, a fraction of the
// plot area's height. A constant `legend.y` therefore means a gap that changes
// with the figure's height while the axis title stays put, and the two collide.
// Measured here before the fix: at 560px the horizontal legend landed directly
// on "Calendar Age (cal BP)".
const MARGIN_TOP = 10
const TICK_LABEL_BAND = 18
const AXIS_TITLE_STANDOFF = 8
const AXIS_TITLE_BAND = 22
const LEGEND_BAND = 30
const BOTTOM_SLACK = 6
const LEGEND_GAP_PIXELS = TICK_LABEL_BAND + AXIS_TITLE_STANDOFF + AXIS_TITLE_BAND
const MARGIN_BOTTOM = LEGEND_GAP_PIXELS + LEGEND_BAND + BOTTOM_SLACK

/**
 * Where the axis title and the legend sit for a figure of the given height.
 *
 * Same contract as stackedPlotGeometry: `legendY` is in the paper units Plotly
 * wants, derived so the legend's top edge always lands LEGEND_GAP_PIXELS below
 * the plot area whatever height the figure is given.
 */
export function curvePlotGeometry(height: number) {
  const plotAreaHeight = Math.max(1, height - MARGIN_TOP - MARGIN_BOTTOM)
  return {
    plotAreaHeight,
    marginTop: MARGIN_TOP,
    marginBottom: MARGIN_BOTTOM,
    titleStandoff: AXIS_TITLE_STANDOFF,
    legendY: -(LEGEND_GAP_PIXELS / plotAreaHeight),
  }
}
