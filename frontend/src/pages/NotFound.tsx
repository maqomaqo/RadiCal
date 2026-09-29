import { Button, Container, Paper, Stack, Typography } from '@mui/material'
import { Link as RouterLink, useLocation } from 'react-router-dom'

/**
 * Any address the router does not know.
 *
 * Without it an unmatched path rendered nothing under the app bar -- a blank
 * canvas that could as easily be a page still loading as a page that does not
 * exist. The most likely way here is a hand-edited or half-copied link, so the
 * page repeats the path back and offers the places a link usually points into.
 */
export default function NotFound() {
  const { pathname } = useLocation()
  return (
    <Container component="main" maxWidth="md" sx={{ py: 6 }}>
      <Paper sx={{ p: { xs: 3, sm: 4 } }}>
        <Typography variant="h2" component="h1" gutterBottom>
          No page at this address
        </Typography>
        <Typography color="text.secondary" sx={{ mb: 1, maxWidth: '60ch' }}>
          Nothing in RadiCal lives at{' '}
          <Typography
            component="code"
            sx={{
              fontFamily: 'monospace',
              fontSize: '0.875em',
              px: 0.5,
              borderRadius: 0.5,
              bgcolor: 'background.muted',
              color: 'text.primary',
              wordBreak: 'break-all',
            }}
          >
            {pathname}
          </Typography>
          . If you followed a link to an analysis or a project, it may have been
          deleted, or the link may have been cut short when it was copied.
        </Typography>
        <Stack direction="row" spacing={1.5} useFlexGap flexWrap="wrap" sx={{ mt: 3 }}>
          <Button variant="contained" component={RouterLink} to="/">
            Go to the dashboard
          </Button>
          <Button variant="outlined" component={RouterLink} to="/projects">
            Projects
          </Button>
          <Button variant="outlined" component={RouterLink} to="/curves">
            Curves
          </Button>
        </Stack>
      </Paper>
    </Container>
  )
}
