import { describe, expect, it } from 'vitest'
import {
  pooledAge,
  pooledMeasurement,
  pooledRanges,
  sameEventFigures,
  sameEventVerdict,
  type SameEventTest,
} from './sameEvent'
import type { DraftMeasurement } from '@/types'

// Hillside, old-wood dates excluded (feature spec Q2).
const HILLSIDE: SameEventTest = {
  element_id: null,
  name: null,
  n: 8,
  df: 7,
  level: 0.05,
  mean_bp: 656.1,
  error_bp: 7.8,
  t: 5.73,
  critical: 14.067,
  consistent: true,
  pooled_hpd_ranges: [
    { confidence: 0.6826894921, ranges: [{ min: 600, max: 640, probability_percent: 68.3 }] },
    { confidence: 0.9544997361, ranges: [{ min: 560, max: 660, probability_percent: 95.4 }] },
  ],
  refused: null,
}

// New dates, Layers 11-7.
const LAYERS: SameEventTest = {
  ...HILLSIDE,
  n: 6,
  df: 5,
  mean_bp: 656.3,
  error_bp: 7.0,
  t: 27.85,
  critical: 11.07,
  consistent: false,
  pooled_hpd_ranges: null,
}

const REFUSED: SameEventTest = {
  ...HILLSIDE,
  mean_bp: null,
  error_bp: null,
  t: null,
  critical: null,
  consistent: null,
  pooled_hpd_ranges: null,
  refused: 'Dates are on more than one curve.',
}

describe('same-event wording', () => {
  it('states the verdict either way', () => {
    expect(sameEventVerdict(HILLSIDE)).toBe('Consistent with one event')
    expect(sameEventVerdict(LAYERS)).toBe('More scatter than one event allows')
  })

  it('gives the critical value to two decimals', () => {
    expect(sameEventFigures(HILLSIDE)).toBe('T = 5.7 · 5% critical 14.07 · df 7')
  })

  it('writes the pooled age', () => {
    expect(pooledAge(HILLSIDE)).toBe('656 ± 8 BP')
  })

  it('picks the calibrated ranges at the page confidence', () => {
    expect(pooledRanges(HILLSIDE, 0.9544997361)[0].max).toBe(660)
    expect(pooledRanges(LAYERS, 0.9544997361)).toEqual([])
  })

  it('says why a set was not tested', () => {
    expect(sameEventVerdict(REFUSED)).toBe('Dates are on more than one curve.')
    expect(sameEventFigures(REFUSED)).toBe('')
  })
})

describe('pooledMeasurement', () => {
  const draft = (id: string, age: number, error: number, extra: Partial<DraftMeasurement> = {}) =>
    ({
      id,
      sample_name: id,
      age_bp: age,
      error_1sigma: error,
      material_type: 'charcoal',
      sample_type: 'terrestrial',
      curve_id: 'shcal20',
      element_id: 'p1',
      latitude: -45.4,
      longitude: 170.6,
      outlier_prior: 0.05,
      ...extra,
    }) as DraftMeasurement
  const dates = [draft('A', 650, 20), draft('B', 662, 16, { material_type: 'eggshell' })]

  it('takes the pooled age and keeps what the dates share', () => {
    const pooled = pooledMeasurement(dates, HILLSIDE, 'pooled_1')
    expect(pooled).toMatchObject({
      id: 'pooled_1',
      sample_name: 'Pooled: A + B',
      age_bp: 656.1,
      error_1sigma: 7.8,
      curve_id: 'shcal20',
      element_id: 'p1',
      latitude: -45.4,
      material_type: 'mixed',
      outlier_prior: 0,
    })
  })

  it('records what was pooled', () => {
    const { notes } = pooledMeasurement(dates, HILLSIDE, 'x')
    expect(notes).toContain('A 650 ± 20 BP; B 662 ± 16 BP')
    expect(notes).toContain('T = 5.7')
  })

  it('leaves blank what the dates disagree on', () => {
    const apart = [draft('A', 650, 20), draft('B', 662, 16, { latitude: -46 })]
    expect(pooledMeasurement(apart, HILLSIDE, 'x').latitude).toBeNull()
  })

  it('keeps a long name inside the field limit', () => {
    const many = Array.from({ length: 30 }, (_, i) => draft(`Le-10001${i}`, 650, 20))
    const { sample_name } = pooledMeasurement(many, HILLSIDE, 'x')
    expect(sample_name.length).toBeLessThanOrEqual(200)
    expect(sample_name).toMatch(/\(30 dates\)$/)
  })

  it('refuses dates that did not pass', () => {
    expect(() => pooledMeasurement(dates, LAYERS, 'x')).toThrow()
  })
})
