/**
 * What you can do to the dates you have ticked.
 *
 * A band rather than more buttons in the toolbar above: Add, Paste and Import
 * act on the collection and are always available, where these act on a
 * selection and mean nothing without one. One row would leave five of eight
 * controls disabled whenever nothing is ticked, reading as a broken toolbar
 * rather than an inapplicable one.
 *
 * So the band appears with the first tick and takes Deploy down with it,
 * tinted from the primary the table already marks a selected row with, so the
 * band and the rows read as one thing.
 *
 * Neither bulk edit is a dialog: choosing a curve is a menu, and a reservoir
 * offset is two numbers and a note about who it lands on, which fits a popover
 * anchored to its button. Both keep the selection on screen while it is
 * edited, where a modal would cover it.
 */
import { useCallback, useMemo, useRef, useState } from 'react'
import {
  Box,
  Button,
  Divider,
  FormControlLabel,
  InputAdornment,
  Menu,
  MenuItem,
  Popover,
  type PopoverActions,
  Radio,
  RadioGroup,
  Stack,
  Tab,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'
import { tint } from '@/theme/diagramColors'
import { PAPER } from '@/theme/surfaces'
import DeleteIcon from '@mui/icons-material/Delete'
import RocketLaunchIcon from '@mui/icons-material/RocketLaunch'
import TimelineIcon from '@mui/icons-material/Timeline'
import WavesIcon from '@mui/icons-material/Waves'
import PlaceIcon from '@mui/icons-material/Place'
import DownloadIcon from '@mui/icons-material/Download'

import { curveOptions, type CurveSpec } from '@/api/curves'
import { isMarineMeasurement } from '@/components/MeasurementFields'
import DeltaRLookupPanel from '@/components/DeltaRLookupPanel'
import type { DeltaRLookupValue } from '@/components/DeltaRLookupPanel'
import type { ProjectSample } from '@/components/ProjectSampleTable'
import {
  datesAtSameLocation,
  datesWithinKm,
  placedCount,
  selectedLocations,
} from '@/utils/sampleSelection'
import { SITE_RADIUS_KM } from '@/utils/siteGrouping'

export interface DeltaRValue {
  delta_r: number | null
  delta_r_error: number | null
  /**
   * Where a derived offset came from. Absent for a typed one, and its
   * ABSENCE is what clears any derivation already stored -- see §5.5:
   * provenance that survives a hand edit is a lie about the number beside it.
   */
  delta_r_source?: Record<string, unknown>
}

/**
 * Who a ΔR lands on: the ticked dates, or the ticked dates widened to their
 * site. A reservoir offset belongs to a place, and a collection from many
 * places is ticked a place at a time -- so the widening is offered here, at
 * the point of applying, rather than asking for every date at the site to be
 * found and ticked first.
 */
type DeltaScope = 'selection' | 'location' | 'radius'

interface Props {
  selected: ProjectSample[]
  /**
   * The whole collection, so a ΔR can be widened from the ticked dates to
   * every date at the same site.
   */
  collection: ProjectSample[]
  curves: CurveSpec[]
  busy: boolean
  onClear: () => void
  onDeploy: () => void
  onAssignCurve: (curveId: string) => void
  /** `targetIds` is the selection, or the selection widened to its site. */
  onAssignDeltaR: (value: DeltaRValue, targetIds: string[]) => void
  /**
   * Opens the map. Unlike the two edits beside it this one is a dialog and is
   * therefore owned by the caller -- see PlaceOnMapDialog for why a map cannot
   * keep the selection on screen the way a popover does.
   */
  onPlaceOnMap: () => void
  /** Saves the selection as a CSV the importer reads back in. */
  onExport: () => void
  onDelete: () => void
}

export default function SampleSelectionBar({
  selected,
  collection,
  curves,
  busy,
  onClear,
  onDeploy,
  onAssignCurve,
  onAssignDeltaR,
  onPlaceOnMap,
  onExport,
  onDelete,
}: Props) {
  const [curveAnchor, setCurveAnchor] = useState<HTMLElement | null>(null)
  const [deltaAnchor, setDeltaAnchor] = useState<HTMLElement | null>(null)
  // The popover fixes its top edge once, when it opens, and MUI moves it
  // again only on a window resize. Its content is anything but fixed: the
  // derived tab is several times taller than the typed one, and it grows
  // again as a lookup returns and the point list appears. Without this, a
  // popover opened on the short tab ran off the bottom of the window once
  // the tall one was chosen, with Apply below the fold and no way to scroll
  // to it. So the content is watched, and every change in its size asks the
  // popover to re-place itself -- which it does by moving up, and past that
  // by scrolling inside its own paper.
  const deltaActions = useRef<PopoverActions | null>(null)
  const deltaResize = useRef<ResizeObserver | null>(null)
  const watchDeltaContent = useCallback((node: HTMLElement | null) => {
    deltaResize.current?.disconnect()
    deltaResize.current = null
    if (!node || typeof ResizeObserver === 'undefined') return
    deltaResize.current = new ResizeObserver(() => deltaActions.current?.updatePosition())
    deltaResize.current.observe(node)
  }, [])
  const [deltaR, setDeltaR] = useState('')
  const [deltaRError, setDeltaRError] = useState('')
  // Typed or derived. Typing is first because it is what works without a
  // reservoir database ingested, and because a date whose position is not
  // recorded has nothing to derive from.
  const [deltaTab, setDeltaTab] = useState<'typed' | 'derived'>('typed')

  const count = selected.length

  // Widening the ΔR from the selection to its site. The radius defaults to
  // the distance the site-grouping rule uses, so "the same site" means the
  // same thing here as it does when a model of sites is built from the
  // collection.
  const [deltaScope, setDeltaScope] = useState<DeltaScope>('selection')
  const [radiusKm, setRadiusKm] = useState(String(SITE_RADIUS_KM))
  const radius = Number(radiusKm)
  const radiusValid = radiusKm.trim() !== '' && Number.isFinite(radius) && radius >= 0

  const locations = useMemo(() => selectedLocations(selected), [selected])
  const placed = useMemo(() => placedCount(selected), [selected])
  const atLocation = useMemo(
    () => datesAtSameLocation(collection, selected),
    [collection, selected],
  )
  const withinRadius = useMemo(
    () => datesWithinKm(collection, selected, radiusValid ? radius : Number.NaN),
    [collection, selected, radius, radiusValid],
  )
  // Offered only where the selection has something to widen by. A selection
  // with neither a name nor a position gets the popover it always had.
  const canWiden = locations.length > 0 || placed > 0
  const targets =
    deltaScope === 'location' ? atLocation : deltaScope === 'radius' ? withinRadius : selected
  const targetCount = targets.length

  // Which of the target dates a reservoir offset applies to. ΔR corrects a
  // marine curve's global average, so on a terrestrial date it is not merely
  // redundant -- it would be applied at calibration and move the result. The
  // backend skips those; asking here puts the answer on screen first.
  const marineCount = useMemo(
    () => targets.filter((s) => isMarineMeasurement(s, curves)).length,
    [targets, curves],
  )
  const skipped = targetCount - marineCount

  // How many dates would change sample type if a given curve were assigned. A
  // marine curve makes a date marine, as the single-date form does in the other
  // direction -- worth saying in the menu, being an edit not asked for by name.
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

  // Keeping the ΔR popover on screen as it grows. See the Popover below.
  const popoverAction = useRef<{ updatePosition: () => void } | null>(null)
  const observer = useRef<ResizeObserver | null>(null)
  const popoverPaper = useCallback((node: HTMLElement | null) => {
    observer.current?.disconnect()
    if (!node || typeof ResizeObserver === 'undefined') return
    observer.current = new ResizeObserver(() => popoverAction.current?.updatePosition())
    observer.current.observe(node)
  }, [])

  const closeDelta = () => {
    setDeltaAnchor(null)
    setDeltaR('')
    setDeltaRError('')
    setDeltaTab('typed')
    setDeltaScope('selection')
    setRadiusKm(String(SITE_RADIUS_KM))
  }

  const targetIds = () => targets.map((s) => s.id)

  const applyDeltaR = () => {
    // No delta_r_source: this is a number somebody typed, and sending none is
    // what clears a derivation that no longer describes it.
    onAssignDeltaR(
      clearing
        ? { delta_r: null, delta_r_error: null }
        : { delta_r: deltaRNumber, delta_r_error: deltaRErrorNumber },
      targetIds(),
    )
    closeDelta()
  }

  const applyDerivedDeltaR = (value: DeltaRLookupValue) => {
    onAssignDeltaR(
      {
        delta_r: value.delta_r,
        delta_r_error: value.delta_r_error,
        delta_r_source: value.delta_r_source,
      },
      targetIds(),
    )
    closeDelta()
  }

  // The scope's own wording: how many dates each choice reaches, and what it
  // reaches them by. Said in numbers so the choice is checkable against the
  // table before Apply, not after.
  const locationLabel =
    locations.length === 1
      ? `Every date at “${locations[0]}” (${atLocation.length})`
      : `Every date at the same ${locations.length} locations (${atLocation.length})`
  const radiusLabel = radiusValid
    ? `Every date within this distance (${withinRadius.length})`
    : 'Every date within this distance'

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
        // Sticky under the app bar: this band sits above a table that runs to
        // well over a screen, so ticking a row near the bottom used to put
        // every control that acts on it -- Deploy included -- off screen.
        position: 'sticky',
        top: { xs: 56, sm: 64 },
        zIndex: theme.zIndex.appBar - 1,
        // The primary tint the table paints a selected row with, so the band
        // reads as an extension of the rows rather than a new surface.
        // Composited rather than translucent, because a sticky band with an
        // alpha fill shows the rows sliding underneath it.
        backgroundColor: tint(theme.palette.primary.main, 0.08, PAPER),
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
      {/* Beside ΔR because it is the same kind of edit: one value that
          belongs to a PLACE rather than to a measurement, so it lands on the
          whole selection at once. */}
      <Button
        size="small"
        variant="outlined"
        startIcon={<PlaceIcon />}
        onClick={onPlaceOnMap}
        disabled={busy}
      >
        Place on map
      </Button>
      <Button
        size="small"
        variant="outlined"
        startIcon={<DownloadIcon />}
        onClick={onExport}
        disabled={busy}
      >
        Export CSV
      </Button>

      <Box sx={{ flexGrow: 1 }} />

      <Button
        size="small"
        variant="contained"
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
        // MUI positions a popover ONCE, when it opens. This one grows after
        // that: the lookup tab fetches, and ten point rows and a
        // counterfactual arrive into a paper that was measured while it still
        // held a two-field form. Measured in the real app, that is 709px of
        // popover opening at y=629 on a 1200px viewport -- so Cancel and Apply
        // sit 138px below the fold, with nothing to scroll, because the paper
        // had already grown to fit. The buttons were simply unreachable.
        //
        // `action.updatePosition()` re-runs MUI's own clamp, which moves the
        // paper up so it fits. A ResizeObserver drives it, rather than the two
        // components coordinating: anything that changes the height -- the tab,
        // the fetch, excluding a point, the suspension-only toggle -- is the
        // same event to a box that got taller.
        action={popoverAction}
        slotProps={{ paper: { ref: popoverPaper, sx: { maxHeight: 'calc(100vh - 32px)' } } }}
      >
        <Box ref={watchDeltaContent}>
        <Tabs
          value={deltaTab}
          onChange={(_event, value) => setDeltaTab(value)}
          variant="fullWidth"
          sx={{ borderBottom: 1, borderColor: 'divider' }}
        >
          <Tab value="typed" label="Enter a value" />
          {/* Second, not first: it needs a reservoir database ingested and a
              position on the dates, and neither is guaranteed. */}
          <Tab value="derived" label="Derive from position" />
        </Tabs>

        {/* Who gets it, before what it is: the same block under either tab,
            so the derived panel's Apply and the typed one count the same
            dates. Not shown when there is nothing to widen by. */}
        {canWiden && (
          <Box sx={{ px: 2, pt: 1.5, pb: 1, borderBottom: 1, borderColor: 'divider' }}>
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 0.5 }}>
              Apply to
            </Typography>
            <RadioGroup
              value={deltaScope}
              onChange={(_event, value) => setDeltaScope(value as DeltaScope)}
            >
              <FormControlLabel
                value="selection"
                control={<Radio size="small" />}
                disabled={busy}
                label={`The ${count} selected date${count === 1 ? '' : 's'}`}
                slotProps={{ typography: { variant: 'body2' } }}
              />
              <Tooltip
                title={locations.length === 0 ? 'None of the selected dates has a location name.' : ''}
                placement="right"
              >
                <FormControlLabel
                  value="location"
                  control={<Radio size="small" />}
                  disabled={busy || locations.length === 0}
                  label={locations.length === 0 ? 'Every date at the same location' : locationLabel}
                  slotProps={{ typography: { variant: 'body2' } }}
                />
              </Tooltip>
              <Tooltip
                title={placed === 0 ? 'None of the selected dates has a position.' : ''}
                placement="right"
              >
                <FormControlLabel
                  value="radius"
                  control={<Radio size="small" />}
                  disabled={busy || placed === 0}
                  label={placed === 0 ? 'Every date within a distance' : radiusLabel}
                  slotProps={{ typography: { variant: 'body2' } }}
                />
              </Tooltip>
            </RadioGroup>
            {deltaScope === 'radius' && (
              <TextField
                size="small"
                type="number"
                label="Distance from a selected date"
                value={radiusKm}
                onChange={(e) => setRadiusKm(e.target.value)}
                error={!radiusValid}
                helperText={
                  radiusValid
                    ? radius === 0
                      ? 'Zero is the same coordinate exactly.'
                      : undefined
                    : 'A distance in kilometres, 0 or more.'
                }
                inputProps={{ min: 0, step: 'any' }}
                InputProps={{ endAdornment: <InputAdornment position="end">km</InputAdornment> }}
                sx={{ mt: 1, ml: 4, width: 240 }}
                disabled={busy}
              />
            )}
          </Box>
        )}

        {deltaTab === 'derived' ? (
          <DeltaRLookupPanel
            // Looked up at the SELECTION's position, whatever the scope: the
            // widened set is where the value lands, not where it comes from.
            dates={selected}
            marineCount={marineCount}
            onApply={applyDerivedDeltaR}
            onCancel={closeDelta}
            busy={busy || (deltaScope === 'radius' && !radiusValid)}
          />
        ) : (
        // Wide enough for both tab labels on one line each; at 320 the
        // second wrapped.
        <Box sx={{ p: 2, width: 360 }}>
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
              <>
                Leave both empty to remove the offset from the marine dates
                {deltaScope === 'selection' ? ' selected' : ' chosen above'}.
              </>
            ) : skipped > 0 ? (
              <>
                Applies to {marineCount} of {targetCount} — {skipped} terrestrial date
                {skipped === 1 ? ' is' : 's are'} skipped, since ΔR corrects a marine curve.
              </>
            ) : (
              <>
                Applies to all {targetCount} date{targetCount === 1 ? '' : 's'}. An offset is always
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
                  disabled={
                    !deltaRValid ||
                    (marineCount === 0 && !clearing) ||
                    (deltaScope === 'radius' && !radiusValid)
                  }
                >
                  {clearing ? 'Clear ΔR' : marineCount === targetCount ? `Apply to ${targetCount}` : `Apply to ${marineCount}`}
                </Button>
              </span>
            </Tooltip>
          </Stack>

          {marineCount === 0 && !clearing && (
            <Typography variant="caption" color="error" display="block" sx={{ mt: 1 }}>
              None of {deltaScope === 'selection' ? 'the selected' : 'these'} dates is marine, so
              there is nothing to apply this to.
            </Typography>
          )}
        </Box>
        )}
        </Box>
      </Popover>
    </Box>
  )
}
