/**
 * Deriving a ΔR from where the selected dates are.
 *
 * The second tab of the ΔR popover. Everything visible here exists because of
 * a measurement in the plan's §2, and the panel's job is to make a derived
 * number harder to over-trust than a typed one:
 *
 *   §2.5  the value moves with N -- at Callao by 189 years across the range --
 *         so N is a live control and the number updates under it. A user who
 *         never sees it move does not know their answer was a choice.
 *   §2.6  the distance to the nearest point is the single most informative
 *         thing beside the value, and Tampa's is 163 km. Shown always, flagged
 *         when it is far.
 *   §2.7  feeding habit moves the answer by a median of 8 yr and occasionally
 *         by 465. So the counterfactual is computed every time and shown as
 *         one line -- never as a prompt, which would fire on 61% of lookups
 *         to matter in 11% of them.
 *
 * The arithmetic is entirely the server's. Nothing here recomputes any part of
 * a weighted mean, because the value has to be the one calib.org's own page
 * would print and there must be exactly one implementation of that.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Link,
  MenuItem,
  Slider,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'

import { fetchSnapshots, lookupDeltaR } from '@/api/reservoir'
import type { LookupPoint, LookupResult, SnapshotSpec } from '@/api/reservoir'
import {
  HABIT_REASONS,
  counterfactualLine,
  formatDeltaR,
  habitLabel,
  isMarkedHabit,
  isWeighable,
  markedBreakdown,
  nearestIsFar,
  selectionCentre,
  centreProblem,
} from '@/utils/deltaRLookup'

export interface DeltaRLookupValue {
  delta_r: number
  delta_r_error: number
  delta_r_source: Record<string, unknown>
}

interface Props {
  /** The ticked dates. Their shared position is what is looked up. */
  dates: Array<{ latitude: number | null; longitude: number | null }>
  /** How many of them the value would be applied to. */
  marineCount: number
  onApply: (value: DeltaRLookupValue) => void
  onCancel: () => void
  busy?: boolean
}

// calib.org's own page defaults to ten.
const DEFAULT_N = 10
const N_MARKS = [1, 5, 10, 20, 30, 40, 50]

