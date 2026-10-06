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

interface SameEventPanelProps {
  test: SameEventTest
  confidence: number
  confidenceLabel: string
  dateFormat: DateFormat
}

const NUMERIC_SX = { fontVariantNumeric: 'tabular-nums' } as const

/**
 * A group calibration's dates tested as one event (Ward and Wilson 1978):
 * the verdict, the test's figures, and -- where pooling is justified -- the
 * pooled age and its calibration. How to read it is in the manual.
 */
export default function SameEventPanel({
  test,
  confidence,
  confidenceLabel,
  dateFormat,
}: SameEventPanelProps) {
  const ranges = test.consistent ? pooledRanges(test, confidence) : []

  return (
    <Paper sx={{ p: 3, mb: 3 }}>
      <Stack spacing={2}>
        <Box>
          <Typography variant="h6" component="h2">
            Same-event test
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {test.n} dates ·{' '}
            <Link component={RouterLink} to="/help#same-event-test">
              How it works
            </Link>
          </Typography>
        </Box>

        {test.refused ? (
          <Typography variant="body2">{test.refused}</Typography>
        ) : (
          <Box>
            <Typography variant="subtitle1" component="p" sx={{ fontWeight: 600 }}>
              {sameEventVerdict(test)}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={NUMERIC_SX}>
              {sameEventFigures(test)}
            </Typography>
          </Box>
        )}

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
    </Paper>
  )
}
