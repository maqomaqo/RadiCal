// What is cut from Positron before it is drawn, and what is left alone.
import { describe, expect, it } from 'vitest'

import { tailorBasemapStyle } from './basemapStyle'

const positron = {
  version: 8,
  sprite: 'https://tiles.openfreemap.org/sprites/ofm_f384/ofm',
  glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
  sources: { openmaptiles: { type: 'vector', url: 'https://tiles.openfreemap.org/planet' } },
  layers: [
    { id: 'background', type: 'background' },
    { id: 'park', type: 'fill' },
    { id: 'water', type: 'fill' },
    { id: 'landcover_wood', type: 'fill' },
    { id: 'highway_major_inner', type: 'line' },
    { id: 'highway-name-major', type: 'symbol' },
    { id: 'road_shield_us', type: 'symbol' },
    { id: 'airport', type: 'symbol' },
    { id: 'label_city', type: 'symbol' },
    { id: 'label_country_1', type: 'symbol' },
  ],
}

describe('tailorBasemapStyle', () => {
  const tailored = tailorBasemapStyle(positron)
  const ids = tailored.layers!.map((layer) => layer.id)

  it('keeps land one colour: no woodland or park fills', () => {
    expect(ids).not.toContain('landcover_wood')
    expect(ids).not.toContain('park')
    expect(ids).toContain('water')
    expect(ids).toContain('background')
  })

  it('drops road names and shields and airports, keeps place names', () => {
    expect(ids).not.toContain('highway-name-major')
    expect(ids).not.toContain('road_shield_us')
    expect(ids).not.toContain('airport')
    expect(ids).toContain('label_city')
    expect(ids).toContain('label_country_1')
    // The roads themselves stay: they are the shape of the land at high zoom.
    expect(ids).toContain('highway_major_inner')
  })

  it('needs no sprite once nothing draws an icon, and keeps the glyphs', () => {
    expect(tailored.sprite).toBeUndefined()
    expect(tailored.glyphs).toBe(positron.glyphs)
  })

  it('leaves the style it was given alone', () => {
    expect(positron.sprite).toBeDefined()
    expect(positron.layers).toHaveLength(10)
  })
})
