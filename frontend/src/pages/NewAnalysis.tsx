import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
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
  Accordion,
  AccordionSummary,
  AccordionDetails,
} from '@mui/material'
import { useNavigate, useSearchParams, useParams, useLocation } from 'react-router-dom'
import AddIcon from '@mui/icons-material/Add'
import AccountTreeIcon from '@mui/icons-material/AccountTree'
import PlaceIcon from '@mui/icons-material/Place'
import PlaceOnMapDialog from '@/components/PlaceOnMapDialog'
import DraftMeasurementTable, { type DraftColumns } from '@/components/DraftMeasurementTable'
import UploadFileIcon from '@mui/icons-material/UploadFile'
import LibraryAddIcon from '@mui/icons-material/LibraryAdd'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import SortDatesButton from '@/components/SortDatesButton'
import { sortMeasurements } from '@/utils/sortMeasurements'
import { projectSampleToMeasurement } from '@/utils/projectSampleToMeasurement'
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
import { useProjectMapTiles } from '@/hooks/useProjectMapTiles'
// The registry's own name for a curve, so this table and every other curve
// label in the app say the same thing about the same id.
import type { ProjectSample } from '@/components/ProjectSampleTable'
import {
  DateFormat,
  DATE_FORMAT_OPTIONS,
  loadDateFormatPreference,
  saveDateFormatPreference,
} from '@/utils/dateFormat'
import { concernFor, concernTitle, type Concern } from '@/utils/diagnostics'
import { formatApiError, describeFetchError } from '@/utils/apiError'
import { buildInputsPayload } from '@/utils/analysisPayload'
import { useNotify } from '@/contexts/NotificationContext'
import { useAnalysisDraftStore, DEFAULT_MCMC, isUsableResolution } from '@/stores/analysisStore'
import type { McmcConfig, SolverChoice } from '@/stores/analysisStore'
import type {
  DraftGroup,
  DraftMeasurement,
  DraftRelationship,
  ElementType,
  ModelElement,
} from '@/types'
import { expandRelationships, findCycle } from '@/utils/stratigraphy'
import { ELEMENT_TYPE_LABEL, elementLabel } from '@/utils/elementLabels'
import { modelCanReportIntervals, modelCanReportKde } from '@/utils/intervalsAvailable'
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
    if (el.id === id) return elementLabel(el)
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
  // Resolution as typed. Only a usable value reaches the draft, so a
  // half-typed one never rides along in a payload -- including one for an
  // analysis type that hides the field. Re-read from the draft when that
  // changes underneath it: an import carrying OxCal's Resolution, or loading
  // a saved analysis to edit.
  const [resolutionText, setResolutionText] = useState(String(mcmc.resolution))
  const resolutionValid = isUsableResolution(resolutionText)
  useEffect(() => {
    if (Number(resolutionText) !== mcmc.resolution) setResolutionText(String(mcmc.resolution))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mcmc.resolution])
  // The assigned project's basemap setting, so the origin is placed on the
  // same map its dates were.
  const mapTiles = useProjectMapTiles(projectId || null)
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

  // /new-analysis is reached at the same bare URL by everything: the nav
  // button, a Back from the model structure page, the browser's own Back and
  // Forward, and a reload. Starting a NEW analysis is the only one of those
  // that should empty the draft, so it is the one that has to say so -- every
  // entry point that means "start over" passes state.fresh (see Navigation,
  // Dashboard and ProjectDetail), and anything else keeps what is there.
  //
  // This was previously inverted: the draft was wiped unless the arrival
  // proved it came from the model structure page. Only that page's own Back
  // buttons could prove it, so browser Back destroyed every typed date and the
  // whole authored model tree, silently and with no way to recover them.
  //
  // The one case that still resets without being asked: a draft left over from
  // EDITING a saved analysis. That draft belongs to an analysis with an id, and
  // carrying it into a new one would submit someone else's dates.
  // Keyed on location.key, not [], because "New Analysis" is often clicked
  // while already on /new-analysis: that pushes a new entry without remounting,
  // so a mount-only effect would never see it and the button would quietly do
  // nothing.
  //
  // The marker is then consumed by replacing the entry with a stateless one.
  // It has to be: a history entry that still says "fresh" resets again every
  // time the user comes Back to it, which is the exact data loss this whole
  // change removes.
  useEffect(() => {
    if (editRouteId) return
    const fresh = (location.state as { fresh?: boolean } | null)?.fresh
    if (fresh || editingAnalysisId) resetDraft()
    const fromQuery = searchParams.get('project')
    if (fromQuery) setProjectId(fromQuery)
    if (fresh) {
      navigate(`${location.pathname}${location.search}`, { replace: true, state: null })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.key])

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
          notify('Could not load analysis: ' + formatApiError(data))
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
          // The two ordinates. Loaded for the same reason as the outlier
          // settings above: an edit resubmits whatever the draft holds, so a
          // ring offset missing here is a ring offset deleted -- and a wiggle
          // match IS its ring offsets.
          ring_offset: m.ring_offset ?? null,
          ring_offset_error: m.ring_offset_error ?? null,
          // And the position, for exactly the same reason: an edit resubmits
          // the draft, so a coordinate missing here is a date unplaced by
          // being edited.
          latitude: m.latitude ?? null,
          longitude: m.longitude ?? null,
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
        if (!cancelled) notify('Could not load analysis: ' + describeFetchError(err))
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
        notify('Could not create project: ' + formatApiError(data))
      }
    } catch (error) {
      notify('Could not create project: ' + describeFetchError(error))
    } finally {
      setCreatingProject(false)
    }
  }

  // Everything derived from the draft's STRUCTURE is memoised on the store
  // values it reads. None of it changes when the name or a setting above the
  // table is typed in, and on an 800-date draft expanding every ordering
  // relationship into date-pairs, checking the result for a cycle and
  // validating the tree were being redone on every keystroke.
  const { cycle, treeProblems, cycleLabels, structureSummary } = useMemo(() => {
    const { olderYounger } = expandRelationships(measurements, groups, relationships)
    const cycle = findCycle(olderYounger.map((e) => ({ from: e.from, to: e.to })))
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
    // counts and validity come from the tree, as the diagram's do, rather
    // than from the groups/relationships arrays, which stay empty for such a
    // draft.
    //
    // summariseTree counts what the MODEL asserts -- one per adjacent pair of
    // children in each Sequence -- not the cross-product edges, which would
    // report 175 for a model asserting 39.
    const structureSummary = modelTree ? summariseTree(modelTree) : null
    return { cycle, treeProblems, cycleLabels, structureSummary }
  }, [measurements, groups, relationships, modelTree, elementAssignments])
  const structurePhaseCount = structureSummary ? structureSummary.phaseCount : groups.length
  const structureRelationshipCount = structureSummary
    ? structureSummary.orderingCount
    : relationships.length
  const structureDefined = modelTree ? true : groups.length > 0 || relationships.length > 0
  const structureInvalid = modelTree ? treeProblems.length > 0 : !!cycle

  // A wiggle match and a deposition sequence hold their dates DIRECTLY, not
  // through Phases, and neither needs a Boundary. So a model made of one
  // summarises to "0 phases, 0 ordering relationships" -- literally true, and
  // read by the analyst as "you have not built anything yet", sitting right
  // above a paragraph telling them to go and build it. Name what is actually
  // there instead.
  const seriesElements: ModelElement[] = []
  if (modelTree) {
    const collect = (element: ModelElement) => {
      if (
        element.element_type === 'wiggle_sequence'
        || element.element_type === 'deposition_sequence'
      ) {
        seriesElements.push(element)
      }
      for (const child of element.children || []) collect(child)
    }
    collect(modelTree)
  }
  const datesInElement = (elementId: string) =>
    measurements.filter((m) => (elementAssignments[m.id] || m.group_id) === elementId).length
  // Only when the series are the whole story. A deposition sequence between
  // two Boundaries is an ordinary model that happens to contain one, and the
  // usual counts describe it perfectly well.
  const seriesIsTheModel = seriesElements.length > 0 && structurePhaseCount === 0
  // A model can hold both kinds -- a timber and a core from the same site --
  // so name both rather than whichever came first in the walk.
  const seriesOrderedBy = [
    seriesElements.some((s) => s.element_type === 'wiggle_sequence') ? 'gaps' : null,
    seriesElements.some((s) => s.element_type === 'deposition_sequence') ? 'depths' : null,
  ]
    .filter(Boolean)
    .join(' and ')

  // Optional columns appear only when at least one date has something to put
  // in them. A single terrestrial calibration should not be handed a table
  // with three columns of em-dashes.
  const showMaterialColumn = measurements.some(
    (m) => m.material_type && m.material_type !== 'unknown'
  )
  const showContextColumn = measurements.some((m) => !!m.default_context_name)
  const showDeltaRColumn = measurements.some((m) => m.delta_r != null)
  const showOutlierColumn = measurements.some((m) => (m.outlier_prior ?? 0) > 0)
  // The two ORDINATES. Like ΔR and the outlier settings, they change the
  // answer rather than describing the sample -- a wiggle match IS its gaps and
  // a deposition sequence IS its depths -- so once any date carries one they
  // are readable without opening anything.
  const showGapColumn = measurements.some((m) => m.ring_offset != null)
  const showDepthColumn = measurements.some((m) => m.depth_cm != null)
  // One object, memoised on the flags, so the table's memo sees the same
  // props until a column actually appears or disappears.
  const draftColumns = useMemo<DraftColumns>(
    () => ({
      material: showMaterialColumn,
      context: showContextColumn,
      deltaR: showDeltaRColumn,
      gap: showGapColumn,
      depth: showDepthColumn,
      outlier: showOutlierColumn,
    }),
    [showMaterialColumn, showContextColumn, showDeltaRColumn, showGapColumn,
     showDepthColumn, showOutlierColumn],
  )
  // Stable, so the memoised rows keep their memo -- see DraftMeasurementTable.
  const beginEditing = useCallback(
    (m: Measurement) => setEditing({ id: m.id, draft: { ...m }, applyToPhase: false }),
    [],
  )

  /**
   * The element type a date is assigned to, or null.
   *
   * What makes a gap or a depth PERTINENT to a particular date: it either
   * already carries one, or it sits in the kind of sequence built on one. A
   * date in neither is offered neither, because a stray value would be
   * carried, exported, and eventually read as if someone meant it.
   */
  const holderTypeFor = (measurementId: string): ElementType | null => {
    const elementId = elementAssignments[measurementId]
    if (!modelTree || !elementId) return null
    let found: ElementType | null = null
    const walk = (element: ModelElement) => {
      if (element.id === elementId) found = element.element_type
      for (const child of element.children || []) walk(child)
    }
    walk(modelTree)
    return found
  }

  // Whether the model as it stands has anything for a duration to be about.
  // `null` is "no structure yet", the ordinary state on this form before one
  // is built, and is treated as yes -- hiding the option because the user has
  // not got to the Model Structure page would be worse than offering one that
  // turns out to report nothing.
  //
  // A wiggle match answers false, which is the point: the spacing of its
  // members is an INPUT rather than something the run infers, so the switch
  // would promise a table that comes back empty.
  const placedMeasurements = measurements.map((m) => ({
    id: m.id,
    element_id: elementAssignments[m.id] || null,
    group_id: m.group_id ?? null,
  }))
  const canReportIntervals = modelCanReportIntervals(modelTree, placedMeasurements) !== false
  // The same rule for a per-phase KDE: a phase with two dates to summarise.
  const canReportKde = modelCanReportKde(modelTree, placedMeasurements) !== false

  // Dates carrying a position. A dispersal fit is about where sites are, so
  // with none of them placed the switch would promise a section that comes
  // back refused -- the same reason the durations switch is gated on the model
  // having something for a duration to be about.
  const placedCount = measurements.filter(
    (m) => m.latitude !== null && m.latitude !== undefined
      && m.longitude !== null && m.longitude !== undefined
  ).length

  // The dispersal origin's pin dialog. Reuses the collection's own
  // place-on-map rather than a second map: an origin is a position, and a
  // reader who has placed a date already knows how this works.
  const [originDialogOpen, setOriginDialogOpen] = useState(false)

  const ordinatesFor = (measurementId: string) => {
    // Read from the SAVED measurement, not from the dialog's draft. Clearing
    // the field would otherwise set the value to null, make it no longer
    // pertinent, and unmount the field being typed in.
    const saved = measurements.find((m) => m.id === measurementId)
    const holder = holderTypeFor(measurementId)
    return {
      showRingOffset: saved?.ring_offset != null || holder === 'wiggle_sequence',
      showDepth: saved?.depth_cm != null || holder === 'deposition_sequence',
      // A wiggle match cannot apply an outlier model -- its gaps are
      // asserted, so there is nowhere for a date to sit apart from the rest,
      // and the server refuses one rather than dropping it. So the switch is
      // not offered for a member of one, EXCEPT where a prior is already set:
      // hiding it then would leave the analyst unable to clear the very
      // setting the refusal is about.
      showOutlier:
        analysisType === 'bayesian'
        && (holder !== 'wiggle_sequence' || (saved?.outlier_prior ?? 0) > 0),
    }
  }

  // One decision, read by the outlier switch in the edit dialog and by the
  // "apply to the rest of the phase" checkbox beneath it, so the two cannot
  // disagree about whether this date has an outlier model to set at all.
  const editFieldVisibility = editing
    ? ordinatesFor(editing.id)
    : { showRingOffset: false, showDepth: false, showOutlier: false }

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
            ring_offset: d.ring_offset ?? null,
            ring_offset_error: d.ring_offset_error ?? null,
            inbuilt_years: d.inbuilt_years ?? null,
            inbuilt_shape: d.inbuilt_shape ?? null,
            latitude: d.latitude ?? null,
            longitude: d.longitude ?? null,
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
      notify('Could not add dates to the collection: ' + describeFetchError(err))
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
      ring_offset: d.ring_offset,
      ring_offset_error: d.ring_offset_error,
      inbuilt_years: d.inbuilt_years ?? null,
      inbuilt_shape: d.inbuilt_shape ?? null,
      latitude: d.latitude,
      longitude: d.longitude,
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
    if (preview.resolution) setMcmc({ resolution: preview.resolution })
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
    // Named, not counted. "Please fill in all required fields" pointed at
    // nothing on a card with six inputs, none of which said which three of
    // them were required -- so the reader had to guess that Material and
    // Curve were optional and Age was not.
    const missing = [
      !currentMeasurement.sample_name && 'Sample name',
      currentMeasurement.age_bp === undefined && 'Age (BP)',
      currentMeasurement.error_1sigma === undefined && '1σ error',
    ].filter(Boolean) as string[]
    if (missing.length > 0) {
      setAddMeasurementError(
        missing.length === 1
          ? `${missing[0]} is needed to add this date.`
          : `${missing.slice(0, -1).join(', ')} and ${missing[missing.length - 1]} are needed to add this date.`
      )
      return
    }
    if (missingDeltaRError) {
      setAddMeasurementError(
        'A ΔR reservoir offset needs its 1σ error — the two are combined, so an offset on its own would understate the uncertainty of the date.'
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
  /**
   * Why Run Analysis cannot start, or null when it can.
   *
   * One source for both the disabled state and the tooltip, so the button
   * cannot be dead without saying why -- and cannot be live while the page
   * shows "Invalid -- fix before running", which is what happened while this
   * covered a circular relationship set but not a broken model tree.
   */
  const runBlockedReason = (): string | null => {
    if (measurements.length === 0) return 'Add at least one date first.'
    if (!analysisName.trim()) return 'Give the analysis a name first.'
    if (analysisType === 'bayesian' && !resolutionValid) {
      return 'Set Resolution under Advanced settings to a whole number of years from 1 to 100.'
    }
    return structureProblem()
  }

  const structureProblem = (): string | null => {
    if (analysisType !== 'bayesian') return null
    // An invalid structure would be rejected with a 422 after submitting, so
    // stop here and point back at the editor. The detail is in the persistent
    // "Model structure problems" banner below (see structureInvalid); this
    // says why the run cannot start.
    if (modelTree && treeProblems.length > 0) {
      // No "below": the primary action is pinned to the bottom of the
      // viewport now, so the banner it refers to is above it as often as not.
      return 'The model structure has problems. Fix them on the Model Structure page first.'
    }
    // The backend rejects a circular relationship set with a 422 anyway;
    // checking here avoids sending a request known to be invalid. Only the
    // relationship path needs it -- a model tree cannot contain a cycle.
    if (!modelTree && cycle) {
      return 'The stratigraphic relationships are circular. Fix this on the Model Structure page first.'
    }
    // No structure at all. The run is NOT harmless: with no model_tree and no
    // groups, the backend builds one anyway -- _tree_from_request falls to
    // migrate_from_relationships, which returns a Sequence holding a single
    // "Start of sequence" Boundary, and every date is then appended as one
    // "Unassigned dates" Phase. With uniform_span_prior on by default, that
    // Phase asserts the dates sample one bounded episode, which pulls the
    // posteriors toward each other.
    //
    // Those are not the independent calibrations a Group Calibration would
    // give, and they are the numbers that get cited. Blocking rather than
    // warning: the page called this "Optional", so the user has been told the
    // opposite of what running would do, and PRODUCT.md's rule is to fail loud
    // and specific rather than silently approximate.
    if (!modelTree && groups.length === 0) {
      return (
        `A Bayesian model needs a structure. Without one, all ${measurements.length} dates would be ` +
        'modelled as a single phase — treated as one bounded episode, which changes every result. ' +
        'Build the structure, or switch to Group Calibration to calibrate the dates independently.'
      )
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

  // Computed once per render so the disabled state, the tooltip and the line
  // beside the button are the same answer rather than three that can disagree.
  const blockedReason = runBlockedReason()

  const handleRunAnalysis = async () => {
    // runBlockedReason names the specific one, and is what disabled the button
    // in the first place -- this is the guard for the case where it is reached
    // anyway, not a second opinion with different wording.
    if (blockedReason) {
      setRunError(blockedReason)
      return
    }

    // A single calibration calibrates ONE date -- the request below sends
    // measurements[0] and nothing else. Without this the other dates were
    // dropped in silence: no error, no mention on the results page, and gone
    // from the draft too, since a successful run resets it. The edit path has
    // warned about this case all along; this is the path a first-timer takes.
    if (!isEditing && analysisType === 'calibration' && measurements.length > 1) {
      setRunError(
        `A single calibration is one date, and this has ${measurements.length}. ` +
          'Switch the analysis type to Group Calibration to calibrate them all separately, ' +
          'or remove the dates you do not want.'
      )
      return
    }

    setRunError(null)
    setLoading(true)
    setPreviewResult(null)
    try {
      const blocked = structureProblem()
      if (blocked) {
        // The persistent alert directly above the button, not a toast that
        // slides in at the far corner and takes the reason away with it after
        // eight seconds. Every other blocking condition on this page already
        // reports itself here.
        setRunError(blocked)
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
            estimate_kde: mcmc.estimate_kde,
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
          Editing a saved analysis. Run Analysis replaces its inputs and its current results.
        </Alert>
      )}

      <Grid container spacing={3}>
        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 3 }}>
            <Typography variant="h5" component="h2" gutterBottom>
              Analysis settings
            </Typography>

            <Box sx={{ mb: 2 }}>
              <TextField
                fullWidth
                required
                label="Analysis name"
                value={analysisName}
                onChange={(e) => setAnalysisName(e.target.value)}
                placeholder="e.g., Site XYZ - Phase 1"
              />
            </Box>

            <Box sx={{ mb: 3 }}>
              <FormControl fullWidth>
                <InputLabel id="analysis-type-label">Analysis type</InputLabel>
                <Select
            labelId="analysis-type-label"
                  value={analysisType}
                  label="Analysis type"
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
                <InputLabel id="project-label">Project</InputLabel>
                <Select
            labelId="project-label"
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
                <InputLabel id="date-format-label">Result date format</InputLabel>
                <Select
            labelId="date-format-label"
                  value={dateFormat}
                  label="Result date format"
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
                <Typography variant="subtitle2" component="h3" gutterBottom>
                  Model settings
                </Typography>
                {/* Solver override. Automatic is right almost always -- exact
                    integration has no sampler to converge and no calibration
                    lobe to miss -- but OxCal is the reference implementation, so
                    forcing its method is how you reproduce a published analysis
                    or cross-check the exact solver against an independent route
                    to the same posterior. */}
                <FormControl fullWidth size="small" sx={{ mb: 2 }}>
                  <InputLabel id="solver-label">Solver</InputLabel>
                  <Select
            labelId="solver-label"
                    value={mcmc.solver}
                    label="Solver"
                    onChange={(e) => setMcmc({ solver: e.target.value as SolverChoice })}
                  >
                    <MenuItem value="auto">Automatic</MenuItem>
                    <MenuItem value="exact">Exact solver only</MenuItem>
                    <MenuItem value="gibbs">MH/Gibbs sampler (as OxCal)</MenuItem>
                  </Select>
                  <FormHelperText>
                    {mcmc.solver === 'auto' &&
                      'Uses an Exact Solver for models where this is possible, and uses the MH/Gibbs method otherwise.'}
                    {mcmc.solver === 'exact' &&
                      'Integrates the posterior exactly for chain-shaped models i.e. a Sequence of Phases separated by Boundaries. Faster, but will fail for branching models.'}
                    {mcmc.solver === 'gibbs' &&
                      'Uses the same MCMC sampling method as Oxcal, works for all models.'}
                  </FormHelperText>
                </FormControl>
{/* Offered only where the model has something for a duration to
                    be about: a pair of Boundaries, or a phase with two dates
                    in it. A wiggle match has neither -- its members' spacing
                    is an INPUT rather than something the run infers -- so the
                    switch would promise a table that comes back empty, which
                    is what the Results page used to invite. */}
                {canReportIntervals && (
                  <>
                    <FormControlLabel
                      sx={{ display: 'flex' }}
                      control={
                        <Switch
                          checked={mcmc.estimate_durations}
                          onChange={(e) => setMcmc({ estimate_durations: e.target.checked })}
                        />
                      }
                      label={<span>Durations and differences</span>}
                    />
                    <FormHelperText sx={{ mt: 0, ml: 0 }}>
                      How long each sequence, phase, hiatus and set of dated events lasted,
                      when each phase was in progress, and how phases in parallel branches
                      overlap. Off by default: a phase&rsquo;s duration
                      assumes its dates sample a bounded episode.
                    </FormHelperText>
                  </>
                )}
                {canReportKde && (
                  <>
                    <FormControlLabel
                      sx={{ display: 'flex' }}
                      control={
                        <Switch
                          checked={mcmc.estimate_kde}
                          onChange={(e) => setMcmc({ estimate_kde: e.target.checked })}
                        />
                      }
                      label={<span>Kernel density estimates</span>}
                    />
                    <FormHelperText sx={{ mt: 0, ml: 0 }}>
                      How each phase&rsquo;s modelled dates are spread through time, as
                      OxCal&rsquo;s KDE_Plot.
                    </FormHelperText>
                  </>
                )}
                {/* Offered only where the dates are placed: a dispersal fit is
                    about where sites ARE, and with nothing placed the switch
                    would promise a section that comes back refused. */}
                {placedCount > 0 && (
                  <>
                    <FormControlLabel
                      sx={{ display: 'flex' }}
                      control={
                        <Switch
                          checked={mcmc.estimate_dispersal}
                          onChange={(e) => setMcmc({ estimate_dispersal: e.target.checked })}
                        />
                      }
                      label={<span>Spatial dispersal</span>}
                    />
                    <FormHelperText sx={{ mt: 0, ml: 0 }}>
                      Whether these {placedCount} placed dates describe something moving
                      across the landscape: the direction and speed of a trend, and where
                      it could have started. Off by default &mdash; a straight line through
                      dated points returns a speed whether or not there is a front.
                    </FormHelperText>
                    {mcmc.estimate_dispersal && (
                      <Box sx={{ mt: 1, mb: 1 }}>
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Button
                            size="small"
                            variant="outlined"
                            startIcon={<PlaceIcon />}
                            onClick={() => setOriginDialogOpen(true)}
                          >
                            {mcmc.dispersal_origin ? 'Move the origin' : 'Place an origin'}
                          </Button>
                          {mcmc.dispersal_origin && (
                            <>
                              <Typography variant="body2" color="text.secondary">
                                {mcmc.dispersal_origin.lat.toFixed(3)},{' '}
                                {mcmc.dispersal_origin.lon.toFixed(3)}
                              </Typography>
                              <Button
                                size="small"
                                onClick={() => setMcmc({ dispersal_origin: null })}
                              >
                                Clear
                              </Button>
                            </>
                          )}
                        </Stack>
                        <FormHelperText sx={{ mt: 0.5, ml: 0 }}>
                          {mcmc.dispersal_origin
                            ? 'Adds a wave of advance measured from this point, beside the models that assume no origin.'
                            : 'Optional. Without one you get the regional gradient and a profile of where the front could have started, both of which assume no origin. Placing a pin adds a front speed measured from it.'}
                        </FormHelperText>
                      </Box>
                    )}
                  </>
                )}
                {/* Run length, seed and the span prior are right as they stand
                    for almost every run, so they sit behind an expander rather
                    than in front of everyone who opens the page. */}
                <Accordion
                  disableGutters
                  elevation={0}
                  sx={{
                    mt: 2,
                    border: '1px solid',
                    borderColor: 'divider',
                    '&::before': { display: 'none' },
                  }}
                >
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Typography variant="subtitle2" component="h3">Advanced settings</Typography>
                  </AccordionSummary>
                  <AccordionDetails>
                    {/* Both apply to a sampled run only: the exact solver has
                        no run length and draws nothing. */}
                    <TextField
                      fullWidth
                      size="small"
                      type="number"
                      label="Minimum sweeps per chain"
                      value={mcmc.mcmc_min_sweeps || ''}
                      placeholder="Until converged"
                      onChange={(e) => {
                        const value = Math.floor(Number(e.target.value))
                        setMcmc({ mcmc_min_sweeps: Number.isFinite(value) && value > 0 ? Math.min(value, 1_000_000) : 0 })
                      }}
                      inputProps={{ min: 0, max: 1_000_000, step: 1000 }}
                      helperText="Sampled runs stop once converged. Set this to make each chain run at least this long."
                    />
                    <TextField
                      fullWidth
                      size="small"
                      type="number"
                      label="Seed"
                      sx={{ mt: 2 }}
                      value={mcmc.seed ?? ''}
                      placeholder="42"
                      onChange={(e) => {
                        const value = Math.floor(Number(e.target.value))
                        setMcmc({
                          seed:
                            e.target.value.trim() === '' || !Number.isFinite(value) || value < 0
                              ? null
                              : Math.min(value, 2 ** 31 - 2),
                        })
                      }}
                      inputProps={{ min: 0, step: 1 }}
                      helperText="The same seed repeats a sampled run exactly. A different one gives an independent replicate."
                    />
                    <TextField
                      fullWidth
                      size="small"
                      type="number"
                      label="Resolution (years)"
                      sx={{ mt: 2 }}
                      value={resolutionText}
                      onChange={(e) => {
                        setResolutionText(e.target.value)
                        if (isUsableResolution(e.target.value)) {
                          setMcmc({ resolution: Number(e.target.value) })
                        }
                      }}
                      inputProps={{ min: 1, max: 100, step: 1 }}
                      error={!resolutionValid}
                      helperText={
                        resolutionValid
                          ? 'The calendar step modelled ranges are reported at.'
                          : 'A whole number of years from 1 to 100.'
                      }
                    />
                    <FormControlLabel
                      sx={{ mt: 1, display: 'flex' }}
                      control={
                        <Switch
                          checked={mcmc.uniform_span_prior}
                          onChange={(e) => setMcmc({ uniform_span_prior: e.target.checked })}
                        />
                      }
                      label={<span>Uniform span prior</span>}
                    />
                    <FormHelperText sx={{ mt: 0, ml: 0 }}>
                      OxCal&rsquo;s UniformSpanPrior correction (Nicholls and Jones 2001), on
                      by default. Only affects a sequence with three or more boundaries.
                    </FormHelperText>
                    <FormControlLabel
                      sx={{ mt: 1, display: 'flex' }}
                      control={
                        <Switch
                          checked={mcmc.apply_inbuilt}
                          onChange={(e) => setMcmc({ apply_inbuilt: e.target.checked })}
                        />
                      }
                      label={<span>Apply inbuilt-age allowances</span>}
                    />
                    <FormHelperText sx={{ mt: 0, ml: 0 }}>
                      {measurements.some((m) => (m.inbuilt_years ?? 0) > 0)
                        ? 'Off runs the model as if no date had an allowance.'
                        : 'No date here has an inbuilt-age allowance.'}
                    </FormHelperText>
                  </AccordionDetails>
                </Accordion>
              </Box>
            )}

            {/* A group calibration has no phases, so nothing here has a
                duration -- but the dates can still be compared with each
                other, which is the half of this option that applies. */}
            {analysisType === 'group_calibration' && (
              <Box sx={{ mb: 2 }}>
                <Typography variant="subtitle2" component="h3" gutterBottom>
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
                <FormControlLabel
                  sx={{ mt: 1 }}
                  control={
                    <Switch
                      checked={mcmc.estimate_kde}
                      onChange={(e) => setMcmc({ estimate_kde: e.target.checked })}
                    />
                  }
                  label={<span>Kernel density estimate</span>}
                />
                <FormHelperText sx={{ mt: 0, ml: 0 }}>
                  How the dates are spread through time, as OxCal&rsquo;s KDE_Plot.
                </FormHelperText>
              </Box>
            )}
          </Paper>
        </Grid>

        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 3 }}>
            <Typography variant="h5" component="h2" gutterBottom>
              Add Date
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
                A spreadsheet of dates (.csv), or an OxCal model (.oxcal) with its structure — or
                fill in the fields below to add one date.
              </Typography>
            </Box>

            <MeasurementFields
              value={currentMeasurement}
              onChange={setCurrentMeasurement}
              showOutlier={analysisType === 'bayesian'}
            />

            <Button variant="outlined" onClick={handleAddMeasurement} fullWidth>
              Add Date
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
              {...editFieldVisibility}
            />
          )}
          {/* Setting an outlier model one date at a time is the usual case
              -- OxCal asks for it per date too -- but a phase of sixteen
              charcoal samples is one decision, not sixteen. Only the outlier
              settings travel; everything else on this form describes the
              individual sample. */}
          {/* Gated on the SAME decision as the switch above, not on the
              analysis type. A wiggle match cannot apply an outlier model at
              all, so offering to apply one to every other date in the series
              is offering to spread a setting that is not there and would be
              refused if it were. */}
          {editFieldVisibility.showOutlier && editing && editSiblings.length > 0 && (
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
              // The visibility check as well as the flag: the checkbox is
              // not rendered where an outlier model cannot apply, and a flag
              // left true by an earlier edit must not spread one anyway.
              if (editing.applyToPhase && editFieldVisibility.showOutlier) {
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
        // So the confirm button can say "Replace 4 dates with 12" rather than
        // "Load 12 dates", which reads as additive for an action that is not.
        existingCount={measurements.length}
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

     <Paper sx={{ p: 2, mt: 3 }}> <Box sx={{ mt: 3 }}>
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={1}
          alignItems={{ sm: 'center' }}
          justifyContent="space-between"
          sx={{ mb: 1 }}
        >
          <Typography variant="h5" component="h2">Dates ({measurements.length})</Typography>
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
        {/* Said here rather than only on submit, because by the time the run
            is blocked the reader has already typed every one of these dates.
            The action is offered inline: the fix is a type change, and making
            them find it back up the page is the actual friction. */}
        {analysisType === 'calibration' && measurements.length > 1 && (
          <Alert
            severity="warning"
            sx={{
              mb: 2,
              // Beside the message, the button leaves it 196px on a 375px
              // screen -- 22 characters a line over four lines. Below sm the
              // message takes the full width and the button follows it down.
              flexWrap: 'wrap',
              alignItems: { xs: 'flex-start', sm: 'center' },
              '& .MuiAlert-message': { minWidth: { xs: '100%', sm: 0 } },
              '& .MuiAlert-action': {
                ml: { xs: 0, sm: 'auto' },
                pl: { xs: 0, sm: 2 },
                pt: { xs: 0, sm: 0 },
                alignItems: 'center',
              },
            }}
            action={
              <Button
                color="inherit"
                size="small"
                onClick={() => setAnalysisType('group_calibration')}
              >
                Use Group Calibration
              </Button>
            }
          >
            A single calibration calibrates one date.{' '}
            {measurements.length === 2
              ? 'The other date below will not be included.'
              : `The other ${measurements.length - 1} dates below will not be included.`}
          </Alert>
        )}
        {measurements.length === 0 ? (
          <Typography color="textSecondary">No dates added yet</Typography>
        ) : (
          <DraftMeasurementTable
            measurements={measurements}
            curves={curves}
            concerns={lastRunConcerns}
            columns={draftColumns}
            onEdit={beginEditing}
            onRemove={removeMeasurement}
          />
        )}
      </Box></Paper>

      {analysisType === 'bayesian' && (
        <Box sx={{ mt: 3 }}>
          <Paper sx={{ p: 3 }}>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ sm: 'center' }} justifyContent="space-between">
              <Box>
                <Typography variant="h5" component="h2" gutterBottom>
                  Model Structure
                </Typography>
                <Stack direction="row" spacing={1} sx={{ mb: 1 }} flexWrap="wrap" useFlexGap>
                  {seriesElements.map((series) => {
                    const dated = datesInElement(series.id)
                    return (
                      <Chip
                        key={series.id}
                        size="small"
                        label={`${ELEMENT_TYPE_LABEL[series.element_type]} -- ${dated} date${dated === 1 ? '' : 's'}`}
                      />
                    )
                  })}
                  {!seriesIsTheModel && (
                    <>
                      <Chip size="small" label={`${structurePhaseCount} phase${structurePhaseCount === 1 ? '' : 's'}`} />
                      <Chip
                        size="small"
                        label={`${structureRelationshipCount} ordering relationship${structureRelationshipCount === 1 ? '' : 's'}`}
                      />
                    </>
                  )}
                  {structureInvalid && <Chip size="small" color="error" label="Invalid — fix before running" />}
                </Stack>
                <Typography variant="body2" color="textSecondary">
                  {seriesIsTheModel ? (
                    <>
                      The dates in this model are ordered by the {seriesOrderedBy} recorded against
                      them, not by Phases and Boundaries. Open Model Structure to change the series
                      or which dates are in it.
                    </>
                  ) : (
                    <>
                      Nested Sequences and Phases, with Boundaries where you want them and each
                      date assigned to a Phase. This structure is what constrains the dates; to
                      calibrate them independently instead, use Group Calibration.
                    </>
                  )}
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

      {/* Pinned to the bottom of the viewport. Twenty dates put this two
          viewport-heights below the fold, so every run ended in a scroll hunt
          for the button the page exists to press -- and DESIGN.md already makes
          the AppBar sticky for exactly this reason ("analyses and model trees
          can run long"); the reasoning simply had not been extended to the
          primary action. Sticky rather than fixed, so it sits in flow and
          cannot cover the last card. */}
      <Box
        sx={{
          position: 'sticky',
          bottom: 0,
          zIndex: (theme) => theme.zIndex.appBar - 1,
          mt: 4,
          // Out to the Container's own gutter, which is 16px on a phone and
          // 24px above -- a flat -3 pushed the bar 8px past a phone's edge.
          mx: { xs: -2, sm: -3 },
          px: { xs: 2, sm: 3 },
          py: 2,
          bgcolor: 'background.paper',
          borderTop: 1,
          borderColor: 'divider',
          display: 'flex',
          gap: 2,
          alignItems: 'center',
          flexWrap: 'wrap',
        }}
      >
        {/* A disabled button that does not say why is a dead end. The span is
            required for a tooltip on a disabled control -- the same pattern
            SortDatesButton already uses on this page. */}
        <Tooltip title={blockedReason ?? ''}>
          <span>
            <Button
              variant="contained"
              color="primary"
              size="large"
              onClick={handleRunAnalysis}
              disabled={loading || savingDetails || !!blockedReason}
            >
              {loading ? 'Running...' : 'Run Analysis'}
            </Button>
          </span>
        </Tooltip>
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
        {blockedReason && (
          <Typography variant="body2" color="text.secondary" sx={{ flex: '1 1 auto', minWidth: 0 }}>
            {blockedReason}
          </Typography>
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
            label="Project name"
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

      {/* The origin is a position like any other, so it is placed the same
          way -- with the dates on the map for context, since an origin nobody
          can see against the sites is a number rather than a decision. */}
      <PlaceOnMapDialog
        open={originDialogOpen}
        onClose={() => setOriginDialogOpen(false)}
        tiles={mapTiles}
        points={measurements.map((m) => ({
          id: m.id,
          name: m.sample_name,
          latitude: m.latitude ?? null,
          longitude: m.longitude ?? null,
        }))}
        selectedIds={[]}
        title="Place the dispersal origin"
        helper={
          'Click the map, or type the position. This is where the front is hypothesised to have '
          + 'started — it is not written to any date, and moving it moves the speed measured from '
          + 'it. The dates are drawn for scale.'
        }
        clearLabel="Remove the origin"
        onApply={(latitude, longitude) => {
          setMcmc({ dispersal_origin: { lat: latitude, lon: longitude } })
          setOriginDialogOpen(false)
        }}
        onClear={() => {
          setMcmc({ dispersal_origin: null })
          setOriginDialogOpen(false)
        }}
      />
    </Container>
  )
}
