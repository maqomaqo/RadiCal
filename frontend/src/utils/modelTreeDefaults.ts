// The model structure a Bayesian analysis starts from before anyone has
// authored one, and the id generator the tree editor shares with it.
//
// The starting shape is the smallest complete model: a site-wide Sequence
// bracketed by boundaries, with every date inside. Dates often arrive already
// carrying a context -- a collection's default_context_name, the phase named
// in an imported CSV, the SampleGroup a deployed analysis was created with --
// and that grouping is information the user already recorded, so it becomes
// the starting phases rather than being flattened and re-typed.
//
// Those phases nest inside ONE wrapper rather than going straight into the
// site Sequence, and the wrapper's type is a claim about the contexts:
//
//   Sequence  the dates carry depths that separate the contexts cleanly, so
//             their order is measured, not assumed (see contextOrderFromDepth)
//   Phase     anything less than that -- the contexts are all part of the
//             site and nothing here knows which came first
//
// The Phase is the safe default because a Sequence orders what it contains,
// and asserting an order the data does not support changes the modelled dates.
// Where depth does support it, seeding the Sequence saves the user re-deriving
// by hand an order their own measurements already record.
import type { ModelElement } from '@/types'

export function newElementId(): string {
  return 'el_' + Math.random().toString(36).slice(2, 11)
}

/** A date about to be placed, and the context it already carries (if any). */
export interface DateToPlace {
  id: string
  context?: string | null
  /**
   * Stratigraphic depth in cm, when the date carries one. Greater depth is
   * older. This is the only evidence available here that the contexts have a
   * real order rather than an arbitrary one -- see contextOrderFromDepth.
   */
  depth?: number | null
}

export interface BootstrappedTree {
  tree: ModelElement
  /** Which phase each date was put in, keyed by date id. */
  assignments: Record<string, string>
}

// Matches the name the backend gives the same pile when it builds a tree
// itself (_tree_from_request), so the two paths agree on what an uncontexted
// date is filed under.
const NO_CONTEXT_PHASE = 'Unassigned dates'

/**
 * The contexts in stratigraphic order, deepest (oldest) first -- or null when
 * the dates do not establish one.
 *
 * The order dates happen to arrive in is NOT evidence of anything: it is the
 * order someone typed them, or the row order of a spreadsheet. Depth is
 * different. It is a physical measurement, and greater depth is older, so
 * contexts whose depths separate cleanly ARE ordered and a Sequence states
 * something true about them.
 *
 * Every condition below has to hold, because a Sequence is a claim and a wrong
 * claim here changes the modelled dates:
 *
 *  - Two or more contexts. One context has nothing to be ordered against.
 *  - No uncontexted dates. A pile with no context has no stratigraphic
 *    position, so nothing can be ordered relative to it.
 *  - Every date carries a depth. A partial set would place some contexts on
 *    measurement and the rest on assumption.
 *  - No two contexts overlap in depth. Overlapping contexts are exactly the
 *    case where depth does NOT resolve an order, which is what a Phase is for.
 *    Touching at a shared boundary is allowed; interleaving is not.
 *
 * Anything short of that falls back to the unordered Phase, which asserts
 * nothing and leaves the ordering to the user.
 */
function contextOrderFromDepth(
  contextOrder: string[],
  datesByContext: Map<string, string[]>,
  depthById: Map<string, number>,
  uncontextedCount: number
): string[] | null {
  if (contextOrder.length < 2) return null
  if (uncontextedCount > 0) return null

  const ranges: Array<{ context: string; min: number; max: number }> = []
  for (const context of contextOrder) {
    const depths: number[] = []
    for (const id of datesByContext.get(context) || []) {
      const depth = depthById.get(id)
      if (depth === undefined) return null
      depths.push(depth)
    }
    if (depths.length === 0) return null
    ranges.push({ context, min: Math.min(...depths), max: Math.max(...depths) })
  }

  // Deepest first, because the tree is built oldest-first.
  ranges.sort((a, b) => b.min - a.min)
  for (let i = 1; i < ranges.length; i++) {
    // Sorted deepest-first, so the previous context must bottom out at or
    // below the next one's top. Any strict overlap and depth has not resolved
    // which came first.
    if (ranges[i - 1].min < ranges[i].max) return null
  }
  return ranges.map((r) => r.context)
}

function element(type: ModelElement['element_type'], name: string): ModelElement {
  return { id: newElementId(), element_type: type, name, tpq_age_bp: null, taq_age_bp: null, children: [] }
}

export function bootstrapModelTree(dates: DateToPlace[]): BootstrappedTree {
  // Distinct contexts in the order the dates introduce them, which is the only
  // ordering information available.
  const contextOrder: string[] = []
  const datesByContext = new Map<string, string[]>()
  const uncontexted: string[] = []
  const depthById = new Map<string, number>()

  for (const date of dates) {
    if (typeof date.depth === 'number' && Number.isFinite(date.depth)) {
      depthById.set(date.id, date.depth)
    }
    const context = (date.context || '').trim()
    if (!context) {
      uncontexted.push(date.id)
      continue
    }
    if (!datesByContext.has(context)) {
      datesByContext.set(context, [])
      contextOrder.push(context)
    }
    datesByContext.get(context)!.push(date.id)
  }

  const assignments: Record<string, string> = {}
  const phases: ModelElement[] = []

  for (const context of contextOrder) {
    const phase = element('phase', context)
    phases.push(phase)
    for (const id of datesByContext.get(context)!) assignments[id] = phase.id
  }

  if (uncontexted.length > 0) {
    // On its own it is the one starting phase; beside real contexts it is the
    // pile that has none, and saying so is the point.
    const phase = element('phase', phases.length > 0 ? NO_CONTEXT_PHASE : 'Phase 1')
    phases.push(phase)
    for (const id of uncontexted) assignments[id] = phase.id
  }

  // No dates at all still needs somewhere to put the first one.
  if (phases.length === 0) phases.push(element('phase', 'Phase 1'))

  // Ordered by depth where the dates prove it, unordered otherwise. The
  // wrapper's TYPE is the whole claim: a Sequence says these contexts happened
  // in this order, a Phase says only that they are all part of the site.
  const orderedContexts = contextOrderFromDepth(
    contextOrder,
    datesByContext,
    depthById,
    uncontexted.length
  )
  const phasesByContext = new Map(contextOrder.map((context, i) => [context, phases[i]]))
  const body =
    phases.length === 1
      ? phases[0]
      : orderedContexts
        ? {
            ...element('sequence', 'Site'),
            children: orderedContexts.map((context) => phasesByContext.get(context)!),
          }
        : { ...element('phase', 'Site'), children: phases }

  return {
    tree: {
      id: newElementId(),
      element_type: 'sequence',
      name: 'Site sequence',
      tpq_age_bp: null,
      taq_age_bp: null,
      children: [
        element('boundary', 'Start of sequence'),
        body,
        element('boundary', 'End of sequence'),
      ],
    },
    assignments,
  }
}
