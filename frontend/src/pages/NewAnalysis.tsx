import React, { useState, useEffect } from 'react'
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
  Switch,
  Alert,
  CircularProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Chip,
  Stack,
  Autocomplete,
} from '@mui/material'
import { useNavigate, useSearchParams, useParams } from 'react-router-dom'
import DeleteIcon from '@mui/icons-material/Delete'
import AddIcon from '@mui/icons-material/Add'
import AccountTreeIcon from '@mui/icons-material/AccountTree'
import PosteriorPlot from '@/components/PosteriorPlot'
import {
  DateFormat,
  DATE_FORMAT_OPTIONS,
  DEFAULT_DATE_FORMAT,
  saveDateFormatPreference,
  formatCalendarAge,
} from '@/utils/dateFormat'
import { formatApiError } from '@/utils/apiError'
import { useAnalysisDraftStore } from '@/stores/analysisStore'
import type { DraftMeasurement } from '@/types'
import { expandRelationships, findCycle } from '@/utils/stratigraphy'

type Measurement = DraftMeasurement

// Common radiocarbon-datable material types, offered as suggestions in
// the Material field below (free text is still allowed -- this is a
// convenience list, not a restriction). material_type is already
// persisted and appears in the CSV export; this is just the form control
// that was missing to actually set it away from the "unknown" default.
const COMMON_MATERIAL_TYPES = [
  'charcoal', 'wood', 'bone', 'shell', 'seed', 'antler', 'eggshell', 'peat', 'sediment',
]

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

