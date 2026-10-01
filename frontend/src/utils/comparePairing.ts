// Matching the same date across two or more analyses, and measuring what
// changed between them.
//
// This is the whole of the comparison's judgement, kept away from the plot so
// it can be asserted without rendering anything. Three rules it exists to
// enforce:
//
//   Dates are matched by IDENTITY, never by position. Two analyses of "the
//   same" dates are two independent sets of rows -- deployed from a project
//   collection as copies, re-imported, or typed again -- so their orders need
//   not agree and their row counts need not either. Lining them up by index
//   would silently compare one date against another.
//
//   A date that is not in every run is NOT dropped. It goes to `unmatched`,
//   for the page to show, because a comparison that quietly omits three dates
//   is worse than one that says it cannot include them.
//
//   Nothing here computes a JOINT statistic. Two runs are two separate
//   posteriors, not a joint one, so a difference of medians and a ratio of
//   interval widths are available (both are functions of two summaries) and
//   "the probability the date moved earlier" is not -- it would need a
//   distribution over both models that was never sampled. See the note on
//   RowMetrics.
import { posteriorEnvelope, type RangeReporting } from '@/utils/hpd'

/**
 * A date's identity for matching, from its name.
 *
 * `sample_name` is the strongest key the schema offers: a RadiocarbonSample
 * deployed from a project collection is a COPY, carrying no reference back to
 * the ProjectSample it came from, so there is no id shared between two
 * analyses of the same dates. Normalising case and internal whitespace makes
 * "OxA-19883" and "oxa-19883 " the same date, which is what a user who typed
 * the second one meant.
 */
export function sampleKey(name: string | null | undefined): string | null {
  if (!name) return null
  const key = name.trim().replace(/\s+/g, ' ').toLowerCase()
  return key.length > 0 ? key : null
}

/** One date's result inside one run, reduced to what a comparison reads. */
export interface CompareResultInput {
  sampleId: string
  sampleName: string | null
  posteriorMedian?: number | null
  posteriorMean?: number | null
  distribution?: Array<[number, number]> | null
  /** The same date's unconstrained calibration, where the run reports one. */
  priorDistribution?: Array<[number, number]> | null
  agreementIndex?: number | null
  outlierProbability?: number | null
  /** Which curve this date was calibrated against in this run. A comparison
   *  across two curves is legitimate but measures something else, so the page
   *  needs to be able to say when it is looking at one. */
  curveId?: string | null
}

/** One analysis being compared. */
export interface CompareRunInput {
  id: string
  name: string
  analysisType: string
  results: CompareResultInput[]
}

/** One date, in every run that has it. `cells` is index-aligned with the runs. */
export interface PairedRow {
  key: string
  /** As the reference run writes it -- the comparison shows one spelling. */
  label: string
  cells: Array<CompareResultInput | null>
}

export interface UnmatchedRow {
  key: string
  label: string
  /** Indices of the runs that do have it. */
  presentIn: number[]
  /**
   * 'missing'   -- at least one run has no date by this name.
   * 'duplicate' -- one run has the name twice, so which of the two the other
   *                run's date corresponds to is not decidable. Excluded from
   *                the comparison entirely rather than matched to whichever
   *                came first.
   */
  reason: 'missing' | 'duplicate'
}

export interface PairedComparison {
  rows: PairedRow[]
  unmatched: UnmatchedRow[]
}

/**
 * Match every run's dates by name.
 *
 * Row order follows the FIRST run -- the reference -- with any date the
 * reference lacks appended in the order the later runs list them. That way
 * "analysis order" on the page means the reference analysis's own order,
 * which is the order its results page shows.
 */
export function pairRuns(runs: CompareRunInput[]): PairedComparison {
  const perRun = runs.map((run) => {
    const byKey = new Map<string, CompareResultInput>()
    const duplicates = new Set<string>()
    for (const result of run.results) {
      const key = sampleKey(result.sampleName)
      if (!key) continue
      if (byKey.has(key)) {
        duplicates.add(key)
        continue
      }
      byKey.set(key, result)
    }
    return { byKey, duplicates }
  })

  // Every key any run knows about, in reference-run order first.
  const order: string[] = []
  const seen = new Set<string>()
  for (const { byKey } of perRun) {
    for (const key of byKey.keys()) {
      if (seen.has(key)) continue
      seen.add(key)
      order.push(key)
    }
  }
  for (const { duplicates } of perRun) {
    for (const key of duplicates) {
      if (seen.has(key)) continue
      seen.add(key)
      order.push(key)
    }
  }

  const rows: PairedRow[] = []
  const unmatched: UnmatchedRow[] = []

  for (const key of order) {
    const cells = perRun.map(({ byKey }) => byKey.get(key) ?? null)
    const presentIn = cells.map((cell, i) => (cell ? i : -1)).filter((i) => i >= 0)
    const label = cells.find((cell) => cell)?.sampleName ?? key
    const ambiguous = perRun.some(({ duplicates }) => duplicates.has(key))

    if (ambiguous) {
      unmatched.push({ key, label, presentIn, reason: 'duplicate' })
    } else if (presentIn.length === runs.length) {
      rows.push({ key, label, cells })
    } else {
      unmatched.push({ key, label, presentIn, reason: 'missing' })
    }
  }

  return { rows, unmatched }
}

