// Durations and differences on the Results page: how long each sequence,
// phase and set of dated events lasted, and how far apart the phases sit.
//
// Everything here is a LENGTH OF TIME in years, never a calendar age, so
// nothing on this panel goes through formatCalendarAge or the BC/AD
// conversion the rest of the page applies -- see DurationSummary in
// types/index.ts for why the backend gives these their own field names.
//
// Two tables rather than one: a duration has a name and a length, a
// difference has two names and a sign, and forcing them through shared
// columns would leave half of every row blank.
import {
  Box,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import { hpdEnvelopeFromHistogram } from '@/utils/hpd'
import type { DifferenceRecord, DurationRecord, DurationSummary, IntervalsBlock } from '@/types'

const NUMERIC_CELL_SX = { whiteSpace: 'nowrap' } as const

// What each duration is of, in the same words the model tree uses for the
// elements themselves.
const DURATION_LABELS: Record<DurationRecord['kind'], string> = {
  sequence_duration: 'Sequence',
  phase_duration: 'Phase',
  sample_duration: 'Dated events',
}

const DURATION_HELP: Record<DurationRecord['kind'], string> = {
  sequence_duration:
    'From the first Boundary of this sequence to its last — how long the whole of it was in use.',
  phase_duration:
    'The interval this phase’s two Boundaries enclose. Longer than the span of its dated events, because those events are a sample of the phase rather than the whole of it.',
  sample_duration:
    'From the oldest to the youngest dated event in this phase — what the dates themselves cover, with nothing inferred beyond them.',
}

/** A length of time. Rounded to the year: the reported step is coarser than
 *  that anyway, and a decimal would claim precision the draws do not have. */
function years(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return 'N/A'
  return `${Math.round(value).toLocaleString()} yr`
}

/** An envelope as "0 to 540 yr", recomputed at whatever confidence the page
 *  is showing — the same client-side re-selection the results table uses, so
 *  moving the selector moves every figure on the page together. */
function envelope(summary: DurationSummary, confidence: number): string {
  const found = hpdEnvelopeFromHistogram(summary.distribution, confidence)
  if (!found) {
    const stored = confidence <= 0.7 ? summary.hpd_68 : summary.hpd_95
    if (!stored) return 'N/A'
    return `${Math.round(stored[0]).toLocaleString()} to ${years(stored[1])}`
  }
  return `${Math.round(found[0]).toLocaleString()} to ${years(found[1])}`
}

/**
 * How far two things are from being separable in time, as a word and a bar.
 *
 * Deliberately not a success/warning/error chip. Those are reserved for
 * genuine state — a solver that ran, a run that did not converge — and an
 * overlap is a measurement, not a status; colouring it like one would make a
 * perfectly sound model look as though something had gone wrong with it. One
 * hue at a strength that tracks the number is the same treatment the HPD
 * bands get, for the same reason: this is one quantity at five strengths, not
 * five categories.
 */
function OverlapMeter({ record }: { record: DifferenceRecord }) {
  const theme = useTheme()
  const share = Math.max(0, Math.min(1, record.overlap))
  const help =
    record.kind === 'phase_difference'
      ? `${(share * 100).toFixed(0)}% probability that the two modelled intervals intersect.`
      : `${(share * 100).toFixed(0)}% — how far the model is from being able to say which of the two came first. 100% is a coin flip.`
  return (
    <Tooltip title={help}>
      <Box sx={{ width: 104 }}>
        <Typography variant="body2" sx={{ textTransform: 'capitalize' }}>
          {record.severity}{' '}
          <Typography component="span" variant="body2" color="textSecondary">
            ({(share * 100).toFixed(0)}%)
          </Typography>
        </Typography>
        <Box
          aria-hidden
          sx={{
            mt: 0.5,
            height: 4,
            borderRadius: 2,
            backgroundColor: alpha(theme.palette.primary.main, 0.12),
          }}
        >
          <Box
            sx={{
              height: '100%',
              width: `${share * 100}%`,
              borderRadius: 2,
              backgroundColor: alpha(theme.palette.primary.main, 0.35 + 0.55 * share),
            }}
          />
        </Box>
      </Box>
    </Tooltip>
  )
}

interface Props {
  intervals: IntervalsBlock
  confidence: number
  confidenceLabel: string
  showMean: boolean
  showMedian: boolean
}

export default function DurationsPanel({
  intervals,
  confidence,
  confidenceLabel,
  showMean,
  showMedian,
}: Props) {
  const { durations, differences } = intervals
  if (durations.length === 0 && differences.length === 0) return null

  // The two kinds never appear together: phases come from a model, dates from
  // a group calibration, and an analysis is one or the other.
  const comparingDates = differences[0]?.kind === 'date_difference'
  const differenceHeading = comparingDates ? 'Difference' : 'Gap or overlap'

  return (
    <Paper sx={{ p: 3, mb: 3 }}>
      <Typography variant="h5" gutterBottom>
        Durations and Differences
      </Typography>
      <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
        Lengths of time in years, not calendar dates — so these are not converted to the
        reporting era. Estimated from {intervals.draws.toLocaleString()}{' '}
        {intervals.independent ? 'independent' : 'sampled'} joint draws, which is why they
        are the only figures on this page carrying a Monte Carlo error.
      </Typography>

      {durations.length > 0 && (
        <TableContainer sx={{ mb: differences.length > 0 ? 3 : 0 }}>
          <Table size="small" sx={{ fontVariantNumeric: 'tabular-nums' }}>
            <TableHead>
              <TableRow sx={{ backgroundColor: 'grey.100' }}>
                <TableCell>What</TableCell>
                <TableCell>Name</TableCell>
                {showMean && <TableCell align="right">Mean</TableCell>}
                {showMedian && <TableCell align="right">Median</TableCell>}
                <TableCell align="right">Duration ({confidenceLabel})</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {durations.map((record) => (
                <TableRow key={record.id}>
                  <TableCell>
                    <Tooltip title={DURATION_HELP[record.kind]}>
                      <span>{DURATION_LABELS[record.kind] ?? record.kind}</span>
                    </Tooltip>
                  </TableCell>
                  <TableCell>
                    {record.name}
                    {record.kind === 'sample_duration' && record.dates ? (
                      <Typography component="span" variant="body2" color="textSecondary">
                        {' '}
                        ({record.dates} dates)
                      </Typography>
                    ) : null}
                  </TableCell>
                  {showMean && (
                    <TableCell align="right" sx={NUMERIC_CELL_SX}>
                      {years(record.years.mean)}
                    </TableCell>
                  )}
                  {showMedian && (
                    <TableCell align="right" sx={NUMERIC_CELL_SX}>
                      {years(record.years.median)}
                    </TableCell>
                  )}
                  <TableCell align="right" sx={NUMERIC_CELL_SX}>
                    {envelope(record.years, confidence)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {differences.length > 0 && (
        <>
          <Typography variant="body2" color="textSecondary" sx={{ mb: 1 }}>
            {comparingDates
              ? 'A positive difference means the first date is the older of the two. “Overlap” is how far the model is from being able to put them in order at all.'
              : 'Positive is a gap between the two phases, negative an overlap. “Overlap” is the probability that their modelled intervals intersect.'}
          </Typography>
          <TableContainer>
            <Table size="small" sx={{ fontVariantNumeric: 'tabular-nums' }}>
              <TableHead>
                <TableRow sx={{ backgroundColor: 'grey.100' }}>
                  <TableCell>{comparingDates ? 'Date' : 'Phase'}</TableCell>
                  <TableCell>Compared with</TableCell>
                  {showMean && <TableCell align="right">Mean</TableCell>}
                  <TableCell align="right">
                    {differenceHeading} ({confidenceLabel})
                  </TableCell>
                  <TableCell>Overlap</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {differences.map((record) => (
                  <TableRow key={record.id}>
                    <TableCell>{record.from_name}</TableCell>
                    <TableCell>{record.to_name}</TableCell>
                    {showMean && (
                      <TableCell align="right" sx={NUMERIC_CELL_SX}>
                        {years(record.years.mean)}
                      </TableCell>
                    )}
                    <TableCell align="right" sx={NUMERIC_CELL_SX}>
                      {record.shared_boundary ? (
                        <Tooltip title="These two phases share a Boundary, so the model gives them no gap — one phase's end is the other's start, by construction rather than by inference.">
                          <Typography variant="body2" color="textSecondary">
                            shares a transition
                          </Typography>
                        </Tooltip>
                      ) : (
                        envelope(record.years, confidence)
                      )}
                    </TableCell>
                    <TableCell>
                      <OverlapMeter record={record} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </>
      )}

      {intervals.note && (
        <Typography variant="body2" color="textSecondary" sx={{ mt: 2 }}>
          {intervals.note}
        </Typography>
      )}
    </Paper>
  )
}
