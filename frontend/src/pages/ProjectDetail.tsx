import { useState, useEffect } from 'react'
import { useParams, useNavigate, Link as RouterLink } from 'react-router-dom'
import {
  Container,
  Box,
  Paper,
  Typography,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  CircularProgress,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  IconButton,
  Alert,
  Tabs,
  Tab,
  Tooltip,
} from '@mui/material'
import EditIcon from '@mui/icons-material/Edit'
import DeleteIcon from '@mui/icons-material/Delete'
import AddIcon from '@mui/icons-material/Add'
import DriveFileMoveIcon from '@mui/icons-material/DriveFileMove'
import ReplayIcon from '@mui/icons-material/Replay'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import { formatApiError } from '@/utils/apiError'
import { useNotify } from '@/contexts/NotificationContext'
import ConfirmDialog from '@/components/ConfirmDialog'
import AssignProjectDialog from '@/components/AssignProjectDialog'
import ProjectSampleTable from '@/components/ProjectSampleTable'

interface AnalysisSummary {
  id: string
  name: string
  analysis_type: string
  status: string
  created_at: string
  measurements_count: number
}

interface ProjectDetailData {
  id: string
  name: string
  description: string | null
  created_at: string
  updated_at: string
  analyses: AnalysisSummary[]
}

// The result of a "re-run all" pass, once one has run -- a per-analysis
// batch, so unlike a single failed request this can carry several distinct
// error messages at once. Shown as a dismissible in-page Alert rather than
// through the toast queue: a toast is sized and timed for one line, and
// forcing "Wk-50104: sampler did not converge, Wk-50099: ..." through that
// would either truncate the list or block behind it whatever else the
// queue was already holding.
interface RerunSummary {
  severity: 'success' | 'warning' | 'error'
  headline: string
  details?: string[]
}

