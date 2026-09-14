import { describe, it, expect, beforeEach } from 'vitest'
import { useAnalysisDraftStore } from './analysisStore'
import type { DraftMeasurement, ModelElement } from '@/types'

const tree: ModelElement = {
  id: 'root',
  element_type: 'sequence',
  children: [{ id: 'p1', element_type: 'phase', name: 'Layer 1' }],
}

function date(id: string, context: string | null): DraftMeasurement {
  return {
    id,
    sample_name: id,
    age_bp: 3000,
    error_1sigma: 30,
    material_type: 'charcoal',
    sample_type: 'terrestrial',
    curve_id: 'intcal20',
    default_context_name: context,
  } as DraftMeasurement
}

describe('analysis draft store phase assignments', () => {
  beforeEach(() => {
    useAnalysisDraftStore.getState().reset()
    useAnalysisDraftStore.getState().setModelTree(tree, {})
  })

  it('files a date added after the tree exists under the phase its context names', () => {
    useAnalysisDraftStore.getState().addMeasurement(date('a', 'Layer 1'))
    expect(useAnalysisDraftStore.getState().elementAssignments).toEqual({ a: 'p1' })
  })

  it('leaves a date unassigned when no phase matches its context', () => {
    useAnalysisDraftStore.getState().addMeasurement(date('b', 'Layer 9'))
    useAnalysisDraftStore.getState().addMeasurement(date('c', null))
    expect(useAnalysisDraftStore.getState().elementAssignments).toEqual({})
  })

  it('drops the assignment of a removed date', () => {
    useAnalysisDraftStore.getState().addMeasurement(date('a', 'Layer 1'))
    useAnalysisDraftStore.getState().removeMeasurement('a')
    expect(useAnalysisDraftStore.getState().elementAssignments).toEqual({})
    expect(useAnalysisDraftStore.getState().measurements).toEqual([])
  })
})
