// Which phases are too thin for their Boundaries to say anything.
//
// The rule under test is one line of arithmetic -- E[a+b] = 2w/(K-3) is finite
// only for K >= 4 -- but the part that goes wrong is not the threshold, it is
// WHICH element the count belongs to: a phase's Boundaries bracket its whole
// subtree, and a Boundary between two phases belongs to both. Those two are
// what these tests pin.
import { describe, expect, it } from 'vitest'

import {
  MIN_DATES_FOR_A_BOUNDED_PHASE,
  describeThinBoundary,
  describeThinPhase,
  findThinPhases,
} from './thinPhases'
import type { MatrixMeasurement, ModelElement } from '@/types'

function el(
  id: string,
  element_type: ModelElement['element_type'],
  name: string,
  children: ModelElement[] = []
): ModelElement {
  return { id, element_type, name, tpq_age_bp: null, taq_age_bp: null, children }
}

/** `n` dates filed under `elementId`. Only the placement matters here. */
function dates(elementId: string, n: number): MatrixMeasurement[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `${elementId}-d${i}`,
    sample_name: `${elementId.toUpperCase()}-${i}`,
    age_bp: 3000 + i,
    error_1sigma: 25,
    element_id: elementId,
  }))
}

describe('the threshold', () => {
  it('is where 2w/(K-3) stops being finite and positive', () => {
    expect(MIN_DATES_FOR_A_BOUNDED_PHASE).toBe(4)
  })

  it.each([1, 2, 3])('flags a phase of %i dates', (n) => {
    const tree = el('root', 'sequence', 'Site', [el('p1', 'phase', 'Pit fill')])
    const { phases } = findThinPhases(tree, dates('p1', n))
    expect(phases.get('p1')?.dateCount).toBe(n)
  })

  it.each([4, 5, 12])('leaves a phase of %i dates alone', (n) => {
    const tree = el('root', 'sequence', 'Site', [el('p1', 'phase', 'Pit fill')])
    const { phases } = findThinPhases(tree, dates('p1', n))
    expect(phases.size).toBe(0)
  })

  it('says nothing about an empty phase, which is a different problem', () => {
    const tree = el('root', 'sequence', 'Site', [el('p1', 'phase', 'Pit fill')])
    const { phases, boundaries } = findThinPhases(tree, [])
    expect(phases.size).toBe(0)
    expect(boundaries.size).toBe(0)
  })
})

describe('what a phase counts', () => {
  it('counts its whole subtree, not just its own dates', () => {
    // Two dates directly in the phase, six in a sub-phase. The outer phase's
    // Boundaries bracket all eight, so it is not thin -- counting only its own
    // would flag it.
    const tree = el('root', 'sequence', 'Site', [
      el('outer', 'phase', 'Building', [el('inner', 'phase', 'Floor')]),
    ])
    const measurements = [...dates('outer', 2), ...dates('inner', 6)]
    const { phases } = findThinPhases(tree, measurements)
    expect(phases.has('outer')).toBe(false)
    expect(phases.has('inner')).toBe(false)
  })

  it('still flags a thin sub-phase inside a well-dated parent', () => {
    const tree = el('root', 'sequence', 'Site', [
      el('outer', 'phase', 'Building', [el('inner', 'phase', 'Floor')]),
    ])
    const measurements = [...dates('outer', 8), ...dates('inner', 2)]
    const { phases } = findThinPhases(tree, measurements)
    expect(phases.has('outer')).toBe(false)
    expect(phases.get('inner')?.dateCount).toBe(2)
  })

  it('resolves a date by group_id when the tree was migrated from contexts', () => {
    const tree = el('root', 'sequence', 'Site', [el('p1', 'phase', 'Pit fill')])
    const legacy: MatrixMeasurement[] = [
      { id: 'a', sample_name: 'A', age_bp: 3000, error_1sigma: 25, group_id: 'p1' },
      { id: 'b', sample_name: 'B', age_bp: 3010, error_1sigma: 25, group_id: 'p1' },
    ]
    expect(findThinPhases(tree, legacy).phases.get('p1')?.dateCount).toBe(2)
  })

  it('ignores a date belonging to no element', () => {
    const tree = el('root', 'sequence', 'Site', [el('p1', 'phase', 'Pit fill')])
    const orphan: MatrixMeasurement[] = [
      { id: 'x', sample_name: 'X', age_bp: 3000, error_1sigma: 25 },
    ]
    expect(findThinPhases(tree, orphan).phases.size).toBe(0)
  })
})

