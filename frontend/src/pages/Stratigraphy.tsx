// Stratigraphy definition page for a Bayesian analysis, reached from
// NewAnalysis.tsx once at least two measurements exist. Offers two ways
// to specify above/below/equal constraints and TPQ/TAQ age limiters --
// a simple list-picker form, and a true drag-and-drop Harris matrix
// diagram (components/HarrisMatrixDiagram.tsx) -- editing the same
// shared draft (stores/analysisStore.ts), so either can be used alone or
// both together (e.g. build via the list, then open the diagram just to
// visually check the relationships are right before running).
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
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
import type { NodeType, RelationshipType } from '@/types'
import HarrisMatrixDiagram from '@/components/HarrisMatrixDiagram'
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
    groups,
    relationships,
    addGroup,
    removeGroup,
    updateGroup,
    updateMeasurement,
    addRelationship,
    removeRelationship,
    updateRelationshipType,
  } = useAnalysisDraftStore()

  const [tab, setTab] = useState<'list' | 'diagram'>('list')

  const [newGroupDialogOpen, setNewGroupDialogOpen] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')

  const [relFrom, setRelFrom] = useState('')
  const [relFromType, setRelFromType] = useState<NodeType>('sample')
  const [relTo, setRelTo] = useState('')
  const [relToType, setRelToType] = useState<NodeType>('sample')
  const [relType, setRelType] = useState<RelationshipType>('above')

  const nodeOptions = [
    ...groups.map((g) => ({ id: g.id, type: 'group' as NodeType, label: `${g.name} (group)` })),
    ...measurements.map((m) => ({ id: m.id, type: 'sample' as NodeType, label: m.sample_name })),
  ]

  const { olderYounger } = expandRelationships(measurements, groups, relationships)
  const cycle = findCycle(olderYounger.map((e) => ({ from: e.from, to: e.to })))
  const namesById = new Map<string, string>([
    ...measurements.map((m) => [m.id, m.sample_name] as [string, string]),
    ...groups.map((g) => [g.id, g.name] as [string, string]),
  ])

  if (measurements.length === 0) {
    return (
      <Container maxWidth="md" sx={{ py: 4 }}>
        <Alert severity="warning" sx={{ mb: 2 }}>
          No measurements yet -- add measurements on the New Analysis page first.
        </Alert>
        <Button startIcon={<ArrowBackIcon />} variant="outlined" onClick={() => navigate('/new-analysis')}>
          Back to New Analysis
        </Button>
      </Container>
    )
  }

  const handleCreateGroup = () => {
    if (!newGroupName.trim()) return
    addGroup({ id: newId(), name: newGroupName.trim(), tpq_age_bp: null, taq_age_bp: null })
    setNewGroupName('')
    setNewGroupDialogOpen(false)
  }

  const handleAddRelationship = () => {
    if (!relFrom || !relTo || relFrom === relTo) return
    addRelationship({
      id: newId(),
      from_sample_id: relFrom,
      from_type: relFromType,
      to_sample_id: relTo,
      to_type: relToType,
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
            Define above/below/equal relationships and TPQ/TAQ age limits for this Bayesian analysis.
          </Typography>
        </Box>
        <Stack direction="row" spacing={1}>
          <Chip label={`${groups.length} group${groups.length === 1 ? '' : 's'}`} />
          <Chip label={`${relationships.length} relationship${relationships.length === 1 ? '' : 's'}`} />
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
      </Tabs>

      {tab === 'list' ? (
        <Box>
          {/* Groups */}
          <Paper sx={{ p: 3, mb: 3 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
              <Typography variant="h6">Groups (phases)</Typography>
              <Button size="small" startIcon={<AddIcon />} onClick={() => setNewGroupDialogOpen(true)}>
                Add Group
              </Button>
            </Box>
            {groups.length === 0 ? (
              <Typography color="textSecondary" variant="body2">
                No groups yet. Groups let one relationship or TPQ/TAQ limiter apply to several samples at once.
              </Typography>
            ) : (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Name</TableCell>
                      <TableCell align="right">TPQ (max age, cal BP)</TableCell>
                      <TableCell align="right">TAQ (min age, cal BP)</TableCell>
                      <TableCell align="right">Members</TableCell>
                      <TableCell align="right" />
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {groups.map((g) => (
                      <TableRow key={g.id}>
                        <TableCell>{g.name}</TableCell>
                        <TableCell align="right">
                          <TextField
                            type="number"
                            size="small"
                            value={g.tpq_age_bp ?? ''}
                            onChange={(e) => updateGroup(g.id, { tpq_age_bp: e.target.value === '' ? null : Number(e.target.value) })}
                            sx={{ width: 110 }}
                          />
                        </TableCell>
                        <TableCell align="right">
                          <TextField
                            type="number"
                            size="small"
                            value={g.taq_age_bp ?? ''}
                            onChange={(e) => updateGroup(g.id, { taq_age_bp: e.target.value === '' ? null : Number(e.target.value) })}
                            sx={{ width: 110 }}
                          />
                        </TableCell>
                        <TableCell align="right">{measurements.filter((m) => m.group_id === g.id).length}</TableCell>
                        <TableCell align="right">
                          <IconButton size="small" color="error" onClick={() => removeGroup(g.id)}>
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

          {/* Samples: group assignment + TPQ/TAQ */}
          <Paper sx={{ p: 3, mb: 3 }}>
            <Typography variant="h6" gutterBottom>
              Samples
            </Typography>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Sample</TableCell>
                    <TableCell>Age (BP)</TableCell>
                    <TableCell>Group</TableCell>
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
                        <FormControl size="small" sx={{ minWidth: 140 }}>
                          <Select
                            value={m.group_id || ''}
                            displayEmpty
                            onChange={(e) => updateMeasurement(m.id, { group_id: e.target.value || null })}
                          >
                            <MenuItem value="">No group</MenuItem>
                            {groups.map((g) => (
                              <MenuItem key={g.id} value={g.id}>
                                {g.name}
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

          {/* Relationships */}
          <Paper sx={{ p: 3 }}>
            <Typography variant="h6" gutterBottom>
              Relationships
            </Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mb: 2 }} alignItems="center">
              <FormControl size="small" sx={{ minWidth: 180 }}>
                <InputLabel>From</InputLabel>
                <Select
                  label="From"
                  value={relFrom}
                  onChange={(e) => {
                    const opt = nodeOptions.find((o) => o.id === e.target.value)
                    setRelFrom(e.target.value)
                    if (opt) setRelFromType(opt.type)
                  }}
                >
                  {nodeOptions.map((o) => (
                    <MenuItem key={`${o.type}-${o.id}`} value={o.id}>
                      {o.label}
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
                <InputLabel>To</InputLabel>
                <Select
                  label="To"
                  value={relTo}
                  onChange={(e) => {
                    const opt = nodeOptions.find((o) => o.id === e.target.value)
                    setRelTo(e.target.value)
                    if (opt) setRelToType(opt.type)
                  }}
                >
                  {nodeOptions.map((o) => (
                    <MenuItem key={`${o.type}-${o.id}`} value={o.id}>
                      {o.label}
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
          </Paper>
        </Box>
      ) : (
        <Paper sx={{ p: 3 }}>
          <HarrisMatrixDiagram
            measurements={measurements}
            groups={groups}
            relationships={relationships}
            editable
            onAddRelationship={(rel) => addRelationship({ ...rel, id: newId() })}
            onUpdateRelationshipType={(id, relationship) => updateRelationshipType(id, relationship)}
            onDeleteRelationship={(id) => removeRelationship(id)}
          />
        </Paper>
      )}

      <Box sx={{ mt: 4 }}>
        <Button startIcon={<ArrowBackIcon />} variant="contained" onClick={() => navigate('/new-analysis')}>
          Back to New Analysis
        </Button>
      </Box>

      <Dialog open={newGroupDialogOpen} onClose={() => setNewGroupDialogOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>New Group</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            autoFocus
            label="Group Name"
            value={newGroupName}
            onChange={(e) => setNewGroupName(e.target.value)}
            placeholder="e.g., Phase 2 hearth fill"
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setNewGroupDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleCreateGroup} disabled={!newGroupName.trim()}>
            Create
          </Button>
        </DialogActions>
      </Dialog>
    </Container>
  )
}
