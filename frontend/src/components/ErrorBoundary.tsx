import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { Alert, AlertTitle, Box, Button, Container, Paper, Stack, Typography } from '@mui/material'
import RefreshIcon from '@mui/icons-material/Refresh'
import HomeIcon from '@mui/icons-material/Home'

/**
 * The last resort for a render-time exception.
 *
 * Every page already has try/catch around its requests and an error state to
 * show what came back, so the gap this fills is the other kind of failure: an
 * exception thrown while React is rendering. Those are not catchable by any
 * of that -- React unmounts the whole tree and leaves a blank white page, with
 * the actual error visible only in the console -- and a blank page is the one
 * failure mode a user cannot report usefully, because there is nothing on it
 * to describe.
 *
 * A class component because that is the only thing React offers: there is no
 * hook equivalent of componentDidCatch, and there has been no sign of one.
 *
 * Deliberately NOT a replacement for the pages' own error states. A failed
 * request is an expected outcome with a known remedy and belongs where the
 * user asked for the thing; this is for the unexpected, and says so rather
 * than pretending to know what went wrong.
 */

interface Props {
  children: ReactNode
  /**
   * Remounts the subtree when it changes, so navigating away from a page that
   * threw clears the error instead of pinning it. App.tsx passes the current
   * pathname: without it, one bad route would leave the boundary showing its
   * fallback over every subsequent page, since nothing else ever resets it.
   */
  resetKey?: string
}

interface State {
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Kept: the console is where this would have gone anyway, and the
    // component stack is the half that says WHERE, which the message alone
    // never does.
    console.error('Unhandled render error:', error, info.componentStack)
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <Container maxWidth="md" sx={{ py: 6 }}>
        <Paper sx={{ p: 4 }}>
          <Alert severity="error" sx={{ mb: 3 }}>
            <AlertTitle>This page stopped rather than showing something wrong</AlertTitle>
            Nothing you have saved is affected — this is a fault in displaying the
            page, not in the analysis behind it. Your projects, analyses and results
            are unchanged on the server.
          </Alert>

          <Typography variant="body2" color="text.secondary" gutterBottom>
            Reloading usually clears it. If it happens again on the same page, the
            detail below is the useful part of a bug report.
          </Typography>

          {/*
            The message is shown, the stack is not. The message is often
            legible ("Cannot read properties of undefined") and is what makes
            two occurrences comparable; a minified stack is neither, and
            putting it on screen invites people to send a screenshot of it
            instead of the console output that actually has the frames.
          */}
          <Box
            component="pre"
            sx={{
              mt: 2,
              mb: 3,
              p: 2,
              borderRadius: 1,
              bgcolor: 'background.muted',
              fontSize: '0.8125rem',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {error.message || String(error)}
          </Box>

          <Stack direction="row" spacing={2}>
            <Button
              variant="contained"
              startIcon={<RefreshIcon />}
              onClick={() => window.location.reload()}
            >
              Reload the page
            </Button>
            {/*
              A full navigation rather than a router push: the router lives
              inside the tree that just threw, and asking a broken tree to
              re-render itself somewhere else is how a boundary gets stuck
              showing its own fallback forever.
            */}
            <Button
              startIcon={<HomeIcon />}
              onClick={() => { window.location.href = '/' }}
            >
              Go to the dashboard
            </Button>
          </Stack>
        </Paper>
      </Container>
    )
  }
}
