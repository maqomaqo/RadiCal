// The comparison as CSV.
//
// Assembled from the same paired rows and the same metrics the figure draws,
// for the reason the single-analysis exports are assembled from one source:
// two views of one comparison must not be able to disagree. Nothing is
// recomputed here that the page has not already computed -- the HPD ranges
// come back through utils/hpd like every other surface's, at the confidence
// the reader selected.
import { formatHPDRanges, type DateFormat } from '@/utils/dateFormat'
import { posteriorRanges, type RangeReporting } from '@/utils/hpd'
import type { ComparedRow, UnmatchedRow } from '@/utils/comparePairing'
import { saveBlob } from '@/utils/saveFile'
import { SHARED_BOUNDARY_TEXT } from '@/utils/sharedBoundary'
import {
  DERIVED_KIND_LABELS,
  rowsOnAxis,
  type DerivedAxis,
  type DerivedRow,
  type DerivedValue,
} from '@/utils/compareDerived'

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
  /** Whether the run is modelled, and at what reporting step; see
   *  utils/hpd.reportingOf. */
  modelled: RangeReporting
}


/**
 * A duration's HPD as one cell: "0-125 (95.4%)", smallest first.
 *
 * The same shape and the same ordering the single-analysis CSV writes
 * (`_years_ranges` in backend/app/api/results.py), and for the same reason
 * given there: a length reads upwards, where a calendar range reads oldest
 * first. One export of a duration must not look different from another.
 */
function yearsRanges(value: DerivedValue, defaultLabel: string, signed: boolean): string {
  if (value.ranges.length === 0) return ''
  const level = value.levelNote ?? defaultLabel
  // A separation can be negative, and "-40-60" is not a range anybody can
  // read. Only that axis spells the join out; a duration keeps the compact
  // form, so its cells stay identical to the single-analysis export's.
  const join = signed ? ' to ' : '-'
  return [...value.ranges]
    .sort((a, b) => a.min - b.min)
    .map((r) => `${Math.round(r.min)}${join}${Math.round(r.max)} (${level})`)
    .join('; ')
}

interface AxisBlock {
  axis: DerivedAxis
  heading: string
  units: string
  /** What a positive shift against the reference run MEANS on this axis --
   *  older, longer, or a wider gap are three different findings. */
  deltaSense: string
}

/**
 * One block per axis, never one table for the lot.
 *
 * A boundary is a calendar age and a duration is a length of time; putting
 * them in one column would have the reporting format convert half of it to
 * BC/AD and leave the other half in years. The screen keeps them on three
 * separate rulers for the same reason, and the file has to keep the same
 * separation to stay readable back.
 */
const AXIS_BLOCKS: AxisBlock[] = [
  {
    axis: 'calendar',
    heading: 'Modelled transitions',
    units: 'Calendar ages, in the reporting format named above.',
    deltaSense: '+ older',
  },
  {
    axis: 'duration',
    heading: 'Durations',
    units: 'Lengths of time in years. Never calendar dates, and never converted to BC/AD.',
    deltaSense: '+ longer',
  },
  {
    axis: 'separation',
    heading: 'Separations',
    units: 'Signed years between two phases: positive is a gap, negative an overlap.',
    deltaSense: '+ wider gap',
  },
]

function derivedBlock(
  block: AxisBlock,
  runs: CompareCsvRun[],
  rows: DerivedRow[],
  dateFormat: DateFormat,
  confidenceLabel: string
): string[] {
  if (rows.length === 0) return []

  const lines: string[] = ['', csvRow([block.heading, block.units])]

  const header: string[] = ['Quantity', 'Kind']
  for (const run of runs) {
    header.push(`${run.name} \u2014 median`, `${run.name} \u2014 ${confidenceLabel} range`)
  }
  for (const run of runs.slice(1)) {
    header.push(`${run.name} \u2014 \u0394 median (yr, ${block.deltaSense})`)
  }
  header.push('Present in')
  lines.push(csvRow(header))

  for (const row of rows) {
    const cells: Array<string | number | null> = [row.label, DERIVED_KIND_LABELS[row.kind]]
    for (const cell of row.cells) {
      // Two phases sharing one Boundary have a zero separation because the
      // model says so, not because the dates do. The text goes in place of
      // both figures here exactly as it does on screen and in the
      // single-analysis exports -- a bare zero would read as a measurement.
      if (cell?.sharedBoundary) {
        cells.push(null, SHARED_BOUNDARY_TEXT)
        continue
      }
      cells.push(
        cell?.point == null ? null : Math.round(cell.point),
        cell == null
          ? ''
          : block.axis === 'calendar'
            ? formatHPDRanges(cell.ranges, dateFormat)
            : yearsRanges(cell, confidenceLabel, block.axis === 'separation')
      )
    }
    const reference = row.cells[0]?.point ?? null
    for (let i = 1; i < runs.length; i++) {
      const point = row.cells[i]?.point ?? null
      cells.push(point == null || reference == null ? null : Math.round(point - reference))
    }
    // Filled only where it says something: a quantity every run has needs no
    // column of repeated names, and one that is missing from a run must not
    // be read as a blank measurement.
    cells.push(
      row.presentIn.length === runs.length
        ? ''
        : row.presentIn.map((i) => runs[i]?.name ?? `run ${i + 1}`).join('; ')
    )
    lines.push(csvRow(cells))
  }

  return lines
}

/**
 * The dates first, then one block per kind of derived quantity, then the dates
 * that could not be paired.
 *
 * `unmatched` is written into the file rather than left to the screen: a
 * spreadsheet of eleven dates from a comparison of fourteen has to say so
 * itself, since the tray explaining the other three does not travel with it.
 * The derived blocks carry the same information in their `Present in` column,
 * for the same reason -- a boundary that exists in only one of the compared
 * models is a finding, and a blank cell is not one.
 */
export function compareCsv(
  runs: CompareCsvRun[],
  rows: ComparedRow[],
  unmatched: UnmatchedRow[],
  derived: DerivedRow[],
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
      'Every delta and ratio below is a difference between summaries from separate runs. ' +
        'They are not joint statistics: no probability that a date or a boundary moved one way ' +
        'or the other is available from two runs that were not sampled together.',
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

  for (const block of AXIS_BLOCKS) {
    lines.push(...derivedBlock(block, runs, rowsOnAxis(derived, block.axis), dateFormat, confidenceLabel))
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

/** Offer a built CSV as a download. */
export function downloadCsv(filename: string, contents: string): void {
  void saveBlob(new Blob([contents], { type: 'text/csv;charset=utf-8;' }), filename)
}
