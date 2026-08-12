import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Box,
  Button,
  Alert,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Checkbox,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  Typography,
  CircularProgress,
  Tooltip,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import EditIcon from '@mui/icons-material/Edit'
import PlaylistAddIcon from '@mui/icons-material/PlaylistAdd'
import RocketLaunchIcon from '@mui/icons-material/RocketLaunch'
import ConfirmDialog from '@/components/ConfirmDialog'
import { formatApiError } from '@/utils/apiError'
import { useNotify } from '@/contexts/NotificationContext'

// A date held in the PROJECT's collection (backend models.ProjectSample) --
// entered once here, then deployed into as many analyses as needed. Note
// deploying COPIES these values, so editing one afterwards never changes an
// analysis already created from it.
export interface ProjectSample {
  id: string
  sample_name: string
  age_bp: number
  error_1sigma: number
  material_type: string
  sample_type: 'terrestrial' | 'marine'
  curve_id: string
  default_context_name: string | null
  tpq_age_bp: number | null
  taq_age_bp: number | null
  notes: string | null
}

const CURVES = [
  { value: 'intcal20', label: 'IntCal20 (Northern Hemisphere)' },
  { value: 'shcal20', label: 'SHCal20 (Southern Hemisphere)' },
  { value: 'marine20', label: 'Marine20' },
]

type DraftForm = {
  sample_name: string
  age_bp: string
  error_1sigma: string
  material_type: string
  curve_id: string
  default_context_name: string
  tpq_age_bp: string
  taq_age_bp: string
}

const EMPTY_FORM: DraftForm = {
  sample_name: '',
  age_bp: '',
  error_1sigma: '',
  material_type: '',
  curve_id: 'intcal20',
  default_context_name: '',
  tpq_age_bp: '',
  taq_age_bp: '',
}

function toForm(s: ProjectSample): DraftForm {
  return {
    sample_name: s.sample_name,
    age_bp: String(s.age_bp),
    error_1sigma: String(s.error_1sigma),
    material_type: s.material_type ?? '',
    curve_id: s.curve_id || 'intcal20',
    default_context_name: s.default_context_name ?? '',
    tpq_age_bp: s.tpq_age_bp == null ? '' : String(s.tpq_age_bp),
    taq_age_bp: s.taq_age_bp == null ? '' : String(s.taq_age_bp),
  }
}

const numOrNull = (v: string) => (v.trim() === '' ? null : Number(v))

function formToPayload(form: DraftForm) {
  return {
    sample_name: form.sample_name.trim(),
    age_bp: Number(form.age_bp),
    error_1sigma: Number(form.error_1sigma),
    material_type: form.material_type.trim() || 'unknown',
    curve_id: form.curve_id,
    default_context_name: form.default_context_name.trim() || null,
    tpq_age_bp: numOrNull(form.tpq_age_bp),
    taq_age_bp: numOrNull(form.taq_age_bp),
  }
}

const VALID_CURVE_IDS = new Set(CURVES.map((c) => c.value))

/**
 * Parse the bulk-paste textarea: one date per line, comma or tab separated,
 * as `name, age, error[, material[, context[, curve]]]`. Deliberately
 * forgiving -- this is for pasting a column range out of a spreadsheet or a
 * lab report, so blank lines are skipped and a malformed line is reported by
 * number rather than silently dropped. Curve is optional and defaults to
 * IntCal20 -- most pasted batches are a single terrestrial curve, so it
 * would be noise to require it on every line.
 */
