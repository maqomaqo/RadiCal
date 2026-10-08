// Client-side HPD selection.
//
// These went in alongside making CalibrationCurve.get_display_distribution
// return a density rather than per-point masses. That change is right --
// the series is plotted against a calendar axis, where only a density means
// anything -- but it moved the goalposts here: summing the values was correct
// while they were masses, and stops being correct once they are densities on
// a grid that is not uniformly spaced. IntCal20 and SHCal20 step 1, 5, 10 and
// 20 years in different ranges, so this is reachable with real data.
//
// The property, then: rank by density (the region wanted is the
// highest-density one) but accumulate mass (what a confidence level is a
// share of). On a uniform grid the two coincide, which is why nothing caught
// it before.
import { describe, expect, it } from 'vitest'

import {
  CONFIDENCE_LEVELS,
  INNER_CONFIDENCE_LABEL,
  REPORTING_STEP_YEARS,
  SIGMA_1,
  SIGMA_2,
  SIGMA_3,
  hpdBands,
  hpdEnvelopeFromHistogram,
  hpdRangesFromCurve,
  posteriorEnvelope,
  posteriorRanges,
  reportingOf,
  storedHpdBands,
} from './hpd'
import reportedRangesFixture from './__fixtures__/reportedRanges.json'

type Series = Array<[number, number]>

/** A flat density over [from, to] sampled at `step`. */
function flat(from: number, to: number, step: number, density: number): Series {
  const out: Series = []
  for (let x = from; x <= to + 1e-9; x += step) out.push([x, density])
  return out
}

describe('hpdEnvelopeFromHistogram', () => {
  it('returns the whole range at 100% confidence', () => {
    const envelope = hpdEnvelopeFromHistogram(flat(0, 100, 10, 0.01), 1)

    // Widened by half a bin at each end: a point is the centre of the width
    // it stands for, not its edge.
    expect(envelope).toEqual([-5, 105])
  })

  it('picks the peak, not the edges', () => {
    const series: Series = [
      [0, 0.001], [10, 0.001], [20, 0.05], [30, 0.05], [40, 0.001], [50, 0.001],
    ]
    const [min, max] = hpdEnvelopeFromHistogram(series, 0.8)!

    expect(min).toBeGreaterThanOrEqual(10)
    expect(max).toBeLessThanOrEqual(40)
  })

  it('is unchanged by rescaling the whole series', () => {
    // Confidence is a share, so a constant factor must not move the answer.
    const series: Series = [[0, 0.01], [10, 0.04], [20, 0.02], [30, 0.005]]
    const scaled: Series = series.map(([x, y]) => [x, y * 1000])

    expect(hpdEnvelopeFromHistogram(scaled, 0.68))
      .toEqual(hpdEnvelopeFromHistogram(series, 0.68))
  })

  it('weights a coarse stretch by the years it covers, not its point count', () => {
    // The regression the density change could have introduced. Two halves of
    // equal probability mass: the left sampled every 1 year, the right every
    // 10. Counting points would give the left ten times the weight and pull
    // the region entirely into it.
    const fine = flat(0, 99, 1, 0.005)      // mass 0.5 over 100 years
    const coarse = flat(100, 199, 10, 0.005) // mass 0.5 over 100 years
    const series = [...fine, ...coarse]

    const [min, max] = hpdEnvelopeFromHistogram(series, 0.9)!

    // 90% of a symmetric-mass distribution has to reach well into the
    // coarsely-sampled half.
    expect(max).toBeGreaterThan(150)
    expect(min).toBeLessThan(50)
  })

  it('returns null for a series too short to have an envelope', () => {
    expect(hpdEnvelopeFromHistogram([], 0.95)).toBeNull()
    expect(hpdEnvelopeFromHistogram([[10, 1]], 0.95)).toBeNull()
    expect(hpdEnvelopeFromHistogram(null, 0.95)).toBeNull()
    expect(hpdEnvelopeFromHistogram(flat(0, 50, 10, 0), 0.95)).toBeNull()
  })
})

