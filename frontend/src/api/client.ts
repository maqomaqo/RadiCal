/**
 * The signed-in session, and how the token reaches the server.
 *
 * Every request in this app is a bare `fetch('/api/...')` -- 64 of them across
 * 13 files, with no shared client to hang a header on. Rather than edit all of
 * them, this installs the header ONCE by wrapping `window.fetch`.
 *
 * That is a deliberate choice and worth defending, because patching a global
 * is usually the wrong instinct. Two reasons it is right here:
 *
 * - **A new call site cannot forget it.** The failure mode of the alternative
 *   is one `fetch` added later without the header, which fails as a 401 in
 *   whatever corner of the app that call lives in. This is the same argument
 *   the backend's test_auth.TestEveryRouteIsProtected makes for asserting over
 *   the route table instead of keeping a list.
 * - **It is narrowly scoped.** Only same-origin `/api/` requests are touched,
 *   and only to add one header. Anything else -- a CDN font, a data: URL, an
 *   absolute URL to somewhere else -- passes through untouched, which matters
 *   because sending a bearer token to a third party is how tokens leak.
 *
 * The 401 handler is the other half. A token expires after twelve hours, and
 * without something watching for that the app would fill with silent failures
 * on every page at once. Instead one 401 clears the session, and the app shows
 * the sign-in screen.
 */

const TOKEN_KEY = 'radical.token'
const USER_KEY = 'radical.user'

export interface AuthUser {
  id: string
  email: string
  display_name: string | null
  is_first_account: boolean
  created_at: string | null
}

/**
 * localStorage rather than a cookie, and rather than memory.
 *
 * Not a cookie, because the API authenticates with a bearer header and a
 * cookie would need CSRF protection this app does not have. Not memory,
 * because a reload would sign the user out, and a reload is how anyone
 * recovers from anything.
 *
 * The trade is that a token is readable by script on this origin, so an XSS
 * would expose it. That is real, and the mitigation is the twelve-hour
 * expiry rather than a claim that it cannot happen.
 *
 * Every access is wrapped: localStorage throws outright in a private window
 * with site data blocked, and a sign-in page that crashes on load is worse
 * than one that cannot remember you.
 */
export function storedToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function storedUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as AuthUser) : null
  } catch {
    return null
  }
}

export function storeSession(token: string, user: AuthUser) {
  try {
    localStorage.setItem(TOKEN_KEY, token)
    localStorage.setItem(USER_KEY, JSON.stringify(user))
  } catch {
    // A session that cannot be remembered still works for this tab: the
    // in-memory copy below is what every request actually reads.
  }
  memoryToken = token
}

export function clearSession() {
  try {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  } catch {
    /* nothing to clear */
  }
  memoryToken = null
}

// Read on every request, so a storage failure or a cleared store does not
// silently sign the user out mid-session.
let memoryToken: string | null = storedToken()

/** Called when the server says the session is no longer good. */
type ExpiryHandler = () => void
let onExpired: ExpiryHandler | null = null

export function whenSessionExpires(handler: ExpiryHandler | null) {
  onExpired = handler
}

/**
 * Whether this request should carry the token.
 *
 * Same-origin and under /api only. Deliberately conservative: the cost of a
 * false negative is one 401, and the cost of a false positive is a bearer
 * token sent to somebody else's server.
 */
function isOurApi(input: RequestInfo | URL): boolean {
  try {
    const url =
      typeof input === 'string'
        ? new URL(input, window.location.origin)
        : input instanceof URL
          ? input
          : new URL(input.url, window.location.origin)
    return url.origin === window.location.origin && url.pathname.startsWith('/api/')
  } catch {
    return false
  }
}

// The endpoints that are reachable without a session. A 401 from one of these
// is the answer to a question -- "is this password right?" -- not an expired
// session, and treating it as one would sign the user out as they try to sign
// in.
const OPEN_PATHS = [
  '/api/auth/login',
  '/api/auth/register',
  '/api/auth/options',
  '/api/auth/forgot-password',
  '/api/auth/reset-password',
]

