import { describe, it, expect } from 'vitest'
import { modelCanReportIntervals, modelCanReportKde } from './intervalsAvailable'
import type { MatrixMeasurement, ModelElement } from '@/types'

function el(
  id: string,
  element_type: ModelElement['element_type'],
  children: ModelElement[] = []
): ModelElement {
  return { id, element_type, name: id, tpq_age_bp: null, taq_age_bp: null, children }
}

const at = (id: string, holder: string) =>
  ({ id, element_id: holder, group_id: null }) as Pick<
    MatrixMeasurement,
    'id' | 'element_id' | 'group_id'
  >

describe('modelCanReportIntervals', () => {
  it('says yes to the ordinary shape: a phase between two boundaries', () => {
    const tree = el('root', 'sequence', [
      el('b0', 'boundary'),
      el('p1', 'phase'),
      el('b1', 'boundary'),
    ])
    expect(modelCanReportIntervals(tree, [at('a', 'p1')])).toBe(true)
  })

  it('says yes to a phase holding two dates, even with no boundaries', () => {
    // Its dated events have a span of their own, whatever encloses them.
    const tree = el('root', 'phase', [el('p1', 'phase')])
    expect(modelCanReportIntervals(tree, [at('a', 'p1'), at('b', 'p1')])).toBe(true)
  })

  it('says no to a phase holding one date and nothing else', () => {
    const tree = el('root', 'phase', [el('p1', 'phase')])
    expect(modelCanReportIntervals(tree, [at('a', 'p1')])).toBe(false)
  })

  it('says no to a wiggle match', () => {
    // The point of the whole helper. A wiggle match's members are spaced by
    // counted rings, an INPUT, so there is no duration to infer -- and it
    // falls out of the general rule rather than needing a case: no
    // Boundaries, and its dates are not in a phase.
    const tree = el('w1', 'wiggle_sequence')
    expect(
      modelCanReportIntervals(tree, [at('a', 'w1'), at('b', 'w1'), at('c', 'w1')])
    ).toBe(false)
  })

  it('says no to a deposition sequence standing on its own', () => {
    const tree = el('d1', 'deposition_sequence')
    expect(modelCanReportIntervals(tree, [at('a', 'd1'), at('b', 'd1')])).toBe(false)
  })

  it('says yes to a deposition sequence between two boundaries', () => {
    // There the Boundaries DO enclose an interval, and it is a real duration.
    const tree = el('root', 'sequence', [
      el('b0', 'boundary'),
      el('d1', 'deposition_sequence'),
      el('b1', 'boundary'),
    ])
    expect(modelCanReportIntervals(tree, [at('a', 'd1'), at('b', 'd1')])).toBe(true)
  })

  it('cannot tell without a model structure', () => {
    // Not "no": on the New Analysis form this is the state before the model
    // has been built, and hiding the option then would be wrong.
    expect(modelCanReportIntervals(null, [])).toBeNull()
    expect(modelCanReportIntervals(undefined, [])).toBeNull()
  })

  it('counts a date filed under a legacy context as well as an element', () => {
    const tree = el('root', 'phase', [el('p1', 'phase')])
    const dates = [
      { id: 'a', element_id: null, group_id: 'p1' },
      { id: 'b', element_id: null, group_id: 'p1' },
    ] as Pick<MatrixMeasurement, 'id' | 'element_id' | 'group_id'>[]
    expect(modelCanReportIntervals(tree, dates)).toBe(true)
  })
})

describe('modelCanReportKde', () => {
  it('says yes to a phase holding two dates', () => {
    const tree = el('root', 'sequence', [el('b0', 'boundary'), el('p1', 'phase'), el('b1', 'boundary')])
    expect(modelCanReportKde(tree, [at('a', 'p1'), at('b', 'p1')])).toBe(true)
  })

  it('says no where every phase has one date, boundaries or not', () => {
    const tree = el('root', 'sequence', [
      el('b0', 'boundary'),
      el('p1', 'phase'),
      el('b1', 'boundary'),
      el('p2', 'phase'),
      el('b2', 'boundary'),
    ])
    expect(modelCanReportKde(tree, [at('a', 'p1'), at('b', 'p2')])).toBe(false)
  })

  it('cannot tell before there is a model', () => {
    expect(modelCanReportKde(null, [])).toBeNull()
  })
})
