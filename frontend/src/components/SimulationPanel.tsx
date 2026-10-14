import { useEffect, useState } from 'react'
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  LinearProgress,
  Link,
  Paper,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'
import { Link as RouterLink } from 'react-router-dom'
import { useJob } from '@/hooks/useJob'
import { useNotify } from '@/contexts/NotificationContext'
import { describeFetchError, formatApiError } from '@/utils/apiError'
import { DateFormat, formatCalendarAge } from '@/utils/dateFormat'
import { parseCalendarAge } from '@/utils/threshold'
import { relativeTime } from '@/utils/serverTime'
import { saveDownload } from '@/utils/saveDownload'

interface TruthWindow {
  element_id: string | null
  name: string
  kind: 'phase' | 'empty' | 'hiatus'
  old_bp: number
  young_bp: number
}

interface SimulationRecord {
  id: string
  name: string
  project_id: string | null
  seed: number
  replicates: number
  scatter: boolean
  inbuilt: boolean
  windows: TruthWindow[]
  created_at: string | null
  analyses: { id: string; name: string; replicate: number; status: string }[]
  job_id: string | null
}

interface Defaults {
  windows: TruthWindow[]
  closed: TruthWindow[]
  seed: number
  max_replicates: number
}

/** A truth window being edited: its two ends as typed, in the page's format. */
interface Draft extends TruthWindow {
  oldText: string
  youngText: string
}

const KIND_LABEL: Record<TruthWindow['kind'], string | null> = {
  phase: null,
  empty: 'no dates',
  hiatus: 'hiatus',
}

function toDrafts(windows: TruthWindow[], format: DateFormat): Draft[] {
  return windows.map((w) => ({
    ...w,
    oldText: formatCalendarAge(w.old_bp, format),
    youngText: formatCalendarAge(w.young_bp, format),
  }))
}

/**
 * Dates drawn from a truth set here, with this analysis's model and its
 * dates' errors, curves and allowances (api/simulation.py), saved as replicate
 * analyses in the project.
 */
