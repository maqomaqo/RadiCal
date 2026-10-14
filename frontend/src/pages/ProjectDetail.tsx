import { useState, useEffect, useMemo } from 'react'
import { useParams, useNavigate, useLocation, Link as RouterLink } from 'react-router-dom'
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
  Checkbox,
  Link,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Divider,
  FormControlLabel,
  Switch,
} from '@mui/material'
import EditIcon from '@mui/icons-material/Edit'
import MoreVertIcon from '@mui/icons-material/MoreVert'
import DeleteIcon from '@mui/icons-material/Delete'
import AddIcon from '@mui/icons-material/Add'
import DriveFileMoveIcon from '@mui/icons-material/DriveFileMove'
import ReplayIcon from '@mui/icons-material/Replay'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import CompareArrowsIcon from '@mui/icons-material/CompareArrows'
import DownloadIcon from '@mui/icons-material/Download'
import { formatApiError, describeFetchError } from '@/utils/apiError'
import { saveDownload } from '@/utils/saveDownload'
import { describeRunChange, type RunDiff } from '@/utils/runDiff'
import { useNotify } from '@/contexts/NotificationContext'
import ConfirmDialog from '@/components/ConfirmDialog'
import AssignProjectDialog from '@/components/AssignProjectDialog'
import ProjectSampleTable from '@/components/ProjectSampleTable'
import JobProgress from '@/components/JobProgress'
import { cancelJob } from '@/api/jobs'
import { useJob } from '@/hooks/useJob'
import { rememberProjectMapTiles } from '@/hooks/useProjectMapTiles'
import { MAX_COMPARED_RUNS } from '@/theme/compareColors'
import StatusStrip, { statusSummary } from '@/components/StatusStrip'
import type { StatusCounts } from '@/components/StatusStrip'
import { relativeTime, absoluteTime } from '@/utils/serverTime'
import BackLink from '@/components/BackLink'
import { analysisTypeLabel } from '@/utils/analysisTypes'
import { COORDINATE_PRECISION_OPTIONS } from '@/utils/coordinates'
import type { CoordinatePrecision } from '@/utils/coordinates'

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
  /** Latest of the project's own edit and any of its analyses' -- see api/projects.get_project. */
  last_activity: string | null
  /** Dates in the project's reusable collection, not the count deployed into analyses. */
  samples_count: number
  /**
   * How precisely this project's coordinates may LEAVE the app -- 'full', or
   * the size of the cell they are rounded into ('0.01', '0.1', '1'). It
   * governs every export and every rendered figure and nothing else; what is
   * stored, and what this page shows, stays full precision.
   */
  coordinate_precision: CoordinatePrecision
  /** Whether the map may fetch raster basemap tiles. Off by default. */
  map_tiles_enabled: boolean
  analyses: AnalysisSummary[]
}

/**
 * Colour is spent only where the status is news. A column of green
 * "completed" would make the most saturated mark on the page the least
 * discriminating one -- the same rule the Dashboard's cards follow. Failed and
 * running keep the hues they carry in the masthead strip, so the strip reads
 * as this column's legend.
 */
function statusColor(status: string): string {
  if (status === 'failed') return 'error.main'
  if (status === 'running') return 'info.main'
  if (status === 'completed') return 'text.secondary'
  return 'text.disabled'
}

// The result of a "re-run all" pass. A per-analysis batch, so unlike a single
// failed request it can carry several error messages at once -- hence a
// dismissible in-page Alert rather than the toast queue, which is sized and
// timed for one line.
interface RerunSummary {
  severity: 'success' | 'info' | 'warning' | 'error'
  headline: string
  details?: string[]
}

