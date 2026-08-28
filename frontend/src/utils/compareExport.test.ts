// The comparison's CSV.
//
// What matters here is that the file says the same thing the screen does, and
// that it carries the two facts a spreadsheet cannot infer on its own: which
// run everything was measured against, and which dates were left out.
import { describe, expect, it } from 'vitest'

import { compareCsv } from './compareExport'
import { pairRuns, rowMetrics, type CompareRunInput } from './comparePairing'
import { pairDerived, type DerivedRunInput } from './compareDerived'
import { SIGMA_2 } from './hpd'
import type { DurationSummary, ModelElement } from '@/types'

function peak(centre: number, halfWidth: number): Array<[number, number]> {
  const points: Array<[number, number]> = []
  for (let age = centre - halfWidth; age <= centre + halfWidth; age += 5) {
    points.push([age, halfWidth - Math.abs(age - centre) + 1])
  }
  return points
}

function run(name: string, dates: Array<[string, number, number]>): CompareRunInput {
  return {
    id: name,
    name,
    analysisType: 'bayesian',
    results: dates.map(([sampleName, median, halfWidth]) => ({
      sampleId: `${name}-${sampleName}`,
      sampleName,
      posteriorMedian: median,
      distribution: peak(median, halfWidth),
      agreementIndex: 98,
    })),
  }
}

const runs = [
  run('One phase', [
    ['OxA-1', 2500, 200],
    ['OxA-2', 2400, 200],
  ]),
  run('Two phases', [
    ['OxA-1', 2640, 60],
    ['OxA-9', 2100, 60],
  ]),
]

const paired = pairRuns(runs)
const rows = paired.rows.map((row) => ({
  ...row,
  metrics: rowMetrics(row, [true, true], SIGMA_2),
}))

const csv = compareCsv(
  runs.map((r) => ({ name: r.name, analysisType: r.analysisType, modelled: true })),
  rows,
  paired.unmatched,
  [],
  'calBP',
  SIGMA_2,
  '95.4%'
)

describe('compareCsv', () => {
  it('names the reference run and the level everything was measured at', () => {
    expect(csv).toContain('Reference run,One phase')
    expect(csv).toContain('Confidence,95.4%')
  })

  it('carries the shift and the width ratio for every run but the reference', () => {
    expect(csv).toContain('Two phases — Δ median (yr, + older)')
    expect(csv).not.toContain('One phase — Δ median')
    expect(csv.split('\n').find((line) => line.startsWith('OxA-1'))).toContain('140')
  })

  it('says which dates it could not compare, so the file is not silently short', () => {
    expect(csv).toContain('Not compared')
    expect(csv).toContain('OxA-2')
    expect(csv).toContain('OxA-9')
  })

  it('says in the file that the figures are not joint statistics', () => {
    expect(csv).toContain('not joint statistics')
  })

  it('quotes a cell holding a comma rather than letting it split the row', () => {
    // A disjoint HPD region is written "3360-3300 cal BP (13.5%), 3270-3100
    // cal BP (81.9%)" -- one cell with a comma in it. So is any analysis whose
    // name has one.
    const commaNamed = compareCsv(
      [
        { name: 'Phase J, then K', analysisType: 'bayesian', modelled: true },
        { name: 'Two phases', analysisType: 'bayesian', modelled: true },
      ],
      rows,
      [],
      [],
      'calBP',
      SIGMA_2,
      '95.4%'
    )
    expect(commaNamed).toContain('Reference run,"Phase J, then K"')
  })
})

// --- the derived-quantity blocks ------------------------------------------

function summary(median: number): DurationSummary {
  const distribution: Array<[number, number]> = []
  for (let value = median - 60; value <= median + 60; value += 5) {
    distribution.push([value, 60 - Math.abs(value - median) + 1])
  }
  return {
    mean: median,
    median,
    std: 20,
    hpd_68: [median - 20, median + 20],
    hpd_95: [median - 40, median + 40],
    step: 5,
    distribution,
  }
}

function boundary(name: string, median: number): ModelElement {
  const distribution: Array<[number, number]> = []
  for (let age = median - 60; age <= median + 60; age += 5) {
    distribution.push([age, 60 - Math.abs(age - median) + 1])
  }
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
      probability_distribution: distribution,
    },
  }
}

