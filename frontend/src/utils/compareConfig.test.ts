// The settings diff, and the tree diff under it.
//
// The field table's only job is to be right about what changed, so the
// properties are: a field the runs agree on is not marked as differing, one
// they disagree on is, and the summary of a many-valued field (four dates on
// one curve, one on another) says so rather than collapsing to whichever came
// first.
//
// The tree diff carries the same rule as compareDerived: match on name, never
// on position, and count what cannot be matched instead of guessing.
import { describe, expect, it } from 'vitest'

import { configFields, diffTrees, structureSummary, type ConfigRunInput } from './compareConfig'
import type { ModelElement } from '@/types'

function date(name: string, overrides: Record<string, unknown> = {}) {
  return { sampleName: name, curveLabel: 'IntCal20', ...overrides }
}

function run(name: string, overrides: Partial<ConfigRunInput> = {}): ConfigRunInput {
  return {
    name,
    analysisType: 'bayesian',
    solverLabel: 'exact solver',
    measurements: [date('OxA-1'), date('OxA-2')],
    ...overrides,
  }
}

const field = (fields: ReturnType<typeof configFields>, label: string) =>
  fields.find((f) => f.label === label)!

describe('configFields', () => {
  it('does not mark a field the runs agree on', () => {
    const fields = configFields([run('A'), run('B')])
    expect(field(fields, 'Calibration curve').changed).toBe(false)
    expect(field(fields, 'Outlier model').changed).toBe(false)
  })

  it('marks the outlier model when one run turns it on', () => {
    const fields = configFields([
      run('A'),
      run('B', {
        measurements: [date('OxA-1', { outlierPrior: 0.05, outlierModel: 'charcoal' }), date('OxA-2')],
      }),
    ])
    const outlier = field(fields, 'Outlier model')
    expect(outlier.changed).toBe(true)
    expect(outlier.values[0]).toBe('none')
    expect(outlier.values[1]).toContain('charcoal prior 0.05')
    expect(outlier.values[1]).toContain('1 of 2 dates')
  })

  it('says how many dates each curve applies to when they are not all the same', () => {
    const fields = configFields([
      run('A', { measurements: [date('OxA-1'), date('OxA-2', { curveLabel: 'Marine20' })] }),
      run('B'),
    ])
    const curve = field(fields, 'Calibration curve')
    expect(curve.changed).toBe(true)
    expect(curve.values[0]).toContain('IntCal20 on 1')
    expect(curve.values[0]).toContain('Marine20 on 1')
    expect(curve.values[1]).toBe('IntCal20')
  })

  it('reports the solver, and says nothing about draws that were never computed', () => {
    const fields = configFields([run('A'), run('B', { solverLabel: 'MH/Gibbs, 34,000 sweeps' })])
    expect(field(fields, 'Solver').changed).toBe(true)
    expect(field(fields, 'Durations and differences').values).toEqual([
      'not computed',
      'not computed',
    ])
  })

  it('distinguishes independent draws from an MCMC chain', () => {
    const fields = configFields([
      run('A', { draws: 5000, independent: true }),
      run('B', { draws: 34000, independent: false }),
    ])
    expect(field(fields, 'Durations and differences').values).toEqual([
      '5,000 independent draws',
      '34,000 MCMC draws',
    ])
  })
})

const single: ModelElement = {
  id: 'r',
  element_type: 'sequence',
  name: 'Site',
  children: [
    { id: 'b1', element_type: 'boundary', name: 'Start' },
    { id: 'p1', element_type: 'phase', name: 'J' },
    { id: 'b2', element_type: 'boundary', name: 'End' },
  ],
}

const split: ModelElement = {
  id: 'r',
  element_type: 'sequence',
  name: 'Site',
  children: [
    { id: 'b1', element_type: 'boundary', name: 'Start' },
    { id: 'p1', element_type: 'phase', name: 'J' },
    { id: 'b3', element_type: 'boundary', name: 'End of J' },
    { id: 'p2', element_type: 'phase', name: 'K' },
    { id: 'b2', element_type: 'boundary', name: 'End' },
  ],
}

describe('structureSummary', () => {
  it('counts each element type', () => {
    expect(structureSummary(single)).toBe('1 sequence, 1 phase, 2 boundaries')
    expect(structureSummary(split)).toBe('1 sequence, 2 phases, 3 boundaries')
  })

  it('says so when there is no model at all', () => {
    expect(structureSummary(null)).toBe('no model structure')
  })
})

describe('diffTrees', () => {
  it('reports what only one tree has, without disturbing what both share', () => {
    const { elements } = diffTrees([single, split])
    const byName = (name: string) => elements.find((e) => e.name === name)!

    expect(byName('Start').status).toBe('unchanged')
    expect(byName('End').status).toBe('unchanged')
    expect(byName('End of J').status).toBe('partial')
    expect(byName('End of J').presentIn).toEqual([1])
    expect(byName('K').status).toBe('partial')
  })

  it('notices an element that moved to a different parent', () => {
    const nested: ModelElement = {
      id: 'r',
      element_type: 'sequence',
      name: 'Site',
      children: [
        {
          id: 'g',
          element_type: 'phase',
          name: 'Souths',
          children: [{ id: 'p1', element_type: 'phase', name: 'J' }],
        },
      ],
    }
    const flat: ModelElement = {
      id: 'r',
      element_type: 'sequence',
      name: 'Site',
      children: [{ id: 'p1', element_type: 'phase', name: 'J' }],
    }
    const { elements } = diffTrees([nested, flat])
    const j = elements.find((e) => e.name === 'J')!
    expect(j.status).toBe('moved')
    expect(j.parents).toEqual(['Souths', 'Site'])
  })

  it('counts unnamed elements instead of matching them by position', () => {
    const unnamedTree: ModelElement = {
      id: 'r',
      element_type: 'sequence',
      name: 'Site',
      children: [
        { id: 'b1', element_type: 'boundary' },
        { id: 'b2', element_type: 'boundary' },
      ],
    }
    const { elements, unnamed } = diffTrees([unnamedTree, single])
    expect(unnamed).toEqual([2, 0])
    expect(elements.some((e) => e.elementType === 'boundary' && e.presentIn.length === 2)).toBe(
      false
    )
  })
})
