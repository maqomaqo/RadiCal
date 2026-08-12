import { useEffect, useState } from 'react'
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
} from '@mui/material'
import { formatApiError } from '@/utils/apiError'
import { useNotify } from '@/contexts/NotificationContext'

interface ProjectOption {
  id: string
  name: string
}

interface AssignProjectDialogProps {
  open: boolean
  analysisId: string
  currentProjectId: string | null
  onClose: () => void
  onAssigned: (projectId: string | null, projectName: string | null) => void
}

// Shared "move this analysis into a project" dialog -- used from both
// Results.tsx (a single analysis's own page) and ProjectDetail.tsx (the
// Unassigned list), so the fetch-projects-and-render-a-select logic and
// the PATCH call only exist in one place. Reuses the same "Project"
// dropdown pattern as NewAnalysis.tsx's project field.
export default function AssignProjectDialog({
  open,
  analysisId,
  currentProjectId,
  onClose,
  onAssigned,
}: AssignProjectDialogProps) {
  const notify = useNotify()
  const [projects, setProjects] = useState<ProjectOption[]>([])
  const [projectId, setProjectId] = useState<string>(currentProjectId || '')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!open) return
    setProjectId(currentProjectId || '')
    fetch('/api/projects')
      .then((r) => r.json())
      .then((data) => {
        if (data.success) setProjects(data.data.items)
      })
      .catch((err) => console.error('Failed to load projects:', err))
  }, [open, currentProjectId])

  const handleSave = async () => {
    setSaving(true)
    try {
      const response = await fetch(`/api/analysis/${analysisId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId || null }),
      })
      const data = await response.json()
      if (data.success) {
        onAssigned(data.data.project_id, data.data.project_name)
        onClose()
      } else {
        notify('Failed to assign project: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error assigning project: ' + (err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onClose={saving ? undefined : onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Assign to Project</DialogTitle>
      <DialogContent>
        <FormControl fullWidth sx={{ mt: 1 }}>
          <InputLabel>Project</InputLabel>
          <Select value={projectId} label="Project" onChange={(e) => setProjectId(e.target.value)}>
            <MenuItem value="">No project (unassigned)</MenuItem>
            {projects.map((p) => (
              <MenuItem key={p.id} value={p.id}>
                {p.name}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button variant="contained" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