describe('hpdRangesFromCurve', () => {
  it('splits a bimodal distribution into separate ranges', () => {
    // The reason this variant exists: a calibration curve is non-monotonic,
    // so one measurement can map to several disjoint calendar peaks, and
    // collapsing them into one span implies mass across the empty gap.
    const series: Series = [
      [0, 0.05], [10, 0.05],
      [20, 0.0001], [30, 0.0001], [40, 0.0001],
      [50, 0.05], [60, 0.05],
    ]
    const ranges = hpdRangesFromCurve(series, 0.9)

    expect(ranges).toHaveLength(2)
    expect(ranges.every((r) => r.probability_percent > 0)).toBe(true)
  })

  it('reports shares that add up', () => {
    const ranges = hpdRangesFromCurve(flat(0, 100, 10, 0.01), 0.95)
    const total = ranges.reduce((sum, r) => sum + r.probability_percent, 0)

    expect(total).toBeGreaterThan(94)
    expect(total).toBeLessThanOrEqual(100.001)
  })

  it('orders ranges youngest-last, as the results table reads them', () => {
    const series: Series = [
      [0, 0.05], [10, 0.05], [20, 0.0001], [30, 0.05], [40, 0.05],
    ]
    const ranges = hpdRangesFromCurve(series, 0.9)

    for (let i = 0; i < ranges.length - 1; i++) {
      expect(ranges[i].max).toBeGreaterThan(ranges[i + 1].max)
    }
  })

  it('is unchanged by rescaling the whole series', () => {
    const series: Series = [[0, 0.01], [10, 0.04], [20, 0.02], [30, 0.005]]
    const scaled: Series = series.map(([x, y]) => [x, y * 250])

    expect(hpdRangesFromCurve(scaled, 0.68)).toEqual(hpdRangesFromCurve(series, 0.68))
  })

  it('is empty for nothing to select from', () => {
    expect(hpdRangesFromCurve([], 0.95)).toEqual([])
    expect(hpdRangesFromCurve(null, 0.95)).toEqual([])
    expect(hpdRangesFromCurve(flat(0, 50, 10, 0), 0.95)).toEqual([])
  })
})

// The bands a posterior plot draws, and the one property that matters about
// them: a band's LABEL and its GEOMETRY are computed at the same confidence.
//
// The failure they guard against is invisible: a plot holding '95% HPD' as a
// literal while the page's selector moves independently exports a figure at
// 99.7% with a legend claiming 95%, and a diagram reading a stored hpd_95 while
// the table re-selects from the distribution puts two ranges for one boundary
// on screen at once. Nothing fails; the numbers are simply wrong together.
describe('hpdBands', () => {
  // A single peak with tails, so the 1-sigma and 2-sigma selections genuinely
  // differ rather than both taking the whole series.
  const peaked: Series = [
    [0, 0.001], [10, 0.01], [20, 0.08], [30, 0.4], [40, 0.8],
    [50, 0.4], [60, 0.08], [70, 0.01], [80, 0.001],
  ]

  it('labels every band with the level its ranges were computed at', () => {
    const bands = hpdBands(peaked, 0.954, '95.4%')

    expect(bands.map((b) => b.label)).toEqual(['95.4%', INNER_CONFIDENCE_LABEL])
    expect(bands[0].ranges).toEqual(hpdRangesFromCurve(peaked, 0.954))
    expect(bands[1].ranges).toEqual(hpdRangesFromCurve(peaked, SIGMA_1))
  })

  it('follows the selected confidence rather than a fixed level', () => {
    const at95 = hpdBands(peaked, 0.954, '95.4%')
    const at997 = hpdBands(peaked, 0.997, '99.7%')

    expect(at997[0].label).toBe('99.7%')
    // The outer band genuinely moved -- a frozen band would make these equal.
    expect(at997[0].ranges).not.toEqual(at95[0].ranges)
  })

  it('draws one band at 1 sigma rather than the same interval twice', () => {
    const bands = hpdBands(peaked, SIGMA_1, '68.3%')

    expect(bands).toHaveLength(1)
    expect(bands[0].label).toBe('68.3%')
  })

  it('orders bands outer first, and the outer band contains the inner one', () => {
    const [outer, inner] = hpdBands(peaked, 0.954, '95.4%')

    expect(outer.tone).toBe('outer')
    expect(inner.tone).toBe('inner')
    expect(Math.min(...outer.ranges.map((r) => r.min)))
      .toBeLessThanOrEqual(Math.min(...inner.ranges.map((r) => r.min)))
    expect(Math.max(...outer.ranges.map((r) => r.max)))
      .toBeGreaterThanOrEqual(Math.max(...inner.ranges.map((r) => r.max)))
  })

  it('is empty for nothing to select from', () => {
    expect(hpdBands([], 0.954, '95.4%')).toEqual([])
    expect(hpdBands(null, 0.954, '95.4%')).toEqual([])
  })

  // The Harris matrix and the results table both read a boundary through
  // hpdEnvelopeFromHistogram at the page's confidence. Same input, same call,
  // same answer -- and it has to MOVE with the selector, which is precisely
  // what the diagram's stored hpd_95 did not do.
  it('agrees with the envelope the results table reports, at every level', () => {
    for (const confidence of [SIGMA_1, SIGMA_2, SIGMA_3]) {
      const bands = hpdBands(peaked, confidence, 'x')
      const envelope = hpdEnvelopeFromHistogram(peaked, confidence)!
      const widest = bands[0].ranges

      // The envelope is the ranges' span, widened by half a bin at each end.
      expect(envelope[0]).toBeLessThanOrEqual(Math.min(...widest.map((r) => r.min)))
      expect(envelope[1]).toBeGreaterThanOrEqual(Math.max(...widest.map((r) => r.max)))
    }

    expect(hpdEnvelopeFromHistogram(peaked, 0.997))
      .not.toEqual(hpdEnvelopeFromHistogram(peaked, SIGMA_1))
  })
})

