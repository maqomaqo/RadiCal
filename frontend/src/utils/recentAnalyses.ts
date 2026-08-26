import { formatApiError } from './apiError'

/**
 * Loading the Dashboard's recent analyses, and saying precisely what went
 * wrong when it does not work.
 *
 * Its own function because the failure branches are the point: swallowing them
 * into an empty array renders as "No analyses yet", telling a researcher whose
 * backend is unreachable that their work does not exist. Distinguishing "we
 * could not ask", "the server refused" and "you have none yet" is the job.
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
    // fetch rejects only when the request got no answer at all -- offline,
    // DNS, TLS, server down -- so there is nothing the server said to report.
    return {
      ok: false,
      error: { message: "Can't reach the server. Check your connection, then try again." },
    }
  }

  let data: unknown
  try {
    data = await response.json()
  } catch {
    // A non-JSON body is usually a proxy or crash page rather than this API,
    // so the status is the only honest thing to report.
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
 * formatApiError owns the shape-handling -- FastAPI's `detail` may be a
 * string, an object, or a list of validation errors. This only guards the case
 * it cannot know about: with no `detail` at all it falls back to
 * JSON.stringify of the whole body, printing the response envelope back at the
 * reader.
 */
function describeFailure(data: unknown): string | undefined {
  const detail = (data as { detail?: unknown } | null | undefined)?.detail
  if (detail === undefined || detail === null || detail === '') return undefined
  return formatApiError(data) || undefined
}
