// The dark theme is the light theme's own colours moved to another ground, so
// what has to hold is what held before: every ink that carries a 10px label
// clears 4.5:1 on the dimmest surface it can sit on, the surfaces are told
// apart by lightness alone, and no colour in the app is a neutral grey.
import { describe, expect, it } from 'vitest'
import { decomposeColor, getContrastRatio } from '@mui/material/styles'

import { createAppTheme } from './appTheme'
import {
  CONTEXT_PALETTE_DARK,
  contextShades,
  elementHues,
} from './diagramColors'
import { curveStyles, CURVE_PALETTE_SIZE, styleForSlot } from './curveColors'
import { darkenBasemapStyle } from './basemapStyle'
import { inkAlpha, inkAt } from './ink'
import { APP_BAR_DARK, SURFACES } from './surfaces'

const dark = SURFACES.dark
const light = SURFACES.light

/** `fg` at `alpha` over `bg`, opaque. */
function flatten(fg: string, a: number, bg: string): string {
  const f = decomposeColor(fg).values
  const b = decomposeColor(bg).values
  return `rgb(${[0, 1, 2].map((i) => Math.round(f[i] * a + b[i] * (1 - a))).join(', ')})`
}

describe('surfaces', () => {
  it('are told apart by lightness: canvas, then card, then band, then what floats', () => {
    const steps = [dark.canvas, dark.paper, dark.muted, dark.raised]
    for (let i = 1; i < steps.length; i++) {
      const ratio = getContrastRatio(steps[i], steps[i - 1])
      expect(ratio).toBeGreaterThan(1.08)
      expect(ratio).toBeLessThan(1.25)
    }
  })

  it('are warm, never a neutral grey and never black', () => {
    for (const hex of [dark.canvas, dark.paper, dark.muted, dark.raised, dark.ink]) {
      const [r, g, b] = decomposeColor(hex).values
      expect(r).toBeGreaterThan(b)
      expect(Math.max(r, g, b)).toBeGreaterThan(0)
      expect(hex).not.toBe('#000000')
    }
  })
})

