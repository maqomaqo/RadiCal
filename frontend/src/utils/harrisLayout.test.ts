// Geometry tests for the Harris matrix layout.
//
// These carry more weight than a normal unit test, because they are the ONLY
// way this diagram gets checked. reactflow draws into a measured viewport, so
// there is no DOM assertion that says "the boxes line up" -- the nodes are
// present at coordinates whether or not those coordinates are sensible. The
// layout was therefore written as pure data in, geometry out, so the three
// things that were wrong with the old diagram can each be stated as a
// property and held to it:
//
//   no implied edges  -- the cross-product that drew 175 edges for 39 orderings
//   containment       -- a nested phase is INSIDE its parent, not beside it
//   alignment         -- children are centred on the parent they belong to
//
// Plus the invariants that keep it readable: oldest at the bottom, siblings
// that do not overlap, and rules that span their parent.
import { describe, expect, it } from 'vitest'

import {
  CONTEXT_HEIGHT,
  CONTEXT_WIDTH,
  PADDING,
  layoutHarrisMatrix,
  summariseTree,
  type HarrisLayout,
  type LaidOutNode,
} from './harrisLayout'
import type { MatrixMeasurement, ModelElement } from '@/types'

function el(
  id: string,
  element_type: ModelElement['element_type'],
  name: string,
  children: ModelElement[] = []
): ModelElement {
  return { id, element_type, name, tpq_age_bp: null, taq_age_bp: null, children }
}

function date(id: string, elementId: string): MatrixMeasurement {
  return { id, sample_name: id, age_bp: 3000, element_id: elementId }
}

/** The real Nenthorn model -- the one the complaint was raised against. */
function nenthorn(): ModelElement {
  return el('nenthorn', 'sequence', 'Nenthorn', [
    el('b_start', 'boundary', 'Start Nenthorn'),
    el('quarry', 'sequence', 'Quarry', [el('ff_j', 'phase', 'Fire_Feature_J')]),
    el('rock', 'sequence', 'Rockshelter', [
      el('fires', 'phase', 'Fires', [
        el('ff_k', 'phase', 'Fire_Feature_K'),
        el('ff_a', 'phase', 'Fire_Feature_A'),
      ]),
    ]),
    el('b_end', 'boundary', 'End Nenthorn'),
  ])
}

/** A site of G ordered groups, each holding S parallel sub-phases. */
function siteModel(groups: number, subphases: number): ModelElement {
  const children: ModelElement[] = [el('b0', 'boundary', 'Start')]
  for (let g = 0; g < groups; g++) {
    children.push(
      el(`g${g}`, 'phase', `Group ${g}`,
        Array.from({ length: subphases }, (_, s) => el(`g${g}s${s}`, 'phase', `G${g}.${s}`)))
    )
  }
  children.push(el('bEnd', 'boundary', 'End'))
  return el('site', 'sequence', 'Site', children)
}

const byId = (layout: HarrisLayout) =>
  new Map(layout.nodes.map((n) => [n.id, n]))

/** A node's position in the coordinate space of the whole diagram. */
function absolute(layout: HarrisLayout, id: string): { x: number; y: number } {
  const nodes = byId(layout)
  let x = 0
  let y = 0
  let current: LaidOutNode | undefined = nodes.get(id)
  while (current) {
    x += current.x
    y += current.y
    current = current.parentId ? nodes.get(current.parentId) : undefined
  }
  return { x, y }
}

const centreX = (layout: HarrisLayout, id: string) =>
  absolute(layout, id).x + byId(layout).get(id)!.width / 2

