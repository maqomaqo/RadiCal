import { useEffect, useMemo, useState, type ReactNode } from 'react'
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
  TableSortLabel,
  Menu,
  Divider,
  ListItemIcon,
  ListItemText,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import EditIcon from '@mui/icons-material/Edit'
import PlaylistAddIcon from '@mui/icons-material/PlaylistAdd'
import UploadFileIcon from '@mui/icons-material/UploadFile'
import MoreVertIcon from '@mui/icons-material/MoreVert'
import ViewColumnIcon from '@mui/icons-material/ViewColumn'
import ConfirmDialog from '@/components/ConfirmDialog'
import ImportDatesDialog, { type ImportPreview } from '@/components/ImportDatesDialog'
import SampleSelectionBar, { type DeltaRValue } from '@/components/SampleSelectionBar'
import PlaceOnMapDialog from '@/components/PlaceOnMapDialog'
import { formatApiError } from '@/utils/apiError'
import { useNotify } from '@/contexts/NotificationContext'
import { curveLabel, curveOptions } from '@/api/curves'
import { useCurves } from '@/hooks/useCurves'
import {
  coordinateHalfMissing,
  deltaRErrorMissing,
  isMarineMeasurement,
} from '@/components/MeasurementFields'
import {
  columnVisible,
  loadColumnOverrides,
  saveColumnOverrides,
  type ColumnOverrides,
} from '@/utils/columnPreferences'
import { formatPosition } from '@/utils/coordinates'
// Pure, and deliberately not defined here: this file reaches Plotly through
// the map dialog, so anything a Node test needs has to live outside it.
import { parsePastedDates } from '@/utils/pastedDates'

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
  /** Superseded by delta_r, but still carried on older and imported rows. */
  reservoir_age: number | null
  depth_cm: number | null
  // Where the date sits in a wiggle-matched series: years BEFORE the series
  // reference, 0 at the reference and increasing into the past. Held in the
  // collection because it is a property of the SAMPLE -- which ring of which
  // timber it came from -- not of any one analysis.
  ring_offset: number | null
  ring_offset_error: number | null
  // Where the sample came from, in WGS84 decimal degrees. Always the stored
  // value: the project's coordinate_precision governs what leaves in an export
  // or a figure, not what its own owner reads back in the table.
  latitude: number | null
  longitude: number | null
  location: string | null
  default_context_name: string | null
  tpq_age_bp: number | null
  taq_age_bp: number | null
  notes: string | null
}

/**
 * A column the reader can turn on or off.
 *
 * `auto` is whether the collection earns the column unasked; `populated` is
 * whether anything is in it at all, which the picker says out loud so ticking
 * "Notes" on a collection that has none reads as empty rather than broken.
 * The two differ on ΔR alone: an empty ΔR cell on a marine date is the finding,
 * not a gap.
 */
interface OptionalColumn {
  id: string
  label: string
  auto: boolean
  populated: boolean
  align?: 'left' | 'right'
  numeric?: boolean
  sortKey?: SortKey
  headTitle?: string
  render: (sample: ProjectSample) => ReactNode
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
  delta_r: string
  delta_r_error: string
  reservoir_age: string
  depth_cm: string
  ring_offset: string
  ring_offset_error: string
  latitude: string
  longitude: string
  location: string
  notes: string
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
  delta_r: '',
  delta_r_error: '',
  reservoir_age: '',
  depth_cm: '',
  ring_offset: '',
  ring_offset_error: '',
  latitude: '',
  longitude: '',
  location: '',
  notes: '',
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
    delta_r: s.delta_r == null ? '' : String(s.delta_r),
    delta_r_error: s.delta_r_error == null ? '' : String(s.delta_r_error),
    reservoir_age: s.reservoir_age == null ? '' : String(s.reservoir_age),
    depth_cm: s.depth_cm == null ? '' : String(s.depth_cm),
    ring_offset: s.ring_offset == null ? '' : String(s.ring_offset),
    ring_offset_error: s.ring_offset_error == null ? '' : String(s.ring_offset_error),
    latitude: s.latitude == null ? '' : String(s.latitude),
    longitude: s.longitude == null ? '' : String(s.longitude),
    location: s.location ?? '',
    notes: s.notes ?? '',
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
    // Sent whether or not they are filled in, so emptying one clears it. The
    // PATCH applies exactly the fields the body carries (exclude_unset), so a
    // field this form omits is left alone -- which is why these four survived
    // every edit before the form could reach them, and why they now have to
    // be sent explicitly rather than dropped when blank.
    delta_r: numOrNull(form.delta_r),
    delta_r_error: numOrNull(form.delta_r_error),
    reservoir_age: numOrNull(form.reservoir_age),
    depth_cm: numOrNull(form.depth_cm),
    ring_offset: numOrNull(form.ring_offset),
    ring_offset_error: numOrNull(form.ring_offset_error),
    latitude: numOrNull(form.latitude),
    longitude: numOrNull(form.longitude),
    location: form.location.trim() || null,
    notes: form.notes.trim() || null,
  }
}

