// Laying a model tree out as a Harris matrix.
//
// The source is a TREE, not an arbitrary DAG, so this is recursive
// box-packing rather than graph layout: no crossing minimisation, no layering
// heuristics, nothing to converge. A Sequence stacks its children, a Phase
// places them side by side, containment is a box rather than an edge, and a
// Sequence's ordering is total -- so its transitive reduction is exactly the
// consecutive chain, and connecting only adjacent children IS the reduction.
// (Harris's own drawing rules require redundant relationships to be left out.)
//
// Everything here is pure: a tree in, geometry out, no React and no reactflow
// types. reactflow draws into a measured viewport, so "the boxes line up"
// cannot be checked in the DOM -- it has to be asserted on the numbers, which
// is what utils/harrisLayout.test.ts does.
import type { ElementType, MatrixMeasurement, ModelElement, PosteriorSummary } from '@/types'
import { estimateTextWidth } from '@/utils/textWidth'

// Box metrics: a context node fits a lab code, a date count and a TPQ/TAQ
// badge line without wrapping at the default zoom.
export const CONTEXT_WIDTH = 172
export const CONTEXT_HEIGHT = 62
// Gap between siblings placed side by side inside a Phase.
export const SIBLING_GAP = 24
// Gap between a Sequence's stacked children, wide enough for an arrow and its
// head to read as a connector rather than a seam.
export const STACK_GAP = 52
// Inset from a container's edge to its contents.
export const PADDING = 16
// Extra space at the top of a container for its own name.
export const HEADER = 26
// A Boundary is drawn as a rule spanning its parent, not as a box.
export const RULE_HEIGHT = 30
// The shortest piece of rule either side of a boundary's label that still
// reads as a rule, and the gap between rule and text. These make the label
// part of the geometry rather than something painted over it: the label rides
// ON the rule, which spans its parent, so a container narrower than its own
// boundary label would push the text out past the box.
export const RULE_STUB = 24
export const RULE_LABEL_GAP = 8
// The size BoundaryNode draws the label at. Declared here, and imported by
// the node, because the width it implies is decided here -- two copies of the
// number would let the drawing outgrow the box measured for it.
export const LABEL_FONT_SIZE = 10

export type LaidOutKind = 'context' | 'group' | 'boundary'

export interface LaidOutNode {
  id: string
  kind: LaidOutKind
  /**
   * The element type this was laid out from, which `kind` deliberately
   * loses: a Phase becomes a 'context' when it holds dates and a 'group'
   * when it holds other elements, and a Sequence is always a 'group'. Kind
   * is what to DRAW; this is what the thing IS, which is what the element
   * type colours key off.
   */
  elementType: ElementType
  name: string
  /** Containing element, or null at the root. Children are positioned
   *  RELATIVE to this, which is what reactflow's sub-flow nodes expect. */
  parentId: string | null
  x: number
  y: number
  width: number
  height: number
  /** Nesting depth, for shading containers progressively. */
  depth: number
  /** Dates assigned to this element. Only ever non-empty on a context. */
  dateCount: number
  tpqAgeBp: number | null
  taqAgeBp: number | null
  /** Set on a boundary once the analysis has run. */
  posterior: PosteriorSummary | null
  /**
   * The modelled range printed beside a boundary's name, already formatted at
   * the page's date format and confidence -- see `boundaryRanges` below. Null
   * on everything else, and on a boundary whose analysis has not run.
   *
   * Carried here rather than formatted in the node because its WIDTH sizes
   * the box that holds it, and formatting it in both places would let the
   * measured string and the drawn one drift apart.
   */
  rangeLabel: string | null
}

export interface LaidOutEdge {
  id: string
  /** The younger element -- drawn from the box higher up the screen. */
  source: string
  /** The older element. */
  target: string
}

export interface HarrisLayout {
  nodes: LaidOutNode[]
  edges: LaidOutEdge[]
  width: number
  height: number
  /** Dates whose element_id matches nothing in the tree. Surfaced rather
   *  than dropped: a date missing from the diagram is otherwise invisible. */
  unassignedCount: number
}

