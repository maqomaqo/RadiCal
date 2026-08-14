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
  },
})
