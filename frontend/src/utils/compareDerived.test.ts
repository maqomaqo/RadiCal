// Matching the quantities a model derives.
//
// These are harder to match than dates and easier to get confidently wrong. A
// date exists whether or not anyone models it; a boundary is a parameter of one
// particular tree, so a run that splits a phase produces boundaries and
// durations that have no counterpart anywhere. Pairing those by position -- the
// only alternative to matching on name -- would compare the end of one phase
// with the end of a different one and report the difference as a finding.
import { describe, expect, it } from 'vitest'

import {
  AXIS_FOR_KIND,
  axisWindow,
  pairDerived,
  rowsOnAxis,
  yearAxisTicks,
  type DerivedRunInput,
} from './compareDerived'
import { SIGMA_2 } from './hpd'
import type { DurationSummary, IntervalsBlock, ModelElement } from '@/types'

function spread(centre: number, halfWidth: number): Array<[number, number]> {
  const points: Array<[number, number]> = []
  for (let value = centre - halfWidth; value <= centre + halfWidth; value += 5) {
    points.push([value, halfWidth - Math.abs(value - centre) + 1])
  }
  return points
}

function boundary(name: string, median: number): ModelElement {
  return {
    id: `b-${name}`,
    element_type: 'boundary',
    name,
    posterior: {
      posterior_mean: median,
      posterior_median: median,
      posterior_std: 20,
      hpd_68: [median - 20, median + 20],
      hpd_95: [median - 40, median + 40],
      probability_distribution: spread(median, 60),
    },
  }
}

function phase(name: string): ModelElement {
  return { id: `p-${name}`, element_type: 'phase', name }
}

function tree(children: ModelElement[]): ModelElement {
  return { id: 'root', element_type: 'sequence', name: 'Site sequence', children }
}

function duration(median: number): DurationSummary {
  return {
    mean: median,
    median,
    std: 30,
    hpd_68: [median - 30, median + 30],
    hpd_95: [median - 60, median + 60],
    step: 5,
    distribution: spread(median, 80),
  }
}

function intervals(
  durations: Array<[string, string, number]>,
  differences: Array<[string, string, number]> = []
): IntervalsBlock {
  return {
    draws: 10000,
    independent: false,
    durations: durations.map(([kind, name, median]) => ({
      kind: kind as 'phase_duration',
      id: `${kind}-${name}`,
      name,
      years: duration(median),
    })),
    differences: differences.map(([from, to, median]) => ({
      kind: 'phase_difference' as const,
      id: `${from}-${to}`,
      from_id: from,
      from_name: from,
      to_id: to,
      to_name: to,
      years: duration(median),
      overlap: 0.1,
      severity: 'slight' as const,
    })),
  }
}

const oneSplitInTwo: DerivedRunInput[] = [
  {
    modelTree: tree([boundary('Start of sequence', 800), phase('J'), boundary('End of sequence', 400)]),
    intervals: intervals([
      ['sequence_duration', 'Site sequence', 400],
      ['phase_duration', 'J', 380],
    ]),
  },
  {
    modelTree: tree([
      boundary('Start of sequence', 820),
      phase('J'),
      boundary('End of J', 600),
      phase('K'),
      boundary('End of sequence', 410),
    ]),
    intervals: intervals(
      [
        ['sequence_duration', 'Site sequence', 410],
        ['phase_duration', 'J', 200],
        ['phase_duration', 'K', 190],
      ],
      [['J', 'K', 12]]
    ),
  },
]