// --------------------------------------------------------------------------
// Pass 1 -- measure
// --------------------------------------------------------------------------

interface Measured {
  element: ModelElement
  /** How this element will be drawn, or 'skip' when it contributes nothing. */
  kind: LaidOutKind | 'skip'
  width: number
  height: number
  children: Measured[]
  dateCount: number
  /**
   * The narrowest the CONTAINER may be for this child to be drawn inside it,
   * as distinct from `width`, which is how much room the child takes up in
   * the packing. Only a boundary sets it: a boundary takes no room in the
   * packing at all (it is a rule across the parent, not a sibling beside
   * one), and yet it is the child most able to make the parent too narrow.
   */
  minSpan: number
  rangeLabel: string | null
}

/**
 * Does this subtree contain anything worth drawing? An empty Phase would
 * otherwise occupy space and pull ordering edges towards nothing. Boundaries
 * count as drawable: they are modelled transitions with their own posteriors.
 */
function isDrawable(measured: Measured): boolean {
  if (measured.kind === 'boundary') return true
  if (measured.kind === 'context') return true
  return measured.children.some(isDrawable)
}

/**
 * How wide the box holding this boundary has to be, label included.
 *
 * The label sits on the rule with a piece of rule either side, and
 * BoundaryNode stacks the name over the range when they do not fit on one
 * line -- so what has to fit is the wider LINE, not the whole label end to
 * end. Measuring the whole would widen a three-phase sequence by half again.
 */
function boundarySpan(name: string, rangeLabel: string | null): number {
  const widestLine = Math.max(
    estimateTextWidth(name, LABEL_FONT_SIZE, true),
    rangeLabel ? estimateTextWidth(rangeLabel, LABEL_FONT_SIZE) : 0
  )
  return widestLine + (RULE_STUB + RULE_LABEL_GAP) * 2
}

function measure(
  element: ModelElement,
  datesByElement: Map<string, MatrixMeasurement[]>,
  boundaryRanges: Record<string, string>
): Measured {
  const type = element.element_type
  const dateCount = (datesByElement.get(element.id) || []).length

  if (type === 'boundary') {
    // Width is 0 here and resolved during placement: a rule spans whatever
    // its parent turns out to be, so it cannot contribute to that width
    // without defining itself circularly. `minSpan` is the part statable up
    // front -- the floor the parent must clear for the label to fit.
    const rangeLabel = boundaryRanges[element.id] ?? null
    return {
      element,
      kind: 'boundary',
      width: 0,
      height: RULE_HEIGHT,
      children: [],
      dateCount: 0,
      minSpan: boundarySpan(element.name || element.element_type, rangeLabel),
      rangeLabel,
    }
  }
  if (type === 'hiatus') {
    // A hiatus carries no parameter of its own: it marks the two Boundaries
    // around it as a gap rather than one shared transition, which the gap
    // between them already shows.
    return {
      element, kind: 'skip', width: 0, height: 0, children: [], dateCount: 0,
      minSpan: 0, rangeLabel: null,
    }
  }

  const children = (element.children || []).map((child) =>
    measure(child, datesByElement, boundaryRanges))
  const drawable = children.filter(isDrawable)
  // A container is at least as wide as the widest rule it carries. Only its
  // OWN children are consulted: a boundary further down is already inside a
  // container that widened for it.
  const ruleFloor = Math.max(0, ...drawable.map((c) => c.minSpan))

  // A Phase holding dates and no drawable children is a context, the thing a
  // Harris matrix is made of. One holding other Phases is a grouping --
  // "Fires" contains the fire features rather than being one -- and is drawn
  // as a box around them.
  if (type === 'phase' && drawable.length === 0) {
    return {
      element, kind: 'context', width: CONTEXT_WIDTH, height: CONTEXT_HEIGHT, children: [],
      dateCount, minSpan: 0, rangeLabel: null,
    }
  }

  if (type === 'phase') {
    // Unordered: side by side. Nothing about a Phase says which of its
    // children came first, so they get equal standing on one row.
    const inner = drawable.reduce((sum, c) => sum + c.width, 0)
      + SIBLING_GAP * Math.max(0, drawable.length - 1)
    const tallest = Math.max(...drawable.map((c) => c.height))
    return {
      element,
      kind: 'group',
      width: Math.max(inner, CONTEXT_WIDTH, ruleFloor) + PADDING * 2,
      height: tallest + PADDING * 2 + HEADER,
      children: drawable,
      dateCount,
      minSpan: 0,
      rangeLabel: null,
    }
  }

  // A Sequence stacks its children, oldest at the bottom.
  const inner = drawable.reduce((sum, c) => sum + c.height, 0)
    + STACK_GAP * Math.max(0, drawable.length - 1)
  const widest = drawable.length ? Math.max(...drawable.map((c) => c.width)) : CONTEXT_WIDTH
  return {
    element,
    kind: 'group',
    width: Math.max(widest, ruleFloor) + PADDING * 2,
    height: inner + PADDING * 2 + HEADER,
    children: drawable,
    dateCount,
    minSpan: 0,
    rangeLabel: null,
  }
}

