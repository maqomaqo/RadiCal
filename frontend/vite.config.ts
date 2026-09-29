/// <reference types="vitest" />
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

/**
 * Fetch the Plotly chunk while the browser is otherwise idle, so the first
 * page that plots finds it already in cache instead of waiting on 1.3 MB.
 *
 * A `<link rel="prefetch">`, not an idle-time `import()`: prefetch only
 * downloads, at the lowest priority the browser has, and never parses or
 * runs anything. An `import()` would also evaluate Plotly -- a couple of
 * hundred milliseconds of main-thread work that could land on someone's
 * click. `crossorigin` matches how the module is requested later, without
 * which the cached copy would not be reused. Build-only: the dev server
 * serves modules unbundled and there is no chunk to name.
 */
function prefetchPlotly(): Plugin {
  return {
    name: 'radical:prefetch-plotly',
    apply: 'build',
    transformIndexHtml(_html, ctx) {
      const chunk = Object.values(ctx.bundle ?? {}).find(
        (file) => file.type === 'chunk' && file.name === 'plotly'
      )
      if (!chunk) return []
      return [
        {
          tag: 'link',
          attrs: { rel: 'prefetch', href: `/${chunk.fileName}`, crossorigin: '' },
          injectTo: 'head',
        },
      ]
    },
  }
}

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), prefetchPlotly()],
  // Unit tests run in plain node, with no DOM: the only thing under test is
  // utils/harrisLayout.ts, which is deliberately pure data in and data out so
  // that the diagram's geometry can be asserted without rendering it. That
  // matters more than it sounds -- reactflow draws to a canvas-backed
  // viewport, so "the boxes line up" is otherwise a claim nobody can check.
  // Adding jsdom would only be worth it for component tests, which these are
  // not.
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    // A repo on a Windows drive, bind-mounted into Docker, sends no file
    // change events into the container; polling is the only way the dev
    // server sees an edit there. Off unless asked for: it costs CPU.
    watch: {
      usePolling: process.env.CHOKIDAR_USEPOLLING === 'true',
    },
    proxy: {
      '/api': {
        // The frontend calls same-origin /api and this forwards it. Run
        // directly on the host, the backend is on localhost; the compose
        // stack sets API_PROXY_TARGET to the backend service instead.
        target: process.env.API_PROXY_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    minify: 'terser',
    rollupOptions: {
      output: {
        // Plotly is most of the bytes and changes only when its version pin
        // does, so it gets a chunk of its own: one download shared by every
        // page that plots, and still cached after a release that touched
        // nothing but the app's own code.
        //
        // Rollup's CommonJS interop helpers are the catch. They are one tiny
        // module shared by every CommonJS package, Plotly and the entry's
        // own dependencies alike, and left to itself Rollup files them in
        // the Plotly chunk -- after which the entry imports (and preloads)
        // all of Plotly to get two ten-line functions. Given a chunk of their
        // own they cost one small request instead.
        manualChunks(id) {
          if (id.includes('/node_modules/plotly.js-dist-min/')) return 'plotly'
          if (id.includes('commonjsHelpers')) return 'cjs-helpers'
        },
      },
    },
  },
})
