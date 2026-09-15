import { useState, useEffect, useMemo } from 'react'
import { useParams, useNavigate, Link as RouterLink } from 'react-router-dom'
import {
  Container,
  Box,
  Paper,
  Typography,
  Button,
  Link,
  IconButton,
  CircularProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Alert,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  SelectChangeEvent,
  FormControlLabel,
  Checkbox,
  ListItemIcon,
  ListItemText,
  Menu,
  Chip,
  Collapse,
  Divider,
  Stack,
  Tooltip,
  ToggleButton,
  ToggleButtonGroup,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import { useElementTypeColors } from '@/theme/diagramColors'
import { MUTED, PAPER } from '@/theme/surfaces'
import { visuallyHidden } from '@mui/utils'
import UnfoldMoreIcon from '@mui/icons-material/UnfoldMore'
import UnfoldLessIcon from '@mui/icons-material/UnfoldLess'
import CheckIcon from '@mui/icons-material/Check'
import DownloadIcon from '@mui/icons-material/Download'
import AccountTreeIcon from '@mui/icons-material/AccountTree'
import DeleteIcon from '@mui/icons-material/Delete'
import ReplayIcon from '@mui/icons-material/Replay'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import EditIcon from '@mui/icons-material/Edit'
import ViewColumnIcon from '@mui/icons-material/ViewColumn'
import KeyboardArrowRightIcon from '@mui/icons-material/KeyboardArrowRight'
import JobProgress from '../components/JobProgress'
import { cancelJob } from '../api/jobs'
import { useJob } from '../hooks/useJob'
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown'
import PosteriorPlot from '@/components/PosteriorPlot'
import StackedPosteriorPlot, { type SeriesMeasurement } from '@/components/StackedPosteriorPlot'
import CurvePosteriorPlot from '@/components/CurvePosteriorPlot'
import HarrisMatrixDiagram from '@/components/HarrisMatrixDiagram'
import DurationsPanel from '@/components/DurationsPanel'
import { modelCanReportIntervals } from '@/utils/intervalsAvailable'
import WiggleMatchPanel from '@/components/WiggleMatchPanel'
import AgeDepthPanel from '@/components/AgeDepthPanel'
import DispersalPanel from '@/components/DispersalPanel'
import ConfirmDialog from '@/components/ConfirmDialog'
import GuideBand from '@/components/GuideBand'
import { formatApiError } from '@/utils/apiError'
import {
  columnVisible,
  loadResultColumnOverrides,
  saveResultColumnOverrides,
  type ColumnOverrides,
} from '@/utils/columnPreferences'
import { useNotify } from '@/contexts/NotificationContext'
import { useCurves } from '@/hooks/useCurves'
import { analysisTypeLabel } from '@/utils/analysisTypes'
import { flattenStructure } from '@/utils/stratigraphy'
import type { StructureRow } from '@/utils/stratigraphy'
import { describeThinBoundary, describeThinPhase, findThinPhases } from '@/utils/thinPhases'
import {
  DateFormat,
  DATE_FORMAT_OPTIONS,
  formatCalendarAge,
  formatCalendarRange,
  formatHPDRanges,
  loadDateFormatPreference,
  saveDateFormatPreference,
  HPDRange,
} from '@/utils/dateFormat'
import {
  CONFIDENCE_LEVELS,
  SIGMA_2,
  hpdBands,
  posteriorEnvelope,
  posteriorRanges,
  type HPDBand,
} from '@/utils/hpd'
import type {
  MatrixMeasurement,
  DraftGroup,
  DraftRelationship,
  ElementType,
  IntervalsBlock,
  ModelElement,
  PosteriorSummary,
} from '@/types'
import {
  concernFor,
  concernTitle,
  NOTABLE_OUTLIER_PRIOR,
  POOR_AGREEMENT,
  type Concern,
} from '@/utils/diagnostics'

/**
 * A date as this analysis stored it: the laboratory measurement, plus what the
 * run needed to interpret it. Wider than the Harris matrix needs -- the
 * citation strings name the curve each date was calibrated against, and the
 * Curve view draws each measurement against the curve, where for a marine date
 * the readable value is the age minus Delta-R.
 */
type AnalysisMeasurement = MatrixMeasurement & {
  curve_id?: string
  delta_r?: number | null
  delta_r_error?: number | null
}

interface AnalysisResult {
  id: string
  name: string
  analysis_type: string
  status: string
  // Why a failed run failed. Null unless status is 'failed'.
  error_message?: string | null
  created_at: string
  measurements_count: number
  project_id?: string | null
  project_name?: string | null
  // The samples, groups and relationships this analysis used.
  measurements?: AnalysisMeasurement[]
  groups?: DraftGroup[]
  // Explicit model structure; boundary elements carry their own posterior.
  model_tree?: ModelElement | null
  relationships?: DraftRelationship[]
  // How the results were computed. Null for an analysis run before this was
  // recorded, reported as unknown rather than assumed to be either method.
  solver?: {
    method: 'exact' | 'gibbs' | 'smc' | 'wiggle_match'
    // What the analysis asked for. 'gibbs' means the analyst chose OxCal's
    // method deliberately, so the result is not a fallback and must not be
    // labelled as one.
    requested?: 'auto' | 'exact' | 'gibbs'
    reason?: string | null
    // Implied transitions supplied to the solver that ran; always 0 when
    // sampled.
    implicit_boundaries?: number
    // How many WOULD be needed for the exact solver to apply, set only when
    // that is why this was sampled. Distinct from `reason`, which says the
    // model cannot be integrated exactly at all.
    exact_needs_boundaries?: number
    // Sampled path only. `converged` is against OxCal's 0.95 target for the
    // overlap-integral convergence measure -- read off the rounds pooled over
    // every chain when several ran -- AND, with several chains, Gelman-Rubin
    // R̂ under `rhat_target`, so chains that settled in different calibration
    // lobes cannot be pooled in silence. The between-chain overlap is
    // information only. The chain fields are null on a single chain, which
    // has nothing to disagree with. `sweeps` is the total over every chain.
    sweeps?: number
    convergence?: number
    converged?: boolean
    chains?: number
    sweeps_per_chain?: number[]
    within_chain_convergence?: number
    per_chain_convergence?: number[]
    between_chain_convergence?: number | null
    rhat?: number | null
    rhat_target?: number | null
    // Legacy: set only on analyses run before the current sampler.
    draws?: number
    // Wiggle-match path only. The combination index is not a diagnostic
    // beside the answer -- a D_Sequence asserts the gaps and cannot widen to
    // absorb a member that does not fit, so this IS the check that the match
    // is real. Reported without a pass/fail threshold, since what OxCal
    // quotes a combination against scales with the number of members and this
    // app does not print a number it has not pinned against a real run.
    combination_agreement?: number | null
    series_span?: number
    gap_units?: string
    reference_sample_id?: string | null
    members?: Array<{
      id: string
      name: string
      ring_offset: number
      ring_offset_error?: number | null
      agreement_index?: number | null
    }>
    // Sampled path: what each deposition sequence inferred about its own
    // rigidity. `pinned` means the posterior ran up against the prior's own
    // bound, so the PRIOR is doing the work rather than the data.
    deposition?: Array<{
      element_id: string
      name: string
      depth_units: string
      levels: number
      variable: boolean
      k_mean?: number
      log10_k_mean?: number
      log10_k_hpd_68?: [number, number]
      log_k_min?: number
      log_k_max?: number
      pinned?: boolean | null
    }>
  } | null
  // Durations and separations. Null when the run was not asked for them, which
  // is not "there are none" -- the page says so and points at the option.
  intervals?: IntervalsBlock | null
  results: SampleResult[]
}

interface SampleResult {
  sample_id: string
  sample_name?: string | null
  posterior_mean?: number
  posterior_median?: number
  posterior_std?: number
  hpd_68?: [number, number]
  hpd_95?: [number, number]
  // Full HPD breakdown: disjoint ranges each with their own probability
  // share. An older stored result may only have hpd_68/95.
  hpd_68_ranges?: HPDRange[]
  hpd_95_ranges?: HPDRange[]
  probability_distribution?: Array<[number, number]>
  // How well the modelled posterior still fits this date's own unmodelled
  // likelihood, as a percentage (Bronk Ramsey 1995).
  agreement_index?: number | null
  // Posterior probability (0-1) that the date does not belong to the
  // population being modelled. Null when no outlier model was applied, which
  // is not the same as a zero.
  outlier_probability?: number | null
  // This sample's unmodelled calibration, for the side-by-side comparison.
  // Bayesian analyses only: for a plain calibration, probability_distribution
  // above already IS the unmodelled result.
  //
  // Served as finished HPD ranges, one per confidence level this page offers,
  // rather than a series to derive them from: the server computes them over
  // the curve's full grid, where deriving them here would have only the
  // plottable series capped at 200 points -- which had the screen and the CSV
  // disagreeing in their last digits.
  unmodelled_hpd_ranges?: Array<{ confidence: number; ranges: HPDRange[] }> | null
  // The same unmodelled calibration as a plottable series, for the optional
  // "prior" shadow behind the modelled posterior -- see showPrior below.
  unmodelled_distribution?: Array<[number, number]> | null
}

/**
 * One posterior to plot. Dates and boundaries are both modelled parameters
 * with a distribution, so they normalise to one shape drawn by one component,
 * and the sequence of plots reads as the model does rather than as two lists.
 */
interface PlottableSeries {
  key: string
  kind: 'date' | 'boundary'
  /** Sample name, or the boundary's name. */
  title: string
  /** Where it sits in the structure; null for a date the structure omits. */
  path: string | null
  /** What kind of element the last step of that path is -- the container, not
   *  the row. The stacked plot draws a group in its own type's hue. */
  groupKind?: ElementType
  distribution?: Array<[number, number]>
  /** HPD bands at the page's selected confidence, outer first, from the same
   *  distribution the results table reads -- so a figure and the row above it
   *  cannot report different numbers for one quantity. */
  bands?: HPDBand[]
  /** A date's plain calibration, with no stratigraphic constraints. Absent
   *  for a boundary, which has no such counterpart. */
  priorDistribution?: Array<[number, number]>
  pointEstimates: { mean?: number; median?: number; std?: number }
  /** The radiocarbon measurement this date came from -- only the Curve view
   *  reads it, and only a date has one. */
  measurement?: SeriesMeasurement
}

/**
 * Copy the headline reading as it would be cited. One per page, beside the
 * number the analysis was run to produce -- not one per table row, which the
 * CSV, JSON and PDF exports already cover and which would cost a spacing shift
 * on every numeric cell and sixty-odd extra tab stops.
 */
function CopyCitation({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <Tooltip title={copied ? 'Copied' : 'Copy as cited'} describeChild>
      <IconButton
        size="small"
        aria-label={`Copy ${text}`}
        onClick={() => {
          navigator.clipboard?.writeText(text).then(
            () => {
              setCopied(true)
              window.setTimeout(() => setCopied(false), 1600)
            },
            () => undefined
          )
        }}
        sx={{
          ml: 0.5,
          p: 0.25,
          verticalAlign: 'baseline',
          // 24x24 is WCAG 2.5.8's floor; the negative margin stops the target
          // pushing the line box taller than the text it sits in.
          minWidth: 24,
          minHeight: 24,
          my: '-3px',
        }}
      >
        {copied ? (
          <CheckIcon sx={{ fontSize: 15, color: 'success.main' }} />
        ) : (
          <ContentCopyIcon sx={{ fontSize: 15 }} />
        )}
      </IconButton>
    </Tooltip>
  )
}

/**
 * The outer bounds of a set of HPD ranges — the same "span" the posterior
 * column reports, for a column that arrives as ranges rather than a
 * distribution. Both range columns default to this and expand to the full
 * breakdown on request, so by default they state the same KIND of thing and
 * can be compared down the row.
 */
function spanOfRanges(ranges: HPDRange[]): [number, number] | null {
  if (!ranges || ranges.length === 0) return null
  return [Math.min(...ranges.map((r) => r.min)), Math.max(...ranges.map((r) => r.max))]
}

/**
 * A range column's cell: the span, or every disjoint range, depending on what
 * the column's own header toggle says.
 */
function RangeCell({
  ranges,
  span,
  expanded,
  dateFormat,
  emphasis = false,
}: {
  ranges: HPDRange[]
  span: [number, number] | null
  expanded: boolean
  dateFormat: DateFormat
  emphasis?: boolean
}) {
  if (expanded && ranges.length > 0) {
    return (
      <Box component="span" sx={{ fontWeight: emphasis ? 600 : 400 }}>
        {formatHPDRanges(ranges, dateFormat)}
      </Box>
    )
  }
  return (
    <Box component="span" sx={{ fontWeight: emphasis ? 600 : 400 }}>
      {span ? formatCalendarRange(span[0], span[1], dateFormat) : 'N/A'}
    </Box>
  )
}

/**
 * A column header that can unfold its own column. The control sits on the
 * thing it changes rather than in the band above the table, so each range
 * column is asked separately and one can be held open against the other.
 */
function RangeColumnHeader({
  label,
  expanded,
  onToggle,
}: {
  label: React.ReactNode
  expanded: boolean
  onToggle(): void
}) {
  return (
    <>
      {label}
      <Tooltip title={expanded ? 'Show the span only' : 'Show every range'} describeChild>
        <IconButton
          size="small"
          aria-pressed={expanded}
          aria-label={
            expanded
              ? 'Collapse to the span'
              : 'Expand to every range in this column'
          }
          onClick={onToggle}
          sx={{
            ml: 0.5,
            p: 0.25,
            verticalAlign: 'baseline',
            minWidth: 24,
            minHeight: 24,
            my: '-3px',
          }}
        >
          {expanded ? (
            <UnfoldLessIcon sx={{ fontSize: 15 }} />
          ) : (
            <UnfoldMoreIcon sx={{ fontSize: 15 }} />
          )}
        </IconButton>
      </Tooltip>
    </>
  )
}

/** The precomputed unmodelled ranges for the confidence currently selected. */
function unmodelledRangesFor(
  result: SampleResult | undefined,
  confidence: number
): HPDRange[] {
  const match = result?.unmodelled_hpd_ranges?.find(
    (entry) => Math.abs(entry.confidence - confidence) < 1e-6
  )
  return match ? match.ranges : []
}

/**
 * A reading in the form it would be written into a manuscript, assembled once
 * from the same envelope the cell beside it prints.
 *
 * Everything in it is a fact the page already holds: the range at the selected
 * level, the curve, the solver. Nothing is inferred, and anything the page
 * does not know is left out rather than guessed at.
 */
export function citationLine(
  name: string,
  range: string,
  confidenceLabel: string,
  provenance: string[]
): string {
  // "span", matching the column it is copied from. A citation is the last
  // place to call outer bounds an HPD -- it is the string defended in review.
  const qualifiers = [`${confidenceLabel} span`, ...provenance].filter(Boolean).join(', ')
  return `${name}: ${range} (${qualifiers})`
}

// The 1/2/3-sigma levels radiocarbon results are conventionally reported at,
// offered as a single user choice rather than a fixed 68%/95% pair. Values and
// labels come from utils/hpd, which mirrors the backend's own definition.
const CONFIDENCE_OPTIONS = CONFIDENCE_LEVELS

// Indentation and marker for one row of the model structure. Sequences and
// phases are headings; boundaries and dates carry the numbers. No entries for
// 'sequence'/'phase': StructureTableRow's disclosure chevron does that job.
const STRUCTURE_MARKER: Record<string, string> = {
  // Only the warning: the coloured type label beside the name says what the
  // row IS, in the hue that element carries in the tree editor, the Harris
  // matrix and every plot. "Unassigned" stays a glyph because it is not an
  // element type -- it warns that a date belongs to nothing, and has no
  // colour of its own.
  unassigned: '⚠',
}

// The inline width the disclosure chevron occupies on a Sequence/Phase row,
// and the step each level of nesting indents by. The two are the same number
// on purpose: with the chevron sitting in the space one level of indentation
// buys, a date's name begins exactly where its Phase's name begins, instead
// of a step to the left of it, under the Phase's chevron.
const CHEVRON_GUTTER_PX = 28

// What each glyph above says, for readers who get no glyph. Only the ones
// carrying information: the date bullet is decoration and the element kind is
// already printed beside the name, so only the unassigned warning needs a
// voice.
const STRUCTURE_MARKER_LABEL: Record<string, string> = {
  unassigned: 'Not assigned to any element',
}

// Rows below which the table's 70vh window is not clipping anything worth a
// control. A dense row is ~37px, so 70vh holds roughly 17 of them on a laptop;
// under that the "show all rows" button would promise a change the reader
// cannot see.
const TABLE_WINDOW_ROWS = 18

// Every numeric column gets this: a multi-range HPD string wraps at tablet
// width, and wrapping mid-value splits a dash-joined range across two lines,
// reading as two numbers. One line instead lets the table outgrow its
// container, which TableContainer already scrolls for.
const NUMERIC_CELL_SX = { whiteSpace: 'nowrap' } as const

/**
 * The name column stays put while the numbers scroll under it. At 375px this
 * table is 894px wide inside a 295px box, so scrolling to the HPD range would
 * take the sample's NAME off screen with it, leaving a reader looking at
 * "633-557 cal BP · A 109 · O 3%" with nothing saying whose it is.
 *
 * Needs its own opaque background, or whatever scrolls beneath paints over it.
 * Callers pass the row's own tint so a flagged row keeps its colour here too.
 */
/* The two surfaces a sticky cell composites against. Imported rather than
   restated: sx colour tokens do not resolve inside a plain style object or
   inside the linear-gradient() below, which is why these were once local
   copies of the hex values -- and a copy of a token is a token that can drift.
   theme/surfaces.ts is the one place both these and createTheme read from. */

function stickyNameCell(background: string) {
  return {
    position: 'sticky' as const,
    left: 0,
    zIndex: 2,
    backgroundColor: background,
    // Capped on phones only: at 375px an uncapped column takes 170px of a
    // 295px container and occludes 88% of the span column at full scroll.
    // Desktop has room and keeps the full name.
    maxWidth: { xs: 116, sm: 'none' },
    overflow: { xs: 'hidden', sm: 'visible' },
    textOverflow: 'ellipsis',
    whiteSpace: { xs: 'nowrap', sm: 'normal' },
  }
}

/**
 * A shadow at the right edge, present only while there is more table to
 * scroll to. Without it the overflow is silent: the Overlap column simply is
 * not there, and nothing on screen suggests it could be.
 * `background-attachment: local` ties it to the scroll position.
 */
const SCROLL_HINT_SX = {
  backgroundImage:
    // PAPER, not var(--mui-paper): that custom property is defined nowhere in
    // this app, so the gradient would fall back to pure white -- including
    // where it fades across the table's warm header band.
    `linear-gradient(to left, ${PAPER} 40%, ${alpha(PAPER, 0)}),` +
    'radial-gradient(farthest-side at 100% 50%, rgba(43,35,32,0.16), rgba(43,35,32,0))',
  backgroundPosition: 'right center, right center',
  backgroundSize: '32px 100%, 14px 100%',
  backgroundRepeat: 'no-repeat',
  backgroundAttachment: 'local, scroll',
} as const

/**
 * The laboratory measurement as it is cited: `3120 ± 30`.
 *
 * The number on the lab report, not the value the Curve view plots a marine
 * date at -- that one has Delta-R subtracted, which makes it a derived figure
 * and this column is the one place on the page showing what was submitted.
 */
function measuredAge(measurement?: AnalysisMeasurement): string {
  if (!measurement || measurement.age_bp == null) return ''
  const { age_bp, error_1sigma } = measurement
  return error_1sigma != null ? `${age_bp} ± ${error_1sigma}` : String(age_bp)
}

/** A results-table column the reader can turn on or off. */
interface ResultColumn {
  id: string
  label: string
  /** Whether this run earns the column with nobody asking. */
  auto: boolean
  /** Whether the run produced any value for it. */
  populated: boolean
  /** What the picker says when it did not. */
  emptyNote: string
}

/**
 * The diagnostics column's cell: the chips that say a date wants looking at,
 * and the two numbers they are drawn from.
 *
 * One component for both row renderers. The structured table carried this
 * inline and the flat one -- every plain calibration, and any Bayesian
 * analysis with no stored model tree -- rendered no cell at all, so the header
 * advertised a Diagnostics column above a blank strip while the banner on the
 * same card named the dates that were supposed to be marked in it.
 */
function DiagnosticsCell({
  result,
  outlierPrior,
  outlierModel,
}: {
  result?: SampleResult
  outlierPrior?: number | null
  outlierModel?: 'general' | 'charcoal' | null
}) {
  const concern = concernFor(result, outlierPrior)
  // A date given a NOTABLE outlier prior whose posterior did not rise above it
  // still had special treatment -- worth saying, but not as a warning, so it
  // is a plain chip rather than folded into the reasons text. Below
  // NOTABLE_OUTLIER_PRIOR it stays quiet: OxCal's routine 5% default across a
  // phase is not worth a chip on every clean row.
  const assignedOutlier =
    !concern && (outlierPrior ?? 0) >= NOTABLE_OUTLIER_PRIOR
      ? { prior: outlierPrior ?? 0, model: outlierModel }
      : null
  return (
    <TableCell align="right" sx={NUMERIC_CELL_SX}>
      <Stack direction="column" spacing={0.5} alignItems="flex-end">
        {concern && (
          /* describeChild, and focusable: the sentence is the chip's
             description rather than its name, so a screen reader still
             hears "Suspect" first, and a keyboard or touch user can
             reach the reason at all -- a Chip is a div, and nothing in
             this column is hoverable on a tablet. */
          <Tooltip title={concernTitle(concern)} describeChild>
            <Chip
              size="small"
              tabIndex={0}
              color={concern.level === 'suspect' ? 'error' : 'warning'}
              variant={concern.level === 'suspect' ? 'filled' : 'outlined'}
              label={concern.level === 'suspect' ? 'Suspect' : 'Check'}
            />
          </Tooltip>
        )}
        {assignedOutlier && (
          <Tooltip
            title={
              `Given a ${Math.round(assignedOutlier.prior * 100)}% ` +
              `${assignedOutlier.model === 'charcoal' ? 'charcoal' : 'general'} outlier prior. ` +
              'The posterior is close to that, so the data did not add anything beyond what was already assumed.'
            }
            describeChild
          >
            <Chip size="small" tabIndex={0} variant="outlined" label="Assigned" />
          </Tooltip>
        )}
        <Box component="span" sx={{ fontSize: '0.75rem', color: 'text.secondary' }}>
          {/* Both numbers, always, so a reader can see a date is fine
              rather than only seeing the ones that are not. An em dash
              where a diagnostic was not computed -- an older analysis,
              or a date with no outlier model -- which is not zero. */}
          {result?.agreement_index != null ? `A ${result.agreement_index.toFixed(0)}` : 'A \u2014'}
          {'  \u00b7  '}
          {result?.outlier_probability != null
            ? `O ${Math.round(result.outlier_probability * 100)}%`
            : 'O \u2014'}
        </Box>
      </Stack>
    </TableCell>
  )
}

/**
 * One row of the structure-ordered results table. A boundary reports through
 * the same columns as a date: both are modelled parameters with a posterior,
 * and one table is what makes the transitions legible against the dates they
 * bracket.
 */
function StructureTableRow({
  row, result, measurement, curveName,
  isBayesian, showAge, showCurve, showUnmodelled, showMean, showMedian,
  showDiagnostics, confidence, dateFormat,
  unmodelledExpanded, posteriorExpanded,
  isCollapsible, isCollapsed, onToggleCollapse, thinNote,
}: {
  row: StructureRow
  result?: SampleResult
  isBayesian: boolean
  /** The lab measurement behind a date row; absent on an element row. */
  measurement?: AnalysisMeasurement
  /** Its curve's display name, already resolved. */
  curveName?: string | null
  showAge: boolean
  showCurve: boolean
  showUnmodelled: boolean
  /** The mean and its sigma: one choice, two columns. */
  showMean: boolean
  showMedian: boolean
  showDiagnostics: boolean
  confidence: number
  dateFormat: DateFormat
  /** Whether each range column is showing every range or just its span. */
  unmodelledExpanded: boolean
  posteriorExpanded: boolean
  /** A Sequence/Phase with something nested under it; only those get a
   *  disclosure control. */
  isCollapsible: boolean
  isCollapsed: boolean
  onToggleCollapse?: () => void
  /** Why this row's figures are expected to be wide; absent when they are not.
   *  See utils/thinPhases. */
  thinNote?: string
}) {
  const theme = useTheme()
  // The same map the Harris matrix and the tree editor colour their nodes
  // from, so one element type cannot be two colours across the app.
  const elementTypeColors = useElementTypeColors()
  const typeColor =
    row.kind !== 'date' && row.kind !== 'unassigned'
      ? elementTypeColors[row.kind]?.ink
      : undefined
  const isDate = row.kind === 'date'
  const posterior = row.posterior
  // A heading has no posterior of its own: its numbers are the boundaries
  // around it and the dates inside it.
  const isHeading = !isDate && !posterior

  const mean = isDate ? result?.posterior_mean : posterior?.posterior_mean
  const median = isDate ? result?.posterior_median : posterior?.posterior_median
  const std = isDate ? result?.posterior_std : posterior?.posterior_std
  const distribution = isDate ? result?.probability_distribution : posterior?.probability_distribution
  const envelope = distribution ? posteriorEnvelope(distribution, confidence, isBayesian) : null
  // The disjoint ranges behind that envelope, for when the column unfolds.
  const expandedRanges = useMemo(
    () => (posteriorExpanded && distribution
      ? posteriorRanges(distribution, confidence, isBayesian)
      : []),
    [posteriorExpanded, distribution, confidence, isBayesian]
  )
  const unmodelledRanges = isDate && isBayesian
    ? unmodelledRangesFor(result, confidence)
    : []

  const limiters = [
    row.tpqAgeBp != null ? `TPQ ${row.tpqAgeBp}` : null,
    row.taqAgeBp != null ? `TAQ ${row.taqAgeBp}` : null,
  ].filter(Boolean).join(' · ')

  const cell = (value: string) => (isHeading ? '' : value)
  // Still computed here as well as inside DiagnosticsCell: the tint is on the
  // row, not in the cell, and the cell is not always drawn.
  const concern = isDate ? concernFor(result, row.outlierPrior) : null
  // A tint carries the flag across the row so it is findable when scanning;
  // the chip says what it is, since colour alone is not a message.
  const tint = concern
    ? alpha(
        concern.level === 'suspect' ? theme.palette.error.main : theme.palette.warning.main,
        0.08
      )
    : undefined

  return (
    <TableRow
      sx={{
        // No fill on headings: a Sequence or Phase row carries only a name,
        // so a tint paints a warm band across empty columns, and a nested
        // model stacks seven of them in twenty rows. Weight and indentation
        // already say "heading"; the hairline gives the group a top edge.
        backgroundColor: tint ?? undefined,
        ...(isHeading && row.depth === 0
          ? { '& > td': { borderTop: 1, borderColor: 'divider' } }
          : {}),
        ...(concern
          ? { borderLeft: 3, borderColor: concern.level === 'suspect' ? 'error.main' : 'warning.main' }
          : {}),
      }}
    >
      <TableCell
        title={row.name}
        sx={{
          pl: `${16 + row.depth * CHEVRON_GUTTER_PX}px`,
          ...stickyNameCell(tint ?? PAPER),
        }}
      >
        {/* The disclosure control is the only marker a Sequence/Phase row
            gets -- a static glyph beside it would be a second symbol saying
            "this is a heading". A non-collapsible heading gets neither: no
            button, having nothing to toggle, and no glyph, STRUCTURE_MARKER
            carrying no entry for those kinds. */}
        {isCollapsible && (
          <IconButton
            size="small"
            onClick={onToggleCollapse}
            aria-label={`${isCollapsed ? 'Expand' : 'Collapse'} ${row.name}`}
            // The chevron stays small, sitting inline in a dense row, but its
            // hit target does not: negative margins let the 44px target
            // overlap the row's padding rather than setting the row height,
            // and narrow its inline footprint to one indentation step so the
            // name beside it lands on the same column its children do.
            sx={{
              p: 0.25,
              verticalAlign: 'middle',
              width: 44,
              height: 44,
              my: '-10px',
              mx: `${(44 - CHEVRON_GUTTER_PX) / -2}px`,
            }}
          >
            {isCollapsed ? (
              <KeyboardArrowRightIcon fontSize="small" />
            ) : (
              <KeyboardArrowDownIcon fontSize="small" />
            )}
          </IconButton>
        )}
        {/* aria-hidden on the glyph, with the word beside it for anything
            that is not read out loud: "!" was the ONLY marker on an
            unassigned date, so a screen reader heard nothing at all where a
            sighted reader saw a warning. */}
        {STRUCTURE_MARKER[row.kind] && (
          <>
            <Box component="span" aria-hidden sx={{ color: 'text.secondary', mr: 1 }}>
              {STRUCTURE_MARKER[row.kind]}
            </Box>
            {STRUCTURE_MARKER_LABEL[row.kind] && (
              <Box component="span" sx={visuallyHidden}>
                {STRUCTURE_MARKER_LABEL[row.kind]},{' '}
              </Box>
            )}
          </>
        )}
        <Box component="span" sx={{ fontWeight: isHeading ? 600 : 400 }}>
          {row.name}
        </Box>
        {/* The row's element type, in that type's own colour.
            
            Not decoration and not removable: this is the ONLY thing in the
            table that distinguishes a Sequence from a Phase, and the two are
            not interchangeable -- a Sequence orders what it contains, a Phase
            does not, so the same dates under one mean something different
            from the same dates under the other. Counted across the real
            models in this database: every one of 45 phases and 18 of 26
            sequences would show nothing at all without it.
            
            The colour is the app's existing element-type palette, so a Phase
            is the same green here as in the tree editor, the Harris matrix
            and the plots. The table was the last view of the model still
            saying element type in grey. All four inks clear 4.5:1 at this
            size; the word carries it for anyone who cannot see the hue.
            
            Still suppressed when the name already says it -- an element
            called "Boundary" does not need "boundary" after it, and briefly
            did. */}
        {!isDate && typeColor && !row.name.toLowerCase().includes(String(row.kind)) && (
          <Box
            component="span"
            sx={{ ml: 1, color: typeColor, fontSize: '0.75rem', fontWeight: 500 }}
          >
            {row.kind}
          </Box>
        )}
        {limiters && (
          <Box component="span" sx={{ ml: 1, color: 'text.secondary', fontSize: '0.75rem' }}>
            ({limiters})
          </Box>
        )}
        {/* A phase with fewer than four dates, or a Boundary next to one.
            Its range is correct and will be very wide, which reads as a
            failed run unless something says otherwise. Same chip-in-a-
            tooltip idiom as the outlier column: describeChild so the label
            is heard first, tabIndex so the reason is reachable without a
            hover. */}
        {thinNote && (
          <Tooltip title={thinNote} describeChild>
            <Chip
              size="small"
              tabIndex={0}
              variant="outlined"
              color="warning"
              label="Few dates"
              sx={{ ml: 1, height: 20, fontSize: '0.6875rem' }}
            />
          </Tooltip>
        )}
      </TableCell>
      {/* Only a date was measured. A Sequence or Phase is a heading and a
          Boundary is a modelled transition, so neither has a lab report or a
          curve behind it -- the cells are blank rather than filled with a
          placeholder that would read as missing data. */}
      {showAge && (
        <TableCell align="right" sx={NUMERIC_CELL_SX}>
          {isDate ? measuredAge(measurement) : ''}
        </TableCell>
      )}
      {showCurve && (
        <TableCell sx={NUMERIC_CELL_SX}>{isDate ? curveName ?? '' : ''}</TableCell>
      )}
      {/* Unmodelled first, then everything the MODEL produced: mean, std,
          median, span. The point estimates used to sit left of the unmodelled
          column, which put three posterior quantities inside the raw
          calibration's half of the table. */}
      {showUnmodelled && (
        <TableCell align="right" sx={NUMERIC_CELL_SX}>
          {/* Only a date has an unmodelled counterpart -- a boundary exists
              solely as a modelled parameter. */}
          {isDate ? (
            <RangeCell
              ranges={unmodelledRanges}
              span={spanOfRanges(unmodelledRanges)}
              expanded={unmodelledExpanded}
              dateFormat={dateFormat}
            />
          ) : (
            ''
          )}
        </TableCell>
      )}
      {showMean && (
        <TableCell align="right" sx={NUMERIC_CELL_SX}>
          {cell(mean != null ? formatCalendarAge(mean, dateFormat) : 'N/A')}
        </TableCell>
      )}
      {showMean && (
        <TableCell align="right" sx={NUMERIC_CELL_SX}>
          {cell(std?.toFixed(0) ?? 'N/A')}
        </TableCell>
      )}
      {showMedian && (
        <TableCell align="right" sx={NUMERIC_CELL_SX}>
          {cell(median != null ? formatCalendarAge(median, dateFormat) : 'N/A')}
        </TableCell>
      )}
      {/* Weight, not a cell fill: the flagged-date rows already carry a
          background tint, and a column fill compositing under those would
          make a Suspect row's answer a different colour from everyone
          else's. 600 reads as emphasis in a tabular-nums column without
          touching the row's own colour. */}
      <TableCell align="right" sx={NUMERIC_CELL_SX}>
        {isHeading ? (
          ''
        ) : (
          <RangeCell
            ranges={expandedRanges}
            span={envelope}
            expanded={posteriorExpanded}
            dateFormat={dateFormat}
            emphasis
          />
        )}
      </TableCell>
      {showDiagnostics &&
        (isDate ? (
          <DiagnosticsCell
            result={result}
            outlierPrior={row.outlierPrior}
            outlierModel={row.outlierModel}
          />
        ) : (
          /* A heading or a boundary has no calibration of its own to agree
             with, so the column is blank rather than reporting two em
             dashes as though the diagnostics had been attempted. */
          <TableCell align="right" sx={NUMERIC_CELL_SX} />
        ))}
    </TableRow>
  )
}

export default function Results() {
  const { analysisId } = useParams<{ analysisId: string }>()
  const navigate = useNavigate()
  const notify = useNotify()
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null)
  // "Gone" and "unreachable" get different copy and different offers: a Try
  // again button on a deleted analysis just fetches the same 404 back.
  const [missing, setMissing] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dateFormat, setDateFormat] = useState<DateFormat>(() =>
    analysisId ? loadDateFormatPreference(analysisId) : 'calBP'
  )
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [rerunOpen, setRerunOpen] = useState(false)
  const [rerunning, setRerunning] = useState(false)
  // The run this page started, if it started one. Null when reached by any
  // other route -- a bookmark, a reload, a dashboard link -- where the
  // analysis id is all there is and useJob asks for its most recent job.
  const [jobId, setJobId] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [duplicating, setDuplicating] = useState(false)
  const [exportingPdf, setExportingPdf] = useState(false)
  // Which optional columns the table draws, remembered per analysis. Only
  // columns the reader has explicitly turned on or off are stored: a column
  // they have never touched keeps following the run, exactly as the date
  // collection's picker works.
  const [columnOverrides, setColumnOverrides] = useState<ColumnOverrides>(() =>
    analysisId ? loadResultColumnOverrides(analysisId) : {}
  )
  useEffect(() => {
    setColumnOverrides(analysisId ? loadResultColumnOverrides(analysisId) : {})
  }, [analysisId])
  const [columnsAnchor, setColumnsAnchor] = useState<HTMLElement | null>(null)
  // On by default, the transitions usually being the point of the model. A
  // real sequence has a boundary between every pair of phases, though, so on a
  // large one they outnumber the dates -- this hides them without the dates.
  const [showBoundaryPlots, setShowBoundaryPlots] = useState(true)
  // The results table's 70vh scroll window, lifted. Off by default -- the cap
  // is what keeps a 63-date model from putting a page-and-a-half of rows
  // between the reader and the plots -- but a reader who wants the table as one
  // block (to read it straight through, or to hand the page to print/Cmd-F
  // across every row) has no way to get it while the window is fixed.
  const [tableExpanded, setTableExpanded] = useState(false)

  // Collapsed by default. The diagram is a tall drawing in a landscape frame,
  // so it leaves wide margins either side -- and a wheel over those margins
  // zooms the canvas instead of scrolling the page, trapping a reader on their
  // way down to the plots. Almost every visit is for the table, the plots and
  // the durations; the structure is a reference the model's author already
  // knows, so it opens on request rather than sitting in the way.
  const [structureOpen, setStructureOpen] = useState(false)

  // Off by default: the modelled posterior is the primary reading, and
  // overlaying the unconstrained calibration behind every date is a comparison
  // a reader opts into.
  const [showPrior, setShowPrior] = useState(false)
  // How the posteriors are drawn. Stacked is the default -- one figure, one
  // row per date, one calendar axis -- which is the only way to see whether two
  // dates overlap, the question a sequence is built to answer. The per-date
  // charts stay available because stacking trades detail for comparison: they
  // carry the full-height curve, the point estimates and their own zoom.
  //
  // Curve draws the same posteriors against the calibration curve that produced
  // them. The other two answer "what came out"; this one answers "why it came
  // out that shape".
  const [plotView, setPlotView] = useState<'stacked' | 'individual' | 'curve'>('stacked')
  // Confidence level for the HPD columns, defaulting to 95.4% (2-sigma) and
  // switchable without a round trip (see utils/hpd.ts).
  // Only to turn a stored curve_id into the curve's name in the citation
  // strings. The request is shared across the app (api/curves.ts), and a
  // failure degrades to the id rather than dropping the curve from the line.
  const { curves } = useCurves()
  const [confidence, setConfidence] = useState<number>(SIGMA_2)
  // Each range column unfolds from its own header. Off by default: a span per
  // column is the scannable form, and the breakdown is asked one column at a
  // time.
  const [unmodelledExpanded, setUnmodelledExpanded] = useState(false)
  const [posteriorExpanded, setPosteriorExpanded] = useState(false)
  // One string for that level wherever the page names it, so a column
  // heading, a plot legend and a diagram label cannot disagree about which
  // confidence the figures beside them were computed at.
  const CONFIDENCE_LABEL = CONFIDENCE_OPTIONS.find((o) => o.value === confidence)?.label ?? ''
  // Which Sequence/Phase headings are collapsed, by element id. Nothing
  // collapsed by default.
  const [collapsedElements, setCollapsedElements] = useState<Set<string>>(new Set())
  const toggleCollapsed = (elementId: string) =>
    setCollapsedElements((prev) => {
      const next = new Set(prev)
      if (next.has(elementId)) next.delete(elementId)
      else next.add(elementId)
      return next
    })

  const handleDateFormatChange = (e: SelectChangeEvent) => {
    const next = e.target.value as DateFormat
    setDateFormat(next)
    if (analysisId) {
      saveDateFormatPreference(analysisId, next)
    }
  }

  useEffect(() => {
    if (analysisId) {
      fetchAnalysis(analysisId)
    }
  }, [analysisId])

  /**
   * Load the analysis, distinguishing "this one is gone" from "the server is
   * unreachable" -- they need different words and different offers.
   *
   * The `response.ok` check is what separates them: without it a deleted or
   * mistyped analysis falls through to a generic "Failed to load analysis",
   * which reads as transient and invites a pointless reload, while the
   * backend's accurate "Analysis not found" is parsed and discarded.
   */
  const fetchAnalysis = async (id: string) => {
    setLoading(true)
    setError(null)
    setMissing(false)
    try {
      const response = await fetch(`/api/analysis/${id}`)
      const data = await response.json().catch(() => null)
      if (response.ok && data?.success) {
        setAnalysis(data.data)
        return
      }
      if (response.status === 404) {
        setMissing(true)
        setError(formatApiError(data) || 'Analysis not found')
        return
      }
      setError(formatApiError(data) || `The server returned ${response.status}.`)
    } catch {
      // fetch itself rejected, so there is no response to quote. The
      // browser's own "Failed to fetch" names an API rather than a problem;
      // the reader needs to know it is the connection, not their data.
      setError('Could not reach the server. It may be down, or your connection may have dropped.')
    } finally {
      setLoading(false)
    }
  }

  // Follow whatever is running against this analysis and reload when it
  // stops, however it stopped: a failed or cancelled run changes what the page
  // should show as much as a successful one.
  const { job, active: jobActive } = useJob({
    jobId,
    targetId: jobId ? null : analysisId,
    onFinished: () => {
      if (analysisId) fetchAnalysis(analysisId)
      setCancelling(false)
    },
  })

  const handleCancelRun = async () => {
    if (!job) return
    setCancelling(true)
    try {
      await cancelJob(job.job_id)
    } catch (err) {
      notify((err as Error).message)
      setCancelling(false)
    }
  }

  const handleExportCSV = async () => {
    if (!analysisId) return
    try {
      // Carry the displayed reporting format, so the download reads in the
      // same convention as the screen. The cal BP columns come regardless.
      const response = await fetch(
        `/api/results/${analysisId}/export/csv?date_format=${encodeURIComponent(dateFormat)}`
      )
      // An error response is JSON, which a spreadsheet opens happily as a
      // one-cell file -- so unchecked, a failed export saves as a .csv and is
      // only noticed by whoever opens it looking for their results.
      if (!response.ok) {
        throw new Error(formatApiError(await response.json().catch(() => null)))
      }
      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `analysis-${analysisId}.csv`
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)
    } catch (err) {
      notify('Failed to export CSV: ' + (err as Error).message)
    }
  }

  // The PDF is rendered server-side at a plot per modelled parameter, so a
  // large model takes seconds -- long enough that a button just sitting there
  // reads as broken. The CSV, returned in milliseconds, needs no such state.
  const handleExportPDF = async () => {
    if (!analysisId) return
    setExportingPdf(true)
    try {
      const response = await fetch(
        `/api/results/${analysisId}/pdf?date_format=${encodeURIComponent(dateFormat)}`
      )
      // Checked before reading the body: an error response is JSON, and
      // saving it as a .pdf hands the user a file that fails only on opening.
      if (!response.ok) {
        throw new Error(formatApiError(await response.json().catch(() => null)))
      }
      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `analysis-${analysisId}-report.pdf`
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)
    } catch (err) {
      notify('Failed to export PDF: ' + (err as Error).message)
    } finally {
      setExportingPdf(false)
    }
  }

  const handleDelete = async () => {
    if (!analysisId) return
    setDeleting(true)
    try {
      const response = await fetch(`/api/analysis/${analysisId}`, { method: 'DELETE' })
      const data = await response.json()
      if (data.success) {
        navigate(analysis?.project_id ? `/projects/${analysis.project_id}` : '/projects')
      } else {
        notify('Failed to delete analysis: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error deleting analysis: ' + (err as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  // Copy this analysis's inputs into a new one and open it for editing: the
  // "same dates, different model" path, leaving this one untouched.
  const handleDuplicate = async () => {
    if (!analysisId) return
    setDuplicating(true)
    try {
      const response = await fetch(`/api/analysis/${analysisId}/duplicate`, { method: 'POST' })
      const data = await response.json()
      if (data.success) {
        navigate(`/analysis/${data.data.id}/edit`)
      } else {
        notify('Failed to duplicate analysis: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error duplicating analysis: ' + (err as Error).message)
    } finally {
      setDuplicating(false)
    }
  }

  // Queued, not awaited: the endpoint answers 202 with a job id as soon as it
  // accepts the work, and the progress display takes it from there.
  const handleRerun = async () => {
    if (!analysisId) return
    setRerunning(true)
    try {
      const response = await fetch(`/api/analysis/${analysisId}/rerun`, { method: 'POST' })
      const data = await response.json()
      if (data.success) {
        setRerunOpen(false)
        setJobId(data.data.job_id ?? null)
        fetchAnalysis(analysisId)
      } else {
        notify('Failed to rerun analysis: ' + formatApiError(data))
      }
    } catch (err) {
      notify('Error rerunning analysis: ' + (err as Error).message)
    } finally {
      setRerunning(false)
    }
  }

  // Which phase each date sits in, for the Harris matrix below.
  //
  // No fallback to the stored contexts: GET /analysis/:id runs
  // _get_or_migrate_tree for every Bayesian analysis, so model_tree is null
  // only for a plain or group calibration, which has no stratigraphy to draw.
  //
  // Memoised on `analysis` so the identity handed to the diagram is stable and
  // the layout recomputes only when the analysis changes.
  const structureAssignments = useMemo(
    () =>
      Object.fromEntries(
        (analysis?.measurements || [])
          .map((m) => [m.id, m.element_id || m.group_id])
          .filter(([, elementId]) => !!elementId) as [string, string][]
      ),
    [analysis]
  )

  // The wiggle match this analysis is, if it is one: the element carrying the
  // series' single free parameter, plus the posterior the run put on it.
  //
  // Found from the tree rather than from the solver record alone, because the
  // panel needs the element's NAME -- which timber this is -- and the record
  // carries the numbers.
  const wiggleMatch = useMemo(() => {
    const tree = analysis?.model_tree
    if (!tree || analysis?.solver?.method !== 'wiggle_match') return null
    const found: Array<{ name: string; posterior: PosteriorSummary }> = []
    const walk = (element: ModelElement) => {
      if (element.element_type === 'wiggle_sequence' && element.posterior) {
        found.push({ name: element.name || 'Wiggle match', posterior: element.posterior })
      }
      for (const child of element.children || []) walk(child)
    }
    walk(tree)
    return found[0] || null
  }, [analysis])

  // One record per deposition sequence in the model, empty otherwise. Read off
  // the intervals block rather than the tree: the band is a function of
  // several ages at once and so lives with the durations, not on the elements.
  const ageDepthRecords = useMemo(
    () => analysis?.intervals?.age_depth || [],
    [analysis]
  )

  // The wave-of-advance fit, or null when it was not asked for. Read the same
  // way age_depth is, and NULL means the same thing it means there: the run
  // was not asked, rather than there being nothing to report.
  const dispersalFit = useMemo(
    () => analysis?.intervals?.dispersal || null,
    [analysis]
  )

  // Whether this model has anything for a duration or a difference to be
  // ABOUT -- the question the "turn the option on and re-run" offer below
  // depends on, and which it used to skip.
  //
  // A duration is the interval a pair of Boundaries encloses, or the span of a
  // phase's own dated events; a difference is between two such intervals. So a
  // model with neither has nothing to report however the option is set, and
  // offering a re-run promises something the run cannot produce.
  //
  // A WIGGLE MATCH never qualifies, and falls out of the same rule rather
  // than needing a case of its own: its dates sit in the wiggle sequence
  // rather than in a phase, and it has no Boundaries at all.
  const couldReportIntervals = useMemo(() => {
    if (!analysis) return false
    // A group calibration has no model, and its differences are between the
    // dates themselves.
    if (analysis.analysis_type === 'group_calibration') {
      return (analysis.results || []).length > 1
    }
    if (analysis.analysis_type !== 'bayesian') return false
    // A run that HAS results has a stored tree, so "cannot tell" here means
    // an analysis with no structure at all, which reports nothing either way.
    return modelCanReportIntervals(analysis.model_tree, analysis.measurements || []) === true
  }, [analysis])

  // Declared above the loading/error early returns: a hook after a conditional
  // return is only called on some renders.
  //
  // What produced every figure on the page, for the citation strings -- the
  // curves used and the solver that ran, both already displayed elsewhere.
  const solverName =
    analysis?.solver?.method === 'exact'
      ? 'exact solver'
      : analysis?.solver?.method === 'wiggle_match'
      ? 'wiggle-match solver'
      : analysis?.solver?.method
      ? 'MH/Gibbs solver'
      : null

  // Provenance for ONE row, not one string for the page: a date is calibrated
  // against exactly one curve, and that is the one its citation names. Joining
  // every curve in the analysis would have each line of a mixed model -- 59
  // dates on SHCal20 and 4 on Marine20 -- claiming both.
  //
  // A Boundary has no curve at all, being a modelled transition rather than a
  // measurement, so it names the solver alone.
  const curveNameFor = useMemo(() => {
    const byId = new Map(curves.map((c) => [c.id, c.name]))
    return (curveId?: string | null) => (curveId ? byId.get(curveId) ?? curveId : null)
  }, [curves])

  const provenanceForSample = useMemo(() => {
    const curveBySampleId = new Map(
      (analysis?.measurements || []).map((m) => [m.id, m.curve_id])
    )
    return (sampleId?: string | null): string[] =>
      [sampleId ? curveNameFor(curveBySampleId.get(sampleId)) : null, solverName].filter(
        (v): v is string => Boolean(v)
      )
  }, [analysis?.measurements, curveNameFor, solverName])

  /**
   * The plot bands for one series, computed once per confidence level.
   *
   * `plotOrder` is rebuilt on every render, and `hpdBands` runs two
   * `hpdRangesFromCurve` passes, each sorting a ~1000-point distribution --
   * ~126 sorts on a 63-date model, on every keystroke of every unrelated
   * control, measured at 127ms median to paint for one checkbox.
   *
   * The bands depend on the distribution and the selected confidence alone, so
   * they are cached against the series key and the cache is dropped when the
   * confidence changes. Declared above the loading/error early returns,
   * because a hook after a conditional return runs only on some renders.
   */
  const bandsFor = useMemo(() => {
    const cache = new Map<string, HPDBand[]>()
    return (key: string, distribution?: Array<[number, number]>): HPDBand[] => {
      if (!distribution) return []
      const hit = cache.get(key)
      if (hit) return hit
      const bands = hpdBands(
        distribution, confidence, CONFIDENCE_LABEL,
        analysis?.analysis_type === 'bayesian')
      cache.set(key, bands)
      return bands
    }
  }, [confidence, CONFIDENCE_LABEL, analysis?.analysis_type])

  // For the page-level qualifier under the headline, where naming every curve
  // the analysis used IS the honest statement.
  //
  // No solver here, though provenanceForSample above does carry one: the solver
  // chip sits directly beneath this line, and a reader meeting the same fact
  // twice in two lines reads the second as a different fact. A copied citation
  // has no chip beside it, so it keeps the solver.
  const analysisProvenance = useMemo(() => {
    const curveIds = [
      ...new Set((analysis?.measurements || []).map((m) => m.curve_id).filter(Boolean)),
    ]
    const curveNames = curveIds.map((id) => curveNameFor(id)).filter(Boolean) as string[]
    return [curveNames.join(' / ')].filter((v): v is string => Boolean(v))
  }, [analysis?.measurements, curveNameFor])

  if (loading) {
    return (
      <Container sx={{ py: 4, display: 'flex', justifyContent: 'center' }}>
        <CircularProgress />
      </Container>
    )
  }

  if (error || !analysis) {
    // component="main" and role="alert", so the route a stale bookmark lands
    // on has a landmark and an announcement, as the success branch does.
    return (
      <Container component="main" maxWidth="lg" sx={{ py: 4 }}>
        <Paper sx={{ p: 3, maxWidth: 560 }} role="alert">
          {/* h5, not h6: this is the page's only heading on this route, and
              at 1.25rem it read as a card title on a blank page. */}
          <Typography variant="h5" component="h1" gutterBottom>
            {missing ? 'That analysis doesn’t exist' : 'Couldn’t load this analysis'}
          </Typography>
          <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
            {missing
              ? 'It may have been deleted, or the link may be wrong. Your other analyses are unaffected.'
              : error}
          </Typography>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            {/* Retry only where retrying could work. Offering it on a 404
                would invite the same answer a second time. */}
            {!missing && analysisId && (
              <Button
                variant="contained"
                startIcon={<ReplayIcon />}
                onClick={() => fetchAnalysis(analysisId)}
              >
                Try again
              </Button>
            )}
            <Button variant={missing ? 'contained' : 'outlined'} onClick={() => navigate('/')}>
              Back to Dashboard
            </Button>
            <Button variant="outlined" onClick={() => navigate('/projects')}>
              Browse projects
            </Button>
          </Stack>
        </Paper>
      </Container>
    )
  }

  // Where each date sits comes from the structure walk itself (see plotOrder
  // and the results table) rather than a separate sample -> context lookup,
  // which would have to special-case element_id against group_id.
  // flattenStructure resolves that in one place.
  const contextsWithModeledSpan = (analysis.groups || []).filter((g) => g.modeled_start && g.modeled_end)

  // Every named Boundary in the structure, oldest first. Preferred over the
  // per-context table below when there is a model tree, since adjacent
  // contexts SHARE a boundary and listing them per context would print the
  // same transition twice.
  const boundaryRows: Array<{ id: string; name: string; posterior: PosteriorSummary }> = []
  const collectBoundaries = (element?: ModelElement | null) => {
    if (!element) return
    if (element.element_type === 'boundary' && element.posterior) {
      boundaryRows.push({
        id: element.id,
        name: element.name || 'Boundary',
        posterior: element.posterior,
      })
    }
    for (const child of element.children || []) collectBoundaries(child)
  }
  collectBoundaries(analysis.model_tree)
  const isBayesian = analysis.analysis_type === 'bayesian'
  const measurementBySampleId = new Map((analysis.measurements || []).map((m) => [m.id, m]))

  /**
   * The columns the reader can turn on or off, and whether this run earns
   * each one unasked.
   *
   * `auto` is the default the data implies; `populated` is whether the run
   * actually produced values, which is what the picker says out loud so that
   * switching on an empty column reads as empty rather than as broken. The
   * same contract the date collection's picker uses.
   *
   * Mean, median and sigma are opt-in on purpose: HPD ranges are the primary
   * convention for reporting a possibly multimodal result, and a single point
   * estimate can mislead -- a mean can fall in the low-probability gap between
   * two peaks. The unmodelled span and the diagnostics are the opposite: they
   * are why a Bayesian run was made, so they arrive on and can be put away.
   *
   * The mean's entry governs two columns, the estimate and its sigma, and is
   * labelled to say so -- they are reported as one figure, mean ± σ.
   */
  const results = analysis.results || []
  const someResult = (has: (r: SampleResult) => boolean) => results.some(has)
  const someMeasurement = (has: (m: AnalysisMeasurement) => boolean) =>
    (analysis.measurements || []).some(has)
  const optionalColumns: ResultColumn[] = [
    // The lab report, off by default: the page exists to show what the
    // measurement became, and a reader who wants the input has the date
    // collection and every export. Both sit left of everything derived from
    // them, in the order the date collection's own table lists them, so the
    // table reads input first and model last.
    {
      id: 'age',
      // Non-breaking between the sign and the value: with every column on,
      // the header cells wrap to fit and this one broke as "Age BP ±" over
      // "1σ", splitting a notation down the middle.
      label: 'Age BP ±\u00a01σ',
      auto: false,
      populated: someMeasurement((m) => m.age_bp != null),
      emptyNote: 'Not stored for this analysis',
    },
    {
      id: 'curve',
      label: 'Curve',
      auto: false,
      populated: someMeasurement((m) => !!m.curve_id),
      emptyNote: 'Not stored for this analysis',
    },
    ...(isBayesian
      ? [
          {
            id: 'unmodelled',
            label: 'Unmodelled span',
            auto: true,
            populated: someResult((r) => (r.unmodelled_hpd_ranges?.length ?? 0) > 0),
            emptyNote: 'Not stored for this analysis',
          } as ResultColumn,
        ]
      : []),
    {
      id: 'mean',
      // Two columns, one entry, and the label says so. Sigma is the mean's
      // own dispersion -- the pair is reported as mean ± σ -- so a picker
      // that could turn one on without the other would offer a spread with
      // nothing to read it against. The median's counterpart is the HPD
      // span, which is always drawn.
      label: isBayesian ? 'Posterior Mean and σ' : 'Calibrated Mean and σ',
      auto: false,
      populated: someResult((r) => r.posterior_mean != null),
      emptyNote: 'No values in this analysis',
    },
    {
      id: 'median',
      label: isBayesian ? 'Posterior Median' : 'Calibrated Median',
      auto: false,
      populated: someResult((r) => r.posterior_median != null),
      emptyNote: 'No values in this analysis',
    },
    ...(isBayesian
      ? [
          {
            id: 'diagnostics',
            label: 'Diagnostics',
            auto: true,
            populated: someResult(
              (r) => r.agreement_index != null || r.outlier_probability != null
            ),
            emptyNote: 'Not computed for this analysis',
          } as ResultColumn,
        ]
      : []),
  ]

  const labelOf = (id: string) => optionalColumns.find((c) => c.id === id)?.label ?? ''
  const shows = (id: string) => {
    const column = optionalColumns.find((c) => c.id === id)
    return !!column && columnVisible(columnOverrides, id, column.auto)
  }
  const showAge = shows('age')
  const showCurve = shows('curve')
  const showUnmodelled = shows('unmodelled')
  const showMean = shows('mean')
  const showMedian = shows('median')
  const showDiagnostics = shows('diagnostics')

  const toggleColumn = (column: ResultColumn) => {
    setColumnOverrides((prev) => {
      const shown = columnVisible(prev, column.id, column.auto)
      const next = { ...prev }
      // Choosing what the run already does is not an override -- dropping it
      // lets the column go back to following the analysis.
      if (!shown === column.auto) delete next[column.id]
      else next[column.id] = !shown
      if (analysisId) saveResultColumnOverrides(analysisId, next)
      return next
    })
  }

  const resetColumns = () => {
    setColumnOverrides({})
    if (analysisId) saveResultColumnOverrides(analysisId, {})
  }

  // The results table in the model's own order, mirroring the CSV export row
  // for row (backend model_tree.flatten_structure). Empty for an analysis with
  // no stored structure, which falls back to the flat date list below.
  const structureRows = flattenStructure(analysis.model_tree, analysis.measurements || [])

  // Phases too thin for their Boundaries to be informative, and the
  // Boundaries either side of them. Bayesian only: a plain calibration has no
  // Boundaries for the count to be about.
  // Not memoised: this sits below the page's early returns, where a hook
  // would change the hook count between renders. It is one walk of a tree of
  // tens of elements, the same as flattenStructure just above.
  const thinPhases = isBayesian
    ? findThinPhases(analysis.model_tree, analysis.measurements || [])
    : { phases: new Map(), boundaries: new Map() }
  const thinNoteFor = (row: StructureRow): string | undefined => {
    if (!row.elementId) return undefined
    if (row.kind === 'phase') {
      const phase = thinPhases.phases.get(row.elementId)
      return phase ? describeThinPhase(phase) : undefined
    }
    if (row.kind === 'boundary') {
      const neighbours = thinPhases.boundaries.get(row.elementId)
      return neighbours ? describeThinBoundary(neighbours) : undefined
    }
    return undefined
  }

  // Element type by id, so a date row can say what KIND of thing contains it.
  // A date row carries its container's id, not its type; the element's own row
  // in the same walk carries the type.
  const kindByElementId = new Map<string, ElementType>()
  for (const row of structureRows) {
    if (row.elementId && row.kind !== 'date' && row.kind !== 'unassigned') {
      kindByElementId.set(row.elementId, row.kind)
    }
  }

  // What the collapsed Model Structure panel says it is hiding, so the button
  // is a decision rather than a guess. Counted off the same walk the table
  // uses, so the two never disagree.
  const structureSummary = (() => {
    const counts = { phase: 0, boundary: 0, date: 0 }
    for (const row of structureRows) {
      if (row.kind === 'phase') counts.phase += 1
      else if (row.kind === 'boundary') counts.boundary += 1
      else if (row.kind === 'date') counts.date += 1
    }
    const parts: string[] = []
    if (counts.phase) parts.push(`${counts.phase} phase${counts.phase === 1 ? '' : 's'}`)
    if (counts.boundary)
      parts.push(`${counts.boundary} boundar${counts.boundary === 1 ? 'y' : 'ies'}`)
    if (counts.date) parts.push(`${counts.date} date${counts.date === 1 ? '' : 's'}`)
    return parts.join(', ')
  })()

  // Sequence/Phase headings with something nested under them. An empty one
  // gets no collapse control, having nothing to hide.
  const collapsibleElementIds = new Set(
    structureRows
      .filter((row, idx) => {
        if (row.kind !== 'sequence' && row.kind !== 'phase') return false
        const next = structureRows[idx + 1]
        return !!next && next.depth > row.depth
      })
      .map((row) => row.elementId!)
  )

  // structureRows with any collapsed subtree hidden. Boundaries stay visible
  // even inside one: a Boundary is a modelled transition's own result rather
  // than detail to fold away, and collapsing a large structure down to its
  // Boundaries is often the point. One pass suffices, since a collapse nested
  // inside an already-hidden subtree changes nothing.
  const visibleStructureRows: StructureRow[] = []
  let hiddenBelowDepth: number | null = null
  for (const row of structureRows) {
    if (hiddenBelowDepth !== null) {
      if (row.depth <= hiddenBelowDepth) {
        hiddenBelowDepth = null
      } else {
        if (row.kind === 'boundary') visibleStructureRows.push(row)
        continue
      }
    }
    visibleStructureRows.push(row)
    if (
      (row.kind === 'sequence' || row.kind === 'phase') &&
      row.elementId &&
      collapsedElements.has(row.elementId)
    ) {
      hiddenBelowDepth = row.depth
    }
  }

  const resultBySampleId = new Map((analysis.results || []).map((r) => [r.sample_id, r]))
  // As concernFor needs it in StructureTableRow: a posterior close to the
  // prior it was given is not a finding, so the banner needs both.
  // analysis.results arrives in DB insertion order. With no model structure to
  // walk, sort oldest first by posterior mean (larger cal BP is older), which
  // is the convention structureRows gives the structured case. Ties and
  // missing means fall back to the original order.
  const chronologicalResults = [...(analysis.results || [])].sort((a, b) => {
    if (a.posterior_mean == null || b.posterior_mean == null) return 0
    return b.posterior_mean - a.posterior_mean
  })

  // Rows the table will actually draw, in whichever of the two branches it
  // takes. Recomputed as subtrees are collapsed, so the button's count and the
  // window it governs always describe the same table.
  const tableRowCount =
    structureRows.length > 0 ? visibleStructureRows.length : chronologicalResults.length

  // Dates whose diagnostics object to them, worst first.
  const flaggedDates = (analysis.results || [])
    .map((r, idx) => ({
      name: r.sample_name || `Sample ${idx + 1}`,
      concern: concernFor(r, measurementBySampleId.get(r.sample_id)?.outlier_prior),
    }))
    .filter((f): f is { name: string; concern: Concern } => f.concern !== null)
    .sort((a, b) => (a.concern.level === b.concern.level ? 0 : a.concern.level === 'suspect' ? -1 : 1))

  /**
   * A date's laboratory measurement as the Curve view plots it: the
   * radiocarbon age it can be read against a DRAWN curve at.
   *
   * For a marine date that is not the number on the lab report. The curve
   * models only the global average reservoir effect, so a marine sample of
   * calendar age t is expected to measure curve(t) + Delta-R -- subtracting the
   * local offset is the same statement, and is what the calibration itself
   * does. Its uncertainty joins in quadrature, so a poorly known offset widens
   * the measurement rather than only moving it, as it widened the posterior.
   *
   * undefined rather than a guess for a date with no usable age; the plot
   * counts those out and says so instead of placing them at zero.
   */
  const seriesMeasurement = (sampleId: string): SeriesMeasurement | undefined => {
    const m = measurementBySampleId.get(sampleId)
    if (!m || !Number.isFinite(m.age_bp)) return undefined
    const deltaR = m.delta_r ?? 0
    const deltaRError = m.delta_r_error ?? 0
    const stated = m.error_1sigma ?? 0
    const error = Math.sqrt(stated * stated + deltaRError * deltaRError)
    return {
      ageBp: m.age_bp - deltaR,
      error1Sigma: error > 0 ? error : undefined,
      curveId: m.curve_id,
      reservoirCorrected: deltaR !== 0 || deltaRError !== 0,
    }
  }

  // The plots, in the same order: walking structureRows puts the sequence on
  // screen in the model's own order, oldest first, and lets each plot say
  // where it sits.
  //
  // Boundaries are plotted alongside the dates, in their place between the
  // phases they separate -- a boundary is usually the thing the analysis was
  // built to estimate, and reading it against the dates either side is the
  // point. A date the structure omits is appended rather than dropped.
  const plotOrder: PlottableSeries[] = []
  const plotted = new Set<string>()
  for (const row of structureRows) {
    if (row.kind === 'date' && row.sampleId) {
      const result = resultBySampleId.get(row.sampleId)
      if (!result) continue
      plotOrder.push({
        key: `date-${row.sampleId}`,
        kind: 'date',
        title: result.sample_name || 'Date',
        path: row.path,
        groupKind: row.elementId ? kindByElementId.get(row.elementId) : undefined,
        distribution: result.probability_distribution,
        bands: bandsFor(`date-${row.sampleId}`, result.probability_distribution),
        priorDistribution: result.unmodelled_distribution ?? undefined,
        measurement: seriesMeasurement(row.sampleId),
        pointEstimates: {
          mean: result.posterior_mean,
          median: result.posterior_median,
          std: result.posterior_std,
        },
      })
      plotted.add(row.sampleId)
    } else if (row.kind === 'boundary' && row.posterior) {
      const p = row.posterior
      plotOrder.push({
        key: `boundary-${row.elementId}`,
        kind: 'boundary',
        title: row.name,
        path: row.path,
        distribution: p.probability_distribution,
        bands: bandsFor(`boundary-${row.elementId}`, p.probability_distribution),
        pointEstimates: {
          mean: p.posterior_mean,
          median: p.posterior_median,
          std: p.posterior_std,
        },
      })
    }
  }
  for (const result of chronologicalResults) {
    if (plotted.has(result.sample_id)) continue
    plotOrder.push({
      key: `date-${result.sample_id}`,
      kind: 'date',
      title: result.sample_name || 'Date',
      path: null,
      distribution: result.probability_distribution,
      bands: bandsFor(`date-${result.sample_id}`, result.probability_distribution),
      priorDistribution: result.unmodelled_distribution ?? undefined,
      measurement: seriesMeasurement(result.sample_id),
      pointEstimates: {
        mean: result.posterior_mean,
        median: result.posterior_median,
        std: result.posterior_std,
      },
    })
  }
  const boundaryPlotCount = plotOrder.filter((s) => s.kind === 'boundary').length
  // The page's primary reading: the transitions this model was built to
  // estimate, a sequence's outermost Boundaries being the answer.
  //
  // Empty for a calibration, where dates with no relationship asserted between
  // them have no modelled transition and a headline span would be a claim the
  // analysis does not make. There the table is the answer.
  const headlineBoundaries = structureRows.filter(
    (row) => row.kind === 'boundary' && row.posterior
  )
  const primaryReading =
    headlineBoundaries.length >= 2
      ? [headlineBoundaries[0], headlineBoundaries[headlineBoundaries.length - 1]]
      : []

  const visiblePlots = showBoundaryPlots
    ? plotOrder
    : plotOrder.filter((s) => s.kind !== 'boundary')

  // Which solver produced every number on the page. It qualifies all of them
  // rather than any one, so it sits with the summary chips. Split into a quiet
  // chip for the two good outcomes and an alert for the one worth a paragraph,
  // sampled-and-not-converged. Only one of the two is ever set.
  //
  // How the sampling was done, for either: "over 54,000 passes" on one chain,
  // "over 4 chains and 200,000 passes (R̂ 1.00)" when several were pooled --
  // the chain count and R̂ being what tells a reader the answer was checked
  // against independent starts rather than one run's own second half.
  const chainCount = analysis.solver?.chains ?? 1
  const passes = analysis.solver?.sweeps?.toLocaleString()
  const rhat =
    typeof analysis.solver?.rhat === 'number' ? ` (R̂ ${analysis.solver.rhat.toFixed(2)})` : ''
  const sampling =
    chainCount > 1 ? `${chainCount} chains and ${passes} passes${rhat}` : `${passes} passes`
  let solverChip: JSX.Element | null = null
  let solverAlert: JSX.Element | null = null
  if (isBayesian && analysis.status !== 'failed') {
    if (!analysis.solver) {
      solverChip = (
        <Tooltip title="This analysis was run before the solver was recorded. Re-run it to find out which method its model uses.">
          <Chip variant="outlined" label="Solver not recorded" />
        </Tooltip>
      )
    } else if (analysis.solver.method === 'exact') {
      solverChip = (
        // describeChild: without it MUI makes the tooltip the chip's NAME, so
        // the visible label is never announced and voice control has nothing
        // to target. As a description it is additive.
        <Tooltip
          describeChild
          title={
            'The posterior was integrated exactly on a calendar grid -- no sampler, ' +
            'so nothing to converge and no calibration lobe to miss.' +
            (analysis.solver.implicit_boundaries
              ? ` ${analysis.solver.implicit_boundaries} implied transition(s) were supplied to make the model solvable this way.`
              : '')
          }
        >
          <Chip variant="outlined" color="success" label="Exact solver" />
        </Tooltip>
      )
    } else if (analysis.solver.method === 'wiggle_match') {
      solverChip = (
        <Tooltip
          title={
            'A wiggle match has one free parameter -- the calendar age at the ' +
            'series reference point -- so it was solved in closed form. There ' +
            'is no sampler, nothing to converge, and no calibration lobe to miss.'
          }
        >
          <Chip variant="outlined" color="success" label="Wiggle match" />
        </Tooltip>
      )
    } else if (analysis.solver.method === 'smc') {
      solverAlert = (
        <Alert severity="warning">
          <Typography variant="subtitle2" gutterBottom>
            Computed by the old SMC sampler
          </Typography>
          <Typography variant="body2">
            That sampler mis-weighted the separate calendar ranges a wiggly
            calibration curve produces once stratigraphic ordering was involved,
            and has since been replaced. Re-run this analysis to recompute it.
          </Typography>
        </Alert>
      )
    } else if (analysis.solver.converged === false) {
      solverAlert = (
        <Alert severity="warning">
          <Typography variant="subtitle2" gutterBottom>
            Sampled, but not fully converged
          </Typography>
          {analysis.solver.exact_needs_boundaries ? (
            <Typography variant="body2" sx={{ mb: 1 }}>
              Integrating this model exactly would mean supplying{' '}
              {analysis.solver.exact_needs_boundaries === 1
                ? 'a transition'
                : `${analysis.solver.exact_needs_boundaries} transitions`}{' '}
              that its ordering only implies, which changes the model rather than
              restating it, so it was sampled as written instead.
            </Typography>
          ) : analysis.solver.reason ? (
            <Typography variant="body2" sx={{ mb: 1 }}>
              This model cannot be solved exactly because {analysis.solver.reason}.
            </Typography>
          ) : (
            <Typography variant="body2" sx={{ mb: 1 }}>
              The MH/Gibbs sampler was chosen for this analysis. This model can
              also be integrated exactly, which needs no sampling at all — switch
              the solver to Automatic if you don’t need OxCal’s method
              specifically.
            </Typography>
          )}
          <Typography variant="body2">
            The sampler reached{' '}
            {/* One decimal place, not none: a run stopping at 0.945 rounds
                to "95%", contradicting the 95% target it just missed. */}
            {analysis.solver.convergence !== undefined
              ? `${(analysis.solver.convergence * 100).toFixed(1)}%`
              : 'less than 95%'}{' '}
            convergence after {sampling}
            {typeof analysis.solver.rhat === 'number' &&
            typeof analysis.solver.rhat_target === 'number' &&
            analysis.solver.rhat > analysis.solver.rhat_target
              ? `, and its chains had not come into agreement: R̂ ${analysis.solver.rhat.toFixed(2)} against the ${analysis.solver.rhat_target.toFixed(2)} wanted, which usually means a date whose calibration has two separated ranges and different chains settled in different ones.`
              : ', short of the 95% usually wanted.'}{' '}
            The ranges below are probably a few years out rather than wrong
            {analysis.solver.reason
              ? ', but writing in the Boundary the model implies would let it be solved exactly without changing anything.'
              : '.'}
          </Typography>
        </Alert>
      )
    } else {
      // A run the analyst asked for is labelled as their choice, not a
      // fallback: they may have picked OxCal's method to reproduce a published
      // analysis or to cross-check the exact solver.
      solverChip = (
        // describeChild: without it MUI makes the tooltip the chip's NAME, so
        // the visible label is never announced and voice control has nothing
        // to target. As a description it is additive.
        <Tooltip
          describeChild
          title={
            (analysis.solver.exact_needs_boundaries
              ? `This model was sampled with the Metropolis-Hastings/Gibbs scheme. Integrating this model exactly would mean supplying ${
                  analysis.solver.exact_needs_boundaries === 1
                    ? 'a boundary'
                    : analysis.solver.exact_needs_boundaries + ' boundaries'
                } that its ordering only implies. That would be a change to the model, splitting one phase-span in two and measurably moving the answer. Choose the exact solver if you want the missing boundaries supplied anyway; note that it will then be answering a slightly different model. `
              : analysis.solver.reason
              ? `This model was sampled with the Metropolis-Hastings/Gibbs scheme. It cannot be integrated exactly — ${analysis.solver.reason}. `
              : 'The Metropolis-Hastings/Gibbs sampler was chosen for this analysis. This model could also have been integrated exactly; the two agree to well under a year on the models where both can be run. ') +
            `It converged to ${
              analysis.solver.convergence !== undefined
                ? (analysis.solver.convergence * 100).toFixed(1) + '%'
                : 'the 95% target'
            } over ${sampling}.`
          }
        >
          <Chip
            variant="outlined"
            color="success"
            label={`${
              analysis.solver.requested === 'gibbs' ? 'MH/Gibbs (chosen)' : 'MH/Gibbs (auto)'
            } — ${
              analysis.solver.convergence !== undefined
                ? (analysis.solver.convergence * 100).toFixed(0) + '% converged'
                : 'converged'
            }`}
          />
        </Tooltip>
      )
    }
  }

  return (
    // component="main": otherwise there is no way past the AppBar for anyone
    // navigating by landmark.
    <Container component="main" maxWidth="lg" sx={{ py: 4 }}>
      <Paper sx={{ p: 3, mb: 3 }}>
        {/* Identity and finding on the band, the controls that act on them on
            the working surface beneath it -- the masthead the model editor and
            the Stratigraphy diagram already introduce themselves with, so all
            three pages are read the same way. It replaced six stacked rows in
            which the thing this page exists to deliver, the modelled span, was
            the last of them and the smallest. */}
        <GuideBand>
          <Typography variant="body2" sx={{ mb: 0.5 }}>
            {analysis.project_id ? (
              /* primary.dark, not main: the blue was darkened to clear AA
                 against white (5.00:1) and against the canvas (4.65:1), the
                 two surfaces it was measured on. The muted band is a third and
                 darker one, where it lands at 4.31:1 -- under AA at this size
                 and weight. The theme's own dark step is the same hue and
                 measures 6.02:1 there, so nothing new is invented. */
              <Link
                component={RouterLink}
                to={`/projects/${analysis.project_id}`}
                underline="hover"
                sx={{ fontWeight: 600, color: 'primary.dark' }}
              >
                {analysis.project_name}
              </Link>
            ) : (
              <Box component="span" sx={{ color: 'text.secondary' }}>
                Unassigned
              </Box>
            )}
          </Typography>

          {/* h2's scale (2rem/600), not h1's (2.5rem): the band carries the
              finding as well as the name now, and an identity set larger than
              the answer under it is the hierarchy this layout exists to
              replace. Still the page's <h1> element -- the size moved, not the
              rank. */}
          <Typography variant="h2" component="h1">
            {analysis.name}
          </Typography>

          {/* The finding, at the same scale as the name: what this analysis IS
              and what it FOUND, said once each. `primaryReading` is the first
              and last Boundary of the model by construction, and is empty for
              a calibration, where there is no modelled transition to report
              and a headline span would be a claim the analysis does not make. */}
          {primaryReading.length === 2 && (
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={{ xs: 1.5, sm: 5 }}
              sx={{ mt: 2 }}
            >
              {primaryReading.map((row, idx) => {
                const envelope = posteriorEnvelope(
                  row.posterior!.probability_distribution,
                  confidence,
                  true
                )
                if (!envelope) return null
                const range = formatCalendarRange(envelope[0], envelope[1], dateFormat)
                return (
                  <Box key={row.elementId ?? row.name}>
                    {/* The page says what these two ARE; the analyst's own name
                        for them is in the table. */}
                    <Typography variant="body2" color="textSecondary">
                      {idx === 0 ? 'Sequence begins' : 'Sequence ends'}
                    </Typography>
                    <Typography
                      component="p"
                      sx={{
                        fontSize: (theme) => theme.typography.h2.fontSize,
                        fontWeight: 600,
                        fontVariantNumeric: 'tabular-nums',
                        lineHeight: 1.15,
                      }}
                    >
                      {range}
                      <CopyCitation
                        text={citationLine(
                          row.name,
                          range,
                          CONFIDENCE_LABEL,
                          provenanceForSample(row.sampleId)
                        )}
                      />
                    </Typography>
                  </Box>
                )
              })}
            </Stack>
          )}

          <Typography variant="body2" color="textSecondary" sx={{ mt: 2 }}>
            {[
              primaryReading.length === 2 ? `${CONFIDENCE_LABEL} span` : null,
              analysisTypeLabel(analysis.analysis_type),
              `${analysis.measurements_count} sample${analysis.measurements_count === 1 ? '' : 's'}`,
              ...analysisProvenance,
            ]
              .filter(Boolean)
              .join(' · ')}
            {' · '}
            {/* Date on the line, the full timestamp on hover: the minute a run
                started matters when two runs of one model are being told
                apart, and never otherwise. */}
            <Box
              component="span"
              title={new Date(analysis.created_at).toLocaleString()}
            >
              {new Date(analysis.created_at).toLocaleDateString(undefined, {
                day: 'numeric',
                month: 'short',
                year: 'numeric',
              })}
            </Box>
          </Typography>
        </GuideBand>

        {/* Below the rule: what state this run is in, and what can be done
            about it. The chips sit here rather than on the band for a reason
            beyond composition -- MUI's status colours are tuned against white,
            and on the muted band the success green measures 4.42:1, under AA.
            On the paper surface they are the colours they were built to be.
            
            What stays a chip at all is what is worth spending colour on: which
            solver produced every number above, and any state that is NOT the
            ordinary one. `completed` and the sample count restated what the
            table below already proves; a failed, queued or partial run is
            exactly the case a reader must not miss, and keeps its full chip.

            Icons, for the actions. Each has its name for the
            tooltip and for assistive technology -- an icon button with no
            accessible name announces nothing and gives voice control no target
            to say. */}
        <Box
          sx={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 2,
            rowGap: 1.5,
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          {(solverChip ||
            analysis.status !== 'completed' ||
            analysis.results.length !== analysis.measurements_count) && (
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
              {solverChip}
              {analysis.status !== 'completed' && (
                <Chip
                  label={analysis.status}
                  color={analysis.status === 'failed' ? 'error' : 'warning'}
                />
              )}
              {analysis.results.length !== analysis.measurements_count && (
                <Chip
                  color="warning"
                  label={`${analysis.results.length} of ${analysis.measurements_count} have results`}
                />
              )}
            </Stack>
          )}
          
          {/* Always the right end of the row, whether or not there are chips
              to sit opposite: space-between alone would slide the actions to
              the left edge on a plain calibration, which has no solver chip
              and nothing else to report, and the actions would then be in a
              different place on two pages of the same shape. The negative
              right margin puts the last glyph on the band's own text edge
              rather than a padding's width inside it -- an IconButton's
              padding is its touch target, not its optical edge. It tracks
              size="small"'s 5px, so the two move together. */}
          <Stack
            direction="row"
            spacing={0.5}
            alignItems="center"
            sx={{ ml: 'auto', mr: '-5px', my: '-5px' }}
          >
            <Tooltip title="Edit">
              <IconButton
                size="small"
                aria-label="Edit analysis"
                onClick={() => navigate(`/analysis/${analysis.id}/edit`)}
              >
                <EditIcon />
              </IconButton>
            </Tooltip>
            {/* A disabled button dispatches no pointer events, so the tooltip
                would disappear exactly while the copy is running. The span is
                what keeps it. */}
            <Tooltip title={duplicating ? 'Copying…' : 'Duplicate'}>
              <span>
                <IconButton
                  size="small"
                  aria-label="Duplicate analysis"
                  disabled={duplicating}
                  onClick={handleDuplicate}
                >
                  {duplicating ? <CircularProgress size={18} /> : <ContentCopyIcon />}
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Re-run">
              <IconButton
                size="small"
                aria-label="Re-run analysis"
                onClick={() => setRerunOpen(true)}
              >
                <ReplayIcon />
              </IconButton>
            </Tooltip>
            {/* The one destructive action, held off from the three that are
                not by a rule. The rule does the separating, not the colour:
                red at rest reads as an alarm on a page where nothing is wrong,
                so this matches every other delete in the app -- ink until it
                is pointed at or focused, error red then. */}
            <Divider orientation="vertical" flexItem sx={{ mx: 0.75, my: 0.5 }} />
            <Tooltip title="Delete">
              <IconButton
                size="small"
                aria-label="Delete analysis"
                onClick={() => setDeleteOpen(true)}
                sx={{ '&:hover, &:focus-visible': { color: 'error.main' } }}
              >
                <DeleteIcon />
              </IconButton>
            </Tooltip>
          </Stack>
        </Box>
      </Paper>

      {/* A failed run, with the reason -- one way to fail is a deliberate
          choice (requiring the exact solver on a model that cannot support
          it), where the reason names exactly what to change. */}
      {/* A run in progress, with what it is doing and a way to stop it. Sits
          above the failure alert because a re-run of a failed analysis should
          lead with what is happening now, not with why the last one didn't. */}
      {jobActive && job && (
        <JobProgress job={job} onCancel={handleCancelRun} cancelling={cancelling} />
      )}

      {analysis.status === 'failed' && !jobActive && (
        <Alert severity="error" sx={{ mb: 3 }}>
          <Typography variant="subtitle2" gutterBottom>
            This analysis did not complete
          </Typography>
          <Typography variant="body2">
            {analysis.error_message || 'No reason was recorded. Re-running it may report one.'}
          </Typography>
        </Alert>
      )}

      {/* The quiet-chip outcomes (solverChip) already rendered first in the
          summary chip row above -- only a warning worth its own paragraph
          lands here. */}
      {solverAlert && <Box sx={{ mb: 3 }}>{solverAlert}</Box>}

      {/* Boundaries are not listed separately: they sit in the results table
          in their place in the structure, between the phases they separate,
          which is where a shared transition is meaningful. */}

      {boundaryRows.length === 0 && contextsWithModeledSpan.length > 0 && (
        <Paper sx={{ p: 3, mb: 3 }}>
          <Typography variant="h6" component="h2" gutterBottom>
            Context Spans
          </Typography>
          <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
            Modelled start and end date of each context (OxCal-style Boundary parameters) --
            these may extend beyond the oldest/youngest dated sample, since the dated samples
            are treated as a partial sample of the context's true duration.
          </Typography>
          <TableContainer>
            <Table size="small">
              <caption style={visuallyHidden}>
                Modelled start and end of each context in this analysis.
              </caption>
              <TableHead>
                {/* The point-estimate columns follow the results table's own
                    Columns menu rather than carrying a second control: they
                    are the same two quantities about the same run, and two
                    pickers for one decision is how the page ends up showing
                    a mean in one table and not the other. */}
                <TableRow sx={{ backgroundColor: 'background.muted' }}>
                  <TableCell>Context</TableCell>
                  {showMean && <TableCell align="right">Start Mean</TableCell>}
                  {showMedian && <TableCell align="right">Start Median</TableCell>}
                  <TableCell align="right">Modelled Start ({CONFIDENCE_LABEL} span)</TableCell>
                  {showMean && <TableCell align="right">End Mean</TableCell>}
                  {showMedian && <TableCell align="right">End Median</TableCell>}
                  <TableCell align="right">Modelled End ({CONFIDENCE_LABEL} span)</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {contextsWithModeledSpan.map((g) => {
                  const startEnvelope = posteriorEnvelope(g.modeled_start!.probability_distribution, confidence, true)
                  const endEnvelope = posteriorEnvelope(g.modeled_end!.probability_distribution, confidence, true)
                  return (
                    <TableRow key={g.id}>
                      <TableCell>{g.name}</TableCell>
                      {showMean && (
                        <TableCell align="right">
                          {formatCalendarAge(g.modeled_start!.posterior_mean, dateFormat)}
                        </TableCell>
                      )}
                      {showMedian && (
                        <TableCell align="right">
                          {formatCalendarAge(g.modeled_start!.posterior_median, dateFormat)}
                        </TableCell>
                      )}
                      <TableCell align="right">
                        {startEnvelope ? formatCalendarRange(startEnvelope[0], startEnvelope[1], dateFormat) : 'N/A'}
                      </TableCell>
                      {showMean && (
                        <TableCell align="right">
                          {formatCalendarAge(g.modeled_end!.posterior_mean, dateFormat)}
                        </TableCell>
                      )}
                      {showMedian && (
                        <TableCell align="right">
                          {formatCalendarAge(g.modeled_end!.posterior_median, dateFormat)}
                        </TableCell>
                      )}
                      <TableCell align="right">
                        {endEnvelope ? formatCalendarRange(endEnvelope[0], endEnvelope[1], dateFormat) : 'N/A'}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      )}

      {/* Results Table */}
      <Paper sx={{ p: 3, mb: 3 }}>
        {/* The card's masthead: what this is, and the two ways to take it
            away. Everything that changes how it READS is in the strip under
            the rule, so nothing in this row is a decision about the table. */}
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: 2,
          }}
        >
          <Typography variant="h6" component="h2">
            Results Summary
          </Typography>
          <Stack direction="row" spacing={1}>
            <Button startIcon={<DownloadIcon />} onClick={handleExportCSV} variant="outlined">
              Export CSV
            </Button>
            <Button
              startIcon={
                exportingPdf ? <CircularProgress size={16} color="inherit" /> : <DownloadIcon />
              }
              onClick={handleExportPDF}
              variant="outlined"
              disabled={exportingPdf}
            >
              {exportingPdf ? 'Building PDF…' : 'Export PDF'}
            </Button>
          </Stack>
        </Box>

        <Divider sx={{ my: 2 }} />

        {/* Two kinds of control, and the rule above plus the gap between them
            are what say so. On the left, the reporting convention: both
            change what every number on the page MEANS. On the right, what is
            drawn: neither changes a figure.

            Date format sits here rather than up in the masthead because it
            changes how every number reads and nothing about what was
            computed, which is exactly what HPD confidence does. In the
            masthead it was the widest thing in a row of actions, where it
            read as a fourth thing to decide on arrival.

            Show Mean and Show Median used to sit here as loose checkboxes,
            which put "which columns exist" in the same row as "what the
            numbers mean" and left the other four optional columns
            unreachable. They are entries in the Columns menu now, alongside
            the ones that had no control at all. */}
        <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 2, mb: 2.5 }}>
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ sm: 'center' }}>
            <FormControl size="small" sx={{ minWidth: 200 }}>
              {/* id + labelId, not just <InputLabel>: without the pair MUI
                  renders a <label> with no `for` and a role="combobox" with
                  no accessible name, so this announced only its current
                  value -- "cal BP (years before 1950), combobox" -- with
                  nothing saying what it governs, and voice control had no
                  target to say. */}
              <InputLabel id="date-format-label">Date Format</InputLabel>
              <Select
                labelId="date-format-label"
                id="date-format"
                value={dateFormat}
                label="Date Format"
                onChange={handleDateFormatChange}
              >
                {DATE_FORMAT_OPTIONS.map((option) => (
                  <MenuItem key={option.value} value={option.value}>
                    {option.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 140 }}>
              {/* The most consequential control on the page: it sets the
                  level every figure here is reported at. It announced
                  "95.4%, combobox" and nothing else. */}
              <InputLabel id="hpd-confidence-label">HPD Confidence</InputLabel>
              <Select
                labelId="hpd-confidence-label"
                id="hpd-confidence"
                value={confidence}
                label="HPD Confidence"
                onChange={(e) => setConfidence(Number(e.target.value))}
              >
                {CONFIDENCE_OPTIONS.map((opt) => (
                  <MenuItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Stack>

          <Box sx={{ flex: 1 }} />

          <Stack direction="row" spacing={1} alignItems="center">
            {/* The scroll window's control. Only offered when there are
                enough rows for 70vh to actually be cutting some off -- on a
                short table the button would toggle nothing visible -- and
                only from md up, since the cap itself is desktop-only: below
                md the table is already whole and a nested vertical scroller
                would fight the page scroll on touch.

                Here rather than in a row of its own above the table: it
                shapes how the table is presented and changes no figure in
                it, which is the same thing Columns does. */}
            {tableRowCount > TABLE_WINDOW_ROWS && (
              <Button
                sx={{ display: { xs: 'none', md: 'inline-flex' } }}
                aria-expanded={tableExpanded}
                aria-controls="results-table"
                onClick={() => setTableExpanded((open) => !open)}
                startIcon={tableExpanded ? <UnfoldLessIcon /> : <UnfoldMoreIcon />}
              >
                {tableExpanded ? 'Collapse to a window' : `Show all ${tableRowCount} rows`}
              </Button>
            )}
            <Button
              variant="outlined"
              startIcon={<ViewColumnIcon />}
              aria-haspopup="true"
              onClick={(e) => setColumnsAnchor(e.currentTarget)}
            >
              Columns
            </Button>
          </Stack>
        </Box>

        <Menu
          anchorEl={columnsAnchor}
          open={!!columnsAnchor}
          onClose={() => setColumnsAnchor(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
          transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        >
          <Typography
            variant="caption"
            color="text.secondary"
            component="li"
            sx={{ display: 'block', px: 2, pt: 0.5, pb: 1 }}
          >
            Sample and the {CONFIDENCE_LABEL} span always show.
          </Typography>
          {optionalColumns.map((column) => {
            const shown = columnVisible(columnOverrides, column.id, column.auto)
            return (
              <MenuItem
                key={column.id}
                role="menuitemcheckbox"
                aria-checked={shown}
                // The menu stays open: choosing columns is usually choosing
                // several, and reopening between each is the errand.
                onClick={() => toggleColumn(column)}
              >
                <ListItemIcon>
                  <Checkbox edge="start" size="small" checked={shown} tabIndex={-1} disableRipple />
                </ListItemIcon>
                <ListItemText
                  primary={column.label}
                  // Said plainly, so turning on a column this run never
                  // produced reads as empty rather than as broken.
                  secondary={column.populated ? undefined : column.emptyNote}
                />
              </MenuItem>
            )
          })}
          <Divider />
          <MenuItem onClick={resetColumns} disabled={Object.keys(columnOverrides).length === 0}>
            <ListItemText inset>Reset to defaults</ListItemText>
          </MenuItem>
        </Menu>

        {/* Said once at the top as well as marked on each row: a long table
            can hide a flagged date below the fold, and the point of the
            diagnostics is that nobody should have to go looking.

            Names only. The reason each one was flagged is on its own chip in
            the table, where the reader is looking when they ask; printing
            both meant the same sentence twice, in two places that could
            disagree once either was edited. */}
        {isBayesian && flaggedDates.length > 0 && (
          <Alert
            severity={flaggedDates.some((f) => f.concern.level === 'suspect') ? 'error' : 'warning'}
            sx={{ mb: 2 }}
            action={
              <Button
                size="small"
                startIcon={<EditIcon />}
                onClick={() => navigate(`/analysis/${analysisId}/edit`)}
              >
                Edit analysis
              </Button>
            }
          >
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {flaggedDates.length} of {analysis.results.length} dates look suspect
            </Typography>
            <Typography variant="body2">
              {flaggedDates.map((f) => f.name).join(' · ')}
            </Typography>
            {/* What "Edit analysis" is for. The button was the only exit from
                this banner and said nothing about what to change once it was
                pressed, which left the reader with a verdict and no move. */}
            <Typography variant="body2" sx={{ mt: 0.5 }}>
              Suspect dates highlighted in the table below, hover over the chip to see the reason. Edit
              analysis allows you to change the model, remove the date, or apply an outlier prior.
            </Typography>
          </Alert>
        )}

        {/* maxHeight + a sticky header row: <thead> was position:static and
            the container had no height, so only the name column was sticky and
            only horizontally. On the 63-date model 1,532px of a 2,432px table
            scrolled past with no column headings on screen -- the reader was
            comparing two adjacent right-aligned numeric columns with nothing
            saying which was which. Capped at 70vh so the page still scrolls as
            one document on short models, and uncapped on request by the
            control above. */}
        <TableContainer
          id="results-table"
          sx={{
            ...SCROLL_HINT_SX,
            maxHeight: tableExpanded ? 'none' : { xs: 'none', md: '70vh' },
          }}
        >
          {/* tabular-nums so every numeric column -- ages, percentages,
              agreement/outlier diagnostics -- lines up digit-for-digit down
              the column instead of drifting with each digit's own width.
              Inherited onto every cell including the header labels and the
              Suspect/Check/Assigned chips, which is harmless: none of that
              text has digits whose alignment matters. */}
          {/* stickyHeader, with the container's maxHeight above: MUI puts
              position:sticky; top:0 on every head cell, which is what the name
              column was already doing on `left` alone. The cells need their own
              opaque background -- a sticky cell is painted over by whatever
              scrolls beneath it. */}
          {/* Full width, with every column nowrap.
              
              The name column used to take 396px of 1104 (36%) for codes like
              "WK50104", because it was the only column that could wrap and so
              the only one that could absorb the table's slack. Making it
              nowrap too means no column can grow by wrapping, and the browser
              spreads the surplus across all of them instead of pooling it in
              one 400px gap. Content-width was tried and reverted: it fixed the
              gap by leaving a quarter of the card empty, which is a worse
              trade on a card this wide. */}
          <Table
            stickyHeader
            size="small"
            sx={{
              fontVariantNumeric: 'tabular-nums',
              // Vertical stick is a desktop affordance: it needs a scroll
              // container, and a nested vertical scroller fights the page
              // scroll on touch. `top: auto` below md leaves the header in
              // flow while KEEPING position:sticky, so the table's horizontal
              // pin on the name column still works. Same when the window is
              // lifted: the container is then as tall as the table, so there
              // is no scrollport left for top:0 to pin against.
              '& thead th': {
                backgroundColor: MUTED,
                top: tableExpanded ? 'auto' : { xs: 'auto', md: 0 },
              },
              // The name column takes what it needs and no more; the numeric
              // columns are nowrap, so nothing else can claim the slack.
              '& tbody td:first-of-type, & thead th:first-of-type': { whiteSpace: 'nowrap' },
            }}
          >
            <caption style={visuallyHidden}>
              {isBayesian ? 'Modelled' : 'Calibrated'} calendar age for every date in this
              analysis, oldest first, at {CONFIDENCE_LABEL} HPD.
            </caption>
            <TableHead>
              <TableRow>
                {/* width: 1% is the shrink-to-content idiom for an auto-layout
                    table: the browser cannot honour it, so the column falls to
                    its min-content width and every other column shares the
                    surplus. Without it this column absorbed all of it -- 36% of
                    the table for codes like "WK50104", and 220px of nothing
                    between a date and its own result. It settles at 27%, which
                    is the deepest-indented name plus its type label, and the
                    gap halves to 124px. */}
                <TableCell sx={{ ...stickyNameCell(MUTED), top: 0, zIndex: 4, width: '1%' }}>
                  {structureRows.length > 0 ? 'Structure / Sample' : 'Sample'}
                </TableCell>
                {/* What was measured, left of everything derived from it. */}
                {showAge && <TableCell align="right">{labelOf('age')}</TableCell>}
                {showCurve && <TableCell>{labelOf('curve')}</TableCell>}
                {showUnmodelled && (
                  <TableCell align="right">
                    <RangeColumnHeader
                      label={<>Unmodelled {CONFIDENCE_LABEL} span</>}
                      expanded={unmodelledExpanded}
                      onToggle={() => setUnmodelledExpanded((v) => !v)}
                    />
                  </TableCell>
                )}
                {/* The estimate and its dispersion, together or not at all. */}
                {showMean && (
                  <TableCell align="right">
                    {isBayesian ? 'Posterior Mean' : 'Calibrated Mean'}
                  </TableCell>
                )}
                {showMean && <TableCell align="right">σ (yr)</TableCell>}
                {showMedian && <TableCell align="right">{labelOf('median')}</TableCell>}
                <TableCell align="right">
                  {/* "Span", not "HPD Range". This column reports the OUTER
                      BOUNDS of the highest-density region -- its earliest and
                      latest calendar age -- which is one number a reader can
                      scan down. Where the posterior is multimodal the region
                      is genuinely several disjoint ranges, and calling this
                      single pair an HPD claimed probability in the gaps
                      between them. The full breakdown is in the plot and in
                      every export; the note under this table says so. */}
                  <RangeColumnHeader
                    label={
                      <>
                        {isBayesian ? 'Posterior' : 'Calibrated'} {CONFIDENCE_LABEL} span
                      </>
                    }
                    expanded={posteriorExpanded}
                    onToggle={() => setPosteriorExpanded((v) => !v)}
                  />
                </TableCell>
                {/* No help control beside the label: it opened the same
                    paragraph the key under this table already prints without
                    a hover, and a reader who had read one had no way to know
                    the other said nothing new. */}
                {showDiagnostics && <TableCell align="right">Diagnostics</TableCell>}
              </TableRow>
            </TableHead>
            <TableBody>
              {structureRows.length > 0
                ? visibleStructureRows.map((row, idx) => (
                    <StructureTableRow
                      // sampleId first, elementId only as a fallback: a date
                      // row's elementId is the PHASE it sits in, which every
                      // date there shares, so preferring it would give them
                      // one key and let React drop or repeat a date. Element
                      // rows carry sampleId: null and still key on elementId.
                      key={`${row.kind}-${row.sampleId ?? row.elementId ?? idx}`}
                      row={row}
                      result={row.sampleId ? resultBySampleId.get(row.sampleId) : undefined}
                      measurement={
                        row.sampleId ? measurementBySampleId.get(row.sampleId) : undefined
                      }
                      curveName={
                        row.sampleId
                          ? curveNameFor(measurementBySampleId.get(row.sampleId)?.curve_id)
                          : null
                      }
                      isBayesian={isBayesian}
                      showAge={showAge}
                      showCurve={showCurve}
                      showUnmodelled={showUnmodelled}
                      showMean={showMean}
                      showMedian={showMedian}
                      showDiagnostics={showDiagnostics}
                      confidence={confidence}
                      dateFormat={dateFormat}
                      unmodelledExpanded={unmodelledExpanded}
                      posteriorExpanded={posteriorExpanded}
                      // A date row's elementId is its PARENT phase's id, so
                      // these must also check row.kind -- otherwise every date
                      // would inherit its phase's collapsibility and grow a
                      // control that toggles its parent.
                      isCollapsible={
                        (row.kind === 'sequence' || row.kind === 'phase') &&
                        !!row.elementId &&
                        collapsibleElementIds.has(row.elementId)
                      }
                      isCollapsed={
                        (row.kind === 'sequence' || row.kind === 'phase') &&
                        !!row.elementId &&
                        collapsedElements.has(row.elementId)
                      }
                      onToggleCollapse={
                        (row.kind === 'sequence' || row.kind === 'phase') && row.elementId
                          ? () => toggleCollapsed(row.elementId!)
                          : undefined
                      }
                      thinNote={thinNoteFor(row)}
                    />
                  ))
                : chronologicalResults.map((result, idx) => {
                    const unmodelledRanges = isBayesian
                      ? unmodelledRangesFor(result, confidence)
                      : []
                    const modelledEnvelope = posteriorEnvelope(
                      result.probability_distribution, confidence, isBayesian)
                    const modelledRanges = posteriorExpanded
                      ? posteriorRanges(result.probability_distribution, confidence, isBayesian)
                      : []
                    return (
                      <TableRow key={result.sample_id}>
                        <TableCell
                          title={result.sample_name || `Sample ${idx + 1}`}
                          sx={stickyNameCell(PAPER)}
                        >
                          {result.sample_name || `Sample ${idx + 1}`}
                        </TableCell>
                        {showAge && (
                          <TableCell align="right" sx={NUMERIC_CELL_SX}>
                            {measuredAge(measurementBySampleId.get(result.sample_id))}
                          </TableCell>
                        )}
                        {showCurve && (
                          <TableCell sx={NUMERIC_CELL_SX}>
                            {curveNameFor(
                              measurementBySampleId.get(result.sample_id)?.curve_id
                            ) ?? ''}
                          </TableCell>
                        )}
                        {showUnmodelled && (
                          <TableCell align="right" sx={NUMERIC_CELL_SX}>
                            <RangeCell
                              ranges={unmodelledRanges}
                              span={spanOfRanges(unmodelledRanges)}
                              expanded={unmodelledExpanded}
                              dateFormat={dateFormat}
                            />
                          </TableCell>
                        )}
                        {showMean && (
                          <TableCell align="right" sx={NUMERIC_CELL_SX}>
                            {result.posterior_mean != null
                              ? formatCalendarAge(result.posterior_mean, dateFormat)
                              : 'N/A'}
                          </TableCell>
                        )}
                        {showMean && (
                          <TableCell align="right" sx={NUMERIC_CELL_SX}>
                            {result.posterior_std?.toFixed(0) ?? 'N/A'}
                          </TableCell>
                        )}
                        {showMedian && (
                          <TableCell align="right" sx={NUMERIC_CELL_SX}>
                            {result.posterior_median != null
                              ? formatCalendarAge(result.posterior_median, dateFormat)
                              : 'N/A'}
                          </TableCell>
                        )}
                        <TableCell align="right" sx={NUMERIC_CELL_SX}>
                          <RangeCell
                            ranges={modelledRanges}
                            span={modelledEnvelope}
                            expanded={posteriorExpanded}
                            dateFormat={dateFormat}
                            emphasis
                          />
                        </TableCell>
                        {/* The prior comes from the measurement, not the
                            result: it is what the analyst assigned going in,
                            and the chip's whole claim is how far the
                            posterior moved from it. */}
                        {showDiagnostics && (
                          <DiagnosticsCell
                            result={result}
                            outlierPrior={
                              measurementBySampleId.get(result.sample_id)?.outlier_prior
                            }
                            outlierModel={
                              measurementBySampleId.get(result.sample_id)?.outlier_model
                            }
                          />
                        )}
                      </TableRow>
                    )
                  })}
            </TableBody>
          </Table>
        </TableContainer>
        {/* What the span column is, and where the rest of it lives.
            Unconditional, because it applies to every analysis type and
            because it is the page's only expansion of "HPD". The column deliberately shows one pair of
            bounds rather than every disjoint range: a multimodal posterior
            can produce four, and four per row would make the table unreadable
            for the sake of a case the plot already draws correctly. */}
        <Typography variant="caption" color="textSecondary" component="p" sx={{ mt: 1.5 }}>
          <strong>{CONFIDENCE_LABEL} span</strong> is the earliest and latest calendar age of
          the {CONFIDENCE_LABEL} HPD (highest posterior density) region. Use a column’s <strong>unfold</strong> control to read the full ranges in place.
        </Typography>
        {/* The key a first-time reader needs, present without a hover: the
            two diagnostics letters, spelled out once under the table they
            label. */}
        {isBayesian && (
          <Typography variant="caption" color="textSecondary" component="p" sx={{ mt: 1.5 }}>
            <strong>A</strong> = agreement index — how far the model moved a date from the original calibration. 100 is unchanged, above 100 means it sharpened the date onto its own peak. Below 60 is considered a major shift.{' '}
            <br /><strong>O</strong> = the probability the date is an outlier.{' '}
            {/* The chips read as a verdict with no stated rule, and the rule
                is not guessable from the two numbers beside them: both chips
                compare O against the PRIOR the date was given, not against a
                fixed number. Stated here, once, next to the letters they are
                drawn from. */}
            A <strong>Suspect</strong> or <strong>Check</strong> chip marks a date whose
            outlier probability rose well above the prior it was given, or whose agreement
            fell below {POOR_AGREEMENT}; <strong>Assigned</strong> marks one that was given
            a raised outlier prior and stayed close to it.
          </Typography>
        )}
      </Paper>

      {/* A wiggle match, before the results table rather than after it.
          The table below lists every member as a date of its own, which is
          the one reading this model does NOT support -- they are one age with
          the measured gaps added -- so the panel that says so has to come
          first. */}
      {wiggleMatch && (
        <Box sx={{ mb: 3 }}>
          <WiggleMatchPanel
            name={wiggleMatch.name}
            reference={wiggleMatch.posterior}
            members={analysis.solver?.members || []}
            gapUnits={analysis.solver?.gap_units || 'rings'}
            seriesSpan={analysis.solver?.series_span ?? 0}
            combinationAgreement={analysis.solver?.combination_agreement}
            referenceSampleId={analysis.solver?.reference_sample_id}
            dateFormat={dateFormat}
          />
        </Box>
      )}

      {/* An age-depth model, likewise before the table: the band IS the
          answer, and a column of per-level posteriors answers a different
          question. */}
      {ageDepthRecords.map((record) => (
        <Box key={record.element_id} sx={{ mb: 3 }}>
          <AgeDepthPanel
            record={record}
            rigidity={(analysis.solver?.deposition || []).find(
              (d) => d.element_id === record.element_id
            )}
            dateFormat={dateFormat}
          />
        </Box>
      ))}

      {/* A wave of advance, before the table for the same reason the age-depth
          band is: the map IS the answer, and a column of per-site arrival ages
          answers a different question.

          Tiles stay off here even for a project that has opted into them --
          this page has no project loaded, and the offline basemap is the
          private default. Passing the project's setting through is the one
          thing left to do if that is ever wanted. */}
      {dispersalFit && (
        <Box sx={{ mb: 3 }}>
          <DispersalPanel fit={dispersalFit} dateFormat={dateFormat} />
        </Box>
      )}

      {/* Model Structure -- read-only view of the groups/relationships/TPQ-TAQ
          this bayesian analysis actually used, if any. Doubles as the
          "run the Harris matrix as a check" step after the fact: even
          though the constraints already fed into the MCMC run, this
          confirms visually that they were specified as intended.

          It reads after the results table because it answers the question the
          table raises -- "what ordering produced these ranges?" -- rather than
          one asked before the ranges have been seen. */}
      {analysis.analysis_type === 'bayesian' && analysis.model_tree && (
        <Paper sx={{ p: 3, mb: 3 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <AccountTreeIcon color="action" />
            <Typography variant="h6" component="h2">
              Model Structure used
            </Typography>
            <Box sx={{ flexGrow: 1 }} />
            <Button
              size="small"
              aria-expanded={structureOpen}
              aria-controls="model-structure-diagram"
              onClick={() => setStructureOpen((open) => !open)}
              startIcon={
                <KeyboardArrowDownIcon
                  sx={{
                    transform: structureOpen ? 'rotate(180deg)' : 'none',
                    transition: 'transform 160ms ease-out',
                  }}
                />
              }
            >
              {structureOpen ? 'Hide' : 'Show'} diagram
            </Button>
          </Box>
          {/* The id lives on a wrapper that stays in the DOM, since
              unmountOnExit takes the Collapse's child away exactly when the
              button says "Show" and aria-controls would point at nothing.
              unmountOnExit itself is the point of the collapse: it takes the
              reactflow canvas -- and its wheel handler -- out of the page
              entirely while closed, rather than merely hiding it. */}
          {/* A Harris matrix is a drawing of STRATIGRAPHY, and neither of
              these two orders its members that way -- one by counted rings,
              one by measured depth. Drawing them as a column of boxes would
              imply a stratigraphic reading the model is not making, so the
              panel above is the real rendering and this says so rather than
              leaving the diagram to be read as one. */}
          {(wiggleMatch || ageDepthRecords.length > 0) && (
            <Typography variant="body2" color="textSecondary" sx={{ mt: 1 }}>
              {wiggleMatch
                ? 'The dates in this model are ordered by the ring counts between them, not by stratigraphy'
                : 'The levels in this model are ordered by their depths, not by stratigraphy'}
              , so the diagram below shows only where they sit in the model. The{' '}
              {wiggleMatch ? 'match' : 'age-depth band'} above is what the model
              actually says.
            </Typography>
          )}
          <Box id="model-structure-diagram">
            <Collapse in={structureOpen} unmountOnExit>
              <Box sx={{ mt: 2 }}>
                <HarrisMatrixDiagram
                  tree={analysis.model_tree}
                  measurements={analysis.measurements || []}
                  assignments={structureAssignments}
                  dateFormat={dateFormat}
                  confidence={confidence}
                  confidenceLabel={CONFIDENCE_LABEL}
                />
              </Box>
            </Collapse>
          </Box>
          {!structureOpen && structureSummary && (
            <Typography variant="body2" color="textSecondary" sx={{ mt: 1 }}>
              {structureSummary} — expand to view the model diagram.
            </Typography>
          )}
        </Paper>
      )}

      {/* Posterior distribution plots, in the model's own reading order --
          the same order as the results table and both exports, so scrolling
          the plots walks the sequence from oldest to youngest instead of
          whatever order the results happened to come back in.
          Boundaries are drawn in their place between the phases they
          separate, since a transition is usually what the model was built to
          estimate and reads best against the dates either side. Falls back
          to the results order for an analysis with no stored structure.

          Last on the page, not first. Each plot is roughly half a screen, so
          a modest analysis put a full-page-height stack of them between the
          reader and the summary table -- the one thing every visit is for.
          The ranges, the flagged dates and the structure are all readable
          without scrolling past a single plot now; the plots are for the date
          you have already decided to look at closely. */}
      {plotOrder.length > 0 && (
        <Paper sx={{ p: 3, mb: 3 }}>
          {/* The toggle sits beside the heading, and the caption goes on its
              own line beneath both. The two views need captions of different
              lengths, and while the caption shared a line with the toggle
              that difference moved the control when it was pressed -- to a
              line of its own on a wide screen, and 40px down the page on a
              narrow one, both times away from the pointer that had just
              clicked it. Beside a heading of fixed text it cannot move except
              by the page being resized. */}
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 2 }}>
            <Typography variant="h6" component="h2">
              Posterior Distributions
            </Typography>
            <ToggleButtonGroup
              size="small"
              exclusive
              value={plotView}
              onChange={(_, value) => value && setPlotView(value)}
              aria-label="Plot layout"
              sx={{ flexShrink: 0 }}
            >
              <ToggleButton value="stacked">Stacked</ToggleButton>
              <ToggleButton value="individual">Individual</ToggleButton>
              <ToggleButton value="curve">Curve</ToggleButton>
            </ToggleButtonGroup>
          </Box>
          <Typography variant="body2" color="textSecondary" sx={{ mt: 1 }}>
            {plotView === 'stacked'
              ? `All ${
                  isBayesian ? 'dates and modelled transitions' : 'dates'
                } shown, oldest first, in the same order as the table above. Each row is scaled to its own peak, so heights compare precision, not probability.`
              : plotView === 'curve'
              ? 'Each date plotted on the curve it was calibrated against. Lab measurement on the left axis, and the calibrated distribution sitting where it intersects the curve.'
              : `One plot per ${
                  isBayesian ? 'date and modelled transition' : 'date'
                }, oldest first, in the same order as the table above.`}
          </Typography>

          {/* Above the plots rather than beside the table's controls, so the
              switch that hides them isn't below everything it hides. Only shown
              when there are boundary plots to hide. */}
          {/* Not offered in the Curve view, which has no radiocarbon age to
              place a modelled transition at and therefore never draws one --
              a switch that hides nothing is a switch that appears broken. */}
          {boundaryPlotCount > 0 && plotView !== 'curve' && (
            <FormControlLabel
              sx={{ mt: 1 }}
              control={
                <Checkbox
                  size="small"
                  checked={showBoundaryPlots}
                  onChange={(e) => setShowBoundaryPlots(e.target.checked)}
                />
              }
              label={`Show boundary plots (${boundaryPlotCount})`}
            />
          )}

          {/* Only meaningful for a Bayesian analysis -- a plain calibration's
              posterior IS the unconstrained calibration, so there is nothing
              to shadow it against. */}
          {isBayesian && plotOrder.some((s) => s.priorDistribution) && (
            <FormControlLabel
              sx={{ mt: 1, ml: boundaryPlotCount > 0 && plotView !== 'curve' ? 2 : 0 }}
              control={
                <Checkbox
                  size="small"
                  checked={showPrior}
                  onChange={(e) => setShowPrior(e.target.checked)}
                />
              }
              label="Show prior (unmodelled) distribution"
            />
          )}

          {plotView === 'curve' ? (
            <Box sx={{ mt: 2 }}>
              <CurvePosteriorPlot
                series={plotOrder}
                isModelled={isBayesian}
                showMean={showMean}
                showMedian={showMedian}
                showPrior={showPrior}
                dateFormat={dateFormat}
              />
            </Box>
          ) : plotView === 'stacked' ? (
            <Box sx={{ mt: 2 }}>
              <StackedPosteriorPlot
                series={visiblePlots}
                isModelled={isBayesian}
                showMean={showMean}
                showMedian={showMedian}
                showPrior={showPrior}
                dateFormat={dateFormat}
              />
            </Box>
          ) : (
          <Box sx={{ mt: 2 }}>
            {visiblePlots.map((series) => (
              <Box key={series.key} sx={{ mb: 4 }}>
                {(series.path || series.kind === 'boundary') && (
                  <Stack direction="row" spacing={1} sx={{ mb: 1 }} flexWrap="wrap" useFlexGap>
                    {series.path && (
                      <Chip size="small" icon={<AccountTreeIcon />} label={series.path} />
                    )}
                    {series.kind === 'boundary' && (
                      <Chip
                        size="small"
                        color="primary"
                        variant="outlined"
                        label="Boundary (modelled transition)"
                      />
                    )}
                  </Stack>
                )}
                {series.distribution && series.distribution.length > 0 ? (
                  <PosteriorPlot
                    calAges={series.distribution.map(([age]) => age)}
                    probabilities={series.distribution.map(([, prob]) => prob)}
                    bands={series.bands}
                    priorDistribution={series.priorDistribution}
                    showPrior={showPrior}
                    isBoundary={series.kind === 'boundary'}
                    sampleName={series.title}
                    pointEstimates={series.pointEstimates}
                    showMean={showMean}
                    showMedian={showMedian}
                    dateFormat={dateFormat}
                  />
                ) : (
                  <Alert severity="info">
                    No posterior distribution data available for {series.title}
                  </Alert>
                )}
              </Box>
            ))}
          </Box>
          )}
        </Paper>
      )}

      {/* Durations and differences, last on the page. Every figure in it is a
          length of time rather than a calendar age, and it is read after the
          dates and Boundaries it is derived from rather than before them. */}
      {analysis.intervals && (
        <DurationsPanel
          intervals={analysis.intervals}
          confidence={confidence}
          confidenceLabel={CONFIDENCE_LABEL}
          showMean={showMean}
          showMedian={showMedian}
          // Oldest first, as the table and plots are, so a well-ordered
          // chronology reads down the matrix diagonal rather than scattered.
          dateAxis={chronologicalResults.map((r) => ({
            id: r.sample_id,
            name: r.sample_name || 'Date',
          }))}
        />
      )}

      {/* Said once, quietly, and only where it could have applied: a run that
          was not asked for durations has none, and the difference between
          that and "there were none to find" is not something the reader can
          work out from an absent table. */}
      {/* Said once, quietly, and ONLY WHERE IT COULD HAVE APPLIED -- which is
          what couldReportIntervals now decides. A wiggle match has no
          durations to estimate at all, and a model with no Boundary pair and
          no multi-dated phase has nothing for one to be about, so in both the
          offer promised something turning the option on would not produce.

          Emptiness, not absence, for the block itself: an age-depth model
          produces an intervals block whether or not durations were asked for
          -- its band lives there -- so testing for a missing block would
          suppress the offer on a run that says nothing about durations. */}
      {couldReportIntervals
        && !(analysis.intervals?.durations || []).length
        && !(analysis.intervals?.differences || []).length
        && analysis.status === 'completed' && (
        <Typography variant="body2" color="textSecondary" sx={{ mb: 3 }}>
          This run did not estimate{' '}
          {isBayesian ? 'durations and differences' : 'differences between the dates'}.{' '}
          <Link component={RouterLink} to={`/analysis/${analysis.id}/edit`}>
            Edit
          </Link>{' '}
          it, turn the option on under {isBayesian ? 'Model Settings' : 'Reporting'}, and run
          it again.
        </Typography>
      )}

      <Box sx={{ mt: 4 }}>
        <Button onClick={() => navigate('/')} variant="outlined">
          Back to Dashboard
        </Button>
      </Box>

      <ConfirmDialog
        open={deleteOpen}
        title="Delete analysis?"
        message={`Permanently delete "${analysis.name}"? This removes its samples and results and cannot be undone.`}
        confirmLabel="Delete"
        danger
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setDeleteOpen(false)}
      />

      <ConfirmDialog
        open={rerunOpen}
        title="Re-run this analysis?"
        message="Recomputes results from this analysis's existing inputs and replaces its current results. This can't be undone."
        confirmLabel="Re-run"
        loading={rerunning}
        onConfirm={handleRerun}
        onCancel={() => setRerunOpen(false)}
      />
    </Container>
  )
}
