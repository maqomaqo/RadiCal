import { describe, expect, it } from 'vitest'
import { describeMoved, describeRunChange, type MovedQuantity, type RunChange } from './runDiff'

const boundary = (shift: number, modes: [number, number] = [1, 1]): MovedQuantity => ({
  kind: 'boundary', name: 'Start', units: 'cal BP', before: 600, after: 600 + shift,
  shift, modes_before: modes[0], modes_after: modes[1],
})

describe('describeMoved', () => {
  it('says older or younger for a calendar age', () => {
    expect(describeMoved(boundary(23))).toBe('Start 23 yr older')
    expect(describeMoved(boundary(-12))).toBe('Start 12 yr younger')
  })

  it('says longer or shorter for a duration', () => {
    expect(describeMoved({ ...boundary(15), kind: 'phase_duration', name: 'Layer', units: 'yr' }))
      .toBe('Layer 15 yr longer')
  })

  it('names a range gained or lost', () => {
    expect(describeMoved(boundary(3, [2, 1]))).toBe('Start 3 yr older, 2 → 1 ranges')
  })
})

describe('describeRunChange', () => {
  it('lists every change in one line', () => {
    const change: RunChange = {
      analysis_id: 'a', name: 'REAL-Mp-NOE', same_inputs: true,
      moved: [boundary(-4, [2, 1])], only_before: [], only_after: [],
      converged_before: true, converged_after: false, convergence_changed: true,
    }
    expect(describeRunChange(change)).toBe(
      'REAL-Mp-NOE: Start 4 yr younger, 2 → 1 ranges; no longer converged'
    )
  })

  it('says when the inputs changed too', () => {
    const change: RunChange = {
      analysis_id: 'a', name: 'M', same_inputs: false, moved: [boundary(30)],
      only_before: [], only_after: [], converged_before: true, converged_after: true,
      convergence_changed: false,
    }
    expect(describeRunChange(change)).toBe('M (inputs changed): Start 30 yr older')
  })
})
