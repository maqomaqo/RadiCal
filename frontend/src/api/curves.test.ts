// The pure helpers behind the curve pickers. What they are guarding is the
// behaviour of a <Select> whose value is not among its options: MUI silently
// renders it blank, so a slow or failed /api/curves request could present a
// date as having no curve, and the next save would write whatever the blank
// resolved to. That is the failure these functions exist to prevent, and it
// is invisible in a screenshot -- hence tests.
import { describe, expect, it } from 'vitest'

import { curveLabel, curveOptions, curvesForSampleType, type CurveSpec } from './curves'

function curve(id: string, name: string, sample_type: 'terrestrial' | 'marine'): CurveSpec {
  return {
    id,
    name,
    type: sample_type,
    sample_type,
    description: '',
    reference: '',
    year: 2020,
    doi: null,
    kind: 'builtin',
    aliases: [],
    data_points: 9501,
    cal_min: 0,
    cal_max: 55000,
    c14_min: 0,
    c14_max: 50000,
    resolution: '1 yr (0-5,000)',
    content_sha256: 'abc',
    archived: false,
    source_filename: null,
  }
}

const CURVES = [
  curve('intcal20', 'IntCal20', 'terrestrial'),
  curve('shcal20', 'SHCal20', 'terrestrial'),
  curve('marine20', 'Marine20', 'marine'),
]

describe('curvesForSampleType', () => {
  it('keeps the backend ordering, so the first is the default for that type', () => {
    expect(curvesForSampleType(CURVES, 'terrestrial').map((c) => c.id)).toEqual([
      'intcal20',
      'shcal20',
    ])
  })

  it('offers only marine curves for marine carbon', () => {
    expect(curvesForSampleType(CURVES, 'marine').map((c) => c.id)).toEqual(['marine20'])
  })

  it('is empty for a sample type no curve serves', () => {
    expect(curvesForSampleType(CURVES, 'lacustrine')).toEqual([])
  })
})

describe('curveOptions', () => {
  it('lists the curves by name', () => {
    expect(curveOptions(CURVES, 'intcal20')).toEqual([
      { id: 'intcal20', label: 'IntCal20' },
      { id: 'shcal20', label: 'SHCal20' },
      { id: 'marine20', label: 'Marine20' },
    ])
  })

  it('keeps a curve the date already has but the list does not', () => {
    // The list failed to load, or the date is on a curve that has since been
    // archived. Either way the picker must not silently blank it.
    const options = curveOptions([], 'intcal20')
    expect(options).toEqual([{ id: 'intcal20', label: 'intcal20' }])
  })

  it('does not duplicate the current curve when it is in the list', () => {
    expect(curveOptions(CURVES, 'marine20').filter((o) => o.id === 'marine20')).toHaveLength(1)
  })

  it('is just the list when nothing is selected yet', () => {
    expect(curveOptions(CURVES, undefined).map((o) => o.id)).toEqual([
      'intcal20',
      'shcal20',
      'marine20',
    ])
  })
})

describe('curveLabel', () => {
  it('names a known curve', () => {
    expect(curveLabel(CURVES, 'shcal20')).toBe('SHCal20')
  })

  it('falls back to the id rather than showing nothing', () => {
    expect(curveLabel([], 'shcal20')).toBe('shcal20')
    expect(curveLabel(CURVES, undefined)).toBe('')
  })
})
