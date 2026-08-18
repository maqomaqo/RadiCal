import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Slider,
  TextField,
  Typography,
} from '@mui/material'
import { createMixedCurve, type CurveSpec } from '@/api/curves'
import { useCurves } from '@/hooks/useCurves'
import { useNotify } from '@/contexts/NotificationContext'

/**
 * Build a curve from two others in a fixed proportion — OxCal's Mix_Curves.
 *
 * What this is for: a human or animal that ate partly from the sea. Their
 * bone is neither terrestrial nor marine carbon, and calibrating it against
 * either curve alone is wrong by centuries in one direction or the other,
 * with nothing in the result to say so.
 *
 * The uncertainty on the proportion is a required field rather than an
 * optional refinement. A dietary fraction comes from stable isotopes and has
 * real error, and a mixture that ignored it would state a confident age from
 * an admittedly uncertain diet — while looking exactly as trustworthy as one
 * that did not.
 */

interface Props {
  open: boolean
  onClose: () => void
  onCreated: (curve: CurveSpec) => void
}

export default function MixCurvesDialog({ open, onClose, onCreated }: Props) {
  const notify = useNotify()
  const { curves } = useCurves()
  const [a, setA] = useState('intcal20')
  const [b, setB] = useState('marine20')
  const [percentA, setPercentA] = useState(30)
  const [percentError, setPercentError] = useState(10)
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open) return
    setA('intcal20')
    setB('marine20')
    setPercentA(30)
    setPercentError(10)
    setName('')
  }, [open])

  const specA = curves.find((c) => c.id === a)
  const specB = curves.find((c) => c.id === b)

  const suggestedName = useMemo(
    () => (specA && specB ? `${specA.name}/${specB.name} ${percentA}%` : ''),
    [specA, specB, percentA]
  )

  const save = async () => {
    setSaving(true)
    try {
      const created = await createMixedCurve({
        a,
        b,
        percent_a: percentA,
        percent_a_error: percentError,
        name: name.trim() || suggestedName,
      })
      notify(`Created ${created.name}.`, 'success')
      onCreated(created)
      onClose()
    } catch (err) {
      notify((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>New mixed curve</DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
          For a sample whose carbon came from more than one reservoir — most often
          a human or animal with a partly marine diet, whose bone is neither
          terrestrial nor marine carbon.
        </Typography>

        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 2 }}>
          <TextField
            select
            size="small"
            label="First curve"
            value={a}
            onChange={(e) => setA(e.target.value)}
          >
            {curves.map((curve) => (
              <MenuItem key={curve.id} value={curve.id} disabled={curve.id === b}>
                {curve.name}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label="Second curve"
            value={b}
            onChange={(e) => setB(e.target.value)}
          >
            {curves.map((curve) => (
              <MenuItem key={curve.id} value={curve.id} disabled={curve.id === a}>
                {curve.name}
              </MenuItem>
            ))}
          </TextField>
        </Box>

        <Box sx={{ mt: 3 }}>
          <Typography variant="body2" gutterBottom>
            {percentA}% of the carbon from {specA?.name ?? a}, {100 - percentA}% from{' '}
            {specB?.name ?? b}
          </Typography>
          <Slider
            value={percentA}
            onChange={(_, value) => setPercentA(value as number)}
            min={0}
            max={100}
            step={1}
            marks={[
              { value: 0, label: specB?.name ?? b },
              { value: 50, label: '50%' },
              { value: 100, label: specA?.name ?? a },
            ]}
          />
        </Box>

        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 2, mt: 2 }}>
          <TextField
            type="number"
            size="small"
            label="Uncertainty on that percentage (±%)"
            value={percentError}
            onChange={(e) => setPercentError(Math.max(0, Number(e.target.value)))}
          />
          <TextField
            size="small"
            label="Name"
            placeholder={suggestedName}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Box>

        <Alert severity="info" sx={{ mt: 2 }}>
          The uncertainty on the proportion widens the mixed curve most where the
          two curves are furthest apart — which is where being wrong about the
          diet moves a date furthest. Leaving it at zero claims the proportion is
          known exactly.
        </Alert>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={save} disabled={saving || a === b}>
          {saving ? 'Creating…' : 'Create curve'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
