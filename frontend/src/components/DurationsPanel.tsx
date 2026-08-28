// Durations and differences on the Results page: how long each sequence,
// phase and set of dated events lasted, and how far apart the phases sit.
//
// Everything here is a LENGTH OF TIME in years, never a calendar age, so
// nothing goes through formatCalendarAge or the BC/AD conversion the rest of
// the page applies -- see DurationSummary in types/index.ts for why the backend
// gives these their own field names.
//
// Two tables rather than one: a duration has a name and a length, a
// difference has two names and a sign, and forcing them through shared
// columns would leave half of every row blank.
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Box,
  Button,
  Collapse,
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
import { alpha, useTheme } from '@mui/material/styles'
import { useElementTypeColors } from '@/theme/diagramColors'
import useMediaQuery from '@mui/material/useMediaQuery'
import { visuallyHidden } from '@mui/utils'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import { hpdEnvelopeFromHistogram } from '@/utils/hpd'
import OrderingMatrix, { orderingSentence, type OrderingAxisEntry } from '@/components/OrderingMatrix'
import type { DifferenceRecord, DurationRecord, DurationSummary, IntervalsBlock } from '@/types'

const NUMERIC_CELL_SX = { whiteSpace: 'nowrap' } as const

/**
 * What a shared-boundary pair says in place of a figure.
 *
 * Its gap and overlap probability are both exactly zero BY CONSTRUCTION — one
 * phase's end IS the next one's start — and `none` is the wrong word for the
 * second, meaning "separated at 2 sigma" everywhere else here. The CSV and PDF
 * print the same phrase (intervals.SHARED_BOUNDARY_TEXT).
 */
export const SHARED_BOUNDARY_TEXT = 'shares a transition'
const SHARED_BOUNDARY_HELP =
  'These two phases share a Boundary, so the model gives them no gap and no overlap to ' +
  'measure \u2014 one phase\u2019s end is the other\u2019s start, by construction rather than by inference.'

/** The readout beside the matrix never shrinks below this; the grid gets the
 *  rest. A pair's figures wrapping to five lines to buy the grid 40px is a bad
 *  trade both ways. */
const READOUT_MIN_WIDTH = 300

// What each duration is of, in the same words the model tree uses for the
// elements themselves.
const DURATION_LABELS: Record<DurationRecord['kind'], string> = {
  sequence_duration: 'Sequence',
  phase_duration: 'Phase',
  sample_duration: 'Dated events',
}

/**
 * The element-type ink for a duration's kind, matching the results table's
 * type label and the Harris matrix. `sample_duration` gets none: "Dated
 * events" is the span the dates cover, not an element type, and colouring it
 * as one would make it look like a fourth kind of model element.
 */
function useDurationKindColor() {
  const colors = useElementTypeColors()
  return (kind: DurationRecord['kind']): string | undefined =>
    kind === 'sequence_duration'
      ? colors.sequence.ink
      : kind === 'phase_duration'
      ? colors.phase.ink
      : undefined
}

const DURATION_HELP: Record<DurationRecord['kind'], string> = {
  sequence_duration:
    'From the first Boundary of this sequence to its last — how long the whole of it was in use.',
  phase_duration:
    'The interval this phase’s two Boundaries enclose. Longer than the span of its dated events, because those events are a sample of the phase rather than the whole of it.',
  sample_duration:
    'From the oldest to the youngest dated event in this phase — what the dates themselves cover, with nothing inferred beyond them.',
}

/** A length of time, rounded to the year: the reported step is coarser anyway,
 *  and a decimal would claim precision the draws do not have. */
function years(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return 'N/A'
  return `${Math.round(value).toLocaleString()} yr`
}

/** An envelope as "0 to 540 yr", recomputed at whatever confidence the page is
 *  showing, through the same re-selection the results table uses.
 *
 *  With no distribution to re-select from, the stored figure is reported WITH
 *  ITS OWN LEVEL attached rather than bare: the column heading names the
 *  selected confidence, and a 95.4% figure sitting silently under a "Duration
 *  (99.7%)" heading is what this panel must not do. */
