// Model Structure definition page for a Bayesian analysis, reached from
// NewAnalysis.tsx once measurements exist.
//
// The model is authored as an explicit OxCal-style tree -- nested Sequences
// and Phases with Boundaries placed deliberately (components/
// ModelTreeEditor.tsx) -- which is the single source of truth for the
// structure. It replaced an earlier list editor that described stratigraphy
// as Contexts plus pairwise above/below/equal relationships; that could not
// express a placed Boundary, a site-wide Sequence, or any nesting, and
// keeping it alongside the tree would have meant two ways of saying the
// same thing with no way to reconcile them.
//
// The Harris matrix (components/HarrisMatrixDiagram.tsx) is now a READ-ONLY
// view derived from that tree via utils/harrisLayout.layoutHarrisMatrix, rather
// than an editable second representation.
//
// Two flows share this page: a saved analysis edits its stored tree through
// PUT /analysis/:id/model-tree, and a new one edits a draft tree held in
// stores/analysisStore.ts that is submitted with the analysis.
import { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Container,
  Box,
  Paper,
  Typography,
  Button,
  Tabs,
  Tab,
  Alert,
  Chip,
  Stack,
} from '@mui/material'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import { useAnalysisDraftStore } from '@/stores/analysisStore'
import type { ModelElement, MatrixMeasurement } from '@/types'
import HarrisMatrixDiagram from '@/components/HarrisMatrixDiagram'
import ModelTreeEditor, { newElementId, validateTree } from '@/components/ModelTreeEditor'
import { formatApiError } from '@/utils/apiError'
import { summariseTree } from '@/utils/harrisLayout'

const STRUCTURE_BLURB =
  'The explicit OxCal-style model: nested Sequences and Phases with Boundaries placed where you ' +
  'want them. Use a Sequence to put groups in order, a Phase for dates with no order between ' +
  'them, and a Boundary to mark a transition — a Boundary between two Phases is a single shared ' +
  'parameter, giving one transition date rather than two independent ones. Nest a Sequence ' +
  'inside a Phase for parallel branches, and put a Hiatus between two Boundaries for a ' +
  'deliberate gap.'

