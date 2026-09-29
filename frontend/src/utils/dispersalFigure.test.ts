import { describe, it, expect } from 'vitest'

import {
  arrivalAges,
  compassPoint,
  formatLatLon,
  residualReading,
  residualSpread,
  speedText,
  waveIsochrons,
} from './dispersalFigure'
import type { DispersalFit } from '@/types'

function summary(median: number) {
  return {
    mean: median,
    median,
    std: 0.1,
    hpd_68: [median - 0.1, median + 0.1] as [number, number],
    hpd_95: [median - 0.2, median + 0.2] as [number, number],
    step: 0.01,
    distribution: [],
  }
}

function fit(overrides: Partial<DispersalFit> = {}): DispersalFit {
  return {
    origin: { lat: 50, lon: 0 },
    sites: [
      {
        element_id: 'a', name: 'A', parameter_id: 'b0', basis: 'boundary',
        latitude: 50, longitude: 0, date_ids: ['d1'], scatter_km: 0, refusal: null,
      },
      {
        element_id: 'b', name: 'B', parameter_id: 'b1', basis: 'boundary',
        latitude: 50, longitude: 4, date_ids: ['d2'], scatter_km: 0, refusal: null,
      },
    ],
    n_sites: 2,
    min_sites: 6,
    refused: null,
    speed: summary(1.0),
    t0: { median: 6500, mean: 6500, band_68: [6480, 6520], band_95: [6460, 6540] },
    residuals: [
      { element_id: 'a', name: 'A', distance_km: 0, residual_years: 0, residual_sd: 10 },
      { element_id: 'b', name: 'B', distance_km: 300, residual_years: 0, residual_sd: 10 },
    ],
    basis: 'boundary',
    ...overrides,
  }
}

describe('speedText', () => {
  it('keeps three decimals on a slow front', () => {
    // 0.004 km/yr is a real distinction between a slow front and a stationary
    // one, and two decimals would round it to 0.00.
    expect(speedText(0.004)).toBe('0.004')
  })

  it('drops to none above 10 km/yr', () => {
    // A number whose interval is a kilometre wide has no business carrying
    // hundredths.
    expect(speedText(12.345)).toBe('12')
  })

  it('keeps the sign on a front running the other way', () => {
    expect(speedText(-1.5)).toBe('-1.50')
  })

  it('says N/A rather than printing a non-number', () => {
    expect(speedText(Infinity)).toBe('N/A')
    expect(speedText(NaN)).toBe('N/A')
  })
})

describe('arrivalAges', () => {
  it('reconstructs the line the fit drew', () => {
    // 300 km at 1 km/yr is 300 years after t0, and older is a larger cal BP.
    const ages = arrivalAges(fit())
    expect(ages.a).toBeCloseTo(6500)
    expect(ages.b).toBeCloseTo(6200)
  })

  it('carries the residual, so the ramp cannot disagree with the table', () => {
    const ages = arrivalAges(fit({
      residuals: [
        { element_id: 'a', name: 'A', distance_km: 0, residual_years: 0, residual_sd: 10 },
        { element_id: 'b', name: 'B', distance_km: 300, residual_years: 120, residual_sd: 10 },
      ],
    }))
    // The site the table calls 120 years early must be 120 years older on the
    // map. Recomputing the arrival from coordinates instead would put it on
    // the line and lose exactly the thing worth seeing.
    expect(ages.b).toBeCloseTo(6320)
  })

  it('is empty for a refusal, which has no line to reconstruct', () => {
    expect(arrivalAges(fit({ speed: null, t0: null, residuals: [] }))).toEqual({})
  })

  it('does not divide by a zero speed', () => {
    expect(arrivalAges(fit({ speed: summary(0) }))).toEqual({})
  })
})

describe('waveIsochrons', () => {
  it('draws circles at round ages between the origin and the furthest site', () => {
    const lines = waveIsochrons(fit())
    // t0 6500, 300 km at 1 km/yr: 300 years, so round ages every 100.
    expect(lines.map((l) => l.age)).toEqual([6400, 6300, 6200])
  })

  it('puts every point of an isochron at the distance its age implies', () => {
    const [first] = waveIsochrons(fit())
    const radius = 1.0 * (6500 - first.age)
    for (const p of first.points) {
      const phi1 = (50 * Math.PI) / 180
      const phi2 = (p.latitude * Math.PI) / 180
      const dLambda = ((p.longitude - 0) * Math.PI) / 180
      const central = Math.acos(
        Math.min(1, Math.sin(phi1) * Math.sin(phi2) + Math.cos(phi1) * Math.cos(phi2) * Math.cos(dLambda))
      )
      expect(central * 6371.0088).toBeCloseTo(radius, 0)
    }
  })

  it('draws nothing for a front that runs backwards', () => {
    expect(waveIsochrons(fit({ speed: summary(-1.0) }))).toEqual([])
  })
})

describe('residualReading', () => {
  it('calls a residual inside two of its own sd a fit, not a direction', () => {
    expect(residualReading(-3, 644)).toBe('fits')
    expect(residualReading(32, 1080)).toBe('fits')
  })

  it('names the direction once the residual clears its uncertainty', () => {
    expect(residualReading(500, 100)).toBe('early')
    expect(residualReading(-500, 100)).toBe('late')
  })
})

describe('residualSpread', () => {
  it('sets the scatter against the time the front takes to cross the sites', () => {
    const spread = residualSpread(fit({
      residuals: [
        { element_id: 'a', name: 'A', distance_km: 0, residual_years: 30, residual_sd: 10 },
        { element_id: 'b', name: 'B', distance_km: 300, residual_years: -30, residual_sd: 10 },
      ],
    }))
    expect(spread?.rms).toBeCloseTo(30)
    expect(spread?.span).toBeCloseTo(300)
    expect(spread?.share).toBeCloseTo(0.1)
  })
})

describe('formatLatLon', () => {
  it('spells out the hemisphere rather than a sign', () => {
    expect(formatLatLon(-2, 154)).toBe('2.0° S, 154.0° E')
    expect(formatLatLon(21.3, -157.9)).toBe('21.3° N, 157.9° W')
  })
})

describe('compassPoint', () => {
  it('names the cardinals', () => {
    expect(compassPoint(0)).toBe('N')
    expect(compassPoint(90)).toBe('E')
    expect(compassPoint(180)).toBe('S')
    expect(compassPoint(270)).toBe('W')
  })

  it('rounds to the nearest of eight', () => {
    expect(compassPoint(44)).toBe('NE')
    // 112 is closer to E (90) than to SE (135).
    expect(compassPoint(112)).toBe('E')
  })

  it('wraps rather than falling off the end', () => {
    // 359 is north, not an index past the array.
    expect(compassPoint(359)).toBe('N')
    expect(compassPoint(360)).toBe('N')
    expect(compassPoint(-90)).toBe('W')
  })

  it('says nothing for a non-number', () => {
    expect(compassPoint(NaN)).toBe('')
  })
})
