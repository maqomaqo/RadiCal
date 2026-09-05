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

import {
  INDENT_PX,
  commitDrop,
  flattenTree,
  getProjection,
  type FlatNode,
} from './ModelTreeEditor'
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

describe('sticky depth (drag hysteresis)', () => {
  // The indicator must not flicker between two nesting levels while the
  // pointer sits on the boundary between them, so the step a drag is
  // already on survives a small wobble past the halfway mark.
  it('keeps the step it is already on for a wobble just past halfway', () => {
    const tree = sampleTree()
    const flat = flattenTree(tree)
    // 0.55 of an indent past centre would round up to a full step on its own...
    expect(getProjection(flat, 'end', 'end', 0.55 * INDENT_PX, 'root')!.depthDelta).toBe(1)
    // ...but not while the drag is already settled on step 0.
    expect(getProjection(flat, 'end', 'end', 0.55 * INDENT_PX, 'root', 0)!.depthDelta).toBe(0)
  })

  it('still changes step once the pointer moves decisively', () => {
    const tree = sampleTree()
    const flat = flattenTree(tree)
    expect(getProjection(flat, 'end', 'end', 0.7 * INDENT_PX, 'root', 0)!.depthDelta).toBe(1)
  })

  it('carries the sticky step through to the committed tree', () => {
    const tree = sampleTree()
    const flat = flattenTree(tree)
    // Same gesture, different drag history: settled on step 0, the wobble
    // must not silently renest "end" inside Phase 1.
    expect(commitDrop(tree, flat, 'end', 'end', 0.55 * INDENT_PX, 0)).toBeNull()
    expect(commitDrop(tree, flat, 'end', 'end', 0.55 * INDENT_PX)).not.toBeNull()
  })
})

describe('drop indicator', () => {
  // What the indicator promises and what the drop does are both derived
  // from the same projection -- these assert they cannot disagree.
  it('reports the row the block lands above, and null at the end of the outline', () => {
    const tree = sampleTree()
    const flat = flattenTree(tree)
    expect(getProjection(flat, 'end', 'start', 0, 'root')!.insertBeforeId).toBe('start')
    expect(getProjection(flat, 'start', 'start', 0, 'root')!.insertBeforeId).toBe('phase1')
    // Dragging the last row onto itself has nothing following it.
    expect(getProjection(flat, 'end', 'end', 0, 'root')!.insertBeforeId).toBeNull()
  })

  it('promises the depth the block actually ends up at', () => {
    const tree = sampleTree()
    const flat = flattenTree(tree)
    const ids = flat.map((n) => n.id)
    for (const activeId of ids) {
      for (const overId of ids) {
        for (const offset of [-2, -1, 0, 1, 2].map((n) => n * INDENT_PX)) {
          const projection = getProjection(flat, activeId, overId, offset, 'root')!
          const next = commitDrop(tree, flat, activeId, overId, offset)
          if (!next) continue
          const landed = flattenTree(next).find((n) => n.id === activeId)!
          expect(landed.depth).toBe(projection.depth)
        }
      }
    }
  })

  it('names a container that really is the block’s new parent', () => {
    const tree = sampleTree()
    const flat = flattenTree(tree)
    const ids = flat.map((n) => n.id)
    for (const activeId of ids) {
      for (const overId of ids) {
        for (const offset of [-2, -1, 0, 1, 2].map((n) => n * INDENT_PX)) {
          const projection = getProjection(flat, activeId, overId, offset, 'root')!
          const next = commitDrop(tree, flat, activeId, overId, offset)
          if (!next) continue
          // Whatever the indicator labelled itself with must be the element
          // that actually ends up holding the dragged block.
          const parent = flattenTree(next).find((n) =>
            (n.element.children || []).some((c) => c.id === activeId)
          )
          const actualParentId = parent ? parent.id : next.id
          expect(actualParentId).toBe(projection.parentId)
        }
      }
    }
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

// The keyboard path into the same machinery. Indent and outdent are a
// commitDrop against the element's OWN position with a synthetic horizontal
// offset of exactly one indent step, which is what makes the keyboard and the
// pointer share one set of legality rules rather than two implementations that
// can disagree.
describe('keyboard renesting', () => {
  const renest = (id: string, direction: 1 | -1, tree = sampleTree()) =>
    commitDrop(tree, flattenTree(tree), id, id, direction * INDENT_PX)

  it('indents an element into the container immediately above it', () => {
    // `nested` already sits inside phase1; `end` follows phase1 at depth 1, so
    // indenting it puts it inside phase1 too.
    const next = renest('end', 1)
    expect(next).not.toBeNull()
    expect(order(next!)).toEqual([
      ['start', 1],
      ['phase1', 1],
      ['nested', 2],
      ['end', 2],
    ])
  })

  it('outdents an element back out of its container', () => {
    const indented = renest('end', 1)!
    const next = commitDrop(indented, flattenTree(indented), 'end', 'end', -INDENT_PX)
    expect(next).not.toBeNull()
    expect(order(next!)).toEqual(order(sampleTree()))
  })

  it('refuses a move the clamp does not allow rather than building an illegal tree', () => {
    // `start` is the first row: there is no container above it to indent into,
    // and depth 1 is already the shallowest level.
    expect(renest('start', 1)).toBeNull()
    expect(renest('start', -1)).toBeNull()
  })

  it('never nests anything inside a non-container', () => {
    const tree = sampleTree()
    for (const id of flattenTree(tree).map((n) => n.id)) {
      for (const direction of [1, -1] as const) {
        const next = renest(id, direction, tree)
        if (!next) continue
        // A boundary or hiatus must never acquire children.
        const walkAll = (el: ModelElement): void => {
          if (el.element_type === 'boundary' || el.element_type === 'hiatus') {
            expect(el.children ?? []).toHaveLength(0)
          }
          for (const child of el.children ?? []) walkAll(child)
        }
        walkAll(next)
      }
    }
  })

  it('keeps the result a rebuildable sequence, as a drag does', () => {
    const tree = sampleTree()
    for (const id of flattenTree(tree).map((n) => n.id)) {
      for (const direction of [1, -1] as const) {
        const next = renest(id, direction, tree)
        if (!next) continue
        const flat = flattenTree(next)
        for (let i = 1; i < flat.length; i++) {
          expect(flat[i].depth).toBeLessThanOrEqual(flat[i - 1].depth + 1)
        }
      }
    }
  })
})
