import { useState, useEffect, useRef } from 'react'
import {
  Container,
  Box,
  Paper,
  Typography,
  TextField,
  Button,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Card,
  CardContent,
  Grid,
  IconButton,
  FormControlLabel,
  FormHelperText,
  Switch,
  Alert,
  CircularProgress,
  Checkbox,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Chip,
  Stack,
  Tooltip,
} from '@mui/material'
import { useNavigate, useSearchParams, useParams, useLocation } from 'react-router-dom'
import DeleteIcon from '@mui/icons-material/Delete'
import EditIcon from '@mui/icons-material/Edit'
import AddIcon from '@mui/icons-material/Add'
import AccountTreeIcon from '@mui/icons-material/AccountTree'
import UploadFileIcon from '@mui/icons-material/UploadFile'
import PosteriorPlot from '@/components/PosteriorPlot'
import ImportDatesDialog, { type ImportPreview } from '@/components/ImportDatesDialog'
import MeasurementFields, {
  deltaRErrorMissing,
  isMarineMeasurement,
} from '@/components/MeasurementFields'
import { useCurves } from '@/hooks/useCurves'
import type { ProjectSample } from '@/components/ProjectSampleTable'
import {
  DateFormat,
  DATE_FORMAT_OPTIONS,
  loadDateFormatPreference,
  saveDateFormatPreference,
} from '@/utils/dateFormat'
import { formatApiError } from '@/utils/apiError'
import { useNotify } from '@/contexts/NotificationContext'
import { useAnalysisDraftStore, DEFAULT_MCMC } from '@/stores/analysisStore'
import type { McmcConfig, SolverChoice } from '@/stores/analysisStore'
import type { DraftGroup, DraftMeasurement, DraftRelationship, ModelElement } from '@/types'
import { expandRelationships, findCycle } from '@/utils/stratigraphy'
import { summariseTree } from '@/utils/harrisLayout'
import { validateTree } from '@/components/ModelTreeEditor'

type Measurement = DraftMeasurement

/**
 * Name of a model-tree element by id, for labelling the "apply to the rest
 * of this phase" action. Returns null when the tree has no such element --
 * an assignment can outlive the phase it pointed at if the structure was
 * edited afterwards.
 */
function elementName(tree: ModelElement | null, id: string | null): string | null {
  if (!tree || !id) return null
  const stack: ModelElement[] = [tree]
  while (stack.length) {
    const el = stack.pop()!
    if (el.id === id) return el.name || el.element_type
    for (const child of el.children || []) stack.push(child)
  }
  return null
}

interface ProjectOption {
  id: string
  name: string
}

// Result of a "quick test" calibration run (save = false): computed and
// returned in full by the backend, but never written to the database, so
// there's no analysis id to navigate to a Results page for -- it's shown
// inline instead, right here on the form.
interface PreviewResult {
  calibrated_age_bp: number
  posterior_mean: number
  posterior_median: number
  posterior_std: number
  hpd_68: { min: number; max: number }
  hpd_95: { min: number; max: number }
  hpd_68_ranges?: Array<{ min: number; max: number; probability_percent: number }>
  hpd_95_ranges?: Array<{ min: number; max: number; probability_percent: number }>
  probability_distribution: Array<[number, number]>
}

/**
 * A comparable fingerprint of everything a run actually consumes: the dates
 * and the model, but not the label on them. Comparing the fingerprint taken
 * when an analysis was loaded against the current draft is what tells a
 * rename apart from an edit to the inputs -- the first can be saved on its
 * own, the second only takes effect through a re-run.
 */
function inputsSignature(parts: {
  analysisType: string
  measurements: Measurement[]
  groups: DraftGroup[]
  relationships: DraftRelationship[]
  modelTree: ModelElement | null
  elementAssignments: Record<string, string>
  mcmc: McmcConfig
}): string {
  return JSON.stringify(parts)
}

