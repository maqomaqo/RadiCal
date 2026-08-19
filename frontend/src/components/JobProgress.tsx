/**
 * What a run in progress looks like.
 *
 * The convergence figure sits beside the bar rather than under it because the
 * bar alone is misleading: progress is measured against the sampler's cap,
 * and a run stops the moment it converges, so a perfectly good run typically
 * finishes at fifteen or twenty per cent. Without the convergence reading,
 * "done" arriving at 0.15 looks like something went wrong.
 */
import CloseIcon from '@mui/icons-material/Close'
import { Alert, Box, Button, LinearProgress, Stack, Typography } from '@mui/material'

import { describeJob, type Job } from '../api/jobs'

interface Props {
  job: Job
  onCancel?: () => void
  cancelling?: boolean
}

export default function JobProgress({ job, onCancel, cancelling }: Props) {
  const detail = job.detail || {}
  const percent = job.progress != null ? Math.round(job.progress * 100) : null

  // Queued and burn-in have nothing meaningful to show as a fraction yet, and
  // an indeterminate bar says "started, no estimate" honestly where a bar
  // pinned at zero says "stuck".
  const indeterminate = job.state === 'queued' || percent === null || detail.phase === 'burn_in'

  return (
    <Alert
      severity="info"
      icon={false}
      sx={{ mb: 3 }}
      action={
        onCancel && (
          <Button
            size="small"
            color="inherit"
            startIcon={<CloseIcon />}
            onClick={onCancel}
            disabled={cancelling}
          >
            {cancelling ? 'Stopping…' : 'Stop'}
          </Button>
        )
      }
    >
      <Typography variant="subtitle2" gutterBottom>
        {job.kind === 'project_rerun' ? 'Re-running this project' : 'Running this analysis'}
      </Typography>

      <Box sx={{ minWidth: 280, mb: 1 }}>
        <LinearProgress
          variant={indeterminate ? 'indeterminate' : 'determinate'}
          value={percent ?? 0}
        />
      </Box>

      <Stack direction="row" spacing={2} flexWrap="wrap">
        <Typography variant="body2">{describeJob(job)}</Typography>
        {detail.sweeps != null && (
          <Typography variant="body2" color="text.secondary">
            {detail.sweeps.toLocaleString()} sweeps
          </Typography>
        )}
      </Stack>

      {detail.phase === 'sampling' && (
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
          The bar measures progress towards the longest run allowed. Sampling stops as soon as
          it has converged, which is usually much sooner — so this will finish well before 100%.
        </Typography>
      )}
    </Alert>
  )
}
