// The fetch wrapper that carries the session: api/client.ts.
//
// This patches a global, which is the kind of thing that has to be pinned
// rather than trusted. Two properties matter and neither is visible from a
// reading of the call sites, because the call sites are all bare
// `fetch('/api/...')` and say nothing about headers at all:
//
//   1. Our API requests carry the token.
//   2. NOTHING ELSE DOES. A bearer token sent to a third party is how tokens
//      leak, so the tests for what is *not* touched carry more weight here
//      than the ones for what is.
//
// The 401 handling is the third: an expired token has to end the session
// once, rather than turning into a page full of silent failures -- but a 401
// from the login endpoint is an answer, not an expiry, and must not sign
// somebody out as they try to sign in.
import { beforeEach, describe, expect, it, vi } from 'vitest'

type Client = typeof import('./client')

const USER = {
  id: 'u1',
  email: 'someone@example.com',
  display_name: null,
  is_first_account: false,
  created_at: null,
}

/** The fetch every test asserts against, standing in for the network. */
let underlying: ReturnType<typeof vi.fn>
let client: Client

function lastHeaders(): Headers {
  const calls = underlying.mock.calls
  const init = calls[calls.length - 1]?.[1]
  return new Headers(init?.headers || {})
}

// A FRESH module per test, not a fresh call to installAuthenticatedFetch.
//
// The installer is idempotent on purpose -- React StrictMode mounts effects
// twice -- so calling it again after replacing window.fetch would leave the
// raw mock in place and quietly test nothing at all. Resetting the module is
// what makes each test see a real wrap of that test's own mock.
beforeEach(async () => {
  vi.resetModules()
  underlying = vi.fn(async () => new Response('{}', { status: 200 }))
  ;(globalThis as any).window = globalThis
  ;(globalThis as any).location = { origin: 'https://radical.example.com' }
  window.fetch = underlying as unknown as typeof fetch

  client = await import('./client')
  client.installAuthenticatedFetch()
  client.clearSession()
})

describe('what carries the token', () => {
  it('adds it to our own API requests', async () => {
    client.storeSession('a-token', USER)
    await fetch('/api/projects')
    expect(lastHeaders().get('Authorization')).toBe('Bearer a-token')
  })

  it('adds it to an absolute URL on our own origin', async () => {
    client.storeSession('a-token', USER)
    await fetch('https://radical.example.com/api/projects')
    expect(lastHeaders().get('Authorization')).toBe('Bearer a-token')
  })

  it('adds it to a Request object as well as a string', async () => {
    client.storeSession('a-token', USER)
    await fetch(new Request('https://radical.example.com/api/curves'))
    expect(lastHeaders().get('Authorization')).toBe('Bearer a-token')
  })

  it('keeps the caller’s other headers', async () => {
    client.storeSession('a-token', USER)
    await fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
    expect(lastHeaders().get('Content-Type')).toBe('application/json')
    expect(lastHeaders().get('Authorization')).toBe('Bearer a-token')
  })

  it('never overwrites an Authorization a caller set itself', async () => {
    client.storeSession('a-token', USER)
    await fetch('/api/projects', { headers: { Authorization: 'Bearer deliberate' } })
    expect(lastHeaders().get('Authorization')).toBe('Bearer deliberate')
  })
})

describe('what does not', () => {
  // The important half. Each of these is a way a token could leave the app.
  it('does not send it to another origin', async () => {
    client.storeSession('a-token', USER)
    await fetch('https://someone-else.example.net/api/projects')
    expect(lastHeaders().get('Authorization')).toBeNull()
  })

  it('does not send it to a path outside /api on our own origin', async () => {
    client.storeSession('a-token', USER)
    await fetch('/assets/logo.png')
    expect(lastHeaders().get('Authorization')).toBeNull()
  })

  it('is not fooled by an origin that merely starts the same way', async () => {
    client.storeSession('a-token', USER)
    await fetch('https://radical.example.com.evil.test/api/projects')
    expect(lastHeaders().get('Authorization')).toBeNull()
  })

  it('is not fooled by our origin appearing later in the URL', async () => {
    client.storeSession('a-token', USER)
    await fetch('https://evil.test/redirect?to=https://radical.example.com/api/projects')
    expect(lastHeaders().get('Authorization')).toBeNull()
  })

  it('sends nothing when nobody is signed in', async () => {
    await fetch('/api/projects')
    expect(lastHeaders().get('Authorization')).toBeNull()
  })
})

describe('when the session ends', () => {
  it('a 401 clears the token and says so once', async () => {
    client.storeSession('a-token', USER)
    const expired = vi.fn()
    client.whenSessionExpires(expired)
    underlying.mockResolvedValueOnce(new Response('{}', { status: 401 }))

    await fetch('/api/projects')

    expect(expired).toHaveBeenCalledTimes(1)
    expect(client.storedToken()).toBeNull()
    client.whenSessionExpires(null)
  })

  it('a 401 from signing in is an answer, not an expiry', async () => {
    // Otherwise a mistyped password signs you out of the attempt to sign in.
    const expired = vi.fn()
    client.whenSessionExpires(expired)
    underlying.mockResolvedValueOnce(new Response('{}', { status: 401 }))

    await fetch('/api/auth/login', { method: 'POST' })

    expect(expired).not.toHaveBeenCalled()
    client.whenSessionExpires(null)
  })

  it('leaves an ordinary error alone', async () => {
    client.storeSession('a-token', USER)
    const expired = vi.fn()
    client.whenSessionExpires(expired)
    underlying.mockResolvedValueOnce(new Response('{}', { status: 404 }))

    await fetch('/api/projects/nope')
    // Asserted through a following request rather than by reading the store:
    // "the session survived" means the next call still carries it, and in a
    // browser with site data blocked there is no store to read anyway.
    await fetch('/api/projects')

    expect(expired).not.toHaveBeenCalled()
    expect(lastHeaders().get('Authorization')).toBe('Bearer a-token')
    client.whenSessionExpires(null)
  })
})

describe('storage that will not cooperate', () => {
  it('a session still works for this tab when localStorage throws', async () => {
    // A private window with site data blocked throws on access rather than
    // returning null. The request path must not depend on it.
    const original = globalThis.localStorage
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      get() { throw new Error('blocked') },
    })

    client.storeSession('a-token', USER)
    await fetch('/api/projects')
    expect(lastHeaders().get('Authorization')).toBe('Bearer a-token')

    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true, value: original, writable: true,
    })
  })
})

describe('the desktop app, which has no accounts', () => {
  const answer = (single: boolean | undefined) => async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/api/auth/options')) {
      return new Response(JSON.stringify({ success: true, data: { password_reset_email: false, single_user: single } }))
    }
    if (url.endsWith('/api/auth/me')) {
      return new Response(JSON.stringify({ success: true, data: { ...USER, id: 'local' } }))
    }
    return new Response('{}')
  }

  it('goes straight in when the server says it serves one person', async () => {
    underlying.mockImplementation(answer(true))
    const user = await client.fetchCurrentUser()
    expect(user?.id).toBe('local')
    expect(client.isSingleUser()).toBe(true)
  })

  it('still asks a web server for a sign-in', async () => {
    underlying.mockImplementation(answer(undefined))
    expect(await client.fetchCurrentUser()).toBeNull()
    expect(client.isSingleUser()).toBe(false)
    expect(underlying.mock.calls.some(([u]) => String(u).endsWith('/api/auth/me'))).toBe(false)
  })
})
