import { useEffect, useRef, useState } from 'react'
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'
import UploadFileIcon from '@mui/icons-material/UploadFile'
import { formatApiError, describeFetchError } from '@/utils/apiError'
import { analysisTypeLabel } from '@/utils/analysisTypes'
import { useNotify } from '@/contexts/NotificationContext'

// What POST /api/import/project/preview says a package holds and what
// importing it would do (api/project_import.ImportPlan.summary).
interface PackagePreview {
  ok: boolean
  errors: string[]
  warnings: string[]
  notes: string[]
  filename: string | null
  project_name: string
  exported_at: string | null
  samples: number
  analyses: { name: string; analysis_type: string; measurements: number; has_run: boolean }[]
  curves: { id: string; name: string; kind: string; action: 'shipped' | 'reuse' | 'add' }[]
}

export interface ImportedProject {
  projectId: string
  /** The re-run queued after the import, for the project page to follow. */
  jobId: string | null
}

interface Props {
  open: boolean
  onClose: () => void
  onImported: (result: ImportedProject) => void
}

function MessageList({ items }: { items: string[] }) {
  return (
    <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
      {items.map((message, index) => (
        <li key={index}>
          <Typography variant="body2">{message}</Typography>
        </li>
      ))}
    </Box>
  )
}

/**
 * Read a project package -- the .zip a project page's Export saves -- and,
 * once its preview has been seen, import it as a new project.
 *
 * As ImportDatesDialog: the preview stores nothing, and confirming sends the
 * same file again rather than anything the preview returned, so the server
 * re-reads what it stores. Running the analyses straight away is on by
 * default because a package exists to be replicated: the project page then
 * reports where this installation's results differ from the author's.
 */
