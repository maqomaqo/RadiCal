import { useEffect, useRef, useState } from 'react'
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
  Menu,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import MoreVertIcon from '@mui/icons-material/MoreVert'
import UploadFileIcon from '@mui/icons-material/UploadFile'
import DownloadIcon from '@mui/icons-material/Download'
import EditIcon from '@mui/icons-material/Edit'
import DeleteIcon from '@mui/icons-material/Delete'
import ConfirmDialog from '@/components/ConfirmDialog'
import { formatApiError, describeFetchError } from '@/utils/apiError'
import { saveDownload } from '@/utils/saveDownload'
import { useNotify } from '@/contexts/NotificationContext'
import type { ModelElement } from '@/types'
import type { ModelTemplate } from '@/utils/modelTemplates'

function counts(template: Pick<ModelTemplate, 'phases' | 'boundaries'>): string {
  const phases = `${template.phases} phase${template.phases === 1 ? '' : 's'}`
  const boundaries = `${template.boundaries} boundar${template.boundaries === 1 ? 'y' : 'ies'}`
  return `${phases}, ${boundaries}`
}

/**
 * Save the model being edited as a template: its structure and, where the
 * page has them, its settings. The server drops anything that names a date.
 */
export function SaveTemplateDialog({
  open,
  onClose,
  tree,
  settings,
}: {
  open: boolean
  onClose: () => void
  tree: ModelElement
  settings: Record<string, unknown> | null
}) {
  const notify = useNotify()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open) {
      setName(tree.name || '')
      setDescription('')
    }
  }, [open, tree])

  const save = async () => {
    setSaving(true)
    try {
      const response = await fetch('/api/model-templates', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim() || null,
          model_tree: tree,
          bayesian_config: settings,
        }),
      })
      const data = await response.json()
      if (!response.ok || !data.success) {
        notify('Could not save the template: ' + formatApiError(data))
        return
      }
      notify(`Saved “${data.data.name}” to your templates`, 'success')
      onClose()
    } catch (err) {
      notify('Could not save the template: ' + describeFetchError(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Save as a template</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {settings
            ? 'Keeps the structure and the model settings, without the dates.'
            : 'Keeps the structure, without the dates.'}
        </Typography>
        <TextField
          fullWidth
          autoFocus
          label="Template name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          sx={{ mb: 2 }}
        />
        <TextField
          fullWidth
          multiline
          minRows={2}
          label="Description (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={save} disabled={saving || !name.trim()}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

interface ImportPlan {
  ok: boolean
  errors: string[]
  templates: { name: string; action: 'add' | 'rename' | 'same'; saved_as: string; phases: number; boundaries: number }[]
}

/**
 * The account's templates: apply one, rename or delete it, export some or
 * all of them as a file, or import a file of them.
 */
export function TemplateLibraryDialog({
  open,
  onClose,
  onApply,
}: {
  open: boolean
  onClose: () => void
  onApply: (template: ModelTemplate) => void
}) {
  const notify = useNotify()
  const inputRef = useRef<HTMLInputElement>(null)
  const [templates, setTemplates] = useState<ModelTemplate[]>([])
  const [loading, setLoading] = useState(false)
  const [ticked, setTicked] = useState<Set<string>>(new Set())
  const [rowMenu, setRowMenu] = useState<{ anchor: HTMLElement; template: ModelTemplate } | null>(null)
  const [renaming, setRenaming] = useState<ModelTemplate | null>(null)
  const [renameText, setRenameText] = useState('')
  const [deleting, setDeleting] = useState<ModelTemplate | null>(null)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importPlan, setImportPlan] = useState<ImportPlan | null>(null)
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const response = await fetch('/api/model-templates')
      const data = await response.json()
      if (data.success) setTemplates(data.data.items)
      else notify('Could not load your templates: ' + formatApiError(data))
    } catch (err) {
      notify('Could not load your templates: ' + describeFetchError(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open) {
      load()
      setTicked(new Set())
      setImportFile(null)
      setImportPlan(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const toggle = (id: string) =>
    setTicked((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const exportTemplates = async () => {
    const ids = [...ticked]
    const query = ids.length ? '?' + ids.map((id) => `template=${encodeURIComponent(id)}`).join('&') : ''
    try {
      await saveDownload(`/api/model-templates/export${query}`, 'model-templates.json')
    } catch (err) {
      notify('Could not export the templates: ' + describeFetchError(err))
    }
  }

  const readImport = async (file: File) => {
    setBusy(true)
    setImportFile(file)
    setImportPlan(null)
    try {
      const body = new FormData()
      body.append('file', file)
      const response = await fetch('/api/import/model-templates/preview', { method: 'POST', body })
      const data = await response.json()
      if (!response.ok || !data.success) {
        notify('Could not read the file: ' + formatApiError(data))
        setImportFile(null)
        return
      }
      setImportPlan(data.data)
    } catch (err) {
      notify('Could not read the file: ' + describeFetchError(err))
      setImportFile(null)
    } finally {
      setBusy(false)
    }
  }

  const confirmImport = async () => {
    if (!importFile) return
    setBusy(true)
    try {
      const body = new FormData()
      body.append('file', importFile)
      const response = await fetch('/api/import/model-templates', { method: 'POST', body })
      const data = await response.json()
      if (!response.ok || !data.success) {
        notify('Could not import the templates: ' + formatApiError(data))
        return
      }
      const { added, skipped } = data.data
      notify(
        `Added ${added} template${added === 1 ? '' : 's'}` +
          (skipped ? `; ${skipped} you already had` : ''),
        'success'
      )
      setImportFile(null)
      setImportPlan(null)
      load()
    } catch (err) {
      notify('Could not import the templates: ' + describeFetchError(err))
    } finally {
      setBusy(false)
    }
  }

  const rename = async () => {
    if (!renaming || !renameText.trim()) return
    setBusy(true)
    try {
      const response = await fetch(`/api/model-templates/${renaming.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: renameText.trim() }),
      })
      const data = await response.json()
      if (!response.ok || !data.success) {
        notify('Could not rename the template: ' + formatApiError(data))
        return
      }
      setRenaming(null)
      load()
    } catch (err) {
      notify('Could not rename the template: ' + describeFetchError(err))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!deleting) return
    setBusy(true)
    try {
      const response = await fetch(`/api/model-templates/${deleting.id}`, { method: 'DELETE' })
      const data = await response.json()
      if (!response.ok || !data.success) {
        notify('Could not delete the template: ' + formatApiError(data))
        return
      }
      const gone = deleting.id
      setDeleting(null)
      setTicked((current) => new Set([...current].filter((id) => id !== gone)))
      load()
    } catch (err) {
      notify('Could not delete the template: ' + describeFetchError(err))
    } finally {
      setBusy(false)
    }
  }

  const toAdd = importPlan?.templates.filter((t) => t.action !== 'same').length ?? 0

  return (
    <>
      <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
        <DialogTitle>Model templates</DialogTitle>
        <DialogContent dividers>
          <Stack direction="row" spacing={1} sx={{ mb: 2, flexWrap: 'wrap', gap: 1 }}>
            <Button
              size="small"
              variant="outlined"
              startIcon={<UploadFileIcon />}
              onClick={() => inputRef.current?.click()}
              disabled={busy}
            >
              Import file
            </Button>
            <input
              ref={inputRef}
              type="file"
              accept=".json,application/json"
              hidden
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) readImport(file)
                e.target.value = ''
              }}
            />
            {templates.length > 0 && (
              <Button size="small" variant="outlined" startIcon={<DownloadIcon />} onClick={exportTemplates}>
                {ticked.size ? `Export (${ticked.size})` : 'Export all'}
              </Button>
            )}
          </Stack>

          {importPlan && (
            <Alert
              severity={importPlan.ok ? 'info' : 'error'}
              sx={{ mb: 2 }}
              action={
                importPlan.ok ? (
                  <Stack direction="row" spacing={1}>
                    <Button color="inherit" size="small" onClick={() => setImportPlan(null)}>
                      Cancel
                    </Button>
                    <Button
                      color="inherit"
                      size="small"
                      variant="outlined"
                      onClick={confirmImport}
                      disabled={busy || toAdd === 0}
                    >
                      {toAdd === 0 ? 'Nothing new' : `Add ${toAdd}`}
                    </Button>
                  </Stack>
                ) : undefined
              }
              onClose={importPlan.ok ? undefined : () => setImportPlan(null)}
            >
              <AlertTitle>{importPlan.ok ? importFile?.name : 'Nothing was imported'}</AlertTitle>
              <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                {(importPlan.ok
                  ? importPlan.templates.map(
                      (t) =>
                        `${t.saved_as}: ${counts(t)}` +
                        (t.action === 'same'
                          ? ' (already saved)'
                          : t.action === 'rename'
                            ? ' (renamed; you have a different template by that name)'
                            : '')
                    )
                  : importPlan.errors
                ).map((line, index) => (
                  <li key={index}>
                    <Typography variant="body2">{line}</Typography>
                  </li>
                ))}
              </Box>
            </Alert>
          )}

          {loading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
              <CircularProgress size={28} />
            </Box>
          ) : templates.length === 0 ? (
            <Typography color="text.secondary" sx={{ py: 3, textAlign: 'center' }}>
              No templates yet. Save a model as a template, or import a file of them.
            </Typography>
          ) : (
            <List dense disablePadding>
              {templates.map((template) => (
                <ListItem
                  key={template.id}
                  disableGutters
                  secondaryAction={
                    <Stack direction="row" spacing={0.5} alignItems="center">
                      <Button size="small" variant="contained" onClick={() => onApply(template)}>
                        Apply
                      </Button>
                      <IconButton
                        size="small"
                        aria-label={`Actions for ${template.name}`}
                        onClick={(e) => setRowMenu({ anchor: e.currentTarget, template })}
                      >
                        <MoreVertIcon fontSize="small" />
                      </IconButton>
                    </Stack>
                  }
                  sx={{ pr: 16 }}
                >
                  <ListItemIcon sx={{ minWidth: 40 }}>
                    <Checkbox
                      size="small"
                      edge="start"
                      checked={ticked.has(template.id)}
                      onChange={() => toggle(template.id)}
                      inputProps={{ 'aria-label': `Select ${template.name}` }}
                    />
                  </ListItemIcon>
                  <ListItemText
                    primary={template.name}
                    secondary={
                      template.description
                        ? `${counts(template)} · ${template.description}`
                        : counts(template)
                    }
                    secondaryTypographyProps={{ noWrap: true }}
                  />
                </ListItem>
              ))}
            </List>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>Close</Button>
        </DialogActions>
      </Dialog>

      <Menu anchorEl={rowMenu?.anchor ?? null} open={!!rowMenu} onClose={() => setRowMenu(null)}>
        <MenuItem
          onClick={() => {
            if (rowMenu) {
              setRenaming(rowMenu.template)
              setRenameText(rowMenu.template.name)
            }
            setRowMenu(null)
          }}
        >
          <ListItemIcon>
            <EditIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Rename</ListItemText>
        </MenuItem>
        <MenuItem
          onClick={() => {
            setDeleting(rowMenu?.template ?? null)
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

      <Dialog open={!!renaming} onClose={() => setRenaming(null)} fullWidth maxWidth="xs">
        <DialogTitle>Rename template</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            autoFocus
            label="Template name"
            value={renameText}
            onChange={(e) => setRenameText(e.target.value)}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenaming(null)}>Cancel</Button>
          <Tooltip title={renameText.trim() ? '' : 'A template needs a name'}>
            <span>
              <Button variant="contained" onClick={rename} disabled={busy || !renameText.trim()}>
                Rename
              </Button>
            </span>
          </Tooltip>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={!!deleting}
        title="Delete template?"
        message={
          deleting
            ? `Delete “${deleting.name}”? Analyses already built from it are not changed.`
            : ''
        }
        confirmLabel="Delete"
        danger
        loading={busy}
        onConfirm={remove}
        onCancel={() => setDeleting(null)}
      />
    </>
  )
}