describe('the edges that used to be implied', () => {
  it('draws one arrow between adjacent blocks, not one per contained phase', () => {
    const layout = layoutHarrisMatrix(nenthorn(), [])

    // Quarry and Rockshelter are adjacent with no Boundary between them, so
    // exactly one arrow carries that order. The old code drew three:
    // Fire_Feature_J to each of Fires, Fire_Feature_K and Fire_Feature_A.
    expect(layout.edges).toHaveLength(1)
    expect(layout.edges[0]).toMatchObject({ source: 'rock', target: 'quarry' })
  })

  it('never connects a phase to something its container already orders', () => {
    const layout = layoutHarrisMatrix(nenthorn(), [])
    const touched = new Set(layout.edges.flatMap((e) => [e.source, e.target]))

    for (const id of ['ff_j', 'ff_k', 'ff_a', 'fires']) {
      expect(touched.has(id)).toBe(false)
    }
  })

  it('stays linear in the number of nodes instead of quadratic', () => {
    // The measured behaviour of the old projection, for reference:
    //   2x2 -> 9 edges, 3x3 -> 32, 5x4 -> 100, 8x4 -> 175.
    for (const [groups, subphases] of [[2, 2], [3, 3], [5, 4], [8, 4]] as const) {
      const layout = layoutHarrisMatrix(siteModel(groups, subphases), [])
      // Groups are adjacent to each other, with Boundaries only at the ends,
      // so the chain is one arrow per adjacent pair of groups.
      expect(layout.edges).toHaveLength(groups - 1)
    }
  })

  it('lets a boundary rule carry the order instead of an arrow', () => {
    const withBoundary = el('s', 'sequence', 'S', [
      el('a', 'phase', 'A'),
      el('b_mid', 'boundary', 'Transition'),
      el('b', 'phase', 'B'),
    ])
    const without = el('s2', 'sequence', 'S', [
      el('a2', 'phase', 'A'),
      el('b2', 'phase', 'B'),
    ])

    // The rule spans the full width between them and carries the transition's
    // own date, so an arrow beside it would be the redundant ink again.
    expect(layoutHarrisMatrix(withBoundary, []).edges).toHaveLength(0)
    // With nothing between them, the arrow is the only thing saying which is
    // older, so it must be drawn.
    expect(layoutHarrisMatrix(without, []).edges).toHaveLength(1)
  })
})

describe('containment is a box, not an edge', () => {
  it('nests a phase inside the phase that contains it', () => {
    const layout = layoutHarrisMatrix(nenthorn(), [])
    const nodes = byId(layout)

    expect(nodes.get('ff_k')!.parentId).toBe('fires')
    expect(nodes.get('ff_a')!.parentId).toBe('fires')
    expect(nodes.get('fires')!.parentId).toBe('rock')
    expect(nodes.get('rock')!.parentId).toBe('nenthorn')
  })

  it('draws a grouping phase as a container and a dated phase as a context', () => {
    const layout = layoutHarrisMatrix(nenthorn(), [])
    const nodes = byId(layout)

    // "Fires" contains the fire features; it is not one of them.
    expect(nodes.get('fires')!.kind).toBe('group')
    expect(nodes.get('ff_k')!.kind).toBe('context')
    expect(nodes.get('ff_j')!.kind).toBe('context')
  })

  it('keeps a child strictly within its parent box', () => {
    const layout = layoutHarrisMatrix(siteModel(3, 3), [])
    const nodes = byId(layout)

    for (const node of layout.nodes) {
      if (!node.parentId) continue
      const parent = nodes.get(node.parentId)!
      expect(node.x).toBeGreaterThanOrEqual(0)
      expect(node.y).toBeGreaterThanOrEqual(0)
      expect(node.x + node.width).toBeLessThanOrEqual(parent.width + 0.001)
      expect(node.y + node.height).toBeLessThanOrEqual(parent.height + 0.001)
    }
  })
})

describe('descendants align with their parent', () => {
  it('centres a stacked child on the sequence that holds it', () => {
    const layout = layoutHarrisMatrix(nenthorn(), [])

    // Quarry and Rockshelter are different widths -- Rockshelter nests two
    // phases -- so this only holds if each is centred rather than placed at
    // an index.
    expect(centreX(layout, 'quarry')).toBeCloseTo(centreX(layout, 'nenthorn'), 6)
    expect(centreX(layout, 'rock')).toBeCloseTo(centreX(layout, 'nenthorn'), 6)
  })

  it('centres a row of parallel phases on their container', () => {
    const layout = layoutHarrisMatrix(nenthorn(), [])
    const nodes = byId(layout)

    const left = absolute(layout, 'ff_k').x
    const right = absolute(layout, 'ff_a').x + nodes.get('ff_a')!.width
    expect((left + right) / 2).toBeCloseTo(centreX(layout, 'fires'), 6)
  })

  it('holds however deep the nesting goes', () => {
    const deep = el('s', 'sequence', 'S', [
      el('p1', 'phase', 'P1', [
        el('p2', 'phase', 'P2', [
          el('p3', 'phase', 'P3', [el('leaf', 'phase', 'Leaf')]),
        ]),
      ]),
    ])
    const layout = layoutHarrisMatrix(deep, [])

    for (const id of ['p1', 'p2', 'p3', 'leaf']) {
      expect(centreX(layout, id)).toBeCloseTo(centreX(layout, 's'), 6)
    }
  })
})

