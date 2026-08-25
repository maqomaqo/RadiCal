import { describe, it, expect } from 'vitest'
import { bootstrapModelTree } from './modelTreeDefaults'
import type { ModelElement } from '@/types'

function phaseNames(root: ModelElement): string[] {
  const out: string[] = []
  const walk = (el: ModelElement) => {
    if (el.element_type === 'phase') out.push(el.name || '')
    for (const child of el.children || []) walk(child)
  }
  walk(root)
  return out
}

function find(root: ModelElement, id: string): ModelElement | null {
  if (root.id === id) return root
  for (const child of root.children || []) {
    const hit = find(child, id)
    if (hit) return hit
  }
  return null
}

describe('bootstrapModelTree', () => {
  it('puts uncontexted dates in one phase inside a bracketed sequence', () => {
    const { tree, assignments } = bootstrapModelTree([{ id: 'a' }, { id: 'b', context: null }])

    expect(tree.element_type).toBe('sequence')
    const types = (tree.children || []).map((c) => c.element_type)
    expect(types).toEqual(['boundary', 'phase', 'boundary'])
    expect(phaseNames(tree)).toEqual(['Phase 1'])
    expect(assignments.a).toBe(assignments.b)
    expect(find(tree, assignments.a)?.element_type).toBe('phase')
  })

  it('gives each context its own phase, in the order the dates introduce them', () => {
    const { tree, assignments } = bootstrapModelTree([
      { id: 'a', context: 'Layer 3' },
      { id: 'b', context: 'Layer 2' },
      { id: 'c', context: 'Layer 3' },
    ])

    expect(phaseNames(tree)).toEqual(['Site', 'Layer 3', 'Layer 2'])
    expect(assignments.a).toBe(assignments.c)
    expect(assignments.b).not.toBe(assignments.a)
    expect(find(tree, assignments.b)?.name).toBe('Layer 2')
  })

  it('nests several contexts in an unordered phase, asserting no order between them', () => {
    // A Sequence would put the contexts in chronological order, which the
    // collection never claimed -- so the wrapper has to be a Phase.
    const { tree } = bootstrapModelTree([
      { id: 'a', context: 'Layer 3' },
      { id: 'b', context: 'Layer 2' },
    ])

    const body = (tree.children || [])[1]
    expect(body.element_type).toBe('phase')
    expect((body.children || []).map((c) => c.element_type)).toEqual(['phase', 'phase'])
  })

  it('does not wrap a single context, and treats blank contexts as none', () => {
    const { tree, assignments } = bootstrapModelTree([
      { id: 'a', context: 'Layer 3' },
      { id: 'b', context: '  ' },
    ])

    expect(phaseNames(tree)).toEqual(['Site', 'Layer 3', 'Unassigned dates'])
    expect(find(tree, assignments.b)?.name).toBe('Unassigned dates')

    const single = bootstrapModelTree([{ id: 'a', context: 'Layer 3' }])
    expect(phaseNames(single.tree)).toEqual(['Layer 3'])
    expect((single.tree.children || [])[1].id).toBe(single.assignments.a)
  })

  it('assigns every date to a phase that exists', () => {
    const dates = [
      { id: 'a', context: 'Layer 3' },
      { id: 'b', context: null },
      { id: 'c', context: 'Layer 2' },
    ]
    const { tree, assignments } = bootstrapModelTree(dates)

    for (const date of dates) {
      expect(find(tree, assignments[date.id])?.element_type).toBe('phase')
    }
  })

  it('still produces a usable starting structure with no dates', () => {
    const { tree, assignments } = bootstrapModelTree([])
    expect(phaseNames(tree)).toEqual(['Phase 1'])
    expect(assignments).toEqual({})
  })
})
