/**
 * Follow a background job until it stops.
 *
 * Polling rather than a push channel: a run takes tens of seconds, and once a
 * second is finer than anyone reads a progress bar. A WebSocket would add a
 * connection to hold open and a reconnect to get right for the same three
 * facts.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { getJob, isFinished, latestJobFor, type Job } from '../api/jobs'

const POLL_INTERVAL_MS = 1000

/**
 * A run at the sampler's cap takes about five minutes on the largest model
 * measured. Ten is the point at which the more likely explanation is that the
 * backend went away, and a tab left open should stop asking rather than poll
 * an unreachable server until it is closed.
 */
const GIVE_UP_AFTER_MS = 10 * 60 * 1000

interface UseJob {
  job: Job | null
  /** True while a job is queued or running — i.e. show a progress display. */
  active: boolean
  /** Re-read now, without waiting for the next tick. */
  refresh: () => void
}

/**
 * Watch one job by id, or the most recent job for a target (an analysis or a
 * project) when no id is known — which is the case for a page opened from a
 * bookmark rather than reached by starting the run.
 *
 * `onFinished` fires once, when the job stops, whatever it stopped as. It is
 * how the page knows to go and fetch the results it has been waiting for.
 */
export function useJob(
  { jobId, targetId, onFinished }:
  { jobId?: string | null; targetId?: string | null; onFinished?: (job: Job) => void },
): UseJob {
  const [job, setJob] = useState<Job | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const startedAt = useRef<number>(Date.now())
  // Held in a ref so a caller passing an inline arrow function does not
  // restart the poll on every render.
  const finishedCallback = useRef(onFinished)
  finishedCallback.current = onFinished
  const alreadyFinished = useRef(false)

  const stop = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current)
      timer.current = null
    }
  }, [])

  const poll = useCallback(async () => {
    try {
      const next = jobId ? await getJob(jobId) : targetId ? await latestJobFor(targetId) : null
      if (next) {
        setJob(next)
        if (isFinished(next.state)) {
          stop()
          if (!alreadyFinished.current) {
            alreadyFinished.current = true
            finishedCallback.current?.(next)
          }
          return
        }
      } else {
        // No job at all for this target: nothing is running, and there is
        // nothing to wait for.
        stop()
        return
      }
    } catch {
      // A failed poll is not a failed run — the next tick tries again, and
      // the give-up window below bounds how long that goes on for.
    }
    if (Date.now() - startedAt.current > GIVE_UP_AFTER_MS) {
      stop()
      return
    }
    timer.current = setTimeout(poll, POLL_INTERVAL_MS)
  }, [jobId, targetId, stop])

  useEffect(() => {
    if (!jobId && !targetId) return
    startedAt.current = Date.now()
    alreadyFinished.current = false
    poll()
    return stop
  }, [jobId, targetId, poll, stop])

  return {
    job,
    active: job !== null && !isFinished(job.state),
    refresh: poll,
  }
}