describe('which boundaries are affected', () => {
  // Boundary, Phase, Boundary, Phase, Boundary -- the shape a real sequence
  // has, and the reason a boundary cannot be attributed to one phase.
  const sequence = () =>
    el('root', 'sequence', 'Site', [
      el('b0', 'boundary', 'Start'),
      el('p1', 'phase', 'Lower'),
      el('b1', 'boundary', 'Lower/Upper'),
      el('p2', 'phase', 'Upper'),
      el('b2', 'boundary', 'End'),
    ])

  it('flags the boundaries either side of a thin phase', () => {
    const measurements = [...dates('p1', 2), ...dates('p2', 9)]
    const { boundaries } = findThinPhases(sequence(), measurements)
    expect([...boundaries.keys()].sort()).toEqual(['b0', 'b1'])
    expect(boundaries.get('b1')!.map((p) => p.elementId)).toEqual(['p1'])
  })

  it('names both sides when a shared boundary has a thin phase either side', () => {
    const measurements = [...dates('p1', 2), ...dates('p2', 3)]
    const { boundaries } = findThinPhases(sequence(), measurements)
    expect(boundaries.get('b1')!.map((p) => p.elementId)).toEqual(['p1', 'p2'])
    // The outer two touch one thin phase each.
    expect(boundaries.get('b0')!.map((p) => p.elementId)).toEqual(['p1'])
    expect(boundaries.get('b2')!.map((p) => p.elementId)).toEqual(['p2'])
  })

  it('leaves every boundary alone when every phase is well dated', () => {
    const measurements = [...dates('p1', 6), ...dates('p2', 9)]
    expect(findThinPhases(sequence(), measurements).boundaries.size).toBe(0)
  })

  it('resolves the phase that follows a boundary as well as the one before it', () => {
    // b0 precedes p1 in the walk, so a naive implementation that only reads
    // phases already visited would miss it. This is that case on its own.
    const tree = el('root', 'sequence', 'Site', [
      el('b0', 'boundary', 'Start'),
      el('p1', 'phase', 'Lower'),
    ])
    const { boundaries } = findThinPhases(tree, dates('p1', 3))
    expect(boundaries.get('b0')!.map((p) => p.elementId)).toEqual(['p1'])
  })

  it('does not attribute a boundary to a phase that is not its sibling', () => {
    // The boundary is inside the phase rather than beside it, so nothing in
    // the sequence brackets anything thin.
    const tree = el('root', 'sequence', 'Site', [
      el('p1', 'phase', 'Lower', [el('b0', 'boundary', 'Internal')]),
    ])
    const { boundaries } = findThinPhases(tree, dates('p1', 2))
    expect(boundaries.size).toBe(0)
  })

  it('returns nothing for an analysis with no tree at all', () => {
    const { phases, boundaries } = findThinPhases(null, dates('p1', 2))
    expect(phases.size).toBe(0)
    expect(boundaries.size).toBe(0)
  })
})

describe('the wording', () => {
  it('agrees in number with the count it quotes', () => {
    expect(describeThinPhase({ elementId: 'p', name: 'Lower', dateCount: 1 }))
      .toContain('holds 1 date.')
    expect(describeThinPhase({ elementId: 'p', name: 'Lower', dateCount: 3 }))
      .toContain('holds 3 dates.')
  })

  it('says the width is expected rather than wrong', () => {
    const text = describeThinPhase({ elementId: 'p', name: 'Lower', dateCount: 2 })
    expect(text).toContain('not a failed run')
  })

  it('names every phase behind a shared boundary', () => {
    const text = describeThinBoundary([
      { elementId: 'p1', name: 'Lower', dateCount: 2 },
      { elementId: 'p2', name: 'Upper', dateCount: 3 },
    ])
    expect(text).toContain('Lower (2 dates) and Upper (3 dates)')
  })
})
