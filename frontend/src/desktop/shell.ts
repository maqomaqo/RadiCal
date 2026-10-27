import { useEffect, useState } from 'react'
import { isTauri } from '@tauri-apps/api/core'
import { getCurrentWindow } from '@tauri-apps/api/window'

/**
 * The desktop app's window, as against a browser tab.
 *
 * In a browser the document scrolls and the app bar sticks to the top of it,
 * which puts the browser's scrollbar beside the bar as well as the page. A
 * desktop window is expected to keep its chrome out of the scrolling, so
 * there the page scrolls in a region of its own below the bar
 * (`PageScroller` in App.tsx), and the bar is not over anything that
 * scrolls.
 */
export const DESKTOP = isTauri()

/**
 * On macOS the window has no title bar of its own: the app bar runs to the
 * top edge and the window's close, minimise and zoom buttons sit inside it,
 * as they do in Safari or Finder. Two bars in two blues, with a hairline
 * between them, was what that replaced.
 */
export const MAC_TITLE_BAR = DESKTOP && navigator.userAgent.includes('Mac')

/**
 * Where a sticky band stops when it reaches the top of what scrolls: under
 * the app bar in a browser, where the bar is over the scrolling document,
 * and at the top of the page's own region in the desktop app, where it is
 * not. The browser values are MUI's Toolbar heights.
 */
export const STICKY_TOP = DESKTOP ? 0 : { xs: 56, sm: 64 }

/**
 * Call `listener` whenever the page scrolls, wherever the page scrolls --
 * the window in a browser, its own region in the desktop app. Captured at
 * the document, because an element's scroll events do not bubble. Returns
 * the way to stop.
 */
export function onPageScroll(listener: () => void): () => void {
  const options = { capture: true, passive: true }
  document.addEventListener('scroll', listener, options)
  return () => document.removeEventListener('scroll', listener, options)
}

/**
 * Whether the desktop app's window is in full screen, where macOS hides the
 * window's own buttons. Always false in a browser.
 */
export function useFullscreen(): boolean {
  const [fullscreen, setFullscreen] = useState(false)
  useEffect(() => {
    if (!DESKTOP) return
    const appWindow = getCurrentWindow()
    let stopped = false
    let stop: (() => void) | undefined
    const check = () =>
      void appWindow.isFullscreen().then((now) => {
        if (!stopped) setFullscreen(now)
      })
    // Entering and leaving full screen both resize the window.
    void appWindow.onResized(check).then((unlisten) => (stopped ? unlisten() : (stop = unlisten)))
    check()
    return () => {
      stopped = true
      stop?.()
    }
  }, [])
  return fullscreen
}
