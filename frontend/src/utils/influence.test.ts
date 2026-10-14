import { describe, expect, it } from 'vitest'
import { figure, probabilityAbove, quantitiesOf, rank } from './influence'
import type { InfluenceReport, InfluenceVariant, RunSummary } from './influence'

const run = (startMedian: number, width = 100, duration = 50): RunSummary => ({
  boundaries: {
    b0: {
      name: 'Start',
      median: startMedian,
      hpd_95: [startMedian - width / 2, startMedian + width / 2],
      distribution: [
        [startMedian - 50, 0],
        [startMedian, 1],
        [startMedian + 50, 0],
      ],
    },
  },
  durations: {
    'phase_duration:p1': { name: 'Upper', kind: 'phase_duration', median: duration, hpd_95: [10, 90], distribution: null },
  },
})

const variant = (id: string, summary: RunSummary | null, error: string | null = null): InfluenceVariant => ({
  id,
  kind: 'leave_out',
  date_id: id,
  date_name: id,
  label: `${id} left out`,
  summary,
  solver: summary ? 'exact' : null,
  error,
})

const report: InfluenceReport = {
  created_at: '2026-10-14T00:00:00',
  kinds: ['leave_out'],
  solver: 'exact',
  baseline: run(3300),
  variants: [
    variant('small', run(3305)),
    variant('failed', null, 'no dates left'),
    variant('large', run(3260, 160)),
  ],
}

describe('quantitiesOf', () => {
  it('lists Boundaries, then durations named by kind', () => {
    expect(quantitiesOf(report)).toEqual([
      { key: 'boundary:b0', group: 'boundary', name: 'Start' },
      { key: 'duration:phase_duration:p1', group: 'duration', name: 'Phase duration: Upper' },
    ])
  })
})

describe('rank', () => {
  it('puts the largest change first and failures last', () => {
    const rows = rank(report, 'boundary:b0', 'median', null)
    expect(rows.map((row) => row.variant.id)).toEqual(['large', 'small', 'failed'])
    expect(rows[0].change).toBe(-40)
    expect(rows[2].change).toBeNull()
  })

  it('ranks by the 95% width when asked', () => {
    const rows = rank(report, 'boundary:b0', 'width', null)
    expect(rows[0].variant.id).toBe('large')
    expect(rows[0].change).toBe(60)
  })

  it('reads a duration', () => {
    expect(figure(report.baseline, 'duration:phase_duration:p1', 'median', null)).toBe(50)
  })
})

describe('probabilityAbove', () => {
  it('cuts a triangle at its peak in half', () => {
    expect(probabilityAbove(report.baseline.boundaries.b0.distribution, 3300)).toBeCloseTo(0.5)
  })

  it('reads 1 and 0 beyond either end', () => {
    const dist = report.baseline.boundaries.b0.distribution
    expect(probabilityAbove(dist, 3000)).toBe(1)
    expect(probabilityAbove(dist, 3400)).toBe(0)
  })

  it('needs a threshold for P before', () => {
    expect(figure(report.baseline, 'boundary:b0', 'p_before', null)).toBeNull()
    expect(figure(report.baseline, 'boundary:b0', 'p_before', 3325)).toBeCloseTo(0.125)
  })
})
