// Stratigraphy definition page for a Bayesian analysis, reached from
// NewAnalysis.tsx once at least two measurements exist. The workflow
// mirrors how archaeologists actually record stratigraphy: samples aren't
// related to each other directly -- each dated sample is assigned to a
// stratigraphic Context (a layer/cut/fill -- the "same context" concept
// standard in archaeological recording), and above/below/equal
// relationships are then defined between Contexts, not between individual
// dates. TPQ/TAQ age limiters can still be set per-sample or per-context.
//
// Offers two ways to specify this -- a simple list-picker form, and a true
// drag-and-drop Harris matrix diagram (components/HarrisMatrixDiagram.tsx)
// -- editing the same shared draft (stores/analysisStore.ts), so either can
// be used alone or both together (e.g. build via the list, then open the
// diagram just to visually check the relationships are right before
// running).
//
// Internally this still uses the backend's generic "group" vocabulary
// (DraftGroup, group_id, from_type/to_type: 'group') -- "Context" is
// purely the label shown to the user, matching the archaeological term.
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
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  IconButton,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  TextField,
  Alert,
  Chip,
  Stack,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from '@mui/material'
import DeleteIcon from '@mui/icons-material/Delete'
import AddIcon from '@mui/icons-material/Add'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import { useAnalysisDraftStore } from '@/stores/analysisStore'
import type { RelationshipType, ModelElement, MatrixMeasurement } from '@/types'
import HarrisMatrixDiagram from '@/components/HarrisMatrixDiagram'
import ModelTreeEditor from '@/components/ModelTreeEditor'
import { formatApiError } from '@/utils/apiError'
import { expandRelationships, findCycle } from '@/utils/stratigraphy'

function newId(): string {
  return Math.random().toString(36).substr(2, 9)
}

const RELATIONSHIP_LABELS: Record<RelationshipType, string> = {
  above: 'Above (younger than)',
  below: 'Below (older than)',
  equal: 'Equal (same phase as)',
}