export default function DeltaRLookupPanel({
  dates,
  marineCount,
  onApply,
  onCancel,
  busy = false,
}: Props) {
  const [snapshots, setSnapshots] = useState<SnapshotSpec[] | null>(null)
  const [snapshotId, setSnapshotId] = useState('')
  const [n, setN] = useState(DEFAULT_N)
  const [excluded, setExcluded] = useState<Set<number>>(new Set())
  const [suspensionOnly, setSuspensionOnly] = useState(false)

  const [result, setResult] = useState<LookupResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const centre = useMemo(() => selectionCentre(dates), [dates])
  const problem = useMemo(() => centreProblem(dates), [dates])

  useEffect(() => {
    let cancelled = false
    fetchSnapshots()
      .then(({ items }) => {
        if (cancelled) return
        setSnapshots(items)
        // The bundled snapshot first if there is one, otherwise the most
        // recent ingest -- which the API already orders newest first.
        const preferred = items.find((s) => s.kind === 'bundled') ?? items[0]
        if (preferred) setSnapshotId(preferred.id)
      })
      .catch((err) => {
        if (!cancelled) setError((err as Error).message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Every request is numbered, and only the newest one's answer is kept.
  // Dragging the N slider fires several, and they can return out of order --
  // showing a stale value under a live control is exactly the failure §2.5's
  // whole design is meant to prevent.
  const request = useRef(0)

  const run = useCallback(() => {
    if (!centre || !snapshotId) return
    const ticket = ++request.current
    setLoading(true)
    lookupDeltaR({
      latitude: centre.latitude,
      longitude: centre.longitude,
      snapshot_id: snapshotId,
      n,
      exclude_map_nos: [...excluded],
      suspension_only: suspensionOnly,
    })
      .then((next) => {
        if (ticket !== request.current) return
        setResult(next)
        setError(null)
      })
      .catch((err) => {
        if (ticket !== request.current) return
        // The value is dropped as well as the error shown: leaving the last
        // good number on screen under a set of controls that no longer
        // produced it would be the worst of both.
        setResult(null)
        setError((err as Error).message)
      })
      .finally(() => {
        if (ticket === request.current) setLoading(false)
      })
  }, [centre, snapshotId, n, excluded, suspensionOnly])

  useEffect(() => {
    run()
  }, [run])

  const toggleExcluded = (mapNo: number) => {
    setExcluded((previous) => {
      const next = new Set(previous)
      if (next.has(mapNo)) next.delete(mapNo)
      else next.add(mapNo)
      return next
    })
  }

  if (problem) {
    return (
      <Box sx={{ p: 2, width: 560 }}>
        <Alert severity="info">{problem}</Alert>
        <Stack direction="row" justifyContent="flex-end" sx={{ mt: 2 }}>
          <Button size="small" onClick={onCancel}>Close</Button>
        </Stack>
      </Box>
    )
  }

  if (snapshots !== null && snapshots.length === 0) {
    return (
      <Box sx={{ p: 2, width: 560 }}>
        <Alert severity="info">
          There is no marine reservoir database on this installation yet. Ingest one from the
          Curves page, and ΔR can be derived from a date's position instead of typed.
        </Alert>
        <Stack direction="row" justifyContent="flex-end" sx={{ mt: 2 }}>
          <Button size="small" onClick={onCancel}>Close</Button>
        </Stack>
      </Box>
    )
  }

  const snapshot = snapshots?.find((s) => s.id === snapshotId)
  const counterfactual = result ? counterfactualLine(result) : null
  const marked = result ? markedBreakdown(result.points) : []

  return (
    <Box sx={{ p: 2, width: 560 }}>
      {/* --- the value, large. §2.5: it is the thing that moves. */}
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.5 }}>
        <Typography variant="h4" component="div" sx={{ fontWeight: 600 }}>
          {result ? formatDeltaR(result.delta_r, result.delta_r_error) : '—'}
        </Typography>
        {loading && <CircularProgress size={16} />}
        {result && (
          <Typography variant="body2" color="text.secondary">
            from {result.n} point{result.n === 1 ? '' : 's'}
          </Typography>
        )}
      </Stack>

      {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}

      {/* --- distance to the nearest point (§2.6), always, flagged when far */}
      {result && result.nearest_km !== null && (
        <Typography
          variant="body2"
          sx={{ mb: 2 }}
          color={nearestIsFar(result) ? 'warning.main' : 'text.secondary'}
        >
          Nearest measurement {result.nearest_km.toFixed(1)} km away
          {result.farthest_km !== null && `, farthest used ${result.farthest_km.toFixed(0)} km`}
          {nearestIsFar(result)
            && ' — far enough that this is a regional average rather than a local offset.'}
        </Typography>
      )}

      {/* --- N, live (§2.5) */}
      <Typography variant="body2" gutterBottom>
        Nearest points: <strong>{n}</strong>
      </Typography>
      <Slider
        size="small"
        value={n}
        min={1}
        max={50}
        marks={N_MARKS.map((value) => ({ value }))}
        onChange={(_event, value) => setN(value as number)}
        disabled={busy}
        sx={{ mb: 1 }}
      />
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 2 }}>
        The value moves with this. Somewhere like Callao it moves by nearly 200 years across the
        range — worth sliding before accepting a number.
      </Typography>

      {/* --- the counterfactual, always, as one line (§5.4.1) */}
      {counterfactual && (
        <Box sx={{ mb: 2 }}>
          <Link
            component="button"
            type="button"
            variant="body2"
            underline="hover"
            disabled={busy}
            onClick={() => setSuspensionOnly((previous) => !previous)}
          >
            {suspensionOnly ? 'Including every habit again' : counterfactual}
          </Link>
          {result?.feeding_shift !== null && result?.feeding_shift !== undefined && (
            <Typography variant="caption" color="text.secondary" display="block">
              {result.feeding_shift < 1
                ? 'The same value either way.'
                : `A difference of ${Math.round(result.feeding_shift)} yr.`}
            </Typography>
          )}
        </Box>
      )}

      {/* --- and the warning, only when it is big enough to matter */}
      {result?.feeding_shift_matters && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Excluding{' '}
          {marked.map(([habit, count], index) => (
            <span key={habit}>
              {index > 0 && ', '}
              {count} {habit}
            </span>
          ))}{' '}
          moves this by {Math.round(result.feeding_shift ?? 0)} years — more than the{' '}
          {result.feeding_shift_warn_years} yr a published ΔR typically claims for itself, so
          which set you use is a decision rather than a detail.
          {marked.some(([habit]) => habit === 'migratory') && (
            <Typography variant="caption" display="block" sx={{ mt: 0.5 }}>
              {HABIT_REASONS.migratory}
            </Typography>
          )}
        </Alert>
      )}

      {/* --- the point list */}
      {result && result.points.length > 0 && (
        <Box sx={{ maxHeight: 260, overflowY: 'auto', mb: 2 }}>
          <Box
            component="table"
            sx={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}
          >
            <Box component="thead">
              <Box component="tr" sx={{ color: 'text.secondary', textAlign: 'left' }}>
                <Box component="th" sx={{ py: 0.5 }} />
                <Box component="th" sx={{ py: 0.5 }}>Map</Box>
                <Box component="th" sx={{ py: 0.5 }}>km</Box>
                <Box component="th" sx={{ py: 0.5 }}>ΔR</Box>
                <Box component="th" sx={{ py: 0.5 }}>Habit</Box>
                <Box component="th" sx={{ py: 0.5 }}>Locality</Box>
              </Box>
            </Box>
            <Box component="tbody">
              {result.points.map((point) => (
                <PointRow
                  key={point.map_no}
                  point={point}
                  excluded={excluded.has(point.map_no)}
                  onToggle={() => toggleExcluded(point.map_no)}
                  disabled={busy}
                />
              ))}
            </Box>
          </Box>
        </Box>
      )}

      {/* --- the snapshot, small: what makes the number citable */}
      <Stack direction="row" spacing={1} sx={{ mb: 2 }} alignItems="center">
        <TextField
          select
          size="small"
          label="Database"
          value={snapshotId}
          onChange={(e) => setSnapshotId(e.target.value)}
          sx={{ minWidth: 260 }}
          disabled={busy || !snapshots}
        >
          {(snapshots ?? []).map((option) => (
            <MenuItem key={option.id} value={option.id}>
              {option.id}
              {option.kind === 'bundled' ? ' (shipped)' : ''}
            </MenuItem>
          ))}
        </TextField>
        {snapshot && (
          <Typography variant="caption" color="text.secondary">
            {snapshot.record_count.toLocaleString()} measurements
            {snapshot.citation ? ` · ${snapshot.citation}` : ''}
          </Typography>
        )}
      </Stack>

      <Stack direction="row" spacing={1} justifyContent="flex-end">
        <Button size="small" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Tooltip title={marineCount === 0 ? 'None of the selected dates is marine' : ''}>
          <span>
            <Button
              size="small"
              variant="contained"
              disabled={
                busy || loading || !result || result.delta_r === null || marineCount === 0
              }
              onClick={() => {
                if (!result || result.delta_r === null || result.delta_r_error === null) return
                onApply({
                  delta_r: Math.round(result.delta_r),
                  delta_r_error: Math.round(result.delta_r_error),
                  delta_r_source: result.delta_r_source,
                })
              }}
            >
              Apply to {marineCount} date{marineCount === 1 ? '' : 's'}
            </Button>
          </span>
        </Tooltip>
      </Stack>
    </Box>
  )
}

