/**
 * What a project re-run changed, as backend api/run_history.diff_runs reports
 * it per analysis, turned into lines a reader can scan.
 */
export interface MovedQuantity {
  kind: string
  name: string
  units: 'cal BP' | 'yr'
  before: number
  after: number
  shift: number
  modes_before: number
  modes_after: number
}

export interface RunChange {
  analysis_id: string
  name: string
  same_inputs: boolean
  moved: MovedQuantity[]
  only_before: string[]
  only_after: string[]
  converged_before: boolean | null
  converged_after: boolean | null
  convergence_changed: boolean
}

export interface RunDiff {
  shift_years: number
  compared: number
  changed: RunChange[]
}

/**
 * One quantity's change in words. A calendar age moving to a larger cal BP is
 * OLDER; a duration growing is LONGER -- said that way rather than as a
 * signed number, whose sign means opposite things in the two units.
 */
export function describeMoved(m: MovedQuantity): string {
  const years = Math.round(Math.abs(m.shift))
  const parts: string[] = []
  if (years > 0) {
    const way =
      m.units === 'cal BP' ? (m.shift > 0 ? 'older' : 'younger') : m.shift > 0 ? 'longer' : 'shorter'
    parts.push(`${years} yr ${way}`)
  }
  if (m.modes_before !== m.modes_after) {
    parts.push(`${m.modes_before} → ${m.modes_after} ranges`)
  }
  return `${m.name} ${parts.join(', ') || 'unchanged'}`
}

/** One analysis's line: its name, then everything that changed in it. */
export function describeRunChange(change: RunChange): string {
  const parts = change.moved.map(describeMoved)
  if (change.convergence_changed) {
    parts.push(change.converged_after ? 'now converged' : 'no longer converged')
  }
  if (change.only_after.length) parts.push(`new: ${change.only_after.join(', ')}`)
  if (change.only_before.length) parts.push(`gone: ${change.only_before.join(', ')}`)
  const inputs = change.same_inputs ? '' : ' (inputs changed)'
  return `${change.name}${inputs}: ${parts.join('; ')}`
}
