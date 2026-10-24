// A wave of advance on the Results page: how fast the thing being dated moved,
// when it set out, and which sites do not fit.
//
// WHY THE MAP IS THE HEADLINE. A speed is one number and it is not the finding.
// The finding is whether these sites look like a front at all, and that is a
// spatial question -- a reader can see a wave in the colour ramp, or see that
// there is no wave, faster than any table can tell them.
//
// WHY THE RESIDUALS ARE NOT AN APPENDIX. OLS on autocorrelated residuals with
// an assumed origin always returns a tidy v with a tidy interval, whether or
// not the dates describe a front. The residual table is the only thing on this
// panel that can contradict the speed, which is exactly why it sits beside it
// rather than below a fold.
//
// WHY THE CAVEATS ARE ON SCREEN. §6.8 lists what this estimator does not do --
// it does not propagate uncertainty in the origin, in the coordinates, or in
// the assumption that these sites ARE the front, and it does not model spatial
// autocorrelation, so the interval on v is optimistic in the way an OLS
// interval on autocorrelated data always is. A reader who cannot see that
// beside the number will not go looking for it in a manual.
import {
  Alert,
  Box,
  Button,
  Chip,
  Link,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Tooltip,
  Typography,
} from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'

import { useCallback, useMemo, useState } from 'react'
import { Link as RouterLink } from 'react-router-dom'
import DispersalProfilePlot from '@/components/DispersalProfilePlot'
import SiteMap from '@/components/SiteMap'
import { usePlotColors } from '@/theme/plotColors'
import {
  formatCalendarAge,
  formatCalendarRange,
  type DateFormat,
} from '@/utils/dateFormat'
import {
  compassPoint,
  formatLatLon,
  residualReading,
  residualSpread,
  speedText,
  waveIsochrons,
} from '@/utils/dispersalFigure'
import { saveBlob } from '@/utils/saveFile'
import type {
  DispersalFit,
  DispersalSection,
  DispersalSite,
  OriginProfile,
  TrendFit,
} from '@/types'

interface Props {
  section: DispersalSection
  dateFormat: DateFormat
  /** Drawn on the project's tiled basemap when it has opted into one. */
  tiles?: boolean | null
  /** Where the analysis is edited, which is where an origin is placed. */
  editPath?: string
}

export default function DispersalPanel({ section, dateFormat, tiles = false, editPath }: Props) {
  const { wave, trend, origin_profile: profile } = section
  return (
    <Stack spacing={3}>
      {/* The trend first, deliberately. It assumes no origin, so it is the
          look at the data that has no hypothesis in it yet — and a reader who
          meets the wave first has already accepted somebody's origin before
          seeing whether the dates point that way at all. */}
      {trend && <TrendCard trend={trend} tiles={tiles} dateFormat={dateFormat} />}
      {profile && (
        <OriginProfileCard
          profile={profile}
          wave={wave}
          // The sites, for scale: a region of candidate origins is unreadable
          // without the excavations it was derived from beside it.
          sites={(trend?.sites || wave?.sites || [])}
          tiles={tiles}
        />
      )}
      {wave && <WaveCard fit={wave} dateFormat={dateFormat} tiles={tiles} />}
      {!wave && trend && !trend.refused && (
        <Alert
          severity="info"
          action={
            editPath ? (
              <Button size="small" component={RouterLink} to={editPath}>
                Place an origin
              </Button>
            ) : undefined
          }
        >
          Place an origin and re-run to add a wave of advance.
        </Alert>
      )}
    </Stack>
  )
}