describe('inkAlpha', () => {
  it('is the identity in the light theme, so nothing there moves', () => {
    for (const a of [0.05, 0.14, 0.45, 0.92]) expect(inkAlpha('light', a)).toBe(a)
  })

  it('gives the dark theme the contrast the light strength has on white', () => {
    for (const a of [0.09, 0.14, 0.3, 0.45, 0.74, 0.92]) {
      const onLight = getContrastRatio(flatten(light.ink, a, light.paper), light.paper)
      const onDark = getContrastRatio(flatten(dark.ink, inkAlpha('dark', a), dark.paper), dark.paper)
      // 8-bit channels cannot match closer than about one to two percent at the strong end.
      expect(Math.abs(onDark - onLight) / onLight).toBeLessThan(0.02)
    }
  })

  it('keeps strengths in the same order, so the prior still recedes behind the posterior', () => {
    const strengths = [0.09, 0.14, 0.3, 0.45, 0.74, 0.92]
    const alphas = strengths.map((a) => inkAlpha('dark', a))
    expect([...alphas].sort((x, y) => x - y)).toEqual(alphas)
    expect(inkAt('dark', 0.45)).toMatch(/^rgba\(247, 243, 238, /)
  })
})

describe('element hues on the dark ground', () => {
  const hues = elementHues('dark')
  const labelled = {
    boundaryInk: hues.boundary.ink,
    phase: hues.phase,
    hiatus: hues.hiatus,
    wiggle: hues.wiggle,
    deposition: hues.deposition,
  }

  it('carry a label at 4.5:1 on the dimmest surface they sit on', () => {
    for (const [name, color] of Object.entries(labelled)) {
      expect(getContrastRatio(color, dark.muted), name).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('draw the Boundary rule at 3:1 or better', () => {
    expect(getContrastRatio(hues.boundary.main, dark.muted)).toBeGreaterThanOrEqual(3)
    expect(getContrastRatio(hues.undated, dark.paper)).toBeGreaterThanOrEqual(3)
  })

  it('keep the context ramp on one hue and 4.5:1 for every step', () => {
    for (const step of CONTEXT_PALETTE_DARK) {
      expect(getContrastRatio(step, dark.muted)).toBeGreaterThanOrEqual(4.5)
    }
    // Steps rise in contrast, so index order still means "stronger".
    const ratios = CONTEXT_PALETTE_DARK.map((c) => getContrastRatio(c, dark.paper))
    expect([...ratios].sort((a, b) => a - b)).toEqual(ratios)
    expect(contextShades(2, 'dark')).toEqual([CONTEXT_PALETTE_DARK[0], CONTEXT_PALETTE_DARK[5]])
  })
})

describe('curve palette on the dark ground', () => {
  it('keeps every line readable and every slot distinct', () => {
    const lines = Array.from({ length: CURVE_PALETTE_SIZE }, (_, i) => styleForSlot(i, 'dark').line)
    expect(new Set(lines).size).toBe(CURVE_PALETTE_SIZE)
    for (const line of lines) {
      expect(getContrastRatio(line, dark.muted)).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('leaves the dashes alone: they are what survives a greyscale print', () => {
    const lightStyles = curveStyles(['intcal20', 'marine20', 'shcal20'], 'light')
    const darkStyles = curveStyles(['intcal20', 'marine20', 'shcal20'], 'dark')
    for (const id of Object.keys(lightStyles)) {
      expect(darkStyles[id].dash).toBe(lightStyles[id].dash)
      expect(darkStyles[id].line).not.toBe(lightStyles[id].line)
    }
  })
})

describe('createAppTheme', () => {
  it('leaves the light theme exactly as it was', () => {
    const theme = createAppTheme('light')
    expect(theme.palette.primary.main).toBe('#3c70b8')
    expect(theme.palette.background.default).toBe('#faf7f4')
    expect(theme.palette.background.paper).toBe('#ffffff')
    expect(theme.palette.divider).toBe('rgba(43, 35, 32, 0.14)')
    expect(theme.palette.text.primary).toBe('rgba(43, 35, 32, 0.92)')
    expect(theme.palette.action.active).toBe('rgba(43, 35, 32, 0.64)')
  })

  it('puts text on the dark ground at the contrast it has on the light one', () => {
    const theme = createAppTheme('dark')
    const flat = (color: string) => {
      const [r, g, b, a = 1] = decomposeColor(color).values
      return flatten(`rgb(${r}, ${g}, ${b})`, a, dark.paper)
    }
    expect(getContrastRatio(flat(theme.palette.text.primary), dark.paper)).toBeGreaterThan(11)
    expect(getContrastRatio(flat(theme.palette.text.secondary), dark.paper)).toBeGreaterThan(6)
  })

  it('reads primary as a link and as a fill, both at 4.5:1', () => {
    const { palette } = createAppTheme('dark')
    expect(getContrastRatio(palette.primary.main, dark.muted)).toBeGreaterThanOrEqual(4.5)
    expect(getContrastRatio(palette.primary.contrastText, palette.primary.main)).toBeGreaterThanOrEqual(4.5)
    for (const role of ['secondary', 'error', 'warning', 'success'] as const) {
      expect(getContrastRatio(palette[role].main, dark.muted), role).toBeGreaterThanOrEqual(4.5)
      expect(getContrastRatio(palette[role].contrastText, palette[role].main), role).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('keeps the label legible when a filled colour is hovered', () => {
    // MUI hovers a contained button to `dark`, its darkened step. On the dark
    // ground the label is the canvas, so darkening would take it under 4.5:1.
    const { palette } = createAppTheme('dark')
    for (const role of ['primary', 'secondary', 'error', 'warning', 'success'] as const) {
      expect(getContrastRatio(palette[role].contrastText, palette[role].dark), role).toBeGreaterThanOrEqual(4.5)
      expect(getContrastRatio(palette[role].dark, palette[role].main), role).toBeGreaterThan(1.1)
    }
  })

  it('keeps white labels on the app bar legible', () => {
    expect(getContrastRatio('#ffffff', APP_BAR_DARK)).toBeGreaterThan(7)
  })

  it('separates the error red from the Boundary coral by lightness, since depth is gone', () => {
    const { palette } = createAppTheme('dark')
    const coral = elementHues('dark').boundary.ink
    expect(getContrastRatio(palette.error.main, coral)).toBeGreaterThan(1.1)
    expect(palette.error.main).not.toBe(coral)
  })
})

describe('darkenBasemapStyle', () => {
  const style = {
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': 'rgb(242,243,240)' } },
      { id: 'water', type: 'fill', paint: { 'fill-color': 'rgb(194, 200, 202)' } },
      { id: 'boundary_2', type: 'line', paint: { 'line-color': 'hsl(0,0%,70%)' } },
      {
        id: 'label_city',
        type: 'symbol',
        paint: { 'text-color': '#000', 'text-halo-color': '#fff' },
      },
      {
        id: 'road',
        type: 'line',
        paint: {
          'line-color': ['interpolate', ['linear'], ['zoom'], 5.8, 'hsla(0,0%,85%,0.53)', 6, '#fff'],
        },
      },
    ],
  }
  const result = darkenBasemapStyle(style)
  const paint = (id: string) => result.layers!.find((l) => l.id === id)!.paint as Record<string, unknown>

  it('makes the land the plot surface, so marks sit on the ground they were checked against', () => {
    expect(paint('background')['background-color']).toBe(dark.paper)
  })

  it('keeps the sea the darker surface and the coastline the lighter mark', () => {
    const lum = (css: string) => getContrastRatio(css, '#000000')
    expect(lum(paint('water')['fill-color'] as string)).toBeLessThan(lum(dark.paper))
    expect(lum(paint('boundary_2')['line-color'] as string)).toBeGreaterThan(lum(dark.paper))
  })

  it('holds a place name to 4.5:1 on the land', () => {
    const name = paint('label_city')['text-color'] as string
    expect(getContrastRatio(name, dark.paper)).toBeGreaterThanOrEqual(4.5)
  })

  it('leaves an expression’s operators alone and recolours its stops', () => {
    const expr = paint('road')['line-color'] as unknown[]
    expect(expr.slice(0, 4)).toEqual(['interpolate', ['linear'], ['zoom'], 5.8])
    expect(expr[4]).toMatch(/^rgba\(/)
    expect(expr[6]).toMatch(/^rgb\(/)
  })

  it('returns the same object for the same style, so a map does not redraw', () => {
    expect(darkenBasemapStyle(style)).toBe(result)
  })
})
