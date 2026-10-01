import { Fragment, ReactNode, useMemo } from 'react'
import {
  Box,
  Collapse,
  IconButton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material'
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown'
import KeyboardArrowRightIcon from '@mui/icons-material/KeyboardArrowRight'
import {
  DateFormat,
  calendarAxisTitle,
  calendarRulerTicks,
  formatCalendarAge,
} from '@/utils/dateFormat'
import { hpdBands, type HPDBand, type RangeReporting } from '@/utils/hpd'
import type { ComparedRow } from '@/utils/comparePairing'
import { useCompareMarks, type RunStyle } from '@/theme/compareColors'
import { POOR_AGREEMENT } from '@/utils/diagnostics'

/**
 * The paired-interval figure at the centre of a comparison: one row per date,
 * one HPD bar per analysis in that row, all on one calendar axis.
 *
 * Drawn as a table of positioned bars rather than as a Plotly figure, and the
 * reason is the gutter. What makes this a comparison rather than an overlay is
 * the numbers beside each row -- how far the date moved, whether its interval
 * tightened, what the model paid in agreement -- and those have to stay locked
 * to their bar as the table sorts and rows expand. A chart with the numbers
 * beside it in a separate element has two orderings to keep in step; a table
 * has one.
 *
 * The bars are absolutely positioned against ONE calendar window shared by
 * every row (see comparisonWindow), because the whole claim of the figure is
 * that a bar's position means something. Per-row scaling would make every date
 * look equally well placed.
 */

/** Bar height and the gap between two runs' bars inside one row, in pixels. */
const TRACK_HEIGHT = 9
const TRACK_GAP = 3
const ROW_PADDING = 6

function trackTop(index: number): number {
  return ROW_PADDING + index * (TRACK_HEIGHT + TRACK_GAP)
}

function rowHeight(runCount: number): number {
  return ROW_PADDING * 2 + runCount * (TRACK_HEIGHT + TRACK_GAP) - TRACK_GAP
}

export interface ShiftPlotRun {
  id: string
  name: string
  analysisType: string
}

interface ShiftPlotProps {
  runs: ShiftPlotRun[]
  runStyles: RunStyle[]
  rows: ComparedRow[]
  /** Per run: whether its distributions came out of a solver, and at what
   *  calendar step, which decides the resolution its HPD ranges are reported
   *  at. See utils/hpd.reportingOf. */
  modelled: RangeReporting[]
  /** [younger, older] in cal BP -- the window every row is drawn in. */
  window: [number, number]
  confidence: number
  confidenceLabel: string
  dateFormat: DateFormat
  expandedKey: string | null
  onToggleRow: (key: string) => void
  /** What to draw under an expanded row. The bars above summarise; this is
   *  where the distributions themselves go. */
  renderExpanded: (row: ComparedRow) => ReactNode
}

export default function ShiftPlot({
  runs,
  runStyles,
  rows,
  modelled,
  window: calendarWindow,
  confidence,
  confidenceLabel,
  dateFormat,
  expandedKey,
  onToggleRow,
  renderExpanded,
}: ShiftPlotProps) {
  const marks = useCompareMarks()
  const [windowMin, windowMax] = calendarWindow
  const span = windowMax - windowMin

  /** Percent from the left edge for a calendar age. Oldest at the left, as a
   *  chronology reads and as every other figure in this app draws it. */
  const position = (calBP: number) => ((windowMax - calBP) / span) * 100

  const ticks = useMemo(
    () => calendarRulerTicks(windowMin, windowMax, dateFormat, 6),
    [windowMin, windowMax, dateFormat]
  )

  // Bands are two HPD selections per bar, so this is the expensive part of a
  // redraw. It depends on the distributions and the selected confidence alone,
  // so it survives a re-sort or a row expanding.
  const bandsByKey = useMemo(() => {
    const map = new Map<string, Array<HPDBand[]>>()
    for (const row of rows) {
      map.set(
        row.key,
        row.cells.map((cell, i) =>
          cell?.distribution
            ? hpdBands(cell.distribution, confidence, confidenceLabel, modelled[i])
            : []
        )
      )
    }
    return map
    // `modelled` is rebuilt per render by the page; its contents are what
    // matter, and they change only when the compared runs do.
  }, [rows, confidence, confidenceLabel, modelled.join(',')])

  /**
   * Which runs report an agreement index at all. A group calibration computes
   * none -- there is no model for a date to agree or disagree with -- so
   * without this the column is a full height of em-dashes for that run, and
   * for a comparison of two calibrations it is a column of nothing.
   */
  const agreementRuns = useMemo(
    () => runs.map((_, i) => rows.some((row) => row.cells[i]?.agreementIndex != null)),
    [runs, rows]
  )
  const showAgreement = agreementRuns.some(Boolean)

  const totalColumns = showAgreement ? 5 : 4

  return (
    <TableContainer>
      <Table size="small" sx={{ tableLayout: 'fixed' }}>
        <TableHead>
          <TableRow sx={{ backgroundColor: 'background.muted' }}>
            <TableCell sx={{ width: 190 }}>Date</TableCell>
            <TableCell>
              <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1 }}>
                <span>{calendarAxisTitle(dateFormat)}</span>
                <Typography variant="caption" color="textSecondary">
                  ← older
                </Typography>
              </Box>
              <Box sx={{ position: 'relative', height: 18, mt: 0.5 }}>
                {ticks.map((tick) => (
                  <Box
                    key={tick.calBP}
                    sx={{
                      position: 'absolute',
                      left: `${position(tick.calBP)}%`,
                      transform: 'translateX(-50%)',
                      fontSize: 11,
                      color: 'text.secondary',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {tick.label}
                  </Box>
                ))}
              </Box>
            </TableCell>
            <TableCell align="right" sx={{ width: 96 }}>
              <Tooltip title="This run's median minus the reference run's. Positive is older.">
                <span>Δ median</span>
              </Tooltip>
            </TableCell>
            <TableCell align="right" sx={{ width: 96 }}>
              <Tooltip
                title={`Width of the ${confidenceLabel} region, divided by the reference run's. Below 1 is tighter.`}
              >
                <span>Width ratio</span>
              </Tooltip>
            </TableCell>
            {showAgreement && (
              <TableCell align="right" sx={{ width: 104 }}>
                <Tooltip title="Agreement index: how well each run's modelled posterior still fits the date's own calibration. Only runs that compute one are listed.">
                  <span>Agreement</span>
                </Tooltip>
              </TableCell>
            )}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row) => {
            const bands = bandsByKey.get(row.key) ?? []
            const referenceMedian = row.cells[0]?.posteriorMedian
            const expanded = expandedKey === row.key
            return (
              <Fragment key={row.key}>
                <TableRow
                  hover
                  onClick={() => onToggleRow(row.key)}
                  sx={{
                    cursor: 'pointer',
                    ...(expanded ? { backgroundColor: 'background.muted' } : {}),
                  }}
                >
                  <TableCell sx={{ verticalAlign: 'middle' }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                      <IconButton
                        size="small"
                        aria-label={`${expanded ? 'Hide' : 'Show'} the distributions for ${row.label}`}
                        aria-expanded={expanded}
                        onClick={(e) => {
                          e.stopPropagation()
                          onToggleRow(row.key)
                        }}
                      >
                        {expanded ? (
                          <KeyboardArrowDownIcon fontSize="small" />
                        ) : (
                          <KeyboardArrowRightIcon fontSize="small" />
                        )}
                      </IconButton>
                      <Typography variant="body2" sx={{ wordBreak: 'break-word' }}>
                        {row.label}
                      </Typography>
                    </Box>
                  </TableCell>

                  <TableCell>
                    <Box sx={{ position: 'relative', height: rowHeight(runs.length) }}>
                      {ticks.map((tick) => (
                        <Box
                          key={tick.calBP}
                          sx={{
                            position: 'absolute',
                            left: `${position(tick.calBP)}%`,
                            top: 0,
                            bottom: 0,
                            width: '1px',
                            backgroundColor: marks.grid,
                          }}
                        />
                      ))}
                      {referenceMedian != null && (
                        <Tooltip
                          title={`Reference median: ${formatCalendarAge(referenceMedian, dateFormat)}`}
                        >
                          <Box
                            sx={{
                              position: 'absolute',
                              left: `${position(referenceMedian)}%`,
                              top: 0,
                              bottom: 0,
                              width: '1px',
                              backgroundColor: marks.referenceRule,
                            }}
                          />
                        </Tooltip>
                      )}
                      {row.cells.map((cell, runIndex) => {
                        const style = runStyles[runIndex]
                        const top = trackTop(runIndex)
                        return (
                          <Fragment key={runs[runIndex].id}>
                            {(bands[runIndex] ?? []).map((band) =>
                              band.ranges.map((range) => (
                                <Tooltip
                                  key={`${band.label}-${range.min}-${range.max}`}
                                  title={`${runs[runIndex].name} · ${band.label}: ${formatCalendarAge(
                                    range.max,
                                    dateFormat
                                  )} – ${formatCalendarAge(range.min, dateFormat)}`}
                                >
                                  <Box
                                    sx={{
                                      position: 'absolute',
                                      top,
                                      height: TRACK_HEIGHT,
                                      left: `${position(range.max)}%`,
                                      width: `${((range.max - range.min) / span) * 100}%`,
                                      minWidth: '3px',
                                      borderRadius: '2px',
                                      backgroundColor:
                                        band.tone === 'outer' ? style.fillOuter : style.fillInner,
                                      ...(band.tone === 'outer'
                                        ? { border: `1px solid ${style.line}` }
                                        : {}),
                                      boxSizing: 'border-box',
                                    }}
                                  />
                                </Tooltip>
                              ))
                            )}
                            {cell?.posteriorMedian != null && (
                              <Box
                                sx={{
                                  position: 'absolute',
                                  top,
                                  height: TRACK_HEIGHT,
                                  left: `${position(cell.posteriorMedian)}%`,
                                  width: '1.5px',
                                  marginLeft: '-0.75px',
                                  backgroundColor: marks.medianTick,
                                }}
                              />
                            )}
                          </Fragment>
                        )
                      })}
                    </Box>
                  </TableCell>

                  <TableCell align="right">
                    <PerRun
                      runs={runs}
                      runStyles={runStyles}
                      values={row.metrics.deltaMedian.map((delta) =>
                        delta == null ? null : `${delta > 0 ? '+' : ''}${Math.round(delta)}`
                      )}
                    />
                  </TableCell>

                  <TableCell align="right">
                    <PerRun
                      runs={runs}
                      runStyles={runStyles}
                      values={row.metrics.widthRatio.map((ratio) =>
                        ratio == null ? null : `${ratio.toFixed(2)}×`
                      )}
                    />
                  </TableCell>

                  {showAgreement && (
                    <TableCell align="right">
                      <PerRun
                        runs={runs}
                        runStyles={runStyles}
                        includeReference
                        omit={agreementRuns.map((reports) => !reports)}
                        values={row.cells.map((cell) =>
                          cell?.agreementIndex == null
                            ? null
                            : Math.round(cell.agreementIndex).toString()
                        )}
                        emphasise={row.cells.map(
                          (cell) => cell?.agreementIndex != null && cell.agreementIndex < POOR_AGREEMENT
                        )}
                      />
                    </TableCell>
                  )}
                </TableRow>

                <TableRow>
                  <TableCell
                    colSpan={totalColumns}
                    sx={{ py: 0, borderBottom: expanded ? undefined : 'none' }}
                  >
                    <Collapse in={expanded} timeout="auto" unmountOnExit>
                      <Box sx={{ py: 2 }}>{renderExpanded(row)}</Box>
                    </Collapse>
                  </TableCell>
                </TableRow>
              </Fragment>
            )
          })}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

/**
 * One numeric cell carrying a value per run, each in that run's own colour so
 * a column with three runs in it is still readable at a glance.
 *
 * The reference run is omitted by default, because Δ and width ratio are
 * measured AGAINST it and so are undefined for it -- a blank line would just
 * be noise. Agreement is a property of each run on its own, so that column
 * shows every run.
 */
function PerRun({
  runs,
  runStyles,
  values,
  includeReference = false,
  emphasise,
  omit,
}: {
  runs: ShiftPlotRun[]
  runStyles: RunStyle[]
  values: Array<string | null>
  includeReference?: boolean
  emphasise?: boolean[]
  /** Runs to leave out of this column entirely, because the quantity does not
   *  exist for them -- distinct from a run that has one and did not report it,
   *  which still gets a line and an em-dash. */
  omit?: boolean[]
}) {
  const shown = values
    .map((value, i) => ({ value, i }))
    .filter(({ i }) => (includeReference || i > 0) && !omit?.[i])

  if (shown.every(({ value }) => value == null)) {
    return (
      <Typography variant="body2" color="textSecondary">
        —
      </Typography>
    )
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 0.25 }}>
      {shown.map(({ value, i }) => (
        <Tooltip key={runs[i].id} title={runs[i].name}>
          <Typography
            variant="body2"
            sx={{
              fontVariantNumeric: 'tabular-nums',
              color: runStyles[i].line,
              fontWeight: emphasise?.[i] ? 600 : 400,
              lineHeight: 1.4,
            }}
          >
            {value ?? '—'}
          </Typography>
        </Tooltip>
      ))}
    </Box>
  )
}
