/**
 * Reading the API's timestamps, and saying how long ago they were.
 *
 * Both halves live here rather than inside one page because the project grid
 * and the project page now report the same activity: a card saying "today"
 * that opens onto a page saying "yesterday" is the drift this module exists to
 * make impossible.
 */

/**
 * The API writes `datetime.utcnow().isoformat()`, which carries no offset --
 * and a bare timestamp is parsed by JS as LOCAL time, not UTC, so west of
 * Greenwich every date read hours older than it is and "today" became
 * "yesterday" for a whole evening. Read it as the UTC it is.
 */
export function parseServerTime(iso: string): Date {
  return new Date(/[Z+]|-\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`)
}

/**
 * Recent activity is relative because that is the question ("has anyone
 * touched this lately?"); past a month it becomes a date, because "in 87 days"
 * is arithmetic the reader has to undo.
 */
export function relativeTime(iso: string | null): string {
  if (!iso) return ''
  const then = parseServerTime(iso)
  const days = Math.floor((Date.now() - then.getTime()) / 86_400_000)
  if (days < 1) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 30) return `${days} days ago`
  return then.toLocaleDateString(undefined, { month: 'short', year: 'numeric' })
}

/**
 * The full timestamp, for the `title` of anything showing the relative form.
 * The relative phrasing is the scannable answer; the exact moment is the one a
 * reader occasionally needs to cite, and it should never require a round trip
 * to another screen to get.
 */
export function absoluteTime(iso: string | null): string {
  if (!iso) return ''
  return parseServerTime(iso).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}
