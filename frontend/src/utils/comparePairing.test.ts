// Matching dates across two analyses.
//
// The properties worth pinning are all about what the comparison REFUSES to
// do. Lining two analyses up by row position is the obvious implementation and
// is wrong the moment the two were built in different orders -- which is the
// normal case, since dates deployed from a project collection are copies and
// nothing preserves their order. Silently dropping a date one run lacks is the
// second obvious shortcut, and it turns a comparison of eleven dates into a
// comparison of eight without saying so.
import { describe, expect, it } from 'vitest'

import {
  comparisonWindow,
  largestShift,
  pairRuns,
  rowMetrics,
  sampleKey,
  sortComparedRows,
  type CompareResultInput,
  type CompareRunInput,
  type ComparedRow,
} from './comparePairing'
import { SIGMA_2 } from './hpd'

/** A narrow triangular density centred on `centre`, `halfWidth` years each side. */
function peak(centre: number, halfWidth: number): Array<[number, number]> {
  const points: Array<[number, number]> = []
  for (let age = centre - halfWidth; age <= centre + halfWidth; age += 5) {
    points.push([age, halfWidth - Math.abs(age - centre) + 1])
  }
  return points
}

function date(
  name: string,
  median: number,
  halfWidth = 100,
  extra: Partial<CompareResultInput> = {}
): CompareResultInput {
  return {
    sampleId: `${name}-${median}`,
    sampleName: name,
    posteriorMedian: median,
    distribution: peak(median, halfWidth),
    ...extra,
  }
}

function run(name: string, results: CompareResultInput[]): CompareRunInput {
  return { id: name, name, analysisType: 'bayesian', results }
}

describe('sampleKey', () => {
  it('treats a name as the same date whatever its case and spacing', () => {
    expect(sampleKey('OxA-19883')).toBe(sampleKey(' oxa-19883 '))
    expect(sampleKey('SUERC  7742')).toBe(sampleKey('SUERC 7742'))
  })

  it('has no key for a date with no name', () => {
    expect(sampleKey(null)).toBeNull()
    expect(sampleKey('   ')).toBeNull()
  })
})

describe('pairRuns', () => {
  it('matches by name, not by position', () => {
    const a = run('A', [date('OxA-1', 2600), date('OxA-2', 2500), date('OxA-3', 2400)])
    const b = run('B', [date('OxA-3', 2380), date('OxA-1', 2650), date('OxA-2', 2510)])

    const { rows, unmatched } = pairRuns([a, b])

    expect(unmatched).toEqual([])
    expect(rows.map((r) => r.label)).toEqual(['OxA-1', 'OxA-2', 'OxA-3'])
    for (const row of rows) {
      expect(row.cells[0]!.sampleName).toBe(row.cells[1]!.sampleName)
    }
  })

  it('keeps the reference run’s order', () => {
    const a = run('A', [date('C', 2600), date('A', 2500), date('B', 2400)])
    const b = run('B', [date('A', 2500), date('B', 2400), date('C', 2600)])

    expect(pairRuns([a, b]).rows.map((r) => r.label)).toEqual(['C', 'A', 'B'])
  })

  it('does not drop a date one run lacks -- it reports it', () => {
    const a = run('A', [date('OxA-1', 2600), date('OxA-2', 2500)])
    const b = run('B', [date('OxA-1', 2610), date('OxA-9', 2200)])

    const { rows, unmatched } = pairRuns([a, b])

    expect(rows.map((r) => r.label)).toEqual(['OxA-1'])
    expect(unmatched).toEqual([
      { key: 'oxa-2', label: 'OxA-2', presentIn: [0], reason: 'missing' },
      { key: 'oxa-9', label: 'OxA-9', presentIn: [1], reason: 'missing' },
    ])
  })

  it('refuses a name that appears twice in one run rather than guessing', () => {
    const a = run('A', [date('OxA-1', 2600), date('OxA-1', 2300)])
    const b = run('B', [date('OxA-1', 2610)])

    const { rows, unmatched } = pairRuns([a, b])

    expect(rows).toEqual([])
    expect(unmatched).toHaveLength(1)
    expect(unmatched[0].reason).toBe('duplicate')
  })

  it('requires a date in every run of a three-way comparison', () => {
    const a = run('A', [date('OxA-1', 2600), date('OxA-2', 2500)])
    const b = run('B', [date('OxA-1', 2610), date('OxA-2', 2510)])
    const c = run('C', [date('OxA-1', 2620)])

    const { rows, unmatched } = pairRuns([a, b, c])

    expect(rows.map((r) => r.label)).toEqual(['OxA-1'])
    expect(unmatched[0]).toMatchObject({ label: 'OxA-2', presentIn: [0, 1] })
  })
})

