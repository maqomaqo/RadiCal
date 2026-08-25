import React, { useCallback, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Container,
  IconButton,
  Paper,
  Link,
  Tooltip,
  Typography,
} from '@mui/material'
import { Link as RouterLink } from 'react-router-dom'
import useMediaQuery from '@mui/material/useMediaQuery'
import type { Theme } from '@mui/material/styles'
import DeleteIcon from '@mui/icons-material/Delete'
import ConfirmDialog from '@/components/ConfirmDialog'
import IntervalPlot, { SampleIntervals } from '@/components/IntervalPlot'
import { useNotify } from '@/contexts/NotificationContext'
import { formatApiError } from '@/utils/apiError'
import { analysisTypeLabel } from '@/utils/analysisTypes'
import { loadRecentAnalyses, LoadError } from '@/utils/recentAnalyses'
import { clusterByPeriod } from '@/utils/periodClusters'
import { calendarRulerTicks, formatCalendarRange, DateFormat } from '@/utils/dateFormat'
import { usePlotColors } from '@/theme/plotColors'

interface AnalysisSummary {
  id: string
  name: string
  analysis_type: string
  status: string
  created_at: string
  measurements_count: number
  project_id: string | null
  project_name: string | null
  calendar_span: [number, number] | null
  intervals: SampleIntervals[]
}

// Analyses are organized into projects (see the Projects page) -- the
// dashboard only needs to show a handful of the most recent ones as a
// quick-glance list, not every analysis ever run.
const RECENT_LIMIT = 5

// Room either side of a cluster's own span, so the outermost interval is a
// bar with an edge rather than a mark flush against the ruler's end.
const AXIS_PADDING = 0.06

/**
 * A cluster reports in AD/BC or in cal BP as a whole, never per row: the
 * ruler and the ranges beneath it have to be in the same unit, or the
 * numbers in the text disagree with the numbers on the axis.
 */
const clusterFormat = (isCurrentEra: boolean): DateFormat =>
  isCurrentEra ? 'bcad' : 'calBP'

