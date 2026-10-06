import { describe, expect, it } from 'vitest'
import {
  pooledAge,
  pooledRanges,
  sameEventFigures,
  sameEventVerdict,
  type SameEventTest,
} from './sameEvent'

// Nenthorn, old-wood dates excluded (feature spec Q2).
const NENTHORN: SameEventTest = {
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
  ...NENTHORN,
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
  ...NENTHORN,
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
    expect(sameEventVerdict(NENTHORN)).toBe('Consistent with one event')
    expect(sameEventVerdict(LAYERS)).toBe('More scatter than one event allows')
  })

  it('gives the critical value to two decimals', () => {
    expect(sameEventFigures(NENTHORN)).toBe('T = 5.7 · 5% critical 14.07 · df 7')
  })

  it('writes the pooled age', () => {
    expect(pooledAge(NENTHORN)).toBe('656 ± 8 BP')
  })

  it('picks the calibrated ranges at the page confidence', () => {
    expect(pooledRanges(NENTHORN, 0.9544997361)[0].max).toBe(660)
    expect(pooledRanges(LAYERS, 0.9544997361)).toEqual([])
  })

  it('says why a set was not tested', () => {
    expect(sameEventVerdict(REFUSED)).toBe('Dates are on more than one curve.')
    expect(sameEventFigures(REFUSED)).toBe('')
  })
})
