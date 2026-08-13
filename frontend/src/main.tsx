import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.tsx'
// The theme declares 'Roboto, sans-serif' (App.tsx) but nothing previously
// loaded the font itself, so every browser silently substituted its own
// sans-serif default (Helvetica on macOS, Arial on Windows) -- the theme's
// choice of font was requested but never actually applied. Weights cover
// what MUI's default typography variants (300-500 across the heading
// levels, 400 for body text, 500 for Button) and this app's own theme
// overrides actually call for; 700 kept as headroom for real emphasis.
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
