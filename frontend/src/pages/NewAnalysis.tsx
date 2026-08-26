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
import LibraryAddIcon from '@mui/icons-material/LibraryAdd'
import SortDatesButton from '@/components/SortDatesButton'
import { sortMeasurements } from '@/utils/sortMeasurements'
import PosteriorPlot from '@/components/PosteriorPlot'
import { storedHpdBands } from '@/utils/hpd'
import ImportDatesDialog, {
  type ImportOptions,
  type ImportPreview,
} from '@/components/ImportDatesDialog'
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
import { concernFor, concernTitle, type Concern } from '@/utils/diagnostics'
import { formatApiError } from '@/utils/apiError'
import { buildInputsPayload } from '@/utils/analysisPayload'
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

// A "quick test" calibration run (save = false): computed in full but never
// written to the database, so there is no analysis id to navigate to. Shown
// inline on the form instead.
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
 * A fingerprint of everything a run consumes -- the dates and the model, not
 * the label on them. Comparing the one taken when an analysis loaded against
 * the current draft tells a rename apart from an edit to the inputs: the first
 * can be saved on its own, the second only takes effect through a re-run.
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
  // The whole draft lives in a shared store rather than local state, so
  // navigating to /new-analysis/model-structure and back loses nothing: local
  // useState would reset on every remount, reverting analysisType to
  // "calibration" and dropping the name, project and date format.
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
    lastRunConcerns,
    loadFromAnalysis,
    reset: resetDraft,
  } = useAnalysisDraftStore()
  // Which curve a date is on decides whether a reservoir offset applies (see
  // isMarineMeasurement). The request is shared with MeasurementFields', so
  // reading it here costs nothing.
  const { curves } = useCurves()
  // Set when reached via /analysis/:analysisId/edit: the same form, but saving
  // replaces an existing analysis's inputs rather than creating one.
  const { analysisId: editRouteId } = useParams<{ analysisId: string }>()
  const isEditing = !!editRouteId
  const [loadingExisting, setLoadingExisting] = useState(isEditing)
  const [savingDetails, setSavingDetails] = useState(false)
  // The inputs as they arrived, for telling a rename-only edit apart from one
  // that changed the dates or the model (see handleSaveDetails). Null when the
  // draft was hydrated by anything but the load effect below -- returning from
  // the Model Structure page, say -- which counts as "may have changed".
  const loadedInputsRef = useRef<string | null>(null)
  const [currentMeasurement, setCurrentMeasurement] = useState<Partial<Measurement>>({
    sample_type: 'terrestrial',
    curve_id: 'intcal20',
  })
  const [loading, setLoading] = useState(false)
  // Validation for the Add Measurement card, shown in place rather than
  // through the toast queue: it names fields on THIS card.
  const [addMeasurementError, setAddMeasurementError] = useState<string | null>(null)
  // Same reasoning, for whatever kept Run Analysis from doing anything.
  const [runError, setRunError] = useState<string | null>(null)
  // The date open in the edit dialog, a working copy so Cancel really
  // discards. Committed to the store on Save only.
  const [editing, setEditing] = useState<
    { id: string; draft: Measurement; applyToPhase: boolean } | null
  >(null)
  // The other dates the outlier setting would copy to: everything in the same
  // phase, or every other date when there is no structure yet.
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
  // A write to the project rather than to this draft, so it gets its own busy
  // flag -- the import and the Run button must not look like they are waiting
  // on it.
  const [storingDates, setStoringDates] = useState(false)

  const [projects, setProjects] = useState<ProjectOption[]>([])
  // Single calibration only: a way to try a measurement without it showing up
  // in a project or the analysis list.
  const [saveResult, setSaveResult] = useState(true)
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null)

  // The selected project's date collection (see ProjectSampleTable), so dates
  // already on file need not be retyped. Picking one copies it into this
  // draft, as the project page's "Deploy" action does.
  const [projectSamples, setProjectSamples] = useState<ProjectSample[]>([])
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerSelected, setPickerSelected] = useState<Set<string>>(new Set())

  // The Project dropdown lists EXISTING projects only, so this dialog is the
  // "type a name to create one" path, reached by the + beside it.
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

  // The assigned project's date collection, from the dropdown or ?project=<id>.
  // Cleared when unassigned rather than left stale. Named rather than inlined
  // in the effect because storing dates has to re-read it -- what is already
  // in the collection is what storeInCollection dedupes against.
  const fetchProjectSamples = async (id: string): Promise<ProjectSample[] | null> => {
    try {
      const response = await fetch(`/api/projects/${id}/samples`)
      const data = await response.json()
      if (!data.success) return null
      return data.data.items as ProjectSample[]
    } catch (err) {
      console.error('Failed to load project dates:', err)
      return null
    }
  }

  useEffect(() => {
    if (!projectId) {
      setProjectSamples([])
      return
    }
    let cancelled = false
    fetchProjectSamples(projectId).then((items) => {
      if (!cancelled && items) setProjectSamples(items)
    })
    return () => {
      cancelled = true
    }
  }, [projectId])

  // /new-analysis is reached two ways at the same bare URL: a fresh start, and
  // Stratigraphy's "Back to New Analysis", which remounts this component since
  // they are separate routes. Only the second should keep the shared draft, and
  // Stratigraphy marks that case via location.state (see backState there) --
  // anything else wipes a stale draft rather than carrying an unrelated
  // analysis's dates into this one. Editing has its own load effect below.
  useEffect(() => {
    if (editRouteId) return
    const fromModelStructure = (location.state as { fromModelStructure?: boolean } | null)
      ?.fromModelStructure
    if (!fromModelStructure) resetDraft()
    const fromQuery = searchParams.get('project')
    if (fromQuery) setProjectId(fromQuery)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Editing: pull the saved inputs into the shared draft store, so this page
  // and the Model Structure page edit them through the same UI that created
  // them. GET /api/analysis/:id returns everything the draft needs. Skipped
  // when the store already holds this analysis -- returning from the Model
  // Structure page -- which would otherwise discard edits made there.
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
          // Carried through: the form does not surface these, but a date
          // deployed from a collection arrives with them set and the edit
          // resubmits whatever the draft holds.
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
        // group_id is the fallback because a migrated tree reuses each
        // context's id as its phase id, and those analyses carry it on
        // group_id. Every date must resolve to a phase or the run is rejected.
        // What the run this edit is reopening said about each date. Same
        // rule as the results table, from utils/diagnostics, so the dates
        // chipped here are exactly the dates chipped there.
        const loadedConcerns: Record<string, Concern> = {}
        for (const r of a.results || []) {
          const measurement = loadedMeasurements.find((m) => m.id === r.sample_id)
          const concern = concernFor(r, measurement?.outlier_prior)
          if (concern) loadedConcerns[r.sample_id] = concern
        }
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
          lastRunConcerns: loadedConcerns,
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
        // id, so it must be read back: otherwise saving an edit writes the
        // default over whatever the analysis was set to.
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

  // Summary for the Model Structure card. For a tree-authored model the
  // counts and validity come from the tree, as the diagram's do, rather than
  // from the groups/relationships arrays, which stay empty for such a draft.
  //
  // summariseTree counts what the MODEL asserts -- one per adjacent pair of
  // children in each Sequence -- not the cross-product edges, which would
  // report 175 for a model asserting 39.
  const structureSummary = modelTree ? summariseTree(modelTree) : null
  const structurePhaseCount = structureSummary ? structureSummary.phaseCount : groups.length
  const structureRelationshipCount = structureSummary
    ? structureSummary.orderingCount
    : relationships.length
  const structureDefined = modelTree ? true : groups.length > 0 || relationships.length > 0
  const structureInvalid = modelTree ? treeProblems.length > 0 : !!cycle

  // The reservoir offset applies only to marine carbon against a marine
  // curve, so that is the only time it is asked for. It is useless without its
  // uncertainty, the two combining in quadrature, and the backend rejects one
  // without the other -- so catch it here rather than on submit.
  const isMarine = isMarineMeasurement(currentMeasurement, curves)
  const missingDeltaRError = deltaRErrorMissing(currentMeasurement)

  // Matched by value: no id links them, a deployed date being an independent
  // copy (see ProjectSample). Enough to grey out dates already pulled in
  // without tracking provenance.
  const measurementKey = (m: { sample_name: string; age_bp: number; error_1sigma: number }) =>
    `${m.sample_name}|${m.age_bp}|${m.error_1sigma}`
  const addedProjectSampleKeys = new Set(measurements.map(measurementKey))
  // What "add all" adds: everything not already on the form. A collection is
  // usually deployed wholesale and then whittled down.
  const addableProjectSamples = projectSamples.filter(
    (s) => !addedProjectSampleKeys.has(measurementKey(s))
  )
  const allAddableSelected =
    addableProjectSamples.length > 0 && addableProjectSamples.every((s) => pickerSelected.has(s.id))

  function projectSampleToMeasurement(s: ProjectSample): Measurement {
    return {
      id: Math.random().toString(36).substr(2, 9),
      sample_name: s.sample_name,
      age_bp: s.age_bp,
      error_1sigma: s.error_1sigma,
      material_type: s.material_type || 'unknown',
      sample_type: s.sample_type,
      curve_id: s.curve_id || 'intcal20',
      // Carried rather than blanked: a reservoir offset belongs to where the
      // sample came from, so dropping it recalibrates a marine date without
      // its local correction -- which moves the answer, not just a label.
      delta_r: s.delta_r ?? null,
      delta_r_error: s.delta_r_error ?? null,
      outlier_prior: 0,
      outlier_model: 'general',
      notes: s.notes ?? null,
      // The context the collection files this date under. Deploying turns
      // these into the analysis's contexts, and adding one at a time means the
      // same thing -- so it is carried, and bootstrapModelTree starts from it
      // rather than dropping every date into one phase.
      default_context_name: s.default_context_name ?? null,
      tpq_age_bp: s.tpq_age_bp ?? null,
      taq_age_bp: s.taq_age_bp ?? null,
    }
  }

  /**
   * Copy dates from this draft into the assigned project's date collection.
   *
   * The draft and the collection are separate stores on purpose -- an analysis
   * keeps its own snapshot of what it ran on (see ProjectSample's docstring on
   * the backend) -- which is why loading a file into a New Analysis leaves the
   * project's collection untouched unless this is asked for.
   *
   * Skips names the collection already holds rather than adding a second row
   * for them: the endpoint does not deduplicate, and importing the same file
   * twice, or storing dates that were picked FROM the collection, would
   * otherwise quietly double them up. Returns what it did so the caller can
   * say so.
   */
  const storeDatesInCollection = async (
    dates: Measurement[]
  ): Promise<{ added: number; skipped: number } | null> => {
    if (!projectId) return null
    const existing = (await fetchProjectSamples(projectId)) ?? projectSamples
    const taken = new Set(existing.map((s) => s.sample_name.trim().toLowerCase()))
    const fresh = dates.filter((d) => !taken.has(d.sample_name.trim().toLowerCase()))
    const skipped = dates.length - fresh.length
    if (fresh.length === 0) return { added: 0, skipped }

    try {
      const response = await fetch(`/api/projects/${projectId}/samples`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          // The collection holds the sample, not the modelling decisions made
          // about it: outlier priors and phase assignments belong to an
          // analysis, while the context name is the collection's own field.
          samples: fresh.map((d) => ({
            sample_name: d.sample_name,
            age_bp: d.age_bp,
            error_1sigma: d.error_1sigma,
            material_type: d.material_type || 'unknown',
            curve_id: d.curve_id,
            sample_type: d.sample_type,
            delta_r: d.delta_r ?? null,
            delta_r_error: d.delta_r_error ?? null,
            default_context_name: d.default_context_name ?? null,
            tpq_age_bp: d.tpq_age_bp ?? null,
            taq_age_bp: d.taq_age_bp ?? null,
            depth_cm: d.depth_cm ?? null,
            location: d.location ?? null,
            notes: d.notes ?? null,
          })),
        }),
      })
      const data = await response.json()
      if (!data.success) {
        notify('Dates were not added to the collection: ' + formatApiError(data))
        return null
      }
      const refreshed = await fetchProjectSamples(projectId)
      if (refreshed) setProjectSamples(refreshed)
      return { added: data.data.items.length, skipped }
    } catch (err) {
      notify('Error adding dates to the collection: ' + (err as Error).message)
      return null
    }
  }

  /** How storeDatesInCollection's outcome reads in a notification. */
  const describeStored = (result: { added: number; skipped: number } | null): string => {
    if (!result) return ''
    const projectName = projects.find((p) => p.id === projectId)?.name ?? 'the project'
    if (result.added === 0) {
      return ` All ${result.skipped === 1 ? 'of it was' : 'of them were'} already in ${projectName}'s collection.`
    }
    const skipped = result.skipped
      ? ` ${result.skipped} ${result.skipped === 1 ? 'was' : 'were'} already there.`
      : ''
    return ` Stored ${result.added} in ${projectName}'s date collection.${skipped}`
  }

  // What storing would actually add, by the same name match storeDatesInCollection
  // uses -- so the button can say how many rather than offering a no-op.
  const collectionNames = new Set(
    projectSamples.map((s) => s.sample_name.trim().toLowerCase())
  )
  const unstoredMeasurements = measurements.filter(
    (m) => !collectionNames.has(m.sample_name.trim().toLowerCase())
  )

  /**
   * Load an uploaded file's dates, and its model structure when it carried
   * one, into this draft.
   *
   * REPLACES the draft's measurements rather than appending. An .oxcal file's
   * structure refers to its own dates by id, so merging would leave the dates
   * already on the form in no phase and the model refusing to run. Replacing
   * is also what the confirm button says it does.
   */
  const handleImport = async (preview: ImportPreview, options: ImportOptions) => {
    const imported: Measurement[] = preview.dates.map((d) => ({
      id: d.id,
      sample_name: d.sample_name,
      age_bp: d.age_bp,
      error_1sigma: d.error_1sigma,
      material_type: d.material_type || 'unknown',
      // A CSV may leave this to the curve, as the backend does for a
      // hand-entered date that omits it.
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
      // A CSV names a phase per date without carrying a tree to put it in,
      // so it is kept as the date's context and the Model Structure page
      // starts from that grouping. Ignored when the file DID bring a tree,
      // where the assignments above are the real thing.
      default_context_name: d.phase_name ?? null,
    }))
    setMeasurements(imported)
    setModelTree(preview.model_tree, preview.element_assignments)
    setAnalysisType(preview.suggested_analysis_type)
    if (!analysisName && preview.analysis_name) setAnalysisName(preview.analysis_name)
    setImportOpen(false)

    // After the draft is populated, so a failure to reach the collection
    // leaves the dates loaded rather than losing the import with it.
    let stored: { added: number; skipped: number } | null = null
    if (options.storeInCollection && projectId) {
      setStoringDates(true)
      stored = await storeDatesInCollection(imported)
      setStoringDates(false)
    }

    const structure = preview.model_tree
      ? ' Its model structure came with it — check it under Model Structure before running.'
      : ' The file carried no model structure, so these will be calibrated independently.'
    // Explicit severity: notify() defaults to 'error', which would report a
    // successful import in red.
    notify(
      `Loaded ${preview.dates.length} date${preview.dates.length === 1 ? '' : 's'}.${structure}` +
        describeStored(stored),
      'success'
    )
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
      // Bayesian only: a single calibration has no model for the date to be
      // an outlier from.
      outlier_prior:
        analysisType === 'bayesian' ? currentMeasurement.outlier_prior ?? 0 : 0,
      outlier_model: currentMeasurement.outlier_model ?? 'general',
      reservoir_age: currentMeasurement.reservoir_age,
    }

    addMeasurement(measurement)
    setCurrentMeasurement({ sample_type: 'terrestrial', curve_id: 'intcal20' })
  }

  /**
 * Save the analysis's name and project without re-running it. Nothing here
   * touches the dates or the model, so the stored results stay as valid as
   * they were -- where going through Run Analysis would recompute them.
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

      // Anything else the form holds is an input change, which reaches the
      // backend only through Run Analysis -- so leaving now would throw it
      // away. Stay put and say why. With the inputs untouched this really was
      // a rename, and there is nothing left to lose.
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

  /**
   * Why the run must not go ahead, or null. Only a Bayesian analysis has a
   * structure to be wrong about.
   */
  const structureProblem = (): string | null => {
    if (analysisType !== 'bayesian') return null
    // An invalid structure would be rejected with a 422 after submitting, so
    // stop here and point back at the editor. The detail is in the persistent
    // "Model structure problems" banner below (see structureInvalid); this
    // toast only says why clicking Run did nothing.
    if (modelTree && treeProblems.length > 0) {
      return 'Cannot run: the model structure has problems shown below. Fix them on the Model Structure page first.'
    }
    // The backend rejects a circular relationship set with a 422 anyway;
    // checking here avoids sending a request known to be invalid. Only the
    // relationship path needs it -- a model tree cannot contain a cycle.
    if (!modelTree && cycle) {
      return 'Cannot run: the stratigraphic relationships are circular. Fix this on the Model Structure page first.'
    }
    return null
  }

  // Stratigraphy is authored as a model tree (Stratigraphy.tsx's Editor tab)
  // and submitted as-is. groups/relationships are still sent for a draft with
  // no tree yet, which the backend converts into one. See
  // utils/analysisPayload.ts for what a non-Bayesian type has to clear.
  const inputsPayload = () =>
    buildInputsPayload({
      name: analysisName,
      analysisType,
      measurements,
      groups,
      relationships,
      modelTree,
      elementAssignments,
      projectId,
      mcmc,
    })

  const handleRunAnalysis = async () => {
    if (!analysisName || measurements.length === 0) {
      setRunError('Please provide analysis name and at least one measurement.')
      return
    }

    setRunError(null)
    setLoading(true)
    setPreviewResult(null)
    try {
      const blocked = structureProblem()
      if (blocked) {
        notify(blocked)
        setLoading(false)
        return
      }

      // Editing replaces THIS analysis's inputs whatever type it is being
      // changed to, so switching a saved analysis to another type CONVERTS it
      // rather than leaving the original behind as a pending duplicate.
      if (isEditing) {
        const response = await fetch(`/api/analysis/${editRouteId}/inputs`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(inputsPayload()),
        })
        const data = await response.json()
        if (!data.success) {
          setRunError('Analysis failed: ' + formatApiError(data))
          return
        }
        // Several dates with no model between them store as a group
        // calibration however the form labelled them, so say so rather than
        // let the results page contradict what was just chosen.
        if (data.data.analysis_type && data.data.analysis_type !== analysisType) {
          notify(
            `Saved as a group calibration: a single calibration is one date, and this has ${measurements.length}.`
          )
        }
        const runResponse = await fetch(`/api/analysis/${editRouteId}/rerun`, { method: 'POST' })
        const runData = await runResponse.json()
        if (!runData.success) {
          setRunError('Inputs saved, but the run failed: ' + formatApiError(runData))
          setLoading(false)
          return
        }
        saveDateFormatPreference(editRouteId!, dateFormat)
        resetDraft()
        navigate(`/results/${editRouteId}`)
      } else if (analysisType === 'calibration') {
        // Single calibration sends just the first measurement, but ALL of it:
        // age/error/curve alone would drop the name, the material, and the
        // reservoir offset, which changes the date rather than labelling it.
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
            // Quick test run: nothing was saved, so show the result here
            // rather than navigating to a Results page that does not exist.
            setPreviewResult(data.data)
          }
        } else {
          setRunError('Analysis failed: ' + formatApiError(data))
        }
      } else if (analysisType === 'group_calibration') {
        // Every measurement calibrated independently: no stratigraphy, no
        // model. Sent as the same measurement objects the Bayesian draft
        // builds (see GroupCalibrationRequest), so the group_id and outlier
        // fields ride along unused.
        //
        // Creating only: an edit went through PUT /inputs above.
        const response = await fetch('/api/analysis/calibrate-group', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: analysisName,
            measurements,
            project_id: projectId || null,
            // The one model setting that means anything without a model: how
            // far apart these dates are and whether they can be told apart.
            // There are no phases here to have durations.
            estimate_durations: mcmc.estimate_durations,
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
        // /analysis/bayesian validates, persists and runs in one go. The full
        // stratigraphy from the Model Structure page goes with it: contexts,
        // relationships, and any TPQ/TAQ limiters set there or inline.
        const response = await fetch('/api/analysis/bayesian', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(inputsPayload()),
        })
        const data = await response.json()
        if (data.success) {
          saveDateFormatPreference(data.data.id, dateFormat)
          resetDraft()
          navigate(`/results/${data.data.id}`)
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
      <Typography variant="h1" component="h1" gutterBottom>
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

            {/* No Tune or Chains control beside Draws: neither means anything
                to either solver -- a chain-shaped model is integrated exactly
                with no run at all, and anything else goes to a Gibbs sampler
                that extends its own run until it converges. They are still
                accepted in the payload so saved analyses round-trip. */}
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
                <FormControlLabel
                  sx={{ mt: 0.5, display: 'flex' }}
                  control={
                    <Switch
                      checked={mcmc.estimate_durations}
                      onChange={(e) => setMcmc({ estimate_durations: e.target.checked })}
                    />
                  }
                  label={<span>Durations and differences</span>}
                />
                <FormHelperText sx={{ mt: 0, ml: 0 }}>
                  How long each sequence, phase and set of dated events lasted, and how
                  far apart the phases sit. Off by default: a phase&rsquo;s duration
                  assumes its dates sample a bounded episode.
                </FormHelperText>
              </Box>
            )}

            {/* A group calibration has no phases, so nothing here has a
                duration -- but the dates can still be compared with each
                other, which is the half of this option that applies. */}
            {analysisType === 'group_calibration' && (
              <Box sx={{ mb: 2 }}>
                <Typography variant="subtitle2" gutterBottom>
                  Reporting
                </Typography>
                <FormControlLabel
                  control={
                    <Switch
                      checked={mcmc.estimate_durations}
                      onChange={(e) => setMcmc({ estimate_durations: e.target.checked })}
                    />
                  }
                  label={<span>Differences between dates</span>}
                />
                <FormHelperText sx={{ mt: 0, ml: 0 }}>
                  How many years apart each pair of dates is, and how confidently they
                  can be put in order.
                </FormHelperText>
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
          {/* The reason, in full, at the point of the edit: the chip on the
              card is a marker, and a reader who has opened the date is past
              needing a marker and into needing the numbers. */}
          {editing && lastRunConcerns[editing.id] && (
            <Alert
              severity={lastRunConcerns[editing.id].level === 'suspect' ? 'error' : 'warning'}
              sx={{ mb: 2 }}
            >
              {concernTitle(lastRunConcerns[editing.id])} From the last run — re-running the
              analysis works it out again from the edited date.
            </Alert>
          )}
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
                // switching back to terrestrial clears it rather than leaving
                // it applied invisibly.
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
        collectionProjectName={
          projectId ? projects.find((p) => p.id === projectId)?.name ?? 'this project' : null
        }
        onConfirm={handleImport}
        confirming={storingDates}
      />

      <Dialog open={pickerOpen} onClose={() => setPickerOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>
          Add dates from {projects.find((p) => p.id === projectId)?.name ?? 'this project'}'s collection
        </DialogTitle>
        <DialogContent dividers sx={{ maxHeight: 420, p: 0 }}>
          <Box
            sx={{
              position: 'sticky',
              top: 0,
              zIndex: 1,
              backgroundColor: 'background.paper',
              borderBottom: '1px solid',
              borderColor: 'divider',
              px: 2,
              py: 0.5,
            }}
          >
            <FormControlLabel
              sx={{ mx: 0 }}
              control={
                <Checkbox
                  checked={allAddableSelected}
                  indeterminate={!allAddableSelected && pickerSelected.size > 0}
                  disabled={addableProjectSamples.length === 0}
                  onChange={() =>
                    setPickerSelected(
                      allAddableSelected ? new Set() : new Set(addableProjectSamples.map((s) => s.id))
                    )
                  }
                />
              }
              label={
                <Typography variant="body2">
                  {addableProjectSamples.length === 0
                    ? 'Every date in this collection is already in this analysis'
                    : allAddableSelected
                      ? 'Clear selection'
                      : `Select all ${addableProjectSamples.length} remaining date${
                          addableProjectSamples.length === 1 ? '' : 's'
                        }`}
                </Typography>
              }
            />
          </Box>
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
                    {s.default_context_name && (
                      <Typography variant="caption" color="textSecondary" sx={{ display: 'block' }}>
                        Context: {s.default_context_name}
                      </Typography>
                    )}
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
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={1}
          alignItems={{ sm: 'center' }}
          justifyContent="space-between"
          sx={{ mb: 1 }}
        >
          <Typography variant="h5">Measurements ({measurements.length})</Typography>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
            {/* Ordering the dates here orders them everywhere downstream --
                inside each phase of the model, in the results table, and in the
                exports -- because all of those read this one array. See
                utils/sortMeasurements. */}
            <SortDatesButton
              measurements={measurements}
              onSort={(key) => setMeasurements(sortMeasurements(measurements, key))}
            />
            {/* The same write the import dialog offers, reachable after the
                fact: dates typed in here, or imported before a project was
                picked, are otherwise stuck in this draft with no way to reach
                the collection. */}
            {projectId && measurements.length > 0 && (
              <Button
                variant="outlined"
                size="small"
                startIcon={<LibraryAddIcon />}
                disabled={storingDates || unstoredMeasurements.length === 0}
                onClick={async () => {
                  setStoringDates(true)
                  const stored = await storeDatesInCollection(measurements)
                  setStoringDates(false)
                  if (stored) notify(describeStored(stored).trim(), 'success')
                }}
              >
                {storingDates
                  ? 'Storing…'
                  : unstoredMeasurements.length === 0
                    ? 'All dates are in the collection'
                    : `Store ${unstoredMeasurements.length} date${
                        unstoredMeasurements.length === 1 ? '' : 's'
                      } in the project collection`}
              </Button>
            )}
          </Stack>
        </Stack>
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
                        {/* The results table's own verdict, carried onto the
                            card the reader was sent here to edit. Arriving
                            from "Edit analysis" they know a date was flagged
                            and not which one, and the table naming it is a
                            page behind them. Same rule and same words as the
                            table, from utils/diagnostics.

                            Left as it was found once the date is changed,
                            with the tooltip saying which run it came from: a
                            note that vanished on the first keystroke would
                            disappear exactly when it was being acted on. */}
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Typography variant="h6">{m.sample_name}</Typography>
                          {lastRunConcerns[m.id] && (
                            <Tooltip
                              title={`${concernTitle(lastRunConcerns[m.id])} From the last run of this analysis.`}
                              describeChild
                            >
                              <Chip
                                size="small"
                                tabIndex={0}
                                color={
                                  lastRunConcerns[m.id].level === 'suspect' ? 'error' : 'warning'
                                }
                                variant={
                                  lastRunConcerns[m.id].level === 'suspect' ? 'filled' : 'outlined'
                                }
                                label={
                                  lastRunConcerns[m.id].level === 'suspect' ? 'Suspect' : 'Check'
                                }
                              />
                            </Tooltip>
                          )}
                        </Stack>
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
                        {/* The context the date arrived filed under. Shown
                            so it is visible that it survived the trip from
                            the collection -- it is what the Model Structure
                            page builds its starting phases from, and a
                            grouping that silently vanished used to have to be
                            re-created from memory. */}
                        {m.default_context_name && (
                          <Typography variant="body2" color="textSecondary">
                            Context: {m.default_context_name}
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
            bands={storedHpdBands(
              previewResult.hpd_68_ranges,
              previewResult.hpd_95_ranges,
              [previewResult.hpd_68.min, previewResult.hpd_68.max],
              [previewResult.hpd_95.min, previewResult.hpd_95.max]
            )}
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