function envelope(summary: DurationSummary, confidence: number): string {
  const found = hpdEnvelopeFromHistogram(summary.distribution, confidence)
  if (found) {
    return `${Math.round(found[0]).toLocaleString()} to ${years(found[1])}`
  }
  const storedIsOneSigma = confidence <= 0.7
  const stored = storedIsOneSigma ? summary.hpd_68 : summary.hpd_95
  if (!stored) return 'N/A'
  const storedLabel = storedIsOneSigma ? '68.3%' : '95.4%'
  return `${Math.round(stored[0]).toLocaleString()} to ${years(stored[1])} (${storedLabel})`
}

/**
 * How far two things are from being separable in time, as a word and a bar.
 *
 * Not a success/warning/error chip: those are for genuine state, and an overlap
 * is a measurement, so colouring it as one would make a sound model look
 * broken. One hue at a strength tracking the number, as the HPD bands are —
 * one quantity at five strengths, not five categories.
 */
function OverlapMeter({ record }: { record: DifferenceRecord }) {
  const theme = useTheme()
  const share = Math.max(0, Math.min(1, record.overlap))
  const help =
    record.kind === 'phase_difference'
      ? `${(share * 100).toFixed(0)}% probability that the two modelled intervals intersect.`
      : `${(share * 100).toFixed(0)}% — how far the model is from being able to say which of the two came first. 100% is a coin flip.`
  return (
    <Tooltip title={help}>
      <Box sx={{ width: 104 }}>
        {/* Lower case, as in the matrix legend. The same five words were
            Capitalized here and lower case there, so a reader matching a
            square to its name was matching two spellings. */}
        <Typography variant="body2">
          {record.severity}{' '}
          <Typography component="span" variant="body2" color="textSecondary">
            ({(share * 100).toFixed(0)}%)
          </Typography>
        </Typography>
        <Box
          aria-hidden
          sx={{
            mt: 0.5,
            height: 4,
            borderRadius: 2,
            backgroundColor: alpha(theme.palette.primary.main, 0.12),
          }}
        >
          <Box
            sx={{
              height: '100%',
              width: `${share * 100}%`,
              borderRadius: 2,
              backgroundColor: alpha(theme.palette.primary.main, 0.35 + 0.55 * share),
            }}
          />
        </Box>
      </Box>
    </Tooltip>
  )
}

interface Props {
  intervals: IntervalsBlock
  confidence: number
  confidenceLabel: string
  showMean: boolean
  showMedian: boolean
  /** The analysis's dated events, oldest first, as the results table and plots
   *  order them. Only the ordering matrix uses it; the panel renders without. */
  dateAxis?: OrderingAxisEntry[]
}