describe('pairDerived', () => {
  const rows = pairDerived(oneSplitInTwo, SIGMA_2)
  const byKey = (key: string) => rows.find((row) => row.key === key)!

  it('matches boundaries by name, across trees of different shapes', () => {
    expect(byKey('boundary::start of sequence').presentIn).toEqual([0, 1])
    expect(byKey('boundary::end of sequence').presentIn).toEqual([0, 1])
  })

  it('gives a boundary with no counterpart a row that says so', () => {
    const inner = byKey('boundary::end of j')
    expect(inner.presentIn).toEqual([1])
    expect(inner.cells[0]).toBeNull()
    expect(inner.cells[1]).not.toBeNull()
  })

  it('never pairs the second boundary of one tree with the second of another', () => {
    // Positional matching would put "End of sequence" (run A's second) against
    // "End of J" (run B's second) -- two different parameters, ~200 years
    // apart, reported as a shift.
    expect(byKey('boundary::end of sequence').cells[1]!.point).toBe(410)
    expect(byKey('boundary::end of j').cells[0]).toBeNull()
  })

  it('keeps a calendar age and a length of time on different axes', () => {
    expect(AXIS_FOR_KIND.boundary).toBe('calendar')
    expect(AXIS_FOR_KIND.phase_duration).toBe('duration')
    expect(AXIS_FOR_KIND.phase_difference).toBe('separation')
    expect(rowsOnAxis(rows, 'calendar').every((row) => row.kind === 'boundary')).toBe(true)
    expect(rowsOnAxis(rows, 'duration').some((row) => row.kind === 'boundary')).toBe(false)
  })

  it('matches durations by kind as well as name', () => {
    // A phase and its dated events share a name and are different quantities.
    const mixed: DerivedRunInput[] = [
      { intervals: intervals([['phase_duration', 'J', 300], ['sample_duration', 'J', 120]]) },
      { intervals: intervals([['phase_duration', 'J', 280], ['sample_duration', 'J', 110]]) },
    ]
    const paired = pairDerived(mixed, SIGMA_2)
    expect(paired).toHaveLength(2)
    expect(paired.map((row) => row.kind).sort()).toEqual(['phase_duration', 'sample_duration'])
  })

  it('leaves a separation unmatched when the runs list the pair the other way round', () => {
    const flipped: DerivedRunInput[] = [
      { intervals: intervals([], [['J', 'K', 12]]) },
      { intervals: intervals([], [['K', 'J', -12]]) },
    ]
    const paired = pairDerived(flipped, SIGMA_2)
    expect(paired).toHaveLength(2)
    expect(paired.every((row) => row.presentIn.length === 1)).toBe(true)
  })

  it('reports a shared boundary as such, never as a separation of zero', () => {
    const shared: DerivedRunInput[] = [
      {
        intervals: {
          ...intervals([]),
          differences: [
            {
              kind: 'phase_difference',
              id: 'jk',
              from_id: 'J',
              from_name: 'J',
              to_id: 'K',
              to_name: 'K',
              years: duration(0),
              overlap: 0,
              severity: 'none',
              shared_boundary: true,
            },
          ],
        },
      },
      { intervals: intervals([], [['J', 'K', 12]]) },
    ]
    const [row] = pairDerived(shared, SIGMA_2)
    expect(row.cells[0]!.sharedBoundary).toBe(true)
    expect(row.cells[0]!.ranges).toEqual([])
    expect(row.cells[0]!.point).toBeNull()
    // The run that does measure one is unaffected.
    expect(row.cells[1]!.sharedBoundary).toBeUndefined()
    expect(row.cells[1]!.point).toBe(12)
  })

  it('skips date-to-date differences, which are about the dates not the model', () => {
    const withDates: DerivedRunInput[] = [
      {
        intervals: {
          ...intervals([]),
          differences: [
            {
              kind: 'date_difference',
              id: 'd',
              from_id: 'a',
              from_name: 'OxA-1',
              to_id: 'b',
              to_name: 'OxA-2',
              years: duration(50),
              overlap: 0.2,
              severity: 'slight',
            },
          ],
        },
      },
      { intervals: intervals([]) },
    ]
    expect(pairDerived(withDates, SIGMA_2)).toHaveLength(0)
  })
})

describe('axisWindow', () => {
  const rows = pairDerived(oneSplitInTwo, SIGMA_2)

  it('anchors a duration axis at zero, because a length of time has one', () => {
    const window = axisWindow(rowsOnAxis(rows, 'duration'), 'duration')!
    expect(window[0]).toBeLessThanOrEqual(0)
  })

  it('keeps zero visible on a separation axis, where a gap becomes an overlap', () => {
    const window = axisWindow(rowsOnAxis(rows, 'separation'), 'separation')!
    expect(window[0]).toBeLessThanOrEqual(0)
    expect(window[1]).toBeGreaterThanOrEqual(0)
  })

  it('does not anchor a calendar axis at zero, which would be AD 1950', () => {
    const window = axisWindow(rowsOnAxis(rows, 'calendar'), 'calendar')!
    expect(window[0]).toBeGreaterThan(300)
  })
})

describe('yearAxisTicks', () => {
  it('lands on round numbers of years', () => {
    expect(yearAxisTicks(0, 500, 5)).toEqual([0, 100, 200, 300, 400, 500])
  })

  it('covers a range that straddles zero', () => {
    const ticks = yearAxisTicks(-120, 240, 5)
    expect(ticks).toContain(0)
    expect(Math.min(...ticks)).toBeGreaterThanOrEqual(-120)
  })

  it('has nothing to draw on an empty range', () => {
    expect(yearAxisTicks(100, 100)).toEqual([])
  })
})
