import { useEffect, useMemo, useState } from 'react'
import { Link as RouterLink, useParams, useSearchParams } from 'react-router-dom'
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Chip,
  CircularProgress,
  Container,
  Link,
  MenuItem,
  Paper,
  Tab,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import AgreementScatter from '@/components/AgreementScatter'
import ComparePosteriorPlot from '@/components/ComparePosteriorPlot'
import DerivedQuantitiesPanel from '@/components/DerivedQuantitiesPanel'
import ModelDiffPanel from '@/components/ModelDiffPanel'
import ShiftPlot from '@/components/ShiftPlot'
import { useCurves } from '@/hooks/useCurves'
import { MAX_COMPARED_RUNS, useRunStyles } from '@/theme/compareColors'
import {
  comparisonWindow,
  pairRuns,
  rowMetrics,
  sortComparedRows,
  type ComparedRow,
  type CompareRunInput,
  type CompareSort,
} from '@/utils/comparePairing'
import { configFields, diffTrees } from '@/utils/compareConfig'
import { pairDerived } from '@/utils/compareDerived'
import { compareCsv, downloadCsv } from '@/utils/compareExport'
import {
  DATE_FORMAT_OPTIONS,
  DateFormat,
  DEFAULT_DATE_FORMAT,
  formatCalendarAge,
} from '@/utils/dateFormat'
import { CONFIDENCE_LEVELS, SIGMA_2 } from '@/utils/hpd'
import { formatApiError } from '@/utils/apiError'
import type { IntervalsBlock, MatrixMeasurement, ModelElement } from '@/types'

/**
 * Comparing two or more analyses of the same dates.
 *
 * Everything on this page is assembled in the browser from the analyses'
 * ordinary GET /api/analysis/:id payloads. There is no compare endpoint and no
 * new solver work: every distribution and summary a comparison reads is
 * already in what the results page fetches, and computing the comparison here
 * keeps it from becoming a second, drifting account of numbers the backend
 * already publishes.
 *
 * Three things the page refuses to do, each of which would produce a
 * confident, well-drawn, wrong answer:
 *
 *   It will not compare runs that have not finished, since a failed or
 *   half-run analysis has no posterior to compare.
 *
 *   It will not silently drop a date that only some of the runs have. Those go
 *   to a visible tray -- see utils/comparePairing.
 *
 *   It will not report a joint statistic. Two runs are two separate
 *   posteriors; a difference of medians is available and a probability that
 *   the date moved is not.
 *
 * One thing it deliberately allows, with a caption rather than a refusal:
 * comparing analyses run against different curves. That is a real question --
 * a marine-diet date against a terrestrial curve and a mixed one is exactly
 * it -- but then the shift measures the curve rather than the model, and the
 * page has to say so.
 */

interface RunPayload {
  id: string
  name: string
  analysis_type: string
  status: string
  error_message?: string | null
  solver?: {
    method: 'exact' | 'gibbs' | 'smc'
    sweeps?: number
    convergence?: number
    converged?: boolean
  } | null
  model_tree?: ModelElement | null
  intervals?: IntervalsBlock | null
  measurements?: Array<
    MatrixMeasurement & {
      curve_id?: string
      delta_r?: number | null
      delta_r_error?: number | null
    }
  >
  results: Array<{
    sample_id: string
    sample_name?: string | null
    posterior_mean?: number
    posterior_median?: number
    probability_distribution?: Array<[number, number]>
    unmodelled_distribution?: Array<[number, number]> | null
    agreement_index?: number | null
    outlier_probability?: number | null
  }>
}

const SORT_OPTIONS: { value: CompareSort; label: string }[] = [
  { value: 'shift', label: 'Largest shift first' },
  { value: 'analysis', label: 'Reference analysis order' },
  { value: 'name', label: 'Name' },
]

/** How a run computed its numbers, in one line. */
function solverLabel(run: RunPayload): string {
  if (run.analysis_type !== 'bayesian') return 'calibration only'
  if (!run.solver) return 'solver not recorded'
  if (run.solver.method === 'exact') return 'exact solver'
  const sweeps = run.solver.sweeps ? `, ${run.solver.sweeps.toLocaleString()} sweeps` : ''
  return `MH/Gibbs${sweeps}`
}

