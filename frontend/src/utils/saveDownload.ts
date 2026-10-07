import { formatApiError } from '@/utils/apiError'

/**
 * Fetch an export and save it under `filename`. The status is checked before
 * the body is read: an error response is JSON, which a spreadsheet opens
 * happily as a one-cell file and a PDF reader or unzipper fails on only when
 * opened -- so unchecked, a failed export is noticed by whoever opens it
 * looking for their results.
 */
export async function saveDownload(url: string, filename: string): Promise<void> {
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(formatApiError(await response.json().catch(() => null)))
  }
  const blob = await response.blob()
  const href = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = href
  a.download = filename
  document.body.appendChild(a)
  a.click()
  window.URL.revokeObjectURL(href)
  document.body.removeChild(a)
}
