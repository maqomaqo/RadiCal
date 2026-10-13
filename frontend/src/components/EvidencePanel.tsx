import {
  Alert,
  Link,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import { Link as RouterLink } from 'react-router-dom'
import { compareEvidence, formatLog10, oddsOf } from '@/utils/evidence'
import type { EvidenceRun } from '@/utils/evidence'

/**
 * How well each model explains the same readings: the evidence of each run and
 * the Bayes factor against the first. The first run in the comparison is the
 * reference. Withheld, with the reason, when the runs cannot fairly be set
 * against each other.
 */
export default function EvidencePanel({ runs }: { runs: EvidenceRun[] }) {
  const comparison = compareEvidence(runs)

  if (comparison.kind === 'withheld') {
    return (
      <Alert severity="info">
        <Typography variant="body2">
          Bayes factors are not shown. {comparison.reason}{' '}
          <Link component={RouterLink} to="/help#comparing-models-by-evidence">
            Comparing models by evidence
          </Link>{' '}
          in the manual.
        </Typography>
      </Alert>
    )
  }

  return (
    <>
      <Table size="small" sx={{ maxWidth: 760 }}>
        <TableHead>
          <TableRow>
            <TableCell>Analysis</TableCell>
            <TableCell align="right">log₁₀ evidence</TableCell>
            <TableCell align="right">log₁₀ Bayes factor</TableCell>
            <TableCell align="right">Odds</TableCell>
            <TableCell>Against the first</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {comparison.rows.map((row, index) => (
            <TableRow key={index}>
              <TableCell>{row.name}</TableCell>
              <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                {formatLog10(row.log10)}
              </TableCell>
              <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                {row.log10Factor === null ? '—' : formatLog10(row.log10Factor)}
              </TableCell>
              <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                {row.log10Factor === null
                  ? '—'
                  : `${oddsOf(row.log10Factor)} ${row.log10Factor >= 0 ? 'for' : 'against'}`}
              </TableCell>
              <TableCell>{row.strength ?? 'reference'}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {comparison.approximate && (
        <Typography variant="body2" color="textSecondary" sx={{ mt: 1.5, maxWidth: '72ch' }}>
          One or more of these was taken across a wide range of years, so its figure is
          approximate.
        </Typography>
      )}
    </>
  )
}
