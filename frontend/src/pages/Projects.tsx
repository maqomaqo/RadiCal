import { useState, useEffect, useMemo } from 'react'
import {
  Container,
  Box,
  Paper,
  Typography,
  Button,
  Grid,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  InputAdornment,
  CircularProgress,
  IconButton,
  Tooltip,
} from '@mui/material'
import { Link as RouterLink, useSearchParams } from 'react-router-dom'
import { alpha } from '@mui/material/styles'
import { WARM_INK } from '@/theme/diagramColors'
import AddIcon from '@mui/icons-material/Add'
import InboxIcon from '@mui/icons-material/Inbox'
import DeleteIcon from '@mui/icons-material/Delete'
import SearchIcon from '@mui/icons-material/Search'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import { formatApiError } from '@/utils/apiError'
import { useNotify } from '@/contexts/NotificationContext'
import ConfirmDialog from '@/components/ConfirmDialog'

interface StatusCounts {
  completed: number
  running: number
  failed: number
  pending: number
}

interface ProjectSummary {
  id: string
  name: string
  description: string | null
  created_at: string
  updated_at: string
  /** Latest of the project's own edit and any of its analyses' -- see api/projects.list_projects. */
  last_activity: string | null
  analyses_count: number
  status_counts: StatusCounts
  samples_count: number
}

type SortKey = 'activity' | 'name' | 'analyses'

const SORT_LABELS: Record<SortKey, string> = {
  activity: 'Last activity',
  name: 'Name',
  analyses: 'Analyses',
}

const isSortKey = (value: string | null): value is SortKey =>
  value === 'activity' || value === 'name' || value === 'analyses'

// The search field and sort control only appear once there are enough projects
// for finding one to be work. Below that they are chrome over a grid the eye
// already reads in full.
const CONTROLS_APPEAR_AT = 6

/**
 * The API writes `datetime.utcnow().isoformat()`, which carries no offset --
 * and a bare timestamp is parsed by JS as LOCAL time, not UTC, so west of
 * Greenwich every date read hours older than it is and "today" became
 * "yesterday" for a whole evening. Read it as the UTC it is.
 */