function WaveCard({ fit, dateFormat, tiles }: {
  fit: DispersalFit
  dateFormat: DateFormat
  tiles: boolean | null
}) {
  const placed = fit.sites.filter((s) => s.latitude !== null && s.longitude !== null)
  // One map point per site. SiteMap groups by position, and these are already
  // one per position, so the grouping is a no-op that keeps one code path.
  const points = placed.map((site) => ({
    id: site.element_id,
    name: site.name,
    latitude: site.latitude as number,
    longitude: site.longitude as number,
  }))

  // Each site coloured by its RESIDUAL, as on the regional gradient. It was
  // arrival age, which is mostly distance restated -- a ramp that says "further
  // is later", which the model assumed -- while the residuals are the finding.
  const residualByElement: Record<string, number> = {}
  for (const residual of fit.residuals) {
    residualByElement[residual.element_id] = residual.residual_years
  }
  // Circles of equal arrival round the origin, from the fit's own t0 and speed.
  const isochrons = useMemo(() => waveIsochrons(fit), [fit])
  const formatIsochron = useCallback(
    (age: number) => formatCalendarAge(age, dateFormat),
    [dateFormat]
  )
  // The site the reader is pointing at in the residual table, ringed on the
  // map and the profile so a row and its mark can be found together.
  const [highlightId, setHighlightId] = useState<string | null>(null)

  const refusedSites = fit.sites.filter((s) => s.refusal)
  const spread = residualSpread(fit)
  const fallback = fit.basis === 'earliest_dated_event'

  return (
    <Paper sx={{ p: 3 }}>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.5 }}>
        <Typography variant="h6" component="h2">
          Wave of advance
        </Typography>
        <Typography variant="body2" color="text.secondary">
          from {formatLatLon(fit.origin.lat, fit.origin.lon, 2)}
        </Typography>
      </Stack>

      {/* Said before the number, not after it: a reader who takes the speed
          first and finds out afterwards what it was measured from has already
          formed the belief. */}
      {fallback && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Sites are dated by their earliest date, so arrival times are likely too late.
        </Alert>
      )}

      {fit.refused ? (
        <Alert severity="info">{fit.refused}</Alert>
      ) : (
        <>
          <Box sx={{ mb: 2 }}>
            <SiteMap
              points={points}
              values={residualByElement}
              valueLabel="Residual (yr, + early)"
              valueScale="signed"
              isochrons={isochrons}
              formatIsochron={formatIsochron}
              highlightIds={highlightId ? [highlightId] : undefined}
              origin={{ latitude: fit.origin.lat, longitude: fit.origin.lon }}
              tiles={tiles}
              height={380}
              exportName="wave-of-advance"
              label="Wave of advance map"
            />
          </Box>

          {fit.speed && (
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={3}
              sx={{ mb: 2 }}
              divider={<Box sx={{ borderLeft: 1, borderColor: 'divider' }} />}
            >
              <Box>
                <Typography variant="overline" color="text.secondary" display="block">
                  Front speed
                </Typography>
                <Typography variant="h4" component="p">
                  {speedText(fit.speed.median)}{' '}
                  <Typography variant="body1" component="span" color="text.secondary">
                    km/yr
                  </Typography>
                </Typography>
                {fit.speed.hpd_68 && (
                  <Typography variant="body2" color="text.secondary">
                    68.3%: {speedText(fit.speed.hpd_68[0])} to {speedText(fit.speed.hpd_68[1])}
                  </Typography>
                )}
                {fit.speed.hpd_95 && (
                  <Typography variant="body2" color="text.secondary">
                    95.4%: {speedText(fit.speed.hpd_95[0])} to {speedText(fit.speed.hpd_95[1])}
                  </Typography>
                )}
                {/* Beside the speed, not in the table: the interval above is
                    how much the LINE moves between draws, and says nothing
                    about how far the sites sit from it. A tight speed over a
                    millennium of scatter is the reading this stops. */}
                {spread && (
                  <Typography variant="body2" sx={{ mt: 0.75, maxWidth: '34ch' }}>
                    Site scatter:{' '}
                    <Box component="strong" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                      ±{Math.round(spread.rms).toLocaleString()} yr
                    </Box>
                    {spread.span != null && spread.span > 0 && (
                      <>
                        {' '}({Math.round((spread.share ?? 0) * 100)}% of the front's{' '}
                        {Math.round(spread.span).toLocaleString()}-year span)
                      </>
                    )}
                  </Typography>
                )}
              </Box>

              {fit.t0 && (
                <Box>
                  <Typography variant="overline" color="text.secondary" display="block">
                    Arrival at the origin
                  </Typography>
                  <Typography variant="h5" component="p">
                    {formatCalendarAge(fit.t0.median, dateFormat)}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    95.4%: {formatCalendarRange(fit.t0.band_95[0], fit.t0.band_95[1], dateFormat)}
                  </Typography>
                </Box>
              )}

              <Box>
                <Typography variant="overline" color="text.secondary" display="block">
                  Sites
                </Typography>
                <Typography variant="h5" component="p">{fit.n_sites}</Typography>
              </Box>
            </Stack>
          )}

          {/* A negative speed is a finding, not an error, and the panel has to
              say what it means rather than leaving a minus sign to do it. */}
          {fit.speed && fit.speed.median < 0 && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              Negative speed: sites further from the origin are older. The front may
              run the other way, or start somewhere else.
            </Alert>
          )}

          {fit.degenerate_draws ? (
            <Alert severity="info" sx={{ mb: 2 }}>
              {fit.degenerate_draws.toLocaleString()} of {(fit.draws || 0).toLocaleString()}{' '}
              draws had no gradient and are left out of the summary.
            </Alert>
          ) : null}

          {fit.residuals.length > 0 && (
            <>
              <Typography variant="subtitle2" component="h3" sx={{ mt: 1, mb: 0.5 }}>
                Arrival against distance
              </Typography>
              <Box sx={{ mb: 3 }}>
                <DispersalProfilePlot fit={fit} dateFormat={dateFormat} highlightId={highlightId} />
              </Box>
              <ResidualTable fit={fit} onHighlight={setHighlightId} />
            </>
          )}
        </>
      )}

      {refusedSites.map((site) => (
        <Alert severity="warning" sx={{ mt: 2 }} key={site.element_id}>
          {site.refusal}
        </Alert>
      ))}

      <Link
        component={RouterLink}
        to="/help#spatial-dispersal"
        variant="body2"
        sx={{ display: 'inline-block', mt: 2 }}
      >
        How to read these results
      </Link>
    </Paper>
  )
}

