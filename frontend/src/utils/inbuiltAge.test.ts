import { describe, expect, it } from 'vitest'
import { describeInbuilt, inbuiltCell } from './inbuiltAge'

describe('inbuilt age wording', () => {
  it('says what each allowance means', () => {
    expect(describeInbuilt(null)).toMatch(/same age as its event/)
    expect(describeInbuilt(30, 'uniform')).toMatch(/up to 30 years older/)
    expect(describeInbuilt(15, 'exponential')).toMatch(/on average 15 years older/)
  })

  it('reads as a maximum or a typical age in a table', () => {
    expect(inbuiltCell(30, 'uniform')).toBe('≤ 30 yr')
    expect(inbuiltCell(15, 'exponential')).toBe('~ 15 yr')
    expect(inbuiltCell(0)).toBe('')
  })
})