/**
 * What changed for one date, measured against the reference run.
 *
 * Every figure here is a function of the two runs' own summaries, so each is
 * defined without a joint posterior over both models. `deltaMedian` is a
 * difference of two point estimates and `widthRatio` a ratio of two interval
 * widths; neither claims a probability, and none is available -- a statement
 * like "P(this date is older under B)" needs draws from a distribution over
 * both models at once, which no run produced and no pair of marginals can be
 * assembled into.
 */
export interface RowMetrics {
  /**
   * Per run, cal BP: this run's median minus the reference run's. POSITIVE IS
   * OLDER, cal BP counting backwards from 1950. Null for the reference itself
   * and wherever either median is missing.
   */
  deltaMedian: Array<number | null>
  /** Per run, the width in years of the HPD envelope at the selected level. */
  width: Array<number | null>
  /** Per run, width divided by the reference's. Below 1 means tighter. */
  widthRatio: Array<number | null>
}

/**
 * `modelled` per run decides the resolution its ranges are reported at: whether
 * the analysis went through a solver, and at what calendar step -- the same
 * distinction utils/hpd.posteriorRanges makes, passed through so the widths measured here
 * are the same numbers the results page prints for each run.
 */
export function rowMetrics(
  row: PairedRow,
  modelled: RangeReporting[],
  confidence: number
): RowMetrics {
  const widths = row.cells.map((cell, i) => {
    if (!cell?.distribution || cell.distribution.length === 0) return null
    const envelope = posteriorEnvelope(cell.distribution, confidence, modelled[i])
    if (!envelope) return null
    return Math.abs(envelope[1] - envelope[0])
  })

  const referenceMedian = row.cells[0]?.posteriorMedian ?? null
  const referenceWidth = widths[0]

  return {
    deltaMedian: row.cells.map((cell, i) => {
      if (i === 0) return null
      const median = cell?.posteriorMedian ?? null
      if (median == null || referenceMedian == null) return null
      return median - referenceMedian
    }),
    width: widths,
    widthRatio: widths.map((width, i) => {
      if (i === 0) return null
      if (width == null || referenceWidth == null || referenceWidth === 0) return null
      return width / referenceWidth
    }),
  }
}

/** A row with its measurements, which is what the table and the sort both read. */
export interface ComparedRow extends PairedRow {
  metrics: RowMetrics
}

/**
 * How far this date moved at all -- the largest shift against the reference
 * across every other run. What "sort by shift" ranks on, so that with three
 * runs a date that one of them moves a long way still rises.
 */
export function largestShift(metrics: RowMetrics): number {
  let largest = 0
  for (const delta of metrics.deltaMedian) {
    if (delta == null) continue
    largest = Math.max(largest, Math.abs(delta))
  }
  return largest
}

export type CompareSort = 'shift' | 'analysis' | 'name'

/**
 * Sorted rows, without mutating the input.
 *
 * 'shift' puts the dates the later runs disagree with the reference about at
 * the top, which is the reading the comparison exists for. 'analysis' keeps
 * the reference analysis's own order, for reading the comparison against that
 * analysis's results page. Rows with no measurable shift sort last under
 * 'shift' rather than first, so an unmeasurable row never leads.
 */
export function sortComparedRows(rows: ComparedRow[], sort: CompareSort): ComparedRow[] {
  const sorted = [...rows]
  if (sort === 'name') {
    sorted.sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }))
  } else if (sort === 'shift') {
    sorted.sort((a, b) => {
      const shiftA = largestShift(a.metrics)
      const shiftB = largestShift(b.metrics)
      if (shiftA !== shiftB) return shiftB - shiftA
      return a.label.localeCompare(b.label, undefined, { numeric: true })
    })
  }
  return sorted
}

/**
 * The calendar window every row is drawn in: the union of every HPD envelope
 * in the comparison, padded.
 *
 * One window for the whole table, not one per row -- the point of the figure
 * is that a bar's POSITION means something, and a per-row scale would make
 * every date look equally placed. Null when nothing could be measured.
 */
export function comparisonWindow(
  rows: ComparedRow[],
  modelled: RangeReporting[],
  confidence: number,
  paddingFraction = 0.04
): [number, number] | null {
  let min = Infinity
  let max = -Infinity
  for (const row of rows) {
    row.cells.forEach((cell, i) => {
      if (!cell?.distribution || cell.distribution.length === 0) return
      const envelope = posteriorEnvelope(cell.distribution, confidence, modelled[i])
      if (!envelope) return
      min = Math.min(min, envelope[0], envelope[1])
      max = Math.max(max, envelope[0], envelope[1])
    })
  }
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return null
  const pad = (max - min) * paddingFraction
  return [min - pad, max + pad]
}
