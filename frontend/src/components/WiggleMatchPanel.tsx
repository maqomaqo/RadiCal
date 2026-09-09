// A wiggle match on the Results page: the calendar age the series resolved
// to, every member's place in it, and the agreement that says whether the
// match is real.
//
// THE ONE THING THIS PANEL EXISTS TO SAY. A wiggle match's member posteriors
// are ONE distribution translated, not n independent results. Drawn without
// that said, n identically-shaped curves read as n independent confirmations
// of the same answer -- which is the opposite of what the model claims. The
// heading says it, the table repeats the shared width in every row, and the
// member ages are written as the reference plus a gap rather than as
// free-standing dates.
//
// The second thing: a D_Sequence asserts the ring counts. If a sample is
// misidentified by forty rings the model cannot widen to absorb it the way a
// Phase can -- it simply fits everything worse -- so the agreement index here
// is not a diagnostic beside the answer, it IS the check that the match
// happened. It is reported without a pass/fail threshold, because what OxCal
// quotes a combination against scales with the number of members and this app
// does not print a number it has not pinned against a real run (the same
// standard services/agreement.py holds Amodel to).
import {
  Box,
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
import { formatCalendarAge, formatCalendarRange, type DateFormat } from '@/utils/dateFormat'
import { POOR_AGREEMENT } from '@/utils/diagnostics'
import type { PosteriorSummary } from '@/types'

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
  /** Which member gap zero refers to; null when no sample sits exactly there. */
  referenceSampleId?: string | null
  dateFormat: DateFormat
}

const NUMERIC_CELL_SX = { whiteSpace: 'nowrap' } as const

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
  referenceSampleId,
  dateFormat,
}: Props) {
  const colors = useElementTypeColors()
  const ink = colors.wiggle_sequence.ink
  // Youngest first, which is ascending offset: an offset counts years BEFORE
  // the reference, so it increases into the past.
  const ordered = [...members].sort((a, b) => a.ring_offset - b.ring_offset)
  const referenceMember = members.find((m) => m.id === referenceSampleId)
  const width95 = reference.hpd_95 ? reference.hpd_95[1] - reference.hpd_95[0] : null
  const poor = ordered.filter(
    (m) => m.agreement_index != null && m.agreement_index < POOR_AGREEMENT
  )

  return (
    <Paper variant="outlined" sx={{ p: { xs: 2, md: 3 } }}>
      <Stack spacing={1.5}>
        <Box>
          <Typography variant="h6" component="h2" sx={{ color: ink }}>
            {name}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {members.length} date{members.length === 1 ? '' : 's'} over{' '}
            {Math.round(seriesSpan)} {gapUnits}, matched as one series.
          </Typography>
        </Box>

        {/* The answer, given the room it deserves: this is the whole model's
            single free parameter, and everything in the table below is it
            plus a number. */}
        <Box>
          <Typography variant="overline" color="text.secondary" component="div">
            {referenceMember
              ? `Age at ${referenceMember.name} (gap 0)`
              : `Age at gap 0${referenceSampleId ? '' : ' — no sample sits there'}`}
          </Typography>
          <Typography variant="h5" component="p" sx={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatCalendarAge(reference.posterior_median, dateFormat)}
          </Typography>
          {reference.hpd_95 && (
            <Typography variant="body2" color="text.secondary">
              95.4%: {formatCalendarRange(reference.hpd_95[0], reference.hpd_95[1], dateFormat, { compact: true })}
              {width95 != null ? ` (${Math.round(width95)} years wide)` : ''}
            </Typography>
          )}
          {reference.hpd_95_ranges && reference.hpd_95_ranges.length > 1 && (
            // A match that has not resolved. Worth saying plainly, because
            // the headline above still reads as one confident answer.
            <Typography variant="body2" color="warning.main">
              The 95.4% region is in {reference.hpd_95_ranges.length} separate
              ranges, so this series has not settled on one place on the curve.
              Extending it usually fixes that; adding samples inside the same
              span usually does not.
            </Typography>
          )}
        </Box>

        {/* The claim the whole panel turns on, stated rather than implied. */}
        <Typography variant="body2" color="text.secondary">
          Every date below is that same distribution moved by its own gap — one
          answer read at {members.length} points on the timber, not{' '}
          {members.length} independent results. They all have the same width for
          that reason.
        </Typography>

        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Date</TableCell>
                <TableCell align="right" sx={NUMERIC_CELL_SX}>
                  Gap ({gapUnits})
                </TableCell>
                <TableCell align="right" sx={NUMERIC_CELL_SX}>
                  Modelled age
                </TableCell>
                <TableCell align="right" sx={NUMERIC_CELL_SX}>
                  <Tooltip title="How well this date's modelled age still fits its own unmodelled calibration. The gaps are asserted, so a date at the wrong ring cannot be absorbed — it just fits worse.">
                    <span>Agreement</span>
                  </Tooltip>
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {ordered.map((member) => {
                const age = reference.posterior_median + member.ring_offset
                const low = member.agreement_index != null
                  && member.agreement_index < POOR_AGREEMENT
                return (
                  <TableRow key={member.id}>
                    <TableCell>{member.name}</TableCell>
                    <TableCell align="right" sx={NUMERIC_CELL_SX}>
                      {member.ring_offset}
                      {member.ring_offset_error
                        ? ` ± ${Math.round(member.ring_offset_error)}`
                        : ''}
                    </TableCell>
                    <TableCell align="right" sx={NUMERIC_CELL_SX}>
                      {formatCalendarAge(age, dateFormat)}
                    </TableCell>
                    <TableCell
                      align="right"
                      sx={{ ...NUMERIC_CELL_SX, color: low ? 'warning.main' : undefined }}
                    >
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
            <Box component="span" sx={{ fontVariantNumeric: 'tabular-nums' }}>
              {agreementText(combinationAgreement)}
            </Box>
          </Typography>
          <Typography variant="caption" color="text.secondary" component="p">
            No pass mark is quoted for this figure.
          </Typography>
        </Box>

        {poor.length > 0 && (
          <Typography variant="body2" color="warning.main">
            {poor.map((m) => m.name).join(', ')} agree{poor.length === 1 ? 's' : ''} poorly
            with {poor.length === 1 ? 'its' : 'their'} own calibration. In a wiggle
            match that usually means the gap is wrong — check the ring counts
            before the dates.
          </Typography>
        )}
      </Stack>
    </Paper>
  )
}