describe('reading direction and spacing', () => {
  it('puts the oldest at the bottom', () => {
    // The tree runs oldest first; a Harris matrix reads oldest at the bottom,
    // so the first child must end up with the LARGEST y.
    const layout = layoutHarrisMatrix(nenthorn(), [])

    const start = absolute(layout, 'b_start').y
    const quarry = absolute(layout, 'quarry').y
    const rock = absolute(layout, 'rock').y
    const end = absolute(layout, 'b_end').y

    expect(start).toBeGreaterThan(quarry)
    expect(quarry).toBeGreaterThan(rock)
    expect(rock).toBeGreaterThan(end)
  })

  it('keeps the whole stack in order for a long sequence', () => {
    const layout = layoutHarrisMatrix(siteModel(6, 2), [])
    const ys = Array.from({ length: 6 }, (_, g) => absolute(layout, `g${g}`).y)

    for (let i = 0; i < ys.length - 1; i++) {
      expect(ys[i]).toBeGreaterThan(ys[i + 1])
    }
  })

  it('never overlaps two sibling boxes', () => {
    const layout = layoutHarrisMatrix(siteModel(4, 3), [])
    const nodes = layout.nodes

    for (const a of nodes) {
      for (const b of nodes) {
        if (a === b || a.parentId !== b.parentId) continue
        const separated =
          a.x + a.width <= b.x + 0.001 ||
          b.x + b.width <= a.x + 0.001 ||
          a.y + a.height <= b.y + 0.001 ||
          b.y + b.height <= a.y + 0.001
        expect(separated, `${a.name} overlaps ${b.name}`).toBe(true)
      }
    }
  })

  it('spans a boundary rule across its parent content width', () => {
    const layout = layoutHarrisMatrix(nenthorn(), [])
    const nodes = byId(layout)
    const root = nodes.get('nenthorn')!

    for (const id of ['b_start', 'b_end']) {
      const rule = nodes.get(id)!
      expect(rule.kind).toBe('boundary')
      expect(rule.x).toBe(PADDING)
      expect(rule.width).toBe(root.width - PADDING * 2)
    }
  })
})

describe('dates and what is missing', () => {
  it('counts each context’s own dates', () => {
    const dates = [date('d1', 'ff_j'), date('d2', 'ff_j'), date('d3', 'ff_k')]
    const layout = layoutHarrisMatrix(nenthorn(), dates)
    const nodes = byId(layout)

    expect(nodes.get('ff_j')!.dateCount).toBe(2)
    expect(nodes.get('ff_k')!.dateCount).toBe(1)
    expect(nodes.get('ff_a')!.dateCount).toBe(0)
    expect(layout.unassignedCount).toBe(0)
  })

  it('reports a date pointing at nothing rather than dropping it', () => {
    const layout = layoutHarrisMatrix(nenthorn(), [date('d1', 'ff_j'), date('d2', 'gone')])

    expect(layout.unassignedCount).toBe(1)
  })

  it('prefers an explicit assignment over the date’s own element_id', () => {
    // The draft store keeps assignments separately while a model is being
    // built, and they win -- otherwise moving a date between phases in the
    // editor would not move it in the diagram.
    const layout = layoutHarrisMatrix(nenthorn(), [date('d1', 'ff_j')], { d1: 'ff_a' })
    const nodes = byId(layout)

    expect(nodes.get('ff_j')!.dateCount).toBe(0)
    expect(nodes.get('ff_a')!.dateCount).toBe(1)
  })
})

