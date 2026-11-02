import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { CssBaseline, ThemeProvider } from '@mui/material'

import { createAppTheme } from '@/theme/appTheme'
import type { ColorMode } from '@/theme/surfaces'

/**
 * Light, dark, or whichever the system is in.
 *
 * "System" is the default and a real choice rather than the absence of one: a
 * visitor who has never opened the control gets the mode their machine is in,
 * and keeps getting it when that changes at sunset. Choosing Light or Dark
 * pins it, and the pin survives a reload.
 */
export type ColorModePreference = 'system' | 'light' | 'dark'

interface ColorModeContextValue {
  /** What the visitor chose. */
  preference: ColorModePreference
  /** What is drawn: the preference, with "system" resolved. */
  mode: ColorMode
  setPreference: (preference: ColorModePreference) => void
}

const ColorModeContext = createContext<ColorModeContextValue | null>(null)

/**
 * Kept in localStorage rather than in the account: it is a property of this
 * screen and this person's eyes, not of what they have saved, and it has to be
 * known before anything is fetched. Unavailable in private browsing or with
 * storage full, in which case the choice lasts as long as the page does.
 */
const STORAGE_KEY = 'radical.colorMode'

function storedPreference(): ColorModePreference {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  } catch {
    // Unavailable; fall through to the default.
  }
  return 'system'
}

const DARK_QUERY = '(prefers-color-scheme: dark)'

function systemMode(): ColorMode {
  return typeof window !== 'undefined' && window.matchMedia?.(DARK_QUERY).matches
    ? 'dark'
    : 'light'
}

/** Follows the system's mode for as long as the component using it is mounted. */
function useSystemMode(): ColorMode {
  const [mode, setMode] = useState<ColorMode>(systemMode)
  useEffect(() => {
    const query = window.matchMedia?.(DARK_QUERY)
    if (!query) return
    const onChange = () => setMode(query.matches ? 'dark' : 'light')
    onChange()
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])
  return mode
}

const THEMES: Record<ColorMode, ReturnType<typeof createAppTheme>> = {
  light: createAppTheme('light'),
  dark: createAppTheme('dark'),
}

/**
 * The theme for the chosen mode, and the control's state. Wraps everything
 * that draws, the sign-in screen included: it has no account to read a
 * preference from, and it is the first thing a returning visitor sees.
 */
export function ColorModeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ColorModePreference>(storedPreference)
  const system = useSystemMode()
  const mode: ColorMode = preference === 'system' ? system : preference

  const setPreference = useCallback((next: ColorModePreference) => {
    setPreferenceState(next)
    try {
      window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // The choice holds for this page only.
    }
  }, [])

  // The page behind the app -- the margin an overscroll shows, the first paint
  // of a reload -- is the canvas of the mode, not the browser's white.
  useEffect(() => {
    document.documentElement.style.backgroundColor = THEMES[mode].palette.background.default
  }, [mode])

  const value = useMemo(() => ({ preference, mode, setPreference }), [preference, mode, setPreference])

  return (
    <ColorModeContext.Provider value={value}>
      <ThemeProvider theme={THEMES[mode]}>
        {/* enableColorScheme sets `color-scheme` on the root, which is what
            turns the browser's own scrollbars, form controls and autofill
            dark along with the page. */}
        <CssBaseline enableColorScheme />
        {children}
      </ThemeProvider>
    </ColorModeContext.Provider>
  )
}

export function useColorMode(): ColorModeContextValue {
  const value = useContext(ColorModeContext)
  if (!value) throw new Error('useColorMode must be used inside a ColorModeProvider')
  return value
}
