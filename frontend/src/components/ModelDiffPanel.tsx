import {
  Box,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material'
import HarrisMatrixDiagram from '@/components/HarrisMatrixDiagram'
import type { ConfigField, ElementDiff } from '@/utils/compareConfig'
import type { DateFormat } from '@/utils/dateFormat'
import type { MatrixMeasurement, ModelElement } from '@/types'
import { useElementTypeColors } from '@/theme/diagramColors'
import type { RunStyle } from '@/theme/compareColors'

/**
 * What differs between the runs.
 *
 * The field table is the settings --
 * curve, ΔR, outlier model, limiters, solver -- read out of what each analysis
 * stored. The structure panel is the model tree, matched element by element and then drawn.
 *
 * Elements are matched by type and name.
 */

interface ModelDiffPanelProps {
  runs: Array<{ id: string; name: string }>
  runStyles: RunStyle[]
  fields: ConfigField[]
  elements: ElementDiff[]
  /** Unnamed elements per run, which have no identity to be matched on. */
  unnamed: number[]
  trees: Array<ModelElement | null | undefined>
  measurementsPerRun: MatrixMeasurement[][]
  dateFormat: DateFormat
  confidence: number
  confidenceLabel: string
}

export default function ModelDiffPanel({
  runs,
  runStyles,
  fields,
  elements,
  unnamed,
  trees,
  measurementsPerRun,
  dateFormat,
  confidence,
  confidenceLabel,
}: ModelDiffPanelProps) {
  const elementColors = useElementTypeColors()
  const changedElements = elements.filter((element) => element.status !== 'unchanged')
  const unchangedCount = elements.length - changedElements.length
  const anyTree = trees.some(Boolean)

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <Box>
        <Typography variant="subtitle2" gutterBottom>
          Settings
        </Typography>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow sx={{ backgroundColor: 'background.muted' }}>
                <TableCell sx={{ width: 40 }} />
                <TableCell sx={{ width: 210 }}>Field</TableCell>
                {runs.map((run, i) => (
                  <TableCell key={run.id}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                      <Box
                        sx={{
                          width: 10,
                          height: 10,
                          borderRadius: '2px',
                          flex: 'none',
                          backgroundColor: runStyles[i]?.fillOuter,
                          border: `1px solid ${runStyles[i]?.line}`,
                        }}
                      />
                      {run.name}
                    </Box>
                  </TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {fields.map((field) => (
                <TableRow key={field.label} hover>
                  <TableCell align="center">
                    <Tooltip
                      title={field.changed ? 'These runs differ here' : 'The same across every run'}
                    >
                      <Typography
                        variant="body2"
                        sx={{
                          fontFamily: 'monospace',
                          color: field.changed ? 'secondary.main' : 'text.disabled',
                        }}
                      >
                        {field.changed ? '±' : '='}
                      </Typography>
                    </Tooltip>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: field.changed ? 600 : 400 }}>
                      {field.label}
                    </Typography>
                    {field.note && field.changed && (
                      <Typography variant="caption" color="textSecondary" component="div">
                        {field.note}
                      </Typography>
                    )}
                  </TableCell>
                  {field.values.map((value, i) => (
                    <TableCell key={runs[i].id}>
                      <Typography
                        variant="body2"
                        color={field.changed ? 'textPrimary' : 'textSecondary'}
                      >
                        {value}
                      </Typography>
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Box>

      {anyTree && (
        <Box>
          <Typography variant="subtitle2" gutterBottom>
            Structure
          </Typography>
          {changedElements.length === 0 ? (
            <Typography variant="body2" color="textSecondary" sx={{ mb: 1.5 }}>
              Every named element appears in every run, in the same place. Whatever separates these
              runs, it is not the shape of the tree.
            </Typography>
          ) : (
            <>
              <Typography variant="body2" color="textSecondary" sx={{ mb: 1, maxWidth: '72ch' }}>
                {changedElements.length} element{changedElements.length === 1 ? '' : 's'} differ
                {changedElements.length === 1 ? 's' : ''}
                {unchangedCount > 0 ? `, ${unchangedCount} shared unchanged` : ''}.
              </Typography>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 1.5 }}>
                {changedElements.map((element) => {
                  const color = elementColors[element.elementType]
                  const detail =
                    element.status === 'moved'
                      ? `Sits under ${element.parents
                          .map((parent, i) => `${parent || 'the root'} in ${runs[i].name}`)
                          .join(', ')}`
                      : `Only in ${element.presentIn.map((i) => runs[i].name).join(', ')}`
                  return (
                    <Tooltip key={element.key} title={detail}>
                      <Chip
                        size="small"
                        variant="outlined"
                        label={`${element.elementType} ${element.name} · ${
                          element.status === 'moved'
                            ? 'moved'
                            : `only in ${element.presentIn.length} of ${runs.length}`
                        }`}
                        sx={{ borderColor: color.main, color: color.ink }}
                      />
                    </Tooltip>
                  )
                })}
              </Box>
            </>
          )}
          {unnamed.some((count) => count > 0) && (
            <Typography variant="caption" color="textSecondary" component="div" sx={{ mb: 1.5 }}>
              Unnamed elements are not matched — with no name there is nothing to match on. Counts:{' '}
              {unnamed.map((count, i) => `${runs[i].name} ${count}`).join(', ')}.
            </Typography>
          )}

          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
              gap: 2,
            }}
          >
            {runs.map((run, i) => (
              <Box key={run.id}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 0.5 }}>
                  <Box
                    sx={{
                      width: 10,
                      height: 10,
                      borderRadius: '2px',
                      flex: 'none',
                      backgroundColor: runStyles[i]?.fillOuter,
                      border: `1px solid ${runStyles[i]?.line}`,
                    }}
                  />
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {run.name}
                  </Typography>
                </Box>
                {trees[i] ? (
                  <HarrisMatrixDiagram
                    tree={trees[i]}
                    measurements={measurementsPerRun[i]}
                    dateFormat={dateFormat}
                    confidence={confidence}
                    confidenceLabel={confidenceLabel}
                    height={420}
                  />
                ) : (
                  <Typography variant="body2" color="textSecondary">
                    No model structure — this run calibrated its dates without one.
                  </Typography>
                )}
              </Box>
            ))}
          </Box>
        </Box>
      )}
    </Box>
  )
}
