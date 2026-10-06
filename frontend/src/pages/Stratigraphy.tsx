// Model Structure definition page for a Bayesian analysis, reached from
// NewAnalysis.tsx once measurements exist.
//
// The model is authored as an explicit OxCal-style tree -- nested Sequences
// and Phases with Boundaries placed deliberately (components/
// ModelTreeEditor.tsx) -- which is the single source of truth for the
// structure. The Harris matrix (components/HarrisMatrixDiagram.tsx) is a
// READ-ONLY view derived from it via utils/harrisLayout.layoutHarrisMatrix,
// not an editable second representation.
//
// Two flows share this page: a saved analysis edits its stored tree through
// PUT /analysis/:id/model-tree, and a new one edits a draft tree held in
// stores/analysisStore.ts that is submitted with the analysis.
import { useState, useEffect, useMemo } from 'react'
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
  Skeleton,
} from '@mui/material'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import { useAnalysisDraftStore } from '@/stores/analysisStore'
import { useProjectMapTiles } from '@/hooks/useProjectMapTiles'
import type { ModelElement, MatrixMeasurement } from '@/types'
import HarrisMatrixDiagram from '@/components/HarrisMatrixDiagram'
import ModelTreeEditor, { validateTree } from '@/components/ModelTreeEditor'
import GuideBand from '@/components/GuideBand'
import { bootstrapModelTree } from '@/utils/modelTreeDefaults'
import { formatApiError } from '@/utils/apiError'
import { summariseTree } from '@/utils/harrisLayout'
import { sortMeasurements, type MeasurementSortKey } from '@/utils/sortMeasurements'
import { pooledMeasurement, type SameEventTest } from '@/utils/sameEvent'

// The element vocabulary, the drag gestures and the TPQ/TAQ definitions used
// to be restated here as a ~105-word paragraph, immediately above a component
// that says the same things better: ModelTreeEditor now carries them as a
// colour key (each term in its own element-type hue, beside the 4px bar the
// rows carry) and a disclosure. Two descriptions of one vocabulary is one too
// many, and the loser was the reader scrolling past both.

