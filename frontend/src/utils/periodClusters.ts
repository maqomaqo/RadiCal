/**
 * Grouping a list of analyses into periods that can each share one calendar
 * axis.
 *
 * A shared axis is what makes two analyses comparable at a glance -- put
 * them on separate axes and a 100-year model and a 5,000-year one draw the
 * same width, which is the opposite of what the mark is for. But one axis
 * across the whole list only works while the list is roughly one period. Add
 * a Palaeolithic date to a medieval corpus and every medieval analysis
 * collapses into a hairline at one end.
 *
 * So the axis is shared per cluster rather than per page, and this decides
 * where the clusters break.
 */

/** Everything clustering needs from an analysis: where it sits, and when it was made. */
export interface Periodic {
  /** [min, max] cal BP across the analysis's 95% HPD, or null if unsolved. */
  calendar_span: [number, number] | null
  created_at: string
}

export interface PeriodCluster<T> {
  items: T[]
  /** [min, max] cal BP covering every member. */
  span: [number, number]
  /**
   * Whether every member falls after 1 BC, and so can be reported as AD/CE.
   * Resolved per cluster, not per analysis: the cluster's ruler and the rows
   * beneath it have to agree about what unit they are in.
   */
  isCurrentEra: boolean
}

/**
 * How much wider than its narrowest member a cluster may get before it stops
 * being one readable axis. At 10x, the narrowest analysis still occupies a
 * legible tenth of the width; past that it starts reading as a tick mark.
 */
const MAX_SPAN_RATIO = 10

// Radiocarbon "Present" is 1950; a span entirely below it is entirely AD.
const RADIOCARBON_PRESENT = 1950

const width = (span: [number, number]) => span[1] - span[0]

const newest = <T extends Periodic>(items: T[]) =>
  items.reduce((max, i) => (i.created_at > max ? i.created_at : max), '')

/**
 * Cluster by period, then order by recency.
 *
 * Both orderings matter and they are not the same one. Membership is
 * chronological, because that is what an axis can be shared across. Order --
 * of the clusters, and of the rows inside each -- stays newest-first,
 * because the surface this feeds is a list of *recent* analyses, and
 * silently re-sorting it into a chronological index would answer a question
 * nobody asked.
 *
 * Analyses with no span (nothing solved yet) cluster nowhere; they are
 * returned separately rather than dropped, so the caller can still list
 * them.
 */
export function clusterByPeriod<T extends Periodic>(
  items: T[]
): { clusters: PeriodCluster<T>[]; unplaced: T[] } {
  const unplaced = items.filter((i) => !i.calendar_span)
  const placed = items.filter((i) => i.calendar_span)

  // Walk oldest-first so "the next analysis" is always the next one along
  // the axis, which is what makes a single pass enough.
  const byPeriod = [...placed].sort((a, b) => {
    const [aLo, aHi] = a.calendar_span as [number, number]
    const [bLo, bHi] = b.calendar_span as [number, number]
    return bLo + bHi - (aLo + aHi)
  })

  const groups: T[][] = []
  let span: [number, number] | null = null
  let narrowest = Infinity

  for (const item of byPeriod) {
    const itemSpan = item.calendar_span as [number, number]

    if (span) {
      const merged: [number, number] = [
        Math.min(span[0], itemSpan[0]),
        Math.max(span[1], itemSpan[1]),
      ]
      // Gap is zero whenever the two overlap, and overlapping analyses are
      // the same period whatever their widths -- so a wide survey never
      // splits off the dates sitting inside it.
      const gap = Math.max(0, Math.max(span[0] - itemSpan[1], itemSpan[0] - span[1]))
      const stillReadable =
        width(merged) <= MAX_SPAN_RATIO * Math.min(narrowest, width(itemSpan))

      if (gap === 0 || stillReadable) {
        groups[groups.length - 1].push(item)
        span = merged
        narrowest = Math.min(narrowest, width(itemSpan))
        continue
      }
    }

    groups.push([item])
    span = itemSpan
    narrowest = width(itemSpan)
  }

  const clusters = groups.map((group) => {
    const lo = Math.min(...group.map((i) => (i.calendar_span as [number, number])[0]))
    const hi = Math.max(...group.map((i) => (i.calendar_span as [number, number])[1]))
    return {
      items: [...group].sort((a, b) => b.created_at.localeCompare(a.created_at)),
      span: [lo, hi] as [number, number],
      // Strictly below 1950 cal BP: at or above it the cluster reaches into
      // BC, and a cluster with any BC in it is reported in cal BP whole.
      isCurrentEra: hi < RADIOCARBON_PRESENT,
    }
  })

  clusters.sort((a, b) => newest(b.items).localeCompare(newest(a.items)))
  return { clusters, unplaced }
}
