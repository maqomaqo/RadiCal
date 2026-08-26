import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Link,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import CallMergeIcon from '@mui/icons-material/CallMerge'
import DeleteIcon from '@mui/icons-material/Delete'
import DownloadIcon from '@mui/icons-material/Download'
import ShowChartIcon from '@mui/icons-material/ShowChart'
import EditIcon from '@mui/icons-material/Edit'
import UnarchiveIcon from '@mui/icons-material/Unarchive'
import { Link as RouterLink } from 'react-router-dom'
import ConfirmDialog from '@/components/ConfirmDialog'
import ImportCurveDialog from '@/components/ImportCurveDialog'
import MixCurvesDialog from '@/components/MixCurvesDialog'
import {
  curveExportUrl,
  deleteCurve,
  fetchAllCurves,
  fetchUsage,
  restoreCurve,
  updateCurve,
  type CurveSpec,
} from '@/api/curves'
import { useNotify } from '@/contexts/NotificationContext'

/**
 * The calibration curve library.
 *
 * Two kinds of row: the three shipped curves are files this app owns and
 * cannot be edited or removed, while an imported curve belongs to the user.
 * Everything countable about either -- point count, range, grid spacing -- is
 * measured from the curve that actually loads rather than asserted, which is
 * the only way to see at a glance that an import is the curve you meant.
 */

// 'builtin' reads oddly in a row of curves; the others are already the word
// a user would use.
const KIND_LABELS: Record<string, string> = { builtin: 'bundled' }

const TYPE_LABELS: Record<string, string> = {
  terrestrial: 'Terrestrial (NH)',
  southern_hemisphere: 'Terrestrial (SH)',
  marine: 'Marine',
  mixed: 'Mixed',
}

function range(spec: CurveSpec): string {
  return `${spec.cal_min.toLocaleString()}–${spec.cal_max.toLocaleString()}`
}

