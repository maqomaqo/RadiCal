import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import { CssBaseline, ThemeProvider, createTheme } from '@mui/material'
import { NotificationProvider } from '@/contexts/NotificationContext'
import Navigation from '@/components/Navigation'
import Dashboard from '@/pages/Dashboard'
import NewAnalysis from '@/pages/NewAnalysis'
import Stratigraphy from '@/pages/Stratigraphy'
import Results from '@/pages/Results'
import Projects from '@/pages/Projects'
import Curves from '@/pages/Curves'
import CurveBrowser from '@/pages/CurveBrowser'
import ProjectDetail from '@/pages/ProjectDetail'
import Reference from '@/pages/Reference'

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
const theme = createTheme({
  palette: {
    primary: {
      // The logo's pale blue (#f0f0fc) is a tint, not a usable interactive
      // color -- this is that same hue saturated up enough for buttons,
      // filled chips, and focus states.
      //
      // Darkened from #4a7dc4, which measured 4.18:1 against white and 3.89:1
      // against the canvas -- below AA's 4.5:1 for normal text in BOTH
      // directions, so every AppBar link (white on blue) and every text button
      // (blue on paper) failed on one token. Same hue (215 degrees) and same
      // saturation, one step down in lightness: 5.00:1 on white, 4.65:1 on the
      // canvas. Secondary was darkened for exactly this reason and the primary
      // never was.
      main: '#3c70b8',
    },
    secondary: {
      // Darkened from the logo's own #f05460 for AA contrast as white-on-
      // secondary text; the logo's exact coral is reserved for
      // illustration use, not interactive surfaces.
      main: '#e14a56',
    },
    background: {
      // A warm-tinted canvas instead of MUI's white default, so elevated
      // white Cards/Paper actually read as lifted off the page rather than
      // blending into it.
      default: '#faf6f3',
      // A banded surface INSIDE an elevated white one -- table header rows,
      // a stat strip under a chart. Every one of these used MUI's grey.100
      // (#f5f5f5), a perfectly neutral R=G=B that is the one palette this
      // theme exists to replace, sitting on the warmest background in the
      // app. Warmer and a step darker than the canvas so it reads as a band
      // on paper rather than as a hole in it.
      muted: '#f4ede7',
    },
    // MUI's defaults here are pure black at opacity (rgba(0,0,0,0.87) /
    // 0.6), the same cool-neutral-on-a-warm-canvas mismatch the shadows and
    // background.muted above were written to fix -- and text is far more of
    // the page than either.
    //
    // This is a warmth change, not a contrast fix: the defaults already pass
    // AA on this canvas (15.15:1 and 5.62:1). The replacements are the same
    // warm ink as the shadows, at alphas chosen not to give any of that
    // margin back -- 0.92 measures 11.34:1 on the canvas and 0.74 measures
    // 6.30:1, so secondary text ends up slightly better than the default it
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

function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <NotificationProvider>
        <Router>
          <Navigation />
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
            <Route path="/reference" element={<Reference />} />
            <Route path="/curves/:curveId/browse" element={<CurveBrowser />} />
            <Route path="/projects/:projectId" element={<ProjectDetail />} />
          </Routes>
        </Router>
      </NotificationProvider>
    </ThemeProvider>
  )
}

export default App
