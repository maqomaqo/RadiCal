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
}

/** How much room the rotated column labels get. Sample codes are short
 *  ("NZA-38894", "WK50104"); anything longer is clipped rather than allowed to
 *  push the grid taller than the matrix it labels. */
const LABEL_GUTTER = 96

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
  return `${(share * 100).toFixed(0)}% likely ${name} is the older. Overlap ${pct}% — how far the model is from being able to order them at all, 100% being a coin flip.`
}

export default function OrderingMatrix({ differences, axis, selectedId, onSelect }: Props) {
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

  // Cells shrink with the axis so a 24-date matrix still fits a laptop, and a
  // 5-date one does not sprawl into a wall chart.
  const cell = Math.max(18, Math.min(34, Math.round(760 / Math.max(axis.length - 1, 1))))

  const move = (dRow: number, dCol: number) => {
    let [r, c] = focusCell
    for (let step = 0; step < axis.length * 2; step++) {
      r += dRow
      c += dCol
      if (r < 0 || c >= axis.length) return
      if (c > r) break
      // Skipped past the diagonal: keep going in the same direction.
      if (dRow === 0 && dCol === 0) return
    }
    if (r >= 0 && c < axis.length && c > r) {
      setFocusCell([r, c])
      const next = gridRef.current?.querySelector<HTMLElement>(`[data-cell="${r}-${c}"]`)
      next?.focus()
    }
  }

  return (
    <Box sx={{ mb: 3 }}>
      {/* Kept out of <caption>: a caption participates in the table's width
          calculation, and a paragraph in there stretched a 400px matrix to
          1330px of mostly empty grid. The caption names the table for a
          screen reader; the prose reads in the flow, where it belongs. */}
      <Typography variant="body2" color="textSecondary" sx={{ mb: 1.5 }}>
        Every pair of dates, oldest first along both axes. The stronger a square, the less
        the model can say about which of the two came first — so a solid block is a group of
        dates it cannot separate. Select a square for that pair’s figures.
      </Typography>
      <TableContainer sx={{ maxWidth: '100%' }}>
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
                  const overlap = record ? Math.max(0, Math.min(1, record.overlap)) : null
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
                        onClick={() => record && onSelect(isSelected ? null : record)}
                        onFocus={() => setFocusCell([row, col])}
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
                          border: isSelected
                            ? `2px solid ${theme.palette.primary.main}`
                            : '1px solid transparent',
                          borderRadius: '2px',
                          cursor: record ? 'pointer' : 'default',
                          // The same hue and the same direction as the table's
                          // OverlapMeter: stronger means less separable.
                          backgroundColor:
                            overlap == null
                              ? 'transparent'
                              : alpha(theme.palette.primary.main, 0.06 + 0.72 * overlap),
                          transition: 'outline-color 120ms ease-out',
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

      {/* The scale, so "stronger" is a stated claim rather than one the reader
          has to infer from the darkest square they happen to notice. */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 1.5 }}>
        <Typography variant="caption" color="textSecondary">
          ordered with confidence
        </Typography>
        <Box
          aria-hidden
          sx={{
            width: 96,
            height: 8,
            borderRadius: 1,
            background: `linear-gradient(to right, ${alpha(
              theme.palette.primary.main,
              0.06
            )}, ${alpha(theme.palette.primary.main, 0.78)})`,
          }}
        />
        <Typography variant="caption" color="textSecondary">
          cannot be ordered
        </Typography>
      </Box>
    </Box>
  )
}
