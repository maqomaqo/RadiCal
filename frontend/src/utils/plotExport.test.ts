// Sample and boundary names come from whatever the analyst typed and go
// straight into a saved file's name. These hold the cases that produce an
// unusable download: characters an OS rejects, and names that sanitise away
// to nothing.
import { describe, expect, it } from 'vitest'

import { PNG_EXPORT_SCALE, exportFilename } from './plotExport'

describe('exportFilename', () => {
  it('keeps an ordinary lab code recognisable', () => {
    expect(exportFilename('posterior-WK50104')).toBe('posterior-wk50104')
  })

  it('replaces everything an OS would reject', () => {
    expect(exportFilename('Trench C / Phase 1: start')).toBe('trench-c-phase-1-start')
    expect(exportFilename('OxA-1234 (charcoal)')).toBe('oxa-1234-charcoal')
  })

  it('never leaves a leading or trailing separator', () => {
    expect(exportFilename('  Start Nenthorn  ')).toBe('start-nenthorn')
    expect(exportFilename('...Boundary...')).toBe('boundary')
  })

  it('falls back rather than returning an empty name', () => {
    // A name of nothing but punctuation would otherwise save as ".png".
    expect(exportFilename('///')).toBe('plot')
    expect(exportFilename('')).toBe('plot')
  })
})

describe('PNG_EXPORT_SCALE', () => {
  it('is above 1, which is the default that made exports a screenshot', () => {
    expect(PNG_EXPORT_SCALE).toBeGreaterThan(1)
  })
})
