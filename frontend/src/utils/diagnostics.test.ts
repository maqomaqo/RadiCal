import { describe, it, expect } from 'vitest'
import { concernFor, concernTitle } from './diagnostics'

describe('concernFor', () => {
  it('says nothing about a date the model fits', () => {
    expect(concernFor({ agreement_index: 104, outlier_probability: 0.02 }, 0)).toBeNull()
  })

  it('measures the outlier probability against the prior, not against zero', () => {
    // Prior 0.6, posterior 0.62: the analyst's own setting came back, so the
    // run found nothing to report.
    expect(concernFor({ outlier_probability: 0.62, agreement_index: 90 }, 0.6)).toBeNull()
    // Same posterior against a routine 5% prior IS a finding.
    expect(concernFor({ outlier_probability: 0.62, agreement_index: 90 }, 0.05)?.level).toBe(
      'suspect'
    )
  })

  it('separates a rise it is sure about from one it is not', () => {
    expect(concernFor({ outlier_probability: 0.3 }, 0)?.level).toBe('check')
    expect(concernFor({ outlier_probability: 0.7 }, 0)?.level).toBe('suspect')
  })

  it('counts poor agreement only while the prior is low enough for it to be independent', () => {
    expect(concernFor({ agreement_index: 41 }, 0)?.reasons).toEqual(['agreement 41, below 60'])
    expect(concernFor({ agreement_index: 41 }, 0.4)).toBeNull()
  })

  it('names the prior in the reason when the date was given one', () => {
    expect(concernFor({ outlier_probability: 0.8 }, 0.1)?.reasons[0]).toBe(
      '80% likely an outlier -- up from the 10% prior it was given'
    )
  })

  it('has nothing to say about a date with no diagnostics', () => {
    expect(concernFor(undefined, 0.5)).toBeNull()
    expect(concernFor({ agreement_index: null, outlier_probability: null }, 0)).toBeNull()
  })
})

describe('concernTitle', () => {
  it('leads with the verdict and follows with every reason', () => {
    const concern = concernFor({ outlier_probability: 0.9, agreement_index: 12 }, 0)!
    expect(concernTitle(concern)).toBe(
      'The model does not fit this date: 90% likely an outlier; agreement 12, below 60.'
    )
  })
})
