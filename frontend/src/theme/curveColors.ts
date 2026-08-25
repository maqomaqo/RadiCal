import { alpha } from '@mui/material/styles'
import { PHASE_GREEN } from './diagramColors'

/**
 * How each calibration curve is drawn, keyed by which curve it is.
 *
 * A curve's appearance used to depend on its POSITION in the figure: whichever
 * one happened to be listed first took the app's blue and a solid line, and
 * anything after it took grey and a dash. So SHCal20 was the blue line in one
 * analysis and the grey dashed one in the next, with nothing about the curve
 * having changed -- only which dates the reader happened to open. That makes
 * the strongest signal on the figure (its colour) mean nothing at all, and
 * quietly implies a ranking between two curves that are simply different.
 *
 * Here a curve's style is a property OF THE CURVE. IntCal20 is the same green
 * in every figure in the app, and Marine20 the same dashed violet, so a reader
 * learns the palette once. That is the same premise the model diagrams run on
 * (see diagramColors.ts): a hue is a claim about what something IS.
 *
 * Deliberately not the app's primary blue. That belongs to the dates -- their
 * measurements and their posteriors -- and this palette sits in the same
 * figure as them: a curve in the same blue as the lobes drawn over it is the
 * one collision that makes the Curve view unreadable.
 *
 * Dash is fixed alongside hue rather than left to chance, so the curves are
 * still told apart in a greyscale print, which is where a lot of these figures
 * end up.
 */

/** Plotly's dash vocabulary, narrowed to the patterns this palette uses. */
export type CurveDash = 'solid' | 'dash' | 'dot' | 'dashdot' | 'longdash' | 'longdashdot'

export interface CurveStyle {
  /** The curve line itself. */
  line: string
  dash: CurveDash
  /** Fill for the ±1σ envelope. */
  band1: string
  /** Fill for the ±2σ envelope, which the ±1σ sits inside. */
  band2: string
}

/**
 * Envelope strengths.
 *
 * Weaker than a single curve's envelope used to be, because there can now be
 * more than one of them on a figure: at the old 0.72 two overlapping ±1σ bands
 * composited to a flat dark mass with no edge visible between them, which is
 * the one thing a reader comparing two curves is looking for. At these values
 * an overlap reads as a blend of two hues, and each band still reads as a band.
 */
const BAND_1_ALPHA = 0.55
const BAND_2_ALPHA = 0.22

/**
 * The palette, in assignment order.
 *
 * Five slots, not more, because five is how many hues this figure actually
 * has room for. Three are already spoken for elsewhere and cannot be borrowed
 * -- the dates' blue, the diagrams' Phase green, the Boundary coral -- and
 * what is left has to stay clear of those AND of each other. Every pair here
 * is at least 53° apart, and every one is at least 51° from the dates' blue.
 * A sixth would have had to squeeze into the 24° between the green and the
 * blue, which is not a distinguishable colour; it is a colour that LOOKS like
 * a mistake. Past five, curveStyles falls back to reusing a slot, which is
 * honest about the limit rather than pretending past it.
 *
 * Contrast on white runs 4.8:1 to 6.1:1 -- comfortably past the 3:1 a 2px
 * line needs, and past 4.5:1 as well so the legend swatches hold up in a
 * printed figure.
 */
const SLOTS: Array<{ line: string; dash: CurveDash }> = [
  // Violet. See BUILTIN_SLOT for why the marine curve, and not this one, holds
  // the green.
  { line: '#7a5c99', dash: 'solid' },
  // Deep raspberry.
  //
  // This slot was an ochre, and an ochre is where the warm quadrant inevitably
  // lands once it is dark enough to hold a 2px line against white: a darkened
  // orange IS brown, and its envelope washed out to beige. Every warm
  // candidate tested had the same problem, because the problem is the hue
  // region and not the shade. The stretch between the violet and the coral is
  // the only span of the wheel this palette has not already claimed, and it
  // holds a saturated colour at full contrast without going muddy -- the
  // envelope washes to a clean rose rather than to mud.
  { line: '#a8386f', dash: 'solid' },
  // The app's own green, shared with the diagrams' Phase. Nothing in the Curve
  // view draws a Phase, so there is no object here that green already means.
  { line: PHASE_GREEN, dash: 'dash' },
  // The two slots below are only ever reached by imported curves; the
  // built-ins have first claim on the ones above.
  { line: '#b35a1f', dash: 'dashdot' },
  { line: '#4a7524', dash: 'dot' },
]

