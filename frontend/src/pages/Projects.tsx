import React, { useState, useEffect } from 'react'
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
} from '@mui/material'
import { Link as RouterLink } from 'react-router-dom'
import AddIcon from '@mui/icons-material/Add'
import FolderIcon from '@mui/icons-material/Folder'
import InboxIcon from '@mui/icons-material/Inbox'
import { formatApiError } from '@/utils/apiError'

interface ProjectSummary {
  id: string
  name: string
  description: string | null
  created_at: string
  updated_at: string
  analyses_count: number
}

export default function Projects() {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [unassignedCount, setUnassignedCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [creating, setCreating] = useState(false)

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
        alert('Failed to create project: ' + formatApiError(data))
      }
    } catch (error) {
      alert('Error creating project: ' + (error as Error).message)
    } finally {
      setCreating(false)
    }
  }

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 4, flexWrap: 'wrap', gap: 2 }}>
        <Box>
          <Typography variant="h3" component="h1" gutterBottom>
            Projects
          </Typography>
          <Typography variant="body1" color="textSecondary">
            Group analyses by site, dig season, or research question.
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => setDialogOpen(true)}>
          New Project
        </Button>
      </Box>

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
                sx={{ p: 2, cursor: 'pointer', '&:hover': { boxShadow: 3 }, display: 'flex', gap: 1.5, alignItems: 'flex-start' }}
                component={RouterLink}
                to={`/projects/${project.id}`}
                style={{ textDecoration: 'none' }}
              >
                <FolderIcon color="primary" />
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="h6" component="h3" noWrap>
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
    </Container>
  )
}
