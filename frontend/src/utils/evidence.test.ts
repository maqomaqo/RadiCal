import { describe, expect, it } from 'vitest'
import { compareEvidence, formatLog10, oddsOf, strengthOf } from './evidence'
import type { EvidenceRun } from './evidence'

const run = (name: string, log10: number, overrides: Partial<EvidenceRun> = {}): EvidenceRun => ({
  name,
  isBayesian: true,
  method: 'exact',
  evidence: { available: true, log10, log: log10 * Math.LN10, readings: 'abc' },
  ...overrides,
})

describe('compareEvidence', () => {
  it('sets each run against the first', () => {
    const result = compareEvidence([run('Long', -10), run('Short', -16.5), run('Late', -9.5)])
    expect(result.kind).toBe('ok')
    if (result.kind !== 'ok') return
    expect(result.rows[0]).toMatchObject({ name: 'Long', log10Factor: null, strength: null })
    expect(result.rows[1].log10Factor).toBeCloseTo(-6.5)
    expect(result.rows[1].strength).toBe('decisive')
    expect(result.rows[2].log10Factor).toBeCloseTo(0.5)
    expect(result.rows[2].strength).toBe('substantial')
  })

  it('withholds when the runs are fitted to different readings', () => {
    const other = run('Fewer dates', -9, {
      evidence: { available: true, log10: -9, readings: 'def' },
    })
    const result = compareEvidence([run('All dates', -10), other])
    expect(result).toMatchObject({ kind: 'withheld' })
    expect((result as { reason: string }).reason).toMatch(/same radiocarbon readings/)
  })

  it('withholds, and names the run, when one was sampled', () => {
    const result = compareEvidence([run('Exact', -10), run('Sampled', -9, { method: 'gibbs' })])
    expect((result as { reason: string }).reason).toMatch(/Sampled was sampled/)
  })

  it('withholds with the backend reason when a run has none', () => {
    const noBounds = run('Open', 0, {
      evidence: { available: false, reason: "the oldest Boundary, 'Start', has no TPQ" },
    })
    const result = compareEvidence([run('Bounded', -10), noBounds])
    expect((result as { reason: string }).reason).toBe(
      "Open: the oldest Boundary, 'Start', has no TPQ."
    )
  })

  it('withholds for a run from before the evidence was recorded', () => {
    const old = run('Old', 0, { evidence: null })
    expect((compareEvidence([run('New', -10), old]) as { reason: string }).reason).toMatch(
      /Run it again/
    )
  })

  it('refuses a calibration-only run', () => {
    const result = compareEvidence([run('A', -10), run('Cal', 0, { isBayesian: false })])
    expect(result.kind).toBe('withheld')
  })

  it('carries the approximate flag', () => {
    const rough = run('Rough', -9, {
      evidence: { available: true, log10: -9, readings: 'abc', approximate: true },
    })
    const result = compareEvidence([run('Fine', -10), rough])
    expect(result).toMatchObject({ kind: 'ok', approximate: true })
  })
})

describe('strengthOf', () => {
  it('uses the sign-free size, on the Jeffreys scale', () => {
    expect(strengthOf(0.2)).toBe('barely worth mentioning')
    expect(strengthOf(-0.7)).toBe('substantial')
    expect(strengthOf(1.2)).toBe('strong')
    expect(strengthOf(-1.7)).toBe('very strong')
    expect(strengthOf(2.3)).toBe('decisive')
  })
})

describe('oddsOf', () => {
  it('reads as plain odds up to a thousand, then as a power of ten', () => {
    expect(oddsOf(0.5)).toBe('3.2 : 1')
    expect(oddsOf(-1.3)).toBe('20 : 1')
    expect(oddsOf(6.5)).toBe('10^6.5 : 1')
  })
})

describe('formatLog10', () => {
  it('prints two decimals with a true minus, and never a negative zero', () => {
    expect(formatLog10(-6.2)).toBe('−6.20')
    expect(formatLog10(0.004)).toBe('0.00')
    expect(formatLog10(-0.001)).toBe('0.00')
  })
})
