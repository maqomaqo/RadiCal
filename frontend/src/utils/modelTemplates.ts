import type { MatrixMeasurement, ModelElement } from '@/types'
import { DEFAULT_MCMC, type McmcConfig } from '@/stores/analysisStore'
import { newElementId } from '@/utils/modelTreeDefaults'

/** A saved model template, as GET /api/model-templates lists it. */
export interface ModelTemplate {
  id: string
  name: string
  description: string | null
  model_tree: ModelElement
  /** Null for a template saved without settings, which applies none. */
  bayesian_config: Record<string, unknown> | null
  phases: number
  boundaries: number
  elements: number
  created_at: string | null
  updated_at: string | null
}

// The element types a date can sit in (backend model_tree.DATE_HOLDERS).
const DATE_HOLDERS = new Set(['phase', 'wiggle_sequence', 'deposition_sequence'])

const nameKey = (name: string | null | undefined) => (name ?? '').trim().toLowerCase()

function walk(element: ModelElement, visit: (el: ModelElement) => void) {
  visit(element)
  for (const child of element.children ?? []) walk(child, visit)
}

/** The template's tree with ids of this draft's own. */
function withFreshIds(element: ModelElement): ModelElement {
  return {
    ...element,
    id: newElementId(),
    children: (element.children ?? []).map(withFreshIds),
  }
}

export interface AppliedTemplate {
  tree: ModelElement
  assignments: Record<string, string>
  /** Dates placed in a phase of the same name. */
  placed: number
  /** Dates with no phase of their name in the template, left unassigned. */
  unplaced: string[]
}

/**
 * The template as this draft's model, with each date placed in the template
 * phase whose name matches the phase it sits in now (case and surrounding
 * spaces ignored). A date with no such phase is left unassigned, for the
 * reader to place: guessing would assert a position nobody chose.
 */
export function applyTemplate(
  template: ModelElement,
  currentTree: ModelElement | null,
  measurements: MatrixMeasurement[],
  assignments: Record<string, string>,
): AppliedTemplate {
  const currentNames = new Map<string, string>()
  if (currentTree) walk(currentTree, (el) => currentNames.set(el.id, nameKey(el.name)))

  const tree = withFreshIds(template)
  const holders = new Map<string, string>()
  walk(tree, (el) => {
    const key = nameKey(el.name)
    if (DATE_HOLDERS.has(el.element_type) && key && !holders.has(key)) holders.set(key, el.id)
  })

  const placedAssignments: Record<string, string> = {}
  const unplaced: string[] = []
  for (const m of measurements) {
    const current = assignments[m.id] || m.element_id || m.group_id || ''
    const holder = holders.get(currentNames.get(current) ?? '')
    if (holder) placedAssignments[m.id] = holder
    else unplaced.push(m.sample_name)
  }
  return {
    tree,
    assignments: placedAssignments,
    placed: measurements.length - unplaced.length,
    unplaced,
  }
}

/** The settings a template carries, as this draft's settings, or null for none. */
export function templateSettings(
  config: Record<string, unknown> | null,
): Partial<McmcConfig> | null {
  if (!config) return null
  const out: Partial<McmcConfig> = {}
  for (const key of Object.keys(DEFAULT_MCMC) as (keyof McmcConfig)[]) {
    if (key === 'dispersal_origin') continue
    if (key in config) (out as Record<string, unknown>)[key] = config[key]
  }
  return out
}

/** One line saying what applying these settings changes. */
export function describeSettings(config: Partial<McmcConfig>): string {
  const parts: string[] = []
  if (config.solver) {
    parts.push(
      config.solver === 'auto'
        ? 'solver chosen automatically'
        : config.solver === 'exact'
          ? 'exact solver'
          : 'sampled (MH/Gibbs)'
    )
  }
  if (config.resolution != null) parts.push(`${config.resolution}-year resolution`)
  if (config.estimate_durations) parts.push('durations')
  if (config.estimate_kde) parts.push('KDE')
  if (config.estimate_dispersal) parts.push('dispersal')
  if (config.seed != null) parts.push(`seed ${config.seed}`)
  return parts.join(', ')
}
