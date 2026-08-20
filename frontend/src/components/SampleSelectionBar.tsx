/**
 * What you can do to the dates you have ticked.
 *
 * A band rather than more buttons in the toolbar above. Those three (Add,
 * Paste, Import) act on the collection and are always available; these act on
 * a selection and mean nothing without one. Keeping them in one row would
 * have left five of eight controls disabled whenever nothing was ticked,
 * which reads as a broken toolbar rather than an inapplicable one — the fault
 * Deploy already had, sitting permanently greyed at the end of the row.
 *
 * So the band appears with the first tick and takes Deploy down with it. It
 * is tinted from the primary, which is the same colour the table already uses
 * to mark a selected row, so the band and the rows it acts on read as one
 * thing.
 *
 * Neither bulk edit is a dialog. Choosing a curve is a menu; a reservoir
 * offset is two numbers and a note about who it lands on, which fits a
 * popover anchored to its own button. Both keep the table visible, so the
 * selection being edited stays on screen while it is edited — which a modal
 * would cover up at exactly the moment it matters.
 */
import { useMemo, useState } from 'react'
import {
  Box,
  Button,
  Divider,
  Menu,
  MenuItem,
  Popover,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'
import DeleteIcon from '@mui/icons-material/Delete'
import RocketLaunchIcon from '@mui/icons-material/RocketLaunch'
import TimelineIcon from '@mui/icons-material/Timeline'
import WavesIcon from '@mui/icons-material/Waves'

import { curveOptions, type CurveSpec } from '@/api/curves'
import { isMarineMeasurement } from '@/components/MeasurementFields'
import type { ProjectSample } from '@/components/ProjectSampleTable'

export interface DeltaRValue {
  delta_r: number | null
  delta_r_error: number | null
}

interface Props {
  selected: ProjectSample[]
  curves: CurveSpec[]
  busy: boolean
  onClear: () => void
  onDeploy: () => void
  onAssignCurve: (curveId: string) => void
  onAssignDeltaR: (value: DeltaRValue) => void
  onDelete: () => void
}

export default function SampleSelectionBar({
  selected,
  curves,
  busy,
  onClear,
  onDeploy,
  onAssignCurve,
  onAssignDeltaR,
  onDelete,
}: Props) {
  const [curveAnchor, setCurveAnchor] = useState<HTMLElement | null>(null)
  const [deltaAnchor, setDeltaAnchor] = useState<HTMLElement | null>(null)
  const [deltaR, setDeltaR] = useState('')
  const [deltaRError, setDeltaRError] = useState('')

  const count = selected.length

  // Which of the ticked dates a reservoir offset actually applies to. Δ R
  // corrects a marine curve's global average, so on a terrestrial date it is
  // not merely redundant — it would be applied at calibration and move the
  // result. The backend skips those; this is the same question asked here so
  // the answer is on screen before the offset is applied rather than reported
  // afterwards.
  const marineCount = useMemo(
    () => selected.filter((s) => isMarineMeasurement(s, curves)).length,
    [selected, curves],
  )
  const skipped = count - marineCount

  // How many dates would change sample type if a given curve were assigned.
  // A marine curve makes a date marine, matching what the single-date form
  // already does in the other direction — worth saying out loud in the menu,
  // since it is a second edit the reader did not ask for by name.
  const typeChangesFor = (curveId: string) => {
    const spec = curves.find((c) => c.id === curveId)
    const next = spec?.sample_type === 'marine' ? 'marine' : 'terrestrial'
    return selected.filter((s) => (s.sample_type || 'terrestrial') !== next).length
  }

  const deltaRNumber = Number(deltaR)
  const deltaRErrorNumber = Number(deltaRError)
  const clearing = deltaR.trim() === '' && deltaRError.trim() === ''
  const deltaRValid =
    clearing ||
    (deltaR.trim() !== '' &&
      Number.isFinite(deltaRNumber) &&
      deltaRError.trim() !== '' &&
      Number.isFinite(deltaRErrorNumber) &&
      deltaRErrorNumber >= 0)

  const closeDelta = () => {
    setDeltaAnchor(null)
    setDeltaR('')
    setDeltaRError('')
  }

  const applyDeltaR = () => {
    onAssignDeltaR(
      clearing
        ? { delta_r: null, delta_r_error: null }
        : { delta_r: deltaRNumber, delta_r_error: deltaRErrorNumber },
    )
    closeDelta()
  }

  return (
    <Box
      sx={(theme) => ({
        display: 'flex',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: 1,
        mb: 2,
        px: 2,
        py: 1.5,
        borderRadius: 1,
        // The same primary tint the table paints a selected row with, so the
        // band reads as an extension of the rows rather than a new surface.
        backgroundColor: alpha(theme.palette.primary.main, 0.08),
        border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
      })}
    >
      <Typography variant="body2" sx={{ fontWeight: 600, mr: 1 }}>
        {count} date{count === 1 ? '' : 's'} selected
      </Typography>
      <Button size="small" onClick={onClear} disabled={busy}>
        Clear
      </Button>

      <Divider
        orientation="vertical"
        flexItem
        sx={{ mx: 1, display: { xs: 'none', md: 'block' } }}
      />

      <Button
        size="small"
        variant="outlined"
        startIcon={<TimelineIcon />}
        onClick={(e) => setCurveAnchor(e.currentTarget)}
        disabled={busy || curves.length === 0}
      >
        Set curve
      </Button>
      <Button
        size="small"
        variant="outlined"
        startIcon={<WavesIcon />}
        onClick={(e) => setDeltaAnchor(e.currentTarget)}
        disabled={busy}
      >
        Set ΔR
      </Button>

      <Box sx={{ flexGrow: 1 }} />

      <Button
        size="small"
        variant="contained"
        color="secondary"
        startIcon={<RocketLaunchIcon />}
        onClick={onDeploy}
        disabled={busy}
      >
        Deploy to New Analysis
      </Button>
      <Divider
        orientation="vertical"
        flexItem
        sx={{ mx: 0.5, display: { xs: 'none', md: 'block' } }}
      />

      {/* Neutral until hover, not a red button sitting beside the primary
          one. Two saturated buttons side by side read as two destructive
          actions and make the reader check which is which every time. This is
          the idiom the table's own per-row delete already uses; red is spent
          on the confirm step, where it is a decision rather than a target. */}
      <Button
        size="small"
        startIcon={<DeleteIcon />}
        onClick={onDelete}
        disabled={busy}
        sx={(theme) => ({
          color: 'text.secondary',
          '&:hover, &:focus-visible': {
            color: 'error.main',
            backgroundColor: alpha(theme.palette.error.main, 0.08),
          },
        })}
      >
        Delete
      </Button>

      <Menu
        anchorEl={curveAnchor}
        open={Boolean(curveAnchor)}
        onClose={() => setCurveAnchor(null)}
      >
        <Typography variant="caption" sx={{ px: 2, py: 1, display: 'block', color: 'text.secondary' }}>
          Calibrate {count} date{count === 1 ? '' : 's'} against
        </Typography>
        {curveOptions(curves, undefined).map((option) => {
          const changes = typeChangesFor(option.id)
          return (
            <MenuItem
              key={option.id}
              onClick={() => {
                setCurveAnchor(null)
                onAssignCurve(option.id)
              }}
            >
              <Box>
                <Typography variant="body2">{option.label}</Typography>
                {changes > 0 && (
                  <Typography variant="caption" color="text.secondary">
                    also sets {changes} to{' '}
                    {curves.find((c) => c.id === option.id)?.sample_type === 'marine'
                      ? 'marine'
                      : 'terrestrial'}
                  </Typography>
                )}
              </Box>
            </MenuItem>
          )
        })}
      </Menu>

      <Popover
        anchorEl={deltaAnchor}
        open={Boolean(deltaAnchor)}
        onClose={closeDelta}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
      >
        <Box sx={{ p: 2, width: 320 }}>
          <Typography variant="subtitle2" gutterBottom>
            Local reservoir offset
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            ΔR is the local deviation from the global average a marine curve already models — one
            value for a site or water body, not a whole reservoir age.
          </Typography>

          <Stack direction="row" spacing={1} sx={{ mb: 1.5 }}>
            <TextField
              label="ΔR"
              size="small"
              type="number"
              value={deltaR}
              onChange={(e) => setDeltaR(e.target.value)}
              autoFocus
              fullWidth
            />
            <TextField
              label="± 1σ"
              size="small"
              type="number"
              value={deltaRError}
              onChange={(e) => setDeltaRError(e.target.value)}
              error={deltaR.trim() !== '' && deltaRError.trim() === ''}
              fullWidth
            />
          </Stack>

          <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 2 }}>
            {clearing ? (
              <>Leave both empty to remove the offset from the marine dates selected.</>
            ) : skipped > 0 ? (
              <>
                Applies to {marineCount} of {count} selected — {skipped} terrestrial date
                {skipped === 1 ? ' is' : 's are'} skipped, since ΔR corrects a marine curve.
              </>
            ) : (
              <>
                Applies to all {count} selected date{count === 1 ? '' : 's'}. An offset is always
                published with its own uncertainty, which is combined into each date's error.
              </>
            )}
          </Typography>

          <Stack direction="row" spacing={1} justifyContent="flex-end">
            <Button size="small" onClick={closeDelta}>
              Cancel
            </Button>
            <Tooltip title={deltaRValid ? '' : 'Enter ΔR and its uncertainty, or leave both empty to clear'}>
              <span>
                <Button
                  size="small"
                  variant="contained"
                  onClick={applyDeltaR}
                  disabled={!deltaRValid || (marineCount === 0 && !clearing)}
                >
                  {clearing ? 'Clear ΔR' : 'Apply'}
                </Button>
              </span>
            </Tooltip>
          </Stack>

          {marineCount === 0 && !clearing && (
            <Typography variant="caption" color="error" display="block" sx={{ mt: 1 }}>
              None of the selected dates is marine, so there is nothing to apply this to.
            </Typography>
          )}
        </Box>
      </Popover>
    </Box>
  )
}
