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
// Those phases nest inside ONE unordered Phase rather than going straight into
// the Sequence, which puts its children in ORDER where a set of contexts
// asserts none. Ordering them is the deliberate act the editor is for.
import type { ModelElement } from '@/types'

export function newElementId(): string {
  return 'el_' + Math.random().toString(36).slice(2, 11)
}

/** A date about to be placed, and the context it already carries (if any). */
export interface DateToPlace {
  id: string
  context?: string | null
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

function element(type: ModelElement['element_type'], name: string): ModelElement {
  return { id: newElementId(), element_type: type, name, tpq_age_bp: null, taq_age_bp: null, children: [] }
}

export function bootstrapModelTree(dates: DateToPlace[]): BootstrappedTree {
  // Distinct contexts in the order the dates introduce them, which is the only
  // ordering information available.
  const contextOrder: string[] = []
  const datesByContext = new Map<string, string[]>()
  const uncontexted: string[] = []

  for (const date of dates) {
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

  const body = phases.length === 1 ? phases[0] : { ...element('phase', 'Site'), children: phases }

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
