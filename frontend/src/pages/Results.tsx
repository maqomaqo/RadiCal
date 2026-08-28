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
  FormGroup,
  FormControlLabel,
  Checkbox,
  Chip,
  Stack,
  Tooltip,
  ToggleButton,
  ToggleButtonGroup,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import { useElementTypeColors } from '@/theme/diagramColors'
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
import ConfirmDialog from '@/components/ConfirmDialog'
import { formatApiError } from '@/utils/apiError'
import { useNotify } from '@/contexts/NotificationContext'
import { useCurves } from '@/hooks/useCurves'
import { flattenStructure } from '@/utils/stratigraphy'
import type { StructureRow } from '@/utils/stratigraphy'
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
  // The samples, groups and relationships this analysis used. Wider than the
  // Harris matrix needs: the citation strings name the curve each date was
  // calibrated against, and the Curve view draws each measurement against the
  // curve, where for a marine date the readable value is the age minus Delta-R.
  measurements?: Array<
    MatrixMeasurement & { curve_id?: string; delta_r?: number | null; delta_r_error?: number | null }
  >
  groups?: DraftGroup[]
  // Explicit model structure; boundary elements carry their own posterior.
  model_tree?: ModelElement | null
  relationships?: DraftRelationship[]
  // How the results were computed. Null for an analysis run before this was
  // recorded, reported as unknown rather than assumed to be either method.
  solver?: {
    method: 'exact' | 'gibbs' | 'smc'
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
    // overlap-integral convergence measure.
    sweeps?: number
    convergence?: number
    converged?: boolean
    // Legacy: set only on analyses run before the current sampler.
    draws?: number
    chains?: number
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

// What each glyph above says, for readers who get no glyph. Only the ones
// carrying information: the date bullet is decoration and the element kind is
// already printed beside the name, so only the unassigned warning needs a
// voice.
const STRUCTURE_MARKER_LABEL: Record<string, string> = {
  unassigned: 'Not assigned to any element',
}

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
/** The two surfaces a sticky cell composites against, named once. They repeat
 *  the theme's background.muted and background.paper by hand, because sx
 *  colour tokens do not resolve inside a plain style object. */
const MUTED = '#f4ede7'
const PAPER = '#ffffff'

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
 * One row of the structure-ordered results table. A boundary reports through
 * the same columns as a date: both are modelled parameters with a posterior,
 * and one table is what makes the transitions legible against the dates they
 * bracket.
 */
function StructureTableRow({
  row, result, isBayesian, showMean, showMedian, confidence, dateFormat,
  unmodelledExpanded, posteriorExpanded,
  isCollapsible, isCollapsed, onToggleCollapse,
}: {
  row: StructureRow
  result?: SampleResult
  isBayesian: boolean
  showMean: boolean
  showMedian: boolean
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
  const concern = isDate ? concernFor(result, row.outlierPrior) : null
  // A date given a NOTABLE outlier prior whose posterior did not rise above it
  // still had special treatment -- worth saying, but not as a warning, so it
  // is a plain chip rather than folded into the reasons text. Below
  // NOTABLE_OUTLIER_PRIOR it stays quiet: OxCal's routine 5% default across a
  // phase is not worth a chip on every clean row.
  const assignedOutlier =
    isDate && !concern && (row.outlierPrior ?? 0) >= NOTABLE_OUTLIER_PRIOR
      ? { prior: row.outlierPrior ?? 0, model: row.outlierModel }
      : null
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
          pl: 2 + row.depth * 2.5,
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
            // hit target does not: a negative margin lets the 44px target
            // overlap the row's padding rather than setting the row height.
            sx={{
              p: 0.25,
              mr: 0.25,
              verticalAlign: 'middle',
              width: 44,
              height: 44,
              my: '-10px',
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
      </TableCell>
      {/* Unmodelled first, then everything the MODEL produced: mean, std,
          median, span. The point estimates used to sit left of the unmodelled
          column, which put three posterior quantities inside the raw
          calibration's half of the table. */}
      {isBayesian && (
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
      {isBayesian && (
        <TableCell align="right" sx={NUMERIC_CELL_SX}>
          {isDate && (
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
                {result?.agreement_index != null ? `A ${result.agreement_index.toFixed(0)}` : 'A —'}
                {'  ·  '}
                {result?.outlier_probability != null
                  ? `O ${Math.round(result.outlier_probability * 100)}%`
                  : 'O —'}
              </Box>
            </Stack>
          )}
        </TableCell>
      )}
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
  // Opt-in: HPD ranges are the primary convention for reporting a possibly
  // multimodal result, and a single point estimate can be misleading -- a mean
  // can fall in the low-probability gap between two peaks.
  const [showMean, setShowMean] = useState(false)
  const [showMedian, setShowMedian] = useState(false)
  // On by default, the transitions usually being the point of the model. A
  // real sequence has a boundary between every pair of phases, though, so on a
  // large one they outnumber the dates -- this hides them without the dates.
  const [showBoundaryPlots, setShowBoundaryPlots] = useState(true)
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

  // Declared above the loading/error early returns: a hook after a conditional
  // return is only called on some renders.
  //
  // What produced every figure on the page, for the citation strings -- the
  // curves used and the solver that ran, both already displayed elsewhere.
  const solverName =
    analysis?.solver?.method === 'exact'
      ? 'exact solver'
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

  // The results table in the model's own order, mirroring the CSV export row
  // for row (backend model_tree.flatten_structure). Empty for an analysis with
  // no stored structure, which falls back to the flat date list below.
  const structureRows = flattenStructure(analysis.model_tree, analysis.measurements || [])

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
  const measurementBySampleId = new Map((analysis.measurements || []).map((m) => [m.id, m]))

  // analysis.results arrives in DB insertion order. With no model structure to
  // walk, sort oldest first by posterior mean (larger cal BP is older), which
  // is the convention structureRows gives the structured case. Ties and
  // missing means fall back to the original order.
  const chronologicalResults = [...(analysis.results || [])].sort((a, b) => {
    if (a.posterior_mean == null || b.posterior_mean == null) return 0
    return b.posterior_mean - a.posterior_mean
  })

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
            convergence after {analysis.solver.sweeps?.toLocaleString()} passes,
            short of the 95% usually wanted. The ranges below are probably a few
            years out rather than wrong
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
            } over ${analysis.solver.sweeps?.toLocaleString()} passes.`
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
        <Box>
          {/* variant="h1", not h3: the theme defines h1 as 2.5rem/600 -- the
              design system's Headline -- and this page was rendering its title
              at MUI's stock h3 (3rem/400), bigger and lighter than the token
              written for exactly this job. */}
          <Typography variant="h1" component="h1" gutterBottom>
            {analysis.name}
          </Typography>
        <Typography variant="body2" color="textSecondary">
            Project:{' '}
            {analysis.project_id ? (
              <Link
                component={RouterLink}
                to={`/projects/${analysis.project_id}`}
                underline="hover"
                sx={{ fontWeight: 600 }}
              >
                {analysis.project_name}
              </Link>
            ) : (
              'Unassigned'
            )}
          </Typography>
          <Typography variant="body2" color="textSecondary" gutterBottom>
            Created: {new Date(analysis.created_at).toLocaleString()}
          </Typography>
        </Box>
        
{/* Highlighted main finding of the analysis.
    Everything below this is the working: the table, the diagram, the
            figures. */}
        {primaryReading.length === 2 && (
          <Box sx={{ mt: 2 }}>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={{ xs: 1.5, sm: 5 }}>
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
                        for them is in the table. `primaryReading` is the first
                        and last Boundary of the model by construction. */}
                    <Typography variant="body2" color="textSecondary">
                      {idx === 0 ? 'Sequence begins' : 'Sequence ends'}
                    </Typography>
                    <Typography
                      variant="h5"
                      component="p"
                      sx={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums', lineHeight: 1.15 }}
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
            <Typography variant="body2" color="textSecondary" sx={{ mt: 1 }}>
              {[`${CONFIDENCE_LABEL} span`, ...analysisProvenance].join(' · ')}
            </Typography>
          </Box>
        )}
        
        {/* A compact line -- this is a single record's
            detail view, not a dashboard. The "results" chip only appears at all
            when the counts actually diverge -- a pending/failed/partial run --
            which is exactly when that divergence is worth a reader's attention. */}
        <Stack direction="row" spacing={1} sx={{ mt: 3 }} flexWrap="wrap" useFlexGap alignItems="center">
          {solverChip}
          <Chip
            label={analysis.analysis_type === 'group_calibration' ? 'calibration' : analysis.analysis_type}
          />
          <Chip
            label={analysis.status}
            color={
              analysis.status === 'completed' ? 'success' : analysis.status === 'failed' ? 'error' : 'warning'
            }
          />
          <Chip
            label={`${analysis.measurements_count} sample${analysis.measurements_count === 1 ? '' : 's'}`}
          />
          {analysis.results.length !== analysis.measurements_count && (
            <Chip
              color="warning"
              label={`${analysis.results.length} of ${analysis.measurements_count} have results`}
            />
          )}
        </Stack>

        

        {/* Its own row below the title/chips rather than beside the title,
            so a 220px-minimum select plus four buttons has the full card
            width to reflow into rather than competing with the title block
            for horizontal space. flexWrap still earns its place here: on a
            narrow screen this is five separate controls, and without it
            they'd force the page to scroll sideways instead of wrapping. */}
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', mt: 3 }}>
          <FormControl size="small" sx={{ minWidth: 220 }}>
            {/* id + labelId, not just <InputLabel>: without the pair MUI
                renders a <label> with no `for` and a role="combobox" with no
                accessible name, so this announced only its current value --
                "cal BP (years before 1950), combobox" -- with nothing saying
                what it governs, and voice control had no target to say. */}
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
          <Button
            startIcon={<EditIcon />}
            variant="outlined"
            onClick={() => navigate(`/analysis/${analysis.id}/edit`)}
          >
            Edit
          </Button>
          <Button
            startIcon={<ContentCopyIcon />}
            variant="outlined"
            disabled={duplicating}
            onClick={handleDuplicate}
          >
            {duplicating ? 'Copying...' : 'Duplicate'}
          </Button>
          <Button
            startIcon={<ReplayIcon />}
            variant="outlined"
            onClick={() => setRerunOpen(true)}
          >
            Re-run
          </Button>
          <Button
            startIcon={<DeleteIcon />}
            color="error"
            variant="outlined"
            onClick={() => setDeleteOpen(true)}
          >
            Delete
          </Button>
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
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 2, mb: 2 }}>
          <Box>
            <Typography variant="h6" component="h2" gutterBottom>
              Results Summary
            </Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ sm: 'center' }}>
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
              <FormGroup row>
                <FormControlLabel
                  control={<Checkbox size="small" checked={showMean} onChange={(e) => setShowMean(e.target.checked)} />}
                  label="Show Mean"
                />
                <FormControlLabel
                  control={<Checkbox size="small" checked={showMedian} onChange={(e) => setShowMedian(e.target.checked)} />}
                  label="Show Median"
                />
              </FormGroup>
            </Stack>
          </Box>
          <Stack direction="row" spacing={1}>
            <Button
              startIcon={<DownloadIcon />}
              onClick={handleExportCSV}
              variant="outlined"
            >
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
              analysis reopens the model, where a date can be given an outlier prior,
              moved to another phase, or dropped from the run.
            </Typography>
          </Alert>
        )}

        {/* maxHeight + a sticky header row: <thead> was position:static and
            the container had no height, so only the name column was sticky and
            only horizontally. On the 63-date model 1,532px of a 2,432px table
            scrolled past with no column headings on screen -- the reader was
            comparing two adjacent right-aligned numeric columns with nothing
            saying which was which. Capped at 70vh so the page still scrolls as
            one document on short models. */}
        <TableContainer sx={{ ...SCROLL_HINT_SX, maxHeight: { xs: 'none', md: '70vh' } }}>
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
              // pin on the name column still works.
              '& thead th': { backgroundColor: MUTED, top: { xs: 'auto', md: 0 } },
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
                {isBayesian && (
                  <TableCell align="right">
                    <RangeColumnHeader
                      label={<>Unmodelled {CONFIDENCE_LABEL} span</>}
                      expanded={unmodelledExpanded}
                      onToggle={() => setUnmodelledExpanded((v) => !v)}
                    />
                  </TableCell>
                )}
                {showMean && <TableCell align="right">Posterior Mean</TableCell>}
                {showMean && <TableCell align="right">σ (yr)</TableCell>}
                {showMedian && <TableCell align="right">Posterior Median</TableCell>}
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
                {isBayesian && <TableCell align="right">Diagnostics</TableCell>}
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
                      isBayesian={isBayesian}
                      showMean={showMean}
                      showMedian={showMedian}
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
                        {isBayesian && (
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
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <AccountTreeIcon color="action" />
            <Typography variant="h6" component="h2">
              Model Structure used
            </Typography>
          </Box>
          <HarrisMatrixDiagram
            tree={analysis.model_tree}
            measurements={analysis.measurements || []}
            assignments={structureAssignments}
            dateFormat={dateFormat}
            confidence={confidence}
            confidenceLabel={CONFIDENCE_LABEL}
          />
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
              ? `Every ${
                  isBayesian ? 'date and modelled transition' : 'date'
                } in one plot. Oldest first, in the same order as the table above. Each row is scaled to its own peak, so heights compare precision, not probability.`
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
      {!analysis.intervals
        && analysis.status === 'completed'
        && (isBayesian || analysis.analysis_type === 'group_calibration')
        && (analysis.results || []).length > 1 && (
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
