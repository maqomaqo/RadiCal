// The comparison's CSV.
//
// What matters here is that the file says the same thing the screen does, and
// that it carries the two facts a spreadsheet cannot infer on its own: which
// run everything was measured against, and which dates were left out.
import { describe, expect, it } from 'vitest'

import { compareCsv } from './compareExport'
import { pairRuns, rowMetrics, type CompareRunInput } from './comparePairing'
import { SIGMA_2 } from './hpd'

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
      'calBP',
      SIGMA_2,
      '95.4%'
    )
    expect(commaNamed).toContain('Reference run,"Phase J, then K"')
  })
})
