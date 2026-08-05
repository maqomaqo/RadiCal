import React, { useState, useEffect, useMemo } from 'react'
import { useParams, useNavigate, Link as RouterLink } from 'react-router-dom'
import {
  Container,
  Box,
  Paper,
  Typography,
  Button,
  Grid,
  Card,
  CardContent,
  CircularProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Alert,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  SelectChangeEvent,
  FormGroup,
  FormControlLabel,
  Checkbox,
  Chip,
  Stack,
} from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import AccountTreeIcon from '@mui/icons-material/AccountTree'
import DeleteIcon from '@mui/icons-material/Delete'
import ReplayIcon from '@mui/icons-material/Replay'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import EditIcon from '@mui/icons-material/Edit'
import PosteriorPlot from '@/components/PosteriorPlot'
import HarrisMatrixDiagram from '@/components/HarrisMatrixDiagram'
import ConfirmDialog from '@/components/ConfirmDialog'
import AssignProjectDialog from '@/components/AssignProjectDialog'
import { formatApiError } from '@/utils/apiError'
import { treeToMatrix } from '@/utils/stratigraphy'
import {
  DateFormat,
  DATE_FORMAT_OPTIONS,
  formatCalendarAge,
  formatCalendarRange,
  formatHPDRanges,
  loadDateFormatPreference,
  saveDateFormatPreference,
  HPDRange,
} from '@/utils/dateFormat'
import { hpdRangesFromCurve, hpdEnvelopeFromHistogram } from '@/utils/hpd'
import type { MatrixMeasurement, DraftGroup, DraftRelationship, ModelElement, PosteriorSummary } from '@/types'

interface AnalysisResult {
  id: string
  name: string
  analysis_type: string
  status: string
  created_at: string
  measurements_count: number
  project_id?: string | null
  project_name?: string | null
  // The samples, groups, and stratigraphic relationships actually used --
  // present for analyses saved after the stratigraphy feature was added;
  // older stored analyses will have empty groups/relationships (there
  // were none to persist) but still have measurements.
  measurements?: MatrixMeasurement[]
  groups?: DraftGroup[]
  // Explicit model structure; boundary elements carry their own posterior.
  model_tree?: ModelElement | null
  relationships?: DraftRelationship[]
  results: Array<{
    sample_id: string
    sample_name?: string | null
    posterior_mean?: number
    posterior_median?: number
    posterior_std?: number
    hpd_68?: [number, number]
    hpd_95?: [number, number]
    // Full HPD breakdown, one or more disjoint ranges with their own
    // probability share -- present for analyses computed after this
    // reporting was added; older stored results may only have hpd_68/95.
    hpd_68_ranges?: HPDRange[]
    hpd_95_ranges?: HPDRange[]
    probability_distribution?: Array<[number, number]>
    // The plain/unmodelled calibration for this sample (no stratigraphic
    // constraints applied) -- present only for a Bayesian analysis (see
    // GET /api/analysis/:id), so "unmodelled vs modelled" can be
    // compared side by side. For a calibration-only analysis,
    // probability_distribution above already *is* the unmodelled result.
    unmodelled_probability_distribution?: Array<[number, number]> | null
  }>
}

// The three standard Gaussian-equivalent confidence levels radiocarbon
// results are conventionally reported at (1/2/3-sigma) -- OxCal's own
// convention, offered here as a single user choice rather than always
// showing the fixed 68%/95% pair.
const CONFIDENCE_OPTIONS = [
  { value: 0.682, label: '68.2%' },
  { value: 0.954, label: '95.4%' },
  { value: 0.997, label: '99.7%' },
] as const