describe('rowMetrics', () => {
  const modelled = [true, true]

  it('measures a shift against the reference, positive being older', () => {
    const { rows } = pairRuns([run('A', [date('D', 2500)]), run('B', [date('D', 2640)])])
    const metrics = rowMetrics(rows[0], modelled, SIGMA_2)

    expect(metrics.deltaMedian[0]).toBeNull()
    expect(metrics.deltaMedian[1]).toBe(140)
  })

  it('reports a tightened interval as a ratio below one', () => {
    const { rows } = pairRuns([
      run('A', [date('D', 2500, 200)]),
      run('B', [date('D', 2500, 50)]),
    ])
    const metrics = rowMetrics(rows[0], modelled, SIGMA_2)

    expect(metrics.widthRatio[1]).toBeLessThan(0.5)
    expect(metrics.width[0]!).toBeGreaterThan(metrics.width[1]!)
  })

  it('has no shift to report where a median is missing', () => {
    const { rows } = pairRuns([
      run('A', [{ sampleId: 'x', sampleName: 'D' }]),
      run('B', [date('D', 2500)]),
    ])
    const metrics = rowMetrics(rows[0], modelled, SIGMA_2)

    expect(metrics.deltaMedian[1]).toBeNull()
    expect(metrics.widthRatio[1]).toBeNull()
  })
})

describe('sortComparedRows', () => {
  const modelled = [true, true]

  function compared(pairs: Array<[string, number, number]>): ComparedRow[] {
    const a = run('A', pairs.map(([name, from]) => date(name, from)))
    const b = run('B', pairs.map(([name, , to]) => date(name, to)))
    return pairRuns([a, b]).rows.map((row) => ({
      ...row,
      metrics: rowMetrics(row, modelled, SIGMA_2),
    }))
  }

  const rows = compared([
    ['steady', 2500, 2505],
    ['mover', 2500, 2640],
    ['middling', 2500, 2560],
  ])

  it('puts the biggest mover first', () => {
    expect(sortComparedRows(rows, 'shift').map((r) => r.label)).toEqual([
      'mover',
      'middling',
      'steady',
    ])
  })

  it('leaves the reference analysis order alone when asked to', () => {
    expect(sortComparedRows(rows, 'analysis').map((r) => r.label)).toEqual([
      'steady',
      'mover',
      'middling',
    ])
  })

  it('does not mutate the rows it is given', () => {
    const before = rows.map((r) => r.label)
    sortComparedRows(rows, 'shift')
    expect(rows.map((r) => r.label)).toEqual(before)
  })

  it('measures a shift as the largest across every run', () => {
    const a = run('A', [date('D', 2500)])
    const b = run('B', [date('D', 2510)])
    const c = run('C', [date('D', 2700)])
    const { rows: three } = pairRuns([a, b, c])
    expect(largestShift(rowMetrics(three[0], [true, true, true], SIGMA_2))).toBe(200)
  })
})

describe('comparisonWindow', () => {
  it('covers every run’s interval, so one scale serves every row', () => {
    const a = run('A', [date('early', 3000, 50), date('late', 2000, 50)])
    const b = run('B', [date('early', 3040, 50), date('late', 1960, 50)])
    const rows = pairRuns([a, b]).rows.map((row) => ({
      ...row,
      metrics: rowMetrics(row, [true, true], SIGMA_2),
    }))

    const window = comparisonWindow(rows, [true, true], SIGMA_2)!

    expect(window[0]).toBeLessThan(1960)
    expect(window[1]).toBeGreaterThan(3040)
  })

  it('has no window when nothing could be measured', () => {
    const a = run('A', [{ sampleId: 'x', sampleName: 'D' }])
    const b = run('B', [{ sampleId: 'y', sampleName: 'D' }])
    const rows = pairRuns([a, b]).rows.map((row) => ({
      ...row,
      metrics: rowMetrics(row, [true, true], SIGMA_2),
    }))

    expect(comparisonWindow(rows, [true, true], SIGMA_2)).toBeNull()
  })
})
