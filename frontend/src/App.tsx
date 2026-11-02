import { lazy, Suspense, type ReactNode } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { Box, CircularProgress } from '@mui/material'
import { ColorModeProvider } from '@/contexts/ColorModeContext'
import { NotificationProvider } from '@/contexts/NotificationContext'
import { AuthProvider, useAuth } from '@/contexts/AuthContext'
import Navigation from '@/components/Navigation'
import DesktopBridge from '@/desktop/DesktopBridge'
import { DESKTOP } from '@/desktop/shell'
import ErrorBoundary from '@/components/ErrorBoundary'
import Dashboard from '@/pages/Dashboard'
import SignIn, { isResetLink } from '@/pages/SignIn'

// Every page but the two a visit can open on is its own chunk, fetched the
// first time it is navigated to. Most of them draw with Plotly, which is
// about 4.5 MB of the bundle on its own; imported eagerly it was in the one
// file every visit downloaded and parsed before the dashboard -- which draws
// nothing with it -- could appear. Dashboard and SignIn stay in the entry
// chunk because one or the other is the first thing on screen.
const NewAnalysis = lazy(() => import('@/pages/NewAnalysis'))
const Stratigraphy = lazy(() => import('@/pages/Stratigraphy'))
const Results = lazy(() => import('@/pages/Results'))
const Projects = lazy(() => import('@/pages/Projects'))
const Curves = lazy(() => import('@/pages/Curves'))
const CurveBrowser = lazy(() => import('@/pages/CurveBrowser'))
const ProjectDetail = lazy(() => import('@/pages/ProjectDetail'))
const CompareResults = lazy(() => import('@/pages/CompareResults'))
const Help = lazy(() => import('@/pages/Help'))
const Glossary = lazy(() => import('@/pages/Glossary'))
const Account = lazy(() => import('@/pages/Account'))
const NotFound = lazy(() => import('@/pages/NotFound'))

/** Shown while a page's chunk is on its way; below the nav, which stays put. */
function PageLoading() {
  return (
    <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }} role="status" aria-label="Loading page">
      <CircularProgress />
    </Box>
  )
}

/**
 * The routed pages, with a boundary around them.
 *
 * Split out of App only so it can call useLocation, which has to be inside
 * the Router. The pathname is the boundary's reset key: a page that throws
 * would otherwise leave the fallback showing over every page navigated to
 * afterwards, since nothing else clears it.
 *
 * The boundary sits INSIDE the Router and BELOW Navigation deliberately.
 * Below, so a broken page still leaves the user something to navigate with;
 * inside, so the fallback's own links are real routes rather than reloads.
 */
function RoutedPages() {
  const location = useLocation()
  return (
    <ErrorBoundary resetKey={location.pathname}>
      {/* Inside the boundary, so a chunk that fails to load (a deploy
          replaced it mid-visit, the network dropped) gets the same fallback
          as a page that throws, rather than a blank screen. */}
      <Suspense fallback={<PageLoading />}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/new-analysis" element={<NewAnalysis />} />
          <Route path="/new-analysis/model-structure" element={<Stratigraphy />} />
          {/* Editing a saved analysis reuses the same two pages as creating
              one -- NewAnalysis loads the saved inputs into the shared draft
              store, and Stratigraphy edits them exactly as for a new
              analysis. See NewAnalysis's edit-mode effect. */}
          <Route path="/analysis/:analysisId/edit" element={<NewAnalysis />} />
          <Route path="/analysis/:analysisId/edit/model-structure" element={<Stratigraphy />} />
          <Route path="/results/:analysisId" element={<Results />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/curves" element={<Curves />} />
          <Route path="/help" element={<Help />} />
          <Route path="/help/glossary" element={<Glossary />} />
          {/* The glossary was the whole of /reference before the manual
              joined it; anything bookmarked or linked to that path is
              still asking for the same page. */}
          <Route path="/reference" element={<Navigate to="/help/glossary" replace />} />
          <Route path="/curves/:curveId/browse" element={<CurveBrowser />} />
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
          <Route path="/projects/:projectId/compare" element={<CompareResults />} />
          <Route path="/account" element={<Account />} />
          {/* Without this an unknown path rendered an empty canvas under the
              bar, indistinguishable from a page still loading. */}
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  )
}

/**
 * The app, or the sign-in screen.
 *
 * Nothing in RadiCal is readable without an account, so this is a swap rather
 * than a redirect: there is no route to send an anonymous visitor to that
 * would show them anything. Not navigating also means a bookmarked deep link
 * survives signing in -- the URL is untouched throughout, so the page it names
 * is what appears afterwards.
 *
 * Navigation is inside the signed-in branch: its links go to pages that
 * require an account, and offering them to somebody who has not signed in
 * would be offering a row of 401s.
 */
function AppShell() {
  const { user } = useAuth()
  // A reset link wins even over a session: it may be for another account, and
  // its owner opened it to set a password, not to see the app.
  if (!user || isResetLink()) return <SignIn />
  return (
    <Router>
      <DesktopBridge />
      <Box
        sx={
          DESKTOP
            ? { height: '100vh', display: 'flex', flexDirection: 'column' }
            : undefined
        }
      >
        <Navigation />
        <PageScroller>
          <RoutedPages />
        </PageScroller>
      </Box>
    </Router>
  )
}

/**
 * In the desktop app, the region the pages scroll in: the window's height
 * less the app bar's, so the scrollbar starts below the bar rather than
 * running up beside it. In a browser, nothing -- the document scrolls, as a
 * web page's should. See `DESKTOP` in desktop/shell.ts.
 *
 * `position: relative` makes the region the containing block of anything
 * absolutely positioned inside it with no positioned ancestor of its own --
 * the visually hidden table captions, for one. Without it their containing
 * block is the window: they sit where they fall on the long page, stretch
 * the document past the window and give it a second scrollbar.
 */
function PageScroller({ children }: { children: ReactNode }) {
  if (!DESKTOP) return <>{children}</>
  return (
    <Box sx={{ flex: 1, minHeight: 0, overflowY: 'auto', position: 'relative' }}>{children}</Box>
  )
}

function App() {
  return (
    <ColorModeProvider>
      {/* NotificationProvider outside AuthProvider: signing in and out both
          report through it, and the auth layer unmounts everything under it
          when the session ends. */}
      <NotificationProvider>
        <AuthProvider>
          <AppShell />
        </AuthProvider>
      </NotificationProvider>
    </ColorModeProvider>
  )
}

export default App