function parseServerTime(iso: string): Date {
  return new Date(/[Z+]|-\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`)
}

/**
 * Recent activity is relative because that is the question ("has anyone
 * touched this lately?"); past a month it becomes a date, because "in 87 days"
 * is arithmetic the reader has to undo.
 */
function relativeTime(iso: string | null): string {
  if (!iso) return ''
  const then = parseServerTime(iso)
  const days = Math.floor((Date.now() - then.getTime()) / 86_400_000)
  if (days < 1) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 30) return `${days} days ago`
  return then.toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
}

/**
 * The analysis mix as one 3px strip along the foot of the card: segments in
 * proportion, in the app's semantic state colors, never wider than the truth.
 * Anything not completed, running or failed is drawn in ink at low strength
 * rather than given a hue -- "queued" is the absence of an outcome, not an
 * outcome. It sits under the words that name it, and below the count, so that
 * the number stays the card's dominant mark rather than the colour.
 */
function StatusStrip({ counts, total }: { counts: StatusCounts; total: number }) {
  const untouched = Math.max(0, total - counts.completed - counts.running - counts.failed)
  const filled: Array<{ key: string; value: number; color: string }> = [
    { key: 'completed', value: counts.completed, color: 'success.main' },
    { key: 'running', value: counts.running, color: 'info.main' },
    { key: 'failed', value: counts.failed, color: 'error.main' },
    { key: 'untouched', value: untouched, color: alpha(WARM_INK, 0.16) },
  ].filter((segment) => segment.value > 0)
  // A project with nothing in it still gets the empty track, so every card in
  // the grid ends on the same line. The track is the scale; a card that simply
  // stopped short would read as unfinished rather than as empty.
  const segments = filled.length
    ? filled
    : [{ key: 'empty', value: 1, color: alpha(WARM_INK, 0.08) }]

  return (
    <Box
      aria-hidden
      sx={{
        display: 'flex',
        gap: '2px',
        height: 3,
        mt: 1.5,
        borderRadius: 2,
        overflow: 'hidden',
      }}
    >
      {segments.map((segment) => (
        <Box
          key={segment.key}
          sx={{ flex: segment.value, backgroundColor: segment.color, borderRadius: 2 }}
        />
      ))}
    </Box>
  )
}

/** "12 completed, 2 failed" -- zero states omitted, the app's own status words. */
function statusSummary(counts: StatusCounts, total: number): string {
  const untouched = Math.max(0, total - counts.completed - counts.running - counts.failed)
  const parts: string[] = []
  if (counts.completed) parts.push(`${counts.completed} completed`)
  if (counts.running) parts.push(`${counts.running} running`)
  if (counts.failed) parts.push(`${counts.failed} failed`)
  if (untouched) parts.push(`${untouched} pending`)
  return parts.join(' · ')
}

function ProjectCard({
  project,
  onDelete,
}: {
  project: ProjectSummary
  onDelete: (project: ProjectSummary) => void
}) {
  const total = project.analyses_count

  return (
    <Paper
      sx={{
        position: 'relative',
        p: 2.5,
        // height:100% against the stretched Grid item, plus a floor, is what
        // makes a row of cards one height: without it every Paper shrank to
        // its own text and a description turned one card into a taller object
        // than its neighbours.
        height: '100%',
        minHeight: 200,
        display: 'flex',
        flexDirection: 'column',
        transition: (theme) =>
          theme.transitions.create('box-shadow', { duration: theme.transitions.duration.shorter }),
        '&:hover': { boxShadow: 4 },
      }}
    >
      {/* Stretched link: covers the whole card for navigation, kept as a
          sibling of (not a wrapper around) the delete button below -- a
          <button> nested inside an <a> is invalid HTML and gets dropped from
          the accessibility tree, which would make delete unreachable by
          keyboard or screen reader. The focus ring is drawn here rather than
          on the Paper so that tabbing to delete does not outline the card. */}
      <Box
        component={RouterLink}
        to={`/projects/${project.id}`}
        aria-label={project.name}
        sx={{
          position: 'absolute',
          inset: 0,
          borderRadius: 'inherit',
          '&:focus-visible': {
            outline: '2px solid',
            outlineColor: 'primary.main',
            outlineOffset: 2,
          },
        }}
      />

      <Box sx={{ pointerEvents: 'none', display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0 }}>
        <Typography variant="h6" component="h3" noWrap sx={{ pr: 4, lineHeight: 1.3 }}>
          {project.name}
        </Typography>
        {/* The slot is always two body2 lines tall, whether or not there is
            a description to put in it. Clamping alone equalised cards within
            a row; reserving the space equalises them down the whole grid, so
            the counts line up on a fixed rhythm rather than on whatever the
            tallest neighbour happened to be. */}
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{
            mt: 0.75,
            minHeight: 40,
            display: '-webkit-box',
            WebkitBoxOrient: 'vertical',
            WebkitLineClamp: 2,
            overflow: 'hidden',
          }}
        >
          {project.description}
        </Typography>

        {/* mt:auto pins the measure block to the bottom of every card, so the
            counts line up straight across a row regardless of how much text
            sits above them. That alignment is the point of the layout. */}
        <Box sx={{ mt: 'auto', pt: 2.5 }}>
          <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 0.75 }}>
            <Typography
              component="span"
              sx={{
                fontSize: '2rem',
                fontWeight: 600,
                lineHeight: 1,
                color: total > 0 ? 'primary.main' : 'text.disabled',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {total}
            </Typography>
            <Typography component="span" variant="body2" color="text.secondary">
              {total === 1 ? 'analysis' : 'analyses'}
            </Typography>
            <Box sx={{ flex: 1 }} />
            {project.samples_count > 0 && (
              <Typography
                component="span"
                variant="body2"
                color="text.secondary"
                sx={{ fontVariantNumeric: 'tabular-nums' }}
              >
                {project.samples_count} {project.samples_count === 1 ? 'date' : 'dates'}
              </Typography>
            )}
          </Box>

          <Box
            sx={{
              display: 'flex',
              alignItems: 'baseline',
              justifyContent: 'space-between',
              gap: 1,
              mt: 1,
            }}
          >
            <Typography variant="caption" color="text.secondary" noWrap sx={{ minWidth: 0 }}>
              {total > 0 ? statusSummary(project.status_counts, total) : 'No analyses yet'}
            </Typography>
            <Typography variant="caption" color="text.secondary" noWrap>
              {relativeTime(project.last_activity)}
            </Typography>
          </Box>

          <StatusStrip counts={project.status_counts} total={total} />
        </Box>
      </Box>

      {/* Always visible, not hover-revealed: a keyboard user tabbing to it and
          a touch user have no hover state to trigger. Grey by default and red
          only on :hover/:focus, though -- a grid of always-red icons competes
          with the content. Every destructive icon button in the app follows
          this rule. */}
      <Tooltip title={`Delete ${project.name}`}>
        <IconButton
          size="small"
          aria-label={`Delete ${project.name}`}
          sx={{
            position: 'absolute',
            top: 14,
            right: 12,
            zIndex: 1,
            '&:hover, &:focus-visible': { color: 'error.main' },
          }}
          onClick={() => onDelete(project)}
        >
          <DeleteIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Paper>
  )
}

export default function Projects() {
  const notify = useNotify()
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [unassignedCount, setUnassignedCount] = useState(0)
  const [loading, setLoading] = useState(true)
  // ?new=1 opens the create dialog on arrival, so a "New project" link lands
  // on one rather than a list. Read once as the initial value rather than
  // watched: this is where the dialog starts, not a lock on it staying open.
  const [searchParams, setSearchParams] = useSearchParams()
  const [dialogOpen, setDialogOpen] = useState(searchParams.get('new') === '1')
  const [query, setQuery] = useState(() => searchParams.get('q') ?? '')
  const [sort, setSort] = useState<SortKey>(() => {
    const requested = searchParams.get('sort')
    return isSortKey(requested) ? requested : 'activity'
  })
  const [newName, setNewName] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [creating, setCreating] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<ProjectSummary | null>(null)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    fetchProjects()
  }, [])

  // Search and sort live in the URL so a filtered view is a link and survives
  // a reload. Rewriting the whole query string also clears the one-shot ?new=1
  // above, which has already been consumed into dialog state by now.
  // setSearchParams is deliberately not a dependency: react-router hands back a
  // fresh function each render, which would make this effect re-run forever.
  useEffect(() => {
    const next = new URLSearchParams()
    if (query.trim()) next.set('q', query.trim())
    if (sort !== 'activity') next.set('sort', sort)
    setSearchParams(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, sort])

  const fetchProjects = async () => {
    setLoading(true)
    try {
      const response = await fetch('/api/projects')
      const data = await response.json()
      if (data.success) {
        setProjects(data.data.items)
        setUnassignedCount(data.data.unassigned_count)
      }
    } catch (error) {
      console.error('Failed to fetch projects:', error)
    } finally {
      setLoading(false)
    }
  }

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const matched = needle
      ? projects.filter(
          (p) =>
            p.name.toLowerCase().includes(needle) ||
            (p.description ?? '').toLowerCase().includes(needle)
        )
      : projects

    const sorted = [...matched]
    if (sort === 'name') {
      // localeCompare so an accented site name files where a reader expects it.
      sorted.sort((a, b) => a.name.localeCompare(b.name))
    } else if (sort === 'analyses') {
      sorted.sort((a, b) => b.analyses_count - a.analyses_count || a.name.localeCompare(b.name))
    } else {
      // A project with no recorded activity sorts last rather than first --
      // an absent timestamp is unknown, not recent.
      sorted.sort((a, b) => {
        const left = a.last_activity ? parseServerTime(a.last_activity).getTime() : 0
        const right = b.last_activity ? parseServerTime(b.last_activity).getTime() : 0
        return right - left
      })
    }
    return sorted
  }, [projects, query, sort])

  const handleCreate = async () => {
    if (!newName.trim()) return
    setCreating(true)
    try {
      const response = await fetch('/api/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName.trim(), description: newDescription.trim() || null }),
      })
      const data = await response.json()
      if (data.success) {
        setDialogOpen(false)
        setNewName('')
        setNewDescription('')
        fetchProjects()
      } else {
        notify('Failed to create project: ' + formatApiError(data))
      }
    } catch (error) {
      notify('Error creating project: ' + (error as Error).message)
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      const response = await fetch(`/api/projects/${deleteTarget.id}`, { method: 'DELETE' })
      const data = await response.json()
      if (data.success) {
        setDeleteTarget(null)
        fetchProjects()
      } else {
        notify('Failed to delete project: ' + formatApiError(data))
      }
    } catch (error) {
      notify('Error deleting project: ' + (error as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  const showControls = projects.length >= CONTROLS_APPEAR_AT

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Paper sx={{ p: 3, mb: 3 }}>
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            mb: 2,
            flexWrap: 'wrap',
            gap: 2,
          }}
        >
          <Box>
            <Typography variant="h1" component="h1" gutterBottom>
              Projects
            </Typography>
            <Typography variant="body1" color="textSecondary">
              Group analyses by site, region, or research question. <br />
              Dates collected in a project can be reused across every analysis in it.
            </Typography>
          </Box>
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setDialogOpen(true)}>
            New Project
          </Button>
        </Box>

        {showControls && (
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, pt: 1 }}>
            <TextField
              size="small"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search projects"
              // The field is the search control; a visible label would repeat
              // the placeholder and the icon in a row that has room for neither.
              inputProps={{ 'aria-label': 'Search projects by name or description' }}
              sx={{ flex: '1 1 240px', maxWidth: 360 }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" color="action" />
                  </InputAdornment>
                ),
              }}
            />
            <TextField
              select
              size="small"
              label="Sort by"
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
              sx={{ minWidth: 168 }}
            >
              {(Object.keys(SORT_LABELS) as SortKey[]).map((key) => (
                <MenuItem key={key} value={key}>
                  {SORT_LABELS[key]}
                </MenuItem>
              ))}
            </TextField>
          </Box>
        )}
      </Paper>

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
          <CircularProgress />
        </Box>
      ) : (
        <>
          {/* The Unassigned bucket is not a project -- it has no description,
              no date collection and cannot be deleted -- so it is drawn as a
              tray above the grid rather than as a card that looks like a peer
              and then behaves differently. Unelevated and bordered, so it
              reads as part of the page rather than as another lifted card. */}
          {unassignedCount > 0 && (
            <Paper
              elevation={0}
              component={RouterLink}
              to="/projects/unassigned"
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 1.5,
                p: 2,
                mb: 3,
                textDecoration: 'none',
                color: 'inherit',
                border: '1px solid',
                borderColor: 'divider',
                backgroundColor: 'background.muted',
                transition: (theme) =>
                  theme.transitions.create('border-color', {
                    duration: theme.transitions.duration.shorter,
                  }),
                '&:hover': { borderColor: 'primary.main' },
              }}
            >
              <InboxIcon color="action" />
              <Box sx={{ minWidth: 0 }}>
                <Typography variant="subtitle1" component="h2" sx={{ lineHeight: 1.3 }}>
                  Unassigned
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {unassignedCount} {unassignedCount === 1 ? 'analysis is' : 'analyses are'} not
                  filed under any project
                </Typography>
              </Box>
              <Box sx={{ flex: 1 }} />
              <ChevronRightIcon color="action" />
            </Paper>
          )}

          {projects.length === 0 ? (
            <Paper sx={{ p: 4, textAlign: 'center' }}>
              <Typography variant="h6" component="h2" gutterBottom>
                No projects yet
              </Typography>
              <Typography color="text.secondary" sx={{ mb: 3 }}>
                A project groups analyses by site, region, or research question, and holds a
                collection of dates you can deploy into any of them.
              </Typography>
              <Button variant="contained" startIcon={<AddIcon />} onClick={() => setDialogOpen(true)}>
                New Project
              </Button>
            </Paper>
          ) : visible.length === 0 ? (
            <Paper sx={{ p: 4, textAlign: 'center' }}>
              <Typography color="text.secondary" sx={{ mb: 2 }}>
                No project matches “{query.trim()}”.
              </Typography>
              <Button onClick={() => setQuery('')}>Clear search</Button>
            </Paper>
          ) : (
            <Grid container spacing={2}>
              {visible.map((project) => (
                <Grid item xs={12} sm={6} md={4} key={project.id}>
                  <ProjectCard project={project} onDelete={setDeleteTarget} />
                </Grid>
              ))}
            </Grid>
          )}
        </>
      )}

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>New Project</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            label="Project Name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            sx={{ mt: 1, mb: 2 }}
          />
          <TextField
            fullWidth
            multiline
            minRows={2}
            label="Description (optional)"
            value={newDescription}
            onChange={(e) => setNewDescription(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleCreate} disabled={creating || !newName.trim()}>
            {creating ? 'Creating...' : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete project?"
        message={
          deleteTarget
            ? `Delete "${deleteTarget.name}"? Its ${deleteTarget.analyses_count} analyses will be kept and moved to Unassigned -- only the project itself is removed.`
            : ''
        }
        confirmLabel="Delete"
        danger
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </Container>
  )
}
