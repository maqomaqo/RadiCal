/**
 * Format an error response from the backend into a single readable
 * string, for use in alerts/toasts.
 *
 * FastAPI's `detail` is a plain string for most errors (anything raised
 * as `HTTPException(status_code, "message")`), but for a 422 validation
 * failure -- the kind schemas/sample.py's CalibrateRequest/BayesianRequest
 * now produce for bad input (negative age, zero error, unknown curve_id,
 * etc.) -- `detail` is instead a *list* of structured objects, e.g.
 * `[{"loc": ["body", "age_bp"], "msg": "Input should be >= 0", ...}]`.
 * Concatenating that list straight into a message string renders
 * "[object Object]" rather than anything useful, which would undercut
 * the point of returning a specific validation error in the first place.
 * This normalizes either shape into one readable string.
 */
export function formatApiError(data: unknown): string {
  const detail = (data as { detail?: unknown } | null | undefined)?.detail

  if (typeof detail === 'string') return detail

  // The curve endpoints raise a structured detail -- a message plus the
  // parse errors or the list of analyses using a curve -- because a refusal
  // that only says "409" leaves the user with nothing to act on. Read the
  // message, and append the specifics when they are short enough to be a
  // toast rather than a report.
  if (detail && typeof detail === 'object' && !Array.isArray(detail) && 'message' in detail) {
    const { message, errors } = detail as { message: unknown; errors?: unknown }
    if (Array.isArray(errors) && errors.length > 0) {
      return `${String(message)} ${errors.slice(0, 3).join(' ')}`
    }
    return String(message)
  }

  if (Array.isArray(detail)) {
    const messages = detail.map((err) => {
      if (err && typeof err === 'object' && 'msg' in err) {
        const loc = 'loc' in err && Array.isArray((err as { loc: unknown[] }).loc)
          ? (err as { loc: unknown[] }).loc.filter((p) => p !== 'body').join('.')
          : ''
        const msg = String((err as { msg: unknown }).msg)
        return loc ? `${loc}: ${msg}` : msg
      }
      return String(err)
    })
    return messages.join('; ')
  }

  try {
    return JSON.stringify(data)
  } catch {
    return String(data)
  }
}