/** How many distinct styles the palette holds. See SLOTS for why it is five. */
export const CURVE_PALETTE_SIZE = SLOTS.length

/**
 * Fixed slots for the curves that ship with the app.
 *
 * Spend the contrast on the pairings that actually happen. Only two ever do --
 * a coastal site dates shell alongside charcoal, so it runs Marine20 with ONE
 * terrestrial curve. IntCal20 beside SHCal20 is a site that is somehow in both
 * hemispheres, which is to say a mistake. So Marine20 takes the hue furthest
 * from BOTH terrestrial curves, and the two terrestrial curves are left as the
 * closest pair in the palette -- 105° at worst against the marine curve, and
 * the 61° gap parked on the pairing nobody will see.
 *
 * Marine20 also takes the DASH, for the same reason it takes the far hue: it
 * is the curve that shares a figure. Where two curves cross, a difference in
 * pattern separates them better than hue alone, and it survives a greyscale
 * print. The two terrestrial curves stay solid; neither is the lesser of a
 * pair it is never in.
 */
const BUILTIN_SLOT: Record<string, number> = {
  intcal20: 0, // violet, solid
  shcal20: 1, // raspberry, solid
  marine20: 2, // green, dashed -- the far hue, see above
}

/** Slots an imported curve can be given -- the built-ins keep the rest. */
const IMPORTED_FROM = Object.keys(BUILTIN_SLOT).length

/**
 * FNV-1a over the curve's id.
 *
 * Any stable hash would do; what matters is that it depends on nothing but
 * the id, so an imported curve looks the same today, tomorrow, and in a
 * colleague's copy of the same analysis.
 */
function hash(id: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

/** Which palette slot a curve claims on its own, before any collision. */
export function curveSlot(curveId: string): number {
  const builtin = BUILTIN_SLOT[curveId]
  if (builtin !== undefined) return builtin
  const spare = SLOTS.length - IMPORTED_FROM
  return IMPORTED_FROM + (hash(curveId) % spare)
}

/** The drawing style for one palette slot. */
export function styleForSlot(slot: number): CurveStyle {
  const { line, dash } = SLOTS[((slot % SLOTS.length) + SLOTS.length) % SLOTS.length]
  return {
    line,
    dash,
    band1: alpha(line, BAND_1_ALPHA),
    band2: alpha(line, BAND_2_ALPHA),
  }
}

/**
 * Styles for a set of curves drawn together.
 *
 * Each curve takes its own slot (see curveSlot), which is what makes the
 * palette stable across analyses. Two IMPORTED curves can hash to the same
 * slot, though, and two identical curves in one figure is a worse failure than
 * one of them not matching what it looked like elsewhere -- so a collision
 * steps the later curve to the next free slot. The built-ins never collide,
 * and neither does any figure drawing one curve, which between them is
 * essentially every figure.
 *
 * Order matters only when a collision happens, and the caller's order is the
 * model's own, so the same analysis resolves the same way every time.
 */
export function curveStyles(curveIds: string[]): Record<string, CurveStyle> {
  const taken = new Set<number>()
  const styles: Record<string, CurveStyle> = {}
  for (const id of curveIds) {
    if (styles[id]) continue
    let slot = curveSlot(id)
    for (let step = 0; step < SLOTS.length && taken.has(slot); step++) {
      slot = (slot + 1) % SLOTS.length
    }
    taken.add(slot)
    styles[id] = styleForSlot(slot)
  }
  return styles
}
