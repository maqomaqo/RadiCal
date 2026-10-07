import { useEffect, useMemo, useState } from 'react'
import {
  Box,
  Button,
  Checkbox,
  CircularProgress,
  FormControlLabel,
  Link,
  Popover,
  Stack,
  Typography,
} from '@mui/material'
import type { MatrixMeasurement } from '@/types'
import { DEFAULT_DATE_FORMAT } from '@/utils/dateFormat'
import { SIGMA_2 } from '@/utils/hpd'
import { formatApiError } from '@/utils/apiError'
import { pooledAge, type SameEventTest } from '@/utils/sameEvent'
import { SAME_EVENT_HELP, SameEventSummary } from '@/components/SameEventPanel'

/** A date as the editor holds it: the draft's own fields, which carry the
 *  curve and reservoir offset the test needs. */
export type TestableDate = MatrixMeasurement & {
  curve_id?: string
  sample_type?: string | null
  delta_r?: number | null
  delta_r_error?: number | null
}

interface Props {
  anchorEl: HTMLElement | null
  onClose: () => void
  phaseName: string
  dates: TestableDate[]
  /** Replace the ticked dates with their pooled date. Absent where the host
   *  cannot change the dates, and then not offered. */
  onReplace?: (measurementIds: string[], test: SameEventTest) => void
}

/**
 * A Phase's dates tested as one event, before the run (Ward and Wilson 1978;
 * POST /analysis/same-event). The point is a decision about the model: dates
 * that pass may be one event, to pool into one date, rather than separate
 * events in a Phase. Every date starts ticked; untick any to test the rest.
 */
export default function SameEventPopover({ anchorEl, onClose, phaseName, dates, onReplace }: Props) {
  const [excluded, setExcluded] = useState<Set<string>>(new Set())
  const [test, setTest] = useState<SameEventTest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  // Replacing takes dates out of the analysis, so it asks once first.
  const [confirming, setConfirming] = useState(false)
  const open = !!anchorEl

  const ticked = useMemo(() => dates.filter((m) => !excluded.has(m.id)), [dates, excluded])

  useEffect(() => {
    setConfirming(false)
    if (!open || ticked.length < 2) {
      setTest(null)
      setError(null)
      return
    }
    if (ticked.some((m) => m.error_1sigma == null)) {
      setTest(null)
      setError('A date has no measurement error.')
      return
    }
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    fetch('/api/analysis/same-event', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      body: JSON.stringify({
        measurements: ticked.map((m) => ({
          sample_name: m.sample_name,
          age_bp: m.age_bp,
          error_1sigma: m.error_1sigma,
          curve_id: m.curve_id,
          sample_type: m.sample_type ?? undefined,
          delta_r: m.delta_r ?? null,
          delta_r_error: m.delta_r_error ?? null,
          inbuilt_years: m.inbuilt_years ?? null,
          inbuilt_shape: m.inbuilt_shape ?? null,
        })),
      }),
    })
      .then(async (response) => {
        const body = await response.json().catch(() => null)
        if (!response.ok) throw new Error(formatApiError(body))
        setTest(body.data as SameEventTest)
      })
      .catch((err: Error) => {
        if (err.name === 'AbortError') return
        setTest(null)
        setError(err.message)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [open, ticked])

  const toggle = (id: string) =>
    setExcluded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
      transformOrigin={{ vertical: 'top', horizontal: 'left' }}
      slotProps={{ paper: { sx: { p: 2, width: 360, maxWidth: 'calc(100vw - 32px)' } } }}
    >
      <Stack spacing={2}>
        <Box>
          <Typography variant="subtitle1" component="h2" sx={{ fontWeight: 600 }}>
            {phaseName}: test as one event
          </Typography>
          {/* A new tab: following it in place would leave the model being
              edited. */}
          <Link href={SAME_EVENT_HELP} target="_blank" rel="noopener" variant="body2">
            How it works
          </Link>
        </Box>

        <Box role="group" aria-label="Dates to test">
          {dates.map((m) => (
            <FormControlLabel
              key={m.id}
              sx={{ display: 'flex', mr: 0 }}
              control={
                <Checkbox size="small" checked={!excluded.has(m.id)} onChange={() => toggle(m.id)} />
              }
              label={
                <Typography variant="body2" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                  {m.sample_name} — {m.age_bp} ± {m.error_1sigma ?? '?'} BP
                </Typography>
              }
            />
          ))}
        </Box>

        <Box aria-live="polite" sx={{ minHeight: 48 }}>
          {ticked.length < 2 ? (
            <Typography variant="body2" color="text.secondary">
              Tick two or more dates.
            </Typography>
          ) : error ? (
            <Typography variant="body2" color="error">
              {error}
            </Typography>
          ) : test ? (
            <Box sx={{ opacity: loading ? 0.5 : 1, transition: 'opacity 120ms' }}>
              <SameEventSummary
                test={test}
                confidence={SIGMA_2}
                confidenceLabel="95.4%"
                dateFormat={DEFAULT_DATE_FORMAT}
              />
              {onReplace && test.consistent && !confirming && (
                <Button
                  size="small"
                  variant="outlined"
                  disabled={loading}
                  onClick={() => setConfirming(true)}
                  sx={{ mt: 2 }}
                >
                  Replace with pooled date
                </Button>
              )}
              {onReplace && test.consistent && confirming && (
                <Box sx={{ mt: 2 }}>
                  <Typography variant="body2">
                    The {ticked.length} ticked dates become one date, {pooledAge(test)}, in{' '}
                    {phaseName}. Undo cannot restore them; they would have to be added again.
                  </Typography>
                  <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                    <Button
                      size="small"
                      variant="contained"
                      disabled={loading}
                      onClick={() => {
                        onReplace(ticked.map((m) => m.id), test)
                        onClose()
                      }}
                    >
                      Replace
                    </Button>
                    <Button size="small" onClick={() => setConfirming(false)}>
                      Cancel
                    </Button>
                  </Stack>
                </Box>
              )}
            </Box>
          ) : (
            <CircularProgress size={20} aria-label="Testing" />
          )}
        </Box>
      </Stack>
    </Popover>
  )
}
