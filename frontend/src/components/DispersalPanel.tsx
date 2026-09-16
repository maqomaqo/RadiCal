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
  Chip,
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
} from '@mui/material'

import SiteMap from '@/components/SiteMap'
import { usePlotColors } from '@/theme/plotColors'
import {
  formatCalendarAge,
  formatCalendarRange,
  type DateFormat,
} from '@/utils/dateFormat'
import { arrivalAges, compassPoint, frontLine, speedText } from '@/utils/dispersalFigure'
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
  tiles?: boolean
}

export default function DispersalPanel({ section, dateFormat, tiles = false }: Props) {
  const { wave, trend, origin_profile: profile } = section
  return (
    <Stack spacing={3}>
      {/* The trend first, deliberately. It assumes no origin, so it is the
          look at the data that has no hypothesis in it yet — and a reader who
          meets the wave first has already accepted somebody's origin before
          seeing whether the dates point that way at all. */}
      {trend && <TrendCard trend={trend} tiles={tiles} />}
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
        <Alert severity="info">
          No origin was placed, so no front <strong>speed from a point</strong> was
          fitted — only the models above, which assume none. Place an origin on the
          map and re-run to add a wave of advance measured from it.
        </Alert>
      )}
    </Stack>
  )
}

function WaveCard({ fit, dateFormat, tiles }: {
  fit: DispersalFit
  dateFormat: DateFormat
  tiles: boolean
}) {
  const plot = usePlotColors()

  const placed = fit.sites.filter((s) => s.latitude !== null && s.longitude !== null)
  // One map point per site. SiteMap groups by position, and these are already
  // one per position, so the grouping is a no-op that keeps one code path.
  const points = placed.map((site) => ({
    id: site.element_id,
    name: site.name,
    latitude: site.latitude as number,
    longitude: site.longitude as number,
  }))

  // Arrival age per site, for the colour ramp. Reconstructed from what the
  // fit reported rather than recomputed from coordinates -- see
  // utils/dispersalFigure for why that distinction is load-bearing.
  const arrivalByElement = arrivalAges(fit)

  const refusedSites = fit.sites.filter((s) => s.refusal)
  const fallback = fit.basis === 'earliest_dated_event'

  return (
    <Paper sx={{ p: 3 }}>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.5 }}>
        <Typography variant="h6" component="h2">
          Wave of advance
        </Typography>
        <Typography variant="body2" color="text.secondary">
          from {fit.origin.lat.toFixed(3)}, {fit.origin.lon.toFixed(3)}
        </Typography>
      </Stack>

      {/* Said before the number, not after it: a reader who takes the speed
          first and finds out afterwards what it was measured from has already
          formed the belief. */}
      {fallback && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          These sites are dated by their <strong>earliest dated event</strong>, not by a
          modelled Boundary — this analysis has no model to take one from. The oldest
          date at a site is a <em>terminus ante quem</em> for arrival, not an estimate of
          it: you almost never excavate the genuinely first thing at a site, so every
          arrival here is late by an unknown amount, and late by more at the sites that
          were dug least. The speed below inherits that bias.
        </Alert>
      )}

      {fit.refused ? (
        <Alert severity="info">{fit.refused}</Alert>
      ) : (
        <>
          <Box sx={{ mb: 2 }}>
            <SiteMap
              points={points}
              values={arrivalByElement}
              valueLabel="Arrival (cal BP)"
              front={frontLine(fit)}
              origin={{ latitude: fit.origin.lat, longitude: fit.origin.lon }}
              tiles={tiles}
              height={380}
              exportName="wave-of-advance"
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
                <Typography variant="body2" color="text.secondary">
                  fitted per draw
                </Typography>
              </Box>
            </Stack>
          )}

          {/* A negative speed is a finding, not an error, and the panel has to
              say what it means rather than leaving a minus sign to do it. */}
          {fit.speed && fit.speed.median < 0 && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              The speed is <strong>negative</strong>: sites further from this origin are
              modelled as <em>older</em>, not younger. Nothing has been flipped to hide
              that. Either the front ran the other way, this origin is not where it
              started, or these dates do not describe a front at all — the residuals
              below are what tells the three apart.
            </Alert>
          )}

          {fit.degenerate_draws ? (
            <Alert severity="info" sx={{ mb: 2 }}>
              {fit.degenerate_draws.toLocaleString()} of {(fit.draws || 0).toLocaleString()}{' '}
              draws had no gradient at all — an infinite speed — and are excluded from the
              summary above.
            </Alert>
          ) : null}

          {fit.residuals.length > 0 && (
            <>
              <Typography variant="subtitle2" sx={{ mt: 1, mb: 0.5 }}>
                Residuals
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1, maxWidth: '72ch' }}>
                How far each site sits from the fitted line, in years, on the same draws.
                Positive means the site is <strong>older</strong> than the front predicts —
                the thing was there before the line says it should have been. Structure
                here (neighbouring sites all early, or all late) is the finding; a front
                that fits has residuals that look like noise.
              </Typography>
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Site</TableCell>
                      <TableCell align="right">Distance (km)</TableCell>
                      <TableCell align="right">Residual (yr)</TableCell>
                      <TableCell>&nbsp;</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {fit.residuals.map((residual) => (
                      <TableRow key={residual.element_id}>
                        <TableCell>{residual.name}</TableCell>
                        <TableCell align="right">
                          {Math.round(residual.distance_km).toLocaleString()}
                        </TableCell>
                        <TableCell align="right">
                          {residual.residual_years > 0 ? '+' : ''}
                          {Math.round(residual.residual_years).toLocaleString()}
                          <Typography variant="caption" color="text.secondary" sx={{ ml: 0.5 }}>
                            ± {Math.round(residual.residual_sd).toLocaleString()}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          {/* The two directions of ONE axis, not two unrelated
                              colours: early and late are the same measurement
                              with opposite signs. */}
                          <Tooltip
                            title={
                              residual.residual_years > 0
                                ? 'Older than the front predicts: the thing arrived here early.'
                                : 'Younger than the front predicts: the thing arrived here late.'
                            }
                          >
                            <Chip
                              size="small"
                              variant="outlined"
                              label={residual.residual_years > 0 ? 'early' : 'late'}
                              sx={{
                                borderColor:
                                  residual.residual_years > 0 ? plot.posteriorLine : plot.mean,
                                color:
                                  residual.residual_years > 0 ? plot.posteriorLine : plot.mean,
                              }}
                            />
                          </Tooltip>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </>
          )}
        </>
      )}

      {refusedSites.map((site) => (
        <Alert severity="warning" sx={{ mt: 2 }} key={site.element_id}>
          {site.refusal}
        </Alert>
      ))}

      {/* §6.8, in the panel rather than the manual. */}
      <Box sx={{ mt: 3, pt: 2, borderTop: 1, borderColor: 'divider' }}>
        <Typography variant="subtitle2" gutterBottom>
          What this estimate is, and what it is not
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          component="ul"
          sx={{ pl: 2.5, m: 0, maxWidth: '78ch', '& li': { mb: 0.75 } }}
        >
          <li>
            Fitting per draw propagates the uncertainty in <strong>the ages</strong>. It does
            not propagate uncertainty in the origin, in the coordinates, or in the
            assumption that these sites are the front rather than the excavated subset of
            it.
          </li>
          <li>
            Residuals are assumed <strong>independent</strong>. They are not — neighbouring
            sites share whatever the model is missing. Spatial autocorrelation is not
            modelled, so the interval on the speed is optimistic in exactly the way an OLS
            interval on autocorrelated data always is.
          </li>
          <li>
            This is a plug-in Monte Carlo estimator, <strong>not a hierarchical model</strong>.
            A full treatment would model site arrival as drawn from a front with a
            site-level random effect and estimate the front and the scatter together.
          </li>
          <li>Distances are great-circle, measured from the origin above.</li>
        </Typography>
      </Box>
    </Paper>
  )
}