export default function ImportProjectDialog({ open, onClose, onImported }: Props) {
  const notify = useNotify()
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<PackagePreview | null>(null)
  const [reading, setReading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [name, setName] = useState('')
  const [runAfter, setRunAfter] = useState(true)

  useEffect(() => {
    if (!open) {
      setFile(null)
      setPreview(null)
      setReading(false)
      setDragging(false)
      setName('')
      setRunAfter(true)
    }
  }, [open])

  const read = async (chosen: File) => {
    setReading(true)
    setPreview(null)
    setFile(chosen)
    try {
      const body = new FormData()
      body.append('file', chosen)
      const response = await fetch('/api/import/project/preview', { method: 'POST', body })
      const data = await response.json()
      if (!response.ok || !data.success) {
        notify('Could not read the package: ' + formatApiError(data))
        return
      }
      setPreview(data.data)
      setName(data.data.project_name)
    } catch (err) {
      notify('Could not read the package: ' + describeFetchError(err))
    } finally {
      setReading(false)
    }
  }

  const handleImport = async () => {
    if (!file || !preview?.ok) return
    setImporting(true)
    try {
      const body = new FormData()
      body.append('file', file)
      body.append('name', name.trim())
      const response = await fetch('/api/import/project', { method: 'POST', body })
      const data = await response.json()
      if (!response.ok || !data.success) {
        notify('Could not import the project: ' + formatApiError(data))
        return
      }
      const projectId: string = data.data.project_id
      let jobId: string | null = null
      if (runAfter && data.data.analyses > 0) {
        // The import itself has succeeded by now; a run that cannot be queued
        // leaves the analyses pending, to run from the project page.
        try {
          const run = await fetch(`/api/projects/${projectId}/rerun`, { method: 'POST' })
          const queued = await run.json()
          jobId = queued?.data?.job_id ?? null
        } catch {
          jobId = null
        }
      }
      onImported({ projectId, jobId })
    } catch (err) {
      notify('Could not import the project: ' + describeFetchError(err))
    } finally {
      setImporting(false)
    }
  }

  const carried = preview?.curves.filter((c) => c.action !== 'shipped') ?? []
  const analysesCount = preview?.analyses.length ?? 0

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>Import project</DialogTitle>
      <DialogContent dividers>
        <Box
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            const dropped = e.dataTransfer.files?.[0]
            if (dropped) read(dropped)
          }}
          sx={{
            border: '2px dashed',
            borderColor: dragging ? 'primary.main' : 'divider',
            borderRadius: 1,
            p: 3,
            textAlign: 'center',
            backgroundColor: dragging ? 'action.hover' : 'transparent',
            transition: 'background-color 120ms',
          }}
        >
          <UploadFileIcon color="action" sx={{ fontSize: 36, mb: 1 }} />
          <Typography variant="body1" gutterBottom>
            Drop a project package here, or
          </Typography>
          <Button variant="contained" onClick={() => inputRef.current?.click()} disabled={reading}>
            Choose file
          </Button>
          <input
            ref={inputRef}
            type="file"
            accept=".zip,application/zip"
            hidden
            onChange={(e) => {
              const chosen = e.target.files?.[0]
              if (chosen) read(chosen)
              e.target.value = ''
            }}
          />
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
            A .zip saved with Export on a project page
          </Typography>
        </Box>

        {reading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress size={28} />
          </Box>
        )}

        {preview && (
          <Box sx={{ mt: 3 }}>
            <Stack direction="row" sx={{ mb: 2, flexWrap: 'wrap', gap: 1 }}>
              <Chip
                size="small"
                color={preview.ok ? 'success' : 'error'}
                label={
                  preview.ok
                    ? `${analysesCount} ${analysesCount === 1 ? 'analysis' : 'analyses'}, ${preview.samples} ${preview.samples === 1 ? 'date' : 'dates'}`
                    : 'Not imported'
                }
              />
              {preview.filename && <Chip size="small" variant="outlined" label={preview.filename} />}
            </Stack>

            {preview.errors.length > 0 && (
              <Alert severity="error" sx={{ mb: 2 }}>
                <AlertTitle>Nothing was imported</AlertTitle>
                <MessageList items={preview.errors} />
              </Alert>
            )}
            {preview.warnings.length > 0 && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                <MessageList items={preview.warnings} />
              </Alert>
            )}
            {preview.notes.length > 0 && (
              <Alert severity="info" sx={{ mb: 2 }}>
                <MessageList items={preview.notes} />
              </Alert>
            )}

            {preview.ok && (
              <TextField
                fullWidth
                label="Project name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                sx={{ mt: 1, mb: 2 }}
              />
            )}

            {analysesCount > 0 && (
              <TableContainer component={Paper} variant="outlined" sx={{ maxHeight: 320 }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell>Analysis</TableCell>
                      <TableCell>Type</TableCell>
                      <TableCell align="right">Dates</TableCell>
                      <TableCell>Author's result</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {preview.analyses.map((analysis, index) => (
                      <TableRow key={index}>
                        <TableCell>{analysis.name}</TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>
                          {analysisTypeLabel(analysis.analysis_type)}
                        </TableCell>
                        <TableCell align="right">{analysis.measurements}</TableCell>
                        <TableCell>{analysis.has_run ? 'Included' : '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}

            {carried.length > 0 && (
              <Box sx={{ mt: 2 }}>
                <Typography variant="subtitle2" gutterBottom>
                  Curves
                </Typography>
                {carried.map((curve) => (
                  <Typography key={curve.id} variant="body2" color="text.secondary">
                    {curve.name}: {curve.action === 'add' ? 'added to your curves' : 'already in your curves'}
                  </Typography>
                ))}
              </Box>
            )}
          </Box>
        )}
      </DialogContent>
      {preview?.ok && analysesCount > 0 && (
        <Box sx={{ px: 3, py: 1.5, borderTop: '1px solid', borderColor: 'divider' }}>
          <FormControlLabel
            control={
              <Checkbox size="small" checked={runAfter} onChange={(e) => setRunAfter(e.target.checked)} />
            }
            label={<Typography variant="body2">Run every analysis after importing</Typography>}
          />
        </Box>
      )}
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!preview?.ok || !name.trim() || importing}
          onClick={handleImport}
        >
          {importing ? 'Importing…' : 'Import'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