export default function Dashboard() {
  const notify = useNotify()
  const [analyses, setAnalyses] = useState<AnalysisSummary[]>([])
  const [total, setTotal] = useState(0)
  // Starts true: the request is fired on mount, so there has never been a
  // moment on this page when nothing was loading. Starting false rendered
  // the empty state first and the spinner after it -- a returning user was
  // told "No analyses yet" before their own analyses arrived.
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<LoadError | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<AnalysisSummary | null>(null)
  const [deleting, setDeleting] = useState(false)

  const fetchAnalyses = useCallback(async () => {
    setLoading(true)
    setError(null)
    const outcome = await loadRecentAnalyses<AnalysisSummary>(RECENT_LIMIT)
    if (outcome.ok) {
      setAnalyses(outcome.items)
      setTotal(outcome.total)
    } else {
      setError(outcome.error)
    }
    setLoading(false)
  }, [])

  React.useEffect(() => {
    fetchAnalyses()
  }, [fetchAnalyses])

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      const response = await fetch(`/api/analysis/${deleteTarget.id}`, { method: 'DELETE' })
      const data = await response.json()
      if (data.success) {
        setDeleteTarget(null)
        fetchAnalyses()
      } else {
        notify('Failed to delete analysis: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error deleting analysis: ' + (err as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  const { clusters, unplaced } = clusterByPeriod(analyses)

  return (
    <Container component="main" maxWidth="lg" sx={{ py: 4 }}>
      {/* Persistent, not conditional. Orientation used to live in the empty
          state, which meant it existed until the user's first analysis and
          then vanished for good -- the wrong shape for an audience that
          PRODUCT.md says includes someone calibrating for the first time. */}
      <WaysIn />

      <Box
        sx={{ display: 'flex', alignItems: 'baseline', gap: 2, flexWrap: 'wrap', mt: 4, mb: 1 }}
      >
        {/* variant="h1": the theme defines h1 as 2.5rem/600, the
            design system's Headline. The title names the view rather than
            the app; the wordmark in the AppBar already says RadiCal. */}
        <Typography variant="h1" component="h1">
          Recent analyses
        </Typography>
        {total > 0 && (
          <Typography variant="body2" color="text.secondary">
            {analyses.length} of {total}
          </Typography>
        )}
        {total > 0 && (
          <Button
            component={RouterLink}
            to="/projects"
            size="medium"
            sx={{ ml: 'auto', minHeight: 44 }}
          >
            View all in Projects &rarr;
          </Button>
        )}
      </Box>

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress />
        </Box>
      ) : error ? (
        <Alert
            severity="error"
            action={
              <Button color="inherit" onClick={fetchAnalyses} sx={{ minHeight: 44 }}>
                Retry
              </Button>
            }
          >
            {error.message}
            {error.detail && (
              <Box component="span" sx={{ display: 'block', mt: 0.5, opacity: 0.85 }}>
                {error.detail}
              </Box>
            )}
        </Alert>
      ) : analyses.length === 0 ? (
        <NothingYet />
      ) : (
        <>
          {clusters.map((cluster) => {
            const format = clusterFormat(cluster.isCurrentEra)
            const [lo, hi] = cluster.span
            const pad = (hi - lo) * AXIS_PADDING || 1
            const axis: [number, number] = [lo - pad, hi + pad]
            const ticks = calendarRulerTicks(axis[0], axis[1], format)
            return (
              <PeriodSection
                key={`${lo}-${hi}`}
                heading={formatCalendarRange(lo, hi, format, { compact: true })}
                count={cluster.items.length}
                axis={axis}
                ticks={ticks}
              >
                {cluster.items.map((analysis) => (
                  <AnalysisRow
                    key={analysis.id}
                    analysis={analysis}
                    axis={axis}
                    ticks={ticks.map((t) => t.calBP)}
                    format={format}
                    onDelete={() => setDeleteTarget(analysis)}
                  />
                ))}
              </PeriodSection>
            )
          })}

          {unplaced.length > 0 && (
            <PeriodSection heading="Not yet solved" count={unplaced.length}>
              {unplaced.map((analysis) => (
                <AnalysisRow
                  key={analysis.id}
                  analysis={analysis}
                  onDelete={() => setDeleteTarget(analysis)}
                />
              ))}
            </PeriodSection>
          )}
        </>
      )}

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete analysis?"
        message={
          deleteTarget
            ? `Permanently delete "${deleteTarget.name}"? This removes its samples and results and cannot be undone.`
            : ''
        }
        confirmLabel="Delete"
        danger
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </Container>
  )
}

/**
 * A period and the analyses inside it, under one ruler.
 *
 * The heading is weight and a hairline rather than a tinted band, following
 * the same rule the data tables follow: a filled row paints colour across
 * columns that carry nothing.
 */
function PeriodSection({
  heading,
  count,
  axis,
  ticks,
  children,
}: {
  heading: string
  count: number
  axis?: [number, number]
  ticks?: { calBP: number; label: string }[]
  children: React.ReactNode
}) {
  return (
    <Box component="section" sx={{ mt: 3, pt: 1.5, borderTop: 1, borderColor: 'divider' }}>
      <Box
        sx={{ display: 'flex', alignItems: 'baseline', gap: 1.5, flexWrap: 'wrap', px: 2 }}
      >
        <Typography variant="subtitle1" component="h2" sx={{ fontWeight: 600 }}>
          {heading}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {count} {count === 1 ? 'analysis' : 'analyses'}
        </Typography>
        {axis && <HpdKey />}
      </Box>

      {axis && ticks && (
        <Box sx={{ ...ROW_GRID, mx: 2, mt: 1 }}>
          <Box sx={{ display: { xs: 'none', md: 'block' } }} />
          <Box
            sx={{
              position: 'relative',
              height: 20,
              borderBottom: 1,
              borderColor: 'divider',
            }}
          >
            {ticks.map((tick) => (
              <Typography
                key={tick.calBP}
                variant="caption"
                sx={{
                  position: 'absolute',
                  bottom: 2,
                  fontSize: '0.6875rem',
                  color: 'text.secondary',
                  transform: 'translateX(-50%)',
                  left: `${((axis[1] - tick.calBP) / (axis[1] - axis[0])) * 100}%`,
                }}
              >
                {tick.label}
              </Typography>
            ))}
          </Box>
        </Box>
      )}

      <Box sx={{ display: 'grid', gap: 1.25, mt: 1.25 }}>{children}</Box>
    </Box>
  )
}

/**
 * The key for the two bands. A mark a reader cannot decode is decoration,
 * and the two nested strengths are the only thing distinguishing a 68% band
 * from a 95% one.
 */
function HpdKey() {
  // The swatches come from the same hook the marks do, so a key can never
  // end up naming a fill the plot is not actually using.
  const colors = usePlotColors()
  const bands = [
    { label: '68%', fill: colors.hpd68 },
    { label: '95% HPD', fill: colors.hpd95 },
  ]

  return (
    <Box
      aria-hidden
      sx={{
        ml: 'auto',
        display: 'flex',
        gap: 1.75,
        fontSize: '0.75rem',
        color: 'text.secondary',
      }}
    >
      {bands.map((band) => (
        <Box key={band.label} sx={{ display: 'flex', alignItems: 'center', gap: 0.625 }}>
          <Box
            sx={{ width: 16, height: 9, borderRadius: '1px', backgroundColor: band.fill }}
          />
          {band.label}
        </Box>
      ))}
    </Box>
  )
}

// Identity on the left, the calendar on the right; every row and the ruler
// above them share this grid, which is what makes one ruler serve them all.
const ROW_GRID = {
  display: 'grid',
  gridTemplateColumns: { xs: '1fr', md: '272px 1fr' },
  gap: 2,
  pr: { xs: 0, md: '52px' },
} as const

function AnalysisRow({
  analysis,
  axis,
  ticks,
  format,
  onDelete,
}: {
  analysis: AnalysisSummary
  axis?: [number, number]
  ticks?: number[]
  format?: DateFormat
  onDelete: () => void
}) {
  const range =
    analysis.calendar_span && format
      ? formatCalendarRange(
          analysis.calendar_span[0],
          analysis.calendar_span[1],
          format,
          { compact: true }
        )
      : null

  const meta = [
    `${analysis.measurements_count} sample${analysis.measurements_count === 1 ? '' : 's'}`,
    analysis.project_name ?? 'Unassigned',
    analysisTypeLabel(analysis.analysis_type),
  ].join(' · ')

  return (
    <Paper
      sx={{
        ...ROW_GRID,
        position: 'relative',
        alignItems: 'start',
        p: 2,
        pr: { xs: 2, md: '52px' },
        // Keyboard focus gets the same affordance as the mouse. The link
        // covering the card is invisible, so its own ring would be drawn
        // around nothing; :focus-within moves it onto the card instead.
        '&:hover, &:focus-within': { boxShadow: 3 },
        '&:focus-within': { outline: 2, outlineColor: 'primary.main', outlineOffset: 2 },
        '& a:focus-visible': { outline: 'none' },
      }}
    >
      {/* Stretched link: see the matching comment in Projects.tsx -- kept as
          a sibling of the delete button, not a wrapper around it, so the
          button stays reachable in the accessibility tree. The label carries
          the range and the metadata, because in a screen reader's links list
          the name alone leaves two analyses called "Calibration"
          indistinguishable. */}
      <Box
        component={RouterLink}
        to={`/results/${analysis.id}`}
        aria-label={[analysis.name, range, meta].filter(Boolean).join('. ')}
        sx={{ position: 'absolute', inset: 0, borderRadius: 'inherit' }}
      />

      <Box sx={{ pointerEvents: 'none', minWidth: 0 }}>
        <Typography
          variant="h6"
          component="h3"
          noWrap
          sx={{ fontSize: '1.0625rem', pr: { xs: 5, md: 0 } }}
        >
          {analysis.name}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          {meta}
        </Typography>
        {/* Colour is spent only where the status is news. Every completed
            analysis carrying a green chip would make "completed" the most
            saturated thing on a page where it is also the least
            discriminating. */}
        {analysis.status !== 'completed' && (
          <Chip
            size="small"
            label={analysis.status}
            color={analysis.status === 'failed' ? 'error' : 'warning'}
            sx={{ mt: 1 }}
          />
        )}
      </Box>

      <Box sx={{ pointerEvents: 'none', minWidth: 0 }}>
        {axis && ticks && range ? (
          <>
            <IntervalPlot
              intervals={analysis.intervals}
              axis={axis}
              ticks={ticks}
              label={`Calendar intervals for ${analysis.name}: ${range}`}
            />
            <Typography
              sx={{
                mt: 1,
                fontWeight: 600,
                fontSize: '0.9375rem',
                whiteSpace: 'nowrap',
                // Sits under its own data rather than flush left, so the
                // words and the marks read as one statement. Clamped so a
                // range at the young end cannot run off the card.
                ml: {
                  xs: 0,
                  md: `${Math.min(
                    ((axis[1] - analysis.calendar_span![1]) / (axis[1] - axis[0])) * 100,
                    80
                  )}%`,
                },
              }}
            >
              {range}
            </Typography>
          </>
        ) : (
          <Typography variant="body2" color="text.secondary">
            No calibrated result yet
          </Typography>
        )}
      </Box>

      {/* Always visible, not hover-revealed -- see the matching comment in
          Projects.tsx for why, and for the rule this follows across every
          destructive icon button. */}
      <Tooltip title={`Delete ${analysis.name}`}>
        <IconButton
          aria-label={`Delete ${analysis.name}`}
          sx={{
            position: 'absolute',
            top: 8,
            right: 8,
            zIndex: 1,
            // 30x30 was under every touch-target guideline going, and it is
            // the only control on the row.
            width: 44,
            height: 44,
            '&:hover, &:focus-visible': { color: 'error.main' },
          }}
          onClick={onDelete}
        >
          <DeleteIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Paper>
  )
}

/**
 * The ways into the product, above the list and always present.
 *
 * It carries real information rather than restating the app bar -- three
 * distinct routes in, one of which (the curve browser) is the only surface
 * that shows what calibration DOES before asking anyone to commit a
 * measurement to it.
 *
 * A worked example belongs in this set and is deliberately absent: there is
 * no seeded dataset to open, and inventing archaeological data to teach with
 * would put fabricated measurements on the first page of a tool whose whole
 * claim is that its numbers can be cited.
 */
const WAYS_IN = [
  {
    to: '/new-analysis',
    title: 'Calibrate a date',
    action: 'New analysis',
    body: 'Turn a radiocarbon measurement into a calendar date range. The quickest way to a first result.',
    primary: true,
  },
  {
    to: '/new-analysis',
    title: 'Import dates',
    action: 'Import CSV or .oxcal',
    body: 'Bring in a CSV of measurements, or an existing OxCal file with its model intact.',
  },
  {
    to: '/curves',
    title: 'Browse a calibration curve',
    action: 'Open curve browser',
    body: 'See IntCal20, Marine20 and SHCal20 directly, and the likely resolution for your period of interest.',
  },
]

function WaysIn() {
  // Stacked, the three descriptions run to 672px on a 375px screen -- more
  // than a phone viewport, so the analyses would start below the fold and
  // orientation would cost a returning user their own data. The doors stay;
  // the prose that explains them does not fit and is dropped, with each
  // button taking the fuller label it no longer has room to sit beneath.
  const compact = useMediaQuery((theme: Theme) => theme.breakpoints.down('sm'))

  return (
    // Labelled as a region rather than headed: the page's h1 belongs to the
    // analyses below, and a heading here would either sit above the h1 or
    // skip a level. The label is what lets a screen reader find this block
    // without one.
    <Paper component="section" aria-label="Ways to start" sx={{ p: { xs: 2, sm: 3 } }}>
      <Typography color="text.secondary" sx={{ maxWidth: '64ch' }}>
        RadiCal turns radiocarbon measurements into calendar dates you can cite, and lets you model them
        against stratigraphy or other chronological constraint.
      </Typography>

      <Box
        sx={{
          mt: 2.5,
          display: 'grid',
          gap: { xs: 1, sm: 3 },
          gridTemplateColumns: { xs: '1fr', sm: 'repeat(3, 1fr)' },
        }}
      >
        {WAYS_IN.map((door) => (
          <Box
            key={door.title}
            sx={{ display: 'flex', flexDirection: 'column', alignItems: 'stretch' }}
          >
            {!compact && (
              <>
                {/* Bold text, not a heading: see the region comment above. */}
                <Typography sx={{ fontWeight: 600, fontSize: '1rem' }}>{door.title}</Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.5, mb: 1.5, flex: 1 }}
                >
                  {door.body}
                </Typography>
              </>
            )}
            {/* On a wide screen the button names its destination rather than
                repeating the label above it -- two controls saying the same
                four words is one of them wasted. Compact, there is no label
                above, so the button carries it. */}
            <Button
              component={RouterLink}
              to={door.to}
              variant={door.primary ? 'contained' : 'outlined'}
              sx={{ minHeight: 44, alignSelf: compact ? 'stretch' : 'flex-start' }}
            >
              {compact ? door.title : door.action}
            </Button>
          </Box>
        ))}
      </Box>
    </Paper>
  )
}

/**
 * The empty list, once the ways in are already on the page above it.
 *
 * A sentence rather than a panel: with orientation now permanent, a second
 * full-width card here would say "you have nothing" more loudly than the
 * page says what to do about it.
 */
function NothingYet() {
  return (
    <Typography color="text.secondary" sx={{ py: 2 }}>
      Nothing calibrated yet. Your most recent analyses will appear here, on a shared calendar
      axis.
    </Typography>
  )
}