export default function Stratigraphy() {
  const navigate = useNavigate()
  const {
    measurements,
    setMeasurements,
    removeMeasurement,
    replaceMeasurements,
    modelTree: draftTree,
    elementAssignments: draftAssignments,
    setModelTree: setDraftTree,
    editingAnalysisId,
    projectId: draftProjectId,
  } = useAnalysisDraftStore()

  // Reached either as /new-analysis/model-structure (building a new analysis)
  // or /analysis/:analysisId/edit/model-structure (editing a saved one); the
  // editing UI is identical, only where "Back" returns to differs.
  const { analysisId: editRouteId } = useParams<{ analysisId: string }>()
  const backPath = editRouteId ? `/analysis/${editRouteId}/edit` : '/new-analysis'
  // Returning here carries no marker of any kind. NewAnalysis.tsx keeps the
  // draft by default and empties it only when an entry point asks for a fresh
  // start, so coming back -- by this button, by browser Back, or by a reload
  // -- lands on the draft that is already there.
  const backState = undefined

  // Whether the dates to build around are the DRAFT's. Always, for a new
  // analysis. For a saved one, only when the Edit Analysis page has loaded it
  // into the store -- a direct link here leaves the store empty (or holding
  // some other analysis), and then the saved copy from the API is all there
  // is. The distinction matters because the draft is what Run Analysis
  // submits: a date deleted and re-added on the Edit Analysis page exists only
  // there, and a structure built around the saved copy could neither show it
  // nor place it.
  const draftHoldsDates =
    !editRouteId || (editingAnalysisId === editRouteId && measurements.length > 0)

  // The default tab, being the authoring surface in both flows. A saved
  // analysis loads it from and writes it back to the API; a new one holds it
  // in the draft store and submits it with the analysis.
  const [tab, setTab] = useState<'diagram' | 'structure'>('structure')

  // The SAVED analysis's stored tree, loaded from the API and written back
  // through PUT /analysis/:id/model-tree. Null for a new analysis, whose tree
  // lives in the draft store instead (see draftModelTree below).
  const [modelTree, setModelTree] = useState<ModelElement | null>(null)
  const [savedMeasurements, setSavedMeasurements] = useState<MatrixMeasurement[]>([])
  const [assignments, setAssignments] = useState<Record<string, string>>({})
  const [loadingStructure, setLoadingStructure] = useState(false)
  const [savingStructure, setSavingStructure] = useState(false)
  const [structureDirty, setStructureDirty] = useState(false)
  const [structureError, setStructureError] = useState<string | null>(null)
  const [linearized, setLinearized] = useState(false)
  // What the analysis is saved AS, which is not always what is being edited:
  // switching a saved group calibration to Bayesian changes the draft, and the
  // conversion only reaches the backend through Run Analysis. Until then there
  // is no structure to write to, so what is built here belongs to the unsaved
  // edit -- see the Save block below.
  const [savedType, setSavedType] = useState<string | null>(null)
  // Which project's basemap setting the spatial-order map draws under: the
  // draft's project, or -- editing a saved analysis with no draft of it --
  // the project the analysis is filed under.
  const [savedProjectId, setSavedProjectId] = useState<string | null>(null)
  const mapTiles = useProjectMapTiles(draftProjectId || savedProjectId)

  // For a NEW analysis the tree lives in the draft store, starting as the
  // smallest complete model -- a site-wide Sequence bracketed by boundaries --
  // with one phase per context the dates carry, or a single phase when they
  // carry none. See utils/modelTreeDefaults.
  useEffect(() => {
    if (editRouteId || draftTree || measurements.length === 0) return
    const { tree, assignments: seeded } = bootstrapModelTree(
      // depth_cm decides whether the contexts get an ordered Sequence or an
      // unordered Phase -- see contextOrderFromDepth.
      measurements.map((m) => ({
        id: m.id,
        context: m.default_context_name,
        depth: m.depth_cm,
      }))
    )
    setDraftTree(tree, seeded)
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
        setLinearized(!!data.data.model_tree_linearized)
        setSavedType(data.data.analysis_type || null)
        setSavedProjectId(data.data.project_id || null)
        const saved: MatrixMeasurement[] = data.data.measurements || []
        setSavedMeasurements(saved)
        // When the draft holds this analysis, its tree and assignments are
        // the ones being edited -- they started as the saved copy (see
        // NewAnalysis.loadFromAnalysis) and carry whatever has changed since.
        // Reloading the saved copy over them would discard those changes and
        // drop any date added to the draft.
        let tree: ModelElement | null = draftHoldsDates && draftTree ? draftTree : data.data.model_tree || null
        const initial: Record<string, string> = {}
        if (draftHoldsDates && draftTree) {
          Object.assign(initial, draftAssignments)
        } else {
          for (const m of saved) {
            if (m.element_id) initial[m.id] = m.element_id
            else if (m.group_id) initial[m.id] = m.group_id
          }
        }
        // The dates to place: the draft's when it holds them, for the reason
        // given at draftHoldsDates.
        const toPlace: MatrixMeasurement[] = draftHoldsDates ? measurements : saved
        if (!tree && toPlace.length > 0) {
          // A saved analysis with no structure, usually a group calibration
          // being converted to Bayesian. It has dates, so start it as a new
          // analysis starts rather than asking for dates that are right there.
          // Contexts come from the SampleGroups it was deployed with, so the
          // grouping it arrived with survives.
          const groupNames: Record<string, string> = Object.fromEntries(
            (data.data.groups || []).map((g: { id: string; name: string }) => [g.id, g.name])
          )
          const bootstrapped = bootstrapModelTree(
            // No depth here: MatrixMeasurement does not carry depth_cm, so a
            // converted analysis always starts unordered. That is the honest
            // result rather than a gap to paper over -- the ordering evidence
            // genuinely is not in this payload.
            toPlace.map((m) => ({
              id: m.id,
              context: groupNames[initial[m.id]] ?? null,
            }))
          )
          tree = bootstrapped.tree
          // The bootstrapped phases are new elements, so the saved
          // element_id/group_id assignments no longer name anything.
          for (const key of Object.keys(initial)) delete initial[key]
          Object.assign(initial, bootstrapped.assignments)
          // Nothing on the server matches this yet.
          setStructureDirty(true)
        }
        setModelTree(tree)
        setAssignments(initial)
        // Mirrored into the draft store too: NewAnalysis submits the draft's
        // tree on run and skips reloading a draft it already holds, so a tree
        // edited only here would be resubmitted as whatever the store loaded
        // earlier. This is also the only route a bootstrapped structure takes
        // to the backend before the analysis is saved as Bayesian.
        setDraftTree(tree, initial)
      })
      .catch((err) => setStructureError((err as Error).message))
      .finally(() => setLoadingStructure(false))
    // The draft values are read once, when the structure is first loaded;
    // the early return above makes later changes to them irrelevant here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, editRouteId, modelTree, setDraftTree])

  /**
   * Reorder the dates from the model editor.
   *
   * Writes to the DRAFT store, because that is the copy Run Analysis submits
   * -- a new order is an input change like any other, and reaches the backend
   * the same way. On the edit route the local savedMeasurements list is sorted
   * by the same key alongside it, so what this page shows and what will be
   * saved do not drift apart while the tab is open.
   *
   * Offered only when the draft holds the dates: arriving on a direct link to
   * this page under /analysis/:id/edit leaves the store empty, and sorting
   * then would rearrange the list on screen while changing nothing that gets
   * saved. The measurement list on the Edit Analysis page has the control in
   * that case.
   */
  const canSortDates = draftHoldsDates && measurements.length > 1
  const sortDates = (key: MeasurementSortKey) => {
    setMeasurements(sortMeasurements(measurements, key))
    if (editRouteId) setSavedMeasurements(sortMeasurements(savedMeasurements, key))
  }

  /**
   * Write one date's ordinate -- its ring offset or its depth -- back to
   * wherever the dates are held.
   *
   * Editable HERE, not only on the data-entry table, because moving a date
   * into a wiggle match is exactly the action that leaves it without a gap:
   * the field has to be within reach of the assignment that made it
   * necessary.
   *
   * It writes to the DRAFT, which is what Run Analysis submits -- an ordinate
   * is a property of the DATE and travels with the measurements, not with the
   * tree that PUT /model-tree saves. So it is offered only when the draft
   * holds the dates, the same rule and for the same reason as the sort
   * control above: arriving on a direct link to this page under
   * /analysis/:id/edit leaves the store empty, and editing then would change
   * the screen without changing anything that gets saved.
   */
  const canSetOrdinates = draftHoldsDates && measurements.length > 0
  const setOrdinate = (measurementId: string, patch: Partial<MatrixMeasurement>) => {
    const apply = <T extends MatrixMeasurement>(list: T[]): T[] =>
      list.map((m) => (m.id === measurementId ? { ...m, ...patch } : m))
    setMeasurements(apply(measurements))
    if (editRouteId) setSavedMeasurements(apply(savedMeasurements))
  }

  /**
   * Take dates out of the analysis, from the editor's unassigned list.
   *
   * Through the draft store's own removeMeasurement -- the path the Delete
   * button on the analysis page takes -- so whatever else names a date (its
   * assignment, a relationship) goes with it. Offered only when the draft
   * holds the dates, for the reason given at the sort control: anything else
   * would change the screen and not what gets submitted. On the edit route
   * the dates then differ from the saved ones, so the structure saves with
   * Run Analysis -- see datesDiverged.
   */
  const canRemoveDates = draftHoldsDates && measurements.length > 0
  const removeDates = (ids: string[]) => {
    for (const id of ids) removeMeasurement(id)
    if (editRouteId) {
      const gone = new Set(ids)
      setAssignments((prev) => Object.fromEntries(Object.entries(prev).filter(([mid]) => !gone.has(mid))))
    }
  }

  /**
   * Replace dates that passed the same-event test with their pooled date
   * (utils/sameEvent.pooledMeasurement): OxCal's R_Combine, entered as the
   * date it produces. Written to the draft, on the same terms as removeDates
   * above -- the dates change, so on the edit route the structure saves with
   * Run Analysis.
   */
  const canPoolDates = draftHoldsDates && measurements.length > 1
  const poolDates = (ids: string[], test: SameEventTest) => {
    const wanted = new Set(ids)
    const pooled = pooledMeasurement(
      measurements.filter((m) => wanted.has(m.id)),
      test,
      'pooled_' + Math.random().toString(36).slice(2, 11)
    )
    replaceMeasurements(ids, pooled)
    if (editRouteId) {
      setAssignments((prev) => {
        const phaseId = ids.map((id) => prev[id]).find(Boolean)
        const next = Object.fromEntries(Object.entries(prev).filter(([mid]) => !wanted.has(mid)))
        if (phaseId) next[pooled.id] = phaseId
        return next
      })
    }
  }

  const activeTree = editRouteId ? modelTree : draftTree
  const activeAssignments = editRouteId ? assignments : draftAssignments
  const activeMeasurements = draftHoldsDates ? measurements : savedMeasurements
  // Whether the draft's dates are still the saved analysis's dates. A date
  // added on the Edit Analysis page is not on the server yet, so a structure
  // that places it can only reach the backend with the dates themselves --
  // through Run Analysis -- and PUT /model-tree would reject its id.
  const datesDiverged =
    !!editRouteId &&
    draftHoldsDates &&
    !loadingStructure &&
    (measurements.length !== savedMeasurements.length ||
      measurements.some((m) => !savedMeasurements.some((s) => s.id === m.id)))
  // Only a Bayesian analysis stores a structure, so until the conversion
  // happens there is nothing to save the tree INTO; and once the dates have
  // changed, the tree has to travel with them. Either way the structure is
  // part of the unsaved edit and Run Analysis applies both.
  const structureSavesWithRun = (savedType !== null && savedType !== 'bayesian') || datesDiverged
  const unassignedCount = activeMeasurements.filter((m) => !activeAssignments[m.id]).length

  // The Harris matrix is a derived VIEW rather than an editable source, and
  // draws whichever tree is in play: the saved analysis's, or the draft.
  const summary = summariseTree(activeTree)

  // The rules the backend enforces (services/model_tree.validate_tree), run
  // here so an invalid structure cannot be submitted. The editor shows what is
  // wrong; this stops Save.
  //
  // Memoised: it walks every element and every date, and this page renders
  // on each keystroke in a name field.
  const structureProblems = useMemo(
    () => (activeTree ? validateTree(activeTree, activeAssignments, activeMeasurements) : []),
    [activeTree, activeAssignments, activeMeasurements]
  )

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

  // Only meaningful for a new analysis, whose measurements live in the draft.
  // When editing a saved one the draft may legitimately be empty -- arriving
  // on a direct link -- and this tab loads what it needs from the API.
  if (measurements.length === 0 && !editRouteId) {
    return (
      <Container maxWidth="md" sx={{ py: 4 }}>
        <Alert severity="warning" sx={{ mb: 2 }}>
          No dates yet — add them on the New Analysis page first.
        </Alert>
        <Button startIcon={<ArrowBackIcon />} variant="outlined" onClick={() => navigate(backPath, { state: backState })}>
          Back to New Analysis
        </Button>
      </Container>
    )
  }

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 3, flexWrap: 'wrap', gap: 2 }}>
        <Box>
          {/* variant="h1", not h3, for the reason Results.tsx documents at its
              own title: the theme defines h1 as the design system's Headline
              (2.5rem/600), and this was rendering at MUI's stock h3 -- so two
              consecutive pages in one flow gave their titles two different
              sizes. */}
          <Typography variant="h1" component="h1" gutterBottom>
            Model Structure
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Chip label={`${summary.phaseCount} phase${summary.phaseCount === 1 ? '' : 's'}`} />
          {unassignedCount > 0 && (
            <Chip label={`${unassignedCount} unassigned date${unassignedCount === 1 ? '' : 's'}`} color="warning" />
          )}
        </Stack>
      </Box>

      {/* Editor first, because it is the default and the reason to be on this
          page. The strip used to list the diagram first while opening on the
          second tab, so the page contradicted its own reading order. The
          diagram is derived from what the editor authors, so it follows it
          here too. */}
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab value="structure" label="Editor" />
        <Tab value="diagram" label="Matrix diagram" />
      </Tabs>

      {/* One shell for both flows. The draft and the saved analysis used to
          repeat the Paper, its padding and the whole instruction blurb in two
          sibling branches, which is two copies of the same surface free to
          drift apart the moment either is edited. The alerts below are
          edit-only by nature -- structureError and linearized are set on the
          API paths and stay null for a draft -- so hoisting them costs the
          draft flow nothing and removes the divergence. */}
      {tab === 'structure' ? (
        <>
          {/* Above the card, not inside it. These speak about the model as a
              whole -- how it was loaded, whether it converted cleanly -- and
              the editor's guide band is flush to the card's top edge, so an
              alert placed inside would sit on the band's surface and push it
              off that edge. */}
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
          <Paper sx={{ p: 3 }}>
          {!editRouteId ? (
            draftTree ? (
              <ModelTreeEditor
                tree={draftTree}
                measurements={measurements}
                assignments={draftAssignments}
                onChange={(nextTree, nextAssignments) => setDraftTree(nextTree, nextAssignments)}
                onSortDates={canSortDates ? sortDates : undefined}
                onSetOrdinate={canSetOrdinates ? setOrdinate : undefined}
                onRemoveDates={canRemoveDates ? removeDates : undefined}
                onPoolDates={canPoolDates ? poolDates : undefined}
                tiles={mapTiles}
              />
            ) : (
              <Alert severity="info">No dates yet — add them on the New Analysis page first.</Alert>
            )
          ) : loadingStructure ? (
            // A shape, not the word "Loading" -- the rows that are coming are
            // a known size, so the page can hold their space instead of
            // collapsing and then jumping.
            <Box>
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} variant="rounded" height={56} sx={{ mb: 1, ml: i === 0 ? 0 : 3 }} />
              ))}
            </Box>
          ) : modelTree ? (
            <>
              <ModelTreeEditor
                tree={modelTree}
                measurements={activeMeasurements}
                assignments={assignments}
                onSortDates={canSortDates ? sortDates : undefined}
                onSetOrdinate={canSetOrdinates ? setOrdinate : undefined}
                onRemoveDates={canRemoveDates ? removeDates : undefined}
                onPoolDates={canPoolDates ? poolDates : undefined}
                tiles={mapTiles}
                onChange={(nextTree, nextAssignments) => {
                  setModelTree(nextTree)
                  setAssignments(nextAssignments)
                  setDraftTree(nextTree, nextAssignments)
                  setStructureDirty(true)
                }}
              />
              {structureSavesWithRun ? (
                <Alert severity="info" sx={{ mt: 3 }}>
                  {savedType !== 'bayesian' ? (
                    <>
                      This analysis is still saved as a{' '}
                      {savedType === 'group_calibration' ? 'group calibration' : savedType}, which
                      has no model structure to write to. The structure above is part of your
                      unsaved edit: go back and press Run Analysis to convert the analysis and apply
                      it.
                    </>
                  ) : (
                    <>
                      The dates in this edit differ from the ones saved with the analysis, so the
                      structure above is part of your unsaved edit and travels with them: go back
                      and press Run Analysis to apply both.
                    </>
                  )}
                  {structureProblems.length > 0 && (
                    <>
                      {' '}
                      Fix the {structureProblems.length === 1 ? 'problem' : 'problems'} listed above
                      first.
                    </>
                  )}
                </Alert>
              ) : (
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
              )}
            </>
          ) : (
            <Alert severity="info">
              No dates yet — add them on the Edit Analysis page first.
            </Alert>
          )}
          </Paper>
        </>
      ) : (
        <Paper sx={{ p: 3 }}>
          {/* The same band the editor tab opens with, so the two tabs of one
              page separate what is read from what is worked in identically. */}
          <GuideBand>
            <Typography variant="body2" color="textSecondary">
              A Phase holding dates is a context. Known temporal relationships run vertically —
              younger above older — and unknown ones sit side by side. A Boundary is a labelled rule
              across the groups it divides.
            </Typography>
          </GuideBand>
          <HarrisMatrixDiagram
            tree={activeTree}
            measurements={activeMeasurements}
            assignments={activeAssignments}
          />
        </Paper>
      )}

      <Box sx={{ mt: 4 }}>
        <Button startIcon={<ArrowBackIcon />} variant="contained" onClick={() => navigate(backPath, { state: backState })}>
          {editRouteId ? 'Back to Edit Analysis' : 'Back to New Analysis'}
        </Button>
      </Box>

    </Container>
  )
}
