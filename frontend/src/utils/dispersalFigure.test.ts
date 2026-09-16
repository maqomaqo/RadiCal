import { describe, it, expect } from 'vitest'

import { arrivalAges, compassPoint, frontLine, speedText } from './dispersalFigure'
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

describe('frontLine', () => {
  it('runs from the origin to the furthest site', () => {
    const line = frontLine(fit())
    expect(line).toHaveLength(2)
    expect(line[0]).toEqual({ latitude: 50, longitude: 0 })
    expect(line[1]).toEqual({ latitude: 50, longitude: 4 })
  })

  it('measures furthest on the distances the fit reported', () => {
    // B is nearer in degrees but further along the front's own axis. The line
    // has to end where the REGRESSION reached, not where the arithmetic on
    // degrees happens to land.
    const line = frontLine(fit({
      sites: [
        { element_id: 'a', name: 'A', parameter_id: 'b0', basis: 'boundary',
          latitude: 50, longitude: 6, date_ids: [], scatter_km: 0, refusal: null },
        { element_id: 'b', name: 'B', parameter_id: 'b1', basis: 'boundary',
          latitude: 50, longitude: 4, date_ids: [], scatter_km: 0, refusal: null },
      ],
      residuals: [
        { element_id: 'a', name: 'A', distance_km: 100, residual_years: 0, residual_sd: 5 },
        { element_id: 'b', name: 'B', distance_km: 900, residual_years: 0, residual_sd: 5 },
      ],
    }))
    expect(line[1].longitude).toBe(4)
  })

  it('leaves a refused site out of the line', () => {
    const line = frontLine(fit({
      sites: [
        { element_id: 'a', name: 'A', parameter_id: 'b0', basis: 'boundary',
          latitude: 50, longitude: 1, date_ids: [], scatter_km: 0, refusal: null },
        { element_id: 'bad', name: 'Mixed', parameter_id: 'b1', basis: 'boundary',
          latitude: 50, longitude: 40, date_ids: [], scatter_km: 60,
          refusal: 'two places in one phase' },
      ],
      residuals: [
        { element_id: 'a', name: 'A', distance_km: 70, residual_years: 0, residual_sd: 5 },
      ],
    }))
    expect(line[1].longitude).toBe(1)
  })

  it('draws nothing when no site has a position', () => {
    expect(frontLine(fit({ sites: [] }))).toEqual([])
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
