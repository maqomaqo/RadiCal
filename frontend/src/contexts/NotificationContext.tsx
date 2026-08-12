import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Alert, Snackbar } from '@mui/material'
import type { AlertColor } from '@mui/material'

interface Notice {
  key: number
  message: string
  severity: AlertColor
}

interface NotificationContextValue {
  /** Show a dismissible toast. Defaults to 'error' -- nearly every call site
   * is reporting a failed request, so that's the useful default rather than
   * requiring it at each of them. */
  notify: (message: string, severity?: AlertColor) => void
}

const NotificationContext = createContext<NotificationContextValue | null>(null)

/**
 * Replaces window.alert() for reporting request outcomes: a dismissible,
 * non-blocking toast instead of a modal that halts the page (and, in a
 * background tab, blocks it silently until the user switches back). Message
 * text is unchanged from the alert() calls it replaces -- only the
 * presentation moved.
 *
 * Queued rather than a single slot: two failures in quick succession (e.g.
 * deleting two rows back to back) would otherwise have the second silently
 * overwrite the first before it had been read. Modelled on MUI's own
 * consecutive-Snackbar recipe.
 */
export function NotificationProvider({ children }: { children: ReactNode }) {
  const [queue, setQueue] = useState<Notice[]>([])
  const [current, setCurrent] = useState<Notice | null>(null)
  const [open, setOpen] = useState(false)

  const notify = useCallback((message: string, severity: AlertColor = 'error') => {
    setQueue((prev) => [...prev, { key: Date.now() + Math.random(), message, severity }])
  }, [])

  // Advance the queue once the current toast has fully closed (not just
  // started closing) -- popping the next one in immediately would change
  // the outgoing toast's content mid-exit-transition.
  const handleExited = useCallback(() => {
    setQueue((prev) => {
      const [, ...rest] = prev
      return rest
    })
    setCurrent(null)
  }, [])

  useEffect(() => {
    if (queue.length > 0 && !current) {
      setCurrent(queue[0])
      setOpen(true)
    } else if (queue.length > 0 && current && queue[0].key !== current.key && open) {
      setOpen(false)
    }
  }, [queue, current, open])

  const handleClose = useCallback((_: unknown, reason?: string) => {
    if (reason === 'clickaway') return
    setOpen(false)
  }, [])

  const value = useMemo(() => ({ notify }), [notify])

  return (
    <NotificationContext.Provider value={value}>
      {children}
      <Snackbar
        key={current?.key}
        open={open}
        autoHideDuration={current?.severity === 'error' ? 8000 : 5000}
        onClose={handleClose}
        TransitionProps={{ onExited: handleExited }}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          onClose={() => handleClose(undefined)}
          severity={current?.severity}
          variant="filled"
          sx={{ width: '100%' }}
        >
          {current?.message}
        </Alert>
      </Snackbar>
    </NotificationContext.Provider>
  )
}

export function useNotify() {
  const ctx = useContext(NotificationContext)
  if (!ctx) throw new Error('useNotify must be used within a NotificationProvider')
  return ctx.notify
}
