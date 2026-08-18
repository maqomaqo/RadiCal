// The bulk-paste parser. Its curve column used to be checked against a
// hardcoded set living in this same file; it now takes the ids the backend
// registry actually offers, which is what makes the two agree.
import { describe, expect, it } from 'vitest'

import { parsePastedDates } from './ProjectSampleTable'

const VALID = new Set(['intcal20', 'shcal20', 'marine20'])

describe('parsePastedDates', () => {
  it('reads name, age and error, defaulting the curve', () => {
    const { rows, errors } = parsePastedDates('OxA-1234, 3000, 25', VALID)
    expect(errors).toEqual([])
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({
      sample_name: 'OxA-1234',
      age_bp: 3000,
      error_1sigma: 25,
      material_type: 'unknown',
      curve_id: 'intcal20',
    })
  })

  it('accepts a curve the backend offers', () => {
    const { rows, errors } = parsePastedDates('S1, 3000, 25, shell, Pit 12, marine20', VALID)
    expect(errors).toEqual([])
    expect(rows[0].curve_id).toBe('marine20')
  })

  it('rejects a curve the backend does not have, naming the ones it does', () => {
    const { rows, errors } = parsePastedDates('S1, 3000, 25, wood, Pit 12, intcal13', VALID)
    expect(rows).toEqual([])
    expect(errors[0]).toContain('intcal13')
    expect(errors[0]).toContain('marine20')
  })

  it('leaves the curve to the backend when the list could not be loaded', () => {
    // Refusing every line because a request we made failed would blame the
    // paste for something it did not do.
    const { rows, errors } = parsePastedDates('S1, 3000, 25, wood, Pit 12, intcal13', new Set())
    expect(errors).toEqual([])
    expect(rows[0].curve_id).toBe('intcal13')
  })

  it('reports a malformed line by number and keeps the rest', () => {
    const { rows, errors } = parsePastedDates('S1, 3000, 25\nbroken\nS3, 3100, 30', VALID)
    expect(rows.map((r) => r.sample_name)).toEqual(['S1', 'S3'])
    expect(errors).toHaveLength(1)
    expect(errors[0]).toContain('Line 2')
  })
})
