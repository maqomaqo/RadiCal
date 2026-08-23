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
import { visuallyHidden } from '@mui/utils'
import HelpOutlineIcon from '@mui/icons-material/HelpOutline'
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
import StackedPosteriorPlot from '@/components/StackedPosteriorPlot'
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
import { hpdBands, hpdEnvelopeFromHistogram, type HPDBand } from '@/utils/hpd'
import type {
  MatrixMeasurement,
  DraftGroup,
  DraftRelationship,
  IntervalsBlock,
  ModelElement,
  PosteriorSummary,
} from '@/types'

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
  // The samples, groups, and stratigraphic relationships actually used --
  // present for analyses saved after the stratigraphy feature was added;
  // older stored analyses will have empty groups/relationships (there
  // were none to persist) but still have measurements.
  // Widened past what the Harris matrix needs: the citation strings name the
  // curve each date was calibrated against, and curve_id is on the response
  // even though MatrixMeasurement -- which exists for the diagram -- has no
  // reason to carry it.
  measurements?: Array<MatrixMeasurement & { curve_id?: string }>
  groups?: DraftGroup[]
  // Explicit model structure; boundary elements carry their own posterior.
  model_tree?: ModelElement | null
  relationships?: DraftRelationship[]
  // How the results were actually computed. Null for an analysis run before
  // this was recorded, which is reported as unknown rather than assumed to be
  // either method -- see the solver notice below.
  solver?: {
    method: 'exact' | 'gibbs' | 'smc'
    // What the analysis asked for. 'gibbs' here means the analyst chose OxCal's
    // method deliberately, so a sampled result is not a fallback and must not be
    // labelled as one.
    requested?: 'auto' | 'exact' | 'gibbs'
    reason?: string | null
    // How many implied transitions were supplied to the solver that ran. Always
    // 0 on the sampled path.
    implicit_boundaries?: number
    // How many WOULD be needed for the exact solver to apply. Set only when that
    // is why this was sampled -- a different statement from `reason`, which says
    // the model cannot be integrated exactly at all.
    exact_needs_boundaries?: number
    // Sampled path only. `converged` is against OxCal's own 0.95 target for the
    // overlap-integral convergence measure.
    sweeps?: number
    convergence?: number
    converged?: boolean
    // Only ever set on analyses run before the SMC fallback was replaced.
    draws?: number
    chains?: number
  } | null
  // How long things lasted and how far apart they sit. Null when the run was
  // not asked for them, which is not the same as "there are none" -- the page
  // says so and points at the option rather than showing an empty table.
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
  // Full HPD breakdown, one or more disjoint ranges with their own
  // probability share -- present for analyses computed after this
  // reporting was added; older stored results may only have hpd_68/95.
  hpd_68_ranges?: HPDRange[]
  hpd_95_ranges?: HPDRange[]
  probability_distribution?: Array<[number, number]>
  // How well the modelled posterior still fits this date's own unmodelled
  // likelihood, as a percentage (Bronk Ramsey 1995). Null for an analysis
  // computed before this existed.
  agreement_index?: number | null
  // Posterior probability (0-1) that the date does not belong to the
  // population being modelled. Null when no outlier model was applied to
  // it, which is not the same as a zero.
  outlier_probability?: number | null
  // The plain/unmodelled calibration for this sample (no stratigraphic
  // constraints applied) -- present only for a Bayesian analysis (see
  // GET /api/analysis/:id), so "unmodelled vs modelled" can be
  // compared side by side. For a calibration-only analysis,
  // probability_distribution above already *is* the unmodelled result.
  //
  // Served as finished HPD ranges, one entry per confidence level this
  // page offers, rather than a series to derive them from: the server
  // computes them over the calibration curve's full grid, where deriving
  // them here would only have the plottable series, capped at 200 points.
  // That difference showed up as the screen and the CSV disagreeing in
  // their last digits about the same calibration.
  unmodelled_hpd_ranges?: Array<{ confidence: number; ranges: HPDRange[] }> | null
  // The same unmodelled calibration as a plottable series, for the optional
  // "prior" shadow behind the modelled posterior -- see showPrior below.
  unmodelled_distribution?: Array<[number, number]> | null
}

/**
 * One posterior to plot. Dates and boundaries are both modelled parameters
 * with a distribution, so they are normalised to a single shape and drawn by
 * the same component -- the sequence of plots then reads as the model does,
 * rather than as two separate lists.
 */
interface PlottableSeries {
  key: string
  kind: 'date' | 'boundary'
  /** Sample name, or the boundary's name. */
  title: string
  /** Where it sits in the structure; null for a date the structure omits. */
  path: string | null
  distribution?: Array<[number, number]>
  /** HPD bands at the page's selected confidence, outer first -- computed
   *  from the same distribution the results table reads, so a figure and the
   *  row above it can never report different numbers for one quantity. */
  bands?: HPDBand[]
  /** A date's plain calibration with no stratigraphic constraints applied; absent for a boundary, which has no such counterpart. */
  priorDistribution?: Array<[number, number]>
  pointEstimates: { mean?: number; median?: number; std?: number }
}

/**
 * Copy this row's reading as it would be cited.
 *
 * Quiet until the row is hovered or the control is focused, but always in the
 * tab order and always named -- a copy affordance that only exists on hover is
 * the same mistake the diagnostics tooltip made, and this one has a keyboard
 * user's name on it. The retyping it replaces is a real step: the range is
 * 14px text in the middle of a wide row, and it ends up in a manuscript.
 */
