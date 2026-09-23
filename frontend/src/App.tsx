import { lazy, Suspense } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { Box, CircularProgress, CssBaseline, ThemeProvider } from '@mui/material'
import { alpha, createTheme } from '@mui/material/styles'
import { WARM_INK } from '@/theme/diagramColors'
import { CANVAS, MUTED, PAPER } from '@/theme/surfaces'
import { NotificationProvider } from '@/contexts/NotificationContext'
import { AuthProvider, useAuth } from '@/contexts/AuthContext'
import Navigation from '@/components/Navigation'
import ErrorBoundary from '@/components/ErrorBoundary'
import Dashboard from '@/pages/Dashboard'
import SignIn from '@/pages/SignIn'

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

// `background.muted` is ours, not MUI's -- declared so `sx={{ backgroundColor:
// 'background.muted' }}` resolves and typechecks like any built-in token.
declare module '@mui/material/styles' {
  interface TypeBackground {
    /** A banded surface inside an elevated one: table headers, stat strips. */
    muted: string
  }
}

// MUI's default shadows are tuned (offset/blur/spread) per elevation level,
// but colored pure black -- recoloring them to the theme's own ink rather
// than replacing the 25-entry scale by hand keeps that tuning intact while
// making every elevated surface (Card, Menu, Dialog, Snackbar...) pick up
// the same warm cast as everything else, not just the components this file
// styles directly.
const warmShadows = createTheme().shadows.map((shadow) =>
  shadow.replace(/rgba\(0,\s*0,\s*0,/g, 'rgba(43, 35, 32,')
) as ReturnType<typeof createTheme>['shadows']

// Palette pulled from the app's own logo (frontend/public/RadiCal.png) --
// see the palette mockup this replaced -- rather than MUI's stock blue and
// pink, which the previous values (#1976d2 / #dc004e) were verbatim restating.
const PRIMARY = '#3c70b8'

const theme = createTheme({
  palette: {
    primary: {
      // The logo's pale blue (#f0f0fc) is a tint, not a usable interactive
      // color -- this is that same hue saturated up enough for buttons,
      // filled chips, and focus states.
      main: PRIMARY,
    },
    secondary: {
      // Darkened from the logo's own #f05460; the logo's exact coral is
      // reserved for illustration use, not interactive surfaces. Measures
      // 3.95:1 against white, so white-on-secondary clears AA for LARGE text
      // only -- see DESIGN.md, which used to claim body-text AA for it.
      main: '#e14a56',
    },
    // The semantic trio, shifted slightly from MUI's stock values to accommodate app colour clash/overlap.

    info: { main: PRIMARY },
    error: { main: '#a3231d' },
    warning: { main: '#a85c11' },
    background: {
      default: CANVAS,
      paper: PAPER,
      muted: MUTED,
    },
    // The shadows and the text below were warmed so these are too.
    // `divider` alone reaches every TableCell, every
    // `DialogContent dividers`, every Accordion border and some twenty
    // explicit borderColor sites.

    divider: alpha(WARM_INK, 0.14),
    action: {
      // Hover and the active-icon ink are the same substitution as divider.
      // The alphas are not MUI's own, because warm ink is lighter than black.
      // Each is the alpha that MATCHES the contrast of the value it replaces: 0.64 here
      // measures 4.75:1 on paper against black-at-0.54's 4.61:1 -- this is the
      // ink an unselected IconButton is drawn in, so it is a real graphic mark
      // and not a wash. 0.30 disabled measures 1.87:1 against 1.88:1.
      hover: alpha(WARM_INK, 0.05),
      hoverOpacity: 0.05,
      active: alpha(WARM_INK, 0.64),
      disabled: alpha(WARM_INK, 0.3),
      disabledBackground: alpha(WARM_INK, 0.14),
      // Selection and focus are the exception: they take the PRIMARY, not the
      // ink. The ordering matrix already reserves the primary blue for
      // selection and focus on its own grid; there was no reason that rule
      // stopped at one component. A selected row now reads as chosen rather
      // than as smudged.
      selected: alpha(PRIMARY, 0.1),
      selectedOpacity: 0.1,
      focus: alpha(PRIMARY, 0.12),
      focusOpacity: 0.12,
    },
    // MUI's defaults here are pure black at opacity (rgba(0,0,0,0.87) /
    // 0.6), the same cool-neutral-on-a-warm-canvas mismatch the shadows and
    // background.muted above were written to fix -- and text is far more of
    // the page than either.
    //
    // This is a warmth change, not a contrast fix: the defaults already pass
    // AA on this canvas (15.15:1 and 5.62:1). The replacements are the same
    // warm ink as the shadows, at alphas chosen not to give any of that
    // margin back -- 0.92 measures 11.39:1 on the canvas and 0.74 measures
    // 6.33:1, so secondary text ends up slightly better than the default it
    // replaces rather than trading legibility for tone. `disabled` is below
    // AA by design, as MUI's own default is; disabled text is exempt, and
    // making it pass would stop it reading as disabled.
    text: {
      primary: 'rgba(43, 35, 32, 0.92)',
      secondary: 'rgba(43, 35, 32, 0.74)',
      disabled: 'rgba(43, 35, 32, 0.40)',
    },
  },
  shape: {
    borderRadius: 8,
  },
  components: {
    // The surfaces the app does not draw, which ship in colours belonging to
    // no design system. The selection highlight in particular was a THIRD
    // blue on screen -- the browser's own -- beside the primary and the info
    // blue this palette just merged.
    MuiCssBaseline: {
      styleOverrides: {
        '::selection': {
          backgroundColor: alpha(PRIMARY, 0.24),
          // Not white: at 0.24 the highlight is a wash, and the text under it
          // stays the text it was.
          color: 'inherit',
        },
        // The caret is a mark the user makes, so it takes the ink the app
        // writes in rather than the browser's black.
        'input, textarea, [contenteditable]': { caretColor: WARM_INK },
        // MUI v5 gives a focused Button a ripple and very little else, so the
        // visible focus ring on most controls is whatever the browser draws.
        // One ring, in the colour this palette reserves for focus, offset far
        // enough to clear an 8px radius.
        ':focus-visible': {
          outline: `2px solid ${PRIMARY}`,
          outlineOffset: '2px',
        },
      },
    },
    // The one place the palette cannot reach. MUI v5's OutlinedInput hardcodes
    // its resting border as `rgba(0,0,0,0.23)` instead of reading `divider`,
    // so every text field in the app kept a pure-black outline after every
    // other line in the theme had been warmed. 0.27 is the alpha that matches
    // its contrast (1.738:1 against black-at-0.23's 1.744:1), not its number.
    // Hover and focus are MUI's own (text.primary, then primary at 2px) and
    // are left alone.
    MuiOutlinedInput: {
      styleOverrides: {
        notchedOutline: { borderColor: alpha(WARM_INK, 0.27) },
      },
    },
    MuiButton: {
      styleOverrides: {
        // Material's stock uppercasing is the loudest un-customized default
        // left in the app: every button on every page shouts, including the
        // three nav links and the primary action. The label is written in
        // the product's own words -- "New Analysis", not "NEW ANALYSIS" --
        // and uppercase also throws away the ascender/descender shapes that
        // make a label quick to recognize.
        root: { textTransform: 'none' },
      },
    },
  },
  shadows: warmShadows,
  typography: {
    fontFamily: 'Roboto, sans-serif',
    h1: { fontSize: '2.5rem', fontWeight: 600 },
    h2: { fontSize: '2rem', fontWeight: 600 },
  },
})

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
  if (!user) return <SignIn />
  return (
    <Router>
      <Navigation />
      <RoutedPages />
    </Router>
  )
}

function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      {/* NotificationProvider outside AuthProvider: signing in and out both
          report through it, and the auth layer unmounts everything under it
          when the session ends. */}
      <NotificationProvider>
        <AuthProvider>
          <AppShell />
        </AuthProvider>
      </NotificationProvider>
    </ThemeProvider>
  )
}

export default App