export default function ProjectDetail() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const notify = useNotify()
  const isUnassigned = projectId === 'unassigned'
  const location = useLocation()
  // Set by Import project: the run it queued, and that this project's
  // previous runs are the author's (see ImportProjectDialog).
  const arrival = location.state as { jobId?: string | null; imported?: boolean } | null

  // The project as one package: every analysis's Export JSON, what each was
  // run from, the run its results came from, the curves they need, the
  // collection and a summary table (api/project_export.py). Import project
  // reads it back in. `ids` narrows it to those analyses and the dates they
  // use. Stored results only, no solver or plots, so a large project is
  // seconds, said once while it builds.
  const handleExport = async (ids?: string[]) => {
    if (!projectId) return
    notify('Building the export…', 'info')
    try {
      const name = (project?.name || 'project').replace(/[^A-Za-z0-9._-]+/g, '-')
      const query = ids ? '?' + ids.map((id) => `analysis=${encodeURIComponent(id)}`).join('&') : ''
      const suffix = ids ? `-${ids.length}-${ids.length === 1 ? 'analysis' : 'analyses'}` : ''
      await saveDownload(`/api/projects/${projectId}/export${query}`, `project-${name}${suffix}.zip`)
    } catch (err) {
      notify('Could not export: ' + describeFetchError(err))
    }
  }

  const [project, setProject] = useState<ProjectDetailData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [editOpen, setEditOpen] = useState(false)
  const [editName, setEditName] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [editPrecision, setEditPrecision] = useState<CoordinatePrecision>('full')
  const [editTiles, setEditTiles] = useState(false)
  const [saving, setSaving] = useState(false)

  const [deleteProjectOpen, setDeleteProjectOpen] = useState(false)
  const [deletingProject, setDeletingProject] = useState(false)
  const [deleteAnalysisTarget, setDeleteAnalysisTarget] = useState<AnalysisSummary | null>(null)
  const [deletingAnalysis, setDeletingAnalysis] = useState(false)
  const [deleteSelectedOpen, setDeleteSelectedOpen] = useState(false)
  const [deletingSelected, setDeletingSelected] = useState(false)

  // 'analyses' | 'dates': the date collection has its own tab (see
  // ProjectSampleTable). The Unassigned pseudo-project is not a real project
  // row, so it has no collection and the tabs are hidden.
  const [tab, setTab] = useState<'analyses' | 'dates'>('analyses')
  const [duplicating, setDuplicating] = useState(false)

  const [assignTarget, setAssignTarget] = useState<AnalysisSummary | null>(null)
  const [rerunTarget, setRerunTarget] = useState<AnalysisSummary | null>(null)
  const [rerunningOne, setRerunningOne] = useState(false)
  const [rerunAllOpen, setRerunAllOpen] = useState(false)
  const [rerunningAll, setRerunningAll] = useState(false)
  const [rerunSummary, setRerunSummary] = useState<RerunSummary | null>(null)
  const [jobId, setJobId] = useState<string | null>(arrival?.jobId ?? null)
  const [imported, setImported] = useState(!!arrival?.imported)
  const [cancelling, setCancelling] = useState(false)

  // The ticked analyses, IN THE ORDER THEY WERE TICKED: the first is the
  // reference every shift on the compare page is measured against, so the
  // order is a choice the user makes rather than whatever the table sorts to.
  // An array, not a Set, to say that out loud. The same selection is what
  // "Delete" acts on, where the order does not matter.
  const [selectedForCompare, setSelectedForCompare] = useState<string[]>([])

  // The project's own edit/delete, and each row's five actions, live behind a
  // menu rather than on the surface: four icon buttons per row put twenty grey
  // glyphs on a five-row table, out-weighing the names they act on.
  const [projectMenuAnchor, setProjectMenuAnchor] = useState<HTMLElement | null>(null)
  const [rowMenu, setRowMenu] = useState<{ anchor: HTMLElement; analysis: AnalysisSummary } | null>(
    null
  )

  useEffect(() => {
    if (projectId) fetchProject(projectId)
    setSelectedForCompare([])
  }, [projectId])

  // Read once, then dropped from history, so a reload does not follow the
  // import's run a second time.
  useEffect(() => {
    if (arrival) navigate(location.pathname, { replace: true, state: null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Only a job this page started. Unlike the results page there is no useful
  // fallback by target -- a project's most recent job may be from last week,
  // and a bar for something long finished is worse than none. Arriving mid-run
  // shows the analyses' own status chips, which are already accurate.
  const { job, active: jobActive } = useJob({
    jobId,
    onFinished: (finished) => {
      setCancelling(false)
      if (projectId) fetchProject(projectId)
      const failed = finished.detail?.failed
      if (finished.state === 'failed') {
        setRerunSummary({ severity: 'warning', headline: finished.error_message || 'The re-run did not finish.' })
      } else if (finished.state === 'cancelled') {
        setRerunSummary({ severity: 'warning', headline: 'The re-run was stopped. Analyses already finished keep their new results.' })
      } else if (failed && failed.length > 0) {
        setRerunSummary({
          severity: 'warning',
          headline: `${(finished.detail?.completed ?? 0) - failed.length} rerun successfully. ${failed.length} failed:`,
          details: failed.map((f) => `${f.name}: ${f.error}`),
        })
      } else if (projectId) {
        reportRunDiff(projectId).finally(() => setImported(false))
      }
    },
  })

  // What the re-run changed: every analysis whose Boundaries or durations
  // moved by 10 years or more against its previous run, or gained or lost a
  // range (api/run_history.py). The point of keeping earlier runs is to see
  // what a change to the code did.
  const reportRunDiff = async (id: string) => {
    try {
      const response = await fetch(`/api/projects/${id}/run-diff`)
      const body = await response.json()
      if (!response.ok || !body.success) return
      const diff = body.data as RunDiff
      if (diff.compared === 0) return
      // After an import the previous run is the author's, so the report is
      // about agreeing with them rather than about what changed.
      const against = imported ? "the author's results" : 'their previous run'
      const finished = imported ? 'Run finished.' : 'Re-run finished.'
      if (diff.changed.length === 0) {
        setRerunSummary({
          severity: 'success',
          headline:
            diff.compared === 1
              ? `${finished} The analysis matches ${imported ? against : 'its previous run'} to within ${diff.shift_years} years.`
              : `${finished} All ${diff.compared} analyses match ${against} to within ${diff.shift_years} years.`,
        })
        return
      }
      const lines = diff.changed.map(describeRunChange)
      setRerunSummary({
        severity: 'info',
        headline: imported
          ? `Run finished. ${diff.changed.length} of ${diff.compared} analyses differ from the author's results:`
          : `Re-run finished. ${diff.changed.length} of ${diff.compared} analyses changed since their previous run:`,
        details: lines.length > 20 ? [...lines.slice(0, 20), `and ${lines.length - 20} more`] : lines,
      })
    } catch {
      // The re-run itself succeeded; a summary that cannot be fetched is
      // simply not shown.
    }
  }

  const handleCancelRerun = async () => {
    if (!job) return
    setCancelling(true)
    try {
      await cancelJob(job.job_id)
    } catch (err) {
      notify((err as Error).message)
      setCancelling(false)
    }
  }

  const toggleCompare = (analysisId: string) => {
    setSelectedForCompare((current) =>
      current.includes(analysisId)
        ? current.filter((id) => id !== analysisId)
        : [...current, analysisId]
    )
  }

  // Why a comparison cannot be run yet, or null when it can. Returned as a
  // sentence rather than a boolean so the disabled button can say what is
  // missing instead of just being grey.
  const compareBlockedBecause = (): string | null => {
    if (selectedForCompare.length < 2) return 'Tick two or more finished analyses to compare them.'
    if (selectedForCompare.length > MAX_COMPARED_RUNS) {
      return `A comparison holds at most ${MAX_COMPARED_RUNS} analyses.`
    }
    const unfinished = (project?.analyses ?? []).filter(
      (a) => selectedForCompare.includes(a.id) && a.status !== 'completed'
    )
    if (unfinished.length > 0) {
      return `${unfinished.map((a) => a.name).join(', ')} has not finished, so it has no results to compare.`
    }
    return null
  }

  const fetchProject = async (id: string) => {
    setLoading(true)
    setError(null)
    try {
      if (id === 'unassigned') {
        const response = await fetch('/api/analysis?unassigned=true&per_page=100')
        const data = await response.json()
        if (data.success) {
          const items: AnalysisSummary[] = data.data.items
          setProject({
            id: 'unassigned',
            name: 'Unassigned',
            description: 'Analyses not filed under any project',
            created_at: '',
            updated_at: '',
            // No project row, so no project edit to be the later of: the
            // newest analysis IS the activity here. And no date collection --
            // the collection belongs to a project, which this is not.
            last_activity: items.reduce<string | null>(
              (latest, a) => (!latest || a.created_at > latest ? a.created_at : latest),
              null
            ),
            samples_count: 0,
            // No project row, so no project settings either. The defaults
            // stand in: "Unassigned" has no collection to place, and nothing
            // reads these here.
            coordinate_precision: 'full',
            map_tiles_enabled: false,
            analyses: items,
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
          // So the results and model-structure pages, which draw this
          // project's maps without loading the project, need not fetch it.
          rememberProjectMapTiles(data.data.id, !!data.data.map_tiles_enabled)
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
    setEditPrecision(project.coordinate_precision || 'full')
    setEditTiles(project.map_tiles_enabled)
    setEditOpen(true)
  }

  const handleSaveEdit = async () => {
    if (!project || !editName.trim()) return
    setSaving(true)
    try {
      const response = await fetch(`/api/projects/${project.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: editName.trim(),
          description: editDescription.trim() || null,
          coordinate_precision: editPrecision,
          map_tiles_enabled: editTiles,
        }),
      })
      const data = await response.json()
      if (data.success) {
        setEditOpen(false)
        fetchProject(project.id)
      } else {
        notify('Could not update project: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Could not update project: ' + describeFetchError(err))
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
        notify('Could not delete project: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Could not delete project: ' + describeFetchError(err))
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
        const deletedId = deleteAnalysisTarget.id
        setDeleteAnalysisTarget(null)
        setSelectedForCompare((current) => current.filter((id) => id !== deletedId))
        fetchProject(project.id)
      } else {
        notify('Could not delete analysis: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Could not delete analysis: ' + describeFetchError(err))
    } finally {
      setDeletingAnalysis(false)
    }
  }

  // One request for the whole selection, all-or-nothing on the server, so a
  // failure leaves the table exactly as it was rather than half-emptied.
  const handleDeleteSelected = async () => {
    if (!project || selectedForCompare.length === 0) return
    setDeletingSelected(true)
    try {
      const response = await fetch('/api/analysis', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ analysis_ids: selectedForCompare }),
      })
      const data = await response.json()
      if (data.success) {
        const n = data.data.deleted_count
        setDeleteSelectedOpen(false)
        setSelectedForCompare([])
        notify(`${n} ${n === 1 ? 'analysis' : 'analyses'} deleted`, 'success')
        fetchProject(project.id)
      } else {
        notify('Could not delete the selected analyses: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Could not delete the selected analyses: ' + describeFetchError(err))
    } finally {
      setDeletingSelected(false)
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
        notify('Could not duplicate analysis: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Could not duplicate analysis: ' + describeFetchError(err))
    } finally {
      setDuplicating(false)
    }
  }

  // Both rerun paths queue rather than run: the endpoints answer 202 as soon
  // as the work is accepted, and this page follows the job rather than holding
  // a request open for what can be minutes.
  const handleRerunOne = async () => {
    if (!rerunTarget || !project) return
    setRerunningOne(true)
    try {
      const response = await fetch(`/api/analysis/${rerunTarget.id}/rerun`, { method: 'POST' })
      const data = await response.json()
      if (data.success) {
        setRerunTarget(null)
        setJobId(data.data.job_id ?? null)
        fetchProject(project.id)
      } else {
        notify('Could not re-run analysis: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Could not re-run analysis: ' + describeFetchError(err))
    } finally {
      setRerunningOne(false)
    }
  }

  // Real project: one job for the whole batch. Unassigned pseudo-project:
  // one job per analysis (see below).
  const handleRerunAll = async () => {
    if (!project) return
    setRerunningAll(true)
    setRerunSummary(null)
    try {
      if (isUnassigned) {
        // The unassigned pseudo-project has no id for a batch endpoint, so
        // each analysis is queued separately. They still run one at a time,
        // the queue having a single worker, so this fans out submissions and
        // not work. Only the last job is followed: they run in order, so it
        // finishing means they all have.
        const outcomes = await Promise.allSettled(
          project.analyses.map((a) => fetch(`/api/analysis/${a.id}/rerun`, { method: 'POST' }).then((r) => r.json()))
        )
        const failed = outcomes.filter((o) => o.status === 'rejected' || !o.value?.success)
        if (failed.length > 0) {
          setRerunSummary({
            severity: 'warning',
            headline: `${failed.length} of ${outcomes.length} analyses could not be queued. Check them individually for details.`,
          })
        }
        const queued = outcomes
          .filter((o): o is PromiseFulfilledResult<any> => o.status === 'fulfilled' && !!o.value?.success)
          .map((o) => o.value.data.job_id)
          .filter(Boolean)
        setJobId(queued.length ? queued[queued.length - 1] : null)
      } else {
        const response = await fetch(`/api/projects/${project.id}/rerun`, { method: 'POST' })
        const data = await response.json()
        if (data.success) {
          setJobId(data.data.job_id ?? null)
        } else {
          notify('Could not re-run project: ' + formatApiError(data))
        }
      }
      setRerunAllOpen(false)
      fetchProject(project.id)
    } catch (err) {
      notify('Could not re-run project: ' + describeFetchError(err))
    } finally {
      setRerunningAll(false)
    }
  }

  // The four-way split the masthead strip draws, derived from the analyses the
  // page already holds rather than fetched a second time -- so the summary and
  // the rows printed under it cannot disagree.
  const statusCounts = useMemo<StatusCounts>(() => {
    const counts: StatusCounts = { completed: 0, running: 0, failed: 0, pending: 0 }
    for (const a of project?.analyses ?? []) {
      if (a.status === 'completed' || a.status === 'running' || a.status === 'failed') {
        counts[a.status] += 1
      } else {
        counts.pending += 1
      }
    }
    return counts
  }, [project])

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

  const analysesCount = project.analyses.length
  const menuAnalysis = rowMenu?.analysis ?? null
  const selectedCount = selectedForCompare.length
  const allSelected = analysesCount > 0 && selectedCount === analysesCount
  const showCompare = tab === 'analyses' && analysesCount > 1
  // Delete appears only while something is ticked: a permanently greyed-out
  // red button would be the loudest thing in the bar and do nothing.
  const showDeleteSelected = tab === 'analyses' && selectedCount > 0
  const showSelectionControls = showCompare || showDeleteSelected
  const deleteSelectedControl = showDeleteSelected && (
    <Button
      variant="outlined"
      color="error"
      size="small"
      startIcon={<DeleteIcon />}
      onClick={() => setDeleteSelectedOpen(true)}
    >
      Delete ({selectedCount})
    </Button>
  )
  const compareControl = showCompare && (
    <Tooltip
      title={
        compareBlockedBecause() ??
        'Compare these analyses date by date. The first one ticked is the reference.'
      }
    >
      <span>
        <Button
          variant="outlined"
          size="small"
          startIcon={<CompareArrowsIcon />}
          disabled={compareBlockedBecause() !== null}
          onClick={() =>
            navigate(`/projects/${projectId}/compare?runs=${selectedForCompare.join(',')}`)
          }
        >
          Compare{selectedCount > 0 ? ` (${selectedCount})` : ''}
        </Button>
      </span>
    </Tooltip>
  )
  const exportSelectedControl = showDeleteSelected && (
    <Button
      variant="outlined"
      size="small"
      startIcon={<DownloadIcon />}
      onClick={() => handleExport(selectedForCompare)}
    >
      Export ({selectedCount})
    </Button>
  )
  const selectionControls = (
    <>
      {deleteSelectedControl}
      {exportSelectedControl}
      {compareControl}
    </>
  )

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      {/* MASTHEAD. The project card that opens this page promises a count, a
          date total, a status breakdown and a last-touched time; before this
          the click threw all four away and offered a name and a sentence. */}
      <Paper sx={{ p: 3, mb: 3 }}>
        {/* Was a lone "Back to Projects" button at the very bottom of the
            page -- past the table, the dialogs and 300 lines of markup. Where
            you came from belongs at the top, where you look for it. */}
        <Box
          sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}
        >
          <BackLink to="/projects">Projects</BackLink>
          {/* Here rather than in the action row below: at phone widths that row
              could not hold a third control and wrapped the menu onto a line of
              its own, and the masthead's own right edge belongs to the measure
              rail. This line is empty at every width. */}
          {!isUnassigned && (
            <Tooltip title="Project actions">
              <IconButton
                size="small"
                aria-label="Project actions"
                aria-haspopup="true"
                onClick={(e) => setProjectMenuAnchor(e.currentTarget)}
                sx={{ mr: -0.5 }}
              >
                <MoreVertIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
        </Box>

        <Box
          sx={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'flex-start',
            gap: { xs: 2.5, md: 4 },
          }}
        >
          <Box sx={{ flex: '1 1 320px', minWidth: 0 }}>
            <Typography variant="h1" component="h1">
              {project.name}
            </Typography>
            {project.description && (
              <Typography variant="body1" color="text.secondary" sx={{ mt: 1, maxWidth: '62ch' }}>
                {project.description}
              </Typography>
            )}
          </Box>

          {/* One dominant number and two supporting facts, not three equal
              hero figures: the card's own hierarchy, at masthead scale. */}
          <Box
            sx={{
              flex: '0 1 auto',
              minWidth: 0,
              textAlign: { xs: 'left', md: 'right' },
            }}
          >
            <Box
              sx={{
                display: 'flex',
                alignItems: 'baseline',
                gap: 0.75,
                justifyContent: { xs: 'flex-start', md: 'flex-end' },
              }}
            >
              <Typography
                component="span"
                sx={{
                  fontSize: '2.5rem',
                  fontWeight: 600,
                  lineHeight: 1,
                  color: analysesCount > 0 ? 'primary.main' : 'text.disabled',
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {analysesCount}
              </Typography>
              <Typography component="span" variant="body1" color="text.secondary">
                {analysesCount === 1 ? 'analysis' : 'analyses'}
              </Typography>
            </Box>
            {project.samples_count > 0 && (
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mt: 1, fontVariantNumeric: 'tabular-nums' }}
              >
                {project.samples_count} {project.samples_count === 1 ? 'date' : 'dates'} in the
                collection
              </Typography>
            )}
            <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 0.5 }}>
              {analysesCount > 0 ? statusSummary(statusCounts, analysesCount) : 'No analyses yet'}
              {project.last_activity && (
                <Box component="span" title={absoluteTime(project.last_activity)}>
                  {' · '}
                  {relativeTime(project.last_activity)}
                </Box>
              )}
            </Typography>
            {/* Under the words that name it, at the width of the rail it
                belongs to. Bled across the Paper's bottom edge it stopped
                being a measure and became a coloured rule -- a project whose
                analyses have all finished drew one saturated green line the
                full width of the header, louder than the number above it and
                attached to no caption. */}
            <StatusStrip counts={statusCounts} total={analysesCount} sx={{ mt: 1.25 }} />
          </Box>
        </Box>

        {/* One contained action, one outlined, and everything rarer behind a
            menu -- rather than the four competing weights this row used to
            carry. */}
        <Box sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 1.5, mt: 3 }}>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            component={RouterLink}
            to={isUnassigned ? '/new-analysis' : `/new-analysis?project=${project.id}`}
            state={{ fresh: true }}
          >
            New Analysis
          </Button>
          {analysesCount > 0 && (
            <Button variant="outlined" startIcon={<ReplayIcon />} onClick={() => setRerunAllOpen(true)}>
              Re-run all
            </Button>
          )}
          {!isUnassigned && (
            <Button variant="outlined" startIcon={<DownloadIcon />} onClick={() => handleExport()}>
              Export
            </Button>
          )}
        </Box>

      </Paper>

      <Menu
        anchorEl={projectMenuAnchor}
        open={!!projectMenuAnchor}
        onClose={() => setProjectMenuAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <MenuItem
          onClick={() => {
            setProjectMenuAnchor(null)
            openEdit()
          }}
        >
          <ListItemIcon>
            <EditIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Edit project</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem
          onClick={() => {
            setProjectMenuAnchor(null)
            setDeleteProjectOpen(true)
          }}
          sx={{ color: 'error.main' }}
        >
          <ListItemIcon>
            <DeleteIcon fontSize="small" sx={{ color: 'inherit' }} />
          </ListItemIcon>
          <ListItemText>Delete project</ListItemText>
        </MenuItem>
      </Menu>

      {jobActive && job && (
        <JobProgress job={job} onCancel={handleCancelRerun} cancelling={cancelling} />
      )}

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

      {/* The tabs used to float on the canvas between two Papers, belonging to
          neither, above a heading that repeated the selected tab's own label
          word for word. Docked into the content surface they label it, and the
          heading is gone. */}
      <Paper sx={{ overflow: 'hidden' }}>
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 2,
            px: 3,
            minHeight: 56,
            borderBottom: 1,
            borderColor: 'divider',
          }}
        >
          {isUnassigned ? (
            <Typography variant="h6" component="h2">
              Analyses ({analysesCount})
            </Typography>
          ) : (
            <Tabs
              value={tab}
              onChange={(_e, v) => setTab(v)}
              sx={{ minHeight: 56, '& .MuiTab-root': { minHeight: 56 } }}
            >
              <Tab label={`Analyses (${analysesCount})`} value="analyses" />
              <Tab label={`Dates${project.samples_count ? ` (${project.samples_count})` : ''}`} value="dates" />
            </Tabs>
          )}
          <Box sx={{ flex: 1 }} />
          {showSelectionControls && (
            <Box sx={{ display: { xs: 'none', sm: 'flex' }, gap: 1 }}>{selectionControls}</Box>
          )}
        </Box>
        {/* Below sm the bar cannot hold the tabs and this at once -- the second
            tab's label was being clipped by it -- so it moves above the table
            it acts on. Same control, declared once. */}
        {showSelectionControls && (
          <Box
            sx={{
              display: { xs: 'flex', sm: 'none' },
              flexWrap: 'wrap',
              justifyContent: 'flex-end',
              gap: 1,
              px: 2,
              pt: 2,
              // Three controls fit a phone only on one line each.
              '& .MuiButton-root': { whiteSpace: 'nowrap' },
            }}
          >
            {selectionControls}
          </Box>
        )}

        {tab === 'dates' && !isUnassigned ? (
          // How a collection works -- enter once, deploy copies -- is in the
          // manual (Projects and Date Collections); the empty state says it in
          // a sentence for someone who has not started one.
          <Box sx={{ p: 3 }}>
            <ProjectSampleTable
              projectId={project.id}
              projectName={project.name}
              onCountChange={(count) =>
                setProject((current) =>
                  current && current.samples_count !== count ? { ...current, samples_count: count } : current
                )
              }
              mapTilesEnabled={project.map_tiles_enabled}
            />
          </Box>
        ) : analysesCount === 0 ? (
          <Box sx={{ px: 3, py: 7, textAlign: 'center' }}>
            <Typography variant="h6" component="p" gutterBottom>
              No analyses yet
            </Typography>
            <Typography color="text.secondary" sx={{ mb: 3, maxWidth: '46ch', mx: 'auto' }}>
              {isUnassigned
                ? 'Every analysis is currently filed under a project. Ones created without a project land here.'
                : 'Deploy dates from this project’s collection into a calibration or a model, or start an analysis from a fresh set of measurements.'}
            </Typography>
            {!isUnassigned && (
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                component={RouterLink}
                to={`/new-analysis?project=${project.id}`}
                state={{ fresh: true }}
              >
                New Analysis
              </Button>
            )}
          </Box>
        ) : (
          // Sticky only where there is a container to stick inside: a header
          // with no scrolling ancestor sticks to the VIEWPORT and slides under
          // the app bar, and a nested scroller fights the page scroll on touch.
          <TableContainer sx={{ maxHeight: { xs: 'none', md: '70vh' } }}>
            <Table stickyHeader sx={{ minWidth: 720 }}>
              <TableHead>
                <TableRow>
                  <TableCell padding="checkbox" sx={{ backgroundColor: 'background.muted' }}>
                    <Checkbox
                      size="small"
                      checked={allSelected}
                      indeterminate={selectedCount > 0 && !allSelected}
                      onChange={() =>
                        setSelectedForCompare(allSelected ? [] : project.analyses.map((a) => a.id))
                      }
                      inputProps={{ 'aria-label': 'Select all analyses' }}
                    />
                  </TableCell>
                  <TableCell sx={{ backgroundColor: 'background.muted' }}>Name</TableCell>
                  {/* Every column but the name shrinks to its own content, so
                      the surplus goes to the one column that carries meaning
                      rather than pooling between the short ones. */}
                  <TableCell sx={{ backgroundColor: 'background.muted', width: '1%', whiteSpace: 'nowrap' }}>
                    Type
                  </TableCell>
                  <TableCell sx={{ backgroundColor: 'background.muted', width: '1%', whiteSpace: 'nowrap' }}>
                    Status
                  </TableCell>
                  <TableCell align="right" sx={{ backgroundColor: 'background.muted', width: '1%', whiteSpace: 'nowrap' }}>
                    Dates
                  </TableCell>
                  <TableCell align="right" sx={{ backgroundColor: 'background.muted', width: '1%', whiteSpace: 'nowrap' }}>
                    Created
                  </TableCell>
                  <TableCell align="right" sx={{ backgroundColor: 'background.muted', width: '1%' }} />
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
                    <TableCell padding="checkbox">
                      <Checkbox
                        size="small"
                        checked={selectedForCompare.includes(a.id)}
                        onClick={(e) => e.stopPropagation()}
                        onChange={() => toggleCompare(a.id)}
                        inputProps={{ 'aria-label': `Select ${a.name}` }}
                      />
                    </TableCell>
                    {/* The row navigates on click, which no keyboard user can
                        do. The name is the row's real link; the row click is
                        the convenience on top of it. */}
                    <TableCell>
                      <Link
                        component={RouterLink}
                        to={`/results/${a.id}`}
                        onClick={(e) => e.stopPropagation()}
                        sx={{
                          fontWeight: 500,
                          color: 'text.primary',
                          textDecoration: 'none',
                          '&:hover': { textDecoration: 'underline' },
                        }}
                      >
                        {a.name}
                      </Link>
                    </TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>
                      <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 500 }}>
                        {analysisTypeLabel(a.analysis_type)}
                      </Typography>
                    </TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>
                      <Typography variant="body2" sx={{ fontWeight: 500, color: statusColor(a.status) }}>
                        {a.status}
                      </Typography>
                    </TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
                      {a.measurements_count}
                    </TableCell>
                    {/* Relative, because "has this been touched lately?" is the
                        question a list of runs is scanned for -- with the exact
                        stamp on the title, since this is a record someone may
                        need to cite. */}
                    <TableCell
                      align="right"
                      sx={{ whiteSpace: 'nowrap', color: 'text.secondary' }}
                      title={absoluteTime(a.created_at)}
                    >
                      {relativeTime(a.created_at)}
                    </TableCell>
                    <TableCell align="right" padding="checkbox">
                      <IconButton
                        size="small"
                        aria-label={`Actions for ${a.name}`}
                        aria-haspopup="true"
                        onClick={(e) => {
                          e.stopPropagation()
                          setRowMenu({ anchor: e.currentTarget, analysis: a })
                        }}
                      >
                        <MoreVertIcon fontSize="small" />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      <Menu
        anchorEl={rowMenu?.anchor ?? null}
        open={!!rowMenu}
        onClose={() => setRowMenu(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        {isUnassigned && (
          <MenuItem
            onClick={() => {
              setAssignTarget(menuAnalysis)
              setRowMenu(null)
            }}
          >
            <ListItemIcon>
              <DriveFileMoveIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText>Assign to project</ListItemText>
          </MenuItem>
        )}
        <MenuItem
          disabled={duplicating}
          onClick={() => {
            setRowMenu(null)
            if (menuAnalysis) handleDuplicate(menuAnalysis)
          }}
        >
          <ListItemIcon>
            <ContentCopyIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Duplicate and edit</ListItemText>
        </MenuItem>
        <MenuItem
          onClick={() => {
            setRowMenu(null)
            if (menuAnalysis) navigate(`/analysis/${menuAnalysis.id}/edit`)
          }}
        >
          <ListItemIcon>
            <EditIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Edit inputs</ListItemText>
        </MenuItem>
        <MenuItem
          onClick={() => {
            setRerunTarget(menuAnalysis)
            setRowMenu(null)
          }}
        >
          <ListItemIcon>
            <ReplayIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Re-run</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem
          onClick={() => {
            setDeleteAnalysisTarget(menuAnalysis)
            setRowMenu(null)
          }}
          sx={{ color: 'error.main' }}
        >
          <ListItemIcon>
            <DeleteIcon fontSize="small" sx={{ color: 'inherit' }} />
          </ListItemIcon>
          <ListItemText>Delete</ListItemText>
        </MenuItem>
      </Menu>

      <Dialog open={editOpen} onClose={() => setEditOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Project settings</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            label="Project name"
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
          <Typography variant="subtitle2" sx={{ mt: 3 }}>
            Coordinates
          </Typography>
          <TextField
            select
            fullWidth
            label="Precision in exports and figures"
            value={editPrecision}
            onChange={(e) => setEditPrecision(e.target.value as CoordinatePrecision)}
            sx={{ mt: 1 }}
            helperText={
              editPrecision === 'full'
                ? 'Positions leave this project exactly as they were entered.'
                : 'Positions are rounded on the way out. What is stored here does not change, and this page still shows the full value.'
            }
          >
            {COORDINATE_PRECISION_OPTIONS.map((option) => (
              <MenuItem key={option.value} value={option.value}>
                {option.label}
              </MenuItem>
            ))}
          </TextField>

          <FormControlLabel
            sx={{ mt: 2 }}
            control={
              <Switch
                checked={editTiles}
                onChange={(e) => setEditTiles(e.target.checked)}
              />
            }
            label="Show a basemap with coastlines, rivers and place names"
          />
          {/* The notice says what the request gives away, and says it BEFORE
              the switch is thrown rather than in a footnote afterwards. That
              ordering is the point: defaulting to tiles and mentioning it
              later would be the same disclosure with better manners. */}
          <Alert severity={editTiles ? 'warning' : 'info'} sx={{ mt: 1 }}>
            The map draws its coastlines from data this app serves itself, and asks nothing of
            anyone. Turning this on fetches map tiles from OpenFreeMap instead — on every map this
            project appears in, placing and results alike — which tells their server which patch
            of ground is on screen each time someone opens or pans a map: not the coordinates
            themselves, but enough to locate a site. Worth it to drop a pin on a field boundary;
            think twice for a restricted site. It needs the internet: when OpenFreeMap cannot be
            reached, the map falls back to the app's own coastlines and says so.
          </Alert>
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
        message={`Delete "${project.name}"? Its analyses will be kept and moved to Unassigned — only the project itself is removed.`}
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
        open={deleteSelectedOpen}
        title={`Delete ${selectedCount} ${selectedCount === 1 ? 'analysis' : 'analyses'}?`}
        message={
          <>
            Permanently delete{' '}
            {selectedCount === 1 ? 'this analysis' : `these ${selectedCount} analyses`}? This
            removes their samples and results and cannot be undone.
            <Box component="ul" sx={{ m: 0, mt: 1, pl: 2.5 }}>
              {project.analyses
                .filter((a) => selectedForCompare.includes(a.id))
                .slice(0, 10)
                .map((a) => (
                  <li key={a.id}>{a.name}</li>
                ))}
              {selectedCount > 10 && <li>and {selectedCount - 10} more</li>}
            </Box>
          </>
        }
        confirmLabel="Delete"
        danger
        loading={deletingSelected}
        onConfirm={handleDeleteSelected}
        onCancel={() => setDeleteSelectedOpen(false)}
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
        confirmLabel="Re-run all"
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
