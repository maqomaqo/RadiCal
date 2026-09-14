// Ring NUMBERS into ring OFFSETS, in the one place a user has both.
//
// People have ring numbers -- "this block is rings 41-50 of the sequence" --
// and the model wants each date's distance from a reference. That conversion
// is a subtraction, and doing it by hand is where the sign goes wrong: a
// wiggle match with the sign inverted does not look wrong, it produces a tight
// range in the WRONG PLACE, which reads exactly like a good answer.
//
// So the app does the subtraction, states which way it runs, and shows the
// resulting offsets before anything is applied. The arithmetic itself lives in
// utils/ringOffsets, where it is tested.
import { useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'
import type { MatrixMeasurement } from '@/types'
import { offsetsFromRingNumbers, shortSeriesWarning } from '@/utils/ringOffsets'

interface Props {
  open: boolean
  onClose: () => void
  /** The series' members, in whatever order the outline shows them. */
  members: MatrixMeasurement[]
  /** What the gaps are counted in, for the labels. */
  gapUnits: string
  /** Applied as {measurementId: ring_offset}, plus the chosen reference. */
  onApply: (offsets: Record<string, number>, referenceSampleId: string) => void
}

export default function RingNumberDialog({
  open,
  onClose,
  members,
  gapUnits,
  onApply,
}: Props) {
  // Kept as strings while typing: a half-entered "-" or "" is a real state,
  // and coercing it to 0 would silently place a sample at the reference.
  const [rings, setRings] = useState<Record<string, string>>({})
  const [referenceId, setReferenceId] = useState<string>('')

  const numbered = useMemo(
    () =>
      members
        .map((m) => ({ id: m.id, ring: Number(rings[m.id]) }))
        .filter((entry) => rings[entry.id] !== undefined && rings[entry.id] !== '' && Number.isFinite(entry.ring)),
    [members, rings]
  )

  const reference = numbered.some((n) => n.id === referenceId) ? referenceId : null
  const offsets = useMemo(
    () => offsetsFromRingNumbers(numbered, reference),
    [numbered, reference]
  )
  const complete = numbered.length === members.length
  const duplicates =
    new Set(numbered.map((n) => n.ring)).size !== numbered.length
  const chosenReference =
    reference ??
    (numbered.length
      ? numbered.reduce((outermost, n) => (n.ring > outermost.ring ? n : outermost), numbered[0]).id
      : '')
  const warning = shortSeriesWarning(Object.values(offsets), gapUnits)

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Set gaps from ring numbers</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          Enter each sample&rsquo;s ring number, counting outwards: ring 1 is the
          innermost and oldest. The gaps are then measured back from whichever
          sample you nominate as the reference — 0 at the reference, increasing
          into the past, so an inner sample gets a larger gap.
        </DialogContentText>

        <Stack spacing={2}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Sample</TableCell>
                <TableCell align="right">Ring number</TableCell>
                <TableCell align="right">Gap ({gapUnits})</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {members.map((member) => (
                <TableRow key={member.id}>
                  <TableCell>{member.sample_name}</TableCell>
                  <TableCell align="right">
                    <TextField
                      size="small"
                      type="number"
                      value={rings[member.id] ?? ''}
                      onChange={(e) =>
                        setRings((current) => ({ ...current, [member.id]: e.target.value }))
                      }
                      inputProps={{ 'aria-label': `Ring number for ${member.sample_name}` }}
                      sx={{ width: 104 }}
                    />
                  </TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                    {offsets[member.id] ?? '—'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          <Box>
            <Typography variant="body2" component="label" htmlFor="wiggle-reference">
              Measure gaps from
            </Typography>
            <Select
              id="wiggle-reference"
              size="small"
              fullWidth
              value={chosenReference}
              onChange={(e) => setReferenceId(e.target.value)}
              displayEmpty
            >
              {members.map((member) => (
                <MenuItem key={member.id} value={member.id}>
                  {member.sample_name}
                </MenuItem>
              ))}
            </Select>
            <Typography variant="caption" color="text.secondary">
              The outermost sample by default — the one a felling date is quoted
              against.
            </Typography>
          </Box>

          {duplicates && (
            <Alert severity="warning">
              Two samples share a ring number. Two samples from one ring are one
              event measured twice, not two members of a series, and the model
              will refuse them.
            </Alert>
          )}
          {warning && <Alert severity="info">{warning}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          disabled={!complete || duplicates}
          onClick={() => {
            onApply(offsets, chosenReference)
            onClose()
          }}
        >
          Apply gaps
        </Button>
      </DialogActions>
    </Dialog>
  )
}
