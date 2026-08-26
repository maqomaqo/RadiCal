// How wide a short label will draw, worked out without a DOM to measure it in.
//
// The Harris matrix layout is pure geometry (harrisLayout.ts): it fixes every
// box's width before React renders, so it cannot ask the browser how wide a
// label came out. It still needs one width -- a Boundary's label sits ON its
// rule, which spans its parent, so a container narrower than its own boundary
// label pushes that label past the box.
//
// The numbers are advance widths in em, read off Roboto and checked against
// canvas measureText at 10px. Grouped into classes rather than tabulated per
// glyph: a 200-entry table would look precise and still be wrong for the
// fallback face. Grouped classes land within about 5% on real label text.
//
// The estimate leans wide and the caller adds slack on top: an overestimate
// costs a few pixels of empty box, an underestimate puts text outside it.

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
  // The en dash between the ends of a date range: wider than the hyphen in
  // THIN above, and present in every range this measures.
  if (ch === '–') return 0.55
  if (ch === '—') return 1
  if (ch >= 'A' && ch <= 'Z') return 0.68
  return 0.53
}

/**
 * Estimated width of `text` in px at `fontSize`, rounded up. `bold` widens it
 * by 6% -- more than Roboto's own bold costs, and about right for the
 * sans-serif a reader without Roboto falls back to.
 */
export function estimateTextWidth(text: string, fontSize: number, bold = false): number {
  let em = 0
  for (const ch of text) em += advance(ch)
  return Math.ceil(em * fontSize * (bold ? 1.06 : 1))
}