export default function NewAnalysis() {
  const navigate = useNavigate()
  const location = useLocation()
  const notify = useNotify()
  const [searchParams] = useSearchParams()
  // Name, type, project, date format, measurements, and (for Bayesian
  // analyses) groups/relationships/TPQ-TAQ defined on the Model Structure
  // page all live in a shared store rather than local state, so navigating
  // to /new-analysis/model-structure and back doesn't lose anything that
  // was already entered -- local useState here would reset to its initial
  // value on every remount, silently reverting analysisType to
  // "calibration" and losing whatever name, project, or date format had
  // been chosen.
  const {
    name: analysisName,
    setName: setAnalysisName,
    analysisType,
    setAnalysisType,
    projectId,
    setProjectId,
    dateFormat,
    setDateFormat,
    measurements,
    setMeasurements,
    addMeasurement,
    removeMeasurement,
    updateMeasurement,
    setModelTree,
    groups,
    relationships,
    mcmc,
    setMcmc,
    modelTree,
    elementAssignments,
    editingAnalysisId,
    loadFromAnalysis,
    reset: resetDraft,
  } = useAnalysisDraftStore()
  // Which curve a date is on decides whether a reservoir offset applies to
  // it (see isMarineMeasurement). The request is shared with the one
  // MeasurementFields makes, so reading it here costs nothing extra.
  const { curves } = useCurves()
  // Set when this page is reached via /analysis/:analysisId/edit -- the
  // same form, but saving replaces an existing analysis's inputs (PUT
  // .../inputs then rerun) instead of creating a new one.
  const { analysisId: editRouteId } = useParams<{ analysisId: string }>()
  const isEditing = !!editRouteId
  const [loadingExisting, setLoadingExisting] = useState(isEditing)
  const [savingDetails, setSavingDetails] = useState(false)
  // The inputs as they arrived from the backend, for telling a rename-only
  // edit apart from one that changed the dates or the model (see
  // handleSaveDetails). Null whenever the draft was hydrated by something
  // other than the load effect below -- coming back from the Model
  // Structure page, say -- which counts as "may well have changed".
  const loadedInputsRef = useRef<string | null>(null)
  const [currentMeasurement, setCurrentMeasurement] = useState<Partial<Measurement>>({
    sample_type: 'terrestrial',
    curve_id: 'intcal20',
  })
  const [loading, setLoading] = useState(false)
  // Validation for the Add Measurement card specifically -- shown right
  // there rather than through the toast queue, since it's telling the user
  // which fields on THIS card need attention before the click does anything.
  const [addMeasurementError, setAddMeasurementError] = useState<string | null>(null)
  // Same reasoning, for whatever kept Run Analysis from doing anything.
  const [runError, setRunError] = useState<string | null>(null)
  // The date currently open in the edit dialog, held as a working copy so
  // Cancel really discards. Committed to the store only on Save.
  const [editing, setEditing] = useState<
    { id: string; draft: Measurement; applyToPhase: boolean } | null
  >(null)
  // The other dates the outlier setting would be copied to: everything in
  // the same phase, or -- before a model structure exists, when there are no
  // phases to speak of -- every other date.
  const editPhaseId = editing
    ? elementAssignments[editing.id] || editing.draft.group_id || null
    : null
  const editPhaseName = elementName(modelTree, editPhaseId)
  const editSiblings = editing
    ? measurements.filter(
        (m) =>
          m.id !== editing.id &&
          (editPhaseId
            ? (elementAssignments[m.id] || m.group_id || null) === editPhaseId
            : true)
      )
    : []
  const editInvalid =
    !!editing &&
    (!editing.draft.sample_name ||
      !Number.isFinite(editing.draft.age_bp) ||
      !Number.isFinite(editing.draft.error_1sigma) ||
      deltaRErrorMissing(editing.draft))

  const [importOpen, setImportOpen] = useState(false)

  const [projects, setProjects] = useState<ProjectOption[]>([])
  // Only offered for single calibration -- a quick way to try a
  // measurement without it showing up in a project or the analysis list.
  const [saveResult, setSaveResult] = useState(true)
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null)

  // The selected project's own date collection (see ProjectSampleTable),
  // offered here as an alternative to retyping dates that are already on
  // file -- picking one copies it into this draft's measurements, same as
  // the "Deploy" action on the project page itself.
  const [projectSamples, setProjectSamples] = useState<ProjectSample[]>([])
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerSelected, setPickerSelected] = useState<Set<string>>(new Set())

  // The Project field below is a fixed dropdown of *existing* projects --
  // there's nowhere in it to type a new name. This small dialog is the
  // "type a name to create one" path, reachable via the + button next to
  // the dropdown, so creating a project doesn't require leaving this page.
  const [newProjectDialogOpen, setNewProjectDialogOpen] = useState(false)
  const [newProjectName, setNewProjectName] = useState('')
  const [creatingProject, setCreatingProject] = useState(false)

  const fetchProjects = () => {
    fetch('/api/projects')
      .then((r) => r.json())
      .then((data) => {
        if (data.success) setProjects(data.data.items)
      })
      .catch((err) => console.error('Failed to load projects:', err))
  }

  useEffect(() => {
    fetchProjects()
  }, [])

  // Whichever project is currently assigned -- picked from the dropdown or
  // preselected via ?project=<id> -- its date collection becomes available
  // to add from below. Cleared when unassigned rather than left stale.
  useEffect(() => {
    if (!projectId) {
      setProjectSamples([])
      return
    }
    let cancelled = false
    fetch(`/api/projects/${projectId}/samples`)
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled && data.success) setProjectSamples(data.data.items)
      })
      .catch((err) => console.error('Failed to load project dates:', err))
    return () => {
      cancelled = true
    }
  }, [projectId])

  // /new-analysis is reached two ways that land on the exact same bare URL:
  // a genuine fresh start (nav bar, Dashboard, a project's own "New
  // Analysis" link) and Stratigraphy's "Back to New Analysis", which remounts
  // this component too since they're separate top-level routes. Only the
  // latter should keep whatever is already in the shared draft store --
  // Stratigraphy marks that case via location.state (see backState there),
  // so anything else wipes a stale draft rather than silently carrying an
  // earlier, unrelated analysis's measurements and project into this one.
  // Editing an existing analysis is unaffected: it has its own load effect
  // below, keyed on editRouteId rather than this.
  useEffect(() => {
    if (editRouteId) return
    const fromModelStructure = (location.state as { fromModelStructure?: boolean } | null)
      ?.fromModelStructure
    if (!fromModelStructure) resetDraft()
    const fromQuery = searchParams.get('project')
    if (fromQuery) setProjectId(fromQuery)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Editing an existing analysis: pull its saved inputs into the shared
  // draft store, so this page and the Model Structure page edit them with
  // exactly the same UI used to create them. GET /api/analysis/:id already
  // returns measurements/groups/relationships/bayesian_config, so nothing
  // extra is needed to reconstruct the draft. Skipped if the store is
  // already holding this analysis (e.g. returning from the Model Structure
  // page), which would otherwise discard edits made over there.
  useEffect(() => {
    if (!editRouteId || editingAnalysisId === editRouteId) {
      setLoadingExisting(false)
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const response = await fetch(`/api/analysis/${editRouteId}`)
        const data = await response.json()
        if (cancelled) return
        if (!data.success) {
          notify('Failed to load analysis: ' + formatApiError(data))
          navigate('/projects')
          return
        }
        const a = data.data
        const loadedType: 'calibration' | 'group_calibration' | 'bayesian' =
          a.analysis_type === 'bayesian' || a.analysis_type === 'group_calibration'
            ? a.analysis_type
            : 'calibration'
        const loadedMeasurements: Measurement[] = (a.measurements || []).map((m: any) => ({
          id: m.id,
          sample_name: m.sample_name,
          age_bp: m.age_bp,
          error_1sigma: m.error_1sigma,
          material_type: m.material_type || 'unknown',
          sample_type: m.sample_type || 'terrestrial',
          curve_id: m.curve_id || 'intcal20',
          // Carried straight through: the form doesn't surface these, but
          // a date deployed from a project collection arrives with them
          // set, and running the edit resubmits whatever the draft holds.
          delta_r: m.delta_r ?? null,
          delta_r_error: m.delta_r_error ?? null,
          // Loaded so editing an analysis does not silently switch the
          // outlier model off -- the edit resubmits whatever the draft
          // holds, so anything missing here is anything removed.
          outlier_prior: m.outlier_prior ?? 0,
          outlier_model: m.outlier_model ?? 'general',
          reservoir_age: m.reservoir_age ?? null,
          depth_cm: m.depth_cm ?? null,
          location: m.location ?? null,
          notes: m.notes ?? null,
          group_id: m.group_id ?? null,
          tpq_age_bp: m.tpq_age_bp ?? null,
          taq_age_bp: m.taq_age_bp ?? null,
        }))
        const loadedGroups: DraftGroup[] = (a.groups || []).map((g: any) => ({
          id: g.id,
          name: g.name,
          tpq_age_bp: g.tpq_age_bp ?? null,
          taq_age_bp: g.taq_age_bp ?? null,
        }))
        const loadedRelationships: DraftRelationship[] = (a.relationships || []).map((r: any) => ({
          id: r.id,
          from_sample_id: r.from_sample_id,
          to_sample_id: r.to_sample_id,
          from_type: r.from_type,
          to_type: r.to_type,
          relationship: r.relationship,
        }))
        // Merged here rather than left to the store, so the fingerprint
        // below is taken over the same object the store ends up holding.
        const loadedMcmc: McmcConfig = { ...DEFAULT_MCMC, ...(a.bayesian_config || {}) }
        // Carry the saved structure into the draft, so running the edit
        // resubmits the tree it was saved with. Without it the draft has
        // no tree, the submit falls back to groups/relationships, and the
        // backend rebuilds a different structure from those.
        //
        // group_id is the fallback because a tree migrated from the old
        // contexts reuses each context's id as its phase id, and those
        // analyses have the id on group_id rather than element_id. Every
        // date must resolve to a phase or the run is rejected outright.
        const loadedTree: ModelElement | null = a.model_tree || null
        const loadedAssignments: Record<string, string> = Object.fromEntries(
          (a.measurements || [])
            .map((m: any) => [m.id, m.element_id || m.group_id])
            .filter(([, elementId]: [string, string | null]) => !!elementId)
        )
        loadFromAnalysis({
          analysisId: editRouteId,
          name: a.name,
          analysisType: loadedType,
          measurements: loadedMeasurements,
          groups: loadedGroups,
          relationships: loadedRelationships,
          mcmc: loadedMcmc,
          projectId: a.project_id || '',
          modelTree: loadedTree,
          elementAssignments: loadedAssignments,
        })
        loadedInputsRef.current = inputsSignature({
          analysisType: loadedType,
          measurements: loadedMeasurements,
          groups: loadedGroups,
          relationships: loadedRelationships,
          modelTree: loadedTree,
          elementAssignments: loadedAssignments,
          mcmc: loadedMcmc,
        })
        // The reporting format is a client-side preference keyed by analysis
        // id, so it has to be read back here: otherwise saving an edit would
        // write the default over whatever format the analysis was set to.
        setDateFormat(loadDateFormatPreference(editRouteId))
      } catch (err) {
        if (!cancelled) notify('Error loading analysis: ' + (err as Error).message)
      } finally {
        if (!cancelled) setLoadingExisting(false)
      }
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editRouteId])

  const handleCreateProjectInline = async () => {
    if (!newProjectName.trim()) return
    setCreatingProject(true)
    try {
      const response = await fetch('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newProjectName.trim() }),
      })
      const data = await response.json()
      if (data.success) {
        setProjectId(data.data.id)
        setNewProjectName('')
        setNewProjectDialogOpen(false)
        fetchProjects()
      } else {
        notify('Failed to create project: ' + formatApiError(data))
      }
    } catch (error) {
      notify('Error creating project: ' + (error as Error).message)
    } finally {
      setCreatingProject(false)
    }
  }

  const { olderYounger: draftOlderYounger } = expandRelationships(measurements, groups, relationships)
  const cycle = findCycle(draftOlderYounger.map((e) => ({ from: e.from, to: e.to })))
  // Same rules the backend enforces, so an unbuildable model structure is
  // caught here rather than coming back as a 422 after a submit.
  const treeProblems = modelTree
    ? validateTree(modelTree, elementAssignments, measurements)
    : []
  const cycleLabels = (cycle || []).map((id) => {
    const m = measurements.find((mm) => mm.id === id)
    if (m) return m.sample_name
    const g = groups.find((gg) => gg.id === id)
    return g ? g.name : id
  })

  // Summary shown on the Model Structure card below: for a model authored
  // as a tree, phase/ordering counts and validity are derived from it
  // (same derivation the diagram uses) rather than from the legacy
  // groups/relationships arrays, which stay empty for a tree-authored
  // draft and only get backfilled with a phase list (never relationships)
  // once an existing analysis is loaded for editing.
  //
  // The ordering count comes from summariseTree, which counts what the MODEL
  // asserts -- one per adjacent pair of children in each Sequence. The old
  // treeToMatrix counted the cross-product edges the diagram used to draw, so
  // a model asserting 39 orderings displayed 175: the chip was quoting a
  // drawing bug back at the user.
  const structureSummary = modelTree ? summariseTree(modelTree) : null
  const structurePhaseCount = structureSummary ? structureSummary.phaseCount : groups.length
  const structureRelationshipCount = structureSummary
    ? structureSummary.orderingCount
    : relationships.length
  const structureDefined = modelTree ? true : groups.length > 0 || relationships.length > 0
  const structureInvalid = modelTree ? treeProblems.length > 0 : !!cycle

  // The reservoir offset only applies to marine carbon, and only means
  // anything against a marine curve -- so that is the only time it's asked
  // for. An offset is useless without its uncertainty, since the two are
  // combined in quadrature; the backend rejects one without the other, so
  // catch it here rather than on submit.
  const isMarine = isMarineMeasurement(currentMeasurement, curves)
  const missingDeltaRError = deltaRErrorMissing(currentMeasurement)

  // Matches a draft measurement to a project-collection date by value --
  // there's no id linking them, since a deployed/added date is deliberately
  // an independent copy (see ProjectSample's docstring). Good enough to
  // grey out dates already pulled in without needing to track provenance.
  const measurementKey = (m: { sample_name: string; age_bp: number; error_1sigma: number }) =>
    `${m.sample_name}|${m.age_bp}|${m.error_1sigma}`
  const addedProjectSampleKeys = new Set(measurements.map(measurementKey))

  function projectSampleToMeasurement(s: ProjectSample): Measurement {
    return {
      id: Math.random().toString(36).substr(2, 9),
      sample_name: s.sample_name,
      age_bp: s.age_bp,
      error_1sigma: s.error_1sigma,
      material_type: s.material_type || 'unknown',
      sample_type: s.sample_type,
      curve_id: s.curve_id || 'intcal20',
      // The collection has no ΔR fields of its own (see ProjectSampleTable),
      // so a marine date arrives without one and can be given one here.
      delta_r: null,
      delta_r_error: null,
      outlier_prior: 0,
      outlier_model: 'general',
      notes: s.notes ?? null,
      tpq_age_bp: s.tpq_age_bp ?? null,
      taq_age_bp: s.taq_age_bp ?? null,
    }
  }

  /**
   * Load an uploaded file's dates -- and, when it carried one, its model
   * structure -- into this draft.
   *
   * REPLACES the draft's measurements rather than appending to them. An
   * .oxcal file's structure refers to its own dates by id, so merging it with
   * dates already on the form would leave those dates in no phase and the
   * model refusing to run. Replacing is also what the confirm button says it
   * does, and the dialog shows the full list first.
   */
  const handleImport = (preview: ImportPreview) => {
    setMeasurements(
      preview.dates.map((d) => ({
        id: d.id,
        sample_name: d.sample_name,
        age_bp: d.age_bp,
        error_1sigma: d.error_1sigma,
        material_type: d.material_type || 'unknown',
        // A CSV may leave this to the curve, the same way the backend does
        // when a hand-entered date omits it.
        sample_type: d.sample_type || (d.curve_id === 'marine20' ? 'marine' : 'terrestrial'),
        curve_id: d.curve_id,
        delta_r: d.delta_r,
        delta_r_error: d.delta_r_error,
        outlier_prior: d.outlier_prior,
        outlier_model: d.outlier_model,
        depth_cm: d.depth_cm,
        location: d.location,
        notes: d.notes,
        tpq_age_bp: d.tpq_age_bp,
        taq_age_bp: d.taq_age_bp,
        element_id: d.element_id ?? null,
      }))
    )
    setModelTree(preview.model_tree, preview.element_assignments)
    setAnalysisType(preview.suggested_analysis_type)
    if (!analysisName && preview.analysis_name) setAnalysisName(preview.analysis_name)
    setImportOpen(false)

    const structure = preview.model_tree
      ? ' Its model structure came with it — check it under Model Structure before running.'
      : ' The file carried no model structure, so these will be calibrated independently.'
    notify(`Loaded ${preview.dates.length} date${preview.dates.length === 1 ? '' : 's'}.${structure}`)
  }

  const handleAddMeasurement = () => {
    if (
      !currentMeasurement.sample_name ||
      currentMeasurement.age_bp === undefined ||
      currentMeasurement.error_1sigma === undefined
    ) {
      setAddMeasurementError('Please fill in all required fields.')
      return
    }
    if (missingDeltaRError) {
      setAddMeasurementError(
        'A ΔR reservoir offset needs its 1σ error -- the two are combined, so an offset on its own would understate the uncertainty of the date.'
      )
      return
    }
    setAddMeasurementError(null)

    const measurement: Measurement = {
      id: Math.random().toString(36).substr(2, 9),
      sample_name: currentMeasurement.sample_name || '',
      age_bp: currentMeasurement.age_bp || 0,
      error_1sigma: currentMeasurement.error_1sigma || 0,
      material_type: currentMeasurement.material_type || 'unknown',
      sample_type: currentMeasurement.sample_type || 'terrestrial',
      curve_id: currentMeasurement.curve_id || 'intcal20',
      // Only carried for marine carbon -- see isMarine above.
      delta_r: isMarine ? currentMeasurement.delta_r ?? null : null,
      delta_r_error: isMarine ? currentMeasurement.delta_r_error ?? null : null,
      // Only meaningful for a Bayesian run; a single calibration has no
      // model for the date to be an outlier from.
      outlier_prior:
        analysisType === 'bayesian' ? currentMeasurement.outlier_prior ?? 0 : 0,
      outlier_model: currentMeasurement.outlier_model ?? 'general',
      reservoir_age: currentMeasurement.reservoir_age,
    }

    addMeasurement(measurement)
    setCurrentMeasurement({ sample_type: 'terrestrial', curve_id: 'intcal20' })
  }

  /**
   * Save the analysis's name and project without re-running it. Renaming
   * used to mean going through Run Analysis, which recomputes the results
   * and (for a calibration or group calibration) saves a second analysis
   * rather than replacing the one being edited -- a heavy, lossy price for
   * fixing a label. Nothing here touches the dates or the model, so the
   * stored results stay exactly as valid as they were.
   */
  const handleSaveDetails = async () => {
    if (!editRouteId) return
    if (!analysisName.trim()) {
      setRunError('Please provide an analysis name.')
      return
    }

    setRunError(null)
    setSavingDetails(true)
    try {
      const response = await fetch(`/api/analysis/${editRouteId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: analysisName.trim(), project_id: projectId || null }),
      })
      const data = await response.json()
      if (!data.success) {
        setRunError('Failed to save: ' + formatApiError(data))
        return
      }
      saveDateFormatPreference(editRouteId, dateFormat)

      // Anything else the form is holding is an input change, which only
      // reaches the backend through Run Analysis -- so leaving the page now
      // would quietly throw it away. Stay put and say why. When the inputs
      // are untouched this really was just a rename, and there is nothing
      // left on the page to lose.
      const inputsChanged =
        loadedInputsRef.current === null ||
        loadedInputsRef.current !==
          inputsSignature({
            analysisType,
            measurements,
            groups,
            relationships,
            modelTree,
            elementAssignments,
            mcmc,
          })
      if (inputsChanged) {
        notify(
          'Name and project saved. Changes to the dates or model structure still need Run Analysis to take effect.'
        )
      } else {
        notify('Analysis saved.')
        resetDraft()
        navigate(`/results/${editRouteId}`)
      }
    } catch (error) {
      setRunError('Error saving analysis: ' + (error as Error).message)
    } finally {
      setSavingDetails(false)
    }
  }

  const handleRunAnalysis = async () => {
    if (!analysisName || measurements.length === 0) {
      setRunError('Please provide analysis name and at least one measurement.')
      return
    }

    setRunError(null)
    setLoading(true)
    setPreviewResult(null)
    try {
      if (analysisType === 'calibration') {
        // For single calibration, send just the first measurement -- but all
        // of it. Sending only age/error/curve dropped everything else the
        // form had collected: the sample's own name (so it saved as the
        // schema default), its material, and its reservoir offset, which
        // changes the date rather than just labelling it.
        const measurement = measurements[0]
        const response = await fetch('/api/analysis/calibrate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            sample_name: measurement.sample_name,
            age_bp: measurement.age_bp,
            error_1sigma: measurement.error_1sigma,
            material_type: measurement.material_type,
            sample_type: measurement.sample_type,
            curve_id: measurement.curve_id,
            delta_r: measurement.delta_r ?? null,
            delta_r_error: measurement.delta_r_error ?? null,
            project_id: saveResult ? projectId || null : null,
            save: saveResult,
          }),
        })
        const data = await response.json()
        if (data.success) {
          if (data.data.saved) {
            saveDateFormatPreference(data.data.id, dateFormat)
            resetDraft()
            navigate(`/results/${data.data.id}`)
          } else {
            // Quick test run -- nothing was saved, so show the result
            // right here instead of navigating to a Results page that
            // doesn't exist.
            setPreviewResult(data.data)
          }
        } else {
          setRunError('Analysis failed: ' + formatApiError(data))
        }
      } else if (analysisType === 'group_calibration') {
        // Standard direct calibration of every measurement, independently --
        // no stratigraphy, no model. Sent as the same measurement objects
        // the Bayesian draft builds (see GroupCalibrationRequest's
        // docstring), so group_id/outlier fields ride along unused.
        //
        // Like Bayesian, always saved -- and like 'calibration' above,
        // editing does not replace this analysis's inputs in place; it
        // always creates a new one. Fixing that is a pre-existing gap in
        // 'calibration' too, not something introduced here.
        const response = await fetch('/api/analysis/calibrate-group', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: analysisName,
            measurements,
            project_id: projectId || null,
          }),
        })
        const data = await response.json()
        if (data.success) {
          saveDateFormatPreference(data.data.id, dateFormat)
          resetDraft()
          navigate(`/results/${data.data.id}`)
        } else {
          setRunError('Analysis failed: ' + formatApiError(data))
        }
      } else {
        // For Bayesian, send the full stratigraphy defined on the
        // Model Structure page too (groups, relationships, and any TPQ/TAQ
        // limiters set there or inline on a measurement) -- previously
        // this always sent relationships: [], so the working constraint
        // logic on the backend was unreachable from the UI entirely.
        //
        // The backend rejects a circular relationship set with a 422
        // before running any MCMC, but checking here too avoids sending
        // a request that's already known to be invalid. Only relevant to
        // the legacy relationship path -- a model tree cannot contain a
        // cycle, which is one of the things authoring one directly buys.
        // An invalid model structure would only be rejected with a 422 after
        // submitting, so stop here and point back at the editor instead.
        // The detail lives in the persistent "Model structure problems: ..."
        // banner further down the page (see structureInvalid below) --
        // this toast is only the acknowledgment that clicking Run didn't
        // do anything, and why to look down there.
        if (modelTree && treeProblems.length > 0) {
          notify('Cannot run: the model structure has problems shown below. Fix them on the Model Structure page first.')
          setLoading(false)
          return
        }
        if (!modelTree && cycle) {
          notify('Cannot run: the stratigraphic relationships are circular. Fix this on the Model Structure page first.')
          setLoading(false)
          return
        }
        // Stratigraphy is authored as an explicit model tree (see
        // Stratigraphy.tsx's Editor tab), submitted as-is. The
        // groups/relationships fields are still sent for a draft that
        // predates a tree being built, in which case the backend converts
        // them into an equivalent one.
        const payload: Record<string, unknown> = {
          name: analysisName,
          measurements: modelTree
            ? measurements.map((m) => ({ ...m, element_id: elementAssignments[m.id] || null }))
            : measurements,
          groups: modelTree ? [] : groups,
          relationships: modelTree ? [] : relationships.map(({ id: _id, ...r }) => r),
          project_id: projectId || null,
          bayesian_config: mcmc,
        }
        if (modelTree) payload.model_tree = modelTree

        // Editing an existing analysis replaces its inputs and then re-runs
        // it in place, keeping the same id/URL. Creating a new one posts to
        // /analysis/bayesian, which validates, persists and runs in one go.
        const response = isEditing
          ? await fetch(`/api/analysis/${editRouteId}/inputs`, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload),
            })
          : await fetch('/api/analysis/bayesian', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload),
            })
        const data = await response.json()
        if (data.success) {
          const resultId = isEditing ? editRouteId! : data.data.id
          if (isEditing) {
            const runResponse = await fetch(`/api/analysis/${resultId}/rerun`, { method: 'POST' })
            const runData = await runResponse.json()
            if (!runData.success) {
              setRunError('Inputs saved, but the run failed: ' + formatApiError(runData))
              setLoading(false)
              return
            }
          }
          saveDateFormatPreference(resultId, dateFormat)
          resetDraft()
          navigate(`/results/${resultId}`)
        } else {
          setRunError('Analysis failed: ' + formatApiError(data))
        }
      }
    } catch (error) {
      setRunError('Error running analysis: ' + (error as Error).message)
    } finally {
      setLoading(false)
    }
  }

  if (loadingExisting) {
    return (
      <Container sx={{ py: 4, display: 'flex', justifyContent: 'center' }}>
        <CircularProgress />
      </Container>
    )
  }

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Typography variant="h3" component="h1" gutterBottom>
        {isEditing ? 'Edit Analysis' : 'New Analysis'}
      </Typography>
      {isEditing && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Editing a saved analysis. Run Analysis replaces its inputs and re-runs it, replacing its
          current results. To change only the name or the project, use Save Details at the bottom of
          the page -- it keeps the existing results.
        </Alert>
      )}

      <Grid container spacing={3}>
        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 3 }}>
            <Typography variant="h5" gutterBottom>
              Analysis Settings
            </Typography>

            <Box sx={{ mb: 2 }}>
              <TextField
                fullWidth
                label="Analysis Name"
                value={analysisName}
                onChange={(e) => setAnalysisName(e.target.value)}
                placeholder="e.g., Site XYZ - Phase 1"
              />
            </Box>

            <Box sx={{ mb: 3 }}>
              <FormControl fullWidth>
                <InputLabel>Analysis Type</InputLabel>
                <Select
                  value={analysisType}
                  label="Analysis Type"
                  onChange={(e) => setAnalysisType(e.target.value as 'calibration' | 'group_calibration' | 'bayesian')}
                >
                  <MenuItem value="calibration">Single Calibration</MenuItem>
                  <MenuItem value="group_calibration">Group Calibration</MenuItem>
                  <MenuItem value="bayesian">Bayesian (with stratigraphic constraints)</MenuItem>
                </Select>
              </FormControl>
            </Box>

            {analysisType === 'calibration' && (
              <Box sx={{ mb: 1 }}>
                <FormControlLabel
                  control={
                    <Switch
                      checked={saveResult}
                      onChange={(e) => setSaveResult(e.target.checked)}
                    />
                  }
                  label="Save this analysis"
                />
                <Typography variant="caption" color="textSecondary" sx={{ display: 'block', mb: 2 }}>
                  {saveResult
                    ? 'Saved analyses are added to a project and appear in the analysis list.'
                    : "Quick test run — results are shown below but won't be saved anywhere."}
                </Typography>
              </Box>
            )}

            <Box sx={{ mb: 3, display: 'flex', gap: 1, alignItems: 'flex-start' }}>
              <FormControl fullWidth disabled={analysisType === 'calibration' && !saveResult}>
                <InputLabel>Project</InputLabel>
                <Select
                  value={projectId}
                  label="Project"
                  onChange={(e) => setProjectId(e.target.value)}
                >
                  <MenuItem value="">No project (unassigned)</MenuItem>
                  {projects.map((p) => (
                    <MenuItem key={p.id} value={p.id}>
                      {p.name}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <IconButton
                onClick={() => setNewProjectDialogOpen(true)}
                disabled={analysisType === 'calibration' && !saveResult}
                title="Create a new project"
                sx={{ mt: 1 }}
              >
                <AddIcon />
              </IconButton>
            </Box>

            <Box sx={{ mb: 3 }}>
              <FormControl fullWidth>
                <InputLabel>Result Date Format</InputLabel>
                <Select
                  value={dateFormat}
                  label="Result Date Format"
                  onChange={(e) => setDateFormat(e.target.value as DateFormat)}
                >
                  {DATE_FORMAT_OPTIONS.map((option) => (
                    <MenuItem key={option.value} value={option.value}>
                      {option.label}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            </Box>

            {/* Tune and Chains used to sit beside Draws here, from when the
                fallback was PyMC's SMC sampler. Neither means anything now:
                a chain-shaped model is integrated exactly, with no run at
                all, and anything else goes to a Gibbs sampler that extends
                its own run until it converges rather than taking a length on
                trust. They are still accepted in the payload so saved
                analyses round-trip, but presenting spinners that change
                nothing would just be misleading. */}
            {analysisType === 'bayesian' && (
              <Box sx={{ mb: 2 }}>
                <Typography variant="subtitle2" gutterBottom>
                  Model Settings
                </Typography>
                {/* Solver override. Automatic is right almost always -- exact
                    integration has no sampler to converge and no calibration
                    lobe to miss -- but OxCal is the reference implementation, so
                    forcing its method is how you reproduce a published analysis
                    or cross-check the exact solver against an independent route
                    to the same posterior. */}
                <FormControl fullWidth size="small" sx={{ mb: 2 }}>
                  <InputLabel>Solver</InputLabel>
                  <Select
                    value={mcmc.solver}
                    label="Solver"
                    onChange={(e) => setMcmc({ solver: e.target.value as SolverChoice })}
                  >
                    <MenuItem value="auto">Automatic (recommended)</MenuItem>
                    <MenuItem value="exact">Exact solver only</MenuItem>
                    <MenuItem value="gibbs">MH/Gibbs sampler (as OxCal)</MenuItem>
                  </Select>
                  <FormHelperText>
                    {mcmc.solver === 'auto' &&
                      'Integrates the posterior exactly where the model as written allows it, and samples otherwise.'}
                    {mcmc.solver === 'exact' &&
                      'Fails rather than sampling if this model cannot be integrated exactly. Where a model needs a Boundary its ordering only implies, this supplies it — which changes the model slightly.'}
                    {mcmc.solver === 'gibbs' &&
                      'Uses OxCal’s own method throughout — for reproducing a published analysis, or cross-checking the exact solver.'}
                  </FormHelperText>
                </FormControl>
                <TextField
                  fullWidth
                  size="small"
                  type="number"
                  label="Posterior draws"
                  value={mcmc.mcmc_samples}
                  onChange={(e) => setMcmc({ mcmc_samples: Number(e.target.value) })}
                  helperText="How many samples each date's posterior is reported with."
                />
                <FormControlLabel
                  sx={{ mt: 1 }}
                  control={
                    <Switch
                      checked={mcmc.uniform_span_prior}
                      onChange={(e) => setMcmc({ uniform_span_prior: e.target.checked })}
                    />
                  }
                  label={
                    <Tooltip title="OxCal's UniformSpanPrior correction (Nicholls and Jones 2001), on by default. Only affects a sequence with three or more boundaries.">
                      <span>Uniform span prior</span>
                    </Tooltip>
                  }
                />
              </Box>
            )}
          </Paper>
        </Grid>

        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 3 }}>
            <Typography variant="h5" gutterBottom>
              Add Measurement
            </Typography>

            {addMeasurementError && (
              <Alert severity="error" sx={{ mb: 2 }} onClose={() => setAddMeasurementError(null)}>
                {addMeasurementError}
              </Alert>
            )}

            <Box sx={{ mb: 3 }}>
              {projectId && projectSamples.length > 0 && (
                <Button
                  variant="outlined"
                  fullWidth
                  sx={{ mb: 1 }}
                  onClick={() => {
                    setPickerSelected(new Set())
                    setPickerOpen(true)
                  }}
                >
                  Add from Project's Date Collection ({projectSamples.length})
                </Button>
              )}
              <Button
                variant="outlined"
                fullWidth
                startIcon={<UploadFileIcon />}
                onClick={() => setImportOpen(true)}
              >
                Import from a File
              </Button>
              <Typography variant="caption" color="textSecondary" sx={{ display: 'block', mt: 0.5 }}>
                A spreadsheet of dates (.csv), or an OxCal model (.oxcal) with its structure. Or
                fill in the fields below to add a single date.
              </Typography>
            </Box>

            <MeasurementFields
              value={currentMeasurement}
              onChange={setCurrentMeasurement}
              showOutlier={analysisType === 'bayesian'}
            />

            <Button variant="outlined" onClick={handleAddMeasurement} fullWidth>
              Add Measurement
            </Button>
          </Paper>
        </Grid>
      </Grid>

      {/* Editing a date already in the list. Uses the same fields as adding
          one, so the two cannot offer different things -- which is how the
          outlier settings came to be missing from half the app in the first
          place. */}
      <Dialog
        open={!!editing}
        onClose={() => setEditing(null)}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>Edit date</DialogTitle>
        <DialogContent dividers>
          {editing && (
            <MeasurementFields
              value={editing.draft}
              onChange={(next) =>
                setEditing((current) =>
                  current ? { ...current, draft: next as Measurement } : current
                )
              }
              showOutlier={analysisType === 'bayesian'}
            />
          )}
          {/* Setting an outlier model one date at a time is the usual case
              -- OxCal asks for it per date too -- but a phase of sixteen
              charcoal samples is one decision, not sixteen. Only the outlier
              settings travel; everything else on this form describes the
              individual sample. */}
          {analysisType === 'bayesian' && editing && editSiblings.length > 0 && (
            <FormControlLabel
              sx={{ mt: 1 }}
              control={
                <Checkbox
                  size="small"
                  checked={editing.applyToPhase}
                  onChange={(e) =>
                    setEditing((current) =>
                      current ? { ...current, applyToPhase: e.target.checked } : current
                    )
                  }
                />
              }
              label={
                <Typography variant="body2">
                  {editPhaseName
                    ? `Also apply this outlier setting to the other ${editSiblings.length} date${
                        editSiblings.length === 1 ? '' : 's'
                      } in ${editPhaseName}`
                    : `Also apply this outlier setting to the other ${editSiblings.length} date${
                        editSiblings.length === 1 ? '' : 's'
                      } (no phases assigned yet)`}
                </Typography>
              }
            />
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditing(null)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={editInvalid}
            onClick={() => {
              if (!editing) return
              const { draft } = editing
              updateMeasurement(editing.id, {
                ...draft,
                // A reservoir offset means nothing off a marine curve, so
                // switching a date back to terrestrial has to clear it
                // rather than leave it applied invisibly.
                delta_r: isMarineMeasurement(draft, curves) ? draft.delta_r ?? null : null,
                delta_r_error: isMarineMeasurement(draft, curves)
                  ? draft.delta_r_error ?? null
                  : null,
              })
              if (editing.applyToPhase) {
                for (const sibling of editSiblings) {
                  updateMeasurement(sibling.id, {
                    outlier_prior: draft.outlier_prior ?? 0,
                    outlier_model: draft.outlier_model ?? 'general',
                  })
                }
              }
              setEditing(null)
            }}
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>

      <ImportDatesDialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        target="analysis"
        onConfirm={handleImport}
      />

      <Dialog open={pickerOpen} onClose={() => setPickerOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>
          Add dates from {projects.find((p) => p.id === projectId)?.name ?? 'this project'}'s collection
        </DialogTitle>
        <DialogContent dividers sx={{ maxHeight: 420, p: 0 }}>
          {projectSamples.map((s) => {
            const alreadyAdded = addedProjectSampleKeys.has(measurementKey(s))
            return (
              <FormControlLabel
                key={s.id}
                sx={{ display: 'flex', width: '100%', mx: 0, px: 2, py: 0.5 }}
                control={
                  <Checkbox
                    checked={pickerSelected.has(s.id) || alreadyAdded}
                    disabled={alreadyAdded}
                    onChange={() =>
                      setPickerSelected((prev) => {
                        const next = new Set(prev)
                        if (next.has(s.id)) next.delete(s.id)
                        else next.add(s.id)
                        return next
                      })
                    }
                  />
                }
                label={
                  <Box>
                    <Typography variant="body2">
                      {s.sample_name} — {s.age_bp} ± {s.error_1sigma} BP ({s.sample_type}, {s.curve_id})
                    </Typography>
                    {alreadyAdded && (
                      <Typography variant="caption" color="textSecondary">
                        Already in this analysis
                      </Typography>
                    )}
                  </Box>
                }
              />
            )
          })}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPickerOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={pickerSelected.size === 0}
            onClick={() => {
              for (const s of projectSamples) {
                if (pickerSelected.has(s.id)) addMeasurement(projectSampleToMeasurement(s))
              }
              setPickerSelected(new Set())
              setPickerOpen(false)
            }}
          >
            Add {pickerSelected.size} Date{pickerSelected.size === 1 ? '' : 's'}
          </Button>
        </DialogActions>
      </Dialog>

      <Box sx={{ mt: 3 }}>
        <Typography variant="h5" gutterBottom>
          Measurements ({measurements.length})
        </Typography>
        {measurements.length === 0 ? (
          <Typography color="textSecondary">No measurements added yet</Typography>
        ) : (
          <Grid container spacing={2}>
            {measurements.map((m) => (
              <Grid item xs={12} sm={6} md={4} key={m.id}>
                <Card>
                  <CardContent>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                      <Box>
                        <Typography variant="h6">{m.sample_name}</Typography>
                        <Typography variant="body2" color="textSecondary">
                          {m.age_bp} ± {m.error_1sigma} BP
                        </Typography>
                        <Typography variant="body2" color="textSecondary">
                          {m.sample_type} ({m.curve_id})
                        </Typography>
                        {m.material_type && m.material_type !== 'unknown' && (
                          <Typography variant="body2" color="textSecondary">
                            Material: {m.material_type}
                          </Typography>
                        )}
                        {/* Shown because it changes the date -- a reader
                            should be able to see a correction was applied
                            without opening the export. */}
                        {m.delta_r != null && (
                          <Typography variant="body2" color="textSecondary">
                            ΔR: {m.delta_r} ± {m.delta_r_error ?? 0} yr
                          </Typography>
                        )}
                        {/* Shown for the same reason as ΔR: it changes what
                            the model does with the date, so it should be
                            visible without opening anything. */}
                        {(m.outlier_prior ?? 0) > 0 && (
                          <Typography variant="body2" color="textSecondary">
                            Outlier: {m.outlier_model === 'charcoal' ? 'charcoal' : 'general'}
                            {' '}({Math.round((m.outlier_prior ?? 0) * 1000) / 10}% prior)
                          </Typography>
                        )}
                      </Box>
                      <Box>
                        <Tooltip title="Edit this date">
                          <IconButton
                            size="small"
                            onClick={() => setEditing({ id: m.id, draft: { ...m }, applyToPhase: false })}
                          >
                            <EditIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        <Tooltip title={`Delete ${m.sample_name}`}>
                          <IconButton
                            size="small"
                            aria-label={`Delete ${m.sample_name}`}
                            onClick={() => removeMeasurement(m.id)}
                            sx={{ '&:hover, &:focus-visible': { color: 'error.main' } }}
                          >
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </Box>
                    </Box>
                  </CardContent>
                </Card>
              </Grid>
            ))}
          </Grid>
        )}
      </Box>

      {analysisType === 'bayesian' && (
        <Box sx={{ mt: 3 }}>
          <Paper sx={{ p: 3 }}>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ sm: 'center' }} justifyContent="space-between">
              <Box>
                <Typography variant="h5" gutterBottom>
                  Model Structure
                </Typography>
                <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
                  <Chip size="small" label={`${structurePhaseCount} phase${structurePhaseCount === 1 ? '' : 's'}`} />
                  <Chip
                    size="small"
                    label={`${structureRelationshipCount} ordering relationship${structureRelationshipCount === 1 ? '' : 's'}`}
                  />
                  {structureInvalid && <Chip size="small" color="error" label="Invalid -- fix before running" />}
                </Stack>
                <Typography variant="body2" color="textSecondary">
                  Optional: build the model as nested Sequences and Phases with Boundaries placed where
                  you want them, then assign each date to a Phase -- the Harris matrix diagram is derived
                  from this structure automatically.
                </Typography>
              </Box>
              <Button
                variant="outlined"
                startIcon={<AccountTreeIcon />}
                onClick={() =>
                  navigate(isEditing ? `/analysis/${editRouteId}/edit/model-structure` : '/new-analysis/model-structure')
                }
                disabled={measurements.length === 0}
              >
                {structureDefined ? 'Edit Model Structure' : 'Specify Model Structure'}
              </Button>
            </Stack>
            {structureInvalid && (
              <Alert severity="error" sx={{ mt: 2 }}>
                {modelTree
                  ? <>Model structure problems: {treeProblems.join('; ')}.</>
                  : <>Circular relationship involving: {cycleLabels.join(', ')}.</>}
                {' '}Open Model Structure to fix this before running the analysis.
              </Alert>
            )}
          </Paper>
        </Box>
      )}

      {runError && (
        <Alert severity="error" sx={{ mt: 3 }} onClose={() => setRunError(null)}>
          {runError}
        </Alert>
      )}

      <Box sx={{ mt: 4, display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
        <Button
          variant="contained"
          color="primary"
          size="large"
          onClick={handleRunAnalysis}
          disabled={
            loading ||
            savingDetails ||
            measurements.length === 0 ||
            (analysisType === 'bayesian' && !!cycle)
          }
        >
          {loading ? 'Running...' : 'Run Analysis'}
        </Button>
        {/* Only when there is something to save onto: a new analysis has no
            row to rename yet, so its name is settled by running it. */}
        {isEditing && (
          <Button
            variant="outlined"
            size="large"
            onClick={handleSaveDetails}
            disabled={loading || savingDetails || !analysisName.trim()}
          >
            {savingDetails ? 'Saving...' : 'Save Details'}
          </Button>
        )}
      </Box>
      {isEditing && (
        <Typography variant="body2" color="textSecondary" sx={{ mt: 1 }}>
          Save Details stores the name and project only, leaving the current results in place.
        </Typography>
      )}

      {previewResult && (
        <Box sx={{ mt: 4 }}>
          <Alert severity="info" sx={{ mb: 2 }}>
            Quick test run — not saved. Turn "Save this analysis" back on to keep a result like this.
          </Alert>
          <PosteriorPlot
            calAges={previewResult.probability_distribution.map(([age]) => age)}
            probabilities={previewResult.probability_distribution.map(([, prob]) => prob)}
            hpd68={[previewResult.hpd_68.min, previewResult.hpd_68.max]}
            hpd95={[previewResult.hpd_95.min, previewResult.hpd_95.max]}
            hpd68Ranges={previewResult.hpd_68_ranges}
            hpd95Ranges={previewResult.hpd_95_ranges}
            sampleName={analysisName}
            pointEstimates={{
              mean: previewResult.posterior_mean,
              median: previewResult.posterior_median,
              std: previewResult.posterior_std,
            }}
            dateFormat={dateFormat}
          />
        </Box>
      )}

      <Dialog open={newProjectDialogOpen} onClose={() => setNewProjectDialogOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>New Project</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            label="Project Name"
            value={newProjectName}
            onChange={(e) => setNewProjectName(e.target.value)}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setNewProjectDialogOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleCreateProjectInline}
            disabled={creatingProject || !newProjectName.trim()}
          >
            {creatingProject ? 'Creating...' : 'Create & Select'}
          </Button>
        </DialogActions>
      </Dialog>
    </Container>
  )
}
