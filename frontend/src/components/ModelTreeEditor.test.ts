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
  orderedForHolder,
  phaseOptions,
  validateTree,
  type FlatNode,
} from './ModelTreeEditor'
import { depositionSequenceTree, wiggleMatchTree } from '@/utils/modelTreeDefaults'
import type { MatrixMeasurement, ModelElement } from '@/types'

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

// --------------------------------------------------------------------------
// Sequences with a measured ordinate
// --------------------------------------------------------------------------
//
// A wiggle match and a deposition sequence hold DATES but no elements, and
// each is built on an ordinate the date has to carry. validateTree is the
// client-side mirror of services/model_tree.py: the point of duplicating it
// is that a problem surfaces while the model is being built rather than on
// submit, so what is asserted here is that it catches the same things.

function date(id: string, extra: Partial<MatrixMeasurement> = {}): MatrixMeasurement {
  return { id, sample_name: id.toUpperCase(), age_bp: 2500, error_1sigma: 25, ...extra }
}

const dated = date

describe('phaseOptions', () => {
  it('offers every element a date can be attached to, not phases alone', () => {
    const tree = el('root', 'phase', 'Site', [
      el('p1', 'phase', 'Phase 1'),
      el('w1', 'wiggle_sequence', 'Timber 1'),
      el('d1', 'deposition_sequence', 'Core A'),
      el('b1', 'boundary', 'Start'),
    ])
    expect(phaseOptions(tree).map((o) => [o.id, o.element_type])).toEqual([
      ['root', 'phase'],
      ['p1', 'phase'],
      ['w1', 'wiggle_sequence'],
      ['d1', 'deposition_sequence'],
    ])
  })

  it('offers a root that is itself a wiggle match', () => {
    // The preset and the OxCal importer both build exactly this shape, and a
    // walk that only looked at CHILDREN would leave its dates nowhere to go.
    const tree = el('w1', 'wiggle_sequence', 'Timber 1')
    expect(phaseOptions(tree).map((o) => o.id)).toEqual(['w1'])
  })
})

describe('validateTree on a wiggle match', () => {
  const tree = el('w1', 'wiggle_sequence', 'Timber 1')

  it('accepts a series whose members all carry a distinct gap', () => {
    const dates = [date('a', { ring_offset: 0 }), date('b', { ring_offset: 120 })]
    expect(validateTree(tree, { a: 'w1', b: 'w1' }, dates)).toEqual([])
  })

  it('refuses a member with no gap, because the gaps are the model', () => {
    const dates = [date('a', { ring_offset: 0 }), date('b')]
    const problems = validateTree(tree, { a: 'w1', b: 'w1' }, dates)
    expect(problems.some((p) => p.includes('no ring offset'))).toBe(true)
  })

  it('refuses two members at the same gap', () => {
    const dates = [date('a', { ring_offset: 40 }), date('b', { ring_offset: 40 })]
    const problems = validateTree(tree, { a: 'w1', b: 'w1' }, dates)
    expect(problems.some((p) => p.includes('same gap'))).toBe(true)
  })

  it('refuses a series holding other elements', () => {
    const withChild = el('w1', 'wiggle_sequence', 'Timber 1', [
      el('b1', 'boundary', 'Felling'),
    ])
    const problems = validateTree(withChild, { a: 'w1' }, [date('a', { ring_offset: 0 })])
    expect(problems.some((p) => p.includes('contains other elements'))).toBe(true)
  })

  it('refuses an empty series', () => {
    const problems = validateTree(tree, {}, [])
    expect(problems.some((p) => p.includes('no dates in it'))).toBe(true)
  })
})

describe('validateTree on a deposition sequence', () => {
  it('accepts levels at distinct depths', () => {
    const tree = el('d1', 'deposition_sequence', 'Core A')
    const dates = [date('a', { depth_cm: 60 }), date('b', { depth_cm: 20 })]
    expect(validateTree(tree, { a: 'd1', b: 'd1' }, dates)).toEqual([])
  })

  it('refuses a date with no depth, since depth is the axis it runs along', () => {
    const tree = el('d1', 'deposition_sequence', 'Core A')
    const dates = [date('a', { depth_cm: 60 }), date('b')]
    const problems = validateTree(tree, { a: 'd1', b: 'd1' }, dates)
    expect(problems.some((p) => p.includes('no depth'))).toBe(true)
  })

  it('refuses two levels at one depth, counting undated ones too', () => {
    // A zero interval has no accumulation rate, whether the two levels are
    // both dated or one of them is an undated question about a depth.
    const tree: ModelElement = {
      ...el('d1', 'deposition_sequence', 'Core A', [
        { ...el('lv', 'interpolated_depth', 'Tephra'), params: { depth_cm: 60 } },
      ]),
    }
    const dates = [date('a', { depth_cm: 60 }), date('b', { depth_cm: 20 })]
    const problems = validateTree(tree, { a: 'd1', b: 'd1' }, dates)
    expect(problems.some((p) => p.includes('same depth'))).toBe(true)
  })

  it('refuses an undated level with no depth to report an age at', () => {
    const tree = el('d1', 'deposition_sequence', 'Core A', [
      el('lv', 'interpolated_depth', 'Tephra'),
    ])
    const dates = [date('a', { depth_cm: 60 }), date('b', { depth_cm: 20 })]
    const problems = validateTree(tree, { a: 'd1', b: 'd1' }, dates)
    expect(problems.some((p) => p.includes('no depth, so there is no age'))).toBe(true)
  })

  it('refuses anything but an undated level inside it', () => {
    const tree = el('d1', 'deposition_sequence', 'Core A', [el('p1', 'phase', 'Phase 1')])
    const dates = [date('a', { depth_cm: 60 }), date('b', { depth_cm: 20 })]
    const problems = validateTree(tree, { a: 'd1', b: 'd1' }, dates)
    expect(problems.some((p) => p.includes('dates and undated levels only'))).toBe(true)
  })
})

