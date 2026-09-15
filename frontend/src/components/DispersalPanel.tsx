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
import { arrivalAges, frontLine, speedText } from '@/utils/dispersalFigure'
import type { DispersalFit } from '@/types'

interface Props {
  fit: DispersalFit
  dateFormat: DateFormat
  /** Drawn on the project's tiled basemap when it has opted into one. */
  tiles?: boolean
}

export default function DispersalPanel({ fit, dateFormat, tiles = false }: Props) {
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
