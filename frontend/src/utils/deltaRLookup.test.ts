// How a derived ΔR is presented.
//
// None of this is arithmetic -- the weighted mean is entirely the server's,
// and has to be, because it must reproduce calib.org's own printed value and
// there can only be one implementation of that. What is asserted here is the
// judgement layer: which habits get marked and which deliberately do not,
// what a selection's position is when its dates disagree about it, and that a
// point with no uncertainty is shown rather than dropped.
import { describe, expect, it } from 'vitest'

import {
  FAR_NEAREST_KM,
  MARKED_HABITS,
  centreProblem,
  counterfactualLine,
  formatDeltaR,
  habitLabel,
  isMarkedHabit,
  isWeighable,
  markedBreakdown,
  nearestIsFar,
  selectionCentre,
} from './deltaRLookup'
import type { LookupPoint, LookupResult } from '@/api/reservoir'

const pt = (overrides: Partial<LookupPoint> = {}): LookupPoint => ({
  map_no: 1,
  latitude: 54.5,
  longitude: -5.6,
  delta_r: -200,
  delta_r_error: 25,
  feeding: 'suspension',
  feeding_raw: 'suspension',
  locality: 'Somewhere',
  reference: 'Reimer and Reimer 2001',
  distance_km: 4,
  ...overrides,
})

const result = (overrides: Partial<LookupResult> = {}): LookupResult => ({
  delta_r: -200,
  delta_r_error: 25,
  n: 10,
  snapshot_id: 'calib-marine20-2026-05-11',
  centre: { lat: 54.5, lon: -5.6 },
  nearest_km: 4,
  farthest_km: 60,
  excluded_no_error: [],
  excluded_by_user: [],
  feeding: {},
  feeding_raw: {},
  included_all: {
    delta_r: -200, delta_r_error: 25, n: 10, map_nos: [],
    internal: 10, scatter: 25, excluded_no_error: [],
  },
  suspension_equivalent: {
    delta_r: -188, delta_r_error: 61, n: 7, map_nos: [],
    internal: 20, scatter: 61, excluded_no_error: [],
  },
  feeding_shift: 12,
  feeding_shift_matters: false,
  feeding_shift_warn_years: 35,
  points: [],
  delta_r_source: {},
  ...overrides,
})

describe('which habits are marked', () => {
  it('marks the three the paired data found biased', () => {
    expect(MARKED_HABITS).toEqual(['deposit', 'migratory', 'carnivore'])
  })

  it('does not mark grazer or unknown', () => {
    // §2.7 measured both indistinguishable from the suspension pool. Marking
    // them would invent a suspicion the data does not support -- and `unknown`
    // is 14% of the database.
    expect(isMarkedHabit('grazer')).toBe(false)
    expect(isMarkedHabit('unknown')).toBe(false)
  })

  it('does not mark ambiguous either', () => {
    // Those five values are truncated compound judgements. Flagging them would
    // be treating a hedge as a verdict.
    expect(isMarkedHabit('ambiguous')).toBe(false)
  })

  it('handles a point with no habit at all', () => {
    expect(isMarkedHabit(null)).toBe(false)
    expect(isMarkedHabit(undefined)).toBe(false)
  })
})

describe('habitLabel', () => {
  it('shows the class for an ordinary point', () => {
    expect(habitLabel(pt({ feeding: 'deposit', feeding_raw: 'Deposit' }))).toBe('deposit')
  })

  it("shows the source's own truncated string for an ambiguous one", () => {
    // The string IS the evidence: a compound judgement its author wrote and
    // the source's column width cut off mid-word. A reader deciding whether to
    // exclude the point needs to see it, not the word "ambiguous".
    const point = pt({ feeding: 'ambiguous', feeding_raw: 'Deposit or suspe' })
    expect(habitLabel(point)).toBe('Deposit or suspe')
  })

  it('falls back to unknown rather than showing nothing', () => {
    expect(habitLabel(pt({ feeding: null, feeding_raw: null }))).toBe('unknown')
  })
})

describe('isWeighable', () => {
  it('is false for a point with no published uncertainty', () => {
    // 1/σ² is not a number when σ is absent. Thirty rows of 2060 are here.
    expect(isWeighable(pt({ delta_r_error: null }))).toBe(false)
  })

  it('is false for a zero uncertainty, which is the same problem', () => {
    expect(isWeighable(pt({ delta_r_error: 0 }))).toBe(false)
  })

  it('is false for a point with no offset at all', () => {
    expect(isWeighable(pt({ delta_r: null }))).toBe(false)
  })

  it('is true for an ordinary point', () => {
    expect(isWeighable(pt())).toBe(true)
  })
})

