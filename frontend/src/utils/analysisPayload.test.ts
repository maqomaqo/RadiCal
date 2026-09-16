import { describe, it, expect } from 'vitest'
import { buildInputsPayload } from './analysisPayload'
import type { AnalysisDraft, AnalysisType } from './analysisPayload'
import type { ModelElement } from '@/types'

const TREE: ModelElement = {
  id: 'root',
  element_type: 'sequence',
  name: 'Site',
  children: [
    { id: 'b0', element_type: 'boundary', name: 'Start', children: [] },
    { id: 'p1', element_type: 'phase', name: 'Lower', children: [] },
    { id: 'b1', element_type: 'boundary', name: 'End', children: [] },
  ],
}

function draft(overrides: Partial<AnalysisDraft> = {}): AnalysisDraft {
  return {
    name: 'A site',
    analysisType: 'bayesian' as AnalysisType,
    measurements: [
      {
        id: 'm1',
        sample_name: 'Wk-1',
        age_bp: 3000,
        error_1sigma: 30,
        material_type: 'charcoal',
        sample_type: 'terrestrial',
        curve_id: 'intcal20',
        group_id: 'g1',
      },
    ],
    groups: [{ id: 'g1', name: 'Lower' }],
    relationships: [
      { id: 'r1', from_sample_id: 'g1', to_sample_id: 'g1', from_type: 'group', to_type: 'group', relationship: 'above' },
    ],
    modelTree: null,
    elementAssignments: { m1: 'p1' },
    projectId: 'proj',
    mcmc: { solver: 'auto' },
    ...overrides,
  }
}

describe('buildInputsPayload', () => {
  it('sends contexts and relationships for a Bayesian draft with no tree', () => {
    const payload = buildInputsPayload(draft())
    expect(payload.analysis_type).toBe('bayesian')
    expect(payload.groups).toHaveLength(1)
    expect(payload.relationships).toHaveLength(1)
    expect(payload).not.toHaveProperty('model_tree')
  })

  it('drops the client-only relationship id, which the backend does not accept', () => {
    const payload = buildInputsPayload(draft())
    expect(payload.relationships).toEqual([
      { from_sample_id: 'g1', to_sample_id: 'g1', from_type: 'group', to_type: 'group', relationship: 'above' },
    ])
  })

  it('sends the tree and each date its phase when one has been authored', () => {
    const payload = buildInputsPayload(draft({ modelTree: TREE }))
    expect(payload.model_tree).toBe(TREE)
    // A tree is the authoritative structure, so contexts and relationships
    // go empty rather than competing with it.
    expect(payload.groups).toEqual([])
    expect(payload.relationships).toEqual([])
    expect((payload.measurements as Array<{ element_id: string | null }>)[0].element_id).toBe('p1')
  })

  it.each<AnalysisType>(['calibration', 'group_calibration'])(
    'clears every trace of a model when the type is %s',
    (analysisType) => {
      // The case the bug was in: editing a deployed (Bayesian) analysis and
      // switching it. Leaving any of this behind either fails validation as a
      // dangling reference or leaves the analysis claiming a structure it no
      // longer has.
      const payload = buildInputsPayload(draft({ analysisType, modelTree: TREE }))
      expect(payload.analysis_type).toBe(analysisType)
      expect(payload.groups).toEqual([])
      expect(payload.relationships).toEqual([])
      expect(payload).not.toHaveProperty('model_tree')
      const [measurement] = payload.measurements as Array<{
        group_id: string | null
        element_id: string | null
        sample_name: string
      }>
      expect(measurement.group_id).toBeNull()
      expect(measurement.element_id).toBeNull()
      // ...while everything that is not structure survives: the dates
      // themselves are what is being converted, not discarded.
      expect(measurement.sample_name).toBe('Wk-1')
    }
  )

  it('sends a null project rather than an empty string', () => {
    expect(buildInputsPayload(draft({ projectId: '' })).project_id).toBeNull()
  })
})

describe('the dispersal settings reach the run', () => {
  // This is the wiring that was missing: the solver path, the API and the
  // results panel were all built while nothing in the UI could turn dispersal
  // on, so the feature was unreachable. These assert the whole trip from the
  // draft store to bayesian_config.

  it('carries the flag and the origin', () => {
    const payload = buildInputsPayload(draft({
      mcmc: {
        solver: 'auto',
        estimate_dispersal: true,
        dispersal_origin: { lat: 50.5, lon: -1.25 },
      },
    }))
    expect(payload.bayesian_config).toMatchObject({
      estimate_dispersal: true,
      dispersal_origin: { lat: 50.5, lon: -1.25 },
    })
  })

  it('carries the flag with no origin, which asks for the origin-free models', () => {
    // Not an incomplete request: the trend surface and the origin profile both
    // assume no origin, and that is what makes them the first look at a
    // dataset. A pin adds the wave of advance on top.
    const payload = buildInputsPayload(draft({
      mcmc: { solver: 'auto', estimate_dispersal: true, dispersal_origin: null },
    }))
    expect(payload.bayesian_config).toMatchObject({
      estimate_dispersal: true,
      dispersal_origin: null,
    })
  })

  it('leaves an origin behind without asking for anything', () => {
    // The backend's _dispersal_spec reads the flag, not the pin, so a stale
    // origin from a fit the analyst switched off must not re-enable it.
    const payload = buildInputsPayload(draft({
      mcmc: {
        solver: 'auto',
        estimate_dispersal: false,
        dispersal_origin: { lat: 50.5, lon: -1.25 },
      },
    }))
    expect(payload.bayesian_config).toMatchObject({ estimate_dispersal: false })
  })
})
