import { useEffect, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Link,
  MenuItem,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import { Link as RouterLink } from 'react-router-dom'
import AddIcon from '@mui/icons-material/Add'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import { useAuth } from '@/contexts/AuthContext'
import { useNotify } from '@/contexts/NotificationContext'
import ConfirmDialog from '@/components/ConfirmDialog'
import { describeFetchError, formatApiError } from '@/utils/apiError'
import { absoluteTime, parseServerTime, relativeTime } from '@/utils/serverTime'

/**
 * The signed-in account, and its personal access tokens: long-lived
 * credentials for scripts (api/auth.py). The secret is shown once, in the
 * dialog that follows making one, because the server keeps only its hash.
 */

interface AccessToken {
  id: string
  name: string
  prefix: string
  scope: 'read' | 'full'
  created_at: string | null
  expires_at: string
  last_used_at: string | null
  expired: boolean
}

const LIFETIMES = [7, 30, 90, 365]

/** An expiry as a date: relativeTime reads only backwards. */
function expiryDate(iso: string): string {
  return parseServerTime(iso).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}
const DEFAULT_LIFETIME = 90

export default function Account() {
  const { user } = useAuth()
  const notify = useNotify()
  const [tokens, setTokens] = useState<AccessToken[] | null>(null)
  const [making, setMaking] = useState(false)
  const [name, setName] = useState('')
  const [scope, setScope] = useState<'read' | 'full'>('read')
  const [days, setDays] = useState(DEFAULT_LIFETIME)
  const [saving, setSaving] = useState(false)
  const [made, setMade] = useState<string | null>(null)
  const [revoking, setRevoking] = useState<AccessToken | null>(null)

  const load = async () => {
    try {
      const response = await fetch('/api/auth/tokens')
      const data = await response.json()
      if (!response.ok) throw new Error(formatApiError(data))
      setTokens(data.data)
    } catch (error) {
      notify('Could not load your access tokens: ' + describeFetchError(error))
      setTokens([])
    }
  }

  useEffect(() => {
    load()
  }, [])

  const create = async () => {
    setSaving(true)
    try {
      const response = await fetch('/api/auth/tokens', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), scope, days }),
      })
      const data = await response.json()
      if (!response.ok) {
        notify('Could not make the token: ' + formatApiError(data))
        return
      }
      setMaking(false)
      setName('')
      setScope('read')
      setDays(DEFAULT_LIFETIME)
      setMade(data.data.token)
      load()
    } catch (error) {
      notify('Could not make the token: ' + describeFetchError(error))
    } finally {
      setSaving(false)
    }
  }

  const revoke = async (token: AccessToken) => {
    try {
      const response = await fetch(`/api/auth/tokens/${token.id}`, { method: 'DELETE' })
      if (!response.ok) {
        notify('Could not revoke the token: ' + formatApiError(await response.json()))
        return
      }
      setRevoking(null)
      load()
    } catch (error) {
      notify('Could not revoke the token: ' + describeFetchError(error))
    }
  }

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      notify('Copied.')
    } catch {
      notify('Could not copy. Select the token and copy it by hand.')
    }
  }

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Paper sx={{ p: 3, mb: 3 }}>
        <Typography variant="h1" component="h1" gutterBottom>
          Account
        </Typography>
        <Typography variant="body1" color="textSecondary">
          Signed in as {user?.email}
          {user?.display_name ? ` (${user.display_name})` : ''}.
        </Typography>
      </Paper>

      <Paper sx={{ p: 3 }}>
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            flexWrap: 'wrap',
            gap: 2,
            mb: 2,
          }}
        >
          <Box sx={{ maxWidth: '72ch' }}>
            <Typography variant="h6" gutterBottom>
              Access tokens
            </Typography>
            <Typography variant="body2" color="textSecondary">
              For scripts that use the API. See{' '}
              <Link component={RouterLink} to="/help#scripts-and-access-tokens">
                scripts and access tokens
              </Link>{' '}
              in the manual.
            </Typography>
          </Box>
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setMaking(true)}>
            New token
          </Button>
        </Box>

        {tokens && tokens.length === 0 && (
          <Typography variant="body2" color="textSecondary">
            You have no access tokens.
          </Typography>
        )}
        {tokens && tokens.length > 0 && (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Name</TableCell>
                  <TableCell>Token</TableCell>
                  <TableCell>Access</TableCell>
                  <TableCell>Expires</TableCell>
                  <TableCell>Last used</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {tokens.map((token) => (
                  <TableRow key={token.id}>
                    <TableCell>{token.name}</TableCell>
                    <TableCell sx={{ fontFamily: 'monospace', whiteSpace: 'nowrap' }}>
                      {token.prefix}…
                    </TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>
                      {token.scope === 'full' ? 'Read and write' : 'Read only'}
                    </TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>
                      {token.expired ? (
                        <Chip size="small" color="warning" label="Expired" />
                      ) : (
                        <Tooltip title={absoluteTime(token.expires_at)}>
                          <span>{expiryDate(token.expires_at)}</span>
                        </Tooltip>
                      )}
                    </TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>
                      {token.last_used_at ? relativeTime(token.last_used_at) : 'Never'}
                    </TableCell>
                    <TableCell align="right">
                      <Button size="small" color="error" onClick={() => setRevoking(token)}>
                        Revoke
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      <Dialog open={making} onClose={() => setMaking(false)} fullWidth maxWidth="sm">
        <DialogTitle>New access token</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '8px !important' }}>
          <TextField
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            helperText="What it is for, such as “overnight re-run”."
            inputProps={{ maxLength: 100 }}
            autoFocus
          />
          <TextField
            select
            label="Access"
            value={scope}
            onChange={(e) => setScope(e.target.value as 'read' | 'full')}
          >
            <MenuItem value="read">Read only</MenuItem>
            <MenuItem value="full">Read and write</MenuItem>
          </TextField>
          <TextField
            select
            label="Expires after"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            {LIFETIMES.map((value) => (
              <MenuItem key={value} value={value}>
                {value === 365 ? '1 year' : `${value} days`}
              </MenuItem>
            ))}
          </TextField>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setMaking(false)}>Cancel</Button>
          <Button variant="contained" onClick={create} disabled={!name.trim() || saving}>
            Make token
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={made !== null} onClose={() => setMade(null)} fullWidth maxWidth="sm">
        <DialogTitle>Your new token</DialogTitle>
        <DialogContent>
          <Alert severity="warning" sx={{ mb: 2 }}>
            Copy it now. It will not be shown again.
          </Alert>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <TextField
              value={made ?? ''}
              fullWidth
              size="small"
              InputProps={{ readOnly: true, sx: { fontFamily: 'monospace' } }}
              inputProps={{ 'aria-label': 'Access token' }}
              onFocus={(e) => e.target.select()}
            />
            <Tooltip title="Copy">
              <IconButton aria-label="Copy token" onClick={() => made && copy(made)}>
                <ContentCopyIcon />
              </IconButton>
            </Tooltip>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button variant="contained" onClick={() => setMade(null)}>
            Done
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={revoking !== null}
        title="Revoke this token?"
        message={
          revoking
            ? `“${revoking.name}” stops working at once. Anything using it will need a new token.`
            : ''
        }
        confirmLabel="Revoke"
        danger
        onConfirm={() => revoking && revoke(revoking)}
        onCancel={() => setRevoking(null)}
      />
    </Container>
  )
}
