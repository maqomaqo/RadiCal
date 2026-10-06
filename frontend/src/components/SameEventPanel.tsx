import { Box, Link, Paper, Stack, Typography } from '@mui/material'
import { Link as RouterLink } from 'react-router-dom'
import { DateFormat, formatHPDRanges } from '@/utils/dateFormat'
import {
  pooledAge,
  pooledRanges,
  sameEventFigures,
  sameEventVerdict,
  type SameEventTest,
} from '@/utils/sameEvent'

const NUMERIC_SX = { fontVariantNumeric: 'tabular-nums' } as const

/** Where the manual explains how to read the test. */
export const SAME_EVENT_HELP = '/help#same-event-test'

interface SummaryProps {
  test: SameEventTest
  confidence: number
  confidenceLabel: string
  dateFormat: DateFormat
}

/**
 * One test's answer: the verdict, its figures, and -- where pooling is
 * justified -- the pooled age and its calibration. Shared by the group
 * calibration's panel and the model editor's test of a Phase, so the two say
 * it the same way.
 */
export function SameEventSummary({ test, confidence, confidenceLabel, dateFormat }: SummaryProps) {
  if (test.refused) {
    return <Typography variant="body2">{test.refused}</Typography>
  }
  const ranges = test.consistent ? pooledRanges(test, confidence) : []
  return (
    <Stack spacing={2}>
      <Box>
        <Typography variant="subtitle1" component="p" sx={{ fontWeight: 600 }}>
          {sameEventVerdict(test)}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={NUMERIC_SX}>
          {sameEventFigures(test)}
        </Typography>
      </Box>
      {test.consistent && (
        <Box>
          <Typography variant="subtitle2" component="p" color="text.secondary">
            Pooled age
          </Typography>
          <Typography variant="h5" component="p" sx={NUMERIC_SX}>
            {pooledAge(test)}
          </Typography>
          {ranges.length > 0 && (
            <Typography variant="body2" color="text.secondary" sx={NUMERIC_SX}>
              {confidenceLabel}: {formatHPDRanges(ranges, dateFormat)}
            </Typography>
          )}
        </Box>
      )}
    </Stack>
  )
}

/**
 * A group calibration's dates tested as one event (Ward and Wilson 1978). How
 * to read it is in the manual.
 */
export default function SameEventPanel(props: SummaryProps) {
  return (
    <Paper sx={{ p: 3, mb: 3 }}>
      <Stack spacing={2}>
        <Box>
          <Typography variant="h6" component="h2">
            Same-event test
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {props.test.n} dates ·{' '}
            <Link component={RouterLink} to={SAME_EVENT_HELP}>
              How it works
            </Link>
          </Typography>
        </Box>
        <SameEventSummary {...props} />
      </Stack>
    </Paper>
  )
}
