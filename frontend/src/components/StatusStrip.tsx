import { Box } from '@mui/material'
import { alpha } from '@mui/material/styles'
import type { SxProps, Theme } from '@mui/material/styles'
import { WARM_INK } from '@/theme/diagramColors'

/** The four states `Analysis.status` is constrained to. */
export interface StatusCounts {
  completed: number
  running: number
  failed: number
  pending: number
}

/** "12 completed · 2 failed" -- zero states omitted, the app's own status words. */
export function statusSummary(counts: StatusCounts, total: number): string {
  const untouched = Math.max(0, total - counts.completed - counts.running - counts.failed)
  const parts: string[] = []
  if (counts.completed) parts.push(`${counts.completed} completed`)
  if (counts.running) parts.push(`${counts.running} running`)
  if (counts.failed) parts.push(`${counts.failed} failed`)
  if (untouched) parts.push(`${untouched} pending`)
  return parts.join(' · ')
}

/**
 * The analysis mix as one 3px strip: segments in proportion, in the app's
 * semantic state colors, never wider than the truth. Anything not completed,
 * running or failed is drawn in ink at low strength rather than given a hue --
 * "queued" is the absence of an outcome, not an outcome. It sits under the
 * words that name it, so the number stays the dominant mark rather than the
 * colour.
 *
 * Shared by the project card and the project masthead, which draw the same
 * measure at two sizes: on a card it is a rule under the counts, on the
 * masthead it is bled to the Paper's own bottom edge so the header reads as
 * one object with the analyses beneath it rather than as a slab above them.
 * `sx` carries that difference; the encoding is not the caller's to change.
 */
export default function StatusStrip({
  counts,
  total,
  sx,
}: {
  counts: StatusCounts
  total: number
  sx?: SxProps<Theme>
}) {
  const untouched = Math.max(0, total - counts.completed - counts.running - counts.failed)
  const filled: Array<{ key: string; value: number; color: string }> = [
    { key: 'completed', value: counts.completed, color: 'success.main' },
    { key: 'running', value: counts.running, color: 'info.main' },
    { key: 'failed', value: counts.failed, color: 'error.main' },
    { key: 'untouched', value: untouched, color: alpha(WARM_INK, 0.16) },
  ].filter((segment) => segment.value > 0)
  // A project with nothing in it still gets the empty track, so every card in
  // the grid ends on the same line. The track is the scale; a card that simply
  // stopped short would read as unfinished rather than as empty.
  const segments = filled.length
    ? filled
    : [{ key: 'empty', value: 1, color: alpha(WARM_INK, 0.08) }]

  return (
    <Box
      aria-hidden
      sx={[
        {
          display: 'flex',
          gap: '2px',
          height: 3,
          borderRadius: 2,
          overflow: 'hidden',
        },
        ...(Array.isArray(sx) ? sx : [sx]),
      ]}
    >
      {segments.map((segment) => (
        <Box
          key={segment.key}
          sx={{ flex: segment.value, backgroundColor: segment.color, borderRadius: 2 }}
        />
      ))}
    </Box>
  )
}