export default function Results() {
  const { analysisId } = useParams<{ analysisId: string }>()
  const navigate = useNavigate()
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dateFormat, setDateFormat] = useState<DateFormat>(() =>
    analysisId ? loadDateFormatPreference(analysisId) : 'calBP'
  )
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [assignOpen, setAssignOpen] = useState(false)
  const [rerunOpen, setRerunOpen] = useState(false)
  const [rerunning, setRerunning] = useState(false)
  const [duplicating, setDuplicating] = useState(false)
  // Posterior mean/median are opt-in reporting choices, not shown by
  // default -- HPD ranges are the primary convention for reporting a
  // (possibly multimodal) calibrated/Bayesian result; a single point
  // estimate can be misleading on its own (a "mean" can fall in a
  // low-probability gap between two peaks).
  const [showMean, setShowMean] = useState(false)
  const [showMedian, setShowMedian] = useState(false)
  // Confidence level for the HPD columns below -- default 95.4% (2-sigma),
  // the most commonly cited "95% confidence" convention in archaeological
  // reporting; switchable to 68.2% or 99.7% without a network round trip
  // (see utils/hpd.ts).
  const [confidence, setConfidence] = useState<number>(0.954)

  const handleDateFormatChange = (e: SelectChangeEvent) => {
    const next = e.target.value as DateFormat
    setDateFormat(next)
    if (analysisId) {
      saveDateFormatPreference(analysisId, next)
    }
  }

  useEffect(() => {
    if (analysisId) {
      fetchAnalysis(analysisId)
    }
  }, [analysisId])

  const fetchAnalysis = async (id: string) => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch(`/api/analysis/${id}`)
      const data = await response.json()
      if (data.success) {
        setAnalysis(data.data)
      } else {
        setError('Failed to load analysis')
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const handleExportCSV = async () => {
    if (!analysisId) return
    try {
      const response = await fetch(`/api/results/${analysisId}/export/csv`)
      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `analysis-${analysisId}.csv`
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)
    } catch (err) {
      alert('Failed to export CSV: ' + (err as Error).message)
    }
  }

  const handleDelete = async () => {
    if (!analysisId) return
    setDeleting(true)
    try {
      const response = await fetch(`/api/analysis/${analysisId}`, { method: 'DELETE' })
      const data = await response.json()
      if (data.success) {
        navigate(analysis?.project_id ? `/projects/${analysis.project_id}` : '/projects')
      } else {
        alert('Failed to delete analysis: ' + formatApiError(data))
      }
    } catch (err) {
      alert('Error deleting analysis: ' + (err as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  // Copy this analysis's inputs into a new one and open it for editing --
  // the "try the same dates under a different model" path, which leaves
  // this analysis and its results untouched.
  const handleDuplicate = async () => {
    if (!analysisId) return
    setDuplicating(true)
    try {
      const response = await fetch(`/api/analysis/${analysisId}/duplicate`, { method: 'POST' })
      const data = await response.json()
      if (data.success) {
        navigate(`/analysis/${data.data.id}/edit`)
      } else {
        alert('Failed to duplicate analysis: ' + formatApiError(data))
      }
    } catch (err) {
      alert('Error duplicating analysis: ' + (err as Error).message)
    } finally {
      setDuplicating(false)
    }
  }

  const handleRerun = async () => {
    if (!analysisId) return
    setRerunning(true)
    try {
      const response = await fetch(`/api/analysis/${analysisId}/rerun`, { method: 'POST' })
      const data = await response.json()
      if (data.success) {
        setRerunOpen(false)
        fetchAnalysis(analysisId)
      } else {
        alert('Failed to rerun analysis: ' + formatApiError(data))
      }
    } catch (err) {
      alert('Error rerunning analysis: ' + (err as Error).message)
    } finally {
      setRerunning(false)
    }
  }

  // What the Harris matrix further down draws. Derived from the model tree
  // the same way the editor derives it, because the ordering lives in the
  // tree: a tree-authored analysis has no SampleGroup or relationship rows
  // at all, so reading those directly drew unordered phases containing no
  // dates. Falls back to the stored contexts for analyses predating trees.
  //
  // Memoised because HarrisMatrixDiagram memoises its flow nodes/edges on
  // the identity of these arrays and syncs them into reactflow state via an
  // effect -- rebuilding them each render would loop that effect forever,
  // leaving every node stuck unmeasured (invisible) and no edges drawn.
  const structureView = useMemo(() => {
    const measurements = analysis?.measurements || []
    if (analysis?.model_tree) {
      const assignments = Object.fromEntries(
        measurements
          .map((m) => [m.id, m.element_id || m.group_id])
          .filter(([, elementId]) => !!elementId) as [string, string][]
      )
      return treeToMatrix(analysis.model_tree, measurements, assignments)
    }
    return {
      measurements,
      groups: analysis?.groups || [],
      relationships: analysis?.relationships || [],
    }
  }, [analysis])

  if (loading) {
    return (
      <Container sx={{ py: 4, display: 'flex', justifyContent: 'center' }}>
        <CircularProgress />
      </Container>
    )
  }

  if (error || !analysis) {
    return (
      <Container maxWidth="lg" sx={{ py: 4 }}>
        <Typography color="error" variant="h6">
          {error || 'Analysis not found'}
        </Typography>
        <Button onClick={() => navigate('/')} sx={{ mt: 2 }}>
          Back to Dashboard
        </Button>
      </Container>
    )
  }

  // Which context (if any) each sample belongs to -- both maps come
  // straight from data GET /api/analysis/:id already returns (measurement
  // element_id/group_id, group id/name), just cross-referenced here so the
  // table and each plot can show a sample's context without re-deriving it
  // per row.
  //
  // element_id first: a tree-authored analysis has no contexts at all, so
  // its dates carry the phase they sit in on element_id and leave group_id
  // null -- reading group_id alone reported every date as "Unassigned".
  // group_id remains the fallback for a tree migrated from the old
  // contexts, where each phase reuses its context's id.
  const groupNameById = new Map((analysis.groups || []).map((g) => [g.id, g.name]))
  const groupIdBySampleId = new Map(
    (analysis.measurements || []).map((m) => [m.id, m.element_id || m.group_id])
  )
  const contextNameFor = (sampleId: string): string | null => {
    const groupId = groupIdBySampleId.get(sampleId)
    return groupId ? groupNameById.get(groupId) || null : null
  }
  const hasContexts = (analysis.groups || []).length > 0
  const contextsWithModeledSpan = (analysis.groups || []).filter((g) => g.modeled_start && g.modeled_end)

  // Every named Boundary in the model structure, in the order it appears
  // (oldest first). Preferred over the per-context table below when the
  // analysis has an explicit model tree, since adjacent contexts SHARE a
  // boundary -- listing them per context would print the same transition
  // twice, once as one context's end and again as the next one's start.
  const boundaryRows: Array<{ id: string; name: string; posterior: PosteriorSummary }> = []
  const collectBoundaries = (element?: ModelElement | null) => {
    if (!element) return
    if (element.element_type === 'boundary' && element.posterior) {
      boundaryRows.push({
        id: element.id,
        name: element.name || 'Boundary',
        posterior: element.posterior,
      })
    }
    for (const child of element.children || []) collectBoundaries(child)
  }
  collectBoundaries(analysis.model_tree)
  const isBayesian = analysis.analysis_type === 'bayesian'
  const CONFIDENCE_LABEL = CONFIDENCE_OPTIONS.find((o) => o.value === confidence)?.label ?? ''

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Box sx={{ mb: 4 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 2 }}>
          <Box>
            <Typography variant="h3" component="h1" gutterBottom>
              {analysis.name}
            </Typography>
            <Typography variant="body1" color="textSecondary" gutterBottom>
              Created: {new Date(analysis.created_at).toLocaleString()}
            </Typography>
            <Typography variant="body2" color="textSecondary">
              Project:{' '}
              {analysis.project_id ? (
                <RouterLink to={`/projects/${analysis.project_id}`}>{analysis.project_name}</RouterLink>
              ) : (
                'Unassigned'
              )}{' '}
              <Button size="small" onClick={() => setAssignOpen(true)} sx={{ minWidth: 0, py: 0 }}>
                Change
              </Button>
            </Typography>
          </Box>
          <Box sx={{ display: 'flex', gap: 2, alignItems: 'center' }}>
            <FormControl size="small" sx={{ minWidth: 220 }}>
              <InputLabel>Date Format</InputLabel>
              <Select value={dateFormat} label="Date Format" onChange={handleDateFormatChange}>
                {DATE_FORMAT_OPTIONS.map((option) => (
                  <MenuItem key={option.value} value={option.value}>
                    {option.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <Button
              startIcon={<EditIcon />}
              variant="outlined"
              onClick={() => navigate(`/analysis/${analysis.id}/edit`)}
            >
              Edit
            </Button>
            <Button
              startIcon={<ContentCopyIcon />}
              variant="outlined"
              disabled={duplicating}
              onClick={handleDuplicate}
            >
              {duplicating ? 'Copying...' : 'Duplicate'}
            </Button>
            <Button
              startIcon={<ReplayIcon />}
              variant="outlined"
              onClick={() => setRerunOpen(true)}
            >
              Re-run
            </Button>
            <Button
              startIcon={<DeleteIcon />}
              color="error"
              variant="outlined"
              onClick={() => setDeleteOpen(true)}
            >
              Delete
            </Button>
          </Box>
        </Box>
      </Box>

      <Grid container spacing={3} sx={{ mb: 3 }}>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="textSecondary" gutterBottom>
                Analysis Type
              </Typography>
              <Typography variant="h6">{analysis.analysis_type}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="textSecondary" gutterBottom>
                Status
              </Typography>
              <Typography variant="h6" sx={{ color: analysis.status === 'completed' ? 'green' : 'orange' }}>
                {analysis.status}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="textSecondary" gutterBottom>
                Samples
              </Typography>
              <Typography variant="h6">{analysis.measurements_count}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Card>
            <CardContent>
              <Typography color="textSecondary" gutterBottom>
                Results
              </Typography>
              <Typography variant="h6">{analysis.results.length}</Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Posterior Distribution Plots */}
      {analysis.results.map((result, idx) => {
        const contextName = contextNameFor(result.sample_id)
        return (
          <Box key={result.sample_id} sx={{ mb: 4 }}>
            {contextName && (
              <Chip size="small" icon={<AccountTreeIcon />} label={`Context: ${contextName}`} sx={{ mb: 1 }} />
            )}
            {result.probability_distribution && result.probability_distribution.length > 0 ? (
              <PosteriorPlot
                calAges={result.probability_distribution.map(([age]) => age)}
                probabilities={result.probability_distribution.map(([, prob]) => prob)}
                hpd68={result.hpd_68}
                hpd95={result.hpd_95}
                hpd68Ranges={result.hpd_68_ranges}
                hpd95Ranges={result.hpd_95_ranges}
                sampleName={result.sample_name || `Sample ${idx + 1}`}
                pointEstimates={{
                  mean: result.posterior_mean,
                  median: result.posterior_median,
                  std: result.posterior_std,
                }}
                showMean={showMean}
                showMedian={showMedian}
                formatAge={(age) => formatCalendarAge(age, dateFormat)}
              />
            ) : (
              <Alert severity="info">No posterior distribution data available for this sample</Alert>
            )}
          </Box>
        )
      })}

      {/* Model Structure -- read-only view of the groups/relationships/TPQ-TAQ
          this bayesian analysis actually used, if any. Doubles as the
          "run the Harris matrix as a check" step after the fact: even
          though the constraints already fed into the MCMC run above, this
          confirms visually that they were specified as intended. */}
      {analysis.analysis_type === 'bayesian' &&
        analysis.measurements &&
        (structureView.groups.length > 0 || structureView.relationships.length > 0) && (
          <Paper sx={{ p: 3, mb: 3 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
              <AccountTreeIcon color="action" />
              <Typography variant="h6">Model Structure used</Typography>
            </Box>
            <HarrisMatrixDiagram
              measurements={structureView.measurements}
              groups={structureView.groups}
              relationships={structureView.relationships}
              editable={false}
            />
          </Paper>
        )}

      {/* Context Spans -- the modelled start/end of each context's own
          duration, for every context with 2+ dated members. These are
          Boundary parameters in the same sense as OxCal's Boundary command
          (see services/bayesian.py's build_model): separate latent
          variables constrained to lie outside every dated member, so they
          may extend earlier/later than the oldest/youngest dated sample
          itself, not just report that sample's own posterior. */}
      {boundaryRows.length > 0 && (
        <Paper sx={{ p: 3, mb: 3 }}>
          <Typography variant="h6" gutterBottom>
            Boundaries
          </Typography>
          <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
            The modelled transitions in this analysis's structure, oldest first. A boundary
            between two phases is a single shared transition — the end of one and the start of the
            next — and the outermost boundaries may extend beyond every dated sample, since the
            dates are treated as a partial sample of the true duration.
          </Typography>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow sx={{ backgroundColor: '#f5f5f5' }}>
                  <TableCell>Boundary</TableCell>
                  {showMean && <TableCell align="right">Mean</TableCell>}
                  {showMedian && <TableCell align="right">Median</TableCell>}
                  <TableCell align="right">{CONFIDENCE_LABEL} HPD</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {boundaryRows.map((b) => {
                  const envelope = hpdEnvelopeFromHistogram(b.posterior.probability_distribution, confidence)
                  return (
                    <TableRow key={b.id}>
                      <TableCell>{b.name}</TableCell>
                      {showMean && (
                        <TableCell align="right">
                          {formatCalendarAge(b.posterior.posterior_mean, dateFormat)}
                        </TableCell>
                      )}
                      {showMedian && (
                        <TableCell align="right">
                          {formatCalendarAge(b.posterior.posterior_median, dateFormat)}
                        </TableCell>
                      )}
                      <TableCell align="right">
                        {envelope
                          ? formatCalendarRange(envelope[0], envelope[1], dateFormat)
                          : 'N/A'}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      )}

      {boundaryRows.length === 0 && contextsWithModeledSpan.length > 0 && (
        <Paper sx={{ p: 3, mb: 3 }}>
          <Typography variant="h6" gutterBottom>
            Context Spans
          </Typography>
          <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
            Modelled start and end date of each context (OxCal-style Boundary parameters) --
            these may extend beyond the oldest/youngest dated sample, since the dated samples
            are treated as a partial sample of the context's true duration.
          </Typography>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow sx={{ backgroundColor: '#f5f5f5' }}>
                  <TableCell>Context</TableCell>
                  {showMean && <TableCell align="right">Start Mean</TableCell>}
                  {showMedian && <TableCell align="right">Start Median</TableCell>}
                  <TableCell align="right">Modelled Start ({CONFIDENCE_LABEL} HPD)</TableCell>
                  {showMean && <TableCell align="right">End Mean</TableCell>}
                  {showMedian && <TableCell align="right">End Median</TableCell>}
                  <TableCell align="right">Modelled End ({CONFIDENCE_LABEL} HPD)</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {contextsWithModeledSpan.map((g) => {
                  const startEnvelope = hpdEnvelopeFromHistogram(g.modeled_start!.probability_distribution, confidence)
                  const endEnvelope = hpdEnvelopeFromHistogram(g.modeled_end!.probability_distribution, confidence)
                  return (
                    <TableRow key={g.id}>
                      <TableCell>{g.name}</TableCell>
                      {showMean && (
                        <TableCell align="right">
                          {formatCalendarAge(g.modeled_start!.posterior_mean, dateFormat)}
                        </TableCell>
                      )}
                      {showMedian && (
                        <TableCell align="right">
                          {formatCalendarAge(g.modeled_start!.posterior_median, dateFormat)}
                        </TableCell>
                      )}
                      <TableCell align="right">
                        {startEnvelope ? formatCalendarRange(startEnvelope[0], startEnvelope[1], dateFormat) : 'N/A'}
                      </TableCell>
                      {showMean && (
                        <TableCell align="right">
                          {formatCalendarAge(g.modeled_end!.posterior_mean, dateFormat)}
                        </TableCell>
                      )}
                      {showMedian && (
                        <TableCell align="right">
                          {formatCalendarAge(g.modeled_end!.posterior_median, dateFormat)}
                        </TableCell>
                      )}
                      <TableCell align="right">
                        {endEnvelope ? formatCalendarRange(endEnvelope[0], endEnvelope[1], dateFormat) : 'N/A'}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      )}

      {/* Results Table */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 2, mb: 2 }}>
          <Box>
            <Typography variant="h6" gutterBottom>
              Results Summary
            </Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ sm: 'center' }}>
              <FormControl size="small" sx={{ minWidth: 140 }}>
                <InputLabel>HPD Confidence</InputLabel>
                <Select
                  value={confidence}
                  label="HPD Confidence"
                  onChange={(e) => setConfidence(Number(e.target.value))}
                >
                  {CONFIDENCE_OPTIONS.map((opt) => (
                    <MenuItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <FormGroup row>
                <FormControlLabel
                  control={<Checkbox size="small" checked={showMean} onChange={(e) => setShowMean(e.target.checked)} />}
                  label="Show Mean"
                />
                <FormControlLabel
                  control={<Checkbox size="small" checked={showMedian} onChange={(e) => setShowMedian(e.target.checked)} />}
                  label="Show Median"
                />
              </FormGroup>
            </Stack>
          </Box>
          <Button
            startIcon={<DownloadIcon />}
            onClick={handleExportCSV}
            variant="outlined"
          >
            Export CSV
          </Button>
        </Box>

        <TableContainer>
          <Table>
            <TableHead>
              <TableRow sx={{ backgroundColor: '#f5f5f5' }}>
                <TableCell>Sample</TableCell>
                {hasContexts && <TableCell>Context</TableCell>}
                {showMean && <TableCell align="right">Posterior Mean</TableCell>}
                {showMean && <TableCell align="right">Posterior Std (yr)</TableCell>}
                {showMedian && <TableCell align="right">Posterior Median</TableCell>}
                {isBayesian && <TableCell align="right">Unmodelled Calibration</TableCell>}
                <TableCell align="right">
                  {isBayesian ? 'Posterior' : 'Calibrated'} {CONFIDENCE_LABEL} HPD Range
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {analysis.results.map((result, idx) => {
                const unmodelledRanges = isBayesian
                  ? hpdRangesFromCurve(result.unmodelled_probability_distribution, confidence)
                  : []
                const modelledEnvelope = hpdEnvelopeFromHistogram(result.probability_distribution, confidence)
                return (
                  <TableRow key={result.sample_id}>
                    <TableCell>{result.sample_name || `Sample ${idx + 1}`}</TableCell>
                    {hasContexts && <TableCell>{contextNameFor(result.sample_id) || 'Unassigned'}</TableCell>}
                    {showMean && (
                      <TableCell align="right">
                        {result.posterior_mean != null
                          ? formatCalendarAge(result.posterior_mean, dateFormat)
                          : 'N/A'}
                      </TableCell>
                    )}
                    {showMean && (
                      <TableCell align="right">
                        {result.posterior_std?.toFixed(0) ?? 'N/A'}
                      </TableCell>
                    )}
                    {showMedian && (
                      <TableCell align="right">
                        {result.posterior_median != null
                          ? formatCalendarAge(result.posterior_median, dateFormat)
                          : 'N/A'}
                      </TableCell>
                    )}
                    {isBayesian && (
                      <TableCell align="right">
                        {unmodelledRanges.length > 0 ? formatHPDRanges(unmodelledRanges, dateFormat) : 'N/A'}
                      </TableCell>
                    )}
                    <TableCell align="right">
                      {modelledEnvelope ? formatCalendarRange(modelledEnvelope[0], modelledEnvelope[1], dateFormat) : 'N/A'}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      <Box sx={{ mt: 4 }}>
        <Button onClick={() => navigate('/')} variant="outlined">
          Back to Dashboard
        </Button>
      </Box>

      <ConfirmDialog
        open={deleteOpen}
        title="Delete analysis?"
        message={`Permanently delete "${analysis.name}"? This removes its samples and results and cannot be undone.`}
        confirmLabel="Delete"
        danger
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setDeleteOpen(false)}
      />

      <ConfirmDialog
        open={rerunOpen}
        title="Re-run this analysis?"
        message="Recomputes results from this analysis's existing inputs and replaces its current results. This can't be undone."
        confirmLabel="Re-run"
        loading={rerunning}
        onConfirm={handleRerun}
        onCancel={() => setRerunOpen(false)}
      />

      <AssignProjectDialog
        open={assignOpen}
        analysisId={analysis.id}
        currentProjectId={analysis.project_id ?? null}
        onClose={() => setAssignOpen(false)}
        onAssigned={() => analysisId && fetchAnalysis(analysisId)}
      />
    </Container>
  )
}
