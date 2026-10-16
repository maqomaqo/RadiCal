import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Box, CircularProgress } from '@mui/material'

import {
  type AuthUser,
  clearSession,
  fetchCurrentUser,
  installAuthenticatedFetch,
  signIn as apiSignIn,
  register as apiRegister,
  resetPassword as apiResetPassword,
  storedUser,
  whenSessionExpires,
} from '@/api/client'

/**
 * Who is signed in, for the whole app.
 *
 * Deliberately thin. The token itself never comes through here -- it lives in
 * api/client.ts, which is also the only thing that reads it -- so no component
 * can accidentally put it somewhere it does not belong. What this exposes is
 * the question a component actually asks: is somebody signed in, and who.
 *
 * The startup sequence matters and is the reason for the `checking` state:
 *
 *   1. Install the fetch wrapper BEFORE anything can make a request.
 *   2. If there is a stored token, ask the server whether it still works.
 *   3. Only then render the app, or the sign-in screen.
 *
 * Skipping step 2 would let a stale token render the whole chrome and then
 * fail every request on the first page at once, which reads as the app being
 * broken rather than as a session having ended.
 */

interface AuthContextValue {
  user: AuthUser | null
  signIn: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, displayName?: string) => Promise<number>
  resetPassword: (token: string, newPassword: string) => Promise<void>
  signOut: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  // Installed at module evaluation of this component's first render rather
  // than in an effect: an effect runs AFTER the children have mounted, and a
  // page that fetches on mount would beat it.
  installAuthenticatedFetch()

  // Seeded from what was stored, so a returning user does not flash the
  // sign-in screen before the check below confirms them.
  const [user, setUser] = useState<AuthUser | null>(() => storedUser())
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    let cancelled = false
    fetchCurrentUser().then((confirmed) => {
      if (cancelled) return
      // Null means no token, or one the server no longer accepts. Either way
      // the seeded user above was wrong and has to go.
      if (!confirmed) clearSession()
      setUser(confirmed)
      setChecking(false)
    })
    return () => { cancelled = true }
  }, [])

  // A 401 from anywhere in the app lands here, so an expired token shows the
  // sign-in screen rather than a page full of failed requests.
  useEffect(() => {
    whenSessionExpires(() => setUser(null))
    return () => whenSessionExpires(null)
  }, [])

  const signIn = useCallback(async (email: string, password: string) => {
    const { user: signedIn } = await apiSignIn(email, password)
    setUser(signedIn)
  }, [])

  const register = useCallback(
    async (email: string, password: string, displayName?: string) => {
      const result = await apiRegister(email, password, displayName)
      setUser(result.user)
      // How many pre-existing projects and analyses this account adopted. Only
      // ever non-zero for the first account on an install that had work in it
      // before there were users, and worth telling that person: it is the
      // difference between "my analyses are gone" and "they are mine now".
      return result.adopted_existing_records ?? 0
    },
    []
  )

  const resetPassword = useCallback(async (token: string, newPassword: string) => {
    const { user: signedIn } = await apiResetPassword(token, newPassword)
    // Off the reset link's address before the app appears: it names no page,
    // and leaving it would show the reset form again on the next render.
    window.history.replaceState(null, '', '/')
    setUser(signedIn)
  }, [])

  const signOut = useCallback(() => {
    clearSession()
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, signIn, register, resetPassword, signOut }),
    [user, signIn, register, resetPassword, signOut]
  )

  if (checking) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', pt: 12 }}>
        <CircularProgress />
      </Box>
    )
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside an AuthProvider')
  return context
}