describe('storedHpdBands', () => {
  const r = (min: number, max: number) => ({ min, max, probability_percent: 50 })

  it('names the backend levels, not the page ones', () => {
    const bands = storedHpdBands([r(20, 40)], [r(10, 50)])

    expect(bands.map((b) => b.label)).toEqual(['95.4%', '68.3%'])
    expect(bands.map((b) => b.tone)).toEqual(['outer', 'inner'])
  })

  it('falls back to the plain envelope when no breakdown was sent', () => {
    const bands = storedHpdBands(null, null, [20, 40], [10, 50])

    expect(bands[0].ranges).toEqual([{ min: 10, max: 50, probability_percent: 0 }])
    expect(bands[1].ranges).toEqual([{ min: 20, max: 40, probability_percent: 0 }])
  })

  it('omits a band it has nothing for', () => {
    expect(storedHpdBands(null, [r(10, 50)])).toHaveLength(1)
    expect(storedHpdBands(null, null)).toEqual([])
  })
})


describe('the reporting levels', () => {
  /**
   * The digits, not the labels. `confidence` is the threshold an HPD region is
   * accumulated to, so these ARE the reported range: a truncated 0.68 or 0.95
   * is a different answer, not a different spelling of the same one.
   *
   * JavaScript has no erf, so these are written out. The values below are
   * erf(n/sqrt(2)) to full double precision and must match
   * SIGMA_1/SIGMA_2/SIGMA_3 in backend/app/services/calibration.py, which
   * computes them -- the page re-selects a region client-side from a
   * distribution the backend produced, so the two arriving at different levels
   * is exactly the drift this pins.
   */
  it('are the exact two-sided normal coverages', () => {
    expect(SIGMA_1).toBe(0.6826894921370859)
    expect(SIGMA_2).toBe(0.9544997361036416)
    expect(SIGMA_3).toBe(0.9973002039367398)
  })

  it('are labelled by rounding, not truncation', () => {
    // 68.269 rounds to 68.3, which is what OxCal quotes. 68.2% -- what this
    // app printed before -- is the truncation, and is the bug this pins.
    expect(CONFIDENCE_LEVELS.map((l) => l.label)).toEqual(['68.3%', '95.4%', '99.7%'])
    for (const { value, label } of CONFIDENCE_LEVELS) {
      expect(label).toBe(`${(value * 100).toFixed(1)}%`)
    }
  })

  it('offers them in order, tightest first', () => {
    const values = CONFIDENCE_LEVELS.map((l) => l.value)
    expect(values).toEqual([...values].sort((a, b) => a - b))
  })
})

