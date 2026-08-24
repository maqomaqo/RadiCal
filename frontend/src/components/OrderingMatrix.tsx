// All-pairs date differences as a triangular matrix, for the question the
// 55-row table made the reader answer by scanning: WHICH of these dates can
// the model actually put in order, and which can it not separate?
//
// A list of every pair is the wrong shape for that. Pairs grow as n(n-1)/2 --
// 11 dates make 55 rows, 24 make 276 -- while the matrix grows as one cell per
// pair in a grid the eye reads in one pass. What a list structurally cannot
// show is the finding that matters: a CLUSTER of dates none of which can be
// ordered against each other appears here as a dark block, and in a table it
// is thirty rows scattered across four screens.
//
// The cell encodes `overlap` -- the probability the model cannot say which of
// the two came first, 1 being a coin flip. Strong means unsettled, which is
// the same direction OverlapMeter's bar runs in the table below, deliberately:
// two marks on one page encoding one quantity in opposite directions would be
// worse than either alone. So the eye is drawn to the trouble, not away from
// it. Direction -- WHICH one is older, when the model can tell -- is not in
// the colour; it is in the cell's label and in the detail line, because it is
// only meaningful once the ordering is settled enough to have a direction.
//
// Beyond MAX_PAIRS the backend reports consecutive pairs only, which is a
// chain rather than a matrix, so this never renders for that case -- see
// services/intervals.py and `intervals.note`.
import { useMemo, useRef, useState } from 'react'
import { Box, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import { visuallyHidden } from '@mui/utils'
import { WARM_INK } from '@/theme/diagramColors'
import type { DifferenceRecord } from '@/types'

/** An entry on the matrix's axes: one dated event, in chronological order. */
export interface OrderingAxisEntry {
  id: string
  name: string
}

interface Props {
  /** date_difference records only; phase differences ask a different question. */
  differences: DifferenceRecord[]
  /** Oldest first -- the same order the results table and the plots use, so a
   *  well-ordered chronology reads as a clean triangle rather than as noise. */
  axis: OrderingAxisEntry[]
  selectedId: string | null
  onSelect(record: DifferenceRecord | null): void
  /**
   * How much horizontal room the grid may take, in px, measured by the parent.
   *
   * Passed in rather than measured here, and that is the whole point. This
   * component used to observe its own wrapper -- but the wrapper is a
   * `flex-shrink: 0` item with `width: auto`, so it shrink-to-fits its widest
   * child, and its widest child is this component's own legend. Measured:
   * wrapper 441.078px, legend 441px. The grid was therefore sized by its own
   * key rather than by the 1104px row it sits in, which capped an 11-date
   * matrix at 34px cells and 436px -- the exact defect the observer was added
   * to fix, reintroduced by the layout that was supposed to showcase it.
   *
   * A budget handed down cannot close that loop: nothing this component
   * renders can change the number it is given.
   */
  availableWidth: number
}

/** How much room the rotated column labels get. Sample codes are short
 *  ("NZA-38894", "WK50104"); anything longer is clipped rather than allowed to
 *  push the grid taller than the matrix it labels. */
const LABEL_GUTTER = 96

/**
 * The five overlap bands, as the five strengths they are drawn at.
 *
 * WARM INK, not a hue. Every hue in this app is already a claim about what
 * something IS -- primary blue is a Sequence, green a Phase, coral a Boundary,
 * violet a Hiatus -- and success/warning/error are barred here for the reason
 * OverlapMeter states: an overlap is a measurement, not a status. But overlap
 * is not an object type at all. It is the ABSENCE of information: how much the
 * model cannot say about which of two dates came first. Ink reads as fog over
 * the reading rather than as another category beside it, it cannot collide
 * with an element colour, and it survives a colour-vision deficiency that
 * would flatten a hue ramp.
 *
 * It also frees the primary blue to mean one thing on this grid -- selection
 * and focus -- instead of competing with the data it is drawn on top of.
 *
 * DISCRETE, not a continuous ramp. services/intervals.py bins overlap into
 * exactly these five named bands, and a continuous alpha put 24 of 55 cells
 * inside two decades of each other: 0.66, 0.718 and 0.72 for overlaps of 83%,
 * 91% and 92%, indistinguishable by eye. Five steps mean a reader who has been
 * told "substantial" can actually find one, and the legend becomes a real key
 * rather than an unlabelled gradient.
 */
const SEVERITY_STEPS: ReadonlyArray<{
  severity: DifferenceRecord['severity']
  label: string
  strength: number
}> = [
  { severity: 'none', label: 'none', strength: 0.06 },
  { severity: 'slight', label: 'slight', strength: 0.2 },
  { severity: 'moderate', label: 'moderate', strength: 0.38 },
  { severity: 'substantial', label: 'substantial', strength: 0.58 },
  { severity: 'extensive', label: 'extensive', strength: 0.8 },
]

const STRENGTH_BY_SEVERITY = new Map(SEVERITY_STEPS.map((s) => [s.severity, s.strength]))

/** Last resort for a severity word this build does not know: pick the step by
 *  the overlap it was derived from. Monotonic in overlap, which is the only
 *  property that has to hold for the cell not to lie about its direction. */
function strengthFromOverlap(overlap: number): number {
  const clamped = Math.max(0, Math.min(1, overlap))
  const index = Math.min(SEVERITY_STEPS.length - 1, Math.floor(clamped * SEVERITY_STEPS.length))
  return SEVERITY_STEPS[index].strength
}

/**
 * Cells never go below this or above it, whatever the container allows.
 *
 * The floor is WCAG 2.5.8's 24px target minimum plus a little, not a
 * space-filling minimum. At 375px a 10-date grid shrank to 20x20 to avoid
 * scrolling -- but clicking a square is the only way to read a pair, so a grid
 * that fits and cannot be tapped is worse than one that scrolls and can. Past
 * this floor the container scrolls, which is what its scroll hint is for.
 */
const CELL_MIN = 28
const CELL_MAX = 56

/** Reserved for the row labels down the left. Sample codes are short. */
const ROW_LABEL_WIDTH = 72

/** Unordered key for a pair, so a record can be found from either direction. */
function pairKey(a: string, b: string): string {
  return a < b ? `${a}|${b}` : `${b}|${a}`
}

/** The overlap as a sentence, in the panel's own vocabulary. */
export function orderingSentence(record: DifferenceRecord): string {
  const pct = (record.overlap * 100).toFixed(0)
  if (record.older_probability == null) {
    return `${pct}% probability the two modelled intervals intersect.`
  }
  const older = record.older_probability
  const [name, share] =
    older >= 0.5 ? [record.from_name, older] : [record.to_name, 1 - older]
  return `These two dates overlap ${pct}%. It is ${(share * 100).toFixed(0)}% likely ${name} is the older.`
}

export default function OrderingMatrix({
  differences,
  axis,
  selectedId,
  onSelect,
  availableWidth,
}: Props) {
  const theme = useTheme()
  // One tab stop for the whole grid, then arrow keys within it. A cell per
  // tab stop would put 276 of them between the matrix and whatever follows.
  const [focusCell, setFocusCell] = useState<[number, number]>([0, 1])
  const gridRef = useRef<HTMLTableElement | null>(null)

  const byPair = useMemo(() => {
    const map = new Map<string, DifferenceRecord>()
    differences.forEach((d) => map.set(pairKey(d.from_id, d.to_id), d))
    return map
  }, [differences])

  const recordAt = (row: number, col: number): DifferenceRecord | undefined =>
    byPair.get(pairKey(axis[row].id, axis[col].id))

  const columns = Math.max(axis.length - 1, 1)
  const cell = useMemo(() => {
    // The row-label gutter and the 2px border-spacing each column carries.
    const forGrid = availableWidth - ROW_LABEL_WIDTH - columns * 2
    if (!(forGrid > 0)) return CELL_MIN
    return Math.max(CELL_MIN, Math.min(CELL_MAX, Math.floor(forGrid / columns)))
  }, [availableWidth, columns])

  /**
   * Move focus within the triangle. A cell exists only where `col > row`, so
   * each direction has its own boundary rather than one shared bounds check:
   * the old version scanned forward past the diagonal looking for a landing
   * cell, which meant ArrowLeft and ArrowDown silently did nothing at the
   * diagonal instead of stopping at it, and carried a branch that could not
   * be reached.
   */
  const move = (dRow: number, dCol: number) => {
    const [row, col] = focusCell
    const nextRow = row + dRow
    const nextCol = col + dCol
    if (nextRow < 0 || nextRow > axis.length - 2) return
    if (nextCol > axis.length - 1) return
    if (nextCol <= nextRow) return
    setFocusCell([nextRow, nextCol])
    gridRef.current
      ?.querySelector<HTMLElement>(`[data-cell="${nextRow}-${nextCol}"]`)
      ?.focus()
  }

  return (
    <Box sx={{ mb: 2 }}>
      {/* The scroll hint the results table already had, on the artifact that
          clips harder: at 375px this grid is wider than its container, and
          without it the far columns simply are not there. */}
      <TableContainer
        sx={{
          maxWidth: '100%',
          backgroundImage:
            'linear-gradient(to left, #fff 40%, rgba(255,255,255,0)),' +
            'radial-gradient(farthest-side at 100% 50%, rgba(43,35,32,0.16), rgba(43,35,32,0))',
          backgroundPosition: 'right center, right center',
          backgroundSize: '32px 100%, 14px 100%',
          backgroundRepeat: 'no-repeat',
          backgroundAttachment: 'local, scroll',
        }}
      >
        <Table
          ref={gridRef}
          size="small"
          sx={{ width: 'auto', borderCollapse: 'separate', borderSpacing: '2px' }}
        >
          <caption style={visuallyHidden}>
            Ordering matrix: every pair of dated events, oldest first along both axes. Each
            square states how confidently the model orders that pair.
          </caption>
          <TableHead>
            <TableRow>
              <TableCell sx={{ border: 0, p: 0 }} />
              {/* The oldest date has no column and the youngest has no row: in
                  an upper triangle both are always empty, and leaving them in
                  hangs two labels off the grid with nothing beside them. */}
              {axis.slice(1).map((entry) => (
                <TableCell
                  key={entry.id}
                  scope="col"
                  sx={{
                    border: 0,
                    p: 0,
                    width: cell,
                    minWidth: cell,
                    maxWidth: cell,
                    height: LABEL_GUTTER,
                    position: 'relative',
                    verticalAlign: 'bottom',
                    fontSize: 11,
                    fontWeight: 400,
                    color: 'text.secondary',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {/* Absolutely positioned, because a rotation does not shrink
                      the box it rotates: laid out in flow, a 92px-wide label
                      set every 34px column to 113px and spread an 11-date
                      matrix across 1330px of mostly empty grid. */}
                  <Box
                    sx={{
                      position: 'absolute',
                      bottom: 4,
                      left: '50%',
                      width: LABEL_GUTTER - 4,
                      transform: 'rotate(-90deg)',
                      transformOrigin: 'left bottom',
                      textAlign: 'left',
                    }}
                  >
                    {entry.name}
                  </Box>
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {axis.slice(0, -1).map((rowEntry, row) => (
              <TableRow key={rowEntry.id}>
                <TableCell
                  scope="row"
                  sx={{
                    border: 0,
                    p: 0,
                    pr: 1,
                    width: ROW_LABEL_WIDTH,
                    maxWidth: ROW_LABEL_WIDTH,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    fontSize: 11,
                    fontWeight: 400,
                    color: 'text.secondary',
                    whiteSpace: 'nowrap',
                    textAlign: 'right',
                  }}
                >
                  {rowEntry.name}
                </TableCell>
                {axis.slice(1).map((colEntry, colOffset) => {
                  const col = colOffset + 1
                  if (col <= row) {
                    return <TableCell key={colEntry.id} sx={{ border: 0, p: 0 }} aria-hidden />
                  }
                  const record = recordAt(row, col)
                  // Falls back on the overlap itself, not on the first step:
                  // an unrecognised band -- a payload cached from before the
                  // top band was renamed, say -- would otherwise paint a
                  // coin-flip pair at 0.06 ink, which is the shade that means
                  // "separates cleanly". Getting the shade approximately right
                  // is recoverable; stating the opposite of the truth is not.
                  const strength = record
                    ? STRENGTH_BY_SEVERITY.get(record.severity) ?? strengthFromOverlap(record.overlap)
                    : null
                  const isSelected = record != null && record.id === selectedId
                  const isFocus = focusCell[0] === row && focusCell[1] === col
                  return (
                    <TableCell key={colEntry.id} sx={{ border: 0, p: 0 }}>
                      <Box
                        component="button"
                        type="button"
                        data-cell={`${row}-${col}`}
                        tabIndex={isFocus ? 0 : -1}
                        disabled={!record}
                        // Selection follows focus, and click selects rather
                        // than toggles. Arrow-key traversal used to move focus
                        // through all 55 cells while the readout beside them
                        // never changed -- so the roving-tabindex accelerator
                        // let a keyboard user cross the whole grid without ever
                        // reading a value, and the aria-live region announced
                        // only on click. Toggling-off went with it: with
                        // follow-focus, a click that deselected the cell the
                        // pointer had just focused was incoherent.
                        onClick={() => record && onSelect(record)}
                        onFocus={() => {
                          setFocusCell([row, col])
                          if (record) onSelect(record)
                        }}
                        onKeyDown={(e: React.KeyboardEvent) => {
                          const moves: Record<string, [number, number]> = {
                            ArrowUp: [-1, 0],
                            ArrowDown: [1, 0],
                            ArrowLeft: [0, -1],
                            ArrowRight: [0, 1],
                          }
                          const delta = moves[e.key]
                          if (!delta) return
                          e.preventDefault()
                          move(delta[0], delta[1])
                        }}
                        aria-label={
                          record
                            ? `${rowEntry.name} and ${colEntry.name}. ${orderingSentence(record)}`
                            : `${rowEntry.name} and ${colEntry.name}: not compared.`
                        }
                        aria-pressed={isSelected}
                        sx={{
                          display: 'block',
                          width: cell,
                          height: cell,
                          p: 0,
                          border: 0,
                          borderRadius: '2px',
                          cursor: record ? 'pointer' : 'default',
                          // One of five ink strengths, matching the five bands
                          // the backend classified this pair into -- so the
                          // legend below is a key a reader can actually use to
                          // find a "substantial" square. Same direction as the
                          // table's OverlapMeter: stronger means less separable.
                          backgroundColor:
                            strength == null ? 'transparent' : alpha(WARM_INK, strength),
                          // A ring drawn OUTSIDE the cell, over the paper, so
                          // it reads identically on the palest and the darkest
                          // fill. A 2px border inside a cell filled at 0.8 ink
                          // was invisible on exactly the squares the copy tells
                          // you to look at. The white halo separates the blue
                          // from the ink beneath it.
                          boxShadow: isSelected
                            ? `0 0 0 2px ${theme.palette.background.paper}, 0 0 0 4px ${theme.palette.primary.main}`
                            : 'none',
                          transition: 'box-shadow 120ms ease-out',
                          '&:focus-visible': {
                            outline: `2px solid ${theme.palette.primary.main}`,
                            outlineOffset: 2,
                          },
                          '&:hover': record ? { filter: 'brightness(0.92)' } : undefined,
                        }}
                      />
                    </TableCell>
                  )
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>

      {/* The key, and the only place the five overlap words now appear outside
          the pairs table. 
          Each swatch is the exact fill its band paints, in order, which is
          also the ranking: the words do not sort in English, and the last one
          is where the model can say the LEAST. */}
      <Box
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: { xs: 1, sm: 2 },
          mt: 1.5,
        }}
      >
        <Typography variant="caption" color="textSecondary" sx={{ mr: 0.5 }}>
          Overlap
        </Typography>
        {SEVERITY_STEPS.map((step) => (
          <Box key={step.severity} sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
            <Box
              aria-hidden
              sx={{
                width: 14,
                height: 14,
                borderRadius: '2px',
                flexShrink: 0,
                backgroundColor: alpha(WARM_INK, step.strength),
                // The palest step is nearly paper; a hairline keeps it a
                // swatch rather than a gap in the row.
                border: `1px solid ${alpha(WARM_INK, 0.12)}`,
              }}
            />
            <Typography variant="caption" color="textSecondary">
              {step.label}
            </Typography>
          </Box>
        ))}
      </Box>
    </Box>
  )
}