// --------------------------------------------------------------------------
// Pass 2 -- place
// --------------------------------------------------------------------------

/**
 * Walk the measured tree assigning positions, and emit the ordering edges.
 *
 * Positions are relative to the containing element, as reactflow treats a
 * node with a `parentNode`. Absolute coordinates are never needed: every
 * containment in the output is a real parent/child pair.
 */
function place(
  measured: Measured,
  parentId: string | null,
  x: number,
  y: number,
  depth: number,
  width: number,
  nodes: LaidOutNode[],
  edges: LaidOutEdge[]
): void {
  const { element } = measured

  nodes.push({
    id: element.id,
    kind: measured.kind === 'skip' ? 'group' : measured.kind,
    elementType: element.element_type,
    name: element.name || element.element_type,
    parentId,
    x,
    y,
    width,
    height: measured.height,
    depth,
    dateCount: measured.dateCount,
    tpqAgeBp: element.tpq_age_bp ?? null,
    taqAgeBp: element.taq_age_bp ?? null,
    posterior: element.posterior ?? null,
    rangeLabel: measured.rangeLabel,
  })

  if (!measured.children.length) return

  const contentWidth = width - PADDING * 2
  const isSequence = element.element_type === 'sequence'

  if (!isSequence) {
    // Side by side, centred as a row, each child vertically centred.
    const rowWidth = measured.children.reduce((sum, c) => sum + c.width, 0)
      + SIBLING_GAP * Math.max(0, measured.children.length - 1)
    let cursor = PADDING + (contentWidth - rowWidth) / 2
    const tallest = Math.max(...measured.children.map((c) => c.height))
    for (const child of measured.children) {
      const childWidth = child.kind === 'boundary' ? contentWidth : child.width
      place(
        child, element.id, cursor,
        PADDING + HEADER + (tallest - child.height) / 2,
        depth + 1, childWidth, nodes, edges
      )
      cursor += child.width + SIBLING_GAP
    }
    return
  }

  // A Sequence's children run oldest first and a Harris matrix reads oldest
  // at the BOTTOM, so the first child gets the largest y and the stack builds
  // upwards. Centring each on the parent's content width puts a nested group
  // directly beneath the one it follows.
  const placements: { child: Measured; y: number }[] = []
  let cursor = measured.height - PADDING
  for (const child of measured.children) {
    cursor -= child.height
    placements.push({ child, y: cursor })
    cursor -= STACK_GAP
  }

  for (const { child, y: childY } of placements) {
    const childWidth = child.kind === 'boundary' ? contentWidth : child.width
    const childX = child.kind === 'boundary'
      ? PADDING
      : PADDING + (contentWidth - child.width) / 2
    place(child, element.id, childX, childY, depth + 1, childWidth, nodes, edges)
  }

  // A Sequence is a total order, so its transitive reduction is exactly the
  // chain between adjacent children: connecting only these IS the reduction.
  // The edge joins the two CONTAINERS -- expanding it to the phases nested
  // inside them turns 39 orderings into 175 edges.
  //
  // A pair separated by a Boundary gets no arrow: the Boundary is already a
  // labelled rule spanning the width between them, and says everything the
  // arrow would with the transition's own date on it. Without a Boundary the
  // arrow is the only thing carrying the order.
  for (let i = 0; i < measured.children.length - 1; i++) {
    const older = measured.children[i]
    const younger = measured.children[i + 1]
    if (older.kind === 'boundary' || younger.kind === 'boundary') continue
    edges.push({
      id: `order_${older.element.id}_${younger.element.id}`,
      source: younger.element.id,
      target: older.element.id,
    })
  }
}

