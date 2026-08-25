// Loading the Dashboard's recent analyses.
//
// The bug this guards against is a specific one, and it shipped: every
// failure branch fell through to an empty array, which the page rendered as
// "No analyses yet. Create one to get started." A returning researcher whose
// backend was down was told in a reassuring voice that their work did not
// exist -- the exact inverse of the product's own "fail loud and specific"
// principle, which the backend follows.
//
// So what is pinned here is that the three outcomes stay three: an
// unreachable server, a server that refused, and a genuinely empty account
// must never produce the same result.
import { describe, expect, it } from 'vitest'

import { loadRecentAnalyses } from './recentAnalyses'

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

const ok = (items: unknown[], total = items.length) =>
  jsonResponse({ success: true, data: { items, total } })

describe('loadRecentAnalyses', () => {
  it('returns the items and the total on success', async () => {
    const result = await loadRecentAnalyses(5, async () => ok([{ id: 'a' }, { id: 'b' }], 15))
    expect(result).toEqual({ ok: true, items: [{ id: 'a' }, { id: 'b' }], total: 15 })
  })

  it('reports an empty account as success, not as an error', async () => {
    const result = await loadRecentAnalyses(5, async () => ok([], 0))
    expect(result).toEqual({ ok: true, items: [], total: 0 })
  })

  it('names an unreachable server without quoting the browser at the user', async () => {
    const result = await loadRecentAnalyses(5, async () => {
      throw new TypeError('Failed to fetch')
    })
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.error.message).toBe(
      "Can't reach the server. Check your connection, then try again."
    )
    // "Failed to fetch" names an API the reader never called.
    expect(JSON.stringify(result.error)).not.toContain('Failed to fetch')
    expect(result.error.detail).toBeUndefined()
  })

  it('passes on what the server said when it refused', async () => {
    const result = await loadRecentAnalyses(5, async () =>
      jsonResponse({ success: false, detail: 'Database connection lost' }, 500)
    )
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.error.message).toBe('The server could not list your recent analyses.')
    expect(result.error.detail).toBe('Database connection lost')
  })

  it('flattens FastAPI validation errors into the detail line', async () => {
    const result = await loadRecentAnalyses(5, async () =>
      jsonResponse(
        { success: false, detail: [{ loc: ['body', 'per_page'], msg: 'Input should be >= 1' }] },
        422
      )
    )
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.error.detail).toBe('per_page: Input should be >= 1')
  })

  it('omits the detail line rather than dumping the response body', async () => {
    // formatApiError stringifies the whole body when there is no `detail`.
    // On this endpoint that would print the response envelope at the reader.
    const result = await loadRecentAnalyses(5, async () => jsonResponse({ success: false }, 500))
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.error.detail).toBeUndefined()
  })

  it('reports the status when the body is not JSON at all', async () => {
    // A proxy or crash page, not this API.
    const result = await loadRecentAnalyses(5, async () =>
      new Response('<html>502 Bad Gateway</html>', { status: 502 })
    )
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.error.message).toBe('The server returned an error (502).')
  })

  it('distinguishes an unreadable 200 from a server error', async () => {
    const result = await loadRecentAnalyses(5, async () => new Response('not json', { status: 200 }))
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.error.message).toBe('The server sent a response RadiCal could not read.')
  })

  it('treats a 200 carrying success:false as a refusal', async () => {
    const result = await loadRecentAnalyses(5, async () =>
      jsonResponse({ success: false, detail: 'Nope' }, 200)
    )
    expect(result.ok).toBe(false)
  })

  it('asks for the page size it was given', async () => {
    let seen = ''
    await loadRecentAnalyses(5, async (url) => {
      seen = url
      return ok([])
    })
    expect(seen).toBe('/api/analysis?per_page=5')
  })
})
