import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  FormGroup,
  LinearProgress,
  Link,
  MenuItem,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'
import { Link as RouterLink, useNavigate } from 'react-router-dom'
import { useJob } from '@/hooks/useJob'
import { cancelJob } from '@/api/jobs'
import { useNotify } from '@/contexts/NotificationContext'
import { describeFetchError, formatApiError } from '@/utils/apiError'
import { DateFormat, formatCalendarAge } from '@/utils/dateFormat'
import { formatProbability, parseCalendarAge } from '@/utils/threshold'
import { relativeTime } from '@/utils/serverTime'
import {
  changeSense,
  describeChange,
  figure,
  quantitiesOf,
  rank,
  type InfluenceReport,
  type Statistic,
} from '@/utils/influence'

const KINDS = [
  { value: 'leave_out', label: 'Leave each date out', initial: true },
  { value: 'move', label: 'Move each date to the neighbouring Phase', initial: true },
  { value: 'outlier', label: 'Treat each date as an outlier', initial: false },
] as const

const NUMERIC_SX = { fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' } as const

/**
 * Which dates the answer rests on: the model re-solved with each date left
 * out, moved or treated as an outlier (api/influence.py), ranked by how far a
 * chosen figure moves. The figure is chosen after the run.
 */
export default function InfluencePanel({
  analysisId,
  dateFormat,
}: {
  analysisId: string
  dateFormat: DateFormat
}) {
  const notify = useNotify()
  const navigate = useNavigate()
  const [report, setReport] = useState<InfluenceReport | null>(null)
  const [stale, setStale] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [kinds, setKinds] = useState<string[]>(
    KINDS.filter((k) => k.initial).map((k) => k.value)
  )
  const [quantity, setQuantity] = useState('')
  const [statistic, setStatistic] = useState<Statistic>('median')
  const [yearText, setYearText] = useState('')
  const [opening, setOpening] = useState<string | null>(null)

  const load = async () => {
    try {
      const response = await fetch(`/api/analysis/${analysisId}/influence`)
      const body = await response.json()
      if (!response.ok) throw new Error(formatApiError(body))
      setReport(body.data)
      setStale(Boolean(body.stale))
      setJobId(body.job_id ?? null)
    } catch (error) {
      notify('Could not load the influence report: ' + describeFetchError(error))
    }
  }

  useEffect(() => {
    load()
  }, [analysisId])

  const { job, active } = useJob({
    jobId,
    onFinished: (finished) => {
      setJobId(null)
      if (finished.state === 'failed') {
        notify('The influence run failed: ' + (finished.error_message ?? 'unknown error'))
      }
      load()
    },
  })

  const quantities = useMemo(() => (report ? quantitiesOf(report) : []), [report])
  useEffect(() => {
    if (quantities.length && !quantities.some((q) => q.key === quantity)) {
      setQuantity(quantities[0].key)
    }
  }, [quantities])
  const selected = quantities.find((q) => q.key === quantity)
  const isBoundary = selected?.group === 'boundary'
  const effectiveStatistic: Statistic =
    statistic === 'p_before' && !isBoundary ? 'median' : statistic
  const threshold =
    effectiveStatistic === 'p_before' ? parseCalendarAge(yearText, dateFormat) : null

  const rows = useMemo(
    () => (report && quantity ? rank(report, quantity, effectiveStatistic, threshold) : []),
    [report, quantity, effectiveStatistic, threshold]
  )
  const baseline =
    report && quantity ? figure(report.baseline, quantity, effectiveStatistic, threshold) : null
  const largest = Math.max(0, ...rows.map((row) => Math.abs(row.change ?? 0)))

  const formatValue = (value: number | null) => {
    if (value == null) return '—'
    if (effectiveStatistic === 'p_before') return formatProbability(value)
    if (effectiveStatistic === 'median' && isBoundary) return formatCalendarAge(value, dateFormat)
    return `${Math.round(value)} yr`
  }
  const group = isBoundary ? 'boundary' : 'duration'
  const sense = changeSense(effectiveStatistic, group)
  // Which word sits at each end of the bars: older is drawn left, as on the
  // app's calendar axes; for every other figure, more is right.
  const leftWord = sense.positiveRight ? sense.less : sense.more
  const rightWord = sense.positiveRight ? sense.more : sense.less

  const start = async () => {
    try {
      const response = await fetch(`/api/analysis/${analysisId}/influence`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kinds }),
      })
      const body = await response.json()
      if (!response.ok) {
        notify('Could not start the influence run: ' + formatApiError(body))
        return
      }
      setJobId(body.data.job_id)
    } catch (error) {
      notify('Could not start the influence run: ' + describeFetchError(error))
    }
  }

  const open = async (variantId: string) => {
    setOpening(variantId)
    try {
      const response = await fetch(`/api/analysis/${analysisId}/influence/open`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ variant_id: variantId }),
      })
      const body = await response.json()
      if (!response.ok) {
        notify('Could not open the variant: ' + formatApiError(body))
        return
      }
      navigate(`/results/${body.data.id}`)
    } catch (error) {
      notify('Could not open the variant: ' + describeFetchError(error))
    } finally {
      setOpening(null)
    }
  }

  const progressDetail = job?.detail as { done?: number; total?: number; current?: string } | null

  return (
    <Paper sx={{ p: 3, mb: 3 }}>
      <Typography variant="h6" gutterBottom>
        Influence
      </Typography>
      <Typography variant="body2" color="textSecondary" sx={{ mb: 2, maxWidth: '72ch' }}>
        Which dates the answer rests on. See{' '}
        <Link component={RouterLink} to="/help#testing-influence">
          testing influence
        </Link>{' '}
        in the manual.
      </Typography>

      <Box sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 2, mb: 2 }}>
        <FormGroup row>
          {KINDS.map((kind) => (
            <FormControlLabel
              key={kind.value}
              control={
                <Checkbox
                  size="small"
                  checked={kinds.includes(kind.value)}
                  disabled={active}
                  onChange={(e) =>
                    setKinds(
                      e.target.checked
                        ? [...kinds, kind.value]
                        : kinds.filter((k) => k !== kind.value)
                    )
                  }
                />
              }
              label={kind.label}
            />
          ))}
        </FormGroup>
        <Button variant="outlined" onClick={start} disabled={active || kinds.length === 0}>
          {report ? 'Test again' : 'Test influence'}
        </Button>
      </Box>

      {active && (
        <Box sx={{ mb: 2, maxWidth: 560 }}>
          <LinearProgress
            variant={job?.progress != null ? 'determinate' : 'indeterminate'}
            value={(job?.progress ?? 0) * 100}
          />
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mt: 1 }}>
            <Typography variant="body2" color="textSecondary">
              {progressDetail?.total
                ? `Solving ${Math.min((progressDetail.done ?? 0) + 1, progressDetail.total)} of ${progressDetail.total}: ${progressDetail.current ?? ''}`
                : 'Queued'}
            </Typography>
            {jobId && (
              <Button size="small" onClick={() => cancelJob(jobId)}>
                Stop
              </Button>
            )}
          </Box>
        </Box>
      )}

      {report && stale && !active && (
        <Alert severity="info" sx={{ mb: 2 }}>
          The analysis has changed since this was tested. Test again to bring it up to date.
        </Alert>
      )}

      {report && selected && (
        <>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, mb: 2 }}>
            <TextField
              select
              size="small"
              label="Quantity"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              sx={{ minWidth: 240 }}
            >
              {quantities.map((q) => (
                <MenuItem key={q.key} value={q.key}>
                  {q.name}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              size="small"
              label="Figure"
              value={effectiveStatistic}
              onChange={(e) => setStatistic(e.target.value as Statistic)}
              sx={{ minWidth: 200 }}
            >
              <MenuItem value="median">Median</MenuItem>
              <MenuItem value="width">Width of the 95% range</MenuItem>
              {isBoundary && <MenuItem value="p_before">Probability before a year</MenuItem>}
            </TextField>
            {effectiveStatistic === 'p_before' && (
              <TextField
                size="small"
                label="Year"
                placeholder={dateFormat === 'calBP' ? '590 cal BP' : 'AD 1360'}
                value={yearText}
                onChange={(e) => setYearText(e.target.value)}
                error={yearText.trim() !== '' && threshold == null}
                sx={{ width: 160 }}
              />
            )}
          </Box>

          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Variant</TableCell>
                  <TableCell align="right">Value</TableCell>
                  <TableCell align="right">Change</TableCell>
                  <TableCell sx={{ width: 180 }}>
                    <Box
                      sx={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        width: 160,
                        fontWeight: 400,
                        color: 'text.secondary',
                      }}
                    >
                      <span>← {leftWord}</span>
                      <span>{rightWord} →</span>
                    </Box>
                  </TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                <TableRow>
                  <TableCell sx={{ fontWeight: 600 }}>The model as it stands</TableCell>
                  <TableCell align="right" sx={{ ...NUMERIC_SX, fontWeight: 600 }}>
                    {formatValue(baseline)}
                  </TableCell>
                  <TableCell align="right" sx={{ color: 'text.secondary' }}>
                    reference
                  </TableCell>
                  <TableCell />
                  <TableCell />
                </TableRow>
                {rows.map(({ variant, value, change }) => (
                  <TableRow key={variant.id}>
                    <TableCell>{variant.label}</TableCell>
                    <TableCell align="right" sx={NUMERIC_SX}>
                      {variant.error ? (
                        <Typography variant="body2" color="textSecondary" component="span">
                          {variant.error}
                        </Typography>
                      ) : (
                        formatValue(value)
                      )}
                    </TableCell>
                    <TableCell align="right" sx={NUMERIC_SX}>
                      {describeChange(change, effectiveStatistic, group)}
                    </TableCell>
                    <TableCell>
                      {/* The tornado: one bar per variant from a centre line,
                          scaled to the largest change. */}
                      <Box sx={{ position: 'relative', height: 12, width: 160 }} aria-hidden="true">
                        <Box
                          sx={{
                            position: 'absolute',
                            left: '50%',
                            top: 0,
                            bottom: 0,
                            borderLeft: 1,
                            borderColor: 'divider',
                          }}
                        />
                        {change != null && largest > 0 && (() => {
                          const right = change >= 0 === sense.positiveRight
                          const share = (50 * Math.abs(change)) / largest
                          return (
                            <Box
                              sx={{
                                position: 'absolute',
                                top: 2,
                                bottom: 2,
                                bgcolor: right ? 'primary.main' : 'secondary.main',
                                opacity: 0.75,
                                left: right ? '50%' : `${50 - share}%`,
                                width: `${share}%`,
                              }}
                            />
                          )
                        })()}
                      </Box>
                    </TableCell>
                    <TableCell align="right">
                      {!variant.error && (
                        <Button
                          size="small"
                          onClick={() => open(variant.id)}
                          disabled={opening !== null || stale}
                        >
                          Open as analysis
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          <Typography variant="body2" color="textSecondary" sx={{ mt: 1.5 }}>
            Tested {relativeTime(report.created_at)}: {report.variants.length} variants
            {report.solver === 'exact' ? ', solved exactly' : ', sampled'}.
          </Typography>
        </>
      )}
    </Paper>
  )
}
