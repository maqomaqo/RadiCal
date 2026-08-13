import { useEffect, useRef, useState } from 'react'
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Link,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
  Accordion,
  AccordionDetails,
  AccordionSummary,
} from '@mui/material'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import UploadFileIcon from '@mui/icons-material/UploadFile'
import DownloadIcon from '@mui/icons-material/Download'
import { formatApiError } from '@/utils/apiError'
import { useNotify } from '@/contexts/NotificationContext'
import type { ModelElement } from '@/types'

// One date as the importer read it. Mirrors the dicts
// services/date_import.py returns -- measurement fields as
// schemas/sample.py names them, plus the phase it was filed under.
export interface ImportedDate {
  id: string
  sample_name: string
  age_bp: number
  error_1sigma: number
  material_type: string
  curve_id: string
  sample_type: 'terrestrial' | 'marine' | null
  delta_r: number | null
  delta_r_error: number | null
  tpq_age_bp: number | null
  taq_age_bp: number | null
  depth_cm: number | null
  location: string | null
  notes: string | null
  outlier_prior: number
  outlier_model: 'general' | 'charcoal'
  phase_name: string | null
  element_id?: string | null
}

export interface ImportPreview {
  ok: boolean
  dates: ImportedDate[]
  model_tree: ModelElement | null
  element_assignments: Record<string, string>
  errors: string[]
  warnings: string[]
  notes: string[]
  source_format: 'csv' | 'oxcal'
  analysis_name: string | null
  suggested_analysis_type: 'bayesian' | 'group_calibration'
  date_count: number
  filename?: string
}

interface ColumnDoc {
  name: string
  required: boolean
  description: string
  also_accepted: string[]
}

interface Props {
  open: boolean
  onClose: () => void
  /**
   * What the dates are being imported INTO, which changes what can be kept
   * and what the confirm button says:
   *
   * 'collection'  a project's date collection. It holds dates, not models,
   *               so a model structure in the file cannot come along -- the
   *               dialog says so rather than dropping it quietly.
   * 'analysis'    a draft analysis, which keeps the structure too.
   */
  target: 'collection' | 'analysis'
  onConfirm: (preview: ImportPreview) => void | Promise<void>
  confirming?: boolean
}

function formatOutlier(date: ImportedDate): string | null {
  if (!date.outlier_prior) return null
  return `${date.outlier_model} ${Math.round(date.outlier_prior * 1000) / 10}%`
}

/**
 * Upload a .csv or .oxcal file and see what it contains before any of it is
 * kept.
 *
 * The preview step is the point of this dialog rather than a nicety. An
 * import can be right in every value and still be the wrong file, and an
 * OxCal model carries decisions -- which dates are declared residual, where
 * the boundaries sit -- that the user should recognise before running
 * anything on them. Nothing here writes: confirming hands the parsed result
 * to the parent, which puts it through the same endpoints hand-entered dates
 * use.
 */
