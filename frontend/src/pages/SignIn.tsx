import { useState } from 'react'
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

import { useAuth } from '@/contexts/AuthContext'

/**
 * Signing in, and creating an account.
 *
 * One screen for both, because they are the same three fields and the same
 * decision -- "do I already have one of these?" -- and two pages would make a
 * first-time visitor guess which one they are on.
 *
 * Shown INSTEAD of the app rather than at a /sign-in route: with nothing
 * readable without an account, a router that could land on any other page
 * would only ever redirect back here. That also means signing in does not
 * navigate; the app simply appears, and a bookmarked deep link still works
 * because the URL was never changed.
 */
export default function SignIn() {
  const { signIn, register } = useAuth()
  const [creating, setCreating] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [adopted, setAdopted] = useState<number | null>(null)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      if (creating) {
        const count = await register(email, password, displayName)
        if (count > 0) setAdopted(count)
      } else {
        await signIn(email, password)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in did not work. Check the email and password and try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Container maxWidth="sm" sx={{ py: 10 }}>
      <Paper sx={{ p: 4 }}>
        <Typography variant="h5" gutterBottom>
          {creating ? 'Create an account' : 'Sign in to RadiCal'}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
          {creating
            ? 'Your projects and analyses are yours alone — nobody else who uses this installation can see them.'
            : 'Your projects and analyses are private to your account.'}
        </Typography>

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

        <Box component="form" onSubmit={submit}>
          <Stack spacing={2}>
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
            {creating && (
              <TextField
                label="Your name (optional)"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                autoComplete="name"
                fullWidth
              />
            )}
            <TextField
              label="Password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              // Tells a password manager which of the two this is, so it
              // offers to save a new one rather than autofilling an old one.
              autoComplete={creating ? 'new-password' : 'current-password'}
              required
              fullWidth
              helperText={creating ? 'At least 8 characters. Length beats punctuation.' : undefined}
            />
            <Button type="submit" variant="contained" size="large" disabled={busy}>
              {busy ? 'Working…' : creating ? 'Create account' : 'Sign in'}
            </Button>
          </Stack>
        </Box>

        <Typography variant="body2" sx={{ mt: 3 }} color="text.secondary">
          {creating ? 'Already have an account? ' : 'No account yet? '}
          <Link
            component="button"
            type="button"
            onClick={() => { setCreating(!creating); setError(null) }}
          >
            {creating ? 'Sign in' : 'Create one'}
          </Link>
        </Typography>
      </Paper>
    </Container>
  )
}