// --------------------------------------------------------------------------
// Entry point
// --------------------------------------------------------------------------

/**
 * Lay a model tree out as a Harris matrix.
 *
 * `assignments` maps a date id to the element it sits in, falling back to the
 * date's own element_id/group_id -- the same resolution order flattenStructure
 * uses, so the diagram and the results table place a date identically.
 *
 * `boundaryRanges` maps a boundary's element id to the modelled range printed
 * beside its name, already formatted. Supplied rather than derived because
 * the formatting depends on the page's date format and confidence,
 * neither of which is geometry -- and it is supplied HERE, rather than left
 * to the node that draws it, because the label's width decides how wide its
 * container has to be. Leave it out and the boxes are sized for the names
 * alone, which is right for a model that has not been run yet.
 */
export function layoutHarrisMatrix(
  tree: ModelElement | null | undefined,
  measurements: MatrixMeasurement[],
  assignments: Record<string, string> = {},
  boundaryRanges: Record<string, string> = {}
): HarrisLayout {
  if (!tree) {
    return { nodes: [], edges: [], width: 0, height: 0, unassignedCount: measurements.length }
  }

  const datesByElement = new Map<string, MatrixMeasurement[]>()
  for (const m of measurements) {
    const key = assignments[m.id] || m.element_id || m.group_id
    if (!key) continue
    if (!datesByElement.has(key)) datesByElement.set(key, [])
    datesByElement.get(key)!.push(m)
  }

  const measured = measure(tree, datesByElement, boundaryRanges)
  const nodes: LaidOutNode[] = []
  const edges: LaidOutEdge[] = []
  place(measured, null, 0, 0, 0, measured.width, nodes, edges)

  const drawn = new Set(nodes.map((n) => n.id))
  const unassignedCount = measurements.filter((m) => {
    const key = assignments[m.id] || m.element_id || m.group_id
    return !key || !drawn.has(key)
  }).length

  return { nodes, edges, width: measured.width, height: measured.height, unassignedCount }
}

/**
 * Counts for the "N phases / N ordering relationships" summary chips.
 *
 * `orderingCount` counts what the MODEL asserts -- one per adjacent pair of
 * drawable children in every Sequence, Boundaries included -- rather than the
 * cross-product edges, which would report 175 for a model asserting 39. That is
 * deliberately not the same as the number of arrows the diagram draws, which
 * omits pairs a Boundary rule already separates -- the model constrains those
 * pairs whether or not an arrow is the thing showing it.
 */
export function summariseTree(tree: ModelElement | null | undefined): {
  phaseCount: number
  orderingCount: number
  boundaryCount: number
} {
  if (!tree) return { phaseCount: 0, orderingCount: 0, boundaryCount: 0 }

  let phaseCount = 0
  let boundaryCount = 0
  let orderingCount = 0

  const walk = (element: ModelElement) => {
    if (element.element_type === 'phase') phaseCount += 1
    if (element.element_type === 'boundary') boundaryCount += 1
    if (element.element_type === 'sequence') {
      const drawable = (element.children || []).filter(
        (c) => c.element_type !== 'hiatus'
      )
      orderingCount += Math.max(0, drawable.length - 1)
    }
    for (const child of element.children || []) walk(child)
  }
  walk(tree)

  return { phaseCount, orderingCount, boundaryCount }
}
