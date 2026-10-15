// A wiggle match on the Results page: the calendar age the series resolved
// to, every member's place in it, and the agreement that says whether the
// match is real.
//
// THE ONE THING THIS PANEL EXISTS TO SAY. A wiggle match's member posteriors
// are ONE distribution translated, not n independent results. Drawn without
// that said, n identically-shaped curves read as n independent confirmations
// of the same answer -- which is the opposite of what the model claims. The
// table shows each member's own range; the manual explains the reading. A gap that was MEASURED rather
// than counted gives its member a width of its own
// (services/wiggle_match._member_density), and the same column shows that too.
//
// Every range here is re-selected from the distribution at the page's
// confidence through utils/hpd.posteriorRanges -- the one path the results
// table below also takes -- rather than read off the stored hpd_95, so the
// panel and the table cannot quote two different ranges for one parameter.
//
// The second thing: a D_Sequence asserts the ring counts. If a sample is
// misidentified by forty rings the model cannot widen to absorb it the way a
// Phase can -- it simply fits everything worse -- so the agreement index here
// is not a diagnostic beside the answer, it IS the check that the match
// happened. It is quoted against OxCal's pass mark A_n = 100/sqrt(2n), which
// falls as members are added (services/agreement.combination_threshold).
import {
  Box,
  Link,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import WarningAmberRoundedIcon from '@mui/icons-material/WarningAmberRounded'
import { Link as RouterLink } from 'react-router-dom'
import WiggleMatchFigure, { type WiggleFigureMember } from '@/components/WiggleMatchFigure'
import { useElementTypeColors } from '@/theme/diagramColors'
import { formatCalendarAge, formatCalendarRange, type DateFormat } from '@/utils/dateFormat'
import { POOR_AGREEMENT } from '@/utils/diagnostics'
import { posteriorRanges, type RangeReporting } from '@/utils/hpd'
import type { HPDRange, PosteriorSummary } from '@/types'

export interface WiggleMember {
  id: string
  name: string
  ring_offset: number
  ring_offset_error?: number | null
  agreement_index?: number | null
}

interface Props {
  /** The element's own name, so the panel says which timber it is about. */
  name: string
  /** The posterior of the age at gap zero -- the whole model's one parameter. */
  reference: PosteriorSummary
  members: WiggleMember[]
  /** What the gaps were counted in: rings, varves or plain years. */
  gapUnits: string
  seriesSpan: number
  combinationAgreement?: number | null
  /** The pass mark the combination is quoted against; absent on older runs. */
  combinationThreshold?: number | null
  /** Which member gap zero refers to; null when no sample sits exactly there. */
  referenceSampleId?: string | null
  /** Each member's laboratory measurement, by date id, for the figure. */
  measurements: Record<
    string,
    Pick<WiggleFigureMember, 'ageBp' | 'error1Sigma' | 'deltaR' | 'curveId'> | undefined
  >
  /** Each member's own modelled distribution, by date id. */
  memberDistributions: Record<string, Array<[number, number]> | undefined>
  dateFormat: DateFormat
  /** The page's selected HPD confidence, and its label. */
  confidence: number
  confidenceLabel: string
  /** The calendar step the analysis reported its ranges at; `true` for the
   *  default. See utils/hpd.reportingOf. */
  reporting?: RangeReporting
}

const NUMERIC_CELL_SX = { whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' } as const

/** The outer span of a set of ranges, as the table's range columns report it. */
function spanOf(ranges: HPDRange[]): [number, number] | null {
  if (ranges.length === 0) return null
  return [Math.min(...ranges.map((r) => r.min)), Math.max(...ranges.map((r) => r.max))]
}

function agreementText(index: number | null | undefined): string {
  return index == null ? '—' : `${index.toFixed(0)}%`
}

export default function WiggleMatchPanel({
  name,
  reference,
  members,
  gapUnits,
  seriesSpan,
  combinationAgreement,
  combinationThreshold,
  referenceSampleId,
  measurements,
  memberDistributions,
  dateFormat,
  confidence,
  confidenceLabel,
  reporting = true,
}: Props) {
  const colors = useElementTypeColors()
  const ink = colors.wiggle_sequence.ink
  // Youngest first, which is ascending offset: an offset counts years BEFORE
  // the reference, so it increases into the past.
  const ordered = [...members].sort((a, b) => a.ring_offset - b.ring_offset)
  const referenceMember = members.find((m) => m.id === referenceSampleId)
  const referenceRanges = posteriorRanges(reference.probability_distribution, confidence, reporting)
  const referenceSpan = spanOf(referenceRanges)
  const measuredGaps = members.some((m) => (m.ring_offset_error ?? 0) > 0)
  // A run from before a measured gap gave its member its own width: every member
  // exactly the reference's width despite a ± gap. Said, rather than left to
  // contradict the sentence above it; re-running brings the widths in.
  const memberWidths = members.map((m) => {
    const span = spanOf(posteriorRanges(memberDistributions[m.id], confidence, reporting))
    return span ? span[1] - span[0] : null
  })
  const predatesWidening =
    measuredGaps &&
    referenceSpan !== null &&
    memberWidths.every(
      (w) => w !== null && Math.abs(w - (referenceSpan[1] - referenceSpan[0])) < 1e-6
    )
  const figureMembers: WiggleFigureMember[] = ordered.flatMap((m) => {
    const measurement = measurements[m.id]
    return measurement
      ? [{
          id: m.id,
          name: m.name,
          ringOffset: m.ring_offset,
          ringOffsetError: m.ring_offset_error,
          agreementIndex: m.agreement_index,
          ...measurement,
        }]
      : []
  })
  const poor = ordered.filter(
    (m) => m.agreement_index != null && m.agreement_index < POOR_AGREEMENT
  )
  const combinationFails =
    combinationAgreement != null &&
    combinationThreshold != null &&
    combinationAgreement < combinationThreshold

  return (
    <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 } }}>
      <Stack spacing={2}>
        <Box>
          <Typography variant="h6" component="h2" sx={{ color: ink }}>
            {name}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {members.length} date{members.length === 1 ? '' : 's'} over{' '}
            {Math.round(seriesSpan)} {gapUnits}
          </Typography>
        </Box>

        {/* The answer, given the room it deserves: this is the whole model's
            single free parameter, and everything in the table below is it
            plus a number. */}
        <Box>
          <Typography variant="subtitle2" component="p" color="text.secondary">
            {referenceMember
              ? `Age at ${referenceMember.name} (gap 0)`
              : 'Age at the reference point (gap 0)'}
          </Typography>
          <Typography variant="h5" component="p" sx={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatCalendarAge(reference.posterior_median, dateFormat)}
          </Typography>
          {referenceSpan && (
            <Typography variant="body2" color="text.secondary" sx={{ fontVariantNumeric: 'tabular-nums' }}>
              {confidenceLabel}: {formatCalendarRange(referenceSpan[0], referenceSpan[1], dateFormat, { compact: true })}

            </Typography>
          )}
          {referenceRanges.length > 1 && (
            // A match that has not resolved. Worth saying plainly, because
            // the headline above still reads as one confident answer.
            <Typography variant="body2" color="warning.main" sx={{ mt: 0.5 }}>
              Split into {referenceRanges.length} ranges. A longer series will resolve it.
            </Typography>
          )}
        </Box>

        {figureMembers.length > 0 && (
          <WiggleMatchFigure
            members={figureMembers}
            referenceMedian={reference.posterior_median}
            referenceSpan={referenceSpan}
            confidenceLabel={confidenceLabel}
            gapUnits={gapUnits}
            dateFormat={dateFormat}
          />
        )}

        {predatesWidening && (
          <Typography variant="body2" color="warning.main" sx={{ maxWidth: '72ch' }}>
            Re-run this analysis to update the date ranges.
          </Typography>
        )}

        <TableContainer>
          <Table
            size="small"
            sx={{ '& th, & td': { px: { xs: 1, sm: 2 } } }}
          >
            <TableHead>
              <TableRow>
                <TableCell>Date</TableCell>
                <TableCell align="right">Gap ({gapUnits})</TableCell>
                <TableCell align="right">Modelled {confidenceLabel}</TableCell>
                <TableCell align="right">
                  {/* "A" on a phone, as the results table writes it: the
                      word was the widest header and pushed the column it
                      heads out of a 309px card. */}
                  <Box component="span" sx={{ display: { xs: 'none', sm: 'inline' } }}>
                    Agreement
                  </Box>
                  <Box
                    component="abbr"
                    title="Agreement"
                    sx={{ display: { sm: 'none' }, textDecoration: 'none' }}
                  >
                    A
                  </Box>
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {ordered.map((member) => {
                const span = spanOf(
                  posteriorRanges(memberDistributions[member.id], confidence, reporting)
                )
                const low = member.agreement_index != null
                  && member.agreement_index < POOR_AGREEMENT
                return (
                  <TableRow key={member.id}>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>{member.name}</TableCell>
                    <TableCell align="right" sx={NUMERIC_CELL_SX}>
                      {member.ring_offset}
                      {member.ring_offset_error
                        ? ` ± ${Math.round(member.ring_offset_error)}`
                        : ''}
                    </TableCell>
                    <TableCell align="right" sx={NUMERIC_CELL_SX}>
                      {span
                        ? formatCalendarRange(span[0], span[1], dateFormat, { compact: true })
                        : formatCalendarAge(reference.posterior_median + member.ring_offset, dateFormat)}
                    </TableCell>
                    <TableCell
                      align="right"
                      sx={{ ...NUMERIC_CELL_SX, color: low ? 'warning.main' : undefined, fontWeight: low ? 600 : undefined }}
                    >
                      {/* Poor agreement is a mark AND a colour, and says so to
                          a screen reader: amber alone is not a signal. */}
                      {low && (
                        <WarningAmberRoundedIcon
                          titleAccess="Poor agreement"
                          sx={{ fontSize: '1rem', verticalAlign: '-0.2em', mr: 0.5 }}
                        />
                      )}
                      {agreementText(member.agreement_index)}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </TableContainer>

        <Box>
          <Typography variant="body2">
            Combination agreement:{' '}
            <Box
              component="span"
              sx={{
                fontWeight: 600,
                fontVariantNumeric: 'tabular-nums',
                color: combinationFails ? 'warning.main' : undefined,
              }}
            >
              {agreementText(combinationAgreement)}
            </Box>
            {combinationThreshold != null && ` (pass mark ${combinationThreshold.toFixed(0)}%)`}
          </Typography>
          <Link component={RouterLink} to="/help#wiggle-matching" variant="body2">
            How to read a wiggle match
          </Link>
        </Box>

        {poor.length > 0 && (
          <Typography variant="body2" color="warning.main">
            Poor agreement: {poor.map((m) => m.name).join(', ')}. Check{' '}
            {poor.length === 1 ? 'its ring count' : 'their ring counts'}.
          </Typography>
        )}
      </Stack>
    </Paper>
  )
}
