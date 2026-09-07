/**
 * Vite's `?raw` import suffix, for the one document the app ships as Markdown.
 *
 * The manual lives at src/content/USER_MANUAL.md and is inlined into the
 * bundle at build time, so the Help page has no fetch to fail and no second
 * copy of the text to keep in step.
 */
declare module '*.md?raw' {
  const content: string
  export default content
}