export default function ImportDatesDialog({
  open,
  onClose,
  target,
  onConfirm,
  confirming = false,
}: Props) {
  const notify = useNotify()
  const inputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [dragging, setDragging] = useState(false)
  const [columns, setColumns] = useState<ColumnDoc[]>([])

  useEffect(() => {
    if (!open) return
    // Served from the parser's own alias table, so the help and the importer
    // cannot disagree about which columns are actually read.
    fetch('/api/import/csv-columns')
      .then((r) => r.json())
      .then((data) => {
        if (data.success) setColumns(data.data.columns)
      })
      .catch(() => {
        /* The help is optional; failing to load it must not block an import. */
      })
  }, [open])

  useEffect(() => {
    if (!open) {
      setPreview(null)
      setDragging(false)
      setUploading(false)
    }
  }, [open])

  const upload = async (file: File) => {
    setUploading(true)
    setPreview(null)
    try {
      const body = new FormData()
      body.append('file', file)
      const response = await fetch('/api/import/dates', { method: 'POST', body })
      const data = await response.json()
      if (!response.ok || !data.success) {
        notify('Could not read the file: ' + formatApiError(data))
        return
      }
      setPreview(data.data as ImportPreview)
    } catch (err) {
      notify('Error reading the file: ' + (err as Error).message)
    } finally {
      setUploading(false)
    }
  }

  const handleFiles = (files: FileList | null) => {
    const file = files?.[0]
    if (file) upload(file)
  }

  const structureKept = target === 'analysis' && !!preview?.model_tree
  const structureDropped = target === 'collection' && !!preview?.model_tree

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>Import dates from a file</DialogTitle>
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
            handleFiles(e.dataTransfer.files)
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
            Drop a file here, or
          </Typography>
          <Button
            variant="contained"
            onClick={() => inputRef.current?.click()}
            disabled={uploading}
          >
            Choose file
          </Button>
          <input
            ref={inputRef}
            type="file"
            accept=".csv,.tsv,.txt,.oxcal,text/csv,text/plain"
            hidden
            onChange={(e) => {
              handleFiles(e.target.files)
              // Cleared so choosing the same file twice in a row still fires
              // a change event -- otherwise a re-import after an edit to the
              // file appears to do nothing.
              e.target.value = ''
            }}
          />
          <Typography variant="caption" color="textSecondary" sx={{ display: 'block', mt: 1.5 }}>
            A spreadsheet of dates (.csv) or an OxCal model (.oxcal)
          </Typography>
        </Box>

        {uploading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress size={28} />
          </Box>
        )}

        {!preview && !uploading && (
          <Box sx={{ mt: 2 }}>
            <Accordion disableGutters elevation={0} sx={{ border: '1px solid', borderColor: 'divider' }}>
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Typography variant="subtitle2">What a CSV should look like</Typography>
              </AccordionSummary>
              <AccordionDetails>
                <Typography variant="body2" color="textSecondary" sx={{ mb: 1.5 }}>
                  One date per row, with a header row naming the columns. Only{' '}
                  <code>sample_name</code>, <code>age_bp</code> and <code>error_1sigma</code> are
                  required — everything else takes the same default a typed-in date would. Column
                  order does not matter, and common spellings are understood (<code>Lab Code</code>,{' '}
                  <code>Age BP</code>, <code>± 1σ</code>, <code>sd</code>…).
                </Typography>
                <Button
                  size="small"
                  startIcon={<DownloadIcon />}
                  component={Link}
                  href="/api/import/csv-template"
                  sx={{ mb: 2 }}
                >
                  Download template
                </Button>
                <TableContainer component={Paper} variant="outlined" sx={{ maxHeight: 260 }}>
                  <Table size="small" stickyHeader>
                    <TableHead>
                      <TableRow>
                        <TableCell>Column</TableCell>
                        <TableCell>Meaning</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {columns.map((column) => (
                        <TableRow key={column.name}>
                          <TableCell sx={{ whiteSpace: 'nowrap', verticalAlign: 'top' }}>
                            <code>{column.name}</code>
                            {column.required && (
                              <Chip size="small" color="primary" label="required" sx={{ ml: 0.5 }} />
                            )}
                          </TableCell>
                          <TableCell>
                            <Typography variant="body2">{column.description}</Typography>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
                <Typography variant="caption" color="textSecondary" sx={{ display: 'block', mt: 1.5 }}>
                  An OxCal .oxcal file needs none of this — its Sequences, Phases, Boundaries and
                  outlier models are read directly. Anything this app does not model is refused
                  rather than approximated.
                </Typography>
              </AccordionDetails>
            </Accordion>
          </Box>
        )}

        {preview && (
          <Box sx={{ mt: 3 }}>
            <Stack direction="row" spacing={1} sx={{ mb: 2, flexWrap: 'wrap', gap: 1 }}>
              <Chip
                size="small"
                color={preview.ok ? 'success' : 'error'}
                label={preview.ok ? `${preview.date_count} dates read` : 'Not imported'}
              />
              <Chip
                size="small"
                variant="outlined"
                label={preview.source_format === 'oxcal' ? 'OxCal model' : 'CSV table'}
              />
              {preview.filename && (
                <Chip size="small" variant="outlined" label={preview.filename} />
              )}
              {structureKept && <Chip size="small" variant="outlined" label="with model structure" />}
            </Stack>

            {preview.errors.length > 0 && (
              <Alert severity="error" sx={{ mb: 2 }}>
                <AlertTitle>
                  Nothing was imported — {preview.errors.length} problem
                  {preview.errors.length === 1 ? '' : 's'} in the file
                </AlertTitle>
                <Typography variant="body2" sx={{ mb: 1 }}>
                  An import is all or nothing, so the file is unchanged and no dates were added.
                  Fix these and choose it again.
                </Typography>
                <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                  {preview.errors.map((message, index) => (
                    <li key={index}>
                      <Typography variant="body2">{message}</Typography>
                    </li>
                  ))}
                </Box>
              </Alert>
            )}

            {preview.warnings.length > 0 && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                <AlertTitle>Worth checking before you continue</AlertTitle>
                <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                  {preview.warnings.map((message, index) => (
                    <li key={index}>
                      <Typography variant="body2">{message}</Typography>
                    </li>
                  ))}
                </Box>
              </Alert>
            )}

            {structureDropped && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                <AlertTitle>The model structure will not be kept</AlertTitle>
                <Typography variant="body2">
                  A project's date collection holds dates, not models. The phase each date sits in
                  is kept as its default context, but the Sequences, Phases and Boundaries in this
                  file are not — import it from a new analysis instead to keep them.
                </Typography>
              </Alert>
            )}

            {preview.notes.length > 0 && (
              <Alert severity="info" sx={{ mb: 2 }}>
                <AlertTitle>How the file was read</AlertTitle>
                <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                  {preview.notes.map((message, index) => (
                    <li key={index}>
                      <Typography variant="body2">{message}</Typography>
                    </li>
                  ))}
                </Box>
              </Alert>
            )}

            {preview.dates.length > 0 && (
              <>
                <Divider sx={{ my: 2 }} />
                <Typography variant="subtitle2" gutterBottom>
                  Dates read from the file
                </Typography>
                <TableContainer component={Paper} variant="outlined" sx={{ maxHeight: 320 }}>
                  <Table size="small" stickyHeader>
                    <TableHead>
                      <TableRow>
                        <TableCell>Name</TableCell>
                        <TableCell align="right">Age BP</TableCell>
                        <TableCell align="right">± 1σ</TableCell>
                        <TableCell>Curve</TableCell>
                        <TableCell>Phase</TableCell>
                        <TableCell>Outlier</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {preview.dates.map((date) => (
                        <TableRow key={date.id}>
                          <TableCell>{date.sample_name}</TableCell>
                          <TableCell align="right">{date.age_bp}</TableCell>
                          <TableCell align="right">{date.error_1sigma}</TableCell>
                          <TableCell>
                            {date.curve_id}
                            {date.delta_r != null && (
                              <Tooltip title="Local marine reservoir offset applied to this date">
                                <Typography variant="caption" color="textSecondary" sx={{ ml: 0.5 }}>
                                  ΔR {date.delta_r}±{date.delta_r_error ?? 0}
                                </Typography>
                              </Tooltip>
                            )}
                          </TableCell>
                          <TableCell>{date.phase_name || '—'}</TableCell>
                          <TableCell>{formatOutlier(date) || '—'}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              </>
            )}
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!preview?.ok || !preview.dates.length || confirming}
          onClick={() => preview && onConfirm(preview)}
        >
          {confirming
            ? 'Importing…'
            : target === 'collection'
              ? `Add ${preview?.date_count ?? 0} date${preview?.date_count === 1 ? '' : 's'} to the collection`
              : `Load ${preview?.date_count ?? 0} date${preview?.date_count === 1 ? '' : 's'}${
                  structureKept ? ' and model' : ''
                }`}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