export default function CompareResults() {
  const { projectId } = useParams<{ projectId: string }>()
  const [searchParams] = useSearchParams()
  const { curves } = useCurves()

  const requestedIds = useMemo(() => {
    const raw = searchParams.get('runs') ?? ''
    return raw
      .split(',')
      .map((id) => id.trim())
      .filter(Boolean)
  }, [searchParams])

  const [runs, setRuns] = useState<RunPayload[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [dateFormat, setDateFormat] = useState<DateFormat>(DEFAULT_DATE_FORMAT)
  const [confidence, setConfidence] = useState<number>(SIGMA_2)
  const [sort, setSort] = useState<CompareSort>('shift')
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  // 'dates' compares the dates themselves; 'model' compares what the models
  // did with them -- their structure, what they derived, and what they cost in
  // agreement.
  const [tab, setTab] = useState<'dates' | 'model'>('dates')

  const confidenceLabel =
    CONFIDENCE_LEVELS.find((level) => level.value === confidence)?.label ?? ''

  useEffect(() => {
    let active = true
    if (requestedIds.length < 2) {
      setError('Pick at least two analyses to compare.')
      setLoading(false)
      return
    }
    if (requestedIds.length > MAX_COMPARED_RUNS) {
      setError(
        `A comparison holds at most ${MAX_COMPARED_RUNS} analyses; ${requestedIds.length} were asked for.`
      )
      setLoading(false)
      return
    }

    setLoading(true)
    setError(null)
    Promise.all(
      requestedIds.map(async (id) => {
        const response = await fetch(`/api/analysis/${id}`)
        const data = await response.json()
        if (!response.ok || !data.success) {
          throw new Error(formatApiError(data))
        }
        return data.data as RunPayload
      })
    )
      .then((loaded) => {
        if (!active) return
        setRuns(loaded)
      })
      .catch((err: Error) => {
        if (!active) return
        setError(err.message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [requestedIds])

  const unfinished = useMemo(
    () => (runs ?? []).filter((run) => run.status !== 'completed'),
    [runs]
  )

  const modelled = useMemo(
    () => (runs ?? []).map((run) => run.analysis_type === 'bayesian'),
    [runs]
  )

  const runStyles = useRunStyles(runs?.length ?? 0)

  const comparison = useMemo(() => {
    if (!runs || unfinished.length > 0) return null
    const inputs: CompareRunInput[] = runs.map((run) => {
      const curveBySampleId = new Map(
        (run.measurements ?? []).map((m) => [m.id, m.curve_id ?? null])
      )
      return {
        id: run.id,
        name: run.name,
        analysisType: run.analysis_type,
        results: run.results.map((result) => ({
          sampleId: result.sample_id,
          sampleName: result.sample_name ?? null,
          posteriorMedian: result.posterior_median ?? null,
          posteriorMean: result.posterior_mean ?? null,
          distribution: result.probability_distribution ?? null,
          priorDistribution: result.unmodelled_distribution ?? null,
          agreementIndex: result.agreement_index ?? null,
          outlierProbability: result.outlier_probability ?? null,
          curveId: curveBySampleId.get(result.sample_id) ?? null,
        })),
      }
    })
    return pairRuns(inputs)
  }, [runs, unfinished.length])

  const comparedRows: ComparedRow[] = useMemo(() => {
    if (!comparison) return []
    return comparison.rows.map((row) => ({
      ...row,
      metrics: rowMetrics(row, modelled, confidence),
    }))
  }, [comparison, modelled, confidence])

  const sortedRows = useMemo(
    () => sortComparedRows(comparedRows, sort),
    [comparedRows, sort]
  )

  const window = useMemo(
    () => comparisonWindow(comparedRows, modelled, confidence),
    [comparedRows, modelled, confidence]
  )

  /** A curve's display name, or its id while the curve list is still loading. */
  const curveLabel = (id: string | null | undefined) =>
    id ? curves.find((c) => c.id === id)?.name ?? id : 'unrecorded'

  /**
   * Curves the runs disagree about, as a list of "date: curve here, curve
   * there" descriptions. Compared per DATE rather than per run, since two runs
   * can use the same set of curves and still apply them to different samples.
   */
  const curveDisagreements = useMemo(() => {
    const out: string[] = []
    for (const row of comparedRows) {
      const ids = row.cells.map((cell) => cell?.curveId ?? null)
      if (new Set(ids.map((id) => id ?? '')).size > 1) {
        out.push(`${row.label}: ${ids.map(curveLabel).join(' vs ')}`)
      }
    }
    return out
  }, [comparedRows, curves])

  const configDiff = useMemo(() => {
    if (!runs) return null
    return configFields(
      runs.map((run) => ({
        name: run.name,
        analysisType: run.analysis_type,
        solverLabel: solverLabel(run),
        draws: run.intervals?.draws ?? null,
        independent: run.intervals?.independent ?? null,
        modelTree: run.model_tree,
        measurements: (run.measurements ?? []).map((m) => ({
          sampleName: m.sample_name,
          curveLabel: curveLabel(m.curve_id),
          deltaR: m.delta_r,
          deltaRError: m.delta_r_error,
          outlierPrior: m.outlier_prior,
          outlierModel: m.outlier_model,
          tpqAgeBp: m.tpq_age_bp,
          taqAgeBp: m.taq_age_bp,
        })),
      }))
    )
    // curves only ever adds names to ids already present, so a late arrival
    // relabels the table rather than changing what it compares.
  }, [runs, curves])

  const treeDiff = useMemo(
    () => (runs ? diffTrees(runs.map((run) => run.model_tree)) : null),
    [runs]
  )

  const derivedRows = useMemo(() => {
    if (!runs || unfinished.length > 0) return []
    return pairDerived(
      runs.map((run) => ({ modelTree: run.model_tree, intervals: run.intervals })),
      confidence
    )
  }, [runs, unfinished.length, confidence])

  /** Which runs report an agreement index, for the scatter and its guard. */
  const agreementRuns = useMemo(
    () =>
      (runs ?? []).map((_, i) => comparedRows.some((row) => row.cells[i]?.agreementIndex != null)),
    [runs, comparedRows]
  )

  /**
   * Whether to draw the unconstrained calibration behind the runs.
   *
   * Only when every compared run is modelled. A group calibration's posterior
   * IS the unconstrained calibration, so drawing the shadow alongside it
   * plots one series twice and implies two pieces of evidence where there is
   * one.
   */
  const showUnconstrained = modelled.length > 0 && modelled.every(Boolean)

  const unconstrainedFor = (row: ComparedRow) =>
    row.cells.find((cell) => cell?.priorDistribution)?.priorDistribution ?? null

  const handleExport = () => {
    if (!runs || !comparison) return
    const csv = compareCsv(
      runs.map((run, i) => ({
        name: run.name,
        analysisType: run.analysis_type,
        modelled: modelled[i],
      })),
      sortedRows,
      comparison.unmatched,
      derivedRows,
      dateFormat,
      confidence,
      confidenceLabel
    )
    downloadCsv('comparison.csv', csv)
  }

  const backLink = projectId ? `/projects/${projectId}` : '/projects'

  if (loading) {
    return (
      <Container sx={{ mt: 4, textAlign: 'center' }}>
        <CircularProgress />
      </Container>
    )
  }

  if (error) {
    return (
      <Container maxWidth="md" sx={{ mt: 4 }}>
        <Alert severity="error">{error}</Alert>
        <Button component={RouterLink} to={backLink} sx={{ mt: 2 }}>
          Back to the project
        </Button>
      </Container>
    )
  }

  if (!runs) return null

  return (
    <Container maxWidth="xl" sx={{ mt: 4, mb: 6 }}>
      <Box sx={{ mb: 2 }}>
        <Link component={RouterLink} to={backLink} variant="body2">
          ← Back to the project
        </Link>
      </Box>

      <Typography variant="h4" gutterBottom>
        Compare results
      </Typography>
      <Typography variant="body1" color="textSecondary" sx={{ mb: 3, maxWidth: '72ch' }}>
        The same dates under {runs.length} different analyses, on one calendar axis. Rows sort by how
        far each date moved, so the dates these analyses disagree about come to the top.
      </Typography>

      <Paper sx={{ p: 2.5, mb: 3 }}>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, mb: 2 }}>
          {runs.map((run, i) => (
            <Box
              key={run.id}
              sx={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 1,
                minWidth: 220,
                flex: '1 1 240px',
              }}
            >
              <Box
                sx={{
                  width: 12,
                  height: 12,
                  mt: '5px',
                  borderRadius: '2px',
                  flex: 'none',
                  backgroundColor: runStyles[i]?.fillOuter,
                  border: `1px solid ${runStyles[i]?.line}`,
                }}
              />
              <Box>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  <Link component={RouterLink} to={`/results/${run.id}`} color="inherit">
                    {run.name}
                  </Link>
                  {i === 0 && (
                    <Chip label="reference" size="small" variant="outlined" sx={{ ml: 1 }} />
                  )}
                </Typography>
                <Typography variant="caption" color="textSecondary" component="div">
                  {run.analysis_type} · {run.results.length} dates · {solverLabel(run)}
                </Typography>
              </Box>
            </Box>
          ))}
        </Box>

        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center' }}>
          <TextField
            select
            size="small"
            label="Reporting format"
            value={dateFormat}
            onChange={(e) => setDateFormat(e.target.value as DateFormat)}
            sx={{ minWidth: 220 }}
            helperText="Applies to every panel on this page"
          >
            {DATE_FORMAT_OPTIONS.map((option) => (
              <MenuItem key={option.value} value={option.value}>
                {option.label}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label="Confidence"
            value={confidence}
            onChange={(e) => setConfidence(Number(e.target.value))}
            sx={{ minWidth: 140 }}
            helperText="Every run, one level"
          >
            {CONFIDENCE_LEVELS.map((level) => (
              <MenuItem key={level.value} value={level.value}>
                {level.label}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label="Sort rows"
            value={sort}
            onChange={(e) => setSort(e.target.value as CompareSort)}
            sx={{ minWidth: 210 }}
            helperText=" "
          >
            {SORT_OPTIONS.map((option) => (
              <MenuItem key={option.value} value={option.value}>
                {option.label}
              </MenuItem>
            ))}
          </TextField>
          <Box sx={{ flexGrow: 1 }} />
          <Button
            variant="outlined"
            startIcon={<DownloadIcon />}
            onClick={handleExport}
            disabled={sortedRows.length === 0}
          >
            Export CSV
          </Button>
        </Box>
      </Paper>

      {unfinished.length > 0 && (
        <Alert severity="error" sx={{ mb: 3 }}>
          <AlertTitle>Nothing to compare yet</AlertTitle>
          {unfinished.map((run) => (
            <div key={run.id}>
              {run.name} is {run.status}
              {run.error_message ? `: ${run.error_message}` : '.'}
            </div>
          ))}
        </Alert>
      )}

      {curveDisagreements.length > 0 && (
        <Alert severity="warning" sx={{ mb: 3 }}>
          <AlertTitle>These runs used different calibration curves</AlertTitle>
          <Typography variant="body2" sx={{ mb: 1 }}>
            A shift below will be partly due to the chosen curve, not the model. That may be exactly what
            you are asking — it is what a mixed-curve comparison is for — but it is not a comparison
            of two models over one calibration.
          </Typography>
          <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
            {curveDisagreements.slice(0, 6).map((line) => (
              <li key={line}>
                <Typography variant="body2">{line}</Typography>
              </li>
            ))}
            {curveDisagreements.length > 6 && (
              <li>
                <Typography variant="body2">
                  and {curveDisagreements.length - 6} more.
                </Typography>
              </li>
            )}
          </Box>
        </Alert>
      )}

      {unfinished.length === 0 && comparison && (
        <>
          <Tabs value={tab} onChange={(_e, value) => setTab(value)} sx={{ mb: 2 }}>
            <Tab label={`Dates (${sortedRows.length})`} value="dates" />
            <Tab label="Models" value="model" />
          </Tabs>
        </>
      )}

      {unfinished.length === 0 && comparison && tab === 'dates' && (
        <>
          <Paper sx={{ p: 2.5, mb: 3 }}>
            {sortedRows.length === 0 || !window ? (
              <Alert severity="info">
                These analyses have no date in common under the same name, so there is nothing to
                pair up. Dates are matched by sample name — a date deployed from a project
                collection keeps its name, but a date re-typed under a different one is a different
                date as far as this page can tell.
              </Alert>
            ) : (
              <>
                <ShiftPlot
                  runs={runs.map((run) => ({
                    id: run.id,
                    name: run.name,
                    analysisType: run.analysis_type,
                  }))}
                  runStyles={runStyles}
                  rows={sortedRows}
                  modelled={modelled}
                  window={window}
                  confidence={confidence}
                  confidenceLabel={confidenceLabel}
                  dateFormat={dateFormat}
                  expandedKey={expandedKey}
                  onToggleRow={(key) => setExpandedKey((current) => (current === key ? null : key))}
                  renderExpanded={(row) => (
                    <>
                      <Typography variant="subtitle2" gutterBottom>
                        {row.label} — the distributions behind the bars
                      </Typography>
                      <ComparePosteriorPlot
                        sampleName={row.label}
                        series={row.cells.flatMap((cell, i) =>
                          cell?.distribution
                            ? [
                                {
                                  name: runs[i].name,
                                  distribution: cell.distribution,
                                  style: runStyles[i],
                                },
                              ]
                            : []
                        )}
                        priorDistribution={showUnconstrained ? unconstrainedFor(row) : null}
                        dateFormat={dateFormat}
                      />
                      <Typography variant="caption" color="textSecondary" component="div">
                        Each curve is scaled to unit area, so a tighter posterior is taller. Medians:{' '}
                        {row.cells
                          .map((cell, i) =>
                            cell?.posteriorMedian == null
                              ? null
                              : `${runs[i].name} ${formatCalendarAge(cell.posteriorMedian, dateFormat)}`
                          )
                          .filter(Boolean)
                          .join(' · ')}
                      </Typography>
                    </>
                  )}
                />
                <Typography
                  variant="caption"
                  color="textSecondary"
                  component="div"
                  sx={{ mt: 1.5, maxWidth: '84ch' }}
                >
                  Δ median and width ratio are differences between two runs' summaries. They are not
                  joint statistics: the runs were not sampled together.
                </Typography>
              </>
            )}
          </Paper>

          {comparison.unmatched.length > 0 && (
            <Paper sx={{ p: 2.5 }}>
              <Typography variant="h6" gutterBottom>
                Not compared ({comparison.unmatched.length})
              </Typography>
              <Typography variant="body2" color="textSecondary" sx={{ mb: 1.5, maxWidth: '72ch' }}>
                These dates are not in every analysis, or are named more than once inside one of
                them.
              </Typography>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                {comparison.unmatched.map((entry) => (
                  <Tooltip
                    key={entry.key}
                    title={
                      entry.reason === 'duplicate'
                        ? 'This name appears more than once inside one analysis, so which date it refers to is not decidable.'
                        : `Only in: ${entry.presentIn.map((i) => runs[i].name).join(', ')}`
                    }
                  >
                    <Chip
                      label={entry.label}
                      size="small"
                      variant="outlined"
                      color={entry.reason === 'duplicate' ? 'warning' : 'default'}
                    />
                  </Tooltip>
                ))}
              </Box>
            </Paper>
          )}
        </>
      )}

      {unfinished.length === 0 && comparison && tab === 'model' && configDiff && treeDiff && (
        <>
          <Paper sx={{ p: 2.5, mb: 3 }}>
            <Typography variant="h6" gutterBottom>
              Model differences
            </Typography>
            <Typography variant="body2" color="textSecondary" sx={{ mb: 2, maxWidth: '72ch' }}>
              The stored settings for each analysis. A field
              marked ± indicates a difference.
            </Typography>
            <ModelDiffPanel
              runs={runs.map((run) => ({ id: run.id, name: run.name }))}
              runStyles={runStyles}
              fields={configDiff}
              elements={treeDiff.elements}
              unnamed={treeDiff.unnamed}
              trees={runs.map((run) => run.model_tree)}
              measurementsPerRun={runs.map((run) => run.measurements ?? [])}
              dateFormat={dateFormat}
              confidence={confidence}
              confidenceLabel={confidenceLabel}
            />
          </Paper>

          <Paper sx={{ p: 2.5, mb: 3 }}>
            <Typography variant="h6" gutterBottom>
              Findings
            </Typography>
            <Typography variant="body2" color="textSecondary" sx={{ mb: 2, maxWidth: '72ch' }}>
              A comparison of where the models put each transition on the calendar, their estimation of the duration of each 
              phase, and time span of dates in a phase.
            </Typography>
            <DerivedQuantitiesPanel
              runs={runs.map((run) => ({ id: run.id, name: run.name }))}
              runStyles={runStyles}
              rows={derivedRows}
              dateFormat={dateFormat}
              confidenceLabel={confidenceLabel}
            />
          </Paper>

          <Paper sx={{ p: 2.5 }}>
            <Typography variant="h6" gutterBottom>
              Date agreement changes
            </Typography>
            <Typography variant="body2" color="textSecondary" sx={{ mb: 2, maxWidth: '72ch' }}>
              This plot shows how far a date has been shifted in the compared model. Check to see if a model that produced a tighter estimate has had to move its dates further from their original calibration.
            </Typography>
            <AgreementScatter
              runs={runs.map((run) => ({ id: run.id, name: run.name }))}
              runStyles={runStyles}
              rows={comparedRows}
              reports={agreementRuns}
            />
          </Paper>
        </>
      )}
    </Container>
  )
}
