import type { MatrixMeasurement, ModelElement } from '@/types'

/**
 * Which phases hold too few dates for their Boundaries to mean much.
 *
 * A Phase is modelled as a uniform scatter of K dates between a start and an
 * end Boundary, and the expected amount by which those Boundaries extend
 * beyond the outermost date is `E[a+b] = 2w/(K-3)`, where w is the spread of
 * the dates themselves. That expression is infinite at K = 3 and negative
 * below it, which is not a defect in the arithmetic: three dates genuinely
 * carry no information about how much earlier the phase began than its oldest
 * date. The sampler reports it faithfully, so a two- or three-date phase
 * comes back with Boundaries spanning thousands of years.
 *
 * OxCal behaves identically, and the numbers are correct. They are also the
 * single most surprising thing the app prints, because a very wide range LOOKS
 * like a failed run. This module says which figures those are, so the page can
 * put a sentence beside them instead of leaving the reader to guess.
 *
 * Pure arithmetic on the stored tree, kept out of the component that renders
 * it, so the rule can be tested against a model rather than against a DOM.
 */

/**
 * The smallest K for which `E[a+b]` is finite and positive. Not a tuning
 * knob: it is where 2w/(K-3) stops dividing by zero or by a negative.
 */
export const MIN_DATES_FOR_A_BOUNDED_PHASE = 4

export interface ThinPhase {
  elementId: string
  name: string
  /** Dates anywhere in this phase's subtree, which is what its Boundaries bracket. */
  dateCount: number
}

export interface ThinPhaseReport {
  /** Thin phases by element id, in tree order. */
  phases: Map<string, ThinPhase>
  /**
   * Boundary element id -> the thin phases it brackets. A Boundary between two
   * phases belongs to both, and is weakly constrained if EITHER is thin: it is
   * pinned by the dates on both sides, so one thin side is enough to loosen it.
   */
  boundaries: Map<string, ThinPhase[]>
}

/**
 * Dates per element id, resolved the way flattenStructure resolves them:
 * element_id first, group_id as the fallback for a tree migrated from
 * contexts. Anything matching neither belongs to no phase and is not counted
 * against one.
 */
function countDatesByElement(measurements: MatrixMeasurement[]): Map<string, number> {
  const counts = new Map<string, number>()
  for (const m of measurements) {
    const key = m.element_id || m.group_id
    if (!key) continue
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  return counts
}

export function findThinPhases(
  tree: ModelElement | null | undefined,
  measurements: MatrixMeasurement[]
): ThinPhaseReport {
  const phases = new Map<string, ThinPhase>()
  const boundaries = new Map<string, ThinPhase[]>()
  if (!tree) return { phases, boundaries }

  const direct = countDatesByElement(measurements)

  // A phase's Boundaries bracket everything nested inside it, so a phase whose
  // own dates are few but which contains a well-dated sub-phase is not thin.
  // Counting the subtree rather than the element is the difference between
  // flagging that case and not.
  const subtreeCount = new Map<string, number>()
  const measure = (element: ModelElement): number => {
    let total = direct.get(element.id) || 0
    for (const child of element.children || []) total += measure(child)
    subtreeCount.set(element.id, total)
    return total
  }
  measure(tree)

  const walk = (element: ModelElement) => {
    if (element.element_type === 'phase') {
      const dateCount = subtreeCount.get(element.id) || 0
      // A phase with no dates at all is a different problem -- an empty
      // container, which the model editor already shows as empty -- and saying
      // "too few dates to constrain the Boundaries" about it would be true but
      // beside the point.
      if (dateCount > 0 && dateCount < MIN_DATES_FOR_A_BOUNDED_PHASE) {
        phases.set(element.id, {
          elementId: element.id,
          name: element.name || 'Phase',
          dateCount,
        })
      }
    }

    // Boundaries take their looseness from the siblings either side of them,
    // which is where a Sequence puts them: Boundary, Phase, Boundary, Phase...
    const children = element.children || []
    children.forEach((child, index) => {
      if (child.element_type !== 'boundary') return
      const neighbours: ThinPhase[] = []
      for (const offset of [-1, 1]) {
        const sibling = children[index + offset]
        if (!sibling) continue
        const thin = phases.get(sibling.id)
        // The sibling may not have been walked yet -- a Boundary precedes the
        // phase it opens -- so resolve it directly rather than through the map.
        if (thin) {
          neighbours.push(thin)
        } else if (sibling.element_type === 'phase') {
          const dateCount = subtreeCount.get(sibling.id) || 0
          if (dateCount > 0 && dateCount < MIN_DATES_FOR_A_BOUNDED_PHASE) {
            neighbours.push({
              elementId: sibling.id,
              name: sibling.name || 'Phase',
              dateCount,
            })
          }
        }
      }
      if (neighbours.length) boundaries.set(child.id, neighbours)
    })

    for (const child of children) walk(child)
  }
  walk(tree)

  return { phases, boundaries }
}

/**
 * The sentence to put beside the affected figures.
 *
 * One function so the phase heading, the Boundary row and the plot caption
 * cannot word it three different ways, and so the wording is testable.
 */
export function describeThinPhase(phase: ThinPhase): string {
  return (
    `${phase.name} holds ${phase.dateCount} date${phase.dateCount === 1 ? '' : 's'}. ` +
    'Fewer than four cannot say how far a phase extends beyond its outermost ' +
    'date, so the Boundaries either side of it are expected to be very wide. ' +
    'That is the model reporting what the dates support, not a failed run.'
  )
}

export function describeThinBoundary(neighbours: ThinPhase[]): string {
  const named = neighbours
    .map((p) => `${p.name} (${p.dateCount} date${p.dateCount === 1 ? '' : 's'})`)
    .join(' and ')
  return (
    `This transition is constrained by ${named}. Fewer than four dates cannot ` +
    'say how far a phase extends beyond its outermost date, so this range is ' +
    'expected to be very wide.'
  )
}