/**
 * The trend surface: is there a gradient at all, and which way does it point.
 *
 * DRAWN FIRST, and that ordering is the argument. This model assumes no
 * origin, so it is the look at the data with no hypothesis in it — and a
 * reader who meets the wave of advance first has already accepted somebody's
 * origin before seeing whether the dates point that way at all. When the two
 * disagree, the finding is usually about the origin rather than about either
 * fit, and that is only visible if both are on the page.
 */
function TrendCard({ trend, tiles }: { trend: TrendFit; tiles: boolean }) {
  const placed = trend.sites.filter(
    (s) => s.latitude !== null && s.longitude !== null && !s.refusal
  )
  const points = placed.map((site) => ({
    id: site.element_id,
    name: site.name,
    latitude: site.latitude as number,
    longitude: site.longitude as number,
  }))

  return (
    <Paper sx={{ p: 3 }}>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.5 }}>
        <Typography variant="h6" component="h2">
          Trend surface
        </Typography>
        <Typography variant="body2" color="text.secondary">
          no origin assumed
        </Typography>
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2, maxWidth: '74ch' }}>
        A plane fitted through arrival age against position, per draw, in an equal-area
        projection centred on these sites. It asks whether there is a gradient at all and
        which way it points — without being told where anything started.
      </Typography>

      {trend.refused ? (
        <Alert severity="info">{trend.refused}</Alert>
      ) : (
        <>
          <Box sx={{ mb: 2 }}>
            <SiteMap
              points={points}
              isochrons={trend.isochrons}
              tiles={tiles}
              height={380}
              exportName="trend-surface"
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
              <Typography variant="body2" color="text.secondary">fitted per draw</Typography>
            </Box>
          </Stack>

          {/* R near zero means the draws point every which way, and the mean
              direction above is then an artefact of the arithmetic rather than
              a finding. Said out loud, because a bearing printed to the degree
              looks equally confident either way. */}
          {trend.azimuth && trend.azimuth.concentration < 0.5 && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              The direction is <strong>poorly determined</strong>: across draws the
              gradient points every which way (concentration{' '}
              {trend.azimuth.concentration.toFixed(2)} of a possible 1.00). Read the
              bearing above as "no clear direction", not as the one printed.
            </Alert>
          )}

          <Typography variant="caption" color="text.secondary" display="block">
            Isochrons are lines of equal arrival age on the fitted plane, drawn from the
            median surface. On a plane they are straight, parallel and evenly spaced — so
            a curved front, or one that speeds up, is a model this does not fit.
          </Typography>
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
  tiles: boolean
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

  return (
    <Paper sx={{ p: 3 }}>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.5 }}>
        <Typography variant="h6" component="h2">
          Where it could have started
        </Typography>
        <Typography variant="body2" color="text.secondary">
          profiled, not assumed
        </Typography>
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2, maxWidth: '74ch' }}>
        Every position on a coarse grid tried as an origin, and how well a front from
        each one fits. The shaded cells are those within{' '}
        {(profile.cutoff * 100).toFixed(0)}% of the best — a support contour, not a
        confidence region: this is a profile over an assumed model, and calling it 95%
        would claim a coverage property nothing here has established.
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
            />
          </Box>

          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={3}
            sx={{ mb: 2 }}
            divider={<Box sx={{ borderLeft: 1, borderColor: 'divider' }} />}
          >
            {profile.peak && (
              <Box>
                <Typography variant="overline" color="text.secondary" display="block">
                  Best-fitting cell
                </Typography>
                <Typography variant="h5" component="p">
                  {profile.peak.lat.toFixed(1)}, {profile.peak.lon.toFixed(1)}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  the peak of something broad
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
                {/* Said here, beside the count it qualifies: "94 of 416
                    cells" on a grid of 1,058 needs the other 642 accounted
                    for, and they are the cells the front ran towards. */}
                {(profile.candidates_ahead ?? 0) > 0 && (
                  <>
                    ; {profile.candidates_ahead!.toLocaleString()} more were where the front
                    went, not where it started, and were not scored
                  </>
                )}
              </Typography>
            </Box>
          </Stack>

          {/* The finding a best-guess marker would have hidden. */}
          {broad && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              The region covers{' '}
              <strong>{Math.round((profile.share_inside ?? 0) * 100)}%</strong> of the
              grid searched: these dates do <strong>not</strong> locate an origin. That
              is the expected shape when a front is sampled along one axis — sliding the
              start backwards along the line of travel fits just as well — and it is a
              result, not a failure of the search.
            </Alert>
          )}

          {placedOutside && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              The origin used for the wave of advance below — {wave!.origin.lat.toFixed(3)},{' '}
              {wave!.origin.lon.toFixed(3)} — falls <strong>outside</strong> this region.
              The speed reported there is still the speed from that point, correctly
              computed; what this says is that the dates fit a front from somewhere else
              considerably better.
            </Alert>
          )}
        </>
      )}
    </Paper>
  )
}