export default function Stratigraphy() {
  const navigate = useNavigate()
  const {
    measurements,
    modelTree: draftTree,
    elementAssignments: draftAssignments,
    setModelTree: setDraftTree,
  } = useAnalysisDraftStore()

  // Reached either as /new-analysis/model-structure (building a new analysis)
  // or /analysis/:analysisId/edit/model-structure (editing a saved one); the
  // editing UI is identical, only where "Back" returns to differs.
  const { analysisId: editRouteId } = useParams<{ analysisId: string }>()
  const backPath = editRouteId ? `/analysis/${editRouteId}/edit` : '/new-analysis'

  // The model structure is the authoring surface for stratigraphy in both
  // flows, so it is the default tab. For a saved analysis it is loaded from
  // and written back to the API; for a new one it lives in the draft store
  // and is submitted with the analysis.
  const [tab, setTab] = useState<'diagram' | 'structure'>('structure')

  // The explicit model tree only exists for a SAVED analysis (it is stored
  // against it and edited via PUT /analysis/:id/model-tree), so this tab is
  // offered when editing one. A brand-new analysis still uses the
  // context/relationship editor above and is converted to a tree on save.
  const [modelTree, setModelTree] = useState<ModelElement | null>(null)
  const [savedMeasurements, setSavedMeasurements] = useState<MatrixMeasurement[]>([])
  const [assignments, setAssignments] = useState<Record<string, string>>({})
  const [loadingStructure, setLoadingStructure] = useState(false)
  const [savingStructure, setSavingStructure] = useState(false)
  const [structureDirty, setStructureDirty] = useState(false)
  const [structureError, setStructureError] = useState<string | null>(null)
  const [linearized, setLinearized] = useState(false)

  // For a NEW analysis the tree lives in the draft store. Start it off as a
  // site-wide Sequence bracketed by boundaries with every date in one phase
  // -- the smallest complete model -- which the user then splits up.
  useEffect(() => {
    if (editRouteId || draftTree || measurements.length === 0) return
    const phaseId = newElementId()
    setDraftTree(
      {
        id: newElementId(),
        element_type: 'sequence',
        name: 'Site sequence',
        tpq_age_bp: null,
        taq_age_bp: null,
        children: [
          { id: newElementId(), element_type: 'boundary', name: 'Start of sequence', tpq_age_bp: null, taq_age_bp: null, children: [] },
          { id: phaseId, element_type: 'phase', name: 'Phase 1', tpq_age_bp: null, taq_age_bp: null, children: [] },
          { id: newElementId(), element_type: 'boundary', name: 'End of sequence', tpq_age_bp: null, taq_age_bp: null, children: [] },
        ],
      },
      Object.fromEntries(measurements.map((m) => [m.id, phaseId]))
    )
  }, [editRouteId, draftTree, measurements, setDraftTree])

  useEffect(() => {
    if (tab !== 'structure' || !editRouteId || modelTree) return
    setLoadingStructure(true)
    setStructureError(null)
    fetch(`/api/analysis/${editRouteId}`)
      .then((r) => r.json())
      .then((data) => {
        if (!data.success) {
          setStructureError(formatApiError(data))
          return
        }
        setModelTree(data.data.model_tree || null)
        setLinearized(!!data.data.model_tree_linearized)
        const saved: MatrixMeasurement[] = data.data.measurements || []
        setSavedMeasurements(saved)
        const initial: Record<string, string> = {}
        for (const m of saved) {
          if (m.element_id) initial[m.id] = m.element_id
          else if (m.group_id) initial[m.id] = m.group_id
        }
        setAssignments(initial)
        // Mirror into the draft store as well: NewAnalysis submits the
        // draft's tree when the analysis is run, and it skips reloading a
        // draft it already holds -- so a tree only edited here would
        // otherwise be resubmitted as whatever the store loaded earlier.
        setDraftTree(data.data.model_tree || null, initial)
      })
      .catch((err) => setStructureError((err as Error).message))
      .finally(() => setLoadingStructure(false))
  }, [tab, editRouteId, modelTree, setDraftTree])

  const activeTree = editRouteId ? modelTree : draftTree
  const activeAssignments = editRouteId ? assignments : draftAssignments
  const activeMeasurements = editRouteId ? savedMeasurements : measurements
  const unassignedCount = activeMeasurements.filter((m) => !activeAssignments[m.id]).length

  // The Harris matrix is a derived VIEW of the model tree rather than an
  // editable source of its own -- whichever tree is in play (the saved
  // analysis's, or the in-progress draft) is what it draws.
  const summary = summariseTree(activeTree)

  // Same rules the backend enforces (services/model_tree.validate_tree), run
  // here so an invalid structure can't be submitted at all. The editor shows
  // what's wrong; this is what stops Save.
  const structureProblems = activeTree
    ? validateTree(activeTree, activeAssignments, activeMeasurements)
    : []

  const saveStructure = async () => {
    if (!editRouteId || !modelTree || structureProblems.length > 0) return
    setSavingStructure(true)
    setStructureError(null)
    try {
      const response = await fetch(`/api/analysis/${editRouteId}/model-tree`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_tree: modelTree, sample_elements: assignments }),
      })
      const data = await response.json()
      if (data.success) {
        setStructureDirty(false)
        setLinearized(false)
      } else {
        setStructureError(formatApiError(data))
      }
    } catch (err) {
      setStructureError((err as Error).message)
    } finally {
      setSavingStructure(false)
    }
  }

  // Only meaningful for a brand-new analysis, whose measurements live in the
  // in-memory draft. When editing a saved analysis the draft may legitimately
  // be empty (e.g. arriving on a direct link), and the Model Structure tab
  // loads what it needs from the API itself.
  if (measurements.length === 0 && !editRouteId) {
    return (
      <Container maxWidth="md" sx={{ py: 4 }}>
        <Alert severity="warning" sx={{ mb: 2 }}>
          No measurements yet -- add measurements on the New Analysis page first.
        </Alert>
        <Button startIcon={<ArrowBackIcon />} variant="outlined" onClick={() => navigate(backPath)}>
          Back to New Analysis
        </Button>
      </Container>
    )
  }

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 3, flexWrap: 'wrap', gap: 2 }}>
        <Box>
          <Typography variant="h3" component="h1" gutterBottom>
            Model Structure
          </Typography>
          <Typography variant="body1" color="textSecondary">
            Build the model as nested Sequences and Phases with Boundaries where you want them,
            then assign each date to a Phase.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Chip label={`${summary.phaseCount} phase${summary.phaseCount === 1 ? '' : 's'}`} />
          {unassignedCount > 0 && (
            <Chip label={`${unassignedCount} unassigned date${unassignedCount === 1 ? '' : 's'}`} color="warning" />
          )}
        </Stack>
      </Box>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab value="diagram" label="Harris Matrix Diagram" />
        <Tab value="structure" label="Editor" />
      </Tabs>

      {tab === 'structure' && !editRouteId ? (
        <Paper sx={{ p: 3 }}>
          <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
            {STRUCTURE_BLURB}
          </Typography>
          {draftTree ? (
            <ModelTreeEditor
              tree={draftTree}
              measurements={measurements}
              assignments={draftAssignments}
              onChange={(nextTree, nextAssignments) => setDraftTree(nextTree, nextAssignments)}
            />
          ) : (
            <Alert severity="info">Add some dates on the New Analysis page first.</Alert>
          )}
        </Paper>
      ) : tab === 'structure' && editRouteId ? (
        <Paper sx={{ p: 3 }}>
          <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
            {STRUCTURE_BLURB}
          </Typography>
          {structureError && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {structureError}
            </Alert>
          )}
          {linearized && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              This model was converted from the older context/relationship format, and those
              relationships could not be expressed as a tree exactly — they were flattened into a
              single order, which constrains more than the original did. Please check the
              structure below before running.
            </Alert>
          )}
          {loadingStructure ? (
            <Typography variant="body2">Loading…</Typography>
          ) : modelTree ? (
            <>
              <ModelTreeEditor
                tree={modelTree}
                measurements={savedMeasurements}
                assignments={assignments}
                onChange={(nextTree, nextAssignments) => {
                  setModelTree(nextTree)
                  setAssignments(nextAssignments)
                  setDraftTree(nextTree, nextAssignments)
                  setStructureDirty(true)
                }}
              />
              <Box sx={{ mt: 3, display: 'flex', gap: 2, alignItems: 'center' }}>
                <Button
                  variant="contained"
                  onClick={saveStructure}
                  disabled={savingStructure || structureProblems.length > 0}
                >
                  {savingStructure ? 'Saving…' : 'Save Model Structure'}
                </Button>
                {structureProblems.length > 0 ? (
                  <Typography variant="caption" color="error">
                    Fix the {structureProblems.length === 1 ? 'problem' : 'problems'} listed above
                    before saving.
                  </Typography>
                ) : (
                  structureDirty && (
                    <Typography variant="caption" color="textSecondary">
                      Unsaved changes. Saving clears this analysis's current results — re-run it
                      afterwards.
                    </Typography>
                  )
                )}
              </Box>
            </>
          ) : (
            <Alert severity="info">
              This analysis has no model structure yet. Add some dates and save it first.
            </Alert>
          )}
        </Paper>
      ) : (
        <Paper sx={{ p: 3 }}>
          <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
            A read-only view of the model structure as a Harris matrix, oldest at the bottom. A
            Phase holding dates is a context; a Phase or Sequence holding others is drawn as a box
            around them, which groups without ordering. A Boundary is a labelled rule across the
            groups it divides. Edit the structure on the Editor tab.
          </Typography>
          <HarrisMatrixDiagram
            tree={activeTree}
            measurements={activeMeasurements}
            assignments={activeAssignments}
          />
        </Paper>
      )}

      <Box sx={{ mt: 4 }}>
        <Button startIcon={<ArrowBackIcon />} variant="contained" onClick={() => navigate(backPath)}>
          Back to New Analysis
        </Button>
      </Box>

    </Container>
  )
}