export default function ProjectDetail() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const notify = useNotify()
  const isUnassigned = projectId === 'unassigned'

  const [project, setProject] = useState<ProjectDetailData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [editOpen, setEditOpen] = useState(false)
  const [editName, setEditName] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [saving, setSaving] = useState(false)

  const [deleteProjectOpen, setDeleteProjectOpen] = useState(false)
  const [deletingProject, setDeletingProject] = useState(false)
  const [deleteAnalysisTarget, setDeleteAnalysisTarget] = useState<AnalysisSummary | null>(null)
  const [deletingAnalysis, setDeletingAnalysis] = useState(false)

  // 'analyses' | 'dates' -- the project's date collection lives on its own
  // tab (see ProjectSampleTable). The Unassigned pseudo-project isn't a real
  // project row, so it has no collection and the tabs are hidden for it.
  const [tab, setTab] = useState<'analyses' | 'dates'>('analyses')
  const [duplicating, setDuplicating] = useState(false)

  const [assignTarget, setAssignTarget] = useState<AnalysisSummary | null>(null)
  const [rerunTarget, setRerunTarget] = useState<AnalysisSummary | null>(null)
  const [rerunningOne, setRerunningOne] = useState(false)
  const [rerunAllOpen, setRerunAllOpen] = useState(false)
  const [rerunningAll, setRerunningAll] = useState(false)
  const [rerunSummary, setRerunSummary] = useState<RerunSummary | null>(null)

  useEffect(() => {
    if (projectId) fetchProject(projectId)
  }, [projectId])

  const fetchProject = async (id: string) => {
    setLoading(true)
    setError(null)
    try {
      if (id === 'unassigned') {
        const response = await fetch('/api/analysis?unassigned=true&per_page=100')
        const data = await response.json()
        if (data.success) {
          setProject({
            id: 'unassigned',
            name: 'Unassigned',
            description: 'Analyses not filed under any project',
            created_at: '',
            updated_at: '',
            analyses: data.data.items,
          })
        } else {
          setError('Failed to load unassigned analyses')
        }
      } else {
        const response = await fetch(`/api/projects/${id}`)
        if (response.status === 404) {
          setError('Project not found')
          return
        }
        const data = await response.json()
        if (data.success) {
          setProject(data.data)
        } else {
          setError('Failed to load project')
        }
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const openEdit = () => {
    if (!project) return
    setEditName(project.name)
    setEditDescription(project.description || '')
    setEditOpen(true)
  }

  const handleSaveEdit = async () => {
    if (!project || !editName.trim()) return
    setSaving(true)
    try {
      const response = await fetch(`/api/projects/${project.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: editName.trim(), description: editDescription.trim() || null }),
      })
      const data = await response.json()
      if (data.success) {
        setEditOpen(false)
        fetchProject(project.id)
      } else {
        notify('Failed to update project: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error updating project: ' + (err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteProject = async () => {
    if (!project) return
    setDeletingProject(true)
    try {
      const response = await fetch(`/api/projects/${project.id}`, { method: 'DELETE' })
      const data = await response.json()
      if (data.success) {
        navigate('/projects')
      } else {
        notify('Failed to delete project: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error deleting project: ' + (err as Error).message)
    } finally {
      setDeletingProject(false)
    }
  }

  const handleDeleteAnalysis = async () => {
    if (!deleteAnalysisTarget || !project) return
    setDeletingAnalysis(true)
    try {
      const response = await fetch(`/api/analysis/${deleteAnalysisTarget.id}`, { method: 'DELETE' })
      const data = await response.json()
      if (data.success) {
        setDeleteAnalysisTarget(null)
        fetchProject(project.id)
      } else {
        notify('Failed to delete analysis: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error deleting analysis: ' + (err as Error).message)
    } finally {
      setDeletingAnalysis(false)
    }
  }

  // Copy an analysis's inputs into a new one and open it for editing --
  // "same dates, different model" without re-entering anything.
  const handleDuplicate = async (analysis: AnalysisSummary) => {
    setDuplicating(true)
    try {
      const response = await fetch(`/api/analysis/${analysis.id}/duplicate`, { method: 'POST' })
      const data = await response.json()
      if (data.success) {
        navigate(`/analysis/${data.data.id}/edit`)
      } else {
        notify('Failed to duplicate analysis: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error duplicating analysis: ' + (err as Error).message)
    } finally {
      setDuplicating(false)
    }
  }

  const handleRerunOne = async () => {
    if (!rerunTarget || !project) return
    setRerunningOne(true)
    try {
      const response = await fetch(`/api/analysis/${rerunTarget.id}/rerun`, { method: 'POST' })
      const data = await response.json()
      if (data.success) {
        setRerunTarget(null)
        fetchProject(project.id)
      } else {
        notify('Failed to rerun analysis: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error rerunning analysis: ' + (err as Error).message)
    } finally {
      setRerunningOne(false)
    }
  }

  // Real project: one batch call. Unassigned pseudo-project: there's no
  // real project id to hit a batch endpoint with, so rerun each listed
  // analysis individually instead -- same end result, just client-side
  // fan-out rather than a single request.
  const handleRerunAll = async () => {
    if (!project) return
    setRerunningAll(true)
    setRerunSummary(null)
    try {
      if (isUnassigned) {
        const outcomes = await Promise.allSettled(
          project.analyses.map((a) => fetch(`/api/analysis/${a.id}/rerun`, { method: 'POST' }).then((r) => r.json()))
        )
        const failed = outcomes.filter((o) => o.status === 'rejected' || !o.value?.success)
        if (failed.length > 0) {
          setRerunSummary({
            severity: 'warning',
            headline: `Rerun finished with ${failed.length} of ${outcomes.length} analyses failing. Check them individually for details.`,
          })
        }
      } else {
        const response = await fetch(`/api/projects/${project.id}/rerun`, { method: 'POST' })
        const data = await response.json()
        if (data.success && data.data.failed.length > 0) {
          setRerunSummary({
            severity: 'warning',
            headline: `${data.data.rerun_count} rerun successfully. ${data.data.failed.length} failed:`,
            details: data.data.failed.map((f: { name: string; error: string }) => `${f.name}: ${f.error}`),
          })
        } else if (!data.success) {
          notify('Failed to rerun project: ' + formatApiError(data))
        }
      }
      setRerunAllOpen(false)
      fetchProject(project.id)
    } catch (err) {
      notify('Error rerunning project: ' + (err as Error).message)
    } finally {
      setRerunningAll(false)
    }
  }

  if (loading) {
    return (
      <Container sx={{ py: 4, display: 'flex', justifyContent: 'center' }}>
        <CircularProgress />
      </Container>
    )
  }

  if (error || !project) {
    return (
      <Container maxWidth="lg" sx={{ py: 4 }}>
        <Typography color="error" variant="h6">
          {error || 'Project not found'}
        </Typography>
        <Button onClick={() => navigate('/projects')} sx={{ mt: 2 }}>
          Back to Projects
        </Button>
      </Container>
    )
  }

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 4, flexWrap: 'wrap', gap: 2 }}>
        <Box>
          <Typography variant="h3" component="h1" gutterBottom>
            {project.name}
          </Typography>
          {project.description && (
            <Typography variant="body1" color="textSecondary">
              {project.description}
            </Typography>
          )}
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            component={RouterLink}
            to={isUnassigned ? '/new-analysis' : `/new-analysis?project=${project.id}`}
          >
            New Analysis
          </Button>
          {project.analyses.length > 0 && (
            <Button
              variant="outlined"
              startIcon={<ReplayIcon />}
              onClick={() => setRerunAllOpen(true)}
            >
              Re-run All
            </Button>
          )}
          {!isUnassigned && (
            <>
              <Tooltip title="Edit project">
                <IconButton onClick={openEdit} aria-label="Edit project">
                  <EditIcon />
                </IconButton>
              </Tooltip>
              <Tooltip title="Delete project">
                <IconButton
                  onClick={() => setDeleteProjectOpen(true)}
                  aria-label="Delete project"
                  sx={{ '&:hover, &:focus-visible': { color: 'error.main' } }}
                >
                  <DeleteIcon />
                </IconButton>
              </Tooltip>
            </>
          )}
        </Box>
      </Box>

      {rerunSummary && (
        <Alert severity={rerunSummary.severity} onClose={() => setRerunSummary(null)} sx={{ mb: 3 }}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {rerunSummary.headline}
          </Typography>
          {rerunSummary.details && (
            <Box component="ul" sx={{ m: 0, mt: 0.5, pl: 2.5 }}>
              {rerunSummary.details.map((line, i) => (
                <li key={i}>
                  <Typography variant="body2">{line}</Typography>
                </li>
              ))}
            </Box>
          )}
        </Alert>
      )}

      {!isUnassigned && (
        <Tabs value={tab} onChange={(_e, v) => setTab(v)} sx={{ mb: 2 }}>
          <Tab label={`Analyses (${project.analyses.length})`} value="analyses" />
          <Tab label="Dates" value="dates" />
        </Tabs>
      )}

      {tab === 'dates' && !isUnassigned ? (
        <Paper sx={{ p: 3 }}>
          <Typography variant="h5" gutterBottom>
            Date Collection
          </Typography>
          <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
            This project's radiocarbon dates. Enter them once here, then deploy any selection into a
            new analysis — the same dates can feed several analyses, each modelling them under
            different criteria. Deployed dates are copies, so editing one here never alters an
            analysis already created from it.
          </Typography>
          <ProjectSampleTable projectId={project.id} />
        </Paper>
      ) : (
      <Paper sx={{ p: 3 }}>
        <Typography variant="h5" gutterBottom>
          Analyses ({project.analyses.length})
        </Typography>

        {project.analyses.length === 0 ? (
          <Alert severity="info">
            No analyses in {isUnassigned ? 'the unassigned list' : 'this project'} yet.
          </Alert>
        ) : (
          <TableContainer>
            <Table>
              <TableHead>
                <TableRow sx={{ backgroundColor: 'grey.100' }}>
                  <TableCell>Name</TableCell>
                  <TableCell>Type</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell align="right">Samples</TableCell>
                  <TableCell align="right">Created</TableCell>
                  <TableCell align="right" />
                </TableRow>
              </TableHead>
              <TableBody>
                {project.analyses.map((a) => (
                  <TableRow
                    key={a.id}
                    hover
                    sx={{ cursor: 'pointer' }}
                    onClick={() => navigate(`/results/${a.id}`)}
                  >
                    <TableCell>{a.name}</TableCell>
                    <TableCell>{a.analysis_type}</TableCell>
                    <TableCell>{a.status}</TableCell>
                    <TableCell align="right">{a.measurements_count}</TableCell>
                    <TableCell align="right">{new Date(a.created_at).toLocaleDateString()}</TableCell>
                    <TableCell align="right">
                      {isUnassigned && (
                        <IconButton
                          size="small"
                          aria-label={`Assign ${a.name} to a project`}
                          title="Assign to project"
                          onClick={(e) => {
                            e.stopPropagation()
                            setAssignTarget(a)
                          }}
                        >
                          <DriveFileMoveIcon fontSize="small" />
                        </IconButton>
                      )}
                      <IconButton
                        size="small"
                        aria-label={`Duplicate ${a.name}`}
                        title="Duplicate and edit"
                        disabled={duplicating}
                        onClick={(e) => {
                          e.stopPropagation()
                          handleDuplicate(a)
                        }}
                      >
                        <ContentCopyIcon fontSize="small" />
                      </IconButton>
                      <IconButton
                        size="small"
                        aria-label={`Edit ${a.name}`}
                        title="Edit inputs"
                        onClick={(e) => {
                          e.stopPropagation()
                          navigate(`/analysis/${a.id}/edit`)
                        }}
                      >
                        <EditIcon fontSize="small" />
                      </IconButton>
                      <IconButton
                        size="small"
                        aria-label={`Re-run ${a.name}`}
                        title="Re-run"
                        onClick={(e) => {
                          e.stopPropagation()
                          setRerunTarget(a)
                        }}
                      >
                        <ReplayIcon fontSize="small" />
                      </IconButton>
                      <IconButton
                        size="small"
                        aria-label={`Delete ${a.name}`}
                        title="Delete"
                        onClick={(e) => {
                          e.stopPropagation()
                          setDeleteAnalysisTarget(a)
                        }}
                        sx={{ '&:hover, &:focus-visible': { color: 'error.main' } }}
                      >
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
      )}

      <Box sx={{ mt: 4 }}>
        <Button onClick={() => navigate('/projects')} variant="outlined">
          Back to Projects
        </Button>
      </Box>

      <Dialog open={editOpen} onClose={() => setEditOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Edit Project</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            label="Project Name"
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
            sx={{ mt: 1, mb: 2 }}
          />
          <TextField
            fullWidth
            multiline
            minRows={2}
            label="Description (optional)"
            value={editDescription}
            onChange={(e) => setEditDescription(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleSaveEdit} disabled={saving || !editName.trim()}>
            {saving ? 'Saving...' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={deleteProjectOpen}
        title="Delete project?"
        message={`Delete "${project.name}"? Its analyses will be kept and moved to Unassigned -- only the project itself is removed.`}
        confirmLabel="Delete"
        danger
        loading={deletingProject}
        onConfirm={handleDeleteProject}
        onCancel={() => setDeleteProjectOpen(false)}
      />

      <ConfirmDialog
        open={!!deleteAnalysisTarget}
        title="Delete analysis?"
        message={
          deleteAnalysisTarget
            ? `Permanently delete "${deleteAnalysisTarget.name}"? This removes its samples and results and cannot be undone.`
            : ''
        }
        confirmLabel="Delete"
        danger
        loading={deletingAnalysis}
        onConfirm={handleDeleteAnalysis}
        onCancel={() => setDeleteAnalysisTarget(null)}
      />

      <ConfirmDialog
        open={!!rerunTarget}
        title="Re-run this analysis?"
        message={
          rerunTarget
            ? `Recompute results for "${rerunTarget.name}" from its existing inputs and replace its current results. This can't be undone.`
            : ''
        }
        confirmLabel="Re-run"
        loading={rerunningOne}
        onConfirm={handleRerunOne}
        onCancel={() => setRerunTarget(null)}
      />

      <ConfirmDialog
        open={rerunAllOpen}
        title="Re-run all analyses?"
        message={`Recompute results for all ${project.analyses.length} analyses in ${isUnassigned ? 'the unassigned list' : `"${project.name}"`} from their existing inputs, replacing their current results. This can't be undone and may take a while.`}
        confirmLabel="Re-run All"
        loading={rerunningAll}
        onConfirm={handleRerunAll}
        onCancel={() => setRerunAllOpen(false)}
      />

      {assignTarget && (
        <AssignProjectDialog
          open={!!assignTarget}
          analysisId={assignTarget.id}
          currentProjectId={null}
          onClose={() => setAssignTarget(null)}
          onAssigned={() => {
            setAssignTarget(null)
            fetchProject(project.id)
          }}
        />
      )}
    </Container>
  )
}