/**
 * What the collection table can be ordered by. Display only: the collection is
 * a store, not a sequence -- an analysis owns the order its own dates are
 * modelled in, which is what `SortDatesButton` edits elsewhere. Nothing here
 * is written back.
 */
type SortKey = 'sample_name' | 'age_bp' | 'error_1sigma' | 'default_context_name'

function compareSamples(a: ProjectSample, b: ProjectSample, key: SortKey): number {
  if (key === 'age_bp' || key === 'error_1sigma') return a[key] - b[key]
  // Numeric collation so a lab code ending in 7 sorts before one ending in 12,
  // which is the whole reason anyone sorts a list of lab codes.
  return (a[key] ?? '').localeCompare(b[key] ?? '', undefined, { numeric: true })
}

/**
 * A ΔR cell.
 *
 * The column exists whenever any date in the collection is marine, because the
 * fact worth seeing is the ABSENCE of a correction: a marine date with no ΔR
 * calibrates as ΔR = 0, which is a choice, and this table is where it would be
 * caught. Said in words rather than flagged with a warning glyph -- assuming
 * the global average is legitimate, not an error.
 */
function deltaRCell(sample: ProjectSample, marine: boolean) {
  if (!marine) return { text: '—', muted: true, title: 'Terrestrial date: no reservoir offset applies.' }
  if (sample.delta_r == null) {
    return {
      text: 'none set',
      muted: true,
      title: 'No reservoir correction set. This date calibrates as ΔR = 0.',
    }
  }
  const error = sample.delta_r_error == null ? '' : ` ± ${sample.delta_r_error}`
  return { text: `${sample.delta_r}${error}`, muted: false, title: '' }
}

/** "TPQ 3000 · TAQ 2000", either half omitted when it is not set. */
function limitsLabel(sample: ProjectSample): string {
  const parts: string[] = []
  if (sample.tpq_age_bp != null) parts.push(`TPQ ${sample.tpq_age_bp}`)
  if (sample.taq_age_bp != null) parts.push(`TAQ ${sample.taq_age_bp}`)
  return parts.join(' · ')
}

interface Props {
  projectId: string
  /**
   * Whether this project has turned basemap tiles on. Passed down rather than
   * fetched here: the flag belongs to the project, and the page that owns the
   * project has it already.
   */
  mapTilesEnabled?: boolean
}