describe('what a node is, as distinct from how it is drawn', () => {
  // `kind` answers "what shape", and deliberately merges things: a Sequence
  // and a Phase holding other elements are both boxes. The diagram colours
  // a Phase differently from a Sequence, so it needs the distinction back.
  it('keeps the element type a grouping Phase shares with a Sequence', () => {
    const nodes = byId(layoutHarrisMatrix(nenthorn(), []))

    expect(nodes.get('fires')!.kind).toBe('group')
    expect(nodes.get('rock')!.kind).toBe('group')
    expect(nodes.get('fires')!.elementType).toBe('phase')
    expect(nodes.get('rock')!.elementType).toBe('sequence')
  })

  it('reports a context as the Phase it is', () => {
    const nodes = byId(layoutHarrisMatrix(nenthorn(), [date('d1', 'ff_k')]))

    expect(nodes.get('ff_k')!.kind).toBe('context')
    expect(nodes.get('ff_k')!.elementType).toBe('phase')
  })

  it('reports a boundary as itself', () => {
    const nodes = byId(layoutHarrisMatrix(nenthorn(), []))

    expect(nodes.get('b_start')!.elementType).toBe('boundary')
  })
})

describe('degenerate structures', () => {
  it('returns nothing for an absent tree, but still counts the dates', () => {
    const layout = layoutHarrisMatrix(null, [date('d1', 'x')])

    expect(layout.nodes).toEqual([])
    expect(layout.edges).toEqual([])
    expect(layout.unassignedCount).toBe(1)
  })

  it('drops an empty branch rather than reserving space for it', () => {
    const withEmpty = el('s', 'sequence', 'S', [
      el('a', 'phase', 'A'),
      el('empty', 'sequence', 'Empty', []),
      el('b', 'phase', 'B'),
    ])
    const layout = layoutHarrisMatrix(withEmpty, [])

    expect(byId(layout).has('empty')).toBe(false)
    // A and B are still adjacent, so the chain is not broken by the removal.
    expect(layout.edges).toHaveLength(1)
    expect(layout.edges[0]).toMatchObject({ source: 'b', target: 'a' })
  })

  it('does not let a hiatus break the chain', () => {
    // A hiatus carries no parameter -- the gap is the two Boundaries around
    // it -- so it is not drawn, and must not leave a hole in the ordering.
    const withHiatus = el('s', 'sequence', 'S', [
      el('a', 'phase', 'A'),
      el('h', 'hiatus', 'Gap'),
      el('b', 'phase', 'B'),
    ])
    const layout = layoutHarrisMatrix(withHiatus, [])

    expect(byId(layout).has('h')).toBe(false)
    expect(layout.edges).toHaveLength(1)
  })

  it('gives a lone phase a plain context box', () => {
    const layout = layoutHarrisMatrix(el('only', 'phase', 'Only'), [])

    expect(layout.nodes).toHaveLength(1)
    expect(layout.nodes[0]).toMatchObject({
      kind: 'context', width: CONTEXT_WIDTH, height: CONTEXT_HEIGHT, parentId: null,
    })
  })
})

describe('summariseTree', () => {
  it('counts what the model asserts, not what the diagram draws', () => {
    // Nenthorn: 3 phases (Fire_Feature_J/K/A) plus the grouping phase Fires,
    // 2 boundaries, and 3 adjacent pairs in the root sequence.
    expect(summariseTree(nenthorn())).toEqual({
      phaseCount: 4,
      orderingCount: 3,
      boundaryCount: 2,
    })
  })

  it('no longer reports the cross-product', () => {
    // The old treeToMatrix reported 175 relationships for this model.
    const summary = summariseTree(siteModel(8, 4))

    expect(summary.phaseCount).toBe(8 + 8 * 4)
    expect(summary.orderingCount).toBe(9)
  })

  it('is empty for no tree', () => {
    expect(summariseTree(null)).toEqual({
      phaseCount: 0, orderingCount: 0, boundaryCount: 0,
    })
  })
})
