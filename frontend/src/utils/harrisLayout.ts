// Laying a model tree out as a Harris matrix.
//
// This replaces the previous approach, which projected the tree onto a flat
// (nodes, edges) graph and then tried to recover a layout from the edges
// alone. That lost the one thing the source actually has -- its nesting --
// and all three of the diagram's problems followed from it:
//
//   * A Sequence connected every phase in one child to every phase in the
//     next, so an eight-group model with four sub-phases each drew 175 edges
//     where 39 carry the same information. Harris's own drawing rules require
//     redundant relationships to be left out, so that was not a matter of
//     taste.
//   * A parent Phase and the Phases nested inside it came out as unrelated
//     sibling nodes, because containment was expressed by no edge at all. On
//     the Nenthorn model "Fires" was drawn beside "Fire_Feature_K" and
//     "Fire_Feature_A" -- the two phases it contains.
//   * Horizontal position was the node's index within its layer, so nothing
//     lined up with anything.
//
// The way out is that the source is a TREE, not an arbitrary DAG, so this
// needs recursive box-packing rather than graph layout -- no crossing
// minimisation, no layering heuristics, nothing to converge. A Sequence
// stacks its children; a Phase places its children side by side; containment
// is a box rather than an edge; and a Sequence's ordering is a total order,
// whose transitive reduction is just the consecutive chain. Connecting only
// adjacent children is therefore the reduction, not an approximation of it.
//
// Everything here is pure: a tree in, geometry out, no React and no reactflow
// types. That is deliberate. reactflow draws into a measured viewport, so
// "the boxes line up" cannot be checked by looking at the DOM -- it has to be
// asserted on the numbers, which is what utils/harrisLayout.test.ts does.
import type { ElementType, MatrixMeasurement, ModelElement, PosteriorSummary } from '@/types'
import { estimateTextWidth } from '@/utils/textWidth'

// Box metrics. Chosen so a context node fits a lab code, a date count and a
// TPQ/TAQ badge line without wrapping at the diagram's default zoom.
export const CONTEXT_WIDTH = 172
export const CONTEXT_HEIGHT = 62
// Gap between siblings placed side by side inside a Phase.
export const SIBLING_GAP = 24
// Gap between the stacked children of a Sequence. Wide enough for an arrow
// plus its head to read as a connector rather than a seam.
export const STACK_GAP = 52
// Inset from a container's edge to its contents.
export const PADDING = 16
// Extra space at the top of a container for its own name.
export const HEADER = 26
// A Boundary is drawn as a rule spanning its parent, not as a box.
export const RULE_HEIGHT = 30
// The shortest piece of rule either side of a boundary's label that still
// reads as a rule, and the gap the node leaves between rule and text.
//
// These are what make the label part of the geometry rather than something
// painted over it. The label rides ON the rule, and the rule spans its
// parent, so a container narrower than its own boundary label pushed the text
// out past the box -- on the Nenthorn J > A > K model, a 172px box carrying a
// 219px label, with the red overhanging the container on every rule.
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
   * It is carried here rather than formatted in the node because its WIDTH is
   * what sizes the box that holds it. Formatting it in both places would let
   * the string the layout measured and the string the node drew drift apart.
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
 * Does this subtree contain anything worth drawing?
 *
 * An empty Phase, or a Sequence holding only empty Phases, would otherwise
 * occupy space and pull ordering edges towards nothing. Boundaries count as
 * drawable in their own right -- they are modelled transitions with their own
 * posteriors, not decoration.
 */
function isDrawable(measured: Measured): boolean {
  if (measured.kind === 'boundary') return true
  if (measured.kind === 'context') return true
  return measured.children.some(isDrawable)
}

/**
 * How wide the box holding this boundary has to be, label included.
 *
 * The label sits on the rule with a piece of rule either side of it, and the
 * node stacks the name over the range when the two do not fit on one line
 * (see BoundaryNode) -- so what has to fit is the wider LINE, not the whole
 * label laid end to end. Measuring the whole label instead would widen a
 * three-phase sequence by half again to buy a line break the drawing does
 * not need.
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
    // without defining itself circularly. `minSpan` is the part that CAN be
    // stated up front -- the floor the parent has to clear for the label to
    // fit on the rule.
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
    // A hiatus carries no parameter of its own -- it marks that the two
    // Boundaries around it are a gap rather than one shared transition. The
    // gap between them is already visible, so drawing a third thing between
    // them would only add clutter.
    return {
      element, kind: 'skip', width: 0, height: 0, children: [], dateCount: 0,
      minSpan: 0, rangeLabel: null,
    }
  }

  const children = (element.children || []).map((child) =>
    measure(child, datesByElement, boundaryRanges))
  const drawable = children.filter(isDrawable)
  // A container is at least as wide as the widest rule it has to carry. Only
  // its OWN children are consulted: a boundary further down is already inside
  // a container that widened for it, and that container's width is counted
  // here as an ordinary child width.
  const ruleFloor = Math.max(0, ...drawable.map((c) => c.minSpan))

  // A Phase holding dates and no drawable children is a context: the thing a
  // Harris matrix is actually made of. A Phase holding other Phases is a
  // grouping -- "Fires" contains the fire features, it is not one -- and is
  // drawn as a box around them.
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
 * Positions are relative to the containing element, matching how reactflow
 * treats a node with a `parentNode`. Absolute coordinates are never needed:
 * every containment relationship in the output is a real parent/child pair.
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

  // A Sequence's children run oldest first, and a Harris matrix reads oldest
  // at the BOTTOM -- so the first child gets the largest y and the stack is
  // built upwards. Each child is centred on the parent's content width, which
  // is what puts a nested group directly beneath the one it follows instead
  // of wherever its index happened to fall.
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

  // The ordering. A Sequence is a total order, so its transitive reduction is
  // exactly the chain between adjacent children -- connecting only these is
  // the reduction rather than a simplification of it. The edge joins the two
  // CONTAINERS; expanding it to the phases nested inside them is what
  // produced 175 edges for a model that has 39.
  //
  // A pair separated by a Boundary gets no arrow. The Boundary is already
  // drawn as a labelled rule spanning the full width between them, so it says
  // everything the arrow would and says it with the transition's own date on
  // it. Adding the arrow as well is the same redundant ink this rewrite is
  // about. Where there is no Boundary, the arrow is the only thing carrying
  // the order, so it has to be there.
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
 * beside its name, already formatted. It is supplied rather than derived
 * because the formatting depends on the page's date format and confidence,
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
 * Replaces treeToMatrix in that role, and reports something different because
 * what treeToMatrix reported was wrong: it counted the cross-product edges, so
 * a model asserting 39 orderings displayed 175 -- the chip was quoting the
 * drawing bug back at the user.
 *
 * `orderingCount` counts what the MODEL asserts: one per adjacent pair of
 * drawable children in every Sequence, Boundaries included. That is
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
