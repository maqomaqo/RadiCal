import { Fragment, useMemo } from 'react'
import {
  Box,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material'
import {
  DateFormat,
  calendarAxisTitle,
  calendarRulerTicks,
  formatCalendarAge,
  formatCalendarRange,
} from '@/utils/dateFormat'
import {
  axisWindow,
  DERIVED_KIND_LABELS,
  rowsOnAxis,
  yearAxisTicks,
  type DerivedAxis,
  type DerivedRow,
} from '@/utils/compareDerived'
import { useCompareMarks, type RunStyle } from '@/theme/compareColors'
import { SHARED_BOUNDARY_TEXT } from '@/components/DurationsPanel'

/**
 * What each model DERIVED, run against run: boundary ages, durations, and the
 * separation between phases.
 *
 */

const TRACK_HEIGHT = 9
const TRACK_GAP = 3
const ROW_PADDING = 6

const AXIS_TITLES: Record<DerivedAxis, string> = {
  calendar: 'Calendar age',
  duration: 'Length of time · years',
  separation: 'Separation · years, positive is a gap',
}

const AXIS_BLURB: Record<DerivedAxis, string> = {
  calendar: 'Where the model puts each modelled transition.',
  duration: 'How long each sequence, phase, and phase’s set of dated events lasted.',
  separation:
    'How far apart two phases sit — a gap where they are separate, an overlap where they are not.',
}

interface DerivedQuantitiesPanelProps {
  runs: Array<{ id: string; name: string }>
  runStyles: RunStyle[]
  rows: DerivedRow[]
  dateFormat: DateFormat
  confidenceLabel: string
}

export default function DerivedQuantitiesPanel({
  runs,
  runStyles,
  rows,
  dateFormat,
  confidenceLabel,
}: DerivedQuantitiesPanelProps) {
  const axes: DerivedAxis[] = ['calendar', 'duration', 'separation']
  const present = axes.filter((axis) => rowsOnAxis(rows, axis).length > 0)

  if (present.length === 0) {
    return (
      <Typography variant="body2" color="textSecondary">
        None of these analyses reports a boundary, a duration or a phase separation. Boundaries come
        from a model structure; durations and separations are computed only when selected in analysis settings.
      </Typography>
    )
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {present.map((axis) => (
        <AxisBlock
          key={axis}
          axis={axis}
          runs={runs}
          runStyles={runStyles}
          rows={rowsOnAxis(rows, axis)}
          dateFormat={dateFormat}
          confidenceLabel={confidenceLabel}
        />
      ))}
    </Box>
  )
}

function AxisBlock({
  axis,
  runs,
  runStyles,
  rows,
  dateFormat,
  confidenceLabel,
}: {
  axis: DerivedAxis
  runs: Array<{ id: string; name: string }>
  runStyles: RunStyle[]
  rows: DerivedRow[]
  dateFormat: DateFormat
  confidenceLabel: string
}) {
  const marks = useCompareMarks()
  const window = useMemo(() => axisWindow(rows, axis), [rows, axis])

  const ticks = useMemo(() => {
    if (!window) return []
    if (axis === 'calendar') {
      return calendarRulerTicks(window[0], window[1], dateFormat, 6).map((tick) => ({
        value: tick.calBP,
        label: tick.label,
      }))
    }
    return yearAxisTicks(window[0], window[1], 5).map((value) => ({
      value,
      label: `${value.toLocaleString()}`,
    }))
  }, [window, axis, dateFormat])

  if (!window) return null
  const [min, max] = window
  const span = max - min

  /** Percent from the left. A calendar axis runs oldest-left, as every other
   *  figure in the app does; a years axis runs smallest-left, as a ruler does. */
  const position = (value: number) =>
    axis === 'calendar' ? ((max - value) / span) * 100 : ((value - min) / span) * 100

  /** A range's left edge and width, whichever way the axis runs. */
  const extent = (a: number, b: number) => {
    const left = Math.min(position(a), position(b))
    return { left, width: Math.abs(position(a) - position(b)) }
  }

  const format = (value: number) =>
    axis === 'calendar'
      ? formatCalendarAge(value, dateFormat)
      : `${Math.round(value).toLocaleString()} yr`

  const rangeText = (a: number, b: number) =>
    axis === 'calendar'
      ? formatCalendarRange(a, b, dateFormat)
      : `${Math.round(Math.min(a, b)).toLocaleString()} to ${Math.round(Math.max(a, b)).toLocaleString()} yr`

  const rowHeight = ROW_PADDING * 2 + runs.length * (TRACK_HEIGHT + TRACK_GAP) - TRACK_GAP

  return (
    <Box>
      <Typography variant="subtitle2">
        {axis === 'calendar' ? calendarAxisTitle(dateFormat) : AXIS_TITLES[axis]}
      </Typography>
      <Typography variant="caption" color="textSecondary" component="div" sx={{ mb: 0.5 }}>
        {AXIS_BLURB[axis]} Regions are the {confidenceLabel} HPD.
      </Typography>
      <TableContainer>
        <Table size="small" sx={{ tableLayout: 'fixed' }}>
          <TableHead>
            <TableRow sx={{ backgroundColor: 'background.muted' }}>
              <TableCell sx={{ width: 230 }}>Quantity</TableCell>
              <TableCell>
                <Box sx={{ position: 'relative', height: 18 }}>
                  {ticks.map((tick) => (
                    <Box
                      key={tick.value}
                      sx={{
                        position: 'absolute',
                        left: `${position(tick.value)}%`,
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
              <TableCell align="right" sx={{ width: 110 }}>
                <Tooltip title="This run's median minus the reference run's, in years.">
                  <span>Δ median</span>
                </Tooltip>
              </TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((row) => {
              const referencePoint = row.cells[0]?.point ?? null
              return (
                <TableRow key={row.key} hover>
                  <TableCell>
                    <Typography variant="body2">{row.label}</Typography>
                    <Typography variant="caption" color="textSecondary">
                      {DERIVED_KIND_LABELS[row.kind]}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Box sx={{ position: 'relative', height: rowHeight }}>
                      {ticks.map((tick) => (
                        <Box
                          key={tick.value}
                          sx={{
                            position: 'absolute',
                            left: `${position(tick.value)}%`,
                            top: 0,
                            bottom: 0,
                            width: '1px',
                            backgroundColor: marks.grid,
                          }}
                        />
                      ))}
                      {axis === 'separation' && (
                        // Where a gap becomes an overlap. Stronger than the
                        // grid because crossing it changes what the number
                        // means, not just its size.
                        <Tooltip title="Zero: the two phases meet">
                          <Box
                            sx={{
                              position: 'absolute',
                              left: `${position(0)}%`,
                              top: 0,
                              bottom: 0,
                              width: '1px',
                              backgroundColor: marks.referenceRule,
                            }}
                          />
                        </Tooltip>
                      )}
                      {referencePoint != null && axis !== 'separation' && (
                        <Box
                          sx={{
                            position: 'absolute',
                            left: `${position(referencePoint)}%`,
                            top: 0,
                            bottom: 0,
                            width: '1px',
                            backgroundColor: marks.referenceRule,
                          }}
                        />
                      )}
                      {row.cells.map((cell, runIndex) => {
                        const top = ROW_PADDING + runIndex * (TRACK_HEIGHT + TRACK_GAP)
                        const style = runStyles[runIndex]
                        if (!cell) {
                          return (
                            <Box
                              key={runs[runIndex].id}
                              sx={{
                                position: 'absolute',
                                left: 0,
                                right: 0,
                                top: top + TRACK_HEIGHT / 2,
                                height: '1px',
                                borderTop: `1px dashed ${marks.absent}`,
                              }}
                            />
                          )
                        }
                        if (cell.sharedBoundary) {
                          // Zero years by construction, so no bar: these two
                          // phases meet at one Boundary, and drawing a
                          // zero-width mark at the zero rule would read as a
                          // measured separation of nothing.
                          return (
                            <Typography
                              key={runs[runIndex].id}
                              variant="caption"
                              sx={{
                                position: 'absolute',
                                left: 0,
                                top: top - 3,
                                color: runStyles[runIndex].line,
                                lineHeight: 1.2,
                              }}
                            >
                              {SHARED_BOUNDARY_TEXT}
                            </Typography>
                          )
                        }
                        return (
                          <Fragment key={runs[runIndex].id}>
                            {cell.ranges.map((range) => {
                              const { left, width } = extent(range.min, range.max)
                              return (
                                <Tooltip
                                  key={`${range.min}-${range.max}`}
                                  title={`${runs[runIndex].name} · ${
                                    cell.levelNote ?? confidenceLabel
                                  }: ${rangeText(range.min, range.max)}`}
                                >
                                  <Box
                                    sx={{
                                      position: 'absolute',
                                      top,
                                      height: TRACK_HEIGHT,
                                      left: `${left}%`,
                                      width: `${width}%`,
                                      minWidth: '3px',
                                      borderRadius: '2px',
                                      backgroundColor: style.fillOuter,
                                      border: `1px solid ${style.line}`,
                                      boxSizing: 'border-box',
                                    }}
                                  />
                                </Tooltip>
                              )
                            })}
                            {cell.point != null && (
                              <Box
                                sx={{
                                  position: 'absolute',
                                  top,
                                  height: TRACK_HEIGHT,
                                  left: `${position(cell.point)}%`,
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
                    {row.presentIn.length < runs.length ? (
                      <Tooltip
                        title={`Only ${row.presentIn
                          .map((i) => runs[i].name)
                          .join(', ')} has this. There is no counterpart in the others to measure it against.`}
                      >
                        <Typography variant="caption" color="textSecondary">
                          no counterpart
                        </Typography>
                      </Tooltip>
                    ) : (
                      <Box
                        sx={{
                          display: 'flex',
                          flexDirection: 'column',
                          alignItems: 'flex-end',
                          gap: 0.25,
                        }}
                      >
                        {row.cells.slice(1).map((cell, index) => {
                          const runIndex = index + 1
                          const delta =
                            cell?.point != null && referencePoint != null
                              ? cell.point - referencePoint
                              : null
                          return (
                            <Tooltip
                              key={runs[runIndex].id}
                              title={`${runs[runIndex].name}: ${
                                cell?.point != null ? format(cell.point) : 'not reported'
                              }`}
                            >
                              <Typography
                                variant="body2"
                                sx={{
                                  fontVariantNumeric: 'tabular-nums',
                                  color: runStyles[runIndex].line,
                                  lineHeight: 1.4,
                                }}
                              >
                                {delta == null
                                  ? '—'
                                  : `${delta > 0 ? '+' : ''}${Math.round(delta)} yr`}
                              </Typography>
                            </Tooltip>
                          )
                        })}
                      </Box>
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  )
}