function PointRow({
  point,
  excluded,
  onToggle,
  disabled,
}: {
  point: LookupPoint
  excluded: boolean
  onToggle: () => void
  disabled: boolean
}) {
  const weighable = isWeighable(point)
  const marked = isMarkedHabit(point.feeding)
  // Struck through rather than hidden: a nearest-5 that quietly became a
  // nearest-4 would misreport how much evidence is behind the value.
  const muted = !weighable || excluded

  return (
    <Box
      component="tr"
      sx={{
        opacity: muted ? 0.55 : 1,
        textDecoration: weighable ? 'none' : 'line-through',
      }}
    >
      <Box component="td" sx={{ py: 0.25 }}>
        <Tooltip title={weighable ? '' : 'No published uncertainty — cannot be weighted'}>
          <span>
            <Checkbox
              size="small"
              sx={{ p: 0.25 }}
              checked={!excluded && weighable}
              disabled={disabled || !weighable}
              onChange={onToggle}
            />
          </span>
        </Tooltip>
      </Box>
      <Box component="td" sx={{ py: 0.25 }}>{point.map_no}</Box>
      <Box component="td" sx={{ py: 0.25 }}>
        {point.distance_km === null ? '—' : point.distance_km.toFixed(1)}
      </Box>
      <Box component="td" sx={{ py: 0.25, whiteSpace: 'nowrap' }}>
        {weighable
          ? formatDeltaR(point.delta_r, point.delta_r_error)
          : 'no published uncertainty'}
      </Box>
      <Box component="td" sx={{ py: 0.25 }}>
        <Tooltip title={marked ? HABIT_REASONS[point.feeding as string] ?? '' : ''}>
          <Box
            component="span"
            sx={(theme) => ({
              // The app's WARNING ink, not the diagram palette -- that encodes
              // model element types, and a habit is not one.
              color: marked ? theme.palette.warning.dark : 'inherit',
              backgroundColor: marked
                ? alpha(theme.palette.warning.main, 0.12)
                : 'transparent',
              borderRadius: 0.5,
              px: marked ? 0.5 : 0,
            })}
          >
            {habitLabel(point)}
          </Box>
        </Tooltip>
      </Box>
      <Box component="td" sx={{ py: 0.25 }}>{point.locality ?? '—'}</Box>
    </Box>
  )
}
