import { useEffect, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Container,
  Link,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'

import { canEmailResetLinks, requestPasswordReset } from '@/api/client'
import { useAuth } from '@/contexts/AuthContext'

/**
 * Whether the page was opened from a password-reset email. The link is
 * /reset-password#token=..., the token in the fragment so it never reaches a
 * server log (see backend services/mail.py).
 */
export function isResetLink(): boolean {
  return window.location.pathname === '/reset-password'
}

function resetTokenFromUrl(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get('token')
}

type Mode = 'signin' | 'create' | 'forgot' | 'reset'

/**
 * Signing in, creating an account, and a forgotten password.
 *
 * One screen for the first two, because they are the same three fields and the
 * same decision -- "do I already have one of these?" -- and two pages would
 * make a first-time visitor guess which one they are on. The forgotten
 * password lives here too, since the people who need it cannot reach anything
 * else.
 *
 * Shown INSTEAD of the app rather than at a /sign-in route: with nothing
 * readable without an account, a router that could land on any other page
 * would only ever redirect back here. That also means signing in does not
 * navigate; the app simply appears, and a bookmarked deep link still works
 * because the URL was never changed. The one exception is a reset link, whose
 * address is cleared once the new password is set.
 */
export default function SignIn() {
  const { signIn, register, resetPassword } = useAuth()
  const [mode, setMode] = useState<Mode>(() => (isResetLink() ? 'reset' : 'signin'))
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [adopted, setAdopted] = useState<number | null>(null)
  // Null until asked: whether this installation can email a reset link.
  const [canEmail, setCanEmail] = useState<boolean | null>(null)
  const [linkSent, setLinkSent] = useState(false)
  const [resetToken] = useState(resetTokenFromUrl)

  useEffect(() => {
    if (mode !== 'forgot' || canEmail !== null) return
    let cancelled = false
    canEmailResetLinks().then((answer) => { if (!cancelled) setCanEmail(answer) })
    return () => { cancelled = true }
  }, [mode, canEmail])

  const switchTo = (next: Mode) => {
    if (mode === 'reset') window.history.replaceState(null, '', '/')
    setMode(next)
    setError(null)
    setLinkSent(false)
    setPassword('')
    setConfirm('')
  }

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError(null)
    if (mode === 'reset' && password !== confirm) {
      setError('The two passwords do not match.')
      return
    }
    setBusy(true)
    try {
      if (mode === 'create') {
        const count = await register(email, password, displayName)
        if (count > 0) setAdopted(count)
      } else if (mode === 'forgot') {
        await requestPasswordReset(email)
        setLinkSent(true)
      } else if (mode === 'reset') {
        await resetPassword(resetToken ?? '', password)
      } else {
        await signIn(email, password)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in did not work. Check the email and password and try again.')
    } finally {
      setBusy(false)
    }
  }

  const title = {
    signin: 'Sign in to RadiCal',
    create: 'Create an account',
    forgot: 'Reset your password',
    reset: 'Choose a new password',
  }[mode]

  return (
    <Container maxWidth="sm" sx={{ py: 10 }}>
      <Paper sx={{ p: 4 }}>
        <Typography variant="h5" gutterBottom>{title}</Typography>
        {(mode === 'signin' || mode === 'create') && (
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            {mode === 'create'
              ? 'Your projects and analyses are yours alone — nobody else who uses this installation can see them.'
              : 'Your projects and analyses are private to your account.'}
          </Typography>
        )}

        {adopted !== null && (
          // Only ever shown to the first account on an install that already
          // had work in it. Without this, that person's first sight of the app
          // after an upgrade is a set of analyses they cannot account for.
          <Alert severity="success" sx={{ mb: 3 }}>
            {adopted} existing {adopted === 1 ? 'project or analysis' : 'projects and analyses'}{' '}
            from before this installation had accounts {adopted === 1 ? 'is' : 'are'} now
            yours.
          </Alert>
        )}

        {error && <Alert severity="error" sx={{ mb: 3 }}>{error}</Alert>}

        {mode === 'forgot' && canEmail === null && (
          <Typography variant="body2" color="text.secondary">Checking…</Typography>
        )}

        {mode === 'forgot' && canEmail === false && (
          <Stack spacing={2}>
            <Typography variant="body2">
              This installation cannot send email, so ask whoever runs RadiCal to set a new
              password for you.
            </Typography>
            <Typography variant="body2" color="text.secondary">
              If that is you, run this in the RadiCal folder, with your email address:
            </Typography>
            <Box
              component="code"
              sx={{ p: 1.5, bgcolor: 'action.hover', borderRadius: 1, fontSize: '0.85rem', overflowWrap: 'anywhere' }}
            >
              docker compose exec radical python -m app.reset_password you@example.org
            </Box>
          </Stack>
        )}

        {mode === 'forgot' && linkSent && (
          <Alert severity="success">
            If there is an account for {email}, a reset link is on its way. It works for an hour.
          </Alert>
        )}

        {mode === 'reset' && !resetToken && (
          <Alert severity="error">
            This link is incomplete. Open the whole link from the email, or ask for a new one.
          </Alert>
        )}

        {((mode === 'forgot' && canEmail && !linkSent) || mode === 'signin' || mode === 'create'
          || (mode === 'reset' && resetToken)) && (
          <Box component="form" onSubmit={submit}>
            <Stack spacing={2}>
              {mode === 'forgot' && (
                <Typography variant="body2" color="text.secondary">
                  Enter your account's email address and we will send you a link to choose a new
                  password.
                </Typography>
              )}
              {mode !== 'reset' && (
                <TextField
                  label="Email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="username"
                  required
                  fullWidth
                  autoFocus
                />
              )}
              {mode === 'create' && (
                <TextField
                  label="Your name (optional)"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  autoComplete="name"
                  fullWidth
                />
              )}
              {mode !== 'forgot' && (
                <TextField
                  label={mode === 'reset' ? 'New password' : 'Password'}
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  // Tells a password manager which of the two this is, so it
                  // offers to save a new one rather than autofilling an old one.
                  autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
                  required
                  fullWidth
                  autoFocus={mode === 'reset'}
                  helperText={mode === 'signin' ? undefined : 'At least 8 characters. Length beats punctuation.'}
                />
              )}
              {mode === 'reset' && (
                <TextField
                  label="Same again"
                  type="password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  autoComplete="new-password"
                  required
                  fullWidth
                />
              )}
              <Button type="submit" variant="contained" size="large" disabled={busy}>
                {busy
                  ? 'Working…'
                  : { signin: 'Sign in', create: 'Create account', forgot: 'Email me a link', reset: 'Set password and sign in' }[mode]}
              </Button>
            </Stack>
          </Box>
        )}

        {mode === 'signin' && (
          <Typography variant="body2" sx={{ mt: 2 }}>
            <Link component="button" type="button" onClick={() => switchTo('forgot')}>
              Forgot your password?
            </Link>
          </Typography>
        )}

        <Typography variant="body2" sx={{ mt: mode === 'signin' ? 1 : 3 }} color="text.secondary">
          {mode === 'signin' && 'No account yet? '}
          {mode === 'create' && 'Already have an account? '}
          <Link
            component="button"
            type="button"
            onClick={() => switchTo(mode === 'signin' ? 'create' : 'signin')}
          >
            {mode === 'signin' ? 'Create one' : mode === 'create' ? 'Sign in' : 'Back to sign in'}
          </Link>
        </Typography>
      </Paper>
    </Container>
  )
}
