import React, { useState, useEffect } from 'react'
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
} from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import AccountTreeIcon from '@mui/icons-material/AccountTree'
import DeleteIcon from '@mui/icons-material/Delete'
import PosteriorPlot from '@/components/PosteriorPlot'
import HarrisMatrixDiagram from '@/components/HarrisMatrixDiagram'
import ConfirmDialog from '@/components/ConfirmDialog'
import { formatApiError } from '@/utils/apiError'
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
import type { MatrixMeasurement, DraftGroup, DraftRelationship } from '@/types'

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
  }>
}

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
              )}
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
      {analysis.results.map((result, idx) => (
        <Box key={result.sample_id} sx={{ mb: 4 }}>
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
              formatAge={(age) => formatCalendarAge(age, dateFormat)}
            />
          ) : (
            <Alert severity="info">No posterior distribution data available for this sample</Alert>
          )}
        </Box>
      ))}

      {/* Stratigraphy -- read-only view of the groups/relationships/TPQ-TAQ
          this bayesian analysis actually used, if any. Doubles as the
          "run the Harris matrix as a check" step after the fact: even
          though the constraints already fed into the MCMC run above, this
          confirms visually that they were specified as intended. */}
      {analysis.analysis_type === 'bayesian' &&
        analysis.measurements &&
        ((analysis.relationships && analysis.relationships.length > 0) || (analysis.groups && analysis.groups.length > 0)) && (
          <Paper sx={{ p: 3, mb: 3 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
              <AccountTreeIcon color="action" />
              <Typography variant="h6">Stratigraphy used</Typography>
            </Box>
            <HarrisMatrixDiagram
              measurements={analysis.measurements}
              groups={analysis.groups || []}
              relationships={analysis.relationships || []}
              editable={false}
            />
          </Paper>
        )}

      {/* Results Table */}
      <Paper sx={{ p: 3, mb: 3 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
          <Typography variant="h6">Results Summary</Typography>
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
                <TableCell align="right">Posterior Mean</TableCell>
                <TableCell align="right">Posterior Std (yr)</TableCell>
                <TableCell align="right">68% HPD Range</TableCell>
                <TableCell align="right">95% HPD Range</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {analysis.results.map((result, idx) => (
                <TableRow key={result.sample_id}>
                  <TableCell>{result.sample_name || `Sample ${idx + 1}`}</TableCell>
                  <TableCell align="right">
                    {result.posterior_mean != null
                      ? formatCalendarAge(result.posterior_mean, dateFormat)
                      : 'N/A'}
                  </TableCell>
                  <TableCell align="right">
                    {result.posterior_std?.toFixed(0) ?? 'N/A'}
                  </TableCell>
                  <TableCell align="right">
                    {result.hpd_68_ranges && result.hpd_68_ranges.length > 0
                      ? formatHPDRanges(result.hpd_68_ranges, dateFormat)
                      : result.hpd_68
                        ? formatCalendarRange(result.hpd_68[0], result.hpd_68[1], dateFormat)
                        : 'N/A'}
                  </TableCell>
                  <TableCell align="right">
                    {result.hpd_95_ranges && result.hpd_95_ranges.length > 0
                      ? formatHPDRanges(result.hpd_95_ranges, dateFormat)
                      : result.hpd_95
                        ? formatCalendarRange(result.hpd_95[0], result.hpd_95[1], dateFormat)
                        : 'N/A'}
                  </TableCell>
                </TableRow>
              ))}
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
    </Container>
  )
}