export default function DurationsPanel({
  intervals,
  confidence,
  confidenceLabel,
  showMean,
  showMedian,
  dateAxis = [],
}: Props) {
  const theme = useTheme()
  const durationKindColor = useDurationKindColor()
  // The same `sm` the rest of the app collapses at, so this view changes shape
  // where everything else already does.
  const narrow = useMediaQuery(theme.breakpoints.down('sm'))
  // Matches the Stack's own md breakpoint below: above it the matrix and its
  // readout share a row, so the grid's budget is the row minus the readout.
  const sideBySide = useMediaQuery(theme.breakpoints.up('md'))
  /**
   * How wide the matrix may draw.
   *
   * Measured on the flex ROW, not the matrix's own wrapper: the wrapper is a
   * `flex-shrink: 0` item, so its width IS its content's width and observing it
   * would have the grid size itself from its own legend. The row is fixed by
   * the card, so nothing the grid renders can move it.
   */
  const rowRef = useRef<HTMLDivElement | null>(null)
  const [rowWidth, setRowWidth] = useState(0)
  useEffect(() => {
    const node = rowRef.current
    if (!node) return
    const observer = new ResizeObserver(([entry]) => setRowWidth(entry.contentRect.width))
    observer.observe(node)
    setRowWidth(node.getBoundingClientRect().width)
    return () => observer.disconnect()
  }, [])
  const { durations, differences } = intervals
  const [selectedPairId, setSelectedPairId] = useState<string | null>(null)
  const [pairsOpen, setPairsOpen] = useState(false)

  // The two kinds never appear together: phases come from a model, dates from
  // a group calibration, and an analysis is one or the other.
  const comparingDates = differences[0]?.kind === 'date_difference'
  // "Separation", not "Gap or overlap": the neighbouring column is headed
  // "Overlap" and holds a probability, so the word would mean YEARS in one
  // column and a probability in the next. The neighbour keeps it, its five
  // grades being degrees of overlap. Sign carries the distinction here, as the
  // line above the table says: positive is a gap, negative an overlap.
  const differenceHeading = comparingDates ? 'Difference' : 'Separation'

  // The matrix is only meaningful over a COMPLETE set of pairs. Past MAX_PAIRS
  // the backend reports consecutive pairs, which is a chain -- a list by
  // nature, and a diagonal line of squares in an otherwise empty grid.
  const axis = comparingDates ? dateAxis.filter((e) => e.id) : []
  const isAllPairs = axis.length >= 2 && differences.length === (axis.length * (axis.length - 1)) / 2
  // Below five dates the table is one screen and reads faster than a grid.
  const showMatrix = comparingDates && isAllPairs && axis.length >= 5

  // The pairs the model cannot put in order -- the finding a 55-row table
  // buries among its near-identical neighbours.
  const unorderable = useMemo(
    () => differences.filter((d) => d.severity === 'substantial' || d.severity === 'extensive'),
    [differences]
  )
  const selectedPair = differences.find((d) => d.id === selectedPairId) ?? null

  return (
    <>
      {/* Two cards, not one. They answer different questions -- how long
          things lasted, and how far apart they sit -- and every other section
          on this page is its own Paper with its own heading. A single
          "Durations and Differences" heading sat above the durations table and
          left the differences table underneath it with no heading of its own,
          under a title that was half about something else.

          Either can be absent: a group calibration has no phases and so no
          durations, and a model can be run without pairwise differences. */}
      {durations.length > 0 && (
        <Paper sx={{ p: 3, mb: 3 }}>
          <Typography variant="h6" component="h2" gutterBottom>
            Durations
          </Typography>
          <Typography variant="body2" color="textSecondary" sx={{ mb: 2 }}>
            How long each sequence, phase and set of dated events lasted, in years. Estimated from{' '}
            {intervals.draws.toLocaleString()}{' '}
            {intervals.independent ? 'independent' : 'sampled'} joint draws.
          </Typography>
        {/* maxHeight below so stickyHeader has a scroll container of its own.
            A sticky header with no scrolling ancestor sticks to the VIEWPORT
            instead, and slides under the app bar. Short tables never reach
            this height, so nothing scrolls that does not need to. */}
        {durations.length > 0 && (
          <TableContainer sx={{ mb: differences.length > 0 ? 3 : 0, maxHeight: { xs: 'none', md: '70vh' } }}>
            <Table stickyHeader size="small" sx={{
                fontVariantNumeric: 'tabular-nums',
                // Banded on the CELLS, not the row: a sticky <tr> does not
                // work, and a header scrolling away over 33 rows of pairs is
                // the defect the results table fixed.
                '& thead th': { backgroundColor: 'background.muted', top: { xs: 'auto', md: 0 } },
              }}>
                <caption style={visuallyHidden}>
                  How long each sequence, phase and set of dated events lasted, in years.
                </caption>
              <TableHead>
                <TableRow>
                  <TableCell>What</TableCell>
                  <TableCell>Name</TableCell>
                  {showMean && <TableCell align="right">Mean</TableCell>}
                  {showMedian && <TableCell align="right">Median</TableCell>}
                  <TableCell align="right">Duration ({confidenceLabel})</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {durations.map((record) => (
                  <TableRow key={record.id}>
                    <TableCell>
                      <Tooltip title={DURATION_HELP[record.kind]}>
                        {/* Same fact, same colour as the results table's
                            type label: a Phase is Phase green wherever the
                            page names one. */}
                        <Box component="span" sx={{ color: durationKindColor(record.kind), fontWeight: 500 }}>
                          {DURATION_LABELS[record.kind] ?? record.kind}
                        </Box>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      {record.name}
                      {record.kind === 'sample_duration' && record.dates ? (
                        <Typography component="span" variant="body2" color="textSecondary">
                          {' '}
                          ({record.dates} dates)
                        </Typography>
                      ) : null}
                    </TableCell>
                    {showMean && (
                      <TableCell align="right" sx={NUMERIC_CELL_SX}>
                        {years(record.years.mean)}
                      </TableCell>
                    )}
                    {showMedian && (
                      <TableCell align="right" sx={NUMERIC_CELL_SX}>
                        {years(record.years.median)}
                      </TableCell>
                    )}
                    <TableCell align="right" sx={NUMERIC_CELL_SX}>
                      {envelope(record.years, confidence)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
        </Paper>
      )}

      {differences.length > 0 && (
        <Paper sx={{ p: 3, mb: 3 }}>
          <Typography variant="h6" component="h2" gutterBottom>
            Differences
          </Typography>
        {differences.length > 0 && (
          <>

            {comparingDates && (
  
              <Typography variant="body2" sx={{ mb: 0.5 }}>
                {/* Only promise the grid when the grid renders. `showMatrix`
                    needs >=5 dates AND a complete pair set, and past MAX_PAIRS
                    (300, i.e. 26 dates) the backend returns consecutive pairs
                    instead -- so this sentence pointed at squares that were not
                    there at both ends of the range. */}
                {showMatrix && (
                  <>
                    Use the matrix below to identify clusters of overlapping and non-overlapping
                    dates. Squares are ordered oldest first along both axes. <br />
                  </>
                )}
                {unorderable.length === 0 ? (
                  <>It is possible to order all {differences.length} pairs.</>
                ) : (
                  <>
                    {' '}
                    <strong>
                      {unorderable.length} of {differences.length}
                    </strong>{' '}
                    pairs overlap, and cannot be placed in order
                    {unorderable.length <= 4 ? (
                      <>: {unorderable.map((d) => `${d.from_name} and ${d.to_name}`).join('; ')}.</>
                    ) : (
                      <> — the strongest squares below.</>
                    )}
                  </>
                )}
              </Typography>
            )}

            {showMatrix && (
              <>
                <Typography variant="body2" color="textSecondary" sx={{ mb: 1.5 }}>
                        Results are estimated from {intervals.draws.toLocaleString()}{' '}
          {intervals.independent ? 'independent' : 'sampled'} joint draws.
                </Typography>
                {/* Side by side above md: the grid caps its cells at 56px, which
                    left a quarter of the card empty to its right while the
                    readout it feeds sat underneath. Putting the readout in that
                    space uses the width and ties the number to the square that
                    produced it. Stacks below md, where the grid needs the room. */}
                <Stack
                  ref={rowRef}
                  direction={{ xs: 'column', md: 'row' }}
                  spacing={{ xs: 0, md: 3 }}
                  alignItems="flex-start"
                >
                  <Box sx={{ flexShrink: 0, maxWidth: '100%' }}>
                    <OrderingMatrix
                      differences={differences}
                      axis={axis}
                      selectedId={selectedPairId}
                      onSelect={(record) => setSelectedPairId(record ? record.id : null)}
                      availableWidth={
                        rowWidth > 0
                          ? sideBySide
                            ? Math.max(240, rowWidth - READOUT_MIN_WIDTH - 24)
                            : rowWidth
                          : 0
                      }
                    />
                  </Box>
                {/* Detail on demand: the selected pair's own figures, in the
                    same words and at the same confidence as everything else. */}
                {/* aria-live: selecting a square rewrites this box 40px away
                    from the grid, and a screen-reader user hears the cell's own
                    label but never the figures that appear because of it. The
                    years difference exists nowhere else on the page for a pair
                    the reader has singled out. "polite" because it follows a
                    deliberate action rather than interrupting one. */}
                <Box
                  aria-live="polite"
                  sx={{
                    mb: 3,
                    py: 1.5,
                    px: 2,
                    minHeight: 68,
                    flexGrow: 1,
                    alignSelf: { xs: 'stretch', md: 'flex-start' },
                    width: { xs: '100%', md: 'auto' },
                    borderRadius: 1,
                    border: 1,
                    borderColor: 'divider',
                  }}
                >
                  {selectedPair ? (
                    <>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {selectedPair.from_name} → {selectedPair.to_name}
                      </Typography>
                      <Typography variant="body2" color="textSecondary">
                        {differenceHeading} {' '} of {' '}
                        <Box component="span" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                          {envelope(selectedPair.years, confidence)} {' '} (at {confidenceLabel} confidence)
                        </Box>
                        . {orderingSentence(selectedPair)}
                      </Typography>
                    </>
                  ) : (
                    <Typography variant="body2" color="textSecondary">
                      Select a square to read that pair’s difference and how confidently the
                      model orders it.
                    </Typography>
                  )}
                </Box>
                </Stack>
                <Button
                  size="small"
                  aria-expanded={pairsOpen}
                  aria-controls="all-pairs-table"
                  onClick={() => setPairsOpen((open) => !open)}
                  startIcon={
                    <ExpandMoreIcon
                      sx={{
                        transform: pairsOpen ? 'rotate(180deg)' : 'none',
                        transition: 'transform 160ms ease-out',
                      }}
                    />
                  }
                  sx={{ mb: 1 }}
                >
                  {pairsOpen ? 'Hide' : 'Show'} all {differences.length} pairs
                </Button>
              </>
            )}

            {/* The id lives on a wrapper that is always in the DOM, not on the
                Collapse: unmountOnExit removes its child while closed, which
                left aria-controls pointing at nothing exactly when the button
                said "Show". Keeping 55 rows mounted to satisfy the reference
                would defeat the disclosure. */}
            <Box id="all-pairs-table">
            <Collapse in={!showMatrix || pairsOpen} unmountOnExit>
            <Typography variant="body2" color="textSecondary" sx={{ mb: 1 }}>
              {comparingDates
                ? 'A positive difference means the first date is the older of the two. “Overlap” grades how far the model is from being able to say which came first.'
                : 'Separation is positive for a gap between the two phases and negative for an overlap. “Overlap” grades the probability that their modelled intervals intersect. Two phases either side of one shared Boundary have neither measured, because one phase’s end is the next one’s start.'}
            </Typography>
            {/* Below sm the same rows are stacked cards rather than a grid.
                At 375px this table measured 452px inside a 295px container: the
                value clipped mid-unit ("-60 to 130 y", the r cut off) and the
                Overlap column -- the actual finding -- sat entirely off screen
                with nothing to say it was there. A pair is three facts, which
                stacks honestly; forcing them into columns at that width does
                not. */}
            {narrow ? (
              <Stack spacing={1.5} sx={{ mb: 1 }}>
                {differences.map((record) => (
                  <Box
                    key={record.id}
                    sx={{ p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1 }}
                  >
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {record.from_name} → {record.to_name}
                    </Typography>
                    <Typography
                      variant="body2"
                      color="textSecondary"
                      sx={{ mt: 0.5, fontVariantNumeric: 'tabular-nums' }}
                    >
                      {record.shared_boundary ? (
                        SHARED_BOUNDARY_TEXT
                      ) : (
                        <>
                          {differenceHeading} ({confidenceLabel}){' '}
                          {envelope(record.years, confidence)}
                        </>
                      )}
                    </Typography>
                    {showMean && (
                      <Typography
                        variant="body2"
                        color="textSecondary"
                        sx={{ fontVariantNumeric: 'tabular-nums' }}
                      >
                        Mean {years(record.years.mean)}
                      </Typography>
                    )}
                    {record.shared_boundary ? (
                      <Typography variant="body2" color="textSecondary" sx={{ mt: 0.5 }}>
                        {SHARED_BOUNDARY_HELP}
                      </Typography>
                    ) : (
                      <Box sx={{ mt: 1 }}>
                        <Typography variant="caption" color="textSecondary">
                          Overlap
                        </Typography>
                        <OverlapMeter record={record} />
                      </Box>
                    )}
                  </Box>
                ))}
              </Stack>
            ) : (
            <TableContainer sx={{ maxHeight: { xs: 'none', md: '70vh' } }}>
              <Table stickyHeader size="small" sx={{
                fontVariantNumeric: 'tabular-nums',
                // Banded on the CELLS, not the row: a sticky <tr> does not
                // work, and a header scrolling away over 33 rows of pairs is
                // the defect the results table fixed.
                '& thead th': { backgroundColor: 'background.muted', top: { xs: 'auto', md: 0 } },
              }}>
                  <caption style={visuallyHidden}>
                    Every pair compared: how many years apart they sit and how confidently the
                    model can order them.
                  </caption>
                <TableHead>
                  <TableRow>
                    <TableCell>{comparingDates ? 'Date' : 'Phase'}</TableCell>
                    <TableCell>Compared with</TableCell>
                    {showMean && <TableCell align="right">Mean</TableCell>}
                    <TableCell align="right">
                      {differenceHeading} ({confidenceLabel})
                    </TableCell>
                    <TableCell>Overlap</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {differences.map((record) => (
                    <TableRow key={record.id}>
                      <TableCell>{record.from_name}</TableCell>
                      <TableCell>{record.to_name}</TableCell>
                      {showMean && (
                        <TableCell align="right" sx={NUMERIC_CELL_SX}>
                          {years(record.years.mean)}
                        </TableCell>
                      )}
                      <TableCell align="right" sx={NUMERIC_CELL_SX}>
                        {record.shared_boundary ? (
                          <Tooltip title={SHARED_BOUNDARY_HELP}>
                            <Typography variant="body2" color="textSecondary">
                              {SHARED_BOUNDARY_TEXT}
                            </Typography>
                          </Tooltip>
                        ) : (
                          envelope(record.years, confidence)
                        )}
                      </TableCell>
                      {/* An em dash, not a severity word. The probability IS
                          exactly zero, but `none` means "separated at 2 sigma"
                          in every other row, so printing it here told the
                          reader the model had pushed two phases apart when it
                          had only been given no room to measure them. A dash
                          says "not measured", and the cell beside it says
                          why. */}
                      <TableCell>
                        {record.shared_boundary ? (
                          <Tooltip title={SHARED_BOUNDARY_HELP}>
                            <Typography variant="body2" color="textSecondary" aria-label="not measured">
                              &mdash;
                            </Typography>
                          </Tooltip>
                        ) : (
                          <OverlapMeter record={record} />
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
            )}
            </Collapse>
            </Box>
          </>
        )}
        {intervals.note && (
          <Typography variant="body2" color="textSecondary" sx={{ mt: 2 }}>
            {intervals.note}
          </Typography>
        )}
        </Paper>
      )}
    </>
  )
}
