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
import UploadFileIcon from '@mui/icons-material/UploadFile'
import ConfirmDialog from '@/components/ConfirmDialog'
import ImportDatesDialog, { type ImportPreview } from '@/components/ImportDatesDialog'
import SampleSelectionBar, { type DeltaRValue } from '@/components/SampleSelectionBar'
import { formatApiError } from '@/utils/apiError'
import { useNotify } from '@/contexts/NotificationContext'
import { curveLabel, curveOptions } from '@/api/curves'
import { useCurves } from '@/hooks/useCurves'

// A date in the PROJECT's collection (backend models.ProjectSample), entered
// once and deployed into as many analyses as needed. Deploying COPIES these
// values, so editing one never changes an analysis already created from it.
export interface ProjectSample {
  id: string
  sample_name: string
  age_bp: number
  error_1sigma: number
  material_type: string
  sample_type: 'terrestrial' | 'marine'
  curve_id: string
  delta_r: number | null
  delta_r_error: number | null
  default_context_name: string | null
  tpq_age_bp: number | null
  taq_age_bp: number | null
  notes: string | null
}

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

/**
 * Parse the bulk-paste textarea: one date per line, comma or tab separated, as
 * `name, age, error[, material[, context[, curve]]]`. Forgiving, since this is
 * for pasting a column range out of a spreadsheet or lab report -- blank lines
 * are skipped and a malformed one is reported by number rather than dropped.
 * Curve is optional and defaults to IntCal20, most batches being a single
 * terrestrial curve.
 *
 * `validCurveIds` is passed in so this stays pure while the curves come from
 * the backend registry. An empty set accepts any curve id and leaves rejection
 * to the backend, which is right when the curve list could not be loaded:
 * refusing every line would blame the paste for a failed request.
 */
