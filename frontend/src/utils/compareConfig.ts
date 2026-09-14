// What actually differs between two runs' settings.
//
// Derived from the stored configurations rather than from the analyst's memory
// of what they did three weeks ago, which is the whole point: by the time
// someone is comparing four runs of the same eleven dates, "which one had the
// charcoal outlier model on?" is not a question they can answer from the
// names. Every field here is read from the payload each analysis already
// returns, so the summary cannot drift from what was run.
//
// Structure gets its own pass. A model tree is not a scalar and a diff of two
// trees printed as strings tells a reader nothing, so elements are matched --
// by TYPE AND NAME, never by position, for the same reason boundaries are in
// compareDerived -- and reported as shared, moved, or present in only some
// runs.
import type { ElementType, ModelElement } from '@/types'

export interface ConfigMeasurement {
  sampleName: string
  /** The curve's display name, already resolved -- this module does no lookups. */
  curveLabel: string
  deltaR?: number | null
  deltaRError?: number | null
  outlierPrior?: number | null
  outlierModel?: string | null
  tpqAgeBp?: number | null
  taqAgeBp?: number | null
}

export interface ConfigRunInput {
  name: string
  analysisType: string
  measurements: ConfigMeasurement[]
  modelTree?: ModelElement | null
  /** How the numbers were computed, as the page words it elsewhere. */
  solverLabel: string
  /** Joint draws behind the durations and differences, when they were asked for. */
  draws?: number | null
  independent?: boolean | null
}

export interface ConfigField {
  label: string
  /** One display string per run, index-aligned. */
  values: string[]
  /** True when the runs do not all say the same thing. */
  changed: boolean
  /** Why this field is worth reading even when it agrees. Optional. */
  note?: string
}

function tally(items: string[]): string {
  const counts = new Map<string, number>()
  for (const item of items) counts.set(item, (counts.get(item) ?? 0) + 1)
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([label, count]) => (count === items.length ? label : `${label} on ${count}`))
    .join(', ')
}

function curveSummary(run: ConfigRunInput): string {
  const labels = run.measurements.map((m) => m.curveLabel)
  return labels.length === 0 ? 'none' : tally(labels)
}

function deltaRSummary(run: ConfigRunInput): string {
  const applied = run.measurements.filter((m) => m.deltaR != null)
  if (applied.length === 0) return 'none'
  const written = applied.map((m) =>
    m.deltaRError != null ? `${m.deltaR} ± ${m.deltaRError}` : `${m.deltaR}`
  )
  return `${tally(written)} (${applied.length} of ${run.measurements.length} dates)`
}

function outlierSummary(run: ConfigRunInput): string {
  const applied = run.measurements.filter((m) => (m.outlierPrior ?? 0) > 0)
  if (applied.length === 0) return 'none'
  const written = applied.map((m) => `${m.outlierModel ?? 'general'} prior ${m.outlierPrior}`)
  return `${tally(written)} (${applied.length} of ${run.measurements.length} dates)`
}

function limiterSummary(run: ConfigRunInput): string {
  const tpq = run.measurements.filter((m) => m.tpqAgeBp != null).length
  const taq = run.measurements.filter((m) => m.taqAgeBp != null).length
  if (tpq === 0 && taq === 0) return 'none'
  return [tpq > 0 ? `TPQ on ${tpq}` : null, taq > 0 ? `TAQ on ${taq}` : null]
    .filter(Boolean)
    .join(', ')
}

/** Counts of each element type in a tree, as a sentence. */
export function structureSummary(tree: ModelElement | null | undefined): string {
  if (!tree) return 'no model structure'
  const counts: Record<string, number> = {}
  const walk = (element: ModelElement) => {
    counts[element.element_type] = (counts[element.element_type] ?? 0) + 1
    for (const child of element.children ?? []) walk(child)
  }
  walk(tree)
  // Written out rather than suffixed with an "s": the OxCal vocabulary these
  // names come from is the app's, and "2 boundarys" in a panel about
  // Boundaries reads as a bug in the model, not in the sentence.
  const singulars: Record<ElementType, string> = {
    sequence: 'sequence',
    phase: 'phase',
    boundary: 'boundary',
    hiatus: 'hiatus',
    wiggle_sequence: 'wiggle match',
    deposition_sequence: 'deposition sequence',
    interpolated_depth: 'undated level',
  }
  const plurals: Record<ElementType, string> = {
    sequence: 'sequences',
    phase: 'phases',
    boundary: 'boundaries',
    hiatus: 'hiatuses',
    wiggle_sequence: 'wiggle matches',
    deposition_sequence: 'deposition sequences',
    interpolated_depth: 'undated levels',
  }
  const order: ElementType[] = [
    'sequence',
    'phase',
    'boundary',
    'hiatus',
    'wiggle_sequence',
    'deposition_sequence',
    'interpolated_depth',
  ]
  const parts = order
    .filter((type) => counts[type])
    .map((type) => `${counts[type]} ${counts[type] === 1 ? singulars[type] : plurals[type]}`)
  return parts.join(', ')
}

