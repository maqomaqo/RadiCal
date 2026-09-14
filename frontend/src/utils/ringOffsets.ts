// Ring NUMBERS into ring OFFSETS, and the pre-run warning about series length.
//
// People have ring numbers -- "this block is rings 41-50 of the sequence" --
// and the model wants each date's distance from a reference. Converting by
// hand is where the sign goes wrong, and a wiggle match with the sign inverted
// does not look wrong: it produces a tight range in the WRONG PLACE, which
// reads exactly like a good answer. So the conversion lives here, is tested,
// and states its convention once.
//
//   ring_offset is years BEFORE the reference member. It is 0 AT the
//   reference and INCREASES INTO THE PAST, so a member's calendar age is the
//   reference's PLUS its offset.
//
// Same sentence as services/wiggle_match.py's module docstring and the OxCal
// importer's. See docs/AGE_DEPTH_AND_WIGGLE_MATCH_PLAN.md §4.4.

/** A date and the ring number it was cut from, as the entry table has them. */
export interface RingNumbered {
  id: string
  /**
   * Where in the series this sample sits, counted the way dendrochronologists
   * count: ring 1 is the INNERMOST (oldest) and the number increases outwards
   * towards the bark.
   */
  ring: number
}

/**
 * Ring numbers into offsets from one nominated reference, keyed by date id.
 *
 * Rings count forward in time and cal BP counts backward, so the offset is the
 * reference's ring number MINUS this sample's: a sample nearer the pith has a
 * lower ring number, sits further in the past, and gets a positive offset.
 *
 * `referenceId` names the member offset 0 belongs to. Left out, the OUTERMOST
 * ring is used -- the conventional reference, and the one a felling date is
 * quoted against.
 */
export function offsetsFromRingNumbers(
  samples: RingNumbered[],
  referenceId?: string | null
): Record<string, number> {
  if (samples.length === 0) return {}
  const reference =
    samples.find((s) => s.id === referenceId) ??
    samples.reduce((outermost, s) => (s.ring > outermost.ring ? s : outermost), samples[0])

  const out: Record<string, number> = {}
  for (const sample of samples) out[sample.id] = reference.ring - sample.ring
  return out
}

/** Which member offset 0 refers to, given a set of offsets. */
export function referenceFromOffsets(
  samples: Array<{ id: string; ring_offset?: number | null }>
): string | null {
  const at_zero = samples.find((s) => s.ring_offset === 0)
  return at_zero ? at_zero.id : null
}

/** The span of a series in its own units: the outermost gap minus the innermost. */
export function seriesSpan(offsets: Array<number | null | undefined>): number {
  const real = offsets.filter((o): o is number => typeof o === 'number' && Number.isFinite(o))
  if (real.length < 2) return 0
  return Math.max(...real) - Math.min(...real)
}

/**
 * A series has to span enough of the calibration curve's structure to tell one
 * of its wiggles from the next. Below this, a match at a plateau routinely
 * comes back split into several ranges -- barely better than a single date --
 * and no number of samples inside that span fixes it.
 *
 * Measured against IntCal20 at the Hallstatt plateau: eight samples over 140
 * rings came back in more than one range 92% of the time, and over 250 rings
 * 8% of the time. The threshold sits between those, at the point where the
 * answer collapses to a single range and stays there.
 */
export const SHORT_SERIES_SPAN = 200

/**
 * Whether to warn before the run, and what to say.
 *
 * LENGTH decides whether a match resolves at all; COUNT decides how tightly.
 * Twelve samples packed into 60 rings will not break a plateau, and that is
 * worth saying before anyone commissions the dates rather than after the model
 * comes back split in two.
 */
export function shortSeriesWarning(
  offsets: Array<number | null | undefined>,
  units = 'rings'
): string | null {
  const span = seriesSpan(offsets)
  if (span === 0 || span >= SHORT_SERIES_SPAN) return null
  return (
    `This series spans ${Math.round(span)} ${units}. A match needs to cover enough of the ` +
    'calibration curve to tell one of its wiggles from the next, and below about ' +
    `${SHORT_SERIES_SPAN} ${units} the answer often comes back in two or more separate ranges. ` +
    'Adding more samples within the same span does not fix that — extending the series does.'
  )
}