export function parsePastedDates(text: string): { rows: ReturnType<typeof formToPayload>[]; errors: string[] } {
  const rows: ReturnType<typeof formToPayload>[] = []
  const errors: string[] = []

  text.split(/\r?\n/).forEach((rawLine, index) => {
    const line = rawLine.trim()
    if (!line) return
    const parts = line.split(/[\t,;]/).map((p) => p.trim())
    const [name, age, error, material, context, curve] = parts
    if (!name || !age || !error) {
      errors.push(`Line ${index + 1}: expected at least "name, age, error"`)
      return
    }
    if (!Number.isFinite(Number(age)) || !Number.isFinite(Number(error))) {
      errors.push(`Line ${index + 1}: age and error must be numbers`)
      return
    }
    const curveId = curve ? curve.toLowerCase() : 'intcal20'
    if (!VALID_CURVE_IDS.has(curveId)) {
      errors.push(`Line ${index + 1}: unknown curve "${curve}" (expected intcal20, shcal20, or marine20)`)
      return
    }
    rows.push({
      sample_name: name,
      age_bp: Number(age),
      error_1sigma: Number(error),
      material_type: material || 'unknown',
      curve_id: curveId,
      default_context_name: context || null,
      tpq_age_bp: null,
      taq_age_bp: null,
    })
  })

  return { rows, errors }
}

interface Props {
  projectId: string
}