const derivedRuns: DerivedRunInput[] = [
  {
    modelTree: {
      id: 'r',
      element_type: 'sequence',
      name: 'Site',
      children: [boundary('Start of sequence', 800), boundary('End of sequence', 400)],
    },
    intervals: {
      draws: 20000,
      independent: true,
      durations: [
        { kind: 'phase_duration', id: 'd1', name: 'J', years: summary(380) },
      ],
      differences: [],
    },
  },
  {
    modelTree: {
      id: 'r',
      element_type: 'sequence',
      name: 'Site',
      children: [
        boundary('Start of sequence', 820),
        boundary('End of J', 600),
        boundary('End of sequence', 410),
      ],
    },
    intervals: {
      draws: 20000,
      independent: true,
      durations: [{ kind: 'phase_duration', id: 'd1', name: 'J', years: summary(200) }],
      differences: [
        {
          kind: 'phase_difference',
          id: 'x',
          from_id: 'J',
          from_name: 'J',
          to_id: 'K',
          to_name: 'K',
          years: summary(12),
          overlap: 0.1,
          severity: 'slight',
        },
      ],
    },
  },
]

const withDerived = compareCsv(
  [
    { name: 'One phase', analysisType: 'bayesian', modelled: true },
    { name: 'Two phases', analysisType: 'bayesian', modelled: true },
  ],
  rows,
  [],
  pairDerived(derivedRuns, SIGMA_2),
  'calBP',
  SIGMA_2,
  '95.4%'
)

const blockOf = (heading: string) =>
  withDerived
    .split('\n')
    .slice(withDerived.split('\n').findIndex((line) => line.startsWith(heading)))
    .slice(0, 6)

describe('compareCsv derived quantities', () => {
  it('writes a separate block per kind of quantity, not one table for the lot', () => {
    expect(withDerived).toContain('Modelled transitions')
    expect(withDerived).toContain('Durations')
    expect(withDerived).toContain('Separations')
  })

  it('says in the file that a duration is a length, not a date', () => {
    expect(withDerived).toContain('never converted to BC/AD')
  })

  it('writes a duration range smallest-first, as the single-analysis CSV does', () => {
    const durations = blockOf('Durations').join('\n')
    // 380 +/- its 95.4% envelope, low end first.
    expect(durations).toMatch(/\b3\d\d-4\d\d \(95\.4%\)/)
  })

  it('names the runs a quantity is present in, when it is not in all of them', () => {
    const transitions = blockOf('Modelled transitions').join('\n')
    const endOfJ = transitions.split('\n').find((line) => line.startsWith('End of J'))!
    expect(endOfJ).toContain('Two phases')
  })

  it('leaves the present-in column empty for a quantity every run has', () => {
    const transitions = blockOf('Modelled transitions').join('\n')
    const shared = transitions.split('\n').find((line) => line.startsWith('Start of sequence'))!
    expect(shared.endsWith(',')).toBe(true)
  })

  it('says what a positive shift means on each axis, since they are three findings', () => {
    expect(withDerived).toContain('\u0394 median (yr, + older)')
    expect(withDerived).toContain('\u0394 median (yr, + longer)')
    expect(withDerived).toContain('\u0394 median (yr, + wider gap)')
  })

  it('spells out a separation range, where a hyphen would collide with a minus sign', () => {
    const separations = blockOf('Separations').join('\n')
    expect(separations).toContain(' to ')
    expect(separations).not.toMatch(/,-\d+-\d+ \(/)
  })

  it('reports the shift between the runs for a quantity both have', () => {
    const transitions = blockOf('Modelled transitions').join('\n')
    const shared = transitions.split('\n').find((line) => line.startsWith('Start of sequence'))!
    expect(shared).toContain('20')
  })

  it('omits a block no run produced', () => {
    const datesOnly = compareCsv(
      [
        { name: 'A', analysisType: 'group_calibration', modelled: false },
        { name: 'B', analysisType: 'group_calibration', modelled: false },
      ],
      rows,
      [],
      [],
      'calBP',
      SIGMA_2,
      '95.4%'
    )
    expect(datesOnly).not.toContain('Durations')
    expect(datesOnly).not.toContain('Modelled transitions')
  })
})

// eslint-disable-next-line
it('dump', () => { console.log('\n=== CSV ===\n' + withDerived + '\n=== END ===') })
