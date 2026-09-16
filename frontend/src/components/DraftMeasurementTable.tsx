/**
 * The dates in a draft analysis, as the table on the New / Edit Analysis page.
 *
 * Its own component, and memoised, for one reason: the page it sits on
 * re-renders on every keystroke in every field above it, and with 800 dates
 * in the draft each of those re-renders was rebuilding 800 MUI table rows --
 * measured at 650 ms to paint one character typed into the analysis name.
 * Nothing in this table depends on the name, the project, the date format or
 * the MCMC settings, so a keystroke there should not touch it. Kept out here
 * with a props surface that changes only when the dates themselves do, it
 * does not.
 *
 * Each row is memoised too, so editing or deleting one date re-renders that
 * row and not the other 799.
 */
import { memo } from 'react'
import {
  Box,
  Chip,
  IconButton,
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
import { visuallyHidden } from '@mui/utils'
import EditIcon from '@mui/icons-material/Edit'
import DeleteIcon from '@mui/icons-material/Delete'

import { curveLabel, type CurveSpec } from '@/api/curves'
import { concernTitle, type Concern } from '@/utils/diagnostics'
import type { DraftMeasurement } from '@/types'

/** Which optional columns the table shows: only those some date populates. */
export interface DraftColumns {
  material: boolean
  context: boolean
  deltaR: boolean
  gap: boolean
  depth: boolean
  outlier: boolean
}

interface RowProps {
  m: DraftMeasurement
  /** The curve list, for naming the row's curve. */
  curves: CurveSpec[]
  /** The last run's verdict on this date, if it had one. */
  concern: Concern | undefined
  columns: DraftColumns
  onEdit: (m: DraftMeasurement) => void
  onRemove: (id: string) => void
}

const Row = memo(function Row({ m, curves, concern, columns, onEdit, onRemove }: RowProps) {
  return (
    <TableRow hover>
      <TableCell>
        {/* The results table's own verdict, carried onto the row the reader
            was sent here to edit. Arriving from "Edit analysis" they know a
            date was flagged and not which one, and the table naming it is a
            page behind them. Same rule and same words as that table, from
            utils/diagnostics.

            Left as it was found once the date is changed, with the tooltip
            saying which run it came from: a note that vanished on the first
            keystroke would disappear exactly when it was being acted on. */}
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="body2" sx={{ fontWeight: 500 }}>
            {m.sample_name}
          </Typography>
          {concern && (
            <Tooltip title={`${concernTitle(concern)} From the last run of this analysis.`} describeChild>
              <Chip
                size="small"
                tabIndex={0}
                color={concern.level === 'suspect' ? 'error' : 'warning'}
                variant={concern.level === 'suspect' ? 'filled' : 'outlined'}
                label={concern.level === 'suspect' ? 'Suspect' : 'Check'}
              />
            </Tooltip>
          )}
        </Stack>
      </TableCell>
      {/* nowrap so a value and its error never break across two lines and
          stop reading as one measurement. */}
      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
        {m.age_bp} ± {m.error_1sigma}
      </TableCell>
      <TableCell sx={{ whiteSpace: 'nowrap' }}>{curveLabel(curves, m.curve_id)}</TableCell>
      {columns.material && (
        <TableCell>
          {m.material_type && m.material_type !== 'unknown' ? m.material_type : '—'}
        </TableCell>
      )}
      {/* The context the date arrived filed under. Shown so it is visible
          that it survived the trip from the collection -- it is what the
          Model Structure page builds its starting phases from, and a grouping
          that silently vanished used to have to be re-created from memory. */}
      {columns.context && <TableCell>{m.default_context_name || '—'}</TableCell>}
      {/* ΔR and the outlier settings both CHANGE the answer, so they stay
          visible without opening anything. */}
      {columns.deltaR && (
        <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
          {m.delta_r != null ? `${m.delta_r} ± ${m.delta_r_error ?? 0}` : '—'}
        </TableCell>
      )}
      {/* The gap carries its own uncertainty where the separation was
          measured rather than counted, and the two read as one figure, so
          they share a cell as ΔR does. */}
      {columns.gap && (
        <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
          {m.ring_offset == null
            ? '—'
            : m.ring_offset_error
              ? `${m.ring_offset} ± ${m.ring_offset_error}`
              : m.ring_offset}
        </TableCell>
      )}
      {columns.depth && (
        <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
          {m.depth_cm ?? '—'}
        </TableCell>
      )}
      {columns.outlier && (
        <TableCell sx={{ whiteSpace: 'nowrap' }}>
          {(m.outlier_prior ?? 0) > 0
            ? `${m.outlier_model === 'charcoal' ? 'charcoal' : 'general'} ${
                Math.round((m.outlier_prior ?? 0) * 1000) / 10
              }%`
            : '—'}
        </TableCell>
      )}
      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
        <Tooltip title={`Edit ${m.sample_name}`}>
          <IconButton size="small" aria-label={`Edit ${m.sample_name}`} onClick={() => onEdit(m)}>
            <EditIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title={`Delete ${m.sample_name}`}>
          <IconButton
            size="small"
            aria-label={`Delete ${m.sample_name}`}
            onClick={() => onRemove(m.id)}
            sx={{ '&:hover, &:focus-visible': { color: 'error.main' } }}
          >
            <DeleteIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </TableCell>
    </TableRow>
  )
})

interface Props {
  measurements: DraftMeasurement[]
  curves: CurveSpec[]
  /** The last run's verdicts by date id. */
  concerns: Record<string, Concern>
  columns: DraftColumns
  /**
   * Both handlers must be referentially stable across renders -- a store
   * action, or a useCallback -- or the memo on every row is defeated and the
   * table is back to rebuilding itself on every keystroke.
   */
  onEdit: (m: DraftMeasurement) => void
  onRemove: (id: string) => void
}

/**
 * A table, not the card grid this used to be. These rows are the rows the
 * results table will show, and the one thing a reader does with a list of
 * freshly typed dates is scan the ages down a column for a transposed digit
 * -- which a 3-wide grid of cards makes impossible. Follows the house pattern
 * from Results.tsx: full width, numeric columns nowrap and tabular-nums, and
 * the label column at width 1% so the surplus goes to the columns carrying
 * numbers instead of pooling behind the names.
 */
const DraftMeasurementTable = memo(function DraftMeasurementTable({
  measurements,
  curves,
  concerns,
  columns,
  onEdit,
  onRemove,
}: Props) {
  return (
    <TableContainer>
      <Table
        size="small"
        sx={{
          fontVariantNumeric: 'tabular-nums',
          '& thead th': { backgroundColor: 'background.muted' },
          '& tbody td:first-of-type, & thead th:first-of-type': { whiteSpace: 'nowrap' },
        }}
      >
        <caption style={visuallyHidden}>
          The dates in this analysis, in the order they will be submitted.
        </caption>
        <TableHead>
          <TableRow>
            <TableCell sx={{ width: '1%' }}>Sample</TableCell>
            <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
              Age (BP)
            </TableCell>
            <TableCell sx={{ whiteSpace: 'nowrap' }}>Curve</TableCell>
            {columns.material && <TableCell>Material</TableCell>}
            {columns.context && <TableCell>Context</TableCell>}
            {columns.deltaR && (
              <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                ΔR (yr)
              </TableCell>
            )}
            {columns.gap && (
              <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                Gap
              </TableCell>
            )}
            {columns.depth && (
              <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                Depth (cm)
              </TableCell>
            )}
            {columns.outlier && <TableCell sx={{ whiteSpace: 'nowrap' }}>Outlier</TableCell>}
            <TableCell align="right" sx={{ width: '1%' }}>
              <Box component="span" sx={visuallyHidden}>
                Actions
              </Box>
            </TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {measurements.map((m) => (
            <Row
              key={m.id}
              m={m}
              curves={curves}
              concern={concerns[m.id]}
              columns={columns}
              onEdit={onEdit}
              onRemove={onRemove}
            />
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
})

export default DraftMeasurementTable