/** The rows shown before "Show all": enough to see the largest departures. */
const RESIDUAL_ROWS_FOLDED = 10

/**
 * Every site's departure from the front, largest first.
 *
 * Largest first because the finding is at the top of that order -- a
 * leapfrog, a maritime route, a wrong date -- and the manual has always said
 * so. Folded to the first ten on a model with more: seventy-six rows open by
 * default made the wave panel six thousand pixels tall on a phone, and the
 * rows past the tenth are the ones that fit.
 *
 * A third reading, "fits", for a residual inside twice its own sd: the chip
 * used to flip on the median's sign, which called "-3 ± 644" late.
 */
function ResidualTable({ fit, onHighlight }: {
  fit: DispersalFit
  onHighlight: (id: string | null) => void
}) {
  const plot = usePlotColors()
  const [order, setOrder] = useState<'magnitude' | 'distance'>('magnitude')
  const [open, setOpen] = useState(false)

  const rows = useMemo(() => {
    const sorted = [...fit.residuals].sort((a, b) =>
      order === 'magnitude'
        ? Math.abs(b.residual_years) - Math.abs(a.residual_years)
        : a.distance_km - b.distance_km
    )
    return sorted.map((r) => ({ ...r, reading: residualReading(r.residual_years, r.residual_sd) }))
  }, [fit.residuals, order])
  const counts = {
    early: rows.filter((r) => r.reading === 'early').length,
    late: rows.filter((r) => r.reading === 'late').length,
  }
  const shown = open ? rows : rows.slice(0, RESIDUAL_ROWS_FOLDED)

  const colourOf = { early: plot.residualEarly, late: plot.residualLate, fits: undefined }
  const describe = {
    early: 'Arrived earlier than the front predicts',
    late: 'Arrived later than the front predicts',
    fits: 'Within 2 sd of the front',
  }

  const downloadCsv = () => {
    const header = 'site,distance_km,residual_years,residual_sd,reading'
    const quote = (text: string) => `"${text.replace(/"/g, '""')}"`
    const body = rows.map((r) =>
      [quote(r.name), r.distance_km.toFixed(1), r.residual_years.toFixed(1), r.residual_sd.toFixed(1), r.reading].join(',')
    )
    const blob = new Blob([[header, ...body].join('\n')], { type: 'text/csv' })
    void saveBlob(blob, 'wave-of-advance-residuals.csv')
  }

  return (
    <>
      <Box sx={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', columnGap: 2, mb: 0.5 }}>
        <Typography variant="subtitle2" component="h3">
          Residuals
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>
          {counts.early} early, {counts.late} late, {rows.length - counts.early - counts.late} fit
        </Typography>
        <Button size="small" startIcon={<DownloadIcon />} onClick={downloadCsv}>
          Download CSV
        </Button>
      </Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        Years from the front. Positive means the site is older (arrived early).
      </Typography>
      <TableContainer>
        <Table
          size="small"
          sx={{
            '& th, & td': { px: { xs: 1, sm: 2 } },
          }}
        >
          <TableHead>
            <TableRow>
              <TableCell>Site</TableCell>
              {/* Not on a phone: four columns with sort arrows do not fit a
                  295px card, and distance is the arrival plot's own axis. */}
              <TableCell
                align="right"
                sortDirection={order === 'distance' ? 'asc' : false}
                sx={{ display: { xs: 'none', sm: 'table-cell' } }}
              >
                <TableSortLabel
                  active={order === 'distance'}
                  direction="asc"
                  onClick={() => setOrder('distance')}
                >
                  Distance (km)
                </TableSortLabel>
              </TableCell>
              <TableCell align="right" sortDirection={order === 'magnitude' ? 'desc' : false}>
                <TableSortLabel
                  active={order === 'magnitude'}
                  direction="desc"
                  onClick={() => setOrder('magnitude')}
                >
                  <span>
                    Residual <Box component="span" sx={UNIT_SX}>(yr)</Box>
                  </span>
                </TableSortLabel>
              </TableCell>
              <TableCell>Reading</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {shown.map((residual) => (
              <TableRow
                key={residual.element_id}
                hover
                onMouseEnter={() => onHighlight(residual.element_id)}
                onMouseLeave={() => onHighlight(null)}
              >
                <TableCell sx={{ minWidth: '8ch' }}>{residual.name}</TableCell>
                <TableCell
                  align="right"
                  sx={{ ...NUMERIC_SX, display: { xs: 'none', sm: 'table-cell' } }}
                >
                  {Math.round(residual.distance_km).toLocaleString()}
                </TableCell>
                <TableCell align="right" sx={NUMERIC_SX}>
                  {residual.residual_years > 0 ? '+' : ''}
                  {Math.round(residual.residual_years).toLocaleString()}
                  {/* Under the value on a phone, beside it otherwise: side by
                      side the pair was the widest cell in the table. */}
                  <Typography
                    component="span"
                    variant="caption"
                    color="text.secondary"
                    sx={{ ml: { xs: 0, sm: 0.5 }, display: { xs: 'block', sm: 'inline' } }}
                  >
                    ± {Math.round(residual.residual_sd).toLocaleString()}
                  </Typography>
                </TableCell>
                <TableCell>
                  {/* The two directions of ONE axis, not two unrelated
                      colours: early and late are the same measurement with
                      opposite signs. "fits" takes no colour at all. */}
                  <Tooltip title={describe[residual.reading]}>
                    <Chip
                      size="small"
                      variant="outlined"
                      tabIndex={0}
                      label={residual.reading === 'fits' ? 'fits' : residual.reading}
                      sx={{
                        borderColor: colourOf[residual.reading],
                        color: colourOf[residual.reading] ?? 'text.secondary',
                      }}
                    />
                  </Tooltip>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      {rows.length > RESIDUAL_ROWS_FOLDED && (
        <Button size="small" onClick={() => setOpen((value) => !value)} sx={{ mt: 1 }}>
          {open ? `Show the largest ${RESIDUAL_ROWS_FOLDED}` : `Show all ${rows.length} sites`}
        </Button>
      )}
    </>
  )
}

const NUMERIC_SX = { whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' } as const
// A header's unit on its own line on a phone, where the sort arrow beside a
// one-line header made it the widest thing in a 295px card.
const UNIT_SX = { display: { xs: 'block', sm: 'inline' } } as const

/**
 * The regional gradient: is there a gradient at all, and which way does it
 * point.
 *
 * Headed "Regional gradient" rather than "Trend surface", the term the
 * literature uses for this fit. A reader who does not know that convention
 * reads "surface" and expects contours shaped by the data, and this is a
 * PLANE -- one direction and one speed for the whole region -- whose
 * isochrons are straight and evenly spaced whatever the dates did. The name
 * says what the figure shows: the average.
 *
 * The map earns its place by showing the one thing that is spatial and not in
 * the numbers: where the plane fails. Each site is coloured by its residual,
 * early or late against the plane, so a front that curved or slowed reads as
 * a patch of one colour -- which is the finding, and which a table of
 * residuals makes the reader reconstruct from names.
 *
 * DRAWN FIRST, and that ordering is the argument. This model assumes no
 * origin, so it is the look at the data with no hypothesis in it — and a
 * reader who meets the wave of advance first has already accepted somebody's
 * origin before seeing whether the dates point that way at all. When the two
 * disagree, the finding is usually about the origin rather than about either
 * fit, and that is only visible if both are on the page.
 */
function TrendCard({ trend, tiles, dateFormat }: {
  trend: TrendFit
  tiles: boolean | null
  dateFormat: DateFormat
}) {
  const formatIsochron = useCallback(
    (age: number) => formatCalendarAge(age, dateFormat),
    [dateFormat]
  )
  const placed = trend.sites.filter(
    (s) => s.latitude !== null && s.longitude !== null && !s.refusal
  )
  const points = placed.map((site) => ({
    id: site.element_id,
    name: site.name,
    latitude: site.latitude as number,
    longitude: site.longitude as number,
  }))
  // Signed, in years: positive is a site OLDER than the plane predicts, the
  // same convention as the wave of advance's residual table.
  const residualByElement: Record<string, number> = {}
  for (const residual of trend.residuals) {
    residualByElement[residual.element_id] = residual.residual_years
  }

  return (
    <Paper sx={{ p: 3 }}>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.5 }}>
        <Typography variant="h6" component="h2">
          Regional gradient
        </Typography>
        <Typography variant="body2" color="text.secondary">
          from the dates alone
        </Typography>
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Lines: average arrival age. Colour: how much earlier or later each site arrived.
      </Typography>

      {trend.refused ? (
        <Alert severity="info">{trend.refused}</Alert>
      ) : (
        <>
          <Box sx={{ mb: 2 }}>
            <SiteMap
              points={points}
              values={residualByElement}
              valueLabel="Residual (yr, + early)"
              valueScale="signed"
              isochrons={trend.isochrons}
              formatIsochron={formatIsochron}
              tiles={tiles}
              height={380}
              exportName="regional-gradient"
              label="Regional gradient map"
            />
          </Box>

          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={3}
            sx={{ mb: 2 }}
            divider={<Box sx={{ borderLeft: 1, borderColor: 'divider' }} />}
          >
            {trend.speed && (
              <Box>
                <Typography variant="overline" color="text.secondary" display="block">
                  Speed
                </Typography>
                <Typography variant="h4" component="p">
                  {speedText(trend.speed.median)}{' '}
                  <Typography variant="body1" component="span" color="text.secondary">
                    km/yr
                  </Typography>
                </Typography>
                {trend.speed.hpd_95 && (
                  <Typography variant="body2" color="text.secondary">
                    95.4%: {speedText(trend.speed.hpd_95[0])} to{' '}
                    {speedText(trend.speed.hpd_95[1])}
                  </Typography>
                )}
              </Box>
            )}

            {trend.azimuth && (
              <Box>
                <Typography variant="overline" color="text.secondary" display="block">
                  Direction of advance
                </Typography>
                <Typography variant="h4" component="p">
                  {Math.round(trend.azimuth.mean_deg)}°{' '}
                  <Typography variant="body1" component="span" color="text.secondary">
                    {compassPoint(trend.azimuth.mean_deg)}
                  </Typography>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  95.4%: {Math.round(trend.azimuth.hpd_95_deg[0])}° to{' '}
                  {Math.round(trend.azimuth.hpd_95_deg[1])}° (
                  {Math.round(trend.azimuth.width_deg)}° wide)
                </Typography>
              </Box>
            )}

            <Box>
              <Typography variant="overline" color="text.secondary" display="block">
                Sites
              </Typography>
              <Typography variant="h5" component="p">{trend.n_sites}</Typography>
            </Box>
          </Stack>

          {/* R near zero means the draws point every which way, and the mean
              direction above is then an artefact of the arithmetic rather than
              a finding. Said out loud, because a bearing printed to the degree
              looks equally confident either way. */}
          {trend.azimuth && trend.azimuth.concentration < 0.5 && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              Direction is uncertain (concentration{' '}
              {trend.azimuth.concentration.toFixed(2)} of 1.00).
            </Alert>
          )}

        </>
      )}
    </Paper>
  )
}

/**
 * Where the front could have started, asked of the data rather than assumed.
 *
 * WHY THIS IS A REGION AND NOT A PIN. The profile is nearly flat along the
 * front's own axis: sliding the origin backwards along the line of travel and
 * adjusting t₀ to match fits almost exactly as well. That is a real property
 * of the model, not a defect of the arithmetic, so a single best-guess marker
 * would be a confident answer to a question the data do not settle — the
 * exact failure this whole feature is most exposed to.
 *
 * WHY IT SITS BETWEEN THE TREND AND THE WAVE. It is the check on the pin. A
 * reader who has just seen the trend and is about to read a speed measured
 * from somebody's origin should first see whether the data put the origin
 * anywhere near it.
 */
function OriginProfileCard({ profile, wave, sites, tiles }: {
  profile: OriginProfile
  wave: DispersalFit | null
  sites: DispersalSite[]
  tiles: boolean | null
}) {
  const placedOutside =
    wave && profile.region.length > 0 && profile.step_deg
      ? !profile.region.some(
          (cell) =>
            Math.abs(cell.lat - wave.origin.lat) <= profile.step_deg! &&
            Math.abs(cell.lon - wave.origin.lon) <= profile.step_deg!
        )
      : false

  const broad = (profile.share_inside ?? 0) > 0.5
  const unlocated = broad || Boolean(profile.peak_at_reach)

  return (
    <Paper sx={{ p: 3 }}>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.5 }}>
        <Typography variant="h6" component="h2">
          Where it could have started
        </Typography>
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Shaded cells are possible starting points.
      </Typography>

      {profile.refused ? (
        <Alert severity="info">{profile.refused}</Alert>
      ) : (
        <>
          <Box sx={{ mb: 2 }}>
            <SiteMap
              points={sites
                .filter((s) => s.latitude !== null && s.longitude !== null && !s.refusal)
                .map((s) => ({
                  id: s.element_id,
                  name: s.name,
                  latitude: s.latitude as number,
                  longitude: s.longitude as number,
                }))}
              region={profile.region}
              regionStepDeg={profile.step_deg || 1}
              origin={wave ? { latitude: wave.origin.lat, longitude: wave.origin.lon } : null}
              tiles={tiles}
              height={380}
              exportName="origin-profile"
              label="Origin search map"
            />
          </Box>

          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={3}
            sx={{ mb: 2 }}
            divider={<Box sx={{ borderLeft: 1, borderColor: 'divider' }} />}
          >
            {profile.peak && (
              <Box sx={{ minWidth: 0 }}>
                <Typography variant="overline" color="text.secondary" display="block">
                  Best-fitting cell
                </Typography>
                {/* Demoted when the notices below say the data do not locate
                    an origin: a coordinate set as large as the region count
                    reads as the answer those notices are there to withdraw. */}
                <Typography
                  variant={unlocated ? 'body1' : 'h5'}
                  component="p"
                  sx={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}
                >
                  {formatLatLon(profile.peak.lat, profile.peak.lon)}
                </Typography>
              </Box>
            )}
            <Box>
              <Typography variant="overline" color="text.secondary" display="block">
                Region
              </Typography>
              <Typography variant="h5" component="p">
                {profile.region.length.toLocaleString()}
                <Typography variant="body1" component="span" color="text.secondary">
                  {' '}of {profile.candidates.toLocaleString()} cells
                </Typography>
              </Typography>
              <Typography variant="body2" color="text.secondary">
                on a {profile.step_deg}° grid
                {profile.reach_km != null && (
                  <> within {Math.round(profile.reach_km).toLocaleString()} km of a site</>
                )}
              </Typography>
            </Box>
          </Stack>

          {/* Said before the broad-region notice, because it is the more
              specific of the two: a peak on the rim is what a monotone ridge
              looks like, and a monotone ridge is the plane-wave degeneracy
              the next notice explains. */}
          {profile.peak_at_reach && (
            <Alert severity="info" sx={{ mb: 2 }}>
              The best fit is at the edge of the search area. The dates show the
              direction of travel more clearly than the starting point.
            </Alert>
          )}

          {/* The finding a best-guess marker would have hidden. */}
          {broad && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              The shaded region covers {Math.round((profile.share_inside ?? 0) * 100)}% of
              the search area, so the starting point is uncertain.
            </Alert>
          )}

          {placedOutside && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              The origin used below ({formatLatLon(wave!.origin.lat, wave!.origin.lon, 2)}) is
              outside this region. A different start fits the dates better.
            </Alert>
          )}
        </>
      )}
    </Paper>
  )
}
