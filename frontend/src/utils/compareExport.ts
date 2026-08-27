// The comparison as CSV.
//
// Assembled from the same paired rows and the same metrics the figure draws,
// for the reason the single-analysis exports are assembled from one source:
// two views of one comparison must not be able to disagree. Nothing is
// recomputed here that the page has not already computed -- the HPD ranges
// come back through utils/hpd like every other surface's, at the confidence
// the reader selected.
import { formatHPDRanges, type DateFormat } from '@/utils/dateFormat'
import { posteriorRanges } from '@/utils/hpd'
import type { ComparedRow, UnmatchedRow } from '@/utils/comparePairing'

function csvCell(value: string | number | null | undefined): string {
  if (value == null) return ''
  const text = String(value)
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

function csvRow(cells: Array<string | number | null | undefined>): string {
  return cells.map(csvCell).join(',')
}

export interface CompareCsvRun {
  name: string
  analysisType: string
  modelled: boolean
}

/**
 * `unmatched` is written into the file rather than left to the screen: a
 * spreadsheet of eleven dates from a comparison of fourteen has to say so
 * itself, since the tray explaining the other three does not travel with it.
 */
export function compareCsv(
  runs: CompareCsvRun[],
  rows: ComparedRow[],
  unmatched: UnmatchedRow[],
  dateFormat: DateFormat,
  confidence: number,
  confidenceLabel: string
): string {
  const lines: string[] = []

  lines.push(csvRow(['Reference run', runs[0]?.name ?? '']))
  lines.push(csvRow(['Confidence', confidenceLabel]))
  lines.push(csvRow(['Date format', dateFormat]))
  lines.push(
    csvRow([
      'Note',
      'Delta median and width ratio are differences of summaries from separate runs. ' +
        'They are not joint statistics: no probability that a date moved one way or the other ' +
        'is available from two runs that were not sampled together.',
    ])
  )
  lines.push('')

  const header: string[] = ['Date']
  for (const run of runs) {
    header.push(`${run.name} — median`, `${run.name} — ${confidenceLabel} range`, `${run.name} — agreement`)
  }
  for (const run of runs.slice(1)) {
    header.push(`${run.name} — Δ median (yr, + older)`, `${run.name} — width ÷ reference`)
  }
  lines.push(csvRow(header))

  for (const row of rows) {
    const cells: Array<string | number | null> = [row.label]
    row.cells.forEach((cell, i) => {
      const ranges = cell?.distribution
        ? posteriorRanges(cell.distribution, confidence, runs[i].modelled)
        : []
      cells.push(
        cell?.posteriorMedian == null ? null : Math.round(cell.posteriorMedian),
        ranges.length > 0 ? formatHPDRanges(ranges, dateFormat) : '',
        cell?.agreementIndex == null ? null : Math.round(cell.agreementIndex)
      )
    })
    for (let i = 1; i < runs.length; i++) {
      const delta = row.metrics.deltaMedian[i]
      const ratio = row.metrics.widthRatio[i]
      cells.push(delta == null ? null : Math.round(delta), ratio == null ? null : ratio.toFixed(3))
    }
    lines.push(csvRow(cells))
  }

  if (unmatched.length > 0) {
    lines.push('')
    lines.push(csvRow(['Not compared', 'Reason', 'Present in']))
    for (const entry of unmatched) {
      lines.push(
        csvRow([
          entry.label,
          entry.reason === 'duplicate'
            ? 'the name appears more than once in one run'
            : 'not present in every run',
          entry.presentIn.map((i) => runs[i]?.name ?? `run ${i + 1}`).join('; '),
        ])
      )
    }
  }

  return lines.join('\n')
}

/** Offer a built CSV to the browser as a download. */
export function downloadCsv(filename: string, contents: string): void {
  const blob = new Blob([contents], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}
