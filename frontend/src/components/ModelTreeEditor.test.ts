// Tests for the drag-and-drop reparenting logic in ModelTreeEditor: the
// flat-list projection and rebuild that turns "drag this row to a new
// vertical position and horizontal indent" into a new tree.
//
// dnd-kit's own sensor/collision machinery isn't tested here -- that's a
// mature library's job, not this component's -- only the pure functions
// this component adds on top of it: flattening the tree into an ordered,
// depth-tagged list, projecting where a drop lands, and rebuilding a tree
// from the result. This is also why they run as plain data-in/data-out
// tests rather than through a rendered drag gesture: a real drag can only
// be checked by eye, but "does this input tree turn into that output tree"
// can be asserted directly.
import { describe, expect, it } from 'vitest'

import { INDENT_PX, commitDrop, flattenTree, type FlatNode } from './ModelTreeEditor'
import type { ModelElement } from '@/types'

function el(
  id: string,
  element_type: ModelElement['element_type'],
  name: string,
  children: ModelElement[] = []
): ModelElement {
  return { id, element_type, name, tpq_age_bp: null, taq_age_bp: null, children }
}

// root(seq)
//   start (boundary)
//   phase1 (phase)
//     nested (sequence)
//   end (boundary)
function sampleTree(): ModelElement {
  return el('root', 'sequence', 'Site sequence', [
    el('start', 'boundary', 'Start of sequence'),
    el('phase1', 'phase', 'Phase 1', [el('nested', 'sequence', 'Nested sequence')]),
    el('end', 'boundary', 'End of sequence'),
  ])
}

describe('flattenTree', () => {
  it('walks depth-first, excluding the root, tagging each node with its indent depth', () => {
    const flat = flattenTree(sampleTree())
    expect(flat.map((n) => [n.id, n.depth])).toEqual([
      ['start', 1],
      ['phase1', 1],
      ['nested', 2],
      ['end', 1],
    ])
  })
})

function commit(activeId: string, overId: string, dragOffsetX: number, tree = sampleTree()) {
  const flat = flattenTree(tree)
  return commitDrop(tree, flat, activeId, overId, dragOffsetX)
}

function order(tree: ModelElement): Array<[string, number]> {
  return flattenTree(tree).map((n) => [n.id, n.depth])
}

describe('commitDrop', () => {
  it('reorders among siblings without changing depth', () => {
    const next = commit('start', 'end', 0)
    expect(next).not.toBeNull()
    expect(order(next!)).toEqual([
      ['phase1', 1],
      ['nested', 2],
      ['start', 1],
      ['end', 1],
    ])
  })

  it('outdents a nested element to become a sibling of its former parent, carrying its own children along', () => {
    // "nested" starts at depth 2, inside phase1. Dragging it up to just
    // before "end" with a big leftward offset should land it at depth 1,
    // as a sibling between phase1 and end.
    const next = commit('nested', 'end', -3 * INDENT_PX)
    expect(next).not.toBeNull()
    expect(order(next!)).toEqual([
      ['start', 1],
      ['phase1', 1],
      ['nested', 1],
      ['end', 1],
    ])
    const phase1 = next!.children!.find((c) => c.id === 'phase1')!
    expect(phase1.children).toEqual([])
  })

  it('indents an element to become a child of the container immediately before it', () => {
    // A boundary sitting right after a (currently childless) phase, dragged
    // further right without changing its vertical position, should nest
    // inside that phase rather than staying a sibling.
    const tree = el('root', 'sequence', 'Root', [
      el('phaseA', 'phase', 'Phase A'),
      el('after', 'boundary', 'After'),
    ])
    const next = commit('after', 'after', INDENT_PX, tree)
    expect(next).not.toBeNull()
    expect(order(next!)).toEqual([
      ['phaseA', 1],
      ['after', 2],
    ])
    const phaseA = next!.children!.find((c) => c.id === 'phaseA')!
    expect(phaseA.children!.map((c) => c.id)).toEqual(['after'])
  })

  it('clamps depth so an element can never nest inside a non-container', () => {
    // "start" is a boundary (not a container). Dropping "end" right after
    // it with a big rightward offset must not nest "end" inside it.
    const next = commit('end', 'start', 3 * INDENT_PX)
    expect(next).not.toBeNull()
    const flat = flattenTree(next!)
    const startNode = flat.find((n) => n.id === 'start')!
    const endNode = flat.find((n) => n.id === 'end')!
    expect(endNode.depth).toBeLessThanOrEqual(startNode.depth)
  })

  it('moves a whole subtree together, preserving the relative structure of its descendants', () => {
    const tree = el('root', 'sequence', 'Root', [
      el('a', 'boundary', 'A'),
      el('group', 'sequence', 'Group', [
        el('inner', 'phase', 'Inner phase', [el('deep', 'sequence', 'Deep')]),
      ]),
      el('b', 'boundary', 'B'),
    ])
    // Move "group" (and everything inside it) out to before "a".
    const next = commit('group', 'a', 0, tree)
    expect(next).not.toBeNull()
    expect(order(next!)).toEqual([
      ['group', 1],
      ['inner', 2],
      ['deep', 3],
      ['a', 1],
      ['b', 1],
    ])
  })

  it('returns null for a genuine no-op drop', () => {
    const next = commit('start', 'start', 0)
    expect(next).toBeNull()
  })

  it('is a no-op when dropped back in its original slot with no horizontal movement', () => {
    // Hovering back over its own original neighbour with zero offset should
    // reconstruct exactly the starting order, and commitDrop should
    // recognise that as a no-op rather than a wasted state update.
    const next = commit('phase1', 'phase1', 0)
    expect(next).toBeNull()
  })
})

describe('FlatNode depth invariant', () => {
  it('every commitDrop result stays a valid rebuildable sequence (depth increases by at most 1 at a time)', () => {
    const tree = sampleTree()
    // Try every (active, over, offset) combination and check the invariant
    // holds for every legal drop -- this is what makes rebuildFromFlat safe.
    const ids = flattenTree(tree).map((n) => n.id)
    for (const activeId of ids) {
      for (const overId of ids) {
        for (const offset of [-3, -1, 0, 1, 3].map((n) => n * INDENT_PX)) {
          const next = commitDrop(tree, flattenTree(tree), activeId, overId, offset)
          if (!next) continue
          const flat: FlatNode[] = flattenTree(next)
          for (let i = 1; i < flat.length; i++) {
            expect(flat[i].depth).toBeLessThanOrEqual(flat[i - 1].depth + 1)
          }
        }
      }
    }
  })
})
