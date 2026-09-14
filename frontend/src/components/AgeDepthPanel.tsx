// An age-depth model on the Results page: the band, the accumulation rates,
// and the rigidity the run inferred.
//
// WHY A BAND AND NOT A STACK OF POSTERIORS. The deliverable of a deposition
// model is age against depth. A column of per-date posteriors answers a
// different question -- "what age is each of these samples" -- and leaves the
// reader to do in their head the one thing the model was built to do.
//
// WHY THE RIGIDITY IS ON SCREEN. A high-k model interpolates confidently
// straight through a calibration plateau and reports a precision the dates do
// not support. The band is only readable next to the rigidity that produced
// it, and `pinned` says when the posterior ran up against the end of the
// prior's own range -- which is the case where the prior rather than the data
// is doing the work.
//
// Every figure here is read off JOINT draws (services/intervals.py): an
// accumulation rate is a function of two ages at once, and pairing their
// marginals would give a rate distribution wrong in shape rather than scale.
import {
  Box,
  Chip,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material'
import { useElementTypeColors } from '@/theme/diagramColors'
import { usePlotColors } from '@/theme/plotColors'
import {
  calendarAxisTitle,
  calendarRulerTicks,
  formatCalendarAge,
  formatCalendarRange,
  type DateFormat,
} from '@/utils/dateFormat'
import { linearTicks } from '@/utils/linearTicks'
import type { AgeDepthRecord } from '@/types'

/** What the run inferred about one sequence's rigidity, as the solver records it. */
export interface DepositionReport {
  element_id: string
  name: string
  depth_units: string
  levels: number
  variable: boolean
  k_mean?: number
  log10_k_mean?: number
  log10_k_hpd_68?: [number, number]
  log_k_min?: number
  log_k_max?: number
  pinned?: boolean | null
}

interface Props {
  record: AgeDepthRecord
  rigidity?: DepositionReport
  dateFormat: DateFormat
}

const PLOT_HEIGHT = 260
const NUMERIC_CELL_SX = { whiteSpace: 'nowrap' } as const

/**
 * The band itself: depth down the left, calendar age across, the 95.4%
 * envelope at low strength with the 68.3% nested inside it, and the median as
 * a line through them.
 *
 * Drawn as positioned boxes rather than through the plotting library, as
 * IntervalPlot is: it is a handful of marks on two linear scales, and it has
 * to sit inside a card that already carries a table.
 *
 * WHY THE BANDS ARE JOINED UP. The model's claim is about the whole column,
 * not about the levels it happened to evaluate: between two levels the age is
 * interpolated, and that is the entire point of a deposition model. Drawn as
 * a rung per level the figure reads as a set of unrelated intervals, and the
 * reader has to join them by eye -- so the envelope is closed into a shape,
 * with its edges stroked so the two confidence levels can be traced.
 *
 * A dated level is marked; an undated one is not. The difference matters --
 * an undated level's age is entirely inferred from its neighbours, and a
 * reader has to be able to see which points carry a measurement.
 */
function Band({ record, dateFormat }: { record: AgeDepthRecord; dateFormat: DateFormat }) {
  const colors = usePlotColors()
  const levels = record.levels
  if (levels.length < 2) return null

  const ages = levels.flatMap((l) => [l.band_95[0], l.band_95[1]])
  const ageMin = Math.min(...ages)
  const ageMax = Math.max(...ages)
  const ageSpan = Math.max(ageMax - ageMin, 1)
  const depths = levels.map((l) => l.depth)
  const depthMin = Math.min(...depths)
  const depthMax = Math.max(...depths)
  const depthSpan = Math.max(depthMax - depthMin, 1)

  // Older cal BP sits left, as everywhere else in the app; deeper sits lower,
  // as in the core.
  const x = (calBP: number) => ((ageMax - calBP) / ageSpan) * 100
  const y = (depth: number) => ((depth - depthMin) / depthSpan) * 100

  // Deepest first, so the polygon walks down one edge of the envelope and
  // back up the other rather than crossing itself.
  const ordered = [...levels].sort((a, b) => a.depth - b.depth)

  /** One confidence band closed into a shape: older edge down, younger edge back up. */
  const envelope = (band: (level: (typeof levels)[number]) => [number, number]) => {
    const older = ordered.map((l) => `${x(Math.max(...band(l)))},${y(l.depth)}`)
    const younger = ordered.map((l) => `${x(Math.min(...band(l)))},${y(l.depth)}`).reverse()
    return [...older, ...younger].join(' ')
  }

  /** One edge of a band on its own, so the envelope can be traced as a line. */
  const edge = (
    band: (level: (typeof levels)[number]) => [number, number],
    side: 'older' | 'younger'
  ) =>
    ordered
      .map((l) => `${x(side === 'older' ? Math.max(...band(l)) : Math.min(...band(l)))},${y(l.depth)}`)
      .join(' ')

  const band95 = (l: (typeof levels)[number]) => l.band_95
  const band68 = (l: (typeof levels)[number]) => l.band_68

  return (
    <Box
      role="img"
      aria-label={`Age against depth for ${record.name}: ${levels.length} levels from ${
        depthMin
      } to ${depthMax} ${record.depth_units}, ${formatCalendarRange(
        ageMax,
        ageMin,
        dateFormat
      )}`}
      sx={{ position: 'relative', height: PLOT_HEIGHT, width: '100%' }}
    >
      <Box
        component="svg"
        aria-hidden
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        sx={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
      >
        {/* Widest first, so the 68.3% sits inside the 95.4% rather than
            under it. Both are translucent: where they overlap the reader
            should see one band nested in another, which is what they are. */}
        <polygon points={envelope(band95)} fill={colors.hpd95} fillOpacity={0.55} stroke="none" />
        <polygon points={envelope(band68)} fill={colors.hpd68} fillOpacity={0.55} stroke="none" />
        {([
          [band95, colors.hpd95],
          [band68, colors.hpd68],
        ] as const).map(([band, stroke], i) =>
          (['older', 'younger'] as const).map((side) => (
            <polyline
              key={`${i}-${side}`}
              points={edge(band, side)}
              fill="none"
              stroke={stroke}
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          ))
        )}
        {/* Last, so the thread through the envelope is never buried by it. */}
        <polyline
          points={ordered.map((l) => `${x(l.median)},${y(l.depth)}`).join(' ')}
          fill="none"
          stroke={colors.mean}
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
        />
      </Box>

      {ordered.map((level) => (
        <Box
          component="span"
          key={level.id}
          sx={{
            position: 'absolute',
            left: `calc(${x(level.median)}% - 3px)`,
            top: `calc(${y(level.depth)}% - 3px)`,
            width: 6,
            height: 6,
            borderRadius: '50%',
            // Hollow for a level with no date behind it: its age is
            // entirely inferred from the levels either side, and a reader
            // has to be able to tell that from a measured one.
            backgroundColor: level.dated ? colors.mean : colors.surface,
            border: `1px solid ${colors.mean}`,
          }}
        />
      ))}
    </Box>
  )
}

const AXIS_LABEL_SX = {
  fontSize: 11,
  color: 'text.secondary',
  whiteSpace: 'nowrap',
} as const

/**
 * The figure with its two rulers: depth graduated down the left, calendar age
 * across the foot.
 *
 * The plot area is the ONE element both rulers are positioned against, which
 * is why the gutters are laid out around it rather than the ticks being drawn
 * inside the plot: a percentage inside the plot box and a percentage inside a
 * strip of the same width are the same position, so a graduation cannot drift
 * away from the mark it labels.
 */
function BandFigure({
  record,
  dateFormat,
}: {
  record: AgeDepthRecord
  dateFormat: DateFormat
}) {
  const levels = record.levels
  if (levels.length < 2) return null

  const units = record.depth_units || 'cm'
  const ages = levels.flatMap((l) => [l.band_95[0], l.band_95[1]])
  const ageMin = Math.min(...ages)
  const ageMax = Math.max(...ages)
  const ageSpan = Math.max(ageMax - ageMin, 1)
  const depths = levels.map((l) => l.depth)
  const depthMin = Math.min(...depths)
  const depthMax = Math.max(...depths)
  const depthSpan = Math.max(depthMax - depthMin, 1)

  // A higher target than the dashboard ruler's: niceStep rounds UP to the next
  // round step, so asking for 5 across a 2,700-year core lands on 1,000 and
  // graduates it twice. Asking for 7 lands on 500 and gives five graduations.
  const ageTicks = calendarRulerTicks(ageMin, ageMax, dateFormat, 7)
  const depthTicks = linearTicks(depthMin, depthMax, 5)

  return (
    <Box sx={{ my: 1 }}>
      <Box sx={{ display: 'flex' }}>
        {/* Rotated rather than stacked: a vertical axis title is the
            convention, and a horizontal one would need a gutter as wide as
            the words, taking it out of the plot. */}
        <Box
          sx={{
            width: 20,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          <Typography
            sx={{ ...AXIS_LABEL_SX, transform: 'rotate(180deg)', writingMode: 'vertical-rl' }}
          >
            Depth ({units}) — deeper down
          </Typography>
        </Box>

        <Box sx={{ position: 'relative', width: 44, height: PLOT_HEIGHT, flexShrink: 0 }}>
          {depthTicks.map((depth) => (
            <Box
              key={depth}
              sx={{
                position: 'absolute',
                right: 4,
                top: `${((depth - depthMin) / depthSpan) * 100}%`,
                transform: 'translateY(-50%)',
                ...AXIS_LABEL_SX,
              }}
            >
              {depth}
            </Box>
          ))}
        </Box>

        <Box sx={{ position: 'relative', flex: 1, minWidth: 0 }}>
          {/* Gridlines under the data, at the graduations either ruler
              names, so a point can be read off both scales at once. */}
          <Box
            aria-hidden
            sx={{ position: 'absolute', inset: 0, height: PLOT_HEIGHT, pointerEvents: 'none' }}
          >
            {ageTicks.map((tick) => (
              <Box
                key={`v-${tick.calBP}`}
                sx={{
                  position: 'absolute',
                  top: 0,
                  bottom: 0,
                  left: `${((ageMax - tick.calBP) / ageSpan) * 100}%`,
                  borderLeft: '1px solid',
                  borderColor: 'divider',
                }}
              />
            ))}
            {depthTicks.map((depth) => (
              <Box
                key={`h-${depth}`}
                sx={{
                  position: 'absolute',
                  left: 0,
                  right: 0,
                  top: `${((depth - depthMin) / depthSpan) * 100}%`,
                  borderTop: '1px solid',
                  borderColor: 'divider',
                }}
              />
            ))}
          </Box>
          <Band record={record} dateFormat={dateFormat} />
          <Box sx={{ position: 'relative', height: 16, mt: 0.5 }}>
            {ageTicks.map((tick) => (
              <Box
                key={tick.calBP}
                sx={{
                  position: 'absolute',
                  left: `${((ageMax - tick.calBP) / ageSpan) * 100}%`,
                  transform: 'translateX(-50%)',
                  ...AXIS_LABEL_SX,
                }}
              >
                {tick.label}
              </Box>
            ))}
          </Box>
          <Typography sx={{ ...AXIS_LABEL_SX, textAlign: 'center', mt: 0.5 }}>
            {calendarAxisTitle(dateFormat)} — older to the left
          </Typography>
        </Box>
      </Box>
    </Box>
  )
}

function rigiditySentence(rigidity: DepositionReport, units: string): string {
  if (!rigidity.variable) {
    return `Deposition rate held at k = ${rigidity.k_mean} events per ${units}, as set.`
  }
  const band = rigidity.log10_k_hpd_68
  const range = band ? `, 68.3% between ${(10 ** band[0]).toPrecision(2)} and ${(10 ** band[1]).toPrecision(2)}` : ''
  return `Deposition rate inferred from the dates: k ≈ ${
    rigidity.k_mean != null ? rigidity.k_mean.toPrecision(2) : '—'
  } events per ${units}${range}.`
}

export default function AgeDepthPanel({ record, rigidity, dateFormat }: Props) {
  const colors = useElementTypeColors()
  const units = record.depth_units || 'cm'
  const undated = record.levels.filter((l) => !l.dated)

  return (
    <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 } }}>
      <Stack spacing={1.5}>
        <Box>
          <Typography variant="h6" component="h2" sx={{ color: colors.deposition_sequence.ink }}>
            {record.name}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {record.levels.length} level{record.levels.length === 1 ? '' : 's'} between{' '}
            {Math.min(...record.levels.map((l) => l.depth))} and{' '}
            {Math.max(...record.levels.map((l) => l.depth))} {units}
            {undated.length > 0
              ? `, ${undated.length} of them undated (hollow markers below)`
              : ''}
            .
          </Typography>
        </Box>

        <BandFigure record={record} dateFormat={dateFormat} />
        <Typography variant="caption" color="text.secondary">
          The line is the median; the bands are the 68.3% and 95.4% intervals,
          drawn as envelopes because the model's claim is about the whole
          column, not only the levels it was evaluated at.
        </Typography>

        {rigidity && (
          <Box>
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
              <Typography variant="body2">{rigiditySentence(rigidity, units)}</Typography>
              {rigidity.pinned && (
                <Tooltip title="The posterior for the deposition rate ran up against the end of the range it was allowed, so that range is shaping the answer as much as the dates are. Widen it and run again.">
                  <Chip size="small" color="warning" variant="outlined" label="Rate pinned at its limit" />
                </Tooltip>
              )}
            </Stack>
            <Typography variant="caption" color="text.secondary" component="p">
              A stiff model draws a straighter line through a calibration
              plateau than the dates alone can support, so read the band next to
              the rate that produced it.
            </Typography>
          </Box>
        )}

        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Level</TableCell>
                <TableCell align="right" sx={NUMERIC_CELL_SX}>
                  Depth ({units})
                </TableCell>
                <TableCell align="right" sx={NUMERIC_CELL_SX}>
                  Median
                </TableCell>
                <TableCell align="right" sx={NUMERIC_CELL_SX}>
                  95.4%
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {record.levels.map((level) => (
                <TableRow key={level.id}>
                  <TableCell>
                    {level.name}
                    {!level.dated && (
                      <Typography component="span" variant="caption" color="text.secondary">
                        {' '}
                        · undated
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell align="right" sx={NUMERIC_CELL_SX}>
                    {level.depth}
                  </TableCell>
                  <TableCell align="right" sx={NUMERIC_CELL_SX}>
                    {formatCalendarAge(level.median, dateFormat)}
                  </TableCell>
                  <TableCell align="right" sx={NUMERIC_CELL_SX}>
                    {formatCalendarRange(level.band_95[0], level.band_95[1], dateFormat, {
                      compact: true,
                    })}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>

        {record.accumulation.length > 0 && (
          <Box>
            <Typography variant="subtitle2" component="h3">
              Accumulation
            </Typography>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Between</TableCell>
                    <TableCell align="right" sx={NUMERIC_CELL_SX}>
                      {units}
                    </TableCell>
                    <TableCell align="right" sx={NUMERIC_CELL_SX}>
                      Years
                    </TableCell>
                    <TableCell align="right" sx={NUMERIC_CELL_SX}>
                      <Tooltip title="Years per unit depth, read off the same draw of both ages. Not the two medians divided.">
                        <span>Rate (yr/{units})</span>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {record.accumulation.map((step) => (
                    <TableRow key={`${step.from_depth}-${step.to_depth}`}>
                      <TableCell>
                        {step.from_name} → {step.to_name}
                      </TableCell>
                      <TableCell align="right" sx={NUMERIC_CELL_SX}>
                        {step.depth_gap}
                      </TableCell>
                      <TableCell align="right" sx={NUMERIC_CELL_SX}>
                        {Math.round(step.years.median)}
                      </TableCell>
                      <TableCell align="right" sx={NUMERIC_CELL_SX}>
                        {step.rate_median.toFixed(1)}{' '}
                        <Typography component="span" variant="caption" color="text.secondary">
                          ({step.rate_band_68[0].toFixed(1)}–{step.rate_band_68[1].toFixed(1)})
                        </Typography>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Box>
        )}
      </Stack>
    </Paper>
  )
}