export default function ProjectSampleTable({ projectId }: Props) {
  const navigate = useNavigate()
  const notify = useNotify()
  const [samples, setSamples] = useState<ProjectSample[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const [formOpen, setFormOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<DraftForm>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  // Validation only -- request failures go through the toast queue instead,
  // same as everywhere else. This one stays inline because it names which
  // fields are missing right above the fields themselves, which a toast
  // fired from the corner of the screen can't do as directly.
  const [formError, setFormError] = useState<string | null>(null)

  const [pasteOpen, setPasteOpen] = useState(false)
  const [pasteText, setPasteText] = useState('')
  // Same reasoning as formError, but the content here can run to several
  // lines (one per unparsed row), which a single-line toast would truncate
  // or cut off entirely.
  const [pasteError, setPasteError] = useState<{ headline: string; details?: string[] } | null>(null)

  const [deleteTarget, setDeleteTarget] = useState<ProjectSample | null>(null)
  const [deleting, setDeleting] = useState(false)

  const [deployOpen, setDeployOpen] = useState(false)
  const [deployName, setDeployName] = useState('')
  const [deploying, setDeploying] = useState(false)

  const fetchSamples = async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch(`/api/projects/${projectId}/samples`)
      const data = await response.json()
      if (data.success) {
        setSamples(data.data.items)
        // Drop any selection pointing at rows that no longer exist.
        setSelected((prev) => new Set([...prev].filter((id) => data.data.items.some((s: ProjectSample) => s.id === id))))
      } else {
        setError(formatApiError(data))
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchSamples()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  const openAdd = () => {
    setEditingId(null)
    setForm(EMPTY_FORM)
    setFormError(null)
    setFormOpen(true)
  }

  const openEdit = (sample: ProjectSample) => {
    setEditingId(sample.id)
    setForm(toForm(sample))
    setFormError(null)
    setFormOpen(true)
  }

  const handleSave = async () => {
    const payload = formToPayload(form)
    if (!payload.sample_name || !Number.isFinite(payload.age_bp) || !Number.isFinite(payload.error_1sigma)) {
      setFormError('Name, age BP and 1σ error are required.')
      return
    }
    setFormError(null)
    setSaving(true)
    try {
      const response = editingId
        ? await fetch(`/api/projects/${projectId}/samples/${editingId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          })
        : await fetch(`/api/projects/${projectId}/samples`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ samples: [payload] }),
          })
      const data = await response.json()
      if (data.success) {
        setFormOpen(false)
        fetchSamples()
      } else {
        notify('Failed to save date: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error saving date: ' + (err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const handlePaste = async () => {
    const { rows, errors } = parsePastedDates(pasteText)
    if (errors.length > 0) {
      setPasteError({ headline: 'Could not read some lines:', details: errors })
      return
    }
    if (rows.length === 0) {
      setPasteError({ headline: 'No dates found. Use one per line: name, age, error' })
      return
    }
    setPasteError(null)
    setSaving(true)
    try {
      const response = await fetch(`/api/projects/${projectId}/samples`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ samples: rows }),
      })
      const data = await response.json()
      if (data.success) {
        setPasteOpen(false)
        setPasteText('')
        fetchSamples()
      } else {
        notify('Failed to add dates: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error adding dates: ' + (err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      const response = await fetch(`/api/projects/${projectId}/samples/${deleteTarget.id}`, { method: 'DELETE' })
      const data = await response.json()
      if (data.success) {
        setDeleteTarget(null)
        fetchSamples()
      } else {
        notify('Failed to delete date: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error deleting date: ' + (err as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  const handleDeploy = async () => {
    setDeploying(true)
    try {
      const response = await fetch(`/api/projects/${projectId}/deploy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: deployName.trim() || 'New analysis',
          sample_ids: [...selected],
          analysis_type: 'bayesian',
        }),
      })
      const data = await response.json()
      if (data.success) {
        setDeployOpen(false)
        setSelected(new Set())
        // Straight into the editor so contexts/stratigraphy can be set up
        // before running -- the whole point of deploying the same dates
        // more than once is modelling them differently each time.
        navigate(`/analysis/${data.data.id}/edit`)
      } else {
        notify('Failed to deploy dates: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error deploying dates: ' + (err as Error).message)
    } finally {
      setDeploying(false)
    }
  }

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const allSelected = samples.length > 0 && selected.size === samples.length

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
        <CircularProgress />
      </Box>
    )
  }

  return (
    <Box>
      <Box sx={{ display: 'flex', gap: 1, mb: 2, flexWrap: 'wrap' }}>
        <Button variant="contained" startIcon={<AddIcon />} onClick={openAdd}>
          Add Date
        </Button>
        <Button
          variant="outlined"
          startIcon={<PlaylistAddIcon />}
          onClick={() => {
            setPasteError(null)
            setPasteOpen(true)
          }}
        >
          Paste Dates
        </Button>
        <Box sx={{ flexGrow: 1 }} />
        <Tooltip title={selected.size === 0 ? 'Select dates to deploy' : ''}>
          <span>
            <Button
              variant="contained"
              color="secondary"
              startIcon={<RocketLaunchIcon />}
              disabled={selected.size === 0}
              onClick={() => {
                setDeployName('')
                setDeployOpen(true)
              }}
            >
              Deploy to New Analysis ({selected.size})
            </Button>
          </span>
        </Tooltip>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {samples.length === 0 ? (
        <Alert severity="info">
          No dates in this project's collection yet. Add dates here once, then deploy any selection of
          them into as many analyses as you like — each free to group them into different contexts.
        </Alert>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow sx={{ backgroundColor: '#f5f5f5' }}>
                <TableCell padding="checkbox">
                  <Checkbox
                    checked={allSelected}
                    indeterminate={selected.size > 0 && !allSelected}
                    onChange={() =>
                      setSelected(allSelected ? new Set() : new Set(samples.map((s) => s.id)))
                    }
                    inputProps={{ 'aria-label': 'Select all dates' }}
                  />
                </TableCell>
                <TableCell>Name</TableCell>
                <TableCell align="right">Age BP</TableCell>
                <TableCell align="right">± 1σ</TableCell>
                <TableCell>Material</TableCell>
                <TableCell>Curve</TableCell>
                <TableCell>Default Context</TableCell>
                <TableCell align="right" />
              </TableRow>
            </TableHead>
            <TableBody>
              {samples.map((s) => (
                <TableRow key={s.id} hover selected={selected.has(s.id)}>
                  <TableCell padding="checkbox">
                    <Checkbox
                      checked={selected.has(s.id)}
                      onChange={() => toggle(s.id)}
                      inputProps={{ 'aria-label': `Select ${s.sample_name}` }}
                    />
                  </TableCell>
                  <TableCell>{s.sample_name}</TableCell>
                  <TableCell align="right">{s.age_bp}</TableCell>
                  <TableCell align="right">{s.error_1sigma}</TableCell>
                  <TableCell>{s.material_type}</TableCell>
                  <TableCell>{s.curve_id}</TableCell>
                  <TableCell>{s.default_context_name || '—'}</TableCell>
                  <TableCell align="right">
                    <IconButton size="small" aria-label={`Edit ${s.sample_name}`} onClick={() => openEdit(s)}>
                      <EditIcon fontSize="small" />
                    </IconButton>
                    <IconButton
                      size="small"
                      aria-label={`Delete ${s.sample_name}`}
                      onClick={() => setDeleteTarget(s)}
                    >
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <Dialog open={formOpen} onClose={() => setFormOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>{editingId ? 'Edit Date' : 'Add Date'}</DialogTitle>
        <DialogContent>
          {formError && (
            <Alert severity="error" sx={{ mt: 1 }}>
              {formError}
            </Alert>
          )}
          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 2, mt: 1 }}>
            <TextField
              label="Sample Name / Lab Code"
              value={form.sample_name}
              onChange={(e) => setForm({ ...form, sample_name: e.target.value })}
              sx={{ gridColumn: '1 / -1' }}
            />
            <TextField
              label="Age BP"
              type="number"
              value={form.age_bp}
              onChange={(e) => setForm({ ...form, age_bp: e.target.value })}
            />
            <TextField
              label="1σ Error"
              type="number"
              value={form.error_1sigma}
              onChange={(e) => setForm({ ...form, error_1sigma: e.target.value })}
            />
            <TextField
              label="Material"
              value={form.material_type}
              onChange={(e) => setForm({ ...form, material_type: e.target.value })}
            />
            <TextField
              select
              label="Calibration Curve"
              value={form.curve_id}
              onChange={(e) => setForm({ ...form, curve_id: e.target.value })}
            >
              {CURVES.map((c) => (
                <MenuItem key={c.value} value={c.value}>
                  {c.label}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              label="Default Context"
              helperText="Pre-fills a context on deploy; overridable per analysis"
              value={form.default_context_name}
              onChange={(e) => setForm({ ...form, default_context_name: e.target.value })}
              sx={{ gridColumn: '1 / -1' }}
            />
            <TextField
              label="TPQ (max age, cal BP)"
              type="number"
              value={form.tpq_age_bp}
              onChange={(e) => setForm({ ...form, tpq_age_bp: e.target.value })}
            />
            <TextField
              label="TAQ (min age, cal BP)"
              type="number"
              value={form.taq_age_bp}
              onChange={(e) => setForm({ ...form, taq_age_bp: e.target.value })}
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setFormOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={pasteOpen} onClose={() => setPasteOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Paste Dates</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
            One date per line, comma or tab separated:
            <br />
            <code>name, age BP, 1σ error[, material[, context[, curve]]]</code>
            <br />
            Curve defaults to IntCal20 if omitted (intcal20, shcal20, or marine20).
          </Typography>
          <TextField
            fullWidth
            multiline
            minRows={8}
            placeholder={'OxA-1234, 3000, 25, charcoal, Pit 12, intcal20\nOxA-1235, 3120, 30, bone, Pit 12'}
            value={pasteText}
            onChange={(e) => setPasteText(e.target.value)}
          />
          {pasteError && (
            <Alert severity="error" sx={{ mt: 2 }}>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {pasteError.headline}
              </Typography>
              {pasteError.details && (
                <Box component="ul" sx={{ m: 0, mt: 0.5, pl: 2.5 }}>
                  {pasteError.details.map((line, i) => (
                    <li key={i}>
                      <Typography variant="body2">{line}</Typography>
                    </li>
                  ))}
                </Box>
              )}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPasteOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handlePaste} disabled={saving}>
            {saving ? 'Adding...' : 'Add Dates'}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={deployOpen} onClose={() => setDeployOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Deploy {selected.size} date{selected.size === 1 ? '' : 's'} to a new analysis</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
            The selected dates are copied into a new analysis in this project, grouped by their default
            context. You'll go straight to its editor to set up the model structure before running.
            Editing these dates in the collection later won't change that analysis.
          </Typography>
          <TextField
            fullWidth
            autoFocus
            label="Analysis Name"
            value={deployName}
            onChange={(e) => setDeployName(e.target.value)}
            placeholder="e.g. Phase model A"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeployOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleDeploy} disabled={deploying}>
            {deploying ? 'Deploying...' : 'Deploy'}
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={!!deleteTarget}
        title="Remove date from collection?"
        message={
          deleteTarget
            ? `Remove "${deleteTarget.sample_name}" from this project's date collection? Analyses already deployed from it keep their own copies and are unaffected.`
            : ''
        }
        confirmLabel="Remove"
        danger
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </Box>
  )
}
