import { decomposeColor } from '@mui/material/styles'

/**
 * Just enough OKLCH to re-ground a colour that is not ours -- the basemap
 * style's, which arrives as strings in four syntaxes. Lightness and chroma are
 * what a dark ground has to change; hue is what it must not, so the colour is
 * taken apart, moved on those two axes, and put back.
 */

export interface Oklch {
  /** Lightness, 0-1. */
  l: number
  /** Chroma, about 0-0.4. */
  c: number
  /** Hue in degrees. */
  h: number
  /** Opacity, 0-1. */
  a: number
}

const toLinear = (v: number) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
const toGamma = (v: number) => (v <= 0.0031308 ? v * 12.92 : 1.055 * v ** (1 / 2.4) - 0.055)

/** A CSS colour string as OKLCH, or null if it is not one MUI can read. */
export function parseOklch(css: string): Oklch | null {
  let parsed
  try {
    parsed = decomposeColor(css)
  } catch {
    return null
  }
  let rgb: number[]
  if (parsed.type === 'hsl' || parsed.type === 'hsla') {
    const [h, s, l] = [parsed.values[0], parsed.values[1] / 100, parsed.values[2] / 100]
    const k = (n: number) => (n + h / 30) % 12
    const a = s * Math.min(l, 1 - l)
    rgb = [0, 8, 4].map((n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1))))
  } else {
    rgb = parsed.values.slice(0, 3).map((v) => v / 255)
  }
  const [r, g, b] = rgb.map(toLinear)
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s
  return {
    l: L,
    c: Math.hypot(A, B),
    h: ((Math.atan2(B, A) * 180) / Math.PI + 360) % 360,
    a: parsed.values[3] ?? 1,
  }
}

/** OKLCH as `rgb()`/`rgba()`, chroma reduced until it fits the screen's gamut. */
export function formatOklch({ l, c, h, a }: Oklch): string {
  const rad = (h * Math.PI) / 180
  let chroma = c
  for (let i = 0; i < 40; i++) {
    const A = chroma * Math.cos(rad)
    const B = chroma * Math.sin(rad)
    const lc = (l + 0.3963377774 * A + 0.2158037573 * B) ** 3
    const mc = (l - 0.1055613458 * A - 0.0638541728 * B) ** 3
    const sc = (l - 0.0894841775 * A - 1.291485548 * B) ** 3
    const rgb = [
      4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
      -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
      -0.0041960863 * lc - 0.7034186147 * mc + 1.707614701 * sc,
    ]
    if (rgb.every((v) => v >= -0.0005 && v <= 1.0005) || chroma < 1e-4) {
      const [r, g, b] = rgb.map((v) => Math.round(Math.min(1, Math.max(0, toGamma(Math.max(0, v)))) * 255))
      return a < 1 ? `rgba(${r}, ${g}, ${b}, ${a})` : `rgb(${r}, ${g}, ${b})`
    }
    chroma *= 0.95
  }
  return `rgb(0, 0, 0)`
}
