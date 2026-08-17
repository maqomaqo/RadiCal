import React, { useState } from 'react'
import { Container, Box, Paper, Typography, Button, Grid, IconButton, Tooltip } from '@mui/material'
import { Link as RouterLink } from 'react-router-dom'
import FolderIcon from '@mui/icons-material/Folder'
import DeleteIcon from '@mui/icons-material/Delete'
import ConfirmDialog from '@/components/ConfirmDialog'
import { formatApiError } from '@/utils/apiError'
import { useNotify } from '@/contexts/NotificationContext'

interface AnalysisSummary {
  id: string
  name: string
  analysis_type: string
  status: string
  created_at: string
  measurements_count: number
  project_id: string | null
  project_name: string | null
}

// Analyses are organized into projects (see the Projects page) -- the
// dashboard only needs to show a handful of the most recent ones as a
// quick-glance list, not every analysis ever run.
const RECENT_LIMIT = 5

export default function Dashboard() {
  const notify = useNotify()
  const [analyses, setAnalyses] = useState<AnalysisSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<AnalysisSummary | null>(null)
  const [deleting, setDeleting] = useState(false)

  React.useEffect(() => {
    fetchAnalyses()
  }, [])

  const fetchAnalyses = async () => {
    setLoading(true)
    try {
      const response = await fetch(`/api/analysis?per_page=${RECENT_LIMIT}`)
      const data = await response.json()
      if (data.success) {
        setAnalyses(data.data.items)
      }
    } catch (error) {
      console.error('Failed to fetch analyses:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      const response = await fetch(`/api/analysis/${deleteTarget.id}`, { method: 'DELETE' })
      const data = await response.json()
      if (data.success) {
        setDeleteTarget(null)
        fetchAnalyses()
      } else {
        notify('Failed to delete analysis: ' + formatApiError(data))
      }
    } catch (error) {
      notify('Error deleting analysis: ' + (error as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
    <Paper sx={{ p: 3, mb: 3 }}>
      <Box sx={{ mb: 4 }}>
        <Typography variant="h3" component="h1" gutterBottom>
          RadiCal Dashboard
        </Typography>
        <Typography variant="body1" color="textSecondary" paragraph>
          Welcome to RadiCal. Create a new analysis or browse your projects.
        </Typography>
       
      </Box>

      <Box sx={{ mb: 4, display: 'flex', gap: 2 }}>
        <Button
          component={RouterLink}
          to="/new-analysis"
          variant="contained"
          color="primary"
          size="large"
        >
          New Analysis
        </Button>
        <Button
          component={RouterLink}
          to="/projects"
          variant="outlined"
          size="large"
          startIcon={<FolderIcon />}
        >
          Manage Projects
        </Button>
      </Box>
 </Paper>
      <Box>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
          <Typography variant="h5" component="h2">
            Recent Activity
          </Typography>
          <Button component={RouterLink} to="/projects" size="small">
            View all in Projects &rarr;
          </Button>
        </Box>
        {loading ? (
          <Typography>Loading...</Typography>
        ) : analyses.length === 0 ? (
          <Paper sx={{ p: 2 }}>
            <Typography color="textSecondary">
              No analyses yet. Create one to get started.
            </Typography>
          </Paper>
        ) : (
          <Grid container spacing={2}>
            {analyses.map((analysis) => (
              <Grid item xs={12} sm={6} md={4} key={analysis.id}>
                <Paper
                  sx={{
                    position: 'relative',
                    p: 2,
                    '&:hover': { boxShadow: 3 },
                  }}
                >
                  {/* Stretched link: see the matching comment in
                      Projects.tsx -- kept as a sibling of the delete
                      button, not a wrapper around it, so the button stays
                      reachable in the accessibility tree. */}
                  <Box
                    component={RouterLink}
                    to={`/results/${analysis.id}`}
                    aria-label={analysis.name}
                    sx={{ position: 'absolute', inset: 0, borderRadius: 'inherit' }}
                  />
                  <Box sx={{ pointerEvents: 'none' }}>
                    <Typography variant="h6" component="h3" sx={{ pr: 3 }}>
                      {analysis.name}
                    </Typography>
                    <Typography variant="body2" color="textSecondary">
                      Type: {analysis.analysis_type}
                    </Typography>
                    <Typography variant="body2" color="textSecondary">
                      Status: <strong>{analysis.status}</strong>
                    </Typography>
                    <Typography variant="body2" color="textSecondary">
                      Samples: {analysis.measurements_count}
                    </Typography>
                    <Typography variant="body2" color="textSecondary">
                      Project: {analysis.project_name || 'Unassigned'}
                    </Typography>
                    <Typography variant="caption" color="textSecondary">
                      {new Date(analysis.created_at).toLocaleDateString()}
                    </Typography>
                  </Box>
                  {/* Always visible, not hover-revealed -- see the matching
                      comment in Projects.tsx for why, and for the rule this
                      follows across every destructive icon button. */}
                  <Tooltip title={`Delete ${analysis.name}`}>
                    <IconButton
                      size="small"
                      aria-label={`Delete ${analysis.name}`}
                      sx={{
                        position: 'absolute',
                        top: 8,
                        right: 8,
                        zIndex: 1,
                        '&:hover, &:focus-visible': { color: 'error.main' },
                      }}
                      onClick={() => setDeleteTarget(analysis)}
                    >
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Paper>
              </Grid>
            ))}
          </Grid>
        )}
      </Box>

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete analysis?"
        message={
          deleteTarget
            ? `Permanently delete "${deleteTarget.name}"? This removes its samples and results and cannot be undone.`
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
