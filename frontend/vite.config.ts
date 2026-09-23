/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
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
    proxy: {
      '/api': {
        // Inside Docker, use the service name; otherwise use localhost
        target: 'http://radiocarbon_backend:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, '/api'),
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
