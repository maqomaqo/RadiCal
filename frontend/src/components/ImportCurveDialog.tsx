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
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import UploadFileIcon from '@mui/icons-material/UploadFile'
import {
  createCurve,
  previewCurve,
  type CurveImportPreview,
  type CurveMetadataInput,
  type CurveSpec,
} from '@/api/curves'
import { useNotify } from '@/contexts/NotificationContext'

/**
 * Import a calibration curve from a file.
 *
 * The preview step is the whole point, more so than for dates. A date list is
 * short enough to read after importing it; a curve is thousands of rows that
 * nobody will check by eye, and a wrong one produces confident, plausible,
 * wrong calendar ages for every sample calibrated against it. So nothing is
 * stored until the file has been read, described, and drawn -- if the shape
 * below is not a calibration curve, it is visible before it is kept.
 *
 * Nothing here parses. The file goes to the backend to be read, and goes
 * again to be stored, so there is one parser and no way for what was
 * previewed to differ from what was kept.
 */

const CURVE_TYPES = [
  { value: 'terrestrial', label: 'Terrestrial (Northern Hemisphere)' },
  { value: 'southern_hemisphere', label: 'Terrestrial (Southern Hemisphere)' },
  { value: 'marine', label: 'Marine' },
]

interface Props {
  open: boolean
  onClose: () => void
  onImported: (curve: CurveSpec) => void
}

/**
 * A curve id from its name.
 *
 * Must agree with `schemas/curve.py`'s `slugify` and satisfy its
 * `SLUG_PATTERN`, or the dialog offers an id the backend then rejects. The id
 * is a primary key, appears in URLs, and is what every date's `curve_id`
 * stores, so it cannot be changed afterwards.
 */
export function slugify(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64)
}

/**
 * The curve, drawn as an inline SVG rather than a Plotly figure.
 *
 * This is a sanity check at a glance -- does the line rise from bottom-left
 * to top-right with wiggles -- not an instrument. The browser page is where a
 * curve is actually examined, and loading a plotting library into a dialog to
 * draw sixty line segments would be the wrong trade.
 */
