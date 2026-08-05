import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import { CssBaseline, ThemeProvider, createTheme } from '@mui/material'
import Navigation from '@/components/Navigation'
import Dashboard from '@/pages/Dashboard'
import NewAnalysis from '@/pages/NewAnalysis'
import Stratigraphy from '@/pages/Stratigraphy'
import Results from '@/pages/Results'
import Projects from '@/pages/Projects'
import ProjectDetail from '@/pages/ProjectDetail'

const theme = createTheme({
  palette: {
    primary: {
      main: '#1976d2',
    },
    secondary: {
      main: '#dc004e',
    },
  },
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
      <Router>
        <Navigation />
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/new-analysis" element={<NewAnalysis />} />
          <Route path="/new-analysis/stratigraphy" element={<Stratigraphy />} />
          {/* Editing a saved analysis reuses the same two pages as creating
              one -- NewAnalysis loads the saved inputs into the shared draft
              store, and Stratigraphy edits them exactly as for a new
              analysis. See NewAnalysis's edit-mode effect. */}
          <Route path="/analysis/:analysisId/edit" element={<NewAnalysis />} />
          <Route path="/analysis/:analysisId/edit/stratigraphy" element={<Stratigraphy />} />
          <Route path="/results/:analysisId" element={<Results />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/:projectId" element={<ProjectDetail />} />
        </Routes>
      </Router>
    </ThemeProvider>
  )
}

export default App
