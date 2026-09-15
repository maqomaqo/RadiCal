import { describe, it, expect } from 'vitest'

import { proposalChangesAnything, reorderToProposal } from './spatialOrder'
import type { ModelElement } from '@/types'

function el(id: string, type: ModelElement['element_type'] = 'phase'): ModelElement {
  return { id, element_type: type, name: id, children: [] }
}

function sequence(...children: ModelElement[]): ModelElement {
  return { id: 'root', element_type: 'sequence', name: 'Region', children }
}

const BRACKETED = () =>
  sequence(el('b0', 'boundary'), el('A'), el('b1', 'boundary'), el('B'),
           el('b2', 'boundary'), el('C'), el('b3', 'boundary'))

describe('reorderToProposal', () => {
  it('permutes the proposed elements into the proposed order', () => {
    const next = reorderToProposal(BRACKETED(), ['C', 'B', 'A'])
    expect((next.children || []).map((c) => c.id)).toEqual(
      ['b0', 'C', 'b1', 'B', 'b2', 'A', 'b3'])
  })

  it('leaves every boundary exactly where it was', () => {
    // The proposal is a claim about which sites are adjacent, not about how
    // the model should be structured. Rewriting the brackets would apply far
    // more than was proposed.
    const next = reorderToProposal(BRACKETED(), ['C', 'A', 'B'])
    const boundaries = (next.children || [])
      .map((c, i) => [i, c.element_type] as const)
      .filter(([, type]) => type === 'boundary')
      .map(([i]) => i)
    expect(boundaries).toEqual([0, 2, 4, 6])
  })

  it('does not drop or duplicate anything', () => {
    const next = reorderToProposal(BRACKETED(), ['B', 'C', 'A'])
    const ids = (next.children || []).map((c) => c.id).sort()
    expect(ids).toEqual(['A', 'B', 'C', 'b0', 'b1', 'b2', 'b3'])
  })

  it('leaves elements the proposal does not name alone', () => {
    const tree = sequence(el('A'), el('hiatus', 'hiatus'), el('B'), el('C'))
    const next = reorderToProposal(tree, ['C', 'B', 'A'])
    // The hiatus keeps its slot; only the three named elements move.
    expect((next.children || []).map((c) => c.id)).toEqual(
      ['C', 'hiatus', 'B', 'A'])
  })

  it('reaches elements nested inside other containers', () => {
    const inner = { ...sequence(el('A'), el('B')), id: 'inner' }
    const tree = sequence(inner, el('C'))
    const next = reorderToProposal(tree, ['B', 'A', 'C'])
    const found = (next.children || [])[0]
    expect((found.children || []).map((c) => c.id)).toEqual(['B', 'A'])
  })

  it('returns the very same tree when nothing would move', () => {
    // Compared by reference on purpose: it is how a caller tells an
    // already-agreeing proposal from one worth applying.
    const tree = BRACKETED()
    expect(reorderToProposal(tree, ['A', 'B', 'C'])).toBe(tree)
    expect(proposalChangesAnything(tree, ['A', 'B', 'C'])).toBe(false)
    expect(proposalChangesAnything(tree, ['B', 'A', 'C'])).toBe(true)
  })

  it('does nothing with fewer than two proposed elements', () => {
    const tree = BRACKETED()
    expect(reorderToProposal(tree, ['A'])).toBe(tree)
    expect(reorderToProposal(tree, [])).toBe(tree)
  })

  it('ignores ids the model does not have', () => {
    const next = reorderToProposal(BRACKETED(), ['C', 'GHOST', 'A', 'B'])
    expect((next.children || []).map((c) => c.id)).toEqual(
      ['b0', 'C', 'b1', 'A', 'b2', 'B', 'b3'])
  })
})
