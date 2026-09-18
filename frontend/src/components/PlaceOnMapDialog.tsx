/**
 * Putting one position onto every date in a selection.
 *
 * A dialog, unlike the band's other two bulk edits, which are a menu and a
 * popover so the selection stays visible while it is edited. A map cannot do
 * that: it needs the space, and what it puts on screen in exchange -- where
 * these dates are, and where everything else in the collection is -- is more
 * use than the rows it covers.
 *
 * The bulk case is the common one, which is why it is the one with a surface.
 * Dates from one site share a coordinate, so placing fourteen dates is one
 * gesture rather than fourteen, and the single-date form in the table beside
 * this is for correcting one of them afterwards.
 */
import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from '@mui/material'

import SiteMap from '@/components/SiteMap'
import { roundPlacement } from '@/utils/siteMapGeometry'
import type { MapPoint } from '@/utils/siteMapGeometry'

interface Props {
  open: boolean
  onClose: () => void
  /** Every date in the collection, so the selection is placed in context. */
  points: MapPoint[]
  selectedIds: string[]
  onApply: (latitude: number, longitude: number) => void
  /** Remove the position from the selection: both halves, or neither. */
  onClear: () => void
  /** The project's basemap-tiles setting, passed straight to the map. */
  tiles?: boolean | null
  busy?: boolean
  /**
   * What is being placed, when it is not the selected dates.
   *
   * The dialog's own copy counts dates and says the position is written to
   * every one of them, which is right in the collection and wrong anywhere
   * else. A dispersal ORIGIN is one point and no date at all -- reusing this
   * without saying so produced a dialog headed "Place 0 dates on the map",
   * which is how this prop came to exist.
   */
  title?: string
  helper?: string
  /** What the button that removes the position says. */
  clearLabel?: string
}

const inRange = (value: number, limit: number) =>
  Number.isFinite(value) && value >= -limit && value <= limit

export default function PlaceOnMapDialog({
  open,
  onClose,
  points,
  selectedIds,
  onApply,
  onClear,
  tiles = false,
  busy = false,
  title,
  helper,
  clearLabel,
}: Props) {
  // The pending position, as text, because these boxes are also where someone
  // pastes a coordinate out of a site record -- which is the accurate route,
  // the map being a way to say roughly where rather than exactly where.
  const [latitude, setLatitude] = useState('')
  const [longitude, setLongitude] = useState('')

  // Opening on the last edit's leftovers would offer to place this selection
  // at the previous one's position, one click away.
  useEffect(() => {
    if (open) {
      setLatitude('')
      setLongitude('')
    }
  }, [open])

  const selected = new Set(selectedIds)
  const count = selectedIds.length
  const alreadyPlaced = points.filter((p) => selected.has(p.id) && p.latitude != null).length

  const latitudeNumber = Number(latitude)
  const longitudeNumber = Number(longitude)
  const typedBoth = latitude.trim() !== '' && longitude.trim() !== ''
  const valid = typedBoth && inRange(latitudeNumber, 90) && inRange(longitudeNumber, 180)
  // Half of one is the error worth naming, since it is the state the API
  // refuses and the one a half-finished edit leaves behind.
  const half = (latitude.trim() !== '') !== (longitude.trim() !== '')

  // The pending position is drawn as if it were already applied, so the reader
  // sees where the selection is going rather than where it is. Nothing is
  // written until Apply.
  const preview: MapPoint[] = valid
    ? points.map((p) =>
        selected.has(p.id)
          ? { ...p, latitude: latitudeNumber, longitude: longitudeNumber }
          : p,
      )
    : points

  const place = (lat: number, lon: number) => {
    setLatitude(String(lat))
    setLongitude(String(lon))
  }

  // lg rather than md: the map is the dialog, and at md it was a 900px strip on
  // a desktop screen with room for twice that.
  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>
        {title ?? `Place ${count} date${count === 1 ? '' : 's'} on the map`}
      </DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {helper ?? (
            <>
              Click the map, or type the position. It is written to every selected date — dates
              from one site share a coordinate, which is why this is one edit rather than {count}.
            </>
          )}
          {!tiles &&
            ' The basemap is the offline one, which is accurate to about a kilometre. Paste a recorded position for anything finer.'}
        </Typography>

        <SiteMap
          points={preview}
          selectedIds={selectedIds}
          editable
          onPlace={place}
          tiles={tiles}
          height={380}
          exportName="site-map"
        />

        <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
          <TextField
            label="Latitude"
            size="small"
            type="number"
            value={latitude}
            onChange={(e) => setLatitude(e.target.value)}
            inputProps={{ min: -90, max: 90, step: 'any' }}
            error={latitude.trim() !== '' && !inRange(latitudeNumber, 90)}
            fullWidth
          />
          <TextField
            label="Longitude"
            size="small"
            type="number"
            value={longitude}
            onChange={(e) => setLongitude(e.target.value)}
            inputProps={{ min: -180, max: 180, step: 'any' }}
            error={longitude.trim() !== '' && !inRange(longitudeNumber, 180)}
            fullWidth
          />
          <Button
            size="small"
            onClick={() => {
              setLatitude('')
              setLongitude('')
            }}
            disabled={!latitude && !longitude}
          >
            Reset
          </Button>
        </Stack>

        <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
          Decimal degrees, WGS84 — minus for south and west. A click on the map is rounded to{' '}
          {String(roundPlacement(0.12345))} of a degree, about ten metres, which is already finer
          than this basemap can draw.
        </Typography>

        {half && (
          <Alert severity="warning" sx={{ mt: 2 }}>
            A position needs both halves. Fill the other, or clear both.
          </Alert>
        )}

        {alreadyPlaced > 0 && title === undefined && (
          <Alert severity="info" sx={{ mt: 2 }}>
            {alreadyPlaced} of the {count} selected date{alreadyPlaced === 1 ? ' is' : 's are'}{' '}
            already placed. Applying moves {alreadyPlaced === 1 ? 'it' : 'them'}.
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        {/* Clearing is the other half of placing, and it belongs here rather
            than in the table: unplacing fourteen dates one at a time is the
            same fourteen-gesture problem this dialog exists to solve. */}
        <Button
          onClick={() => {
            onClear()
            onClose()
          }}
          disabled={busy || (clearLabel === undefined && alreadyPlaced === 0)}
          sx={{ mr: 'auto' }}
        >
          {clearLabel ?? 'Remove position'}
        </Button>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="contained"
          disabled={busy || !valid}
          onClick={() => {
            onApply(latitudeNumber, longitudeNumber)
            onClose()
          }}
        >
          Apply
        </Button>
      </DialogActions>
    </Dialog>
  )
}