export function parsePastedDates(
  text: string,
  validCurveIds: Set<string>
): { rows: ReturnType<typeof formToPayload>[]; errors: string[] } {
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
    if (validCurveIds.size > 0 && !validCurveIds.has(curveId)) {
      errors.push(
        `Line ${index + 1}: unknown curve "${curve}" (expected ` +
          `${[...validCurveIds].join(', ')})`
      )
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
  const { curves, loading: curvesLoading, error: curvesError } = useCurves()
  const [samples, setSamples] = useState<ProjectSample[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const [formOpen, setFormOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<DraftForm>(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  // Validation only; request failures go through the toast queue. This stays
  // inline because it names the missing fields directly above them.
  const [formError, setFormError] = useState<string | null>(null)

  const [pasteOpen, setPasteOpen] = useState(false)
  const [pasteText, setPasteText] = useState('')
  // As formError, but the content can run to several lines, one per unparsed
  // row, which a single-line toast would cut off.
  const [pasteError, setPasteError] = useState<{ headline: string; details?: string[] } | null>(null)

  const [importOpen, setImportOpen] = useState(false)
  const [importing, setImporting] = useState(false)

  const [deleteTarget, setDeleteTarget] = useState<ProjectSample | null>(null)
  const [deleting, setDeleting] = useState(false)

  // One flag for every bulk action: they act on the same selection, so two at
  // once would be two answers to one question.
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)

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
    const { rows, errors } = parsePastedDates(
      pasteText,
      new Set(curves.map((curve) => curve.id))
    )
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

  /**
   * Add an uploaded file's dates to the collection.
   *
   * Through POST /projects/:id/samples, the endpoint the Add and Paste paths
   * use, so an imported date is stored by the code a typed one is. The phase
   * each date was filed under becomes its default context, which deploying
   * then groups by.
   */
  const handleImport = async (preview: ImportPreview) => {
    setImporting(true)
    try {
      const response = await fetch(`/api/projects/${projectId}/samples`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          samples: preview.dates.map((d) => ({
            sample_name: d.sample_name,
            age_bp: d.age_bp,
            error_1sigma: d.error_1sigma,
            material_type: d.material_type || 'unknown',
            curve_id: d.curve_id,
            sample_type: d.sample_type,
            default_context_name: d.phase_name,
            tpq_age_bp: d.tpq_age_bp,
            taq_age_bp: d.taq_age_bp,
            depth_cm: d.depth_cm,
            location: d.location,
            notes: d.notes,
          })),
        }),
      })
      const data = await response.json()
      if (data.success) {
        setImportOpen(false)
        notify(`Added ${data.data.items.length} date${data.data.items.length === 1 ? '' : 's'} to the collection.`)
        fetchSamples()
      } else {
        notify('Failed to import dates: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error importing dates: ' + (err as Error).message)
    } finally {
      setImporting(false)
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

  // The three bulk actions. Each reports what the server DID rather than what
  // was asked: a Delta-R on a mixed selection lands on the marine dates only,
  // and assigning a marine curve changes sample types with it.
  const runBulk = async (
    request: () => Promise<Response>,
    describe: (data: any) => { message: string; severity?: 'success' | 'info' },
    failure: string,
  ) => {
    setBulkBusy(true)
    try {
      const response = await request()
      const data = await response.json()
      if (data.success) {
        // notify() defaults to 'error', which would report a completed bulk
        // edit in red. 'info' rather than 'success' where the server skipped
        // part of the selection: nothing went wrong, but not everything the
        // reader ticked changed.
        const { message, severity = 'success' } = describe(data.data)
        notify(message, severity)
        await fetchSamples()
        return true
      }
      notify(`${failure}: ${formatApiError(data)}`)
    } catch (err) {
      notify(`${failure}: ${(err as Error).message}`)
    } finally {
      setBulkBusy(false)
    }
    return false
  }

  const handleAssignCurve = (curveId: string) =>
    runBulk(
      () =>
        fetch(`/api/projects/${projectId}/samples`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sample_ids: [...selected], curve_id: curveId }),
        }),
      (data) => {
        const name = curveLabel(curves, curveId)
        const changed = data.sample_type_changed_count
        return {
          message:
            `${data.items.length} date${data.items.length === 1 ? '' : 's'} set to ${name}` +
            (changed ? `, and ${changed} sample type${changed === 1 ? '' : 's'} with them` : ''),
        }
      },
      'Could not set the curve',
    )

  const handleAssignDeltaR = (value: DeltaRValue) =>
    runBulk(
      () =>
        fetch(`/api/projects/${projectId}/samples`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sample_ids: [...selected], ...value }),
        }),
      (data) => {
        if (value.delta_r === null) {
          return {
            message: `ΔR removed from ${data.updated_count} date${
              data.updated_count === 1 ? '' : 's'
            }`,
          }
        }
        const applied = `ΔR ${value.delta_r} ± ${value.delta_r_error} applied to ${data.updated_count} date${
          data.updated_count === 1 ? '' : 's'
        }`
        return data.skipped_count
          ? {
              message: `${applied}; ${data.skipped_count} terrestrial date${
                data.skipped_count === 1 ? '' : 's'
              } skipped`,
              severity: 'info' as const,
            }
          : { message: applied }
      },
      'Could not apply ΔR',
    )

  const handleBulkDelete = async () => {
    const ok = await runBulk(
      () =>
        fetch(`/api/projects/${projectId}/samples`, {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sample_ids: [...selected] }),
        }),
      (data) => ({
        message: `${data.deleted_count} date${data.deleted_count === 1 ? '' : 's'} deleted`,
      }),
      'Could not delete the selected dates',
    )
    if (ok) {
      setSelected(new Set())
      setBulkDeleteOpen(false)
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
        // Straight into the editor so contexts and stratigraphy can be set up
        // before running: deploying the same dates twice is for modelling them
        // differently each time.
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
        <Button
          variant="outlined"
          startIcon={<UploadFileIcon />}
          onClick={() => setImportOpen(true)}
        >
          Import File
        </Button>
      </Box>

      {/* Everything that acts on a selection lives here, and appears only
          when there is one -- including Deploy, which used to sit at the end
          of the toolbar above permanently greyed out. */}
      {selected.size > 0 && (
        <SampleSelectionBar
          selected={samples.filter((s) => selected.has(s.id))}
          curves={curves}
          busy={bulkBusy}
          onClear={() => setSelected(new Set())}
          onDeploy={() => {
            setDeployName('')
            setDeployOpen(true)
          }}
          onAssignCurve={handleAssignCurve}
          onAssignDeltaR={handleAssignDeltaR}
          onDelete={() => setBulkDeleteOpen(true)}
        />
      )}

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
              <TableRow sx={{ backgroundColor: 'background.muted' }}>
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
                  <TableCell>{curveLabel(curves, s.curve_id)}</TableCell>
                  <TableCell>{s.default_context_name || '—'}</TableCell>
                  <TableCell align="right">
                    <IconButton size="small" aria-label={`Edit ${s.sample_name}`} onClick={() => openEdit(s)}>
                      <EditIcon fontSize="small" />
                    </IconButton>
                    <Tooltip title={`Delete ${s.sample_name}`}>
                      <IconButton
                        size="small"
                        aria-label={`Delete ${s.sample_name}`}
                        onClick={() => setDeleteTarget(s)}
                        sx={{ '&:hover, &:focus-visible': { color: 'error.main' } }}
                      >
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
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
              disabled={curvesLoading}
              error={!!curvesError}
              helperText={curvesError ?? undefined}
              onChange={(e) => setForm({ ...form, curve_id: e.target.value })}
            >
              {curveOptions(curves, form.curve_id).map((option) => (
                <MenuItem key={option.id} value={option.id}>
                  {option.label}
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
            Curve defaults to IntCal20 if omitted
            {curves.length > 0 && ` (${curves.map((curve) => curve.id).join(', ')})`}.
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

      <ImportDatesDialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        target="collection"
        confirming={importing}
        onConfirm={handleImport}
      />

      <ConfirmDialog
        open={bulkDeleteOpen}
        title={`Remove ${selected.size} date${selected.size === 1 ? '' : 's'} from collection?`}
        message={
          `Removes ${selected.size === 1 ? 'this date' : 'these dates'} from this project's date ` +
          `collection. Analyses already deployed from ${selected.size === 1 ? 'it' : 'them'} keep ` +
          'their own copies and are unaffected.'
        }
        confirmLabel="Delete"
        danger
        loading={bulkBusy}
        onConfirm={handleBulkDelete}
        onCancel={() => setBulkDeleteOpen(false)}
      />

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