// One source of truth for what resolution a modelled range is reported at.
//
// The backend rebins a modelled posterior to a five-year step before taking
// its HPD (exact_chain._reported_ranges), for two reasons: the solver's own
// grid step is an implementation detail chosen from the date range and the
// group count, so reporting at it makes a published range move when an
// unrelated part of the model changes; and a modelled posterior is sharply
// multi-peaked, so selecting at that step takes peak tops and skips the dips.
//
// The page then threw that away and re-selected from the full-resolution
// distribution, because only 68.3% and 95.4% are stored and the selector also
// offers 99.7%. A one-phase model is the fixture below: Ld10013's
// 95.4% region came back as five pieces on a 1.785-year grid, two of them
// single points holding 0.3%, where the stored region is three pieces on
// whole fives. Nothing failed -- the table simply disagreed with the export,
// the PDF and the stored result, at bounds that would move again on the next
// model edit.
//
// The fixture is generated by running the BACKEND's _reported_ranges over the
// same distributions, so this asserts the two implementations agree rather
// than asserting numbers typed out by hand.
describe('modelled ranges match the backend', () => {
  const { cases, reporting_step_years: step } = reportedRangesFixture

  it('uses the same reporting step the backend does', () => {
    expect(REPORTING_STEP_YEARS).toBe(step)
  })

  for (const testCase of cases) {
    const distribution = testCase.distribution as Series

    for (const expected of testCase.expected) {
      it(`reproduces ${expected.label} for ${testCase.name}`, () => {
        const ranges = posteriorRanges(distribution, expected.confidence, true)

        expect(ranges.map((r) => [r.min, r.max])).toEqual(
          expected.ranges.map((r) => [r.min, r.max])
        )
        ranges.forEach((r, i) => {
          expect(r.probability_percent).toBeCloseTo(
            expected.ranges[i].probability_percent, 9
          )
        })
      })
    }

    it(`puts every modelled bound on the reporting step for ${testCase.name}`, () => {
      for (const { confidence } of testCase.expected) {
        for (const r of posteriorRanges(distribution, confidence, true)) {
          expect(r.min % step).toBe(0)
          expect(r.max % step).toBe(0)
          expect(r.max - r.min).toBeGreaterThanOrEqual(step)
        }
      }
    })

    it(`spans the ranges it reports for ${testCase.name}`, () => {
      // The backend's stored hpd_68/hpd_95 pair is min-of-mins to max-of-maxes
      // over these same ranges, so the envelope and the breakdown in the row
      // beside it cannot disagree.
      for (const { confidence } of testCase.expected) {
        const ranges = posteriorRanges(distribution, confidence, true)
        expect(posteriorEnvelope(distribution, confidence, true)).toEqual([
          Math.min(...ranges.map((r) => r.min)),
          Math.max(...ranges.map((r) => r.max)),
        ])
      }
    })
  }

  // The other half of the rule. An unmodelled calibration sits on the curve's
  // own grid, which IS the resolution of that answer -- there is no solver step
  // to hide -- so rebinning it would throw away real detail and disagree with
  // the backend's unmodelled_hpd_ranges, which are not rebinned either.
  it('leaves an unmodelled distribution on its own grid', () => {
    const distribution = cases[0].distribution as Series
    const unmodelled = posteriorRanges(distribution, SIGMA_2, false)

    expect(unmodelled).toEqual(hpdRangesFromCurve(distribution, SIGMA_2))
    expect(unmodelled.some((r) => r.min % REPORTING_STEP_YEARS !== 0)).toBe(true)
  })

  it('carries the choice into the plot bands', () => {
    const distribution = cases[0].distribution as Series

    expect(hpdBands(distribution, SIGMA_2, '95.4%', true)[0].ranges).toEqual(
      posteriorRanges(distribution, SIGMA_2, true)
    )
    expect(hpdBands(distribution, SIGMA_2, '95.4%', false)[0].ranges).toEqual(
      posteriorRanges(distribution, SIGMA_2, false)
    )
  })
})

describe('a reporting resolution other than the default', () => {
  const peak: Array<[number, number]> = Array.from({ length: 400 }, (_, i) => {
    const age = 300 + i * 0.75
    return [age, Math.exp(-((age - 420) ** 2) / (2 * 15 ** 2))]
  })

  it.each([1, 2, 10])('lands ranges on a %i-year step', (step) => {
    const ranges = posteriorRanges(peak, SIGMA_2, step)
    expect(ranges.length).toBeGreaterThan(0)
    for (const r of ranges) {
      expect(Math.abs(r.min / step - Math.round(r.min / step))).toBeLessThan(1e-9)
      expect(Math.abs(r.max / step - Math.round(r.max / step))).toBeLessThan(1e-9)
    }
  })

  it('treats `true` as the default step', () => {
    expect(posteriorRanges(peak, SIGMA_2, true)).toEqual(
      posteriorRanges(peak, SIGMA_2, REPORTING_STEP_YEARS))
  })

  it('reads the step off an analysis, defaulting where none was recorded', () => {
    expect(reportingOf({ analysis_type: 'bayesian', solver: { resolution: 2 } })).toBe(2)
    expect(reportingOf({ analysis_type: 'bayesian', solver: { } })).toBe(true)
    expect(reportingOf({ analysis_type: 'bayesian', solver: null })).toBe(true)
    expect(reportingOf({ analysis_type: 'group_calibration' })).toBe(false)
  })
})
