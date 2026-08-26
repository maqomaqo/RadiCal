/**
 * Background jobs: a run that happens outside the request that asked for it.
 *
 * A Bayesian model takes tens of seconds and can take minutes, so the
 * endpoints that start one answer 202 with a job id rather than holding the
 * connection open until it finishes. This is how the pages find out what
 * happened to it. See docs/ASYNC_JOBS_PLAN.md.
 */

export type JobState = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface Job {
  job_id: string
  kind: 'analysis_run' | 'project_rerun'
  target_id: string | null
  state: JobState
  /**
   * 0..1, and for a sampled model an UPPER BOUND rather than a prediction: the
   * sampler stops as soon as it converges, so most runs finish well short of 1.
   * `detail.convergence` says how well, which is why the progress display shows
   * the two together -- a bar reaching 0.15 and saying "done" reads as a bug.
   */
  progress: number | null
  detail: JobDetail | null
  error_message: string | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

/** Whatever the running job wants to say about itself; shape varies by kind. */
export interface JobDetail {
  // analysis_run, while sampling
  phase?: 'burn_in' | 'sampling'
  sweeps?: number
  max_sweeps?: number
  convergence?: number
  // analysis_run, once finished
  analysis_id?: string
  solver?: string
  // project_rerun
  completed?: number
  total?: number
  failed?: { id: string; name: string; error: string }[]
}

/** A job in one of the three states that mean it has stopped. */
export const isFinished = (state: JobState) =>
  state === 'completed' || state === 'failed' || state === 'cancelled'

export async function getJob(jobId: string): Promise<Job> {
  const response = await fetch(`/api/jobs/${jobId}`)
  if (!response.ok) throw new Error(`Could not read job ${jobId}`)
  const body = await response.json()
  return body.data as Job
}

/**
 * The most recent job about one analysis or project, or null.
 *
 * What a page opened from a bookmark or reload needs: it arrives with an
 * analysis id and no job id, and still has to know whether something is
 * running before choosing between results and a progress bar.
 */
export async function latestJobFor(targetId: string): Promise<Job | null> {
  const response = await fetch(`/api/jobs?target_id=${encodeURIComponent(targetId)}&limit=1`)
  if (!response.ok) return null
  const body = await response.json()
  return (body.data?.jobs?.[0] as Job) ?? null
}

/**
 * Ask a job to stop.
 *
 * Cooperative, so not instant: a running solve stops at the end of its current
 * sampling round. Resolves when the request was accepted, not when the job
 * stopped -- keep polling for that. A 409 means it had already finished, which
 * is not worth showing as an error.
 */
export async function cancelJob(jobId: string): Promise<void> {
  const response = await fetch(`/api/jobs/${jobId}`, { method: 'DELETE' })
  if (!response.ok && response.status !== 409) {
    throw new Error('Could not cancel this run')
  }
}

/**
 * How a run in progress should be described in one line.
 *
 * Burn-in is called "settling": the person watching wants to know the run has
 * started, not what the sampler calls its first phase.
 */
export function describeJob(job: Job): string {
  if (job.state === 'queued') return 'Waiting for the queue'
  if (job.state === 'cancelled') return 'Stopped'
  if (job.state === 'failed') return job.error_message || 'This run did not finish'
  if (job.state === 'completed') return 'Finished'

  const detail = job.detail || {}
  if (detail.total !== undefined) {
    return `Re-running analyses: ${detail.completed ?? 0} of ${detail.total}`
  }
  if (detail.phase === 'burn_in') return 'Settling the sampler'
  if (detail.convergence !== undefined) {
    return `Sampling — convergence ${detail.convergence.toFixed(3)} of 0.95 needed`
  }
  return 'Running'
}