describe('formatDeltaR', () => {
  it('rounds to whole years, as calib.org prints them', () => {
    expect(formatDeltaR(-238.66, 20.34)).toBe('-239 ± 20')
  })

  it('shows a value with no uncertainty as just the value', () => {
    expect(formatDeltaR(-200, null)).toBe('-200')
  })

  it('shows nothing derivable as a dash', () => {
    expect(formatDeltaR(null, null)).toBe('—')
  })
})

describe('counterfactualLine', () => {
  it('reads as one line naming both counts', () => {
    expect(counterfactualLine(result())).toBe(
      'Suspension feeders only (7 of 10): -188 ± 61')
  })

  it('is null when nothing marked was in the set', () => {
    // Nothing to compare, so no line -- rather than a line saying the same
    // number twice.
    const same = result({
      suspension_equivalent: {
        delta_r: -200, delta_r_error: 25, n: 10, map_nos: [],
        internal: 10, scatter: 25, excluded_no_error: [],
      },
    })
    expect(counterfactualLine(same)).toBeNull()
  })

  it('is null when dropping them would leave too few points', () => {
    // The server returns no counterfactual at all in that case; the panel says
    // so in its own sentence rather than showing a blank line.
    expect(counterfactualLine(result({ suspension_equivalent: null }))).toBeNull()
  })
})

describe('markedBreakdown', () => {
  it('counts each marked habit among the weighable points', () => {
    const points = [
      pt({ map_no: 1, feeding: 'deposit' }),
      pt({ map_no: 2, feeding: 'deposit' }),
      pt({ map_no: 3, feeding: 'migratory' }),
      pt({ map_no: 4, feeding: 'suspension' }),
    ]
    expect(markedBreakdown(points)).toEqual([['migratory', 1], ['deposit', 2]])
  })

  it('puts migratory first', () => {
    // Its case is a-priori -- the animal may not have lived where it was found
    // -- rather than statistical, so a reader should weigh it differently.
    const points = [
      pt({ map_no: 1, feeding: 'carnivore' }),
      pt({ map_no: 2, feeding: 'migratory' }),
    ]
    expect(markedBreakdown(points)[0][0]).toBe('migratory')
  })

  it('ignores a marked point that could not be weighted anyway', () => {
    // It is not in the number, so it cannot be moving the number.
    const points = [pt({ map_no: 1, feeding: 'deposit', delta_r_error: null })]
    expect(markedBreakdown(points)).toEqual([])
  })
})

describe('selectionCentre', () => {
  it('is the shared position of dates from one site', () => {
    const dates = [
      { latitude: 54.5, longitude: -5.6 },
      { latitude: 54.5, longitude: -5.6 },
    ]
    expect(selectionCentre(dates)).toEqual({ latitude: 54.5, longitude: -5.6 })
  })

  it('is null when the dates are at more than one position', () => {
    // A centroid of two sites is a place neither of them is, and deriving a
    // reservoir offset for it would be deriving it for nowhere.
    const dates = [
      { latitude: 54.5, longitude: -5.6 },
      { latitude: 55.1, longitude: -6.2 },
    ]
    expect(selectionCentre(dates)).toBeNull()
  })

  it('is null when nothing is placed', () => {
    expect(selectionCentre([{ latitude: null, longitude: null }])).toBeNull()
  })
})

describe('centreProblem', () => {
  it('says nothing when the selection has one shared position', () => {
    expect(centreProblem([{ latitude: 54.5, longitude: -5.6 }])).toBeNull()
  })

  it('tells an unplaced selection what to do about it', () => {
    const message = centreProblem([{ latitude: null, longitude: null }])
    expect(message).toMatch(/position/)
    expect(message).toMatch(/map/)
  })

  it('counts the unplaced ones in a partly placed selection', () => {
    const message = centreProblem([
      { latitude: 54.5, longitude: -5.6 },
      { latitude: null, longitude: null },
    ])
    expect(message).toMatch(/1 of the 2/)
  })

  it('explains a selection spanning two sites', () => {
    const message = centreProblem([
      { latitude: 54.5, longitude: -5.6 },
      { latitude: 55.1, longitude: -6.2 },
    ])
    expect(message).toMatch(/more than one position/)
  })
})

describe('nearestIsFar', () => {
  it('flags a nearest point beyond the threshold', () => {
    // §2.6: Tampa's nearest measurement is 163 km away, and the p90 across the
    // database is 223 km, so this is not a rare case.
    expect(nearestIsFar(result({ nearest_km: 163 }))).toBe(true)
  })

  it('does not flag a point nearby', () => {
    expect(nearestIsFar(result({ nearest_km: 4 }))).toBe(false)
  })

  it('does not flag when there is no distance to judge', () => {
    expect(nearestIsFar(result({ nearest_km: null }))).toBe(false)
  })

  it('has a threshold set where a local offset stops being local', () => {
    expect(FAR_NEAREST_KM).toBe(100)
  })
})