export default function Stratigraphy() {
  const navigate = useNavigate()
  const {
    measurements,
    groups: contexts,
    relationships,
    addGroup: addContext,
    removeGroup: removeContext,
    updateGroup: updateContext,
    updateMeasurement,
    addRelationship,
    removeRelationship,
    updateRelationshipType,
  } = useAnalysisDraftStore()

  // Reached either as /new-analysis/stratigraphy (building a new analysis)
  // or /analysis/:analysisId/edit/stratigraphy (editing a saved one); the
  // editing UI is identical, only where "Back" returns to differs.
  const { analysisId: editRouteId } = useParams<{ analysisId: string }>()
  const backPath = editRouteId ? `/analysis/${editRouteId}/edit` : '/new-analysis'

  // When editing a saved analysis, the model structure is the primary view
  // (and the one that works on a direct link, since it loads its own data
  // from the API rather than from the in-memory draft).
  const [tab, setTab] = useState<'list' | 'diagram' | 'structure'>(
    editRouteId ? 'structure' : 'list'
  )

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
      })
      .catch((err) => setStructureError((err as Error).message))
      .finally(() => setLoadingStructure(false))
  }, [tab, editRouteId, modelTree])

  const saveStructure = async () => {
    if (!editRouteId || !modelTree) return
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

  const [newContextDialogOpen, setNewContextDialogOpen] = useState(false)
  const [newContextName, setNewContextName] = useState('')

  const [relFrom, setRelFrom] = useState('')
  const [relTo, setRelTo] = useState('')
  const [relType, setRelType] = useState<RelationshipType>('above')

  const { olderYounger } = expandRelationships(measurements, contexts, relationships)
  const cycle = findCycle(olderYounger.map((e) => ({ from: e.from, to: e.to })))
  const namesById = new Map<string, string>([
    ...measurements.map((m) => [m.id, m.sample_name] as [string, string]),
    ...contexts.map((c) => [c.id, c.name] as [string, string]),
  ])
  const unassignedCount = measurements.filter((m) => !m.group_id).length

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

  const handleCreateContext = () => {
    if (!newContextName.trim()) return
    addContext({ id: newId(), name: newContextName.trim(), tpq_age_bp: null, taq_age_bp: null })
    setNewContextName('')
    setNewContextDialogOpen(false)
  }

  const handleAddRelationship = () => {
    if (!relFrom || !relTo || relFrom === relTo) return
    addRelationship({
      id: newId(),
      from_sample_id: relFrom,
      from_type: 'group',
      to_sample_id: relTo,
      to_type: 'group',
      relationship: relType,
    })
    setRelFrom('')
    setRelTo('')
  }

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 3, flexWrap: 'wrap', gap: 2 }}>
        <Box>
          <Typography variant="h3" component="h1" gutterBottom>
            Stratigraphy
          </Typography>
          <Typography variant="body1" color="textSecondary">
            Assign each sample to a stratigraphic context, then define above/below/equal
            relationships and TPQ/TAQ age limits between those contexts.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Chip label={`${contexts.length} context${contexts.length === 1 ? '' : 's'}`} />
          <Chip label={`${relationships.length} relationship${relationships.length === 1 ? '' : 's'}`} />
          {unassignedCount > 0 && <Chip label={`${unassignedCount} unassigned sample${unassignedCount === 1 ? '' : 's'}`} color="warning" />}
          {cycle ? <Chip label="Circular -- fix before running" color="error" /> : <Chip label="No cycles detected" color="success" />}
        </Stack>
      </Box>

      {cycle && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Circular relationship detected involving: {cycle.map((id) => namesById.get(id) || id).join(', ')}.
          No set of calendar ages can satisfy every constraint at once -- fix or remove one of these
          relationships before running the analysis.
        </Alert>
      )}

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab value="list" label="List Editor" />
        <Tab value="diagram" label="Harris Matrix Diagram" />
        {editRouteId && <Tab value="structure" label="Model Structure" />}
      </Tabs>

      {tab === 'structure' && editRouteId ? (
        <Paper sx={{ p: 3 }}>
          <Typography variant="h6" gutterBottom>
            Model Structure
          </Typography>
          <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
            The explicit OxCal-style model: nested Sequences and Phases with Boundaries placed
            where you want them. Use a Sequence to put groups in order, a Phase for dates with no
            order between them, and a Boundary to mark a transition — a Boundary between two
            Phases is a single shared parameter, giving one transition date rather than two
            independent ones. Nest a Sequence inside a Phase for parallel branches, and put a
            Hiatus between two Boundaries for a deliberate gap.
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
                  setStructureDirty(true)
                }}
              />
              <Box sx={{ mt: 3, display: 'flex', gap: 2, alignItems: 'center' }}>
                <Button variant="contained" onClick={saveStructure} disabled={savingStructure}>
                  {savingStructure ? 'Saving…' : 'Save Model Structure'}
                </Button>
                {structureDirty && (
                  <Typography variant="caption" color="textSecondary">
                    Unsaved changes. Saving clears this analysis's current results — re-run it
                    afterwards.
                  </Typography>
                )}
              </Box>
            </>
          ) : (
            <Alert severity="info">
              This analysis has no model structure yet. Add some dates and save it first.
            </Alert>
          )}
        </Paper>
      ) : tab === 'list' ? (
        <Box>
          {/* Contexts */}
          <Paper sx={{ p: 3, mb: 3 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
              <Box>
                <Typography variant="h6">1. Contexts</Typography>
                <Typography variant="body2" color="textSecondary">
                  A context is a stratigraphic layer, cut, or fill -- create one for each distinct
                  deposit your samples came from.
                </Typography>
              </Box>
              <Button size="small" startIcon={<AddIcon />} onClick={() => setNewContextDialogOpen(true)}>
                Add Context
              </Button>
            </Box>
            {contexts.length === 0 ? (
              <Typography color="textSecondary" variant="body2">
                No contexts yet. Create one, then assign your samples to it below.
              </Typography>
            ) : (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Name</TableCell>
                      <TableCell align="right">TPQ (max age, cal BP)</TableCell>
                      <TableCell align="right">TAQ (min age, cal BP)</TableCell>
                      <TableCell align="right">Dates</TableCell>
                      <TableCell align="right" />
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {contexts.map((c) => (
                      <TableRow key={c.id}>
                        <TableCell>{c.name}</TableCell>
                        <TableCell align="right">
                          <TextField
                            type="number"
                            size="small"
                            value={c.tpq_age_bp ?? ''}
                            onChange={(e) => updateContext(c.id, { tpq_age_bp: e.target.value === '' ? null : Number(e.target.value) })}
                            sx={{ width: 110 }}
                          />
                        </TableCell>
                        <TableCell align="right">
                          <TextField
                            type="number"
                            size="small"
                            value={c.taq_age_bp ?? ''}
                            onChange={(e) => updateContext(c.id, { taq_age_bp: e.target.value === '' ? null : Number(e.target.value) })}
                            sx={{ width: 110 }}
                          />
                        </TableCell>
                        <TableCell align="right">{measurements.filter((m) => m.group_id === c.id).length}</TableCell>
                        <TableCell align="right">
                          <IconButton size="small" color="error" onClick={() => removeContext(c.id)}>
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Paper>

          {/* Samples: context assignment + TPQ/TAQ */}
          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h6" gutterBottom>
              2. Assign Samples to Contexts
            </Typography>
            {unassignedCount > 0 && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                {unassignedCount} sample{unassignedCount === 1 ? '' : 's'} not yet assigned to a context --
                {unassignedCount === 1 ? " it won't" : " they won't"} appear in the Harris matrix or be
                constrained by any relationship until assigned.
              </Alert>
            )}
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Sample</TableCell>
                    <TableCell>Age (BP)</TableCell>
                    <TableCell>Context</TableCell>
                    <TableCell align="right">TPQ (max age)</TableCell>
                    <TableCell align="right">TAQ (min age)</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {measurements.map((m) => (
                    <TableRow key={m.id}>
                      <TableCell>{m.sample_name}</TableCell>
                      <TableCell>{m.age_bp} ± {m.error_1sigma}</TableCell>
                      <TableCell>
                        <FormControl size="small" sx={{ minWidth: 160 }}>
                          <Select
                            value={m.group_id || ''}
                            displayEmpty
                            onChange={(e) => updateMeasurement(m.id, { group_id: e.target.value || null })}
                          >
                            <MenuItem value="">
                              <em>No context</em>
                            </MenuItem>
                            {contexts.map((c) => (
                              <MenuItem key={c.id} value={c.id}>
                                {c.name}
                              </MenuItem>
                            ))}
                          </Select>
                        </FormControl>
                      </TableCell>
                      <TableCell align="right">
                        <TextField
                          type="number"
                          size="small"
                          value={m.tpq_age_bp ?? ''}
                          onChange={(e) => updateMeasurement(m.id, { tpq_age_bp: e.target.value === '' ? null : Number(e.target.value) })}
                          sx={{ width: 110 }}
                        />
                      </TableCell>
                      <TableCell align="right">
                        <TextField
                          type="number"
                          size="small"
                          value={m.taq_age_bp ?? ''}
                          onChange={(e) => updateMeasurement(m.id, { taq_age_bp: e.target.value === '' ? null : Number(e.target.value) })}
                          sx={{ width: 110 }}
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Paper>

          {/* Relationships between contexts */}
          <Paper sx={{ p: 3 }}>
            <Typography variant="h6" gutterBottom>
              3. Relationships Between Contexts
            </Typography>
            {contexts.length < 2 ? (
              <Typography color="textSecondary" variant="body2">
                Create at least two contexts above to define a relationship between them.
              </Typography>
            ) : (
              <>
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mb: 2 }} alignItems="center">
                  <FormControl size="small" sx={{ minWidth: 180 }}>
                    <InputLabel>From context</InputLabel>
                    <Select label="From context" value={relFrom} onChange={(e) => setRelFrom(e.target.value)}>
                      {contexts.map((c) => (
                        <MenuItem key={c.id} value={c.id}>
                          {c.name}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                  <FormControl size="small" sx={{ minWidth: 200 }}>
                    <InputLabel>Relationship</InputLabel>
                    <Select label="Relationship" value={relType} onChange={(e) => setRelType(e.target.value as RelationshipType)}>
                      {Object.entries(RELATIONSHIP_LABELS).map(([value, label]) => (
                        <MenuItem key={value} value={value}>
                          {label}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                  <FormControl size="small" sx={{ minWidth: 180 }}>
                    <InputLabel>To context</InputLabel>
                    <Select label="To context" value={relTo} onChange={(e) => setRelTo(e.target.value)}>
                      {contexts.map((c) => (
                        <MenuItem key={c.id} value={c.id}>
                          {c.name}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                  <Button variant="outlined" startIcon={<AddIcon />} onClick={handleAddRelationship} disabled={!relFrom || !relTo || relFrom === relTo}>
                    Add
                  </Button>
                </Stack>

                {relationships.length === 0 ? (
                  <Typography color="textSecondary" variant="body2">
                    No relationships defined yet.
                  </Typography>
                ) : (
                  <TableContainer>
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell>From</TableCell>
                          <TableCell>Relationship</TableCell>
                          <TableCell>To</TableCell>
                          <TableCell align="right" />
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {relationships.map((r) => (
                          <TableRow key={r.id}>
                            <TableCell>{namesById.get(r.from_sample_id) || r.from_sample_id}</TableCell>
                            <TableCell>{RELATIONSHIP_LABELS[r.relationship]}</TableCell>
                            <TableCell>{namesById.get(r.to_sample_id) || r.to_sample_id}</TableCell>
                            <TableCell align="right">
                              <IconButton size="small" color="error" onClick={() => removeRelationship(r.id)}>
                                <DeleteIcon fontSize="small" />
                              </IconButton>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                )}
              </>
            )}
          </Paper>
        </Box>
      ) : (
        <Paper sx={{ p: 3 }}>
          <HarrisMatrixDiagram
            measurements={measurements}
            groups={contexts}
            relationships={relationships}
            editable
            onAddRelationship={(rel) => addRelationship({ ...rel, id: newId() })}
            onUpdateRelationshipType={(id, relationship) => updateRelationshipType(id, relationship)}
            onDeleteRelationship={(id) => removeRelationship(id)}
          />
        </Paper>
      )}

      <Box sx={{ mt: 4 }}>
        <Button startIcon={<ArrowBackIcon />} variant="contained" onClick={() => navigate(backPath)}>
          Back to New Analysis
        </Button>
      </Box>

      <Dialog open={newContextDialogOpen} onClose={() => setNewContextDialogOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>New Context</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            autoFocus
            label="Context Name"
            value={newContextName}
            onChange={(e) => setNewContextName(e.target.value)}
            placeholder="e.g., Context 104 -- hearth fill"
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setNewContextDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleCreateContext} disabled={!newContextName.trim()}>
            Create
          </Button>
        </DialogActions>
      </Dialog>
    </Container>
  )
}