function intervalsSummary(run: ConfigRunInput): string {
  if (run.draws == null) return 'not computed'
  const kind = run.independent ? 'independent draws' : 'MCMC draws'
  return `${run.draws.toLocaleString()} ${kind}`
}

/**
 * The field-by-field diff, in reading order: what was dated, what it was
 * calibrated against, what the model did to it, and how it was solved.
 */
export function configFields(runs: ConfigRunInput[]): ConfigField[] {
  const field = (label: string, values: string[], note?: string): ConfigField => ({
    label,
    values,
    changed: new Set(values).size > 1,
    note,
  })

  return [
    field('Analysis type', runs.map((run) => run.analysisType)),
    field('Dates', runs.map((run) => `${run.measurements.length}`)),
    field('Calibration curve', runs.map(curveSummary)),
    field('Reservoir offset ΔR', runs.map(deltaRSummary)),
    field('Outlier model', runs.map(outlierSummary)),
    field('Age limiters', runs.map(limiterSummary)),
    field('Model structure', runs.map((run) => structureSummary(run.modelTree))),
    field(
      'Solver',
      runs.map((run) => run.solverLabel),
      'Note: different solvers will on their own produce minor differences in result.'
    ),
    field('Durations and differences', runs.map(intervalsSummary)),
  ]
}

// --- Structure ------------------------------------------------------------

export type ElementDiffStatus = 'unchanged' | 'moved' | 'partial'

export interface ElementDiff {
  key: string
  elementType: ElementType
  name: string
  /** Which runs contain it. */
  presentIn: number[]
  /** The containing element's name per run; null where the run lacks it, and
   *  the empty string for the tree's own root, which has no parent. */
  parents: Array<string | null>
  status: ElementDiffStatus
}

function normalise(name: string | null | undefined): string | null {
  if (!name) return null
  const key = name.trim().replace(/\s+/g, ' ').toLowerCase()
  return key.length > 0 ? key : null
}

/**
 * Elements matched across the runs' trees.
 *
 * Unnamed elements are counted, not matched: with no name there is no identity
 * to match on, and the only alternative -- position -- is exactly what would
 * pair the second boundary of one model with the second boundary of a
 * differently shaped one. The count is returned alongside so the panel can say
 * how many were left out rather than quietly under-reporting the tree.
 */
export function diffTrees(trees: Array<ModelElement | null | undefined>): {
  elements: ElementDiff[]
  unnamed: number[]
} {
  const perRun = trees.map((tree) => {
    const found = new Map<string, { elementType: ElementType; name: string; parent: string }>()
    let unnamed = 0
    const walk = (element: ModelElement, parent: string) => {
      const name = normalise(element.name)
      if (name) {
        found.set(`${element.element_type}::${name}`, {
          elementType: element.element_type,
          name: element.name as string,
          parent,
        })
      } else {
        unnamed += 1
      }
      for (const child of element.children ?? []) walk(child, element.name ?? parent)
    }
    if (tree) walk(tree, '')
    return { found, unnamed }
  })

  const order: string[] = []
  const seen = new Set<string>()
  for (const { found } of perRun) {
    for (const key of found.keys()) {
      if (seen.has(key)) continue
      seen.add(key)
      order.push(key)
    }
  }

  const elements: ElementDiff[] = order.map((key) => {
    const entries = perRun.map(({ found }) => found.get(key) ?? null)
    const first = entries.find((entry) => entry)!
    const presentIn = entries.map((entry, i) => (entry ? i : -1)).filter((i) => i >= 0)
    const parents = entries.map((entry) => (entry ? entry.parent : null))
    let status: ElementDiffStatus = 'unchanged'
    if (presentIn.length < trees.length) {
      status = 'partial'
    } else if (new Set(parents.map((parent) => normalise(parent) ?? '')).size > 1) {
      status = 'moved'
    }
    return { key, elementType: first.elementType, name: first.name, presentIn, parents, status }
  })

  return { elements, unnamed: perRun.map(({ unnamed }) => unnamed) }
}
