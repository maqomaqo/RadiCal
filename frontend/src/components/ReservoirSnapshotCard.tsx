/**
 * The marine reservoir database this installation mirrors, and whether
 * calib.org has moved on.
 *
 * On the Curves page because a reservoir snapshot is the same kind of thing as
 * a calibration curve: versioned reference data that a published result
 * depends on, immutable once stored, citable by id. It sits under the curves
 * rather than beside them because it is not one, and because only marine dates
 * ever touch it.
 *
 * **Nothing here happens on its own.** Loading the page reads what is already
 * mirrored -- one local request. Asking calib.org what it holds is a button,
 * and re-ingesting is a second button. That is §5.6, and the reason is not
 * politeness about somebody's server: a ΔR that changed because a remote
 * database changed, underneath an analysis somebody has already published, is
 * exactly the failure a frozen snapshot exists to prevent. Refreshing has to
 * be a decision, taken by a person, at a moment they chose.
 */
import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  CircularProgress,
  Paper,
  Stack,
  Typography,
} from '@mui/material'
import RefreshIcon from '@mui/icons-material/Refresh'
import CloudDownloadIcon from '@mui/icons-material/CloudDownload'

import { fetchFreshness, fetchSnapshots, ingestSnapshot } from '@/api/reservoir'
import type { Freshness, SnapshotSpec } from '@/api/reservoir'
import { useNotify } from '@/contexts/NotificationContext'

/** "2026-05-11", from the source's own stamp. Its time of day is noise here. */
function asDate(stamp: string | null | undefined): string {
  if (!stamp) return 'an unrecorded date'
  return stamp.slice(0, 10)
}

export default function ReservoirSnapshotCard() {
  const notify = useNotify()
  const [snapshots, setSnapshots] = useState<SnapshotSpec[] | null>(null)
  const [freshness, setFreshness] = useState<Freshness | null>(null)
  const [checking, setChecking] = useState(false)
  const [ingesting, setIngesting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    // Local only. This is the app asking itself what it has.
    fetchSnapshots()
      .then(({ items }) => setSnapshots(items))
      .catch((err) => setError((err as Error).message))
  }

  useEffect(load, [])

  const check = () => {
    setChecking(true)
    setError(null)
    fetchFreshness()
      .then(setFreshness)
      .catch((err) => setError((err as Error).message))
      .finally(() => setChecking(false))
  }

  const ingest = () => {
    setIngesting(true)
    setError(null)
    ingestSnapshot()
      .then(({ snapshot, report }) => {
        notify(
          `Stored ${snapshot.id}: ${snapshot.record_count.toLocaleString()} measurements`
          + (report.without_error ? `, ${report.without_error} with no published uncertainty` : ''),
        )
        setFreshness(null)
        load()
      })
      .catch((err) => setError((err as Error).message))
      .finally(() => setIngesting(false))
  }

  // Newest first, as the API orders them.
  const current = snapshots?.[0]
  const busy = checking || ingesting

  return (
    <Paper sx={{ p: 3, mt: 3 }}>
      <Typography variant="h6" component="h2" gutterBottom>
        Marine reservoir database
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ maxWidth: '64ch', mb: 2 }}>
        RadiCal uses this dataset when you choose to set ΔR based on the spatial position of a date.
        A snapshot of the database is frozen once stored, like a calibration curve, so analysed dates keep their reference point.
      </Typography>

      {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}

      {snapshots === null ? (
        <CircularProgress size={20} />
      ) : (
        <>
          {/* --- the line §5.6 asks for */}
          <Typography variant="body2" sx={{ mb: 2 }}>
            {current ? (
              <>
                <strong>{current.id}</strong>:{' '}
                {current.record_count.toLocaleString()} measurements to{' '}
                {asDate(current.source_last_changed)}.
              </>
            ) : (
              <>
                Nothing is mirrored yet, so a ΔR cannot be derived from a position until a
                database is fetched.
              </>
            )}
            {freshness && (
              <>
                {' '}calib.org now reports{' '}
                {freshness.source.record_count.toLocaleString()} measurements to{' '}
                {asDate(freshness.source.source_last_changed)}.
              </>
            )}
          </Typography>

          {freshness && !freshness.behind && (
            <Alert severity="success" sx={{ mb: 2 }}>
              This mirror is current. Nothing to fetch.
            </Alert>
          )}

          {freshness?.behind && current && (
            <Alert severity="info" sx={{ mb: 2 }}>
              Fetching adds a NEW snapshot beside this one. Nothing already computed changes:
              dates that cite {current.id} will continue to cite that version.
            </Alert>
          )}

          <Stack direction="row" spacing={1}>
            <Button
              size="small"
              variant="outlined"
              startIcon={checking ? <CircularProgress size={14} /> : <RefreshIcon />}
              onClick={check}
              disabled={busy}
            >
              Check calib.org
            </Button>
            {(freshness?.behind || !current) && (
              <Button
                size="small"
                variant="contained"
                startIcon={ingesting ? <CircularProgress size={14} /> : <CloudDownloadIcon />}
                onClick={ingest}
                disabled={busy}
              >
                {current ? 'Fetch a new snapshot' : 'Fetch the database'}
              </Button>
            )}
          </Stack>

          <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1.5 }}>
            Checking costs one small request; fetching costs about a megabyte and takes a few
            seconds.
          </Typography>
        </>
      )}
    </Paper>
  )
}
