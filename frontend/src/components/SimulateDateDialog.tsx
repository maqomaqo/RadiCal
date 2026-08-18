import { useEffect, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  TextField,
  Typography,
} from '@mui/material'
import PosteriorPlot from '@/components/PosteriorPlot'
import { simulateDate, type SimulatedDate } from '@/api/curves'
import { formatCalendarAge, formatHPDRanges, type DateFormat } from '@/utils/dateFormat'
import { useNotify } from '@/contexts/NotificationContext'

/**
 * What a date from one calendar year would look like.
 *
 * This is the concrete counterpart of the resolution panel. That panel says
 * "a year here dates to about 300 years"; this shows the actual shape of it —
 * and in a plateau, that shape is three or four disjoint ranges, which is a
 * far more convincing account of what a plateau costs than any single number.
 *
 * No random scatter, unlike OxCal's C_Simulate: the measurement is the curve's
 * own value at that year. A drawn measurement answers "what might one
 * particular sample give", which is a different answer every time it is
 * asked; this answers "how well does this year date", which is the question a
 * curve browser exists for.
 */

const ERRORS = [15, 25, 30, 50, 100]

interface Props {
  open: boolean
  onClose: () => void
  curveId: string
  curveName: string
  calAge: number | null
  dateFormat: DateFormat
}

export default function SimulateDateDialog({
  open,
  onClose,
  curveId,
  curveName,
  calAge,
  dateFormat,
}: Props) {
  const notify = useNotify()
  const [error, setError] = useState(25)
  const [result, setResult] = useState<SimulatedDate | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || calAge === null) return
    let active = true
    setLoading(true)
    simulateDate(curveId, calAge, error)
      .then((data) => {
        if (active) setResult(data)
      })
      .catch((err) => {
        if (active) notify((err as Error).message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [open, curveId, calAge, error, notify])

  const ranges = result?.hpd_95_ranges ?? []
  const envelope = ranges.length
    ? Math.max(...ranges.map((r) => r.max)) - Math.min(...ranges.map((r) => r.min))
    : 0

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>
        A date from {calAge === null ? '' : formatCalendarAge(calAge, dateFormat)}
      </DialogTitle>
      <DialogContent dividers>
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', mb: 2, flexWrap: 'wrap' }}>
          <TextField
            select
            size="small"
            label="Measured to"
            value={error}
            onChange={(e) => setError(Number(e.target.value))}
            sx={{ minWidth: 150 }}
          >
            {ERRORS.map((value) => (
              <MenuItem key={value} value={value}>
                ±{value} yr
              </MenuItem>
            ))}
          </TextField>
          {result && (
            <Typography variant="body2" color="textSecondary">
              {curveName} gives {result.c14_age.toFixed(0)} ± {result.curve_error.toFixed(0)} ¹⁴C BP
              at this calendar age.
            </Typography>
          )}
        </Box>

        {result && (
          <>
            <PosteriorPlot
              calAges={result.probability_distribution.map(([age]) => age)}
              probabilities={result.probability_distribution.map(([, p]) => p)}
              hpd68Ranges={result.hpd_68_ranges}
              hpd95Ranges={result.hpd_95_ranges}
              sampleName={`Simulated at ${formatCalendarAge(result.cal_age, dateFormat)}`}
              dateFormat={dateFormat}
              height={340}
            />
            <Alert severity={ranges.length > 1 ? 'warning' : 'info'} sx={{ mt: 1 }}>
              {ranges.length > 1 ? (
                <>
                  A ±{error} yr measurement of a sample from this year does not give one
                  answer: it matches <strong>{ranges.length} separate calendar ranges</strong>{' '}
                  spread over {envelope.toFixed(0)} years, because the curve doubles back
                  here. That is what a plateau costs, and no amount of laboratory precision
                  removes it.
                </>
              ) : (
                <>
                  A ±{error} yr measurement of a sample from this year calibrates to{' '}
                  <strong>{formatHPDRanges(result.hpd_95_ranges, dateFormat)}</strong> —{' '}
                  {envelope.toFixed(0)} years at 95.4%.
                </>
              )}
            </Alert>
          </>
        )}
        {loading && !result && <Typography variant="body2">Calibrating…</Typography>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  )
}