export default function SimulationPanel({
  analysisId,
  analysisName,
  dateFormat,
}: {
  analysisId: string
  analysisName: string
  dateFormat: DateFormat
}) {
  const notify = useNotify()
  const [records, setRecords] = useState<SimulationRecord[]>([])
  const [jobId, setJobId] = useState<string | null>(null)
  const [defaults, setDefaults] = useState<Defaults | null>(null)
  const [open, setOpen] = useState(false)
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [closeGaps, setCloseGaps] = useState(false)
  const [name, setName] = useState('')
  const [replicates, setReplicates] = useState('5')
  const [seed, setSeed] = useState('')
  const [scatter, setScatter] = useState(true)
  const [inbuilt, setInbuilt] = useState(true)
  const [saving, setSaving] = useState(false)

  const load = async () => {
    try {
      const response = await fetch(`/api/analysis/${analysisId}/simulations`)
      const body = await response.json()
      if (!response.ok) throw new Error(formatApiError(body))
      setRecords(body.data)
      const running = (body.data as SimulationRecord[]).find((r) => r.job_id)
      setJobId(running?.job_id ?? null)
    } catch (error) {
      notify('Could not load the simulations: ' + describeFetchError(error))
    }
  }

  useEffect(() => {
    load()
  }, [analysisId])

  const { job, active } = useJob({
    jobId,
    onFinished: () => {
      setJobId(null)
      load()
    },
  })

  const begin = async () => {
    try {
      const response = await fetch(`/api/analysis/${analysisId}/simulate/defaults`)
      const body = await response.json()
      if (!response.ok) {
        notify('Could not start a simulation: ' + formatApiError(body))
        return
      }
      const found: Defaults = body.data
      setDefaults(found)
      setDrafts(toDrafts(found.windows, dateFormat))
      setCloseGaps(false)
      setName(`${analysisName} simulated`)
      setReplicates('5')
      setSeed(String(found.seed))
      setOpen(true)
    } catch (error) {
      notify('Could not start a simulation: ' + describeFetchError(error))
    }
  }

  const toggleGaps = (on: boolean) => {
    setCloseGaps(on)
    if (defaults) setDrafts(toDrafts(on ? defaults.closed : defaults.windows, dateFormat))
  }

  const parsed = drafts.map((d) => ({
    ...d,
    old: parseCalendarAge(d.oldText, dateFormat),
    young: parseCalendarAge(d.youngText, dateFormat),
  }))
  const windowsValid = parsed.every((d) => d.old != null && d.young != null && d.old >= d.young)
  const replicateCount = Number(replicates)
  const replicatesValid =
    Number.isInteger(replicateCount) &&
    replicateCount >= 0 &&
    replicateCount <= (defaults?.max_replicates ?? 20)
  const seedValid = /^\d+$/.test(seed.trim())

  const generate = async () => {
    setSaving(true)
    try {
      const response = await fetch(`/api/analysis/${analysisId}/simulate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(),
          windows: parsed.map((d) => ({
            element_id: d.element_id,
            name: d.name,
            kind: d.kind,
            old_bp: d.old,
            young_bp: d.young,
          })),
          replicates: replicateCount,
          seed: Number(seed),
          scatter,
          inbuilt,
        }),
      })
      const body = await response.json()
      if (!response.ok) {
        notify('Could not simulate: ' + formatApiError(body))
        return
      }
      setOpen(false)
      setJobId(body.data.job_id)
      load()
    } catch (error) {
      notify('Could not simulate: ' + describeFetchError(error))
    } finally {
      setSaving(false)
    }
  }

  const downloadTruth = async (record: SimulationRecord) => {
    try {
      await saveDownload(`/api/simulations/${record.id}/truth.csv`, `${record.name} truth.csv`)
    } catch (error) {
      notify('Could not download the truth key: ' + describeFetchError(error))
    }
  }

  const progress = job?.detail as { completed?: number; total?: number } | null

  return (
    <Paper sx={{ p: 3, mb: 3 }}>
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          flexWrap: 'wrap',
          gap: 2,
          mb: records.length || active ? 2 : 0,
        }}
      >
        <Box sx={{ maxWidth: '72ch' }}>
          <Typography variant="h6" gutterBottom>
            Simulation
          </Typography>
          <Typography variant="body2" color="textSecondary">
            Dates drawn from a truth you set, with this model and these dates' errors. See{' '}
            <Link component={RouterLink} to="/help#simulating-from-a-known-truth">
              simulating from a known truth
            </Link>{' '}
            in the manual.
          </Typography>
        </Box>
        <Button variant="outlined" onClick={begin} disabled={active}>
          Simulate…
        </Button>
      </Box>

      {active && (
        <Box sx={{ mb: 2, maxWidth: 560 }}>
          <LinearProgress
            variant={job?.progress != null ? 'determinate' : 'indeterminate'}
            value={(job?.progress ?? 0) * 100}
          />
          <Typography variant="body2" color="textSecondary" sx={{ mt: 1 }}>
            {progress?.total
              ? `Running replicate ${Math.min((progress.completed ?? 0) + 1, progress.total)} of ${progress.total}`
              : 'Queued'}
          </Typography>
        </Box>
      )}

      {records.length > 0 && (
        <Stack spacing={2}>
          {records.map((record) => {
            const runs = record.analyses.slice(0, 4).map((a) => a.id).join(',')
            return (
              <Box key={record.id}>
                <Typography variant="subtitle2">{record.name}</Typography>
                <Typography variant="body2" color="textSecondary" sx={{ mb: 0.5 }}>
                  {relativeTime(record.created_at)} · seed {record.seed} ·{' '}
                  {record.replicates + 1} replicates
                  {record.scatter ? '' : ', no scatter'}
                  {record.inbuilt ? '' : ', no inbuilt age'}
                </Typography>
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, alignItems: 'center' }}>
                  {record.analyses.map((a) => (
                    <Chip
                      key={a.id}
                      size="small"
                      clickable
                      component={RouterLink}
                      to={`/results/${a.id}`}
                      label={`r${a.replicate}`}
                      color={a.status === 'failed' ? 'error' : 'default'}
                      variant={a.status === 'completed' ? 'filled' : 'outlined'}
                    />
                  ))}
                  {record.project_id && record.analyses.length > 1 && (
                    <Button
                      size="small"
                      component={RouterLink}
                      to={`/projects/${record.project_id}/compare?runs=${runs}`}
                    >
                      Compare
                    </Button>
                  )}
                  <Button size="small" onClick={() => downloadTruth(record)}>
                    Truth key (CSV)
                  </Button>
                </Box>
              </Box>
            )
          })}
        </Stack>
      )}

      <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="md">
        <DialogTitle>Simulate from this model</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '8px !important' }}>
          <TextField
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            inputProps={{ maxLength: 120 }}
          />
          <Box>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Typography variant="subtitle2">Truth</Typography>
              <FormControlLabel
                control={<Switch checked={closeGaps} onChange={(e) => toggleGaps(e.target.checked)} />}
                label="Close every gap"
              />
            </Box>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Phase</TableCell>
                    <TableCell>Older end</TableCell>
                    <TableCell>Younger end</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {parsed.map((d, index) => (
                    <TableRow key={d.element_id ?? 'all'}>
                      <TableCell>
                        {d.name}
                        {KIND_LABEL[d.kind] && (
                          <Chip size="small" label={KIND_LABEL[d.kind]} sx={{ ml: 1 }} />
                        )}
                      </TableCell>
                      {(['oldText', 'youngText'] as const).map((field) => (
                        <TableCell key={field}>
                          <TextField
                            size="small"
                            value={d[field]}
                            onChange={(e) =>
                              setDrafts(
                                drafts.map((row, i) =>
                                  i === index ? { ...row, [field]: e.target.value } : row
                                )
                              )
                            }
                            error={
                              (field === 'oldText' ? d.old : d.young) == null ||
                              (d.old != null && d.young != null && d.old < d.young)
                            }
                            inputProps={{
                              'aria-label': `${field === 'oldText' ? 'Older' : 'Younger'} end of ${d.name}`,
                            }}
                            sx={{ width: 170 }}
                          />
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Box>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center' }}>
            <TextField
              label="Noisy replicates"
              value={replicates}
              onChange={(e) => setReplicates(e.target.value)}
              error={!replicatesValid}
              helperText={`r0 plus this many, up to ${defaults?.max_replicates ?? 20}`}
              sx={{ width: 180 }}
            />
            <TextField
              label="Seed"
              value={seed}
              onChange={(e) => setSeed(e.target.value)}
              error={!seedValid}
              helperText="The same seed draws the same dates"
              sx={{ width: 200 }}
            />
            <FormControlLabel
              control={<Checkbox checked={scatter} onChange={(e) => setScatter(e.target.checked)} />}
              label="Laboratory scatter"
            />
            <FormControlLabel
              control={<Checkbox checked={inbuilt} onChange={(e) => setInbuilt(e.target.checked)} />}
              label="Inbuilt age"
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={generate}
            disabled={saving || !windowsValid || !replicatesValid || !seedValid || !name.trim()}
          >
            Simulate and run
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  )
}