export default function ProjectSampleTable({ projectId, mapTilesEnabled = false }: Props) {
  const navigate = useNavigate()
  const notify = useNotify()
  const { curves, loading: curvesLoading, error: curvesError } = useCurves()
  const [samples, setSamples] = useState<ProjectSample[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [placeOpen, setPlaceOpen] = useState(false)

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
    if (deltaRErrorIncomplete) {
      setFormError('A ΔR needs its 1σ error, or clear both to apply no correction.')
      return
    }
    if (coordinateIncomplete) {
      setFormError('A position needs both a latitude and a longitude, or neither.')
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
            ring_offset: d.ring_offset,
            ring_offset_error: d.ring_offset_error,
            latitude: d.latitude,
            longitude: d.longitude,
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

  /**
   * One position across the whole selection -- the map's Apply, and its
   * Remove.
   *
   * Through the same bulk PATCH ΔR goes through, but it reaches every date
   * rather than the marine ones: where a sample came from is a fact about the
   * place, not about the material. Null for both halves unplaces them; the
   * endpoint refuses one without the other, as it should.
   */
  const handlePlaceSelection = (
    latitude: number | null,
    longitude: number | null,
  ) =>
    runBulk(
      () =>
        fetch(`/api/projects/${projectId}/samples`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sample_ids: [...selected], latitude, longitude }),
        }),
      (data) => {
        const n = data.updated_count
        const dates = `${n} date${n === 1 ? '' : 's'}`
        return {
          message:
            latitude === null
              ? `Position removed from ${dates}`
              : `${dates} placed at ${latitude}, ${longitude}`,
        }
      },
      'Could not place the selected dates',
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

  // Which dates are marine decides whether the ΔR column is drawn at all, and
  // it is the curve registry that answers that, not the stored sample_type
  // alone -- see isMarineMeasurement.
  const marineIds = useMemo(
    () => new Set(samples.filter((s) => isMarineMeasurement(s, curves)).map((s) => s.id)),
    [samples, curves]
  )

  /**
   * A column earns its width by carrying information, and the reader can
   * overrule that either way.
   *
   * Material is stored as the literal string 'unknown' when nobody filled it
   * in, so a collection imported without materials drew thirty-two rows of the
   * app's own placeholder in the widest text column on the page. TPQ/TAQ,
   * reservoir age, depth, location and notes are all carried by the API and
   * set on a minority of collections. ΔR is the exception that proves the
   * rule: it appears whenever anything is marine, precisely because an empty
   * cell there is the finding.
   */
  const optionalColumns = useMemo<OptionalColumn[]>(() => {
    const anyOf = (has: (s: ProjectSample) => boolean) => samples.some(has)
    const muted = (text: string) => (
      <Box component="span" sx={{ color: 'text.disabled' }}>
        {text}
      </Box>
    )
    return [
      {
        id: 'delta_r',
        label: 'ΔR',
        // Not `populated`: the reason this column exists is to show which
        // marine dates have NO correction set.
        auto: marineIds.size > 0,
        // A marine date with no offset is content, not a blank -- the column
        // reports "none set" on it. Calling that "no values" in the picker
        // would advertise the one column whose empties are the finding as the
        // one worth turning off.
        populated: marineIds.size > 0 || anyOf((s) => s.delta_r != null),
        align: 'right',
        numeric: true,
        headTitle: 'Reservoir offset. Applies to marine dates only.',
        render: (s) => {
          const cell = deltaRCell(s, marineIds.has(s.id))
          return (
            <Tooltip title={cell.title}>
              <Box component="span" sx={{ color: cell.muted ? 'text.disabled' : undefined }}>
                {cell.text}
              </Box>
            </Tooltip>
          )
        },
      },
      {
        id: 'material',
        label: 'Material',
        auto: anyOf((s) => !!s.material_type && s.material_type !== 'unknown'),
        populated: anyOf((s) => !!s.material_type && s.material_type !== 'unknown'),
        render: (s) =>
          s.material_type && s.material_type !== 'unknown' ? s.material_type : muted('—'),
      },
      {
        id: 'limits',
        label: 'Limits',
        auto: anyOf((s) => s.tpq_age_bp != null || s.taq_age_bp != null),
        populated: anyOf((s) => s.tpq_age_bp != null || s.taq_age_bp != null),
        numeric: true,
        headTitle: 'TPQ and TAQ, in cal BP.',
        render: (s) => limitsLabel(s) || muted('—'),
      },
      {
        id: 'context',
        label: 'Default Context',
        auto: true,
        populated: anyOf((s) => !!s.default_context_name),
        sortKey: 'default_context_name',
        render: (s) => s.default_context_name || muted('—'),
      },
      {
        id: 'reservoir_age',
        label: 'Reservoir age',
        auto: anyOf((s) => s.reservoir_age != null),
        populated: anyOf((s) => s.reservoir_age != null),
        align: 'right',
        numeric: true,
        headTitle: 'The older, whole-reservoir form of a marine correction. ΔR supersedes it.',
        render: (s) => (s.reservoir_age == null ? muted('—') : s.reservoir_age),
      },
      {
        id: 'depth_cm',
        label: 'Depth (cm)',
        auto: anyOf((s) => s.depth_cm != null),
        populated: anyOf((s) => s.depth_cm != null),
        align: 'right',
        numeric: true,
        render: (s) => (s.depth_cm == null ? muted('—') : s.depth_cm),
      },
      {
        id: 'position',
        label: 'Position',
        auto: anyOf((s) => s.latitude != null),
        populated: anyOf((s) => s.latitude != null),
        numeric: true,
        headTitle:
          'Where the date came from, in WGS84 decimal degrees. Shown at full precision here; exports and figures follow the project’s coordinate precision setting.',
        render: (s) => formatPosition(s.latitude, s.longitude) ?? muted('—'),
      },
      {
        id: 'ring_offset',
        label: 'Gap',
        auto: anyOf((s) => s.ring_offset != null),
        populated: anyOf((s) => s.ring_offset != null),
        align: 'right',
        numeric: true,
        headTitle:
          "Where this date sits in a wiggle-matched series: years BEFORE the series reference, 0 at the reference and increasing into the past. The Model Structure page converts ring numbers into these.",
        render: (s) =>
          s.ring_offset == null
            ? muted('—')
            : s.ring_offset_error
              ? `${s.ring_offset} ± ${s.ring_offset_error}`
              : s.ring_offset,
      },
      {
        id: 'location',
        label: 'Location',
        auto: anyOf((s) => !!s.location),
        populated: anyOf((s) => !!s.location),
        render: (s) => s.location || muted('—'),
      },
      {
        id: 'notes',
        label: 'Notes',
        auto: anyOf((s) => !!s.notes),
        populated: anyOf((s) => !!s.notes),
        render: (s) =>
          s.notes ? (
            // Notes run to a paragraph. One line here, the whole of it on
            // hover, rather than a column that sets the height of every row.
            <Box
              component="span"
              title={s.notes}
              sx={{
                display: 'block',
                maxWidth: 260,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {s.notes}
            </Box>
          ) : (
            muted('—')
          ),
      },
    ]
  }, [samples, marineIds])

  const [columnOverrides, setColumnOverrides] = useState<ColumnOverrides>(() =>
    loadColumnOverrides(projectId)
  )
  useEffect(() => setColumnOverrides(loadColumnOverrides(projectId)), [projectId])
  const [columnsAnchor, setColumnsAnchor] = useState<HTMLElement | null>(null)

  const visibleColumns = optionalColumns.filter((c) => columnVisible(columnOverrides, c.id, c.auto))

  const toggleColumn = (column: OptionalColumn) => {
    setColumnOverrides((prev) => {
      const shown = columnVisible(prev, column.id, column.auto)
      const next = { ...prev }
      // Choosing what the data already does is not an override -- dropping it
      // lets the column go back to following the collection.
      if (!shown === column.auto) delete next[column.id]
      else next[column.id] = !shown
      saveColumnOverrides(projectId, next)
      return next
    })
  }

  // The same rule the measurement form and the selection bar apply, rather
  // than a second comparison against 'marine20' that could drift from the
  // registry.
  const formIsMarine = isMarineMeasurement({ curve_id: form.curve_id }, curves)
  const showReservoirFields =
    formIsMarine ||
    form.delta_r !== '' ||
    form.delta_r_error !== '' ||
    form.reservoir_age !== ''
  // The same pairing rule the measurement form applies: an offset without its
  // error is not a weaker correction, it is one the solver cannot weight.
  const deltaRErrorIncomplete = deltaRErrorMissing({
    delta_r: numOrNull(form.delta_r),
    delta_r_error: numOrNull(form.delta_r_error),
  })
  // And the same rule again for the coordinate: the API refuses half a
  // position, so the form says which box is empty rather than letting the
  // save come back a 422.
  const coordinateIncomplete = coordinateHalfMissing({
    latitude: numOrNull(form.latitude),
    longitude: numOrNull(form.longitude),
  })

  const resetColumns = () => {
    setColumnOverrides({})
    saveColumnOverrides(projectId, {})
    setColumnsAnchor(null)
  }

  const [sortKey, setSortKey] = useState<SortKey | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const sorted = useMemo(() => {
    if (!sortKey) return samples
    const factor = sortDir === 'asc' ? 1 : -1
    return [...samples].sort((a, b) => factor * compareSamples(a, b, sortKey))
  }, [samples, sortKey, sortDir])

  const sortBy = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const sortableHead = (
    key: SortKey,
    label: string,
    align: 'left' | 'right' = 'left',
    cellKey?: string
  ) => (
    <TableCell
      key={cellKey}
      align={align}
      sortDirection={sortKey === key ? sortDir : false}
      sx={{ backgroundColor: 'background.muted', whiteSpace: 'nowrap' }}
    >
      <TableSortLabel
        active={sortKey === key}
        direction={sortKey === key ? sortDir : 'asc'}
        onClick={() => sortBy(key)}
      >
        {label}
      </TableSortLabel>
    </TableCell>
  )

  // As on the analyses table: two icon buttons a row put sixty-four grey
  // glyphs on a thirty-two row collection, out-weighing the dates themselves.
  const [rowMenu, setRowMenu] = useState<{ anchor: HTMLElement; sample: ProjectSample } | null>(null)
  const menuSample = rowMenu?.sample ?? null

  // Header cells carry the band, never the row: a sticky <tr> scrolls out from
  // under its own colour.
  const headCell = { backgroundColor: 'background.muted', whiteSpace: 'nowrap' } as const

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
        <Button variant="outlined" startIcon={<AddIcon />} onClick={openAdd}>
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

        {/* Every field the collection stores can be put on screen, whether or
            not this project has filled it in -- notes, depth, location and
            reservoir age are carried by the API and by imports, and were
            unreachable from here. Pushed to the right because it changes how
            the table reads rather than what is in it. */}
        <Box sx={{ flex: 1 }} />
        <Button
          variant="outlined"
          startIcon={<ViewColumnIcon />}
          aria-haspopup="true"
          onClick={(e) => setColumnsAnchor(e.currentTarget)}
        >
          Columns
        </Button>
      </Box>

      <Menu
        anchorEl={columnsAnchor}
        open={!!columnsAnchor}
        onClose={() => setColumnsAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <Typography
          variant="caption"
          color="text.secondary"
          component="li"
          sx={{ display: 'block', px: 2, pt: 0.5, pb: 1 }}
        >
          Name, age, error and curve always show.
        </Typography>
        {optionalColumns.map((column) => {
          const shown = columnVisible(columnOverrides, column.id, column.auto)
          return (
            <MenuItem
              key={column.id}
              role="menuitemcheckbox"
              aria-checked={shown}
              // The menu stays open: choosing columns is usually choosing
              // several, and reopening between each is the errand.
              onClick={() => toggleColumn(column)}
            >
              <ListItemIcon>
                <Checkbox edge="start" size="small" checked={shown} tabIndex={-1} disableRipple />
              </ListItemIcon>
              <ListItemText
                primary={column.label}
                // Said plainly, so turning on a column this project never
                // filled in reads as empty rather than as broken.
                secondary={column.populated ? undefined : 'No values in this collection'}
              />
            </MenuItem>
          )
        })}
        <Divider />
        <MenuItem onClick={resetColumns} disabled={Object.keys(columnOverrides).length === 0}>
          <ListItemText inset>Reset to defaults</ListItemText>
        </MenuItem>
      </Menu>

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
          onPlaceOnMap={() => setPlaceOpen(true)}
          onDelete={() => setBulkDeleteOpen(true)}
        />
      )}

      <PlaceOnMapDialog
        open={placeOpen}
        onClose={() => setPlaceOpen(false)}
        // The WHOLE collection, not just the ticked rows: a pin means little
        // without the other sites around it, and seeing them is how someone
        // notices they are about to place a date in the wrong bay.
        points={samples.map((s) => ({
          id: s.id,
          name: s.sample_name,
          latitude: s.latitude,
          longitude: s.longitude,
        }))}
        selectedIds={[...selected]}
        onApply={(latitude, longitude) => handlePlaceSelection(latitude, longitude)}
        onClear={() => handlePlaceSelection(null, null)}
        tiles={mapTilesEnabled}
        busy={bulkBusy}
      />

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {samples.length === 0 ? (
        <Box sx={{ py: 6, textAlign: 'center' }}>
          <Typography variant="h6" component="p" gutterBottom>
            No dates in the collection yet
          </Typography>
          <Typography color="text.secondary" sx={{ mb: 3, maxWidth: '52ch', mx: 'auto' }}>
            Add dates here once, then deploy any selection of them into as many analyses as you like
            — each free to group them into different contexts.
          </Typography>
          {/* The one contained button on the panel, and only while the
              collection is empty: with dates in it the page's primary action
              is the masthead's, not this. */}
          <Button variant="contained" startIcon={<AddIcon />} onClick={openAdd}>
            Add Date
          </Button>
        </Box>
      ) : (
        // Sticky only where there is a container to stick inside -- a header
        // with no scrolling ancestor sticks to the viewport and slides under
        // the app bar, and a nested scroller fights the page scroll on touch.
        <TableContainer sx={{ maxHeight: { xs: 'none', md: '70vh' } }}>
          <Table size="small" stickyHeader sx={{ minWidth: 760 }}>
            <TableHead>
              <TableRow>
                <TableCell padding="checkbox" sx={headCell}>
                  <Checkbox
                    checked={allSelected}
                    indeterminate={selected.size > 0 && !allSelected}
                    onChange={() =>
                      setSelected(allSelected ? new Set() : new Set(samples.map((s) => s.id)))
                    }
                    inputProps={{ 'aria-label': 'Select all dates' }}
                  />
                </TableCell>
                {sortableHead('sample_name', 'Name')}
                {sortableHead('age_bp', 'Age BP', 'right')}
                {sortableHead('error_1sigma', '± 1σ', 'right')}
                <TableCell sx={headCell}>Curve</TableCell>
                {visibleColumns.map((column) =>
                  column.sortKey ? (
                    sortableHead(column.sortKey, column.label, column.align ?? 'left', column.id)
                  ) : (
                    <TableCell key={column.id} align={column.align ?? 'left'} sx={headCell}>
                      {column.headTitle ? (
                        <Tooltip title={column.headTitle}>
                          <span>{column.label}</span>
                        </Tooltip>
                      ) : (
                        column.label
                      )}
                    </TableCell>
                  )
                )}
                <TableCell align="right" sx={headCell} />
              </TableRow>
            </TableHead>
            <TableBody>
              {sorted.map((s) => (
                  <TableRow key={s.id} hover selected={selected.has(s.id)}>
                    <TableCell padding="checkbox">
                      <Checkbox
                        checked={selected.has(s.id)}
                        onChange={() => toggle(s.id)}
                        inputProps={{ 'aria-label': `Select ${s.sample_name}` }}
                      />
                    </TableCell>
                    <TableCell sx={{ fontWeight: 500 }}>{s.sample_name}</TableCell>
                    {/* Tabular figures so a column of ages reads as a column of
                        numbers rather than drifting with each digit's width. */}
                    <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                      {s.age_bp}
                    </TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                      {s.error_1sigma}
                    </TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>{curveLabel(curves, s.curve_id)}</TableCell>
                    {visibleColumns.map((column) => (
                      <TableCell
                        key={column.id}
                        align={column.align ?? 'left'}
                        sx={{
                          whiteSpace: column.id === 'notes' ? 'normal' : 'nowrap',
                          fontVariantNumeric: column.numeric ? 'tabular-nums' : undefined,
                        }}
                      >
                        {column.render(s)}
                      </TableCell>
                    ))}
                    <TableCell align="right" padding="checkbox">
                      <IconButton
                        size="small"
                        aria-label={`Actions for ${s.sample_name}`}
                        aria-haspopup="true"
                        onClick={(e) => setRowMenu({ anchor: e.currentTarget, sample: s })}
                      >
                        <MoreVertIcon fontSize="small" />
                      </IconButton>
                    </TableCell>
                  </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <Menu
        anchorEl={rowMenu?.anchor ?? null}
        open={!!rowMenu}
        onClose={() => setRowMenu(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <MenuItem
          onClick={() => {
            setRowMenu(null)
            if (menuSample) openEdit(menuSample)
          }}
        >
          <ListItemIcon>
            <EditIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Edit date</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem
          onClick={() => {
            setDeleteTarget(menuSample)
            setRowMenu(null)
          }}
          sx={{ color: 'error.main' }}
        >
          <ListItemIcon>
            <DeleteIcon fontSize="small" sx={{ color: 'inherit' }} />
          </ListItemIcon>
          <ListItemText>Remove from collection</ListItemText>
        </MenuItem>
      </Menu>

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

            {/* Only where it means something -- a reservoir offset on a
                terrestrial date is not merely unused, it would move the result
                if it were ever applied. Kept on screen regardless when the row
                already carries a value, so switching a date to a terrestrial
                curve cannot strand one out of reach.

                ΔR leads and the reservoir age follows it, in that order,
                because ΔR is what supersedes the other. Labels and wording are
                the measurement form's own, so the two places a ΔR can be typed
                cannot describe it differently. */}
            {showReservoirFields && (
              <>
                <TextField
                  label="ΔR (yr)"
                  type="number"
                  value={form.delta_r}
                  inputProps={{ min: -5000, max: 5000 }}
                  onChange={(e) => setForm({ ...form, delta_r: e.target.value })}
                />
                <TextField
                  label="ΔR 1σ error"
                  type="number"
                  value={form.delta_r_error}
                  inputProps={{ min: 0, max: 5000 }}
                  error={deltaRErrorIncomplete}
                  helperText={deltaRErrorIncomplete ? 'Required with ΔR' : undefined}
                  onChange={(e) => setForm({ ...form, delta_r_error: e.target.value })}
                />
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ gridColumn: '1 / -1', mt: -1 }}
                >
                  Local marine reservoir offset from the global average the curve already models —
                  not a whole reservoir age. Leave blank to apply no correction.
                </Typography>
                <TextField
                  label="Reservoir age (years)"
                  type="number"
                  value={form.reservoir_age}
                  inputProps={{ min: -500, max: 2000 }}
                  helperText="The older, whole-reservoir form. ΔR above supersedes it."
                  onChange={(e) => setForm({ ...form, reservoir_age: e.target.value })}
                  sx={{ gridColumn: '1 / -1' }}
                />
              </>
            )}

            <Typography
              variant="subtitle2"
              color="text.secondary"
              sx={{ gridColumn: '1 / -1', mt: 1 }}
            >
              Provenance and notes
            </Typography>
            <TextField
              label="Location"
              value={form.location}
              inputProps={{ maxLength: 500 }}
              onChange={(e) => setForm({ ...form, location: e.target.value })}
            />
            <TextField
              label="Latitude"
              type="number"
              value={form.latitude}
              inputProps={{ min: -90, max: 90, step: 'any' }}
              onChange={(e) => setForm({ ...form, latitude: e.target.value })}
              error={coordinateIncomplete && form.latitude.trim() === ''}
              helperText="Decimal degrees, WGS84. Minus for south."
            />
            <TextField
              label="Longitude"
              type="number"
              value={form.longitude}
              inputProps={{ min: -180, max: 180, step: 'any' }}
              onChange={(e) => setForm({ ...form, longitude: e.target.value })}
              error={coordinateIncomplete && form.longitude.trim() === ''}
              helperText={
                coordinateIncomplete
                  ? 'A position needs both halves'
                  : 'Minus for west. Degrees and minutes are not read.'
              }
            />
            <TextField
              label="Depth (cm)"
              type="number"
              value={form.depth_cm}
              inputProps={{ min: 0, max: 10000 }}
              onChange={(e) => setForm({ ...form, depth_cm: e.target.value })}
              helperText="The axis an age-depth model runs along."
            />
            <TextField
              label="Gap"
              type="number"
              value={form.ring_offset}
              inputProps={{ min: -50000, max: 50000 }}
              onChange={(e) => setForm({ ...form, ring_offset: e.target.value })}
              helperText="Years before the series reference, for a wiggle match: 0 at the reference, increasing into the past."
            />
            <TextField
              label="Gap ±"
              type="number"
              value={form.ring_offset_error}
              inputProps={{ min: 0, max: 10000 }}
              onChange={(e) => setForm({ ...form, ring_offset_error: e.target.value })}
              helperText="Only where the gap was measured rather than counted."
            />
            <TextField
              label="Notes"
              multiline
              minRows={2}
              value={form.notes}
              inputProps={{ maxLength: 2000 }}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              sx={{ gridColumn: '1 / -1' }}
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