function CurveSketch({ points }: { points: number[][] }) {
  if (points.length < 2) return null

  const width = 520
  const height = 140
  const cals = points.map((p) => p[0])
  const c14s = points.map((p) => p[1])
  const calMin = Math.min(...cals)
  const calMax = Math.max(...cals)
  const c14Min = Math.min(...c14s)
  const c14Max = Math.max(...c14s)
  const x = (cal: number) => ((cal - calMin) / (calMax - calMin || 1)) * width
  const y = (c14: number) => height - ((c14 - c14Min) / (c14Max - c14Min || 1)) * height

  const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(' ')
  // The ±1σ envelope, out and back, so the band reads as the curve's own
  // uncertainty rather than two unrelated lines.
  const band = [
    ...points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p[0]).toFixed(1)},${y(p[1] + p[2]).toFixed(1)}`),
    ...points.slice().reverse().map((p) => `L${x(p[0]).toFixed(1)},${y(p[1] - p[2]).toFixed(1)}`),
    'Z',
  ].join(' ')

  return (
    <Box sx={{ mt: 2 }}>
      <Box
        component="svg"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="The uploaded curve: radiocarbon age against calendar age"
        sx={{ width: '100%', height: 'auto', display: 'block' }}
      >
        <path d={band} fill="rgba(74, 125, 196, 0.25)" stroke="none" />
        <path d={line} fill="none" stroke="#4a7dc4" strokeWidth={1.5} />
      </Box>
      <Typography variant="caption" color="textSecondary">
        Radiocarbon age (vertical) against calendar age (horizontal, younger at
        the left), with the curve's own ±1σ band. A calibration curve rises
        left to right with wiggles; anything else means the columns were read
        the wrong way round.
      </Typography>
    </Box>
  )
}

export default function ImportCurveDialog({ open, onClose, onImported }: Props) {
  const notify = useNotify()
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [calendar, setCalendar] = useState<'cal_bp' | 'bc_ad'>('cal_bp')
  const [preview, setPreview] = useState<CurveImportPreview | null>(null)
  const [uploading, setUploading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [dragging, setDragging] = useState(false)

  const [curveId, setCurveId] = useState('')
  const [meta, setMeta] = useState<CurveMetadataInput>({
    name: '',
    description: '',
    reference: '',
    doi: '',
    type: 'terrestrial',
    sample_type: 'terrestrial',
    aliases: [],
  })
  const [aliasText, setAliasText] = useState('')

  useEffect(() => {
    if (open) return
    setFile(null)
    setPreview(null)
    setCalendar('cal_bp')
    setCurveId('')
    setAliasText('')
    setMeta({
      name: '', description: '', reference: '', doi: '',
      type: 'terrestrial', sample_type: 'terrestrial', aliases: [],
    })
  }, [open])

  const read = async (chosen: File, cal: 'cal_bp' | 'bc_ad') => {
    setUploading(true)
    setPreview(null)
    try {
      const result = await previewCurve(chosen, cal)
      setPreview(result)
      if (result.ok) {
        // The file's own header is where a curve's citation lives, so these
        // are offered rather than left to be retyped. Every one is editable.
        setMeta((current) => ({
          ...current,
          name: current.name || result.suggested_name || '',
          reference: current.reference || result.suggested_reference || '',
          sample_type: (result.suggested_name || '').toLowerCase().includes('marine')
            ? 'marine'
            : current.sample_type,
          type: (result.suggested_name || '').toLowerCase().includes('marine')
            ? 'marine'
            : current.type,
        }))
        setCurveId((current) => current || result.suggested_id || '')
      }
    } catch (err) {
      notify('Could not read the file: ' + (err as Error).message)
    } finally {
      setUploading(false)
    }
  }

  const handleFile = (chosen: File | undefined) => {
    if (!chosen) return
    setFile(chosen)
    read(chosen, calendar)
  }

  const changeCalendar = (next: 'cal_bp' | 'bc_ad') => {
    setCalendar(next)
    // Re-read rather than reinterpret: the conversion happens in the parser,
    // so the preview on screen has to come from the parser too.
    if (file) read(file, next)
  }

  const save = async () => {
    if (!file || !preview?.ok) return
    setSaving(true)
    try {
      const aliases = aliasText.split(',').map((a) => a.trim()).filter(Boolean)
      const stored = await createCurve(file, { ...meta, aliases }, curveId, calendar)
      notify(`Imported ${stored.name}.`, 'success')
      onImported(stored)
      onClose()
    } catch (err) {
      notify('Could not store the curve: ' + (err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const summary = preview?.summary
  const canSave = !!preview?.ok && !!meta.name.trim() && !!curveId.trim() && !saving

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>Import a calibration curve</DialogTitle>
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
            handleFile(e.dataTransfer.files?.[0])
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
            {file ? file.name : 'Drop a curve file here, or'}
          </Typography>
          <Button variant="contained" onClick={() => inputRef.current?.click()} disabled={uploading}>
            {file ? 'Choose a different file' : 'Choose file'}
          </Button>
          <input
            ref={inputRef}
            type="file"
            accept=".14c,.csv,.tsv,.txt,text/csv,text/plain"
            hidden
            onChange={(e) => {
              handleFile(e.target.files?.[0])
              // Cleared so re-choosing the same file after editing it still
              // fires a change event.
              e.target.value = ''
            }}
          />
          <Typography variant="caption" color="textSecondary" sx={{ display: 'block', mt: 1.5 }}>
            An IntCal-format .14c file, or a CSV with a calendar age, a
            radiocarbon age and a 1σ error column
          </Typography>
        </Box>

        <TextField
          select
          size="small"
          label="Calendar column"
          value={calendar}
          onChange={(e) => changeCalendar(e.target.value as 'cal_bp' | 'bc_ad')}
          sx={{ mt: 2, minWidth: 280 }}
          helperText="What the file's calendar column holds. Converted to cal BP on import."
        >
          <MenuItem value="cal_bp">cal BP (the .14c convention)</MenuItem>
          <MenuItem value="bc_ad">BC / AD years (negative for BC)</MenuItem>
        </TextField>

        {uploading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress size={28} />
          </Box>
        )}

        {preview && !preview.ok && (
          <Alert severity="error" sx={{ mt: 2 }}>
            <AlertTitle>This file was not read, and nothing has been stored</AlertTitle>
            <Box component="ul" sx={{ pl: 2, m: 0 }}>
              {preview.errors.map((error, i) => (
                <li key={i}>
                  <Typography variant="body2">{error}</Typography>
                </li>
              ))}
            </Box>
          </Alert>
        )}

        {preview?.ok && summary && (
          <>
            <Divider sx={{ my: 2 }} />
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              <Chip size="small" label={`${summary.data_points?.toLocaleString()} points`} />
              <Chip
                size="small"
                label={`${summary.cal_min?.toLocaleString()}–${summary.cal_max?.toLocaleString()} cal BP`}
              />
              <Chip
                size="small"
                label={`${summary.c14_min?.toLocaleString()}–${summary.c14_max?.toLocaleString()} ¹⁴C BP`}
              />
              <Chip size="small" label={preview.source_format} variant="outlined" />
            </Stack>

            <CurveSketch points={preview.preview} />

            {preview.notes.length > 0 && (
              <Alert severity="info" sx={{ mt: 2 }}>
                <AlertTitle>What was assumed reading this file</AlertTitle>
                <Box component="ul" sx={{ pl: 2, m: 0 }}>
                  {preview.notes.map((note, i) => (
                    <li key={i}>
                      <Typography variant="body2">{note}</Typography>
                    </li>
                  ))}
                </Box>
              </Alert>
            )}

            <Divider sx={{ my: 2 }} />
            <Typography variant="subtitle2" gutterBottom>
              How this curve should be described
            </Typography>
            <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 2, mt: 1 }}>
              <TextField
                size="small"
                label="Name"
                required
                value={meta.name}
                onChange={(e) => setMeta({ ...meta, name: e.target.value })}
                onBlur={() => setCurveId((current) => current || slugify(meta.name))}
              />
              <TextField
                size="small"
                label="Curve id"
                required
                value={curveId}
                onChange={(e) => setCurveId(slugify(e.target.value))}
                helperText="Used in files and URLs; cannot be changed later"
              />
              <TextField
                select
                size="small"
                label="Type"
                value={meta.type}
                onChange={(e) => {
                  const type = e.target.value
                  setMeta({
                    ...meta,
                    type,
                    // A marine curve is for marine carbon; the pickers use
                    // this to decide what selecting "Marine" switches to.
                    sample_type: type === 'marine' ? 'marine' : 'terrestrial',
                  })
                }}
              >
                {CURVE_TYPES.map((option) => (
                  <MenuItem key={option.value} value={option.value}>
                    {option.label}
                  </MenuItem>
                ))}
              </TextField>
              <TextField
                size="small"
                label="Citation"
                value={meta.reference ?? ''}
                onChange={(e) => setMeta({ ...meta, reference: e.target.value })}
              />
              <TextField
                size="small"
                label="DOI"
                value={meta.doi ?? ''}
                onChange={(e) => setMeta({ ...meta, doi: e.target.value })}
              />
              <TextField
                size="small"
                label="Also known as"
                value={aliasText}
                onChange={(e) => setAliasText(e.target.value)}
                helperText="Comma separated. Lets an imported CSV or .oxcal name this curve."
              />
              <TextField
                size="small"
                label="Notes"
                multiline
                minRows={2}
                value={meta.description ?? ''}
                onChange={(e) => setMeta({ ...meta, description: e.target.value })}
                sx={{ gridColumn: '1 / -1' }}
              />
            </Box>
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={save} disabled={!canSave}>
          {saving ? 'Importing…' : 'Import curve'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
