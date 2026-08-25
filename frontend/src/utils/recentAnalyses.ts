import { formatApiError } from './apiError'

/**
 * Loading the Dashboard's recent analyses, and saying precisely what went
 * wrong when it doesn't work.
 *
 * This exists as its own function because the failure branches are the point.
 * The page used to swallow every one of them into an empty array, which the
 * Dashboard then rendered as "No analyses yet. Create one to get started." --
 * so a researcher whose backend was unreachable was told, in a friendly
 * voice, that their work did not exist. Distinguishing "we could not ask",
 * "the server refused", and "you have none yet" is the whole job, and it is
 * worth testing rather than eyeballing.
 */

export interface LoadError {
  /** What went wrong, in the product's own words. */
  message: string
  /**
   * The server's own words, when it had any worth repeating. Absent for a
   * network failure, where the only text available is the browser's
   * "Failed to fetch" -- which names an API the reader never called.
   */
  detail?: string
}

export type LoadOutcome<T> =
  | { ok: true; items: T[]; total: number }
  | { ok: false; error: LoadError }

type FetchLike = (input: string) => Promise<Response>

export async function loadRecentAnalyses<T>(
  perPage: number,
  fetchImpl: FetchLike = (input) => fetch(input)
): Promise<LoadOutcome<T>> {
  let response: Response
  try {
    response = await fetchImpl(`/api/analysis?per_page=${perPage}`)
  } catch {
    // fetch only rejects when the request never got an answer at all:
    // offline, DNS, TLS, the server down. Nothing the server said is
    // available, because it did not say anything.
    return {
      ok: false,
      error: { message: "Can't reach the server. Check your connection, then try again." },
    }
  }

  let data: unknown
  try {
    data = await response.json()
  } catch {
    // A body that isn't JSON is usually a proxy or a crash page rather than
    // this API, so the status is the only honest thing to report.
    return {
      ok: false,
      error: {
        message: response.ok
          ? 'The server sent a response RadiCal could not read.'
          : `The server returned an error (${response.status}).`,
      },
    }
  }

  const payload = data as { success?: boolean; data?: { items?: T[]; total?: number } }

  if (!response.ok || !payload.success) {
    return {
      ok: false,
      error: {
        message: 'The server could not list your recent analyses.',
        detail: describeFailure(data),
      },
    }
  }

  return {
    ok: true,
    items: payload.data?.items ?? [],
    total: payload.data?.total ?? 0,
  }
}

/**
 * The server's explanation, or nothing.
 *
 * formatApiError owns the shape-handling -- FastAPI's `detail` is a string,
 * a structured object, or a list of validation errors depending on what
 * failed -- so this does not reimplement any of it. It only guards the one
 * case that helper cannot know about: with no `detail` at all, formatApiError
 * falls back to JSON.stringify of the whole body, which on this endpoint
 * would print the response envelope back at the reader. A detail line that is
 * really a dump is worse than no detail line.
 */
function describeFailure(data: unknown): string | undefined {
  const detail = (data as { detail?: unknown } | null | undefined)?.detail
  if (detail === undefined || detail === null || detail === '') return undefined
  return formatApiError(data) || undefined
}
