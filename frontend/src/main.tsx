import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
// The theme declares 'Roboto, sans-serif' (App.tsx); these load the font
// itself, without which every browser substitutes its own sans-serif default.
// The weights cover what MUI's typography variants and this app's theme
// overrides call for, plus 700 as headroom for emphasis.
import '@fontsource/roboto/300.css'
import '@fontsource/roboto/400.css'
import '@fontsource/roboto/500.css'
import '@fontsource/roboto/700.css'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
