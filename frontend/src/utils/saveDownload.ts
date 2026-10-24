import { formatApiError } from '@/utils/apiError'
import { saveBlob } from '@/utils/saveFile'

/**
 * Fetch an export and save it under `filename`. The status is checked before
 * the body is read: an error response is JSON, which a spreadsheet opens
 * happily as a one-cell file and a PDF reader or unzipper fails on only when
 * opened -- so unchecked, a failed export is noticed by whoever opens it
 * looking for their results.
 *
 * Resolves to the response's headers, for an export that reports something
 * about the file beside it.
 */
export async function saveDownload(url: string, filename: string): Promise<Headers> {
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(formatApiError(await response.json().catch(() => null)))
  }
  await saveBlob(await response.blob(), filename)
  return response.headers
}