export default function NewAnalysis() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [dateFormat, setDateFormat] = useState<DateFormat>(DEFAULT_DATE_FORMAT)
  // Name, type, measurements, and (for Bayesian analyses) groups/
  // relationships/TPQ-TAQ defined on the Stratigraphy page all live in a
  // shared store rather than local state, so navigating to
  // /new-analysis/stratigraphy and back doesn't lose anything that was
  // already entered -- local useState here would reset to its initial
  // value on every remount, silently reverting analysisType to
  // "calibration" and losing whatever name was typed.
  const {
    name: analysisName,
    setName: setAnalysisName,
    analysisType,
    setAnalysisType,
    measurements,
    addMeasurement,
    removeMeasurement,
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
  // Set when this page is reached via /analysis/:analysisId/edit -- the
  // same form, but saving replaces an existing analysis's inputs (PUT
  // .../inputs then rerun) instead of creating a new one.
  const { analysisId: editRouteId } = useParams<{ analysisId: string }>()
  const isEditing = !!editRouteId
  const [loadingExisting, setLoadingExisting] = useState(isEditing)
  const [currentMeasurement, setCurrentMeasurement] = useState<Partial<Measurement>>({
    sample_type: 'terrestrial',
    curve_id: 'intcal20',
  })
  const [loading, setLoading] = useState(false)

  const [projects, setProjects] = useState<ProjectOption[]>([])
  const [projectId, setProjectId] = useState<string>(searchParams.get('project') || '')
  // Only offered for single calibration -- a quick way to try a
  // measurement without it showing up in a project or the analysis list.
  const [saveResult, setSaveResult] = useState(true)
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null)

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

  // Editing an existing analysis: pull its saved inputs into the shared
  // draft store, so this page and the Stratigraphy page edit them with
  // exactly the same UI used to create them. GET /api/analysis/:id already
  // returns measurements/groups/relationships/bayesian_config, so nothing
  // extra is needed to reconstruct the draft. Skipped if the store is
  // already holding this analysis (e.g. returning from the Stratigraphy
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
          alert('Failed to load analysis: ' + formatApiError(data))
          navigate('/projects')
          return
        }
        const a = data.data
        loadFromAnalysis({
          analysisId: editRouteId,
          name: a.name,
          analysisType: a.analysis_type === 'bayesian' ? 'bayesian' : 'calibration',
          measurements: (a.measurements || []).map((m: any) => ({
            id: m.id,
            sample_name: m.sample_name,
            age_bp: m.age_bp,
            error_1sigma: m.error_1sigma,
            material_type: m.material_type || 'unknown',
            sample_type: m.sample_type || 'terrestrial',
            curve_id: m.curve_id || 'intcal20',
            group_id: m.group_id ?? null,
            tpq_age_bp: m.tpq_age_bp ?? null,
            taq_age_bp: m.taq_age_bp ?? null,
          })),
          groups: (a.groups || []).map((g: any) => ({
            id: g.id,
            name: g.name,
            tpq_age_bp: g.tpq_age_bp ?? null,
            taq_age_bp: g.taq_age_bp ?? null,
          })),
          relationships: (a.relationships || []).map((r: any) => ({
            id: r.id,
            from_sample_id: r.from_sample_id,
            to_sample_id: r.to_sample_id,
            from_type: r.from_type,
            to_type: r.to_type,
            relationship: r.relationship,
          })),
          mcmc: a.bayesian_config || undefined,
        })
        setProjectId(a.project_id || '')
      } catch (err) {
        if (!cancelled) alert('Error loading analysis: ' + (err as Error).message)
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
        alert('Failed to create project: ' + formatApiError(data))
      }
    } catch (error) {
      alert('Error creating project: ' + (error as Error).message)
    } finally {
      setCreatingProject(false)
    }
  }

  const { olderYounger: draftOlderYounger } = expandRelationships(measurements, groups, relationships)
  const cycle = findCycle(draftOlderYounger.map((e) => ({ from: e.from, to: e.to })))
  const cycleLabels = (cycle || []).map((id) => {
    const m = measurements.find((mm) => mm.id === id)
    if (m) return m.sample_name
    const g = groups.find((gg) => gg.id === id)
    return g ? g.name : id
  })

  const handleAddMeasurement = () => {
    if (
      !currentMeasurement.sample_name ||
      currentMeasurement.age_bp === undefined ||
      currentMeasurement.error_1sigma === undefined
    ) {
      alert('Please fill in all required fields')
      return
    }

    const measurement: Measurement = {
      id: Math.random().toString(36).substr(2, 9),
      sample_name: currentMeasurement.sample_name || '',
      age_bp: currentMeasurement.age_bp || 0,
      error_1sigma: currentMeasurement.error_1sigma || 0,
      material_type: currentMeasurement.material_type || 'unknown',
      sample_type: currentMeasurement.sample_type || 'terrestrial',
      curve_id: currentMeasurement.curve_id || 'intcal20',
      reservoir_age: currentMeasurement.reservoir_age,
    }

    addMeasurement(measurement)
    setCurrentMeasurement({ sample_type: 'terrestrial', curve_id: 'intcal20' })
  }

  const handleRunAnalysis = async () => {
    if (!analysisName || measurements.length === 0) {
      alert('Please provide analysis name and at least one measurement')
      return
    }

    setLoading(true)
    setPreviewResult(null)
    try {
      if (analysisType === 'calibration') {
        // For single calibration, send just the first measurement
        const measurement = measurements[0]
        const response = await fetch('/api/analysis/calibrate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            age_bp: measurement.age_bp,
            error_1sigma: measurement.error_1sigma,
            curve_id: measurement.curve_id,
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
          alert('Analysis failed: ' + formatApiError(data))
        }
      } else {
        // For Bayesian, send the full stratigraphy defined on the
        // Stratigraphy page too (groups, relationships, and any TPQ/TAQ
        // limiters set there or inline on a measurement) -- previously
        // this always sent relationships: [], so the working constraint
        // logic in bayesian.py was unreachable from the UI entirely.
        //
        // The backend rejects a circular relationship set with a 422
        // before running any MCMC, but checking here too avoids sending
        // a request that's already known to be invalid. Only relevant to
        // the legacy relationship path -- a model tree cannot contain a
        // cycle, which is one of the things authoring one directly buys.
        if (!modelTree && cycle) {
          alert(
            'Cannot run: the stratigraphic relationships are circular (' +
              cycleLabels.join(', ') +
              '). Fix this on the Stratigraphy page first.'
          )
          setLoading(false)
          return
        }
        // Stratigraphy is authored as an explicit model tree (see
        // Stratigraphy.tsx's Model Structure tab), submitted as-is. The
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
              alert('Inputs saved, but the run failed: ' + formatApiError(runData))
              setLoading(false)
              return
            }
          }
          saveDateFormatPreference(resultId, dateFormat)
          resetDraft()
          navigate(`/results/${resultId}`)
        } else {
          alert('Analysis failed: ' + formatApiError(data))
        }
      }
    } catch (error) {
      alert('Error running analysis: ' + (error as Error).message)
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
          Editing a saved analysis. Saving replaces its inputs and re-runs it, replacing its current
          results.
        </Alert>
      )}

      <Grid container spacing={3}>
        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 3 }}>
            <Typography variant="h6" gutterBottom>
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
                  onChange={(e) => setAnalysisType(e.target.value as 'calibration' | 'bayesian')}
                >
                  <MenuItem value="calibration">Single Calibration</MenuItem>
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

            {/* MCMC settings -- previously hardcoded here and unreachable
                from the UI. Exposed now because duplicating an analysis to
                compare model variants is exactly when varying them (e.g.
                more draws for a chain that converged poorly) becomes
                useful. */}
            {analysisType === 'bayesian' && (
              <Box sx={{ mb: 2 }}>
                <Typography variant="subtitle2" gutterBottom>
                  MCMC Settings
                </Typography>
                <Box sx={{ display: 'flex', gap: 2 }}>
                  <TextField
                    fullWidth
                    size="small"
                    type="number"
                    label="Draws"
                    value={mcmc.mcmc_samples}
                    onChange={(e) => setMcmc({ mcmc_samples: Number(e.target.value) })}
                  />
                  <TextField
                    fullWidth
                    size="small"
                    type="number"
                    label="Tune"
                    value={mcmc.mcmc_tune}
                    onChange={(e) => setMcmc({ mcmc_tune: Number(e.target.value) })}
                  />
                  <TextField
                    fullWidth
                    size="small"
                    type="number"
                    label="Chains"
                    value={mcmc.mcmc_chains}
                    onChange={(e) => setMcmc({ mcmc_chains: Number(e.target.value) })}
                  />
                </Box>
              </Box>
            )}
          </Paper>
        </Grid>

        <Grid item xs={12} md={6}>
          <Paper sx={{ p: 3 }}>
            <Typography variant="h6" gutterBottom>
              Add Measurement
            </Typography>

            <Box sx={{ mb: 2 }}>
              <TextField
                fullWidth
                label="Sample Name"
                value={currentMeasurement.sample_name || ''}
                onChange={(e) =>
                  setCurrentMeasurement({ ...currentMeasurement, sample_name: e.target.value })
                }
                size="small"
                sx={{ mb: 1 }}
              />
            </Box>

            <Box sx={{ mb: 2 }}>
              <Autocomplete
                freeSolo
                options={COMMON_MATERIAL_TYPES}
                value={currentMeasurement.material_type || ''}
                onInputChange={(_, newValue) =>
                  setCurrentMeasurement({ ...currentMeasurement, material_type: newValue })
                }
                renderInput={(params) => (
                  <TextField {...params} label="Material" placeholder="e.g., charcoal, bone, shell" size="small" />
                )}
              />
            </Box>

            <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
              <TextField
                type="number"
                label="Age (BP)"
                value={currentMeasurement.age_bp || ''}
                onChange={(e) =>
                  setCurrentMeasurement({ ...currentMeasurement, age_bp: parseFloat(e.target.value) })
                }
                size="small"
                sx={{ flex: 1 }}
              />
              <TextField
                type="number"
                label="1σ Error"
                value={currentMeasurement.error_1sigma || ''}
                onChange={(e) =>
                  setCurrentMeasurement({
                    ...currentMeasurement,
                    error_1sigma: parseFloat(e.target.value),
                  })
                }
                size="small"
                sx={{ flex: 1 }}
              />
            </Box>

            <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
              <FormControl size="small" sx={{ flex: 1 }}>
                <InputLabel>Sample Type</InputLabel>
                <Select
                  value={currentMeasurement.sample_type || 'terrestrial'}
                  label="Sample Type"
                  onChange={(e) =>
                    setCurrentMeasurement({
                      ...currentMeasurement,
                      sample_type: e.target.value as 'terrestrial' | 'marine',
                    })
                  }
                >
                  <MenuItem value="terrestrial">Terrestrial</MenuItem>
                  <MenuItem value="marine">Marine</MenuItem>
                </Select>
              </FormControl>
              <FormControl size="small" sx={{ flex: 1 }}>
                <InputLabel>Curve</InputLabel>
                <Select
                  value={currentMeasurement.curve_id || 'intcal20'}
                  label="Curve"
                  onChange={(e) =>
                    setCurrentMeasurement({ ...currentMeasurement, curve_id: e.target.value })
                  }
                >
                  <MenuItem value="intcal20">IntCal20</MenuItem>
                  <MenuItem value="marine20">Marine20</MenuItem>
                  <MenuItem value="shcal20">SHCal20</MenuItem>
                </Select>
              </FormControl>
            </Box>

            <Button variant="outlined" onClick={handleAddMeasurement} fullWidth>
              Add Measurement
            </Button>
          </Paper>
        </Grid>
      </Grid>

      <Box sx={{ mt: 3 }}>
        <Typography variant="h6" gutterBottom>
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
                      </Box>
                      <IconButton
                        size="small"
                        onClick={() => removeMeasurement(m.id)}
                        color="error"
                      >
                        <DeleteIcon />
                      </IconButton>
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
                <Typography variant="h6" gutterBottom>
                  Stratigraphy
                </Typography>
                <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
                  <Chip size="small" label={`${groups.length} context${groups.length === 1 ? '' : 's'}`} />
                  <Chip size="small" label={`${relationships.length} relationship${relationships.length === 1 ? '' : 's'}`} />
                  {cycle && <Chip size="small" color="error" label="Circular -- fix before running" />}
                </Stack>
                <Typography variant="body2" color="textSecondary">
                  Optional: assign samples to stratigraphic contexts, then define above/below/equal
                  relationships between those contexts and TPQ/TAQ age limits, via a list picker or a
                  drag-and-drop Harris matrix diagram.
                </Typography>
              </Box>
              <Button
                variant="outlined"
                startIcon={<AccountTreeIcon />}
                onClick={() =>
                  navigate(isEditing ? `/analysis/${editRouteId}/edit/stratigraphy` : '/new-analysis/stratigraphy')
                }
                disabled={measurements.length === 0}
              >
                {groups.length > 0 || relationships.length > 0 ? 'Edit Stratigraphy' : 'Define Stratigraphy'}
              </Button>
            </Stack>
            {cycle && (
              <Alert severity="error" sx={{ mt: 2 }}>
                Circular relationship involving: {cycleLabels.join(', ')}. Open Stratigraphy to fix this
                before running the analysis.
              </Alert>
            )}
          </Paper>
        </Box>
      )}

      <Box sx={{ mt: 4, display: 'flex', gap: 2 }}>
        <Button
          variant="contained"
          color="primary"
          size="large"
          onClick={handleRunAnalysis}
          disabled={loading || measurements.length === 0 || (analysisType === 'bayesian' && !!cycle)}
        >
          {loading ? 'Running...' : 'Run Analysis'}
        </Button>
      </Box>

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
            formatAge={(age) => formatCalendarAge(age, dateFormat)}
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
