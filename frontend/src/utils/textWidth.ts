// How wide a short label will draw, worked out without a DOM to measure it in.
//
// The Harris matrix layout is pure geometry (see harrisLayout.ts): it fixes
// the width of every box before React has rendered anything, so it cannot ask
// the browser how wide a label came out. It still has to know one width -- a
// Boundary's label sits ON its rule, and the rule spans its parent, so a
// container narrower than its own boundary label pushes that label out past
// the box. That is the overflow this exists to stop.
//
// The numbers are advance widths in em, read off Roboto (the app's UI face)
// and checked against canvas measureText at 10px, the size the diagram labels
// are drawn at. They are grouped into classes rather than tabulated per
// glyph: a 200-entry table would look precise and would still be wrong for
// the fallback face, which is what a reader without Roboto sees. Grouped
// classes land within about 5% on real label text, in either direction.
//
// The estimate leans wide, and the caller adds its own slack on top. An
// overestimate costs a few pixels of empty box. An underestimate puts text
// outside the box, which is the thing being fixed.

/** i, l and their kin: glyphs that take roughly a third of an em. */
const THIN = new Set("ijltfrIJ.,:;'`!|()[]{}-".split(''))
/** The four glyphs that take close to a full em. */
const WIDE = new Set(['m', 'w', 'M', 'W'])

/** Advance width of one character, in em. */
function advance(ch: string): number {
  if (ch === ' ') return 0.26
  if (THIN.has(ch)) return 0.31
  if (WIDE.has(ch)) return 0.86
  if (ch >= '0' && ch <= '9') return 0.57
  if (ch === '%') return 0.8
  // The en dash between the two ends of a date range. Wider than a hyphen,
  // which is in THIN above, and it appears in every range this measures.
  if (ch === '–') return 0.55
  if (ch === '—') return 1
  if (ch >= 'A' && ch <= 'Z') return 0.68
  return 0.53
}

/**
 * Estimated width of `text` in px at `fontSize`, rounded up.
 *
 * `bold` widens the result by 6%. That is more than Roboto's own bold costs
 * (about 1% at this size) and about right for the sans-serif a reader without
 * Roboto falls back to, which is the wider of the two cases.
 */
export function estimateTextWidth(text: string, fontSize: number, bold = false): number {
  let em = 0
  for (const ch of text) em += advance(ch)
  return Math.ceil(em * fontSize * (bold ? 1.06 : 1))
}