export default function Curves() {
  const notify = useNotify()
  const [curves, setCurves] = useState<CurveSpec[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [mixOpen, setMixOpen] = useState(false)
  const [editing, setEditing] = useState<CurveSpec | null>(null)
  const [editDraft, setEditDraft] = useState({ name: '', reference: '', doi: '', description: '', aliases: '' })
  const [saving, setSaving] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<CurveSpec | null>(null)
  const [deleteBlocked, setDeleteBlocked] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setCurves(await fetchAllCurves())
      setError(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const openEdit = (spec: CurveSpec) => {
    setEditing(spec)
    setEditDraft({
      name: spec.name,
      reference: spec.reference ?? '',
      doi: spec.doi ?? '',
      description: spec.description ?? '',
      aliases: (spec.aliases ?? []).join(', '),
    })
  }

  const saveEdit = async () => {
    if (!editing) return
    setSaving(true)
    try {
      await updateCurve(editing.id, {
        name: editDraft.name,
        reference: editDraft.reference,
        doi: editDraft.doi,
        description: editDraft.description,
        aliases: editDraft.aliases.split(',').map((a) => a.trim()).filter(Boolean),
      })
      setEditing(null)
      await load()
    } catch (err) {
      notify('Could not save: ' + (err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  /**
   * Ask what is using a curve before offering to delete it.
   *
   * The backend refuses a delete that would strand a stored analysis, so this
   * is not the check: it turns that refusal into something actionable before
   * the click rather than a 409 after.
   */
  const askDelete = async (spec: CurveSpec) => {
    setDeleteTarget(spec)
    setDeleteBlocked(null)
    try {
      const usage = await fetchUsage(spec.id)
      if (usage.in_use) {
        const parts = []
        if (usage.analysis_ids.length) parts.push(`${usage.analysis_ids.length} analysis/analyses`)
        if (usage.project_ids.length) parts.push(`${usage.project_ids.length} project date collection(s)`)
        if (usage.mixed_curve_ids.length) parts.push(`${usage.mixed_curve_ids.length} mixed curve(s)`)
        setDeleteBlocked(parts.join(', '))
      }
    } catch {
      /* Leave the warning off rather than blocking; the backend still refuses. */
    }
  }

  const runDelete = async (archive: boolean) => {
    if (!deleteTarget) return
    setWorking(true)
    try {
      await deleteCurve(deleteTarget.id, archive)
      notify(archive ? `Archived ${deleteTarget.name}.` : `Deleted ${deleteTarget.name}.`, 'success')
      setDeleteTarget(null)
      await load()
    } catch (err) {
      notify((err as Error).message)
    } finally {
      setWorking(false)
    }
  }

  const restore = async (spec: CurveSpec) => {
    try {
      await restoreCurve(spec.id)
      await load()
    } catch (err) {
      notify('Could not restore: ' + (err as Error).message)
    }
  }

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Paper sx={{ p: 3, mb: 3 }}>
   <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2, flexWrap: 'wrap', gap: 2 }}>
          <Box sx={{ flexGrow: 1, minWidth: 260 }}>
            <Typography variant="h1" gutterBottom>
              Calibration Curves
            </Typography>
             <Typography color="text.secondary" sx={{ maxWidth: '64ch' }}>
              Explore and manage calibration curves here. Three come supplied, and you can import or mix your own. Data below is derived from each curve itself.
              </Typography>
              <Typography variant="body2" color="text.secondary">
            </Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            <Button variant="outlined" startIcon={<CallMergeIcon />} onClick={() => setMixOpen(true)}>
              New Mixed Curve
            </Button>
            <Button variant="contained" startIcon={<AddIcon />} onClick={() => setImportOpen(true)}>
              Import Curve
            </Button>
          </Stack>
        </Box>
      </Paper>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress />
        </Box>
      ) : (
        <TableContainer component={Paper} sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Curve</TableCell>
                <TableCell>Type</TableCell>
                <TableCell align="right">Points</TableCell>
                <TableCell>Range (cal BP)</TableCell>
                <TableCell>Calendar grid</TableCell>
                <TableCell>Citation</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {curves.map((spec) => (
                <TableRow key={spec.id} sx={{ opacity: spec.archived ? 0.55 : 1 }}>
                  <TableCell>
                    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                      <Link
                        component={RouterLink}
                        to={`/curves/${spec.id}/browse`}
                        variant="body2"
                        underline="hover"
                        sx={{ fontWeight: 600 }}
                      >
                        {spec.name}
                      </Link>
                      {/* Three kinds, not two: a mixed curve is neither
                          bundled nor imported from a file, and calling it
                          "imported" hides that it is a recipe over two other
                          curves in this same list. */}
                      <Chip
                        size="small"
                        label={KIND_LABELS[spec.kind] ?? spec.kind}
                        variant="outlined"
                        color={spec.kind === 'builtin' ? 'default' : 'primary'}
                      />
                      {spec.archived && <Chip size="small" label="archived" />}
                    </Stack>
                    <Typography variant="caption" color="textSecondary">
                      {spec.id}
                      {spec.description ? ` — ${spec.description}` : ''}
                    </Typography>
                  </TableCell>
                  <TableCell>{TYPE_LABELS[spec.type] ?? spec.type}</TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                    {spec.data_points.toLocaleString()}
                  </TableCell>
                  <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{range(spec)}</TableCell>
                  <TableCell>
                    <Typography variant="caption">{spec.resolution}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption">
                      {spec.reference}
                      {spec.doi ? ` (${spec.doi})` : ''}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Stack direction="row" spacing={0} justifyContent="flex-end">
                      <Tooltip title="Look at this curve's shape and error bounds">
                        <IconButton
                          size="small"
                          component={RouterLink}
                          to={`/curves/${spec.id}/browse`}
                          aria-label={`Browse ${spec.name}`}
                        >
                          <ShowChartIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Download this curve as a .14c file">
                        <IconButton
                          size="small"
                          component="a"
                          href={curveExportUrl(spec.id)}
                          aria-label={`Download ${spec.name}`}
                        >
                          <DownloadIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      {spec.kind !== 'builtin' && (
                        <>
                          <Tooltip title="Edit description and citation">
                            <IconButton
                              size="small"
                              onClick={() => openEdit(spec)}
                              aria-label={`Edit ${spec.name}`}
                            >
                              <EditIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                          {spec.archived ? (
                            <Tooltip title="Offer this curve again">
                              <IconButton
                                size="small"
                                onClick={() => restore(spec)}
                                aria-label={`Restore ${spec.name}`}
                              >
                                <UnarchiveIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          ) : (
                            <Tooltip title="Remove or archive">
                              <IconButton
                                size="small"
                                onClick={() => askDelete(spec)}
                                aria-label={`Remove ${spec.name}`}
                              >
                                <DeleteIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          )}
                        </>
                      )}
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <ImportCurveDialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={load}
      />

      <MixCurvesDialog open={mixOpen} onClose={() => setMixOpen(false)} onCreated={load} />

      <Dialog open={!!editing} onClose={() => setEditing(null)} fullWidth maxWidth="sm">
        <DialogTitle>Edit {editing?.name}</DialogTitle>
        <DialogContent dividers>
          <Alert severity="info" sx={{ mb: 2 }}>
            A curve's numbers cannot be changed here, and will not be. An analysis
            already run against this curve has to keep meaning what it said — a
            corrected curve is imported as a new one.
          </Alert>
          <Box sx={{ display: 'grid', gap: 2 }}>
            <TextField
              size="small"
              label="Name"
              value={editDraft.name}
              onChange={(e) => setEditDraft({ ...editDraft, name: e.target.value })}
            />
            <TextField
              size="small"
              label="Citation"
              value={editDraft.reference}
              onChange={(e) => setEditDraft({ ...editDraft, reference: e.target.value })}
            />
            <TextField
              size="small"
              label="DOI"
              value={editDraft.doi}
              onChange={(e) => setEditDraft({ ...editDraft, doi: e.target.value })}
            />
            <TextField
              size="small"
              label="Also known as"
              helperText="Comma separated. Lets an imported CSV or .oxcal name this curve."
              value={editDraft.aliases}
              onChange={(e) => setEditDraft({ ...editDraft, aliases: e.target.value })}
            />
            <TextField
              size="small"
              label="Notes"
              multiline
              minRows={2}
              value={editDraft.description}
              onChange={(e) => setEditDraft({ ...editDraft, description: e.target.value })}
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditing(null)}>Cancel</Button>
          <Button variant="contained" onClick={saveEdit} disabled={saving || !editDraft.name.trim()}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={!!deleteTarget}
        title={`Remove ${deleteTarget?.name ?? ''}?`}
        danger
        loading={working}
        confirmLabel={deleteBlocked ? 'Archive' : 'Delete'}
        message={
          deleteBlocked ? (
            <>
              This curve is used by {deleteBlocked}. Deleting it would leave those
              results displayable but impossible to reproduce, so it can only be
              archived: it will disappear from the curve pickers, and those analyses
              will still re-run.
            </>
          ) : (
            <>Nothing has been calibrated against this curve, so it can be deleted outright.</>
          )
        }
        onConfirm={() => runDelete(!!deleteBlocked)}
        onCancel={() => setDeleteTarget(null)}
      />
    </Container>
  )
}
