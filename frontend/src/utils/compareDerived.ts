// The quantities a model DERIVES, matched across runs.
//
// The shift plot compares dates, which every run has by definition. This
// compares the things only a model produces -- boundary ages, phase durations,
// the separation between phases -- and those have a matching problem the dates
// do not: they are parameters OF A PARTICULAR TREE, and a tree that gains a
// phase gains boundaries and durations with no counterpart anywhere.
//
// So the rule here is the one the dates follow, applied harder: match by
// identity, and where there is no counterpart say so. A boundary is matched by
// its NAME and nothing else. Never by position in the tree -- "the second
// boundary" of a two-phase model and of a three-phase model are not the same
// parameter, and lining them up by index would compare the end of one phase
// with the end of a different one.
//
// The other rule is about axes. A boundary is a calendar age; a duration is a
// length of time; a separation is a signed length. They share no scale, no
// zero and no calendar conversion -- which is why DurationSummary in types is
// deliberately not shaped like PosteriorSummary -- so every row carries which
// axis it belongs on, and the panel that draws them keeps one scale per axis
// rather than one for the lot.
import type { DurationSummary, HPDRange, IntervalsBlock, ModelElement } from '@/types'
import { hpdEnvelopeFromHistogram, posteriorRanges } from '@/utils/hpd'

export type DerivedAxis = 'calendar' | 'duration' | 'separation'

export type DerivedKind =
  | 'boundary'
  | 'sequence_duration'
  | 'phase_duration'
  | 'hiatus_duration'
  | 'sample_duration'
  | 'phase_difference'

/**
 * What each kind of derived quantity is, in the app's own words.
 *
 * Here rather than in the panel because the CSV export writes the same words:
 * a "phase duration" cell in a spreadsheet and the row it came from on screen
 * must not describe the quantity differently.
 */
export const DERIVED_KIND_LABELS: Record<DerivedKind, string> = {
  boundary: 'modelled transition',
  sequence_duration: 'sequence duration',
  phase_duration: 'phase duration, boundary to boundary',
  hiatus_duration: 'gap between two phases, boundary to boundary',
  sample_duration: 'span of the dated events in the phase',
  phase_difference: 'phase separation',
}

export const AXIS_FOR_KIND: Record<DerivedKind, DerivedAxis> = {
  boundary: 'calendar',
  sequence_duration: 'duration',
  phase_duration: 'duration',
  hiatus_duration: 'duration',
  sample_duration: 'duration',
  phase_difference: 'separation',
}

/** One run's answer for one derived quantity, at the page's confidence. */
export interface DerivedValue {
  /** The HPD region. Calendar ages for a boundary, years for the rest. */
  ranges: HPDRange[]
  /** Median, in the same units as `ranges`. */
  point: number | null
  /**
   * Set when the region could not be re-selected at the page's confidence and
   * a stored one is shown instead -- naming the level it was actually computed
   * at, so a 95.4% figure never sits silently under a 99.7% heading. The same
   * care DurationsPanel takes with the same data.
   */
  levelNote?: string
  /**
   * Two phases either side of one shared Boundary. Their separation is zero
   * because the model says so, not because the dates do -- so no figure is
   * reported for them anywhere: not on screen, not in the CSV, not in the PDF.
   * A bare zero here would read as a measured separation, which is exactly
   * the reading the rest of the app refuses to allow.
   */
  sharedBoundary?: boolean
}

export interface DerivedRow {
  key: string
  kind: DerivedKind
  axis: DerivedAxis
  label: string
  /** Index-aligned with the runs; null where that run has no such quantity. */
  cells: Array<DerivedValue | null>
  presentIn: number[]
}

export interface DerivedRunInput {
  modelTree?: ModelElement | null
  intervals?: IntervalsBlock | null
}

function normalise(name: string | null | undefined): string | null {
  if (!name) return null
  const key = name.trim().replace(/\s+/g, ' ').toLowerCase()
  return key.length > 0 ? key : null
}

/** Every boundary in a tree that has a posterior, oldest first as the tree lists them. */
function boundariesOf(tree: ModelElement | null | undefined): ModelElement[] {
  const found: ModelElement[] = []
  const walk = (element: ModelElement) => {
    if (element.element_type === 'boundary' && element.posterior) found.push(element)
    for (const child of element.children ?? []) walk(child)
  }
  if (tree) walk(tree)
  return found
}

/**
 * A duration or separation's region at the reader's confidence.
 *
 * Re-selected with hpdEnvelopeFromHistogram, which is what DurationsPanel
 * uses on the same numbers -- NOT the 5-year rebinning a modelled calendar
 * posterior goes through. A duration's distribution already sits on its own
 * reported step, which widens for a duration spanning millennia; imposing a
 * calendar step on it would report a 3,000-year duration to a resolution its
 * own draws never had.
 */
function durationValue(summary: DurationSummary, confidence: number): DerivedValue {
  const envelope = hpdEnvelopeFromHistogram(summary.distribution, confidence)
  if (envelope) {
    return {
      ranges: [{ min: envelope[0], max: envelope[1], probability_percent: confidence * 100 }],
      point: summary.median,
    }
  }
  const oneSigma = confidence <= 0.7
  const stored = oneSigma ? summary.hpd_68 : summary.hpd_95
  if (!stored) return { ranges: [], point: summary.median }
  return {
    ranges: [{ min: stored[0], max: stored[1], probability_percent: 0 }],
    point: summary.median,
    levelNote: oneSigma ? '68.3%' : '95.4%',
  }
}