describe('the presets', () => {
  it('a wiggle match puts every date in one series and adds nothing else', () => {
    const { tree, assignments } = wiggleMatchTree(['a', 'b', 'c'])
    expect(tree.element_type).toBe('wiggle_sequence')
    expect(tree.children).toEqual([])
    // No Boundaries: a wiggle match has none, and inventing a pair would
    // assert a transition the model does not have.
    expect(Object.values(assignments)).toEqual([tree.id, tree.id, tree.id])
    expect(tree.params?.gap_units).toBe('rings')
  })

  it('a deposition sequence infers its rate rather than fixing one', () => {
    const { tree } = depositionSequenceTree(['a', 'b'])
    expect(tree.element_type).toBe('deposition_sequence')
    expect(tree.params?.k).toBeNull()
    expect(tree.params?.k_variable).toBe(true)
  })
})

describe('orderedForHolder', () => {
  it('leaves a phase in the order the user arranged', () => {
    // A Phase asserts no order, so the list is the user's, not the model's.
    const dates = [dated('b', {}), dated('a', {})]
    expect(orderedForHolder('phase', dates).map((m) => m.id)).toEqual(['b', 'a'])
  })

  it('lists a wiggle match oldest first, which is the largest gap', () => {
    // A gap counts years BEFORE the reference, so the biggest gap is the
    // oldest ring -- the same direction the outline reads in.
    const dates = [
      dated('outer', { ring_offset: 0 }),
      dated('inner', { ring_offset: 250 }),
      dated('middle', { ring_offset: 120 }),
    ]
    expect(orderedForHolder('wiggle_sequence', dates).map((m) => m.id)).toEqual([
      'inner',
      'middle',
      'outer',
    ])
  })

  it('lists a deposition sequence deepest first, which is oldest first', () => {
    const dates = [
      dated('shallow', { depth_cm: 20 }),
      dated('deep', { depth_cm: 100 }),
      dated('mid', { depth_cm: 60 }),
    ]
    expect(orderedForHolder('deposition_sequence', dates).map((m) => m.id)).toEqual([
      'deep',
      'mid',
      'shallow',
    ])
  })

  it('puts a date with no ordinate yet at the end rather than at a guess', () => {
    // Half-entered is the normal state while a series is being built, and a
    // row that jumped position on every keystroke would be unusable.
    const dates = [dated('blank', {}), dated('a', { ring_offset: 40 })]
    expect(orderedForHolder('wiggle_sequence', dates).map((m) => m.id)).toEqual(['a', 'blank'])
  })

  it('does not mutate the list it was given', () => {
    const dates = [dated('a', { ring_offset: 0 }), dated('b', { ring_offset: 60 })]
    orderedForHolder('wiggle_sequence', dates)
    expect(dates.map((m) => m.id)).toEqual(['a', 'b'])
  })
})

describe('validateTree on an outlier model in a wiggle match', () => {
  const tree = el('w1', 'wiggle_sequence', 'Timber 1')

  it('refuses a flagged member, naming it', () => {
    // A wiggle match has no room for a date to sit apart from the rest: every
    // member is one age plus a fixed number. The server refuses it rather than
    // dropping it, and this says so before the run.
    const dates = [
      dated('a', { ring_offset: 0 }),
      dated('b', { ring_offset: 120, outlier_prior: 0.05 }),
    ]
    const problems = validateTree(tree, { a: 'w1', b: 'w1' }, dates)
    expect(problems.some((p) => p.includes('outlier model'))).toBe(true)
    expect(problems.some((p) => p.includes('"B"'))).toBe(true)
  })

  it('treats a zero prior as no setting at all', () => {
    const dates = [
      dated('a', { ring_offset: 0, outlier_prior: 0 }),
      dated('b', { ring_offset: 120, outlier_prior: 0 }),
    ]
    expect(validateTree(tree, { a: 'w1', b: 'w1' }, dates)).toEqual([])
  })

  it('leaves a phase alone, where an outlier model is the ordinary thing', () => {
    const phase = el('p1', 'phase', 'Phase 1')
    const dates = [dated('a', { outlier_prior: 0.05 })]
    expect(validateTree(phase, { a: 'p1' }, dates)).toEqual([])
  })
})
