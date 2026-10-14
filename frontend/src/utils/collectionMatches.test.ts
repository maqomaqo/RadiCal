import { describe, expect, it } from 'vitest'
import { describeMatch, splitByCollection } from './collectionMatches'

const date = (sample_name: string, age_bp = 614, error_1sigma = 25, curve_id = 'shcal20') => ({
  sample_name,
  age_bp,
  error_1sigma,
  curve_id,
})

describe('splitByCollection', () => {
  it('separates new dates from ones the collection holds, by name', () => {
    const { fresh, matched } = splitByCollection(
      [date('LA10001'), date('LA10002'), date(' la10003 ')],
      [date('LA10003'), date('LA10001')],
    )
    expect(fresh.map((d) => d.sample_name)).toEqual(['LA10002'])
    expect(matched.map((m) => m.date.sample_name)).toEqual(['LA10001', ' la10003 '])
    expect(matched.every((m) => m.differs.length === 0)).toBe(true)
  })

  it('says which values differ from the stored date', () => {
    const { matched } = splitByCollection(
      [date('LA10001', 620, 30, 'intcal20')],
      [date('LA10001')],
    )
    expect(matched[0].differs).toEqual(['age', 'error', 'curve'])
    expect(describeMatch(matched[0])).toBe(
      'LA10001 (age 620 here, 614 stored; error 30 here, 25 stored; curve intcal20 here, shcal20 stored)'
    )
  })

  it('names an identical match plainly', () => {
    const { matched } = splitByCollection([date('LA10001')], [date('LA10001')])
    expect(describeMatch(matched[0])).toBe('LA10001')
  })
})