/**
 * Every derived quantity in every run, matched by kind and name.
 *
 * Row order is the reference run's, with quantities only later runs have
 * appended -- the same convention the date pairing follows, so "reference
 * analysis order" means one thing across the whole comparison.
 */
export function pairDerived(runs: DerivedRunInput[], confidence: number): DerivedRow[] {
  const perRun = runs.map((run) => {
    const values = new Map<string, { kind: DerivedKind; label: string; value: DerivedValue }>()

    for (const boundary of boundariesOf(run.modelTree)) {
      const name = normalise(boundary.name)
      // An unnamed boundary has no identity to match on. Left out rather than
      // matched positionally, which is the mistake this module exists to
      // avoid; the tree diff still reports it as part of the structure.
      if (!name || !boundary.posterior) continue
      values.set(`boundary::${name}`, {
        kind: 'boundary',
        label: boundary.name as string,
        value: {
          ranges: posteriorRanges(
            boundary.posterior.probability_distribution,
            confidence,
            true
          ),
          point: boundary.posterior.posterior_median,
        },
      })
    }

    for (const record of run.intervals?.durations ?? []) {
      const name = normalise(record.name)
      if (!name) continue
      values.set(`${record.kind}::${name}`, {
        kind: record.kind as DerivedKind,
        label: record.name,
        value: durationValue(record.years, confidence),
      })
    }

    for (const record of run.intervals?.differences ?? []) {
      // Date-to-date differences are excluded: there are O(n^2) of them, they
      // are about the dates rather than the model, and the shift plot already
      // compares the dates themselves.
      if (record.kind !== 'phase_difference') continue
      const from = normalise(record.from_name)
      const to = normalise(record.to_name)
      if (!from || !to) continue
      // Keyed on the ORDERED pair. Two runs that list the same two phases the
      // other way round have swapped the sign of every figure in the record,
      // and that is a structural difference worth showing as unmatched rather
      // than silently negating a distribution to force a pairing.
      values.set(`phase_difference::${from}->${to}`, {
        kind: 'phase_difference',
        label: `${record.from_name} → ${record.to_name}`,
        value: record.shared_boundary
          ? { ranges: [], point: null, sharedBoundary: true }
          : durationValue(record.years, confidence),
      })
    }

    return values
  })

  const order: string[] = []
  const seen = new Set<string>()
  for (const values of perRun) {
    for (const key of values.keys()) {
      if (seen.has(key)) continue
      seen.add(key)
      order.push(key)
    }
  }

  return order.map((key) => {
    const entries = perRun.map((values) => values.get(key) ?? null)
    const first = entries.find((entry) => entry)!
    return {
      key,
      kind: first.kind,
      axis: AXIS_FOR_KIND[first.kind],
      label: first.label,
      cells: entries.map((entry) => entry?.value ?? null),
      presentIn: entries.map((entry, i) => (entry ? i : -1)).filter((i) => i >= 0),
    }
  })
}

/** The rows on one axis, in the order the panel should list them. */
export function rowsOnAxis(rows: DerivedRow[], axis: DerivedAxis): DerivedRow[] {
  const kindOrder: DerivedKind[] = [
    'boundary',
    'sequence_duration',
    'phase_duration',
    'hiatus_duration',
    'sample_duration',
    'phase_difference',
  ]
  return rows
    .filter((row) => row.axis === axis)
    .sort((a, b) => kindOrder.indexOf(a.kind) - kindOrder.indexOf(b.kind))
}

/**
 * The window every row on one axis is drawn in.
 *
 * Per AXIS, never per row and never across axes. A duration axis is anchored
 * at zero because a length of time has one and the eye reads a bar's distance
 * from it; a calendar axis is not, and a separation axis has to keep zero
 * visible because that is where a gap becomes an overlap.
 */
export function axisWindow(rows: DerivedRow[], axis: DerivedAxis): [number, number] | null {
  let min = Infinity
  let max = -Infinity
  for (const row of rows) {
    for (const cell of row.cells) {
      for (const range of cell?.ranges ?? []) {
        min = Math.min(min, range.min, range.max)
        max = Math.max(max, range.min, range.max)
      }
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null
  if (axis === 'duration') min = Math.min(min, 0)
  if (axis === 'separation') {
    min = Math.min(min, 0)
    max = Math.max(max, 0)
  }
  if (max <= min) max = min + 1
  const pad = (max - min) * 0.04
  return [min - pad, max + pad]
}

/**
 * Round tick positions for an axis measured in YEARS OF LENGTH rather than
 * calendar age.
 *
 * dateFormat's calendar ruler cannot serve here: it works in cal BP and
 * converts to BC/AD, and a duration must never go through that conversion --
 * "540 years" is not a date and has no era. A separate function, because the
 * separation is the point.
 */
export function yearAxisTicks(min: number, max: number, target = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return []
  const rough = (max - min) / target
  const magnitude = Math.pow(10, Math.floor(Math.log10(rough)))
  let step = 10 * magnitude
  for (const multiple of [1, 2, 5]) {
    if (multiple * magnitude >= rough) {
      step = multiple * magnitude
      break
    }
  }
  const ticks: number[] = []
  for (let value = Math.ceil(min / step) * step; value <= max; value += step) {
    // `Math.round` of a value just below zero gives -0, which prints as "-0".
    const rounded = Math.round(value)
    ticks.push(Object.is(rounded, -0) ? 0 : rounded)
  }
  return ticks
}
