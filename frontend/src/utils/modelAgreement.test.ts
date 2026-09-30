import { describe, expect, it } from 'vitest'
import { modelAgreementOf, POOR_AGREEMENT } from './diagnostics'

describe('modelAgreementOf', () => {
  it('reads both indices off a solver block', () => {
    expect(modelAgreementOf({ model_agreement: 98.2, overall_agreement: 104.7 })).toEqual({
      model: 98.2,
      overall: 104.7,
      poor: false,
    })
  })

  it('is null when the run reported neither -- a plain calibration, or an older analysis', () => {
    expect(modelAgreementOf(null)).toBeNull()
    expect(modelAgreementOf(undefined)).toBeNull()
    expect(modelAgreementOf({})).toBeNull()
    expect(modelAgreementOf({ model_agreement: null, overall_agreement: null })).toBeNull()
  })

  it('reports one index without the other rather than nothing', () => {
    expect(modelAgreementOf({ overall_agreement: 91 })).toEqual({
      model: null,
      overall: 91,
      poor: false,
    })
  })

  it('screens on Amodel, which is the index Aoverall can flatter', () => {
    // The case the pair exists for: the dates look fine one at a time and the
    // joint fit does not.
    const agreement = modelAgreementOf({ model_agreement: 31, overall_agreement: 88 })
    expect(agreement?.poor).toBe(true)
    // ...and with no Amodel to screen on, Aoverall stands in for it.
    expect(modelAgreementOf({ overall_agreement: POOR_AGREEMENT - 1 })?.poor).toBe(true)
    expect(modelAgreementOf({ overall_agreement: POOR_AGREEMENT })?.poor).toBe(false)
  })
})