function CopyCitation({ text, always = false }: { text: string; always?: boolean }) {
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
          // 24x24 is WCAG 2.5.8's floor; the negative margin keeps the target
          // from pushing the line box taller than the text it sits in.
          minWidth: 24,
          minHeight: 24,
          my: '-3px',
          // In a table this stays out of the way until the row is hovered or
          // the control is focused -- sixty always-on copy icons would be
          // noise. In the headline there is exactly one, and it is the point.
          opacity: always || copied ? 1 : 0,
          transition: 'opacity 120ms ease-out',
          'tr:hover &, &:focus-visible': { opacity: 1 },
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
 * A reading in the form it would be written into a manuscript. Built here
 * rather than at each call site so the string a reader copies is assembled
 * once, from the same envelope the cell beside it prints.
 *
 * Everything in it is a fact the page already holds -- the range at the
 * selected level, which curve produced it, which solver ran. Nothing is
 * inferred, and a part the page does not know is simply left out rather than
 * guessed at.
 */
export function citationLine(
  name: string,
  range: string,
  confidenceLabel: string,
  provenance: string[]
): string {
  const qualifiers = [`${confidenceLabel} HPD`, ...provenance].filter(Boolean).join(', ')
  return `${name}: ${range} (${qualifiers})`
}

// Said in one place because it is said in two: the header's help control and
// the persistent key under the table. Two copies of this drifting apart would
// be the same defect the page had elsewhere, in prose instead of numbers.
const DIAGNOSTICS_HELP =
  'A = agreement index: how well the modelled date still fits the prior distribution ' +
  '(below 60 is worth re-examining). O = posterior probability the date is an outlier, ' +
  'when an outlier model was applied to it. "Suspect"/"Check" identifies an outlier ' +
  'probability greater than the prior it was given; "Assigned" means the data stayed ' +
  'close to the assigned prior.'

// The three standard Gaussian-equivalent confidence levels radiocarbon
// results are conventionally reported at (1/2/3-sigma) -- OxCal's own
// convention, offered here as a single user choice rather than always
// showing the fixed 68%/95% pair.
const CONFIDENCE_OPTIONS = [
  { value: 0.682, label: '68.2%' },
  { value: 0.954, label: '95.4%' },
  { value: 0.997, label: '99.7%' },
] as const

// Indentation and marker for one row of the model structure. Sequences and
// phases are headings; boundaries and dates are the rows carrying numbers.
// No entries for 'sequence'/'phase' -- those now have a real disclosure
// chevron doing that job (see StructureTableRow), so a second, static
// glyph next to it would just be redundant.
const STRUCTURE_MARKER: Record<string, string> = {
  boundary: '◆', hiatus: '⋯', unassigned: '⚠', date: '·',
}

// What each glyph above says, for readers who get no glyph. Only the ones
// that carry information: the date bullet is decoration and the boundary/
// hiatus kind is already printed beside the name, so the one that genuinely
// needs a voice is the unassigned warning.
const STRUCTURE_MARKER_LABEL: Record<string, string> = {
  unassigned: 'Not assigned to any element',
}

// Every numeric column in the results table (ages, HPD ranges, percentages,
// agreement/outlier diagnostics) gets this -- a multi-range HPD string is
// long enough to wrap on an ordinary tablet width, and wrapping mid-value
// splits a single dash-joined calendar range across two lines, which reads
// as two numbers rather than one range. Forcing it onto one line instead
// lets the table grow wider than its container, which TableContainer
// already scrolls horizontally for -- nothing here ever silently clips.
const NUMERIC_CELL_SX = { whiteSpace: 'nowrap' } as const

/**
 * The name column stays put while the numbers scroll under it.
 *
 * The comment above NUMERIC_CELL_SX is right that TableContainer scrolls
 * rather than clipping -- but at 375px this table is 894px wide inside a
 * 295px box, so scrolling to reach the HPD range takes the sample's NAME off
 * screen with it. A reader ends up looking at "633-557 cal BP · A 109 · O 3%"
 * with nothing saying whose it is, which is worse than not showing the number.
 *
 * Needs its own opaque background: a sticky cell is painted over by whatever
 * scrolls beneath it otherwise. Callers pass the row's own tint so a flagged
 * row keeps its colour under the sticky column too.
 */
function stickyNameCell(background: string) {
  return {
    position: 'sticky' as const,
    left: 0,
    zIndex: 2,
    backgroundColor: background,
  }
}

/**
 * A shadow at the right edge that appears only while there is more table to
 * scroll to, and fades out at the end. Otherwise the overflow is silent: the
 * Overlap column simply is not there, and nothing on screen suggests it
 * could be. `background-attachment: local` is what ties it to the scroll
 * position rather than the viewport.
 */
const SCROLL_HINT_SX = {
  backgroundImage:
    'linear-gradient(to left, var(--mui-paper, #fff) 40%, rgba(255,255,255,0)),' +
    'radial-gradient(farthest-side at 100% 50%, rgba(43,35,32,0.16), rgba(43,35,32,0))',
  backgroundPosition: 'right center, right center',
  backgroundSize: '32px 100%, 14px 100%',
  backgroundRepeat: 'no-repeat',
  backgroundAttachment: 'local, scroll',
} as const

// Below this, OxCal's convention is that a date is worth re-examining
// (Bronk Ramsey 1995).
const POOR_AGREEMENT = 60
// How far above the PRIOR the posterior outlier probability has to rise to
// count as the model having found something. Not an absolute posterior
// threshold: a date given prior 1 (forced to be treated as an outlier)
// always comes back with posterior 1 by construction, so judging the
// posterior on its own would flag every forced-outlier date as "suspect"
// for the setting the analyst gave it, not for anything the data said. At
// prior 0 (the common case) this is equivalent to the old absolute
// threshold, since lift and posterior are the same number.
const OUTLIER_LIFT_SUSPECT = 0.5
// ...and a rise it is unsure about. Reported separately rather than folded
// in, because "probably wrong" and "worth a look" are different messages
// and a single threshold would have to pick one.
const OUTLIER_LIFT_CHECK = 0.2
// A prior at or above this is a deliberate, elevated suspicion -- worth
// saying even when the posterior didn't move past it (see "Assigned" below)
// -- rather than OxCal's routine 5% default, which an analyst commonly
// turns on for every date in a phase and which would otherwise put a chip
// on every clean row. Also the line below which agreement_index is still
// treated as independent evidence -- see concernFor.
const NOTABLE_OUTLIER_PRIOR = 0.2

type Concern = { level: 'suspect' | 'check'; reasons: string[] }

/**
 * Whether a date's own diagnostics say something is wrong with it, and what.
 *
 * The outlier check compares the posterior probability to the PRIOR the
 * analyst assigned this date, not the raw posterior on its own -- see
 * OUTLIER_LIFT_SUSPECT above for why. A date whose posterior sits close to
 * its own prior isn't flagged here at all; StructureTableRow shows that as
 * a separate, non-alarming "Assigned" chip instead, since it is a fact
 * about how the date was configured, not a diagnostic finding.
 *
 * Colour alone would not do: the reasons are shown as text beside it, since
 * a red row tells a reader something is wrong without telling them what, and
 * the two diagnostics mean genuinely different things. Agreement says the
 * model had to move the date; the outlier probability says the model thinks
 * it does not belong. A date can trip either without the other -- except
 * that for a date given a notable outlier prior, "the model had to move
 * it" is no longer independent information: agreement.py compares the
 * posterior to the date's plain, un-modelled calibration, and a date
 * pulled toward its broad/outlier kernel is expected to drift from that on
 * its own (agreement.py's own docstring: this "detects a date the model
 * had to move, which is not the same as a date that does not belong").
 * Below NOTABLE_OUTLIER_PRIOR, where no such treatment is doing that
 * pulling, poor agreement is still real, independent evidence.
 */
function concernFor(result?: SampleResult, outlierPrior?: number | null): Concern | null {
  if (!result) return null
  const reasons: string[] = []
  let level: 'suspect' | 'check' | null = null
  const prior = outlierPrior ?? 0

  const outlier = result.outlier_probability
  if (outlier != null) {
    const lift = outlier - prior
    const withPrior = (message: string) =>
      prior > 0 ? `${message} -- up from the ${Math.round(prior * 100)}% prior it was given` : message
    if (lift >= OUTLIER_LIFT_SUSPECT) {
      level = 'suspect'
      reasons.push(withPrior(`${Math.round(outlier * 100)}% likely an outlier`))
    } else if (lift >= OUTLIER_LIFT_CHECK) {
      level = 'check'
      reasons.push(withPrior(`${Math.round(outlier * 100)}% chance of being an outlier`))
    }
  }

  const agreement = result.agreement_index
  if (agreement != null && agreement < POOR_AGREEMENT && prior < NOTABLE_OUTLIER_PRIOR) {
    level = 'suspect'
    reasons.push(`agreement ${agreement.toFixed(0)}, below ${POOR_AGREEMENT}`)
  }

  return level ? { level, reasons } : null
}

/**
 * One row of the structure-ordered results table. A boundary reports
 * through the same columns as a date deliberately: both are modelled
 * parameters with a posterior, and reading them in one table is what makes
 * the transitions legible against the dates they bracket.
 */
function StructureTableRow({
  row, result, isBayesian, showMean, showMedian, confidence, confidenceLabel,
  provenance, dateFormat,
  isCollapsible, isCollapsed, onToggleCollapse,
}: {
  row: StructureRow
  result?: SampleResult
  isBayesian: boolean
  showMean: boolean
  showMedian: boolean
  confidence: number
  confidenceLabel: string
  /** Curve and solver, for this row's citation string. */
  provenance: string[]
  dateFormat: DateFormat
  /** Whether this row is a Sequence/Phase with something nested under it -- only those get a disclosure control. */
  isCollapsible: boolean
  isCollapsed: boolean
  onToggleCollapse?: () => void
}) {
  const theme = useTheme()
  const isDate = row.kind === 'date'
  const posterior = row.posterior
  // A heading (sequence/phase/hiatus) has no posterior of its own -- its
  // numbers are the boundaries around it and the dates inside it.
  const isHeading = !isDate && !posterior

  const mean = isDate ? result?.posterior_mean : posterior?.posterior_mean
  const median = isDate ? result?.posterior_median : posterior?.posterior_median
  const std = isDate ? result?.posterior_std : posterior?.posterior_std
  const distribution = isDate ? result?.probability_distribution : posterior?.probability_distribution
  const envelope = distribution ? hpdEnvelopeFromHistogram(distribution, confidence) : null
  const unmodelledRanges = isDate && isBayesian
    ? unmodelledRangesFor(result, confidence)
    : []

  const limiters = [
    row.tpqAgeBp != null ? `TPQ ${row.tpqAgeBp}` : null,
    row.taqAgeBp != null ? `TAQ ${row.taqAgeBp}` : null,
  ].filter(Boolean).join(' · ')

  const cell = (value: string) => (isHeading ? '' : value)
  const concern = isDate ? concernFor(result, row.outlierPrior) : null
  // A date given a NOTABLE outlier prior whose posterior didn't rise
  // meaningfully above it (see concernFor) still had special treatment --
  // worth saying, just not as a warning. Shown as a plain chip rather than
  // folded into the reasons text above, so it never inherits the red/amber
  // styling a real concern gets. Below NOTABLE_OUTLIER_PRIOR this stays
  // quiet: OxCal's routine 5% default, turned on across a whole phase, is
  // not itself worth a chip on every clean row -- the "O 4%" text already
  // says it was checked.
  const assignedOutlier =
    isDate && !concern && (row.outlierPrior ?? 0) >= NOTABLE_OUTLIER_PRIOR
      ? { prior: row.outlierPrior ?? 0, model: row.outlierModel }
      : null
  // A tint carries the flag across the whole row so it is findable when
  // scanning a long table; the chip in the diagnostics column says what it
  // actually is, since colour on its own is not a message.
  const tint = concern
    ? alpha(
        concern.level === 'suspect' ? theme.palette.error.main : theme.palette.warning.main,
        0.08
      )
    : undefined

  return (
    <TableRow
      sx={{
        backgroundColor: tint ?? (isHeading ? 'background.muted' : undefined),
        ...(concern
          ? { borderLeft: 3, borderColor: concern.level === 'suspect' ? 'error.main' : 'warning.main' }
          : {}),
      }}
    >
      <TableCell
        sx={{
          pl: 2 + row.depth * 2.5,
          ...stickyNameCell(tint ?? (isHeading ? '#f4ede7' : '#ffffff')),
        }}
      >
        {/* The disclosure control replaces the old static ▸/▪ glyph for
            Sequence/Phase rows entirely, rather than sitting beside it --
            two symbols both saying "this is a heading" would be redundant
            now that one of them is real. Non-collapsible headings (empty
            Sequences/Phases with nothing nested under them) get neither: no
            button, since there's nothing to toggle, and no glyph, since
            STRUCTURE_MARKER no longer has an entry for those kinds. */}
        {isCollapsible && (
          <IconButton
            size="small"
            onClick={onToggleCollapse}
            aria-label={`${isCollapsed ? 'Expand' : 'Collapse'} ${row.name}`}
            // The visible chevron stays small -- it sits inline in a dense
            // table row -- but the thing a finger or a stylus has to hit does
            // not: a negative margin lets the 44px target overlap the row's
            // own padding rather than setting the row height.
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
        {/* Only when it adds something: an element named "Site sequence" or
            "Phase 1" already says what it is, but a boundary named "Start"
            does not. */}
        {!isDate && !row.name.toLowerCase().includes(String(row.kind)) && (
          <Box component="span" sx={{ ml: 1, color: 'text.secondary', fontSize: '0.75rem' }}>
            {row.kind}
          </Box>
        )}
        {limiters && (
          <Box component="span" sx={{ ml: 1, color: 'text.secondary', fontSize: '0.75rem' }}>
            ({limiters})
          </Box>
        )}
      </TableCell>
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
      {isBayesian && (
        <TableCell align="right" sx={NUMERIC_CELL_SX}>
          {/* Only a date has an unmodelled counterpart -- a boundary exists
              solely as a modelled parameter. */}
          {isDate ? (unmodelledRanges.length > 0 ? formatHPDRanges(unmodelledRanges, dateFormat) : 'N/A') : ''}
        </TableCell>
      )}
      <TableCell align="right" sx={NUMERIC_CELL_SX}>
        {cell(envelope ? formatCalendarRange(envelope[0], envelope[1], dateFormat) : 'N/A')}
        {!isHeading && envelope && (
          <CopyCitation
            text={citationLine(
              row.name,
              formatCalendarRange(envelope[0], envelope[1], dateFormat),
              confidenceLabel,
              provenance
            )}
          />
        )}
      </TableCell>
      {isBayesian && (
        <TableCell align="right" sx={NUMERIC_CELL_SX}>
          {isDate && (
            <Stack direction="column" spacing={0.5} alignItems="flex-end">
              {concern && (
                <Chip
                  size="small"
                  color={concern.level === 'suspect' ? 'error' : 'warning'}
                  variant={concern.level === 'suspect' ? 'filled' : 'outlined'}
                  label={concern.level === 'suspect' ? 'Suspect' : 'Check'}
                />
              )}
              {assignedOutlier && (
                <Tooltip
                  title={
                    `Given a ${Math.round(assignedOutlier.prior * 100)}% ` +
                    `${assignedOutlier.model === 'charcoal' ? 'charcoal' : 'general'} outlier prior. ` +
                    'The posterior is close to that, so the data did not add anything beyond what was already assumed.'
                  }
                >
                  <Chip size="small" variant="outlined" label="Assigned" />
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
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dateFormat, setDateFormat] = useState<DateFormat>(() =>
    analysisId ? loadDateFormatPreference(analysisId) : 'calBP'
  )
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [rerunOpen, setRerunOpen] = useState(false)
  const [rerunning, setRerunning] = useState(false)
  // The run this page started, if it started one. Null on a page reached by
  // any other route -- a bookmark, a reload, a link from the dashboard --
  // where the analysis id is all there is, and useJob falls back to asking
  // what the most recent job for it was.
  const [jobId, setJobId] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [duplicating, setDuplicating] = useState(false)
  const [exportingPdf, setExportingPdf] = useState(false)
  // Posterior mean/median are opt-in reporting choices, not shown by
  // default -- HPD ranges are the primary convention for reporting a
  // (possibly multimodal) calibrated/Bayesian result; a single point
  // estimate can be misleading on its own (a "mean" can fall in a
  // low-probability gap between two peaks).
  const [showMean, setShowMean] = useState(false)
  const [showMedian, setShowMedian] = useState(false)
  // Boundary plots are on by default -- the transitions are usually the
  // point of the model. But a real sequence has a boundary between every
  // pair of phases, so on a large one they can outnumber the dates and make
  // the page long to scroll; this hides them without hiding the dates.
  const [showBoundaryPlots, setShowBoundaryPlots] = useState(true)
  // Off by default -- the modelled posterior is the primary reading of the
  // plot, and overlaying the unconstrained calibration behind every date at
  // once is a comparison a reader opts into, not something to show unasked.
  const [showPrior, setShowPrior] = useState(false)
  // How the posteriors are drawn. Stacked is the default: one figure, one row
  // per date, all on a single calendar axis, which is the only way to see
  // whether two dates overlap -- the question a sequence is built to answer.
  // The per-date charts remain available because the stacked view trades
  // detail for comparison: they carry the full-height curve, the point
  // estimates panel and their own zoom.
  const [plotView, setPlotView] = useState<'stacked' | 'individual'>('stacked')
  // Confidence level for the HPD columns below -- default 95.4% (2-sigma),
  // the most commonly cited "95% confidence" convention in archaeological
  // reporting; switchable to 68.2% or 99.7% without a network round trip
  // (see utils/hpd.ts).
  // Only to turn a stored curve_id into the curve's own name in the citation
  // strings. The request is shared across the app (see api/curves.ts), and a
  // failure degrades to the id rather than dropping the curve from the line.
  const { curves } = useCurves()
  const [confidence, setConfidence] = useState<number>(0.954)
  // How that level is written wherever the page names it -- one string, so a
  // column heading, a plot legend and a diagram label cannot disagree about
  // which confidence the figures beside them were computed at.
  const CONFIDENCE_LABEL = CONFIDENCE_OPTIONS.find((o) => o.value === confidence)?.label ?? ''
  // Which Sequence/Phase headings in the results table are collapsed, keyed
  // by element id. Nothing collapsed by default -- matches the table's
  // previous always-expanded behaviour, so this is additive rather than a
  // new default state to learn.
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

  const fetchAnalysis = async (id: string) => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch(`/api/analysis/${id}`)
      const data = await response.json()
      if (data.success) {
        setAnalysis(data.data)
      } else {
        setError('Failed to load analysis')
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  // Follow whatever is running against this analysis, and reload the page's
  // data when it stops -- however it stopped. A failed or cancelled run
  // changes what the page should show just as much as a successful one does.
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
      // Carry the reporting format being displayed, so the download reads
      // in the same convention as the screen. The cal BP columns come
      // regardless; the format only adds a counterpart beside each of them.
      const response = await fetch(
        `/api/results/${analysisId}/export/csv?date_format=${encodeURIComponent(dateFormat)}`
      )
      // An error response is JSON, and a spreadsheet will open that
      // perfectly happily as a one-cell file -- so without this check a
      // failed export is saved as a .csv and only noticed later, by
      // whoever opens it looking for their results.
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

  // The PDF is rendered server-side and costs a plot per modelled
  // parameter, so a large model takes a few seconds. That is long enough
  // that a button which just sits there reads as broken -- hence the
  // explicit pending state, which the CSV (returned in milliseconds) has
  // never needed.
  const handleExportPDF = async () => {
    if (!analysisId) return
    setExportingPdf(true)
    try {
      const response = await fetch(
        `/api/results/${analysisId}/pdf?date_format=${encodeURIComponent(dateFormat)}`
      )
      // Checked before reading the body: an error response is JSON, and
      // saving it under a .pdf name would hand the user a file that only
      // fails when they try to open it.
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

  // Copy this analysis's inputs into a new one and open it for editing --
  // the "try the same dates under a different model" path, which leaves
  // this analysis and its results untouched.
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

  // Queued, not awaited: the endpoint answers 202 with a job id as soon as
  // it has accepted the work, and the progress display takes it from there.
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

  // Which phase each date sits in, for the Harris matrix further down.
  //
  // There is no fallback to the stored contexts here any more: GET
  // /analysis/:id runs _get_or_migrate_tree for every Bayesian analysis, so
  // one predating model trees is migrated on read and comes back WITH a tree.
  // model_tree is null only for a plain or group calibration, which has no
  // stratigraphy to draw.
  //
  // Memoised on `analysis` so the object identity handed to the diagram is
  // stable across renders; the layout is recomputed only when the analysis
  // itself changes.
  const structureAssignments = useMemo(
    () =>
      Object.fromEntries(
        (analysis?.measurements || [])
          .map((m) => [m.id, m.element_id || m.group_id])
          .filter(([, elementId]) => !!elementId) as [string, string][]
      ),
    [analysis]
  )

  // Declared here, above the loading/error early returns below: a hook after
  // a conditional return is only called on some renders, which is what "Rendered
  // more hooks than during the previous render" means.
  // What produced every figure on the page, for the citation strings: the
  // curves actually used and the solver that actually ran. Both are facts the
  // page already displays; a citation that omits them is the reason a reviewer
  // asks where a number came from.
  const provenance = useMemo(() => {
    const curveIds = [
      ...new Set((analysis?.measurements || []).map((m) => m.curve_id).filter(Boolean)),
    ]
    const curveNames = curveIds.map((id) => curves.find((c) => c.id === id)?.name ?? id)
    const solverName =
      analysis?.solver?.method === 'exact'
        ? 'exact solver'
        : analysis?.solver?.method
        ? 'MH/Gibbs solver'
        : null
    return [curveNames.join(' / '), solverName].filter((v): v is string => Boolean(v))
  }, [analysis?.measurements, analysis?.solver?.method, curves])

  if (loading) {
    return (
      <Container sx={{ py: 4, display: 'flex', justifyContent: 'center' }}>
        <CircularProgress />
      </Container>
    )
  }

  if (error || !analysis) {
    return (
      <Container maxWidth="lg" sx={{ py: 4 }}>
        <Typography color="error" variant="h6">
          {error || 'Analysis not found'}
        </Typography>
        <Button onClick={() => navigate('/')} sx={{ mt: 2 }}>
          Back to Dashboard
        </Button>
      </Container>
    )
  }

  // Where each date sits in the structure now comes from the structure walk
  // itself (see plotOrder below and the results table), rather than from a
  // separate sample -> context lookup. That lookup had to special-case
  // element_id against group_id to work for both tree-authored and migrated
  // analyses; flattenStructure already resolves that in one place.
  const contextsWithModeledSpan = (analysis.groups || []).filter((g) => g.modeled_start && g.modeled_end)

  // Every named Boundary in the model structure, in the order it appears
  // (oldest first). Preferred over the per-context table below when the
  // analysis has an explicit model tree, since adjacent contexts SHARE a
  // boundary -- listing them per context would print the same transition
  // twice, once as one context's end and again as the next one's start.
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

  // The results table, read in the model's own order. Mirrors the CSV
  // export row for row (backend model_tree.flatten_structure), so what's
  // on screen and what's downloaded describe the same model. Empty for a
  // calibration or an analysis with no stored structure, which falls back
  // to the flat date list below.
  const structureRows = flattenStructure(analysis.model_tree, analysis.measurements || [])

  // Sequence/Phase headings that actually have something nested under them
  // -- an empty one (no dates, no children) gets no collapse control, since
  // there is nothing for it to hide.
  const collapsibleElementIds = new Set(
    structureRows
      .filter((row, idx) => {
        if (row.kind !== 'sequence' && row.kind !== 'phase') return false
        const next = structureRows[idx + 1]
        return !!next && next.depth > row.depth
      })
      .map((row) => row.elementId!)
  )

  // What the table actually renders: structureRows with any collapsed
  // Sequence/Phase's subtree hidden. Boundaries stay visible even inside a
  // collapsed subtree -- a Boundary is a modelled transition's own result,
  // not detail to fold away, and collapsing a large structure down to just
  // its Boundaries is often exactly the point (the dates were the noise;
  // the transitions were the answer). Single pass: at most one collapsed
  // ancestor is ever relevant at a time, since a collapse nested inside an
  // already-hidden subtree changes nothing about what's hidden.
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
  // Same reason concernFor needs it in StructureTableRow: a posterior close
  // to the prior it was given isn't a finding, so the banner below has to
  // know each result's prior too, not just its posterior.
  const measurementBySampleId = new Map((analysis.measurements || []).map((m) => [m.id, m]))

  // analysis.results as returned isn't in any particular order -- it's DB
  // insertion order. When there's no model structure to walk instead (a
  // calibration or group calibration), sort oldest first by posterior mean
  // (larger cal BP = older), matching the "oldest first" convention
  // structureRows already gives the structured case. Ties/missing means
  // (shouldn't happen, but not worth crashing over) fall back to the
  // original order.
  const chronologicalResults = [...(analysis.results || [])].sort((a, b) => {
    if (a.posterior_mean == null || b.posterior_mean == null) return 0
    return b.posterior_mean - a.posterior_mean
  })

  // Dates whose own diagnostics object to them, worst first so the banner
  // leads with the one most worth looking at.
  const flaggedDates = (analysis.results || [])
    .map((r, idx) => ({
      name: r.sample_name || `Sample ${idx + 1}`,
      concern: concernFor(r, measurementBySampleId.get(r.sample_id)?.outlier_prior),
    }))
    .filter((f): f is { name: string; concern: Concern } => f.concern !== null)
    .sort((a, b) => (a.concern.level === b.concern.level ? 0 : a.concern.level === 'suspect' ? -1 : 1))

  // The plots, in the same order. Walking structureRows means the sequence
  // on screen is the sequence in the model -- oldest first -- and each plot
  // can say where it sits.
  //
  // Boundaries are plotted alongside the dates, in their place between the
  // phases they separate. A boundary is a modelled parameter with a
  // posterior of its own, and it is usually the thing an analysis was built
  // to estimate; reading its distribution against the dates on either side
  // is the point of the sequence. Any date the structure doesn't mention is
  // appended rather than dropped, so a plot never silently disappears.
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
        bands: hpdBands(result.probability_distribution, confidence, CONFIDENCE_LABEL),
        priorDistribution: result.unmodelled_distribution ?? undefined,
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
        bands: hpdBands(p.probability_distribution, confidence, CONFIDENCE_LABEL),
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
      bands: hpdBands(result.probability_distribution, confidence, CONFIDENCE_LABEL),
      priorDistribution: result.unmodelled_distribution ?? undefined,
      pointEstimates: {
        mean: result.posterior_mean,
        median: result.posterior_median,
        std: result.posterior_std,
      },
    })
  }
  const boundaryPlotCount = plotOrder.filter((s) => s.kind === 'boundary').length
  // The page's primary reading: the transitions this model was built to
  // estimate. A sequence's outermost Boundaries ARE the answer -- the same
  // reasoning the collapse behaviour already encodes, where collapsing a
  // Phase keeps its Boundaries because "the dates were the noise; the
  // transitions were the answer".
  //
  // Deliberately empty for a calibration: dates laid out with no relationship
  // asserted between them have no modelled transition, and inventing a
  // headline span for them would be a claim the analysis does not make. There
  // the table is the answer, and this block simply does not appear.
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

  // Which solver produced every number on the page -- qualifies all of them
  // rather than any one, so it sits with the other summary chips rather than
  // buried near the table. Split into a chip (the two good outcomes: quiet,
  // since they're the normal cases) and an alert (a warning worth its own
  // paragraph -- sampled-and-not-converged, or the retired SMC path with its
  // known failure on wiggly curves; see services/gibbs.py's module
  // docstring). Only one of the two is ever set.
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
        // the visible "MH/Gibbs (chosen) — 95% converged" was never announced
        // and voice control had nothing to target. As a description it is
        // additive, which is what a tooltip is for.
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
                to "95%", which reads as a contradiction against the 95%
                target it just missed. */}
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
      // A run the analyst asked for is labelled as their choice, not as a
      // fallback -- they may have picked OxCal's method to reproduce a
      // published analysis or to cross-check the exact solver, and calling
      // that "sampled because we had to" would be false.
      solverChip = (
        // describeChild: without it MUI makes the tooltip the chip's NAME, so
        // the visible "MH/Gibbs (chosen) — 95% converged" was never announced
        // and voice control had nothing to target. As a description it is
        // additive, which is what a tooltip is for.
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
    // component="main": the page had no main landmark at all, so there was no
    // way past the AppBar for anyone navigating by landmark.
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
          <Typography variant="body1" color="textSecondary" gutterBottom>
            Created: {new Date(analysis.created_at).toLocaleString()}
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
        </Box>

        {/* A compact line, not four hero stat cards -- this is a single record's
            detail view, not a dashboard, and two of the four numbers those
            cards showed (Samples, Results) are the same number in every normal
            case: one result per measurement. Identical hero numbers read as a
            bug even when they aren't. The "results" chip only appears at all
            when the counts actually diverge -- a pending/failed/partial run --
            which is exactly when that divergence is worth a reader's attention. */}
        <Stack direction="row" spacing={1} sx={{ mt: 2 }} flexWrap="wrap" useFlexGap alignItems="center">
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

        {/* The one thing a reader came for, set as the one thing on the page.
            Everything below this is the working: the table, the diagram, the
            figures. This is the reading, at the size of a reading, with the
            qualifiers a citation needs attached to it rather than scattered
            across a header row and a chip. */}
        {primaryReading.length === 2 && (
          <Box sx={{ mt: 3 }}>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={{ xs: 1.5, sm: 5 }}>
              {primaryReading.map((row) => {
                const envelope = hpdEnvelopeFromHistogram(
                  row.posterior!.probability_distribution,
                  confidence
                )
                if (!envelope) return null
                const range = formatCalendarRange(envelope[0], envelope[1], dateFormat)
                return (
                  <Box key={row.elementId ?? row.name}>
                    <Typography variant="body2" color="textSecondary">
                      {row.name}
                    </Typography>
                    <Typography
                      variant="h4"
                      component="p"
                      sx={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums', lineHeight: 1.15 }}
                    >
                      {range}
                      <CopyCitation
                        always
                        text={citationLine(row.name, range, CONFIDENCE_LABEL, provenance)}
                      />
                    </Typography>
                  </Box>
                )
              })}
            </Stack>
            <Typography variant="body2" color="textSecondary" sx={{ mt: 1 }}>
              {[`${CONFIDENCE_LABEL} HPD`, ...provenance].join(' · ')}
            </Typography>
          </Box>
        )}

        {/* Its own row below the title/chips rather than beside the title,
            so a 220px-minimum select plus four buttons has the full card
            width to reflow into rather than competing with the title block
            for horizontal space. flexWrap still earns its place here: on a
            narrow screen this is five separate controls, and without it
            they'd force the page to scroll sideways instead of wrapping. */}
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center', mt: 3 }}>
          <FormControl size="small" sx={{ minWidth: 220 }}>
            <InputLabel>Date Format</InputLabel>
            <Select value={dateFormat} label="Date Format" onChange={handleDateFormatChange}>
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

      {/* A failed run, with the reason. Before this the page showed only the word
          "failed" in the status card, leaving the user to guess -- and one way to
          fail is now a deliberate choice (requiring the exact solver on a model
          that cannot support it), where the reason names exactly what to change. */}
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

      {/* Boundaries used to be listed in a table of their own here. They now
          sit in the results table in their place in the structure, between
          the phases they separate, which is where they are meaningful --
          a boundary between two phases is one shared transition, the end of
          one and the start of the next. */}

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
                  <TableCell align="right">Modelled Start ({CONFIDENCE_LABEL} HPD)</TableCell>
                  {showMean && <TableCell align="right">End Mean</TableCell>}
                  {showMedian && <TableCell align="right">End Median</TableCell>}
                  <TableCell align="right">Modelled End ({CONFIDENCE_LABEL} HPD)</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {contextsWithModeledSpan.map((g) => {
                  const startEnvelope = hpdEnvelopeFromHistogram(g.modeled_start!.probability_distribution, confidence)
                  const endEnvelope = hpdEnvelopeFromHistogram(g.modeled_end!.probability_distribution, confidence)
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
                <InputLabel>HPD Confidence</InputLabel>
                <Select
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
            diagnostics is that nobody should have to go looking. */}
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
              {flaggedDates
                .map((f) => `${f.name} (${f.concern.reasons.join(', ')})`)
                .join(' · ')}
            </Typography>
          </Alert>
        )}

        <TableContainer sx={SCROLL_HINT_SX}>
          {/* tabular-nums so every numeric column -- ages, percentages,
              agreement/outlier diagnostics -- lines up digit-for-digit down
              the column instead of drifting with each digit's own width.
              Inherited onto every cell including the header labels and the
              Suspect/Check/Assigned chips, which is harmless: none of that
              text has digits whose alignment matters. */}
          <Table size="small" sx={{ fontVariantNumeric: 'tabular-nums' }}>
            <caption style={visuallyHidden}>
              {isBayesian ? 'Modelled' : 'Calibrated'} calendar age for every date in this
              analysis, oldest first, at {CONFIDENCE_LABEL} HPD.
            </caption>
            <TableHead>
              <TableRow sx={{ backgroundColor: 'background.muted' }}>
                <TableCell sx={{ ...stickyNameCell('#f4ede7'), zIndex: 3 }}>
                  {structureRows.length > 0 ? 'Structure / Sample' : 'Sample'}
                </TableCell>
                {showMean && <TableCell align="right">Posterior Mean</TableCell>}
                {showMean && <TableCell align="right">Posterior Std (yr)</TableCell>}
                {showMedian && <TableCell align="right">Posterior Median</TableCell>}
                {isBayesian && <TableCell align="right">Unmodelled Calibration</TableCell>}
                <TableCell align="right">
                  {isBayesian ? 'Posterior' : 'Calibrated'} {CONFIDENCE_LABEL} HPD Range
                </TableCell>
                {isBayesian && (
                  /* The explanation used to be a Tooltip wrapping this cell,
                     which set the whole paragraph as the header's accessible
                     name -- so a screen reader announced sixty words in place
                     of "Diagnostics" against every data cell in the column,
                     while a keyboard or touch user got nothing at all (the
                     <th> was not focusable, and there is no hover on a
                     tablet). A focusable button beside the label reaches all
                     three and the column keeps its own name. The persistent
                     key under the table is what a first-time reader needs. */
                  <TableCell align="right">
                    Diagnostics
                    <Tooltip title={DIAGNOSTICS_HELP} describeChild>
                      <IconButton
                        size="small"
                        aria-label="What the diagnostics columns mean"
                        sx={{
                          ml: 0.5,
                          p: 0.25,
                          verticalAlign: 'baseline',
                          minWidth: 24,
                          minHeight: 24,
                          my: '-3px',
                        }}
                      >
                        <HelpOutlineIcon sx={{ fontSize: 15 }} />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                )}
              </TableRow>
            </TableHead>
            <TableBody>
              {structureRows.length > 0
                ? visibleStructureRows.map((row, idx) => (
                    <StructureTableRow
                      // sampleId first, elementId only as the fallback. A
                      // date row's elementId is the PHASE it sits in, which
                      // every date in that phase shares -- so preferring it
                      // gave all of them one key. React's warning about that
                      // says duplicate keys may leave children "duplicated
                      // and/or omitted", meaning this table could silently
                      // drop or repeat a date. Element rows carry
                      // sampleId: null, so they still key on elementId.
                      key={`${row.kind}-${row.sampleId ?? row.elementId ?? idx}`}
                      row={row}
                      result={row.sampleId ? resultBySampleId.get(row.sampleId) : undefined}
                      isBayesian={isBayesian}
                      showMean={showMean}
                      showMedian={showMedian}
                      confidence={confidence}
                      confidenceLabel={CONFIDENCE_LABEL}
                      provenance={provenance}
                      dateFormat={dateFormat}
                      // A date row's elementId is its PARENT phase's id (see
                      // the comment on the key above), so these must also
                      // check row.kind -- otherwise every date would inherit
                      // its own phase's collapsibility and grow a disclosure
                      // control that actually toggles its parent.
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
                    const modelledEnvelope = hpdEnvelopeFromHistogram(result.probability_distribution, confidence)
                    return (
                      <TableRow key={result.sample_id}>
                        <TableCell sx={stickyNameCell('#ffffff')}>
                          {result.sample_name || `Sample ${idx + 1}`}
                        </TableCell>
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
                        {isBayesian && (
                          <TableCell align="right" sx={NUMERIC_CELL_SX}>
                            {unmodelledRanges.length > 0 ? formatHPDRanges(unmodelledRanges, dateFormat) : 'N/A'}
                          </TableCell>
                        )}
                        <TableCell align="right" sx={NUMERIC_CELL_SX}>
                          {modelledEnvelope ? formatCalendarRange(modelledEnvelope[0], modelledEnvelope[1], dateFormat) : 'N/A'}
                          {modelledEnvelope && (
                            <CopyCitation
                              text={citationLine(
                                result.sample_name || `Sample ${idx + 1}`,
                                formatCalendarRange(modelledEnvelope[0], modelledEnvelope[1], dateFormat),
                                CONFIDENCE_LABEL,
                                provenance
                              )}
                            />
                          )}
                        </TableCell>
                      </TableRow>
                    )
                  })}
            </TableBody>
          </Table>
        </TableContainer>
        {/* The key a first-time reader needs, present without a hover: the
            two diagnostics letters, spelled out once under the table they
            label. Same words as the header's help control, from the one
            constant, so the two cannot drift. */}
        {isBayesian && (
          <Typography variant="caption" color="textSecondary" component="p" sx={{ mt: 1.5 }}>
            <strong>A</strong> = agreement index — how well the modelled date still fits its
            unmodelled calibration; below 60 is worth re-examining.{' '}
            <strong>O</strong> = the probability the date is an outlier.
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
            </ToggleButtonGroup>
          </Box>
          <Typography variant="body2" color="textSecondary" sx={{ mt: 1 }}>
            {plotView === 'stacked'
              ? `One row per ${
                  isBayesian ? 'date and modelled transition' : 'date'
                }, oldest first, on a shared calendar axis — the same order as the table above. Each row is scaled to its own peak, so heights compare precision, not probability.`
              : `One plot per ${
                  isBayesian ? 'date and modelled transition' : 'date'
                }, oldest first — the same order as the table above.`}
          </Typography>

          {/* Above the plots rather than beside the table's controls, so the
              switch that hides them isn't below everything it hides. Only shown
              when there are boundary plots to hide. */}
          {boundaryPlotCount > 0 && (
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
              sx={{ mt: 1, ml: 2 }}
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

          {plotView === 'stacked' ? (
            <Box sx={{ mt: 2 }}>
              <StackedPosteriorPlot
                series={visiblePlots}
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
          // Oldest first, the same order the table and the plots use -- so a
          // well-ordered chronology reads down the ordering matrix's diagonal
          // rather than being scattered across it.
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
