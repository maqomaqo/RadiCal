import { useState, useEffect } from 'react'
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
  CircularProgress,
  Chip,
  IconButton,
  Tooltip,
} from '@mui/material'
import { Link as RouterLink } from 'react-router-dom'
import AddIcon from '@mui/icons-material/Add'
import FolderIcon from '@mui/icons-material/Folder'
import InboxIcon from '@mui/icons-material/Inbox'
import DeleteIcon from '@mui/icons-material/Delete'
import { formatApiError } from '@/utils/apiError'
import { useNotify } from '@/contexts/NotificationContext'
import ConfirmDialog from '@/components/ConfirmDialog'

interface ProjectSummary {
  id: string
  name: string
  description: string | null
  created_at: string
  updated_at: string
  analyses_count: number
}

export default function Projects() {
  const notify = useNotify()
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [unassignedCount, setUnassignedCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [creating, setCreating] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<ProjectSummary | null>(null)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    fetchProjects()
  }, [])

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

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
     <Paper sx={{ p: 3, mb: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 4, flexWrap: 'wrap', gap: 2 }}>
        <Box>
          <Typography variant="h1" component="h1" gutterBottom>
            Projects
          </Typography>
          <Typography variant="body1" color="textSecondary">
            Group analyses by site, region, or research question.
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => setDialogOpen(true)}>
          New Project
        </Button>
      </Box>
      </Paper>

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
          <CircularProgress />
        </Box>
      ) : (
        <Grid container spacing={2}>
          {unassignedCount > 0 && (
            <Grid item xs={12} sm={6} md={4}>
              <Paper
                sx={{ p: 2, cursor: 'pointer', '&:hover': { boxShadow: 3 }, display: 'flex', gap: 1.5, alignItems: 'flex-start' }}
                component={RouterLink}
                to="/projects/unassigned"
                style={{ textDecoration: 'none' }}
              >
                <InboxIcon color="action" />
                <Box>
                  <Typography variant="h6" component="h3">
                    Unassigned
                  </Typography>
                  <Typography variant="body2" color="textSecondary">
                    Analyses not filed under any project
                  </Typography>
                  <Chip size="small" label={`${unassignedCount} analyses`} sx={{ mt: 1 }} />
                </Box>
              </Paper>
            </Grid>
          )}

          {projects.map((project) => (
            <Grid item xs={12} sm={6} md={4} key={project.id}>
              <Paper
                sx={{
                  position: 'relative',
                  p: 2,
                  display: 'flex',
                  gap: 1.5,
                  alignItems: 'flex-start',
                  '&:hover': { boxShadow: 3 },
                }}
              >
                {/* Stretched link: covers the whole card for navigation,
                    kept as a sibling of (not a wrapper around) the delete
                    button below -- a <button> nested inside an <a> is
                    invalid HTML and gets dropped from the accessibility
                    tree, which would make delete unreachable by keyboard
                    or screen reader. */}
                <Box
                  component={RouterLink}
                  to={`/projects/${project.id}`}
                  aria-label={project.name}
                  sx={{ position: 'absolute', inset: 0, borderRadius: 'inherit' }}
                />
                <FolderIcon color="primary" sx={{ pointerEvents: 'none' }} />
                <Box sx={{ minWidth: 0, flex: 1, pointerEvents: 'none' }}>
                  <Typography variant="h6" component="h3" noWrap sx={{ pr: 3 }}>
                    {project.name}
                  </Typography>
                  {project.description && (
                    <Typography variant="body2" color="textSecondary" sx={{ mb: 1 }}>
                      {project.description}
                    </Typography>
                  )}
                  <Chip size="small" label={`${project.analyses_count} analyses`} sx={{ mt: 1 }} />
                  <Typography variant="caption" color="textSecondary" sx={{ display: 'block', mt: 1 }}>
                    Created {new Date(project.created_at).toLocaleDateString()}
                  </Typography>
                </Box>
                {/* Always visible, not hover-revealed: this used to fade
                    in only on :hover, which a keyboard user tabbing to it
                    and a touch user (no hover state to reveal it at all)
                    could never trigger -- delete was reachable by mouse
                    only. Grey by default and red only on :hover/:focus,
                    though -- a whole grid of always-red icons competed with
                    the content for attention regardless of intent to use
                    them. Every other destructive icon button in the app
                    follows the same rule: always visible, small, default
                    colour that turns error on hover, labelled with a
                    Tooltip in addition to the aria-label a screen reader
                    already gets from it. */}
                <Tooltip title={`Delete ${project.name}`}>
                  <IconButton
                    size="small"
                    aria-label={`Delete ${project.name}`}
                    sx={{
                      position: 'absolute',
                      top: 8,
                      right: 8,
                      zIndex: 1,
                      '&:hover, &:focus-visible': { color: 'error.main' },
                    }}
                    onClick={() => setDeleteTarget(project)}
                  >
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Paper>
            </Grid>
          ))}

          {projects.length === 0 && unassignedCount === 0 && (
            <Grid item xs={12}>
              <Paper sx={{ p: 2 }}>
                <Typography color="textSecondary">
                  No projects yet. Create one to start grouping analyses.
                </Typography>
              </Paper>
            </Grid>
          )}
        </Grid>
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
