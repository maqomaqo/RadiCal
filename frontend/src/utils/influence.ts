// Influence analysis: reading the report api/influence.py stores.
//
// Each variant carries every Boundary and duration, so the target -- which
// quantity, and which figure of it -- is chosen here, after the run, and the
// table re-sorts without re-running anything.

export interface QuantitySummary {
  name: string | null
  kind?: string
  median: number
  hpd_95: [number, number] | null
  distribution: [number, number][] | null
}

export interface RunSummary {
  boundaries: Record<string, QuantitySummary>
  durations: Record<string, QuantitySummary>
}

export interface InfluenceVariant {
  id: string
  kind: 'leave_out' | 'move' | 'outlier'
  date_id: string
  date_name: string
  label: string
  direction?: 'older' | 'younger'
  to_phase_name?: string
  summary: RunSummary | null
  solver: string | null
  error: string | null
}

export interface InfluenceReport {
  created_at: string
  kinds: string[]
  solver: string | null
  baseline: RunSummary
  variants: InfluenceVariant[]
}

export type Statistic = 'median' | 'width' | 'p_before'

export interface Quantity {
  /** `boundary:<id>` or `duration:<kind>:<element>`. */
  key: string
  group: 'boundary' | 'duration'
  name: string
}

export interface InfluenceRow {
  variant: InfluenceVariant
  value: number | null
  change: number | null
}

const DURATION_NAMES: Record<string, string> = {
  sequence_duration: 'Sequence duration',
  phase_duration: 'Phase duration',
  hiatus_duration: 'Hiatus',
  sample_duration: 'Dated events',
}

/** Every quantity the baseline has, Boundaries first, in the report's order. */
export function quantitiesOf(report: InfluenceReport): Quantity[] {
  const boundaries = Object.entries(report.baseline.boundaries).map(([id, q]) => ({
    key: `boundary:${id}`,
    group: 'boundary' as const,
    name: q.name || 'Boundary',
  }))
  const durations = Object.entries(report.baseline.durations).map(([id, q]) => ({
    key: `duration:${id}`,
    group: 'duration' as const,
    name: `${DURATION_NAMES[q.kind ?? ''] ?? 'Duration'}: ${q.name || ''}`.replace(/: $/, ''),
  }))
  return [...boundaries, ...durations]
}

function summaryOf(run: RunSummary | null, key: string): QuantitySummary | null {
  if (!run) return null
  const [group, ...rest] = key.split(':')
  const id = rest.join(':')
  return (group === 'boundary' ? run.boundaries[id] : run.durations[id]) ?? null
}

/**
 * The probability a distribution puts above `threshold` -- for a Boundary in
 * cal BP, before that year. The trapezoid rule cut at the threshold, as
 * services/statistics.p_before reads the same series.
 */
export function probabilityAbove(
  distribution: [number, number][] | null,
  threshold: number
): number | null {
  if (!distribution || distribution.length < 2) return null
  const points = [...distribution].sort((a, b) => a[0] - b[0])
  const area = (from: number) => {
    let total = 0
    for (let i = 1; i < points.length; i++) {
      const [x0, y0] = points[i - 1]
      const [x1, y1] = points[i]
      if (x1 <= from) continue
      if (x0 >= from) {
        total += ((y0 + y1) / 2) * (x1 - x0)
      } else {
        const yCut = y0 + ((y1 - y0) * (from - x0)) / (x1 - x0)
        total += ((yCut + y1) / 2) * (x1 - from)
      }
    }
    return total
  }
  const whole = area(-Infinity)
  if (whole <= 0) return null
  return Math.min(1, Math.max(0, area(threshold) / whole))
}

/** One figure of one quantity in one run, or null where it has none. */
export function figure(
  run: RunSummary | null,
  key: string,
  statistic: Statistic,
  threshold: number | null
): number | null {
  const q = summaryOf(run, key)
  if (!q) return null
  if (statistic === 'median') return q.median
  if (statistic === 'width') return q.hpd_95 ? q.hpd_95[1] - q.hpd_95[0] : null
  return threshold == null ? null : probabilityAbove(q.distribution, threshold)
}

/**
 * Every variant with its figure and its change from the model as it stands,
 * the largest change first. Variants with no figure (one that failed, or a
 * quantity it lacks) go last, in the report's order.
 */
export function rank(
  report: InfluenceReport,
  key: string,
  statistic: Statistic,
  threshold: number | null
): InfluenceRow[] {
  const base = figure(report.baseline, key, statistic, threshold)
  const rows = report.variants.map((variant) => {
    const value = figure(variant.summary, key, statistic, threshold)
    return { variant, value, change: value != null && base != null ? value - base : null }
  })
  const measured = rows.filter((row) => row.change != null)
  const unmeasured = rows.filter((row) => row.change == null)
  measured.sort((a, b) => Math.abs(b.change!) - Math.abs(a.change!))
  return [...measured, ...unmeasured]
}

export interface ChangeSense {
  /** What a positive change means, and a negative one, in words. */
  more: string
  less: string
  /** Whether a positive change is drawn to the right of the centre line. */
  positiveRight: boolean
}

/**
 * Which way a change points, in words, for the figure being read.
 *
 * A Boundary's median is in cal BP, so a positive change is OLDER -- and
 * older is drawn to the left, as on every calendar axis in the app. The
 * other figures are lengths or probabilities, where more is to the right.
 */
export function changeSense(statistic: Statistic, group: 'boundary' | 'duration'): ChangeSense {
  if (statistic === 'p_before') return { more: 'more likely', less: 'less likely', positiveRight: true }
  if (statistic === 'width') return { more: 'wider', less: 'narrower', positiveRight: true }
  if (group === 'duration') return { more: 'longer', less: 'shorter', positiveRight: true }
  return { more: 'older', less: 'younger', positiveRight: false }
}

/** A change as words: "80 yr younger", "12 yr wider", "0.239 more likely". */
export function describeChange(
  change: number | null,
  statistic: Statistic,
  group: 'boundary' | 'duration'
): string {
  if (change == null) return '—'
  const sense = changeSense(statistic, group)
  const size = statistic === 'p_before' ? Math.abs(change).toFixed(3) : Math.round(Math.abs(change))
  if (Number(size) === 0) return 'no change'
  const unit = statistic === 'p_before' ? '' : ' yr'
  return `${size}${unit} ${change > 0 ? sense.more : sense.less}`
}
