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
 * How long polls may keep FAILING before a tab left open stops asking: past
 * this the more likely explanation is that the backend went away, and there
 * is no point polling an unreachable server until the tab is closed.
 *
 * Measured from the last poll that answered, not from when polling began. A
 * job that is still answering is not unreachable however long it runs -- an
 * 800-date model at four chains takes twenty minutes and more -- and a limit
 * on the run's length froze the display mid-run while the job went on to
 * finish behind it.
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
 * `onFinished` fires once, when a job STOPS — whatever it stopped as. It is
 * how the page knows to go and fetch the results it has been waiting for.
 *
 * "Stops" is load-bearing. Opening a completed analysis finds a terminal job
 * in `latestJobFor`, and treating that as a stop would fire onFinished on every
 * cold load, re-fetching the whole analysis: a run already over when the page
 * opened was never being waited for. An explicit `jobId` is different -- the
 * caller just started that run, so a job terminal on the first poll is still
 * the event they asked about.
 */
export function useJob(
  { jobId, targetId, onFinished }:
  { jobId?: string | null; targetId?: string | null; onFinished?: (job: Job) => void },
): UseJob {
  const [job, setJob] = useState<Job | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // When a poll last got an answer; the give-up window is measured from here.
  const lastAnswer = useRef<number>(Date.now())
  // Held in a ref so a caller passing an inline arrow function does not
  // restart the poll on every render.
  const finishedCallback = useRef(onFinished)
  finishedCallback.current = onFinished
  const alreadyFinished = useRef(false)
  // Whether this hook has ever seen the job non-terminal. Only meaningful in
  // the targetId case; see the note above onFinished.
  const sawActive = useRef(false)

  const stop = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current)
      timer.current = null
    }
  }, [])

  const poll = useCallback(async () => {
    try {
      const next = jobId ? await getJob(jobId) : targetId ? await latestJobFor(targetId) : null
      lastAnswer.current = Date.now()
      if (next) {
        setJob(next)
        if (isFinished(next.state)) {
          stop()
          const wasWaitedFor = Boolean(jobId) || sawActive.current
          if (!alreadyFinished.current && wasWaitedFor) {
            alreadyFinished.current = true
            finishedCallback.current?.(next)
          }
          return
        }
        sawActive.current = true
      } else {
        // No job for this target: nothing is running or to wait for.
        stop()
        return
      }
    } catch {
      // A failed poll is not a failed run: the next tick tries again, and the
      // give-up window below bounds how long that goes on.
    }
    if (Date.now() - lastAnswer.current > GIVE_UP_AFTER_MS) {
      stop()
      return
    }
    timer.current = setTimeout(poll, POLL_INTERVAL_MS)
  }, [jobId, targetId, stop])

  useEffect(() => {
    if (!jobId && !targetId) return
    lastAnswer.current = Date.now()
    alreadyFinished.current = false
    sawActive.current = false
    poll()
    return stop
  }, [jobId, targetId, poll, stop])

  return {
    job,
    active: job !== null && !isFinished(job.state),
    refresh: poll,
  }
}