function isOpenPath(input: RequestInfo | URL): boolean {
  try {
    const url =
      typeof input === 'string'
        ? new URL(input, window.location.origin)
        : input instanceof URL
          ? input
          : new URL(input.url, window.location.origin)
    return OPEN_PATHS.includes(url.pathname)
  } catch {
    return false
  }
}

let installed = false

/**
 * Wrap `window.fetch` so every API request carries the session.
 *
 * Idempotent: calling it twice would otherwise nest the wrapper, and React's
 * StrictMode mounts effects twice in development.
 */
export function installAuthenticatedFetch() {
  if (installed) return
  installed = true

  const original = window.fetch.bind(window)

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    if (!isOurApi(input)) return original(input, init)

    const token = memoryToken ?? storedToken()
    let request = init
    if (token) {
      const headers = new Headers(init?.headers || {})
      // Never overwrite a header a caller set for itself.
      if (!headers.has('Authorization')) {
        headers.set('Authorization', `Bearer ${token}`)
      }
      request = { ...init, headers }
    }

    const response = await original(input, request)

    if (response.status === 401 && !isOpenPath(input)) {
      clearSession()
      onExpired?.()
    }
    return response
  }
}

/** POST /api/auth/login. Throws with the server's own message on failure. */
export async function signIn(email: string, password: string) {
  return submitCredentials('/api/auth/login', { email, password })
}

/** POST /api/auth/register. Registration is open; see api/auth.py. */
export async function register(email: string, password: string, displayName?: string) {
  return submitCredentials('/api/auth/register', {
    email,
    password,
    display_name: displayName || null,
  })
}

/** POST /api/auth/reset-password: a new password from a reset link, which also signs in. */
export async function resetPassword(token: string, newPassword: string) {
  return submitCredentials('/api/auth/reset-password', { token, new_password: newPassword })
}

/**
 * Whether this installation can email a reset link. False on any failure: the
 * sign-in page then offers the other way, asking whoever runs the server.
 */
export async function canEmailResetLinks(): Promise<boolean> {
  try {
    const response = await fetch('/api/auth/options')
    if (!response.ok) return false
    return Boolean((await response.json()).data?.password_reset_email)
  } catch {
    return false
  }
}

/** POST /api/auth/forgot-password. Succeeds whether or not the address has an account. */
export async function requestPasswordReset(email: string): Promise<void> {
  await postOpen('/api/auth/forgot-password', { email })
}

async function postOpen(path: string, body: Record<string, unknown>) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await response.json().catch(() => null)

  if (!response.ok) {
    // FastAPI's `detail` is a string for our own errors and a list of field
    // problems for a validation failure. Both have to end up as one sentence
    // a person can read.
    const detail = payload?.detail
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail) && detail[0]?.msg
          ? String(detail[0].msg)
          : 'That did not work. Please try again.'
    throw new Error(message)
  }
  return payload
}

async function submitCredentials(path: string, body: Record<string, unknown>) {
  const payload = await postOpen(path, body)
  const data = payload.data as {
    token: string
    user: AuthUser
    adopted_existing_records?: number
  }
  storeSession(data.token, data.user)
  return data
}

export async function signOut() {
  clearSession()
}

/**
 * Check a stored token still works, returning the user it belongs to.
 *
 * Run once at startup. A token that has expired since the last visit would
 * otherwise let the app render its whole chrome before every request on the
 * first page failed at once.
 */
export async function fetchCurrentUser(): Promise<AuthUser | null> {
  if (!memoryToken && !storedToken()) return null
  try {
    const response = await fetch('/api/auth/me')
    if (!response.ok) return null
    return (await response.json()).data as AuthUser
  } catch {
    // A network failure is not an expired session, but there is nothing to
    // show either way until the server answers.
    return null
  }
}
