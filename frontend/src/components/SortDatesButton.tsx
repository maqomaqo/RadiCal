import { useState } from 'react'
import { Button, ListItemIcon, ListItemText, Menu, MenuItem, Tooltip } from '@mui/material'
import CheckIcon from '@mui/icons-material/Check'
import SortIcon from '@mui/icons-material/Sort'
import {
  MEASUREMENT_SORT_OPTIONS,
  isSortedBy,
  type MeasurementSortKey,
} from '@/utils/sortMeasurements'
import type { MatrixMeasurement } from '@/types'

interface Props {
  measurements: MatrixMeasurement[]
  onSort: (key: MeasurementSortKey) => void
  size?: 'small' | 'medium'
  /** Overrides the default label where a shorter one reads better. */
  label?: string
}

/**
 * The one control that decides what order a draft's dates are in.
 *
 * Shared between the measurement list and the model editor rather than
 * duplicated, because there is only one order for them to change: a phase's
 * dates are a filter over the measurement array, not a sequence the phase
 * owns. Two controls editing the same array should not be able to disagree
 * about what "oldest first" means.
 *
 * The option already in effect is ticked and inert, so a list that is already
 * sorted says so instead of offering a press that changes nothing.
 */
export default function SortDatesButton({ measurements, onSort, size = 'small', label }: Props) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)

  return (
    <>
      <Tooltip title="Reorder the dates. The order is kept, and is the order they appear in within each phase, in the results table and in exports.">
        <span>
          <Button
            size={size}
            variant="outlined"
            startIcon={<SortIcon />}
            disabled={measurements.length < 2}
            onClick={(e) => setAnchor(e.currentTarget)}
          >
            {label ?? 'Sort dates'}
          </Button>
        </span>
      </Tooltip>
      <Menu anchorEl={anchor} open={!!anchor} onClose={() => setAnchor(null)}>
        {MEASUREMENT_SORT_OPTIONS.map((option) => {
          const current = isSortedBy(measurements, option.key)
          return (
            <MenuItem
              key={option.key}
              disabled={current}
              onClick={() => {
                onSort(option.key)
                setAnchor(null)
              }}
            >
              <ListItemIcon sx={{ minWidth: 32 }}>{current && <CheckIcon fontSize="small" />}</ListItemIcon>
              <ListItemText>{option.label}</ListItemText>
            </MenuItem>
          )
        })}
      </Menu>
    </>
  )
}
