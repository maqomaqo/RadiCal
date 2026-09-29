import {
  createContext,
  lazy,
  memo,
  startTransition,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  Box,
  Paper,
  Typography,
  Button,
  IconButton,
  MenuItem,
  Select,
  TextField,
  Tooltip,
  Chip,
  Alert,
  Menu,
  Stack,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Divider,
  ListSubheader,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward'
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward'
import AutoFixHighIcon from '@mui/icons-material/AutoFixHigh'
import DragIndicatorIcon from '@mui/icons-material/DragIndicator'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown'
import UndoIcon from '@mui/icons-material/Undo'
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragMoveEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
} from '@dnd-kit/sortable'
import { useElementTypeColors, type ElementTypeColor } from '@/theme/diagramColors'
import type { ModelElement, ElementType, ElementParams, MatrixMeasurement } from '@/types'
import SortDatesButton from '@/components/SortDatesButton'
import ConfirmDialog from '@/components/ConfirmDialog'
import { reorderToProposal } from '@/utils/spatialOrder'
import { groupingPreview, siteModelTree } from '@/utils/siteGrouping'
import type { SiteGroupingKey } from '@/utils/siteGrouping'
import GuideBand from '@/components/GuideBand'
import type { MeasurementSortKey } from '@/utils/sortMeasurements'
// Lives in utils because the starting structure built there (see
// bootstrapModelTree) mints ids too, and both must mint the same kind.
// Re-exported so this module stays the one place the editor is imported from.
import {
  depositionSequenceTree,
  newElementId,
  wiggleMatchTree,
} from '@/utils/modelTreeDefaults'
import { shortSeriesWarning } from '@/utils/ringOffsets'
import RingNumberDialog from '@/components/RingNumberDialog'

export { newElementId }

/**
 * What a deletion costs, in a sentence. Both halves are counted from the tree
 * rather than estimated, because the number IS the reason this dialog is worth
 * interrupting for -- a vague warning gets dismissed unread.
 *
 * These sentences used to end "It cannot be undone." That was true when written
 * and stopped being true the moment Undo landed; a dialog that overstates the
 * stakes to win a click is worse than no dialog, because the next warning gets
 * believed less. The dialog still earns its place on the COUNT -- a delete can
 * take a subtree and a dozen date assignments with it, and Undo is only useful
 * to someone who noticed.
 */
function describeRemoval({
  descendants,
  orphaned,
}: {
  descendants: number
  orphaned: number
}): string {
  const nested = descendants > 0 ? `${descendants} nested element${descendants === 1 ? '' : 's'}` : ''
  const dates = orphaned > 0 ? `${orphaned} date${orphaned === 1 ? '' : 's'}` : ''
  if (nested && dates) {
    return `This also removes ${nested} and leaves ${dates} unassigned. Undo restores it.`
  }
  if (nested) return `This also removes ${nested}. Undo restores it.`
  return `This leaves ${dates} unassigned. Undo restores it.`
}

// Editor for the model structure: a nested, ordered tree of
// Sequences, Phases, Boundaries and Hiatuses, rendered as an indented outline, oldest first -- top of the list is
// bottom of the stratigraphy, the convention OxCal and the Harris matrix use.
//
// A Boundary here is placed deliberately and, between two Phases, is a SINGLE
// shared transition rather than two independent per-context boundaries.
//
// Rows are dragged with @dnd-kit over a FLATTENED, depth-tagged list in the
// outline's own DFS order, so "move this element to a different nesting level"
// is an index+depth projection on a flat array rather than juggling parent
// pointers. Same technique as dnd-kit's sortable-tree example.
//
// The rows DO NOT move during a drag: the sortable strategy is a no-op, no
// transform is applied, and a DropIndicator line shows where the block will
// land. Re-ordering the list to preview the drop in place fights dnd-kit's own
// shifting animation for the same rows -- visibly jitter, and rows changing
// height mid-drag invalidate the rects measured for collision, so a drop can
// land on a stale target or not register. A static list also suits a TREE
// better: a gap between two rows cannot express which surrounding level the
// block would land at, where an indented line can.

const TYPE_LABEL: Record<ElementType, string> = {
  sequence: 'Sequence',
  phase: 'Phase',
  boundary: 'Boundary',
  hiatus: 'Hiatus',
  wiggle_sequence: 'Wiggle match',
  deposition_sequence: 'Deposition sequence',
  interpolated_depth: 'Undated level',
}

const TYPE_HINT: Record<ElementType, string> = {
  sequence: 'Children are in chronological order, oldest at the top',
  phase: 'Children and contents are unordered. Contains dates',
  boundary: 'A transition; one shared parameter between phases',
  hiatus: 'A deliberate gap; must sit between two boundaries',
  wiggle_sequence: 'Dates separated by counted rings or varves. Contains dates',
  deposition_sequence: 'Dates separated by depth, with deposition modelled along it',
  interpolated_depth: 'A depth with no date, to report an age at',
}

// The vocabulary in the key and the add menu. The first four are the ordinary
// model; the last two are the sequences whose members are separated by a
// MEASURED distance, and are offered second because most models are not one.
const OFFERED_TYPES: ElementType[] = [
  'sequence',
  'boundary',
  'phase',
  'hiatus',
  'wiggle_sequence',
  'deposition_sequence',
]

// Types a date can be assigned to. Mirrors model_tree.DATE_HOLDERS: a date is
// a measurement of one event, and these are the elements that say where that
// event sits.
function holdsDates(type: ElementType): boolean {
  return type === 'phase' || type === 'wiggle_sequence' || type === 'deposition_sequence'
}

// What a newly added element of each type starts with. Only the
// ordinate-bearing ones have settings at all; the backend fills in anything a
// stored element does not carry (model_tree.ELEMENT_PARAMS), so these are the
// starting VALUES rather than a schema.
const DEFAULT_PARAMS: Partial<Record<ElementType, ElementParams>> = {
  wiggle_sequence: { gap_units: 'rings', reference_sample_id: null },
  // k inferred rather than fixed: a rigidity chosen badly dominates the
  // answer, so the default is to let the data pick it over two decades either
  // side of one event per centimetre.
  deposition_sequence: {
    k: null,
    k_variable: true,
    log_k_min: -2,
    log_k_max: 2,
    depth_units: 'cm',
  },
  interpolated_depth: { depth_cm: null },
}

// One MUI spacing unit (8px) times 3 -- the indent step every depth level
// renders at.
const INDENT_UNITS = 3
export const INDENT_PX = INDENT_UNITS * 8
// Where a level's guide sits inside its indent step. Half a step centres the
// line in the space its children are pushed across, so the guide reads as the
// container's edge rather than as an underline on the row above.
const GUTTER_INSET = INDENT_PX / 2

// A 44px target on a 20px glyph, pulled back into the row's own padding with a
// negative margin so the row does not grow to fit it -- the pattern Results.tsx
// already uses for its disclosure chevron.
// flexShrink:0 is load-bearing, not defensive: every one of these sits in a
// flex row, and a flex child shrinks to its content by default -- which quietly
// took the drag handle back down to its 20px glyph while still reporting a
// 44px height.
const HIT_TARGET = { width: 44, height: 44, minWidth: 44, flexShrink: 0, my: '-10px' } as const

// How many problems the alert lists before summarising the rest.
const SHOWN_PROBLEMS = 6

// How many rows the outline mounts before yielding, and how many more it
// adds per step after that -- see renderLimit.
const FIRST_PAINT_ROWS = 40
const MOUNT_CHUNK_ROWS = 60

// One shared empty array, so rows with no dates are handed the same reference
// every render rather than a fresh literal that defeats memoisation downstream.
const EMPTY_DATES: MatrixMeasurement[] = []

// Width of the type-word column. "Sequence" is the widest of the four at
// 0.75rem/500 (53px measured); 56 clears it without padding the short ones out
// to a visible gap.
const TYPE_SLOT_PX = 56

// Whether other ELEMENTS can be nested inside. A wiggle match and a
// deposition sequence hold dates but no elements -- their members are
// separated by a measured gap, so there is no position in either for a phase
// or a boundary. Only the deposition sequence takes a child at all, and only
// the undated level, which is added from its own control rather than by
// nesting.
function isContainerType(type: ElementType): boolean {
  return type === 'sequence' || type === 'phase'
}

/**
 * Whether the add button appears on this row, and what it may add.
 *
 * A deposition sequence is not a container in the nesting sense -- nothing can
 * be dragged into it -- but it does take one kind of child: an undated level,
 * a depth with no measurement to report an age at. That is added from its own
 * row because it belongs to that sequence's depth axis and nowhere else.
 */
function acceptsChildren(type: ElementType): boolean {
  return isContainerType(type) || type === 'deposition_sequence'
}

function childTypesFor(type: ElementType): ElementType[] {
  return type === 'deposition_sequence' ? ['interpolated_depth'] : OFFERED_TYPES
}

/** Depth-first walk, yielding [element, parent]. */
function walk(
  element: ModelElement,
  parent: ModelElement | null,
  visit: (el: ModelElement, parent: ModelElement | null) => void
) {
  visit(element, parent)
  for (const child of element.children || []) walk(child, element, visit)
}

function cloneTree(root: ModelElement): ModelElement {
  return JSON.parse(JSON.stringify(root))
}

/** Every element in the tree, by id. */
function indexTree(root: ModelElement): Map<string, ModelElement> {
  const byId = new Map<string, ModelElement>()
  walk(root, null, (el) => byId.set(el.id, el))
  return byId
}

/**
 * `next` with every element an edit did not touch swapped back for the
 * object it was before, so a memoised row holding it sees no change.
 *
 * The structural edits -- move, add, delete, drop, bracket -- all build their
 * result by copying, and a copy is a new object for every element whether
 * or not it changed. On an 800-date model that made a one-row move re-render
 * all 275 rows, at about 600ms a keypress. An element is taken back
 * unchanged when its own fields match and each child, after the same
 * treatment, is the very object it held before; the path from the root to
 * the edit is what remains new, which is exactly the set of rows that need
 * to redraw.
 */
function reuseUnchanged(next: ModelElement, before: Map<string, ModelElement>): ModelElement {
  const children = (next.children || []).map((child) => reuseUnchanged(child, before))
  const original = before.get(next.id)
  if (original) {
    const originalChildren = original.children || []
    const same =
      originalChildren.length === children.length &&
      children.every((child, i) => child === originalChildren[i]) &&
      original.name === next.name &&
      original.element_type === next.element_type &&
      original.tpq_age_bp === next.tpq_age_bp &&
      original.taq_age_bp === next.taq_age_bp &&
      (original.params === next.params ||
        JSON.stringify(original.params ?? null) === JSON.stringify(next.params ?? null))
    if (same) return original
  }
  return { ...next, children }
}

/**
 * The tree with ONE element changed, sharing everything else with the tree
 * it came from.
 *
 * cloneTree copies every element, which is right for an edit that touches
 * many of them but wrong for a keystroke in a name field: with every element
 * a new object, every row's memo fails and all 275 rows of an 800-date model
 * re-render per character. Copying only the path from the root to the edited
 * element leaves every other row holding the same element it held before, so
 * only the edited row redraws.
 *
 * `mutate` receives a shallow copy, so it may assign the copy's own fields
 * but must not reach into a nested object and change it in place -- that
 * object is still shared with the previous tree.
 */
function updateElement(
  root: ModelElement,
  id: string,
  mutate: (copy: ModelElement) => void
): ModelElement {
  const rec = (el: ModelElement): ModelElement => {
    if (el.id === id) {
      const copy = { ...el }
      mutate(copy)
      return copy
    }
    const kids = el.children || []
    for (let i = 0; i < kids.length; i++) {
      const next = rec(kids[i])
      if (next !== kids[i]) {
        const children = [...kids]
        children[i] = next
        return { ...el, children }
      }
    }
    return el
  }
  return rec(root)
}

/**
 * A callback whose identity never changes but which always calls the latest
 * version of `fn`.
 *
 * The row callbacks close over the current tree and assignments, so they are
 * new functions on every render -- and passed straight to a memoised row they
 * would defeat the memo on every one of them. Routed through a ref, the row
 * holds one function for its whole life and still reaches the current state.
 */
function useEvent<A extends unknown[], R>(fn: (...args: A) => R): (...args: A) => R {
  const ref = useRef(fn)
  ref.current = fn
  return useCallback((...args: A) => ref.current(...args), [])
}

/**
 * The same array as last time when nothing in it changed, so a consumer
 * memoised on it -- dnd-kit's SortableContext, which re-renders every
 * sortable row when its items array is a new object -- sees no change.
 */
function useStableArray<T>(next: T[]): T[] {
  const ref = useRef(next)
  const prev = ref.current
  if (prev.length !== next.length || prev.some((v, i) => v !== next[i])) ref.current = next
  return ref.current
}

/** dnd-kit's sorting strategy: none. See the SortableContext below. */
const NO_STRATEGY = () => null

const POINTER_SENSOR_OPTIONS = { activationConstraint: { distance: 6 } }
const KEYBOARD_SENSOR_OPTIONS = { coordinateGetter: sortableKeyboardCoordinates }

function findParent(root: ModelElement, id: string): ModelElement | null {
  let found: ModelElement | null = null
  walk(root, null, (el, parent) => {
    if (el.id === id) found = parent
  })
  return found
}

function findElement(root: ModelElement, id: string): ModelElement | null {
  let found: ModelElement | null = null
  walk(root, null, (el) => {
    if (el.id === id) found = el
  })
  return found
}

/**
 * Where a date can be assigned: a Phase, a wiggle match or a deposition
 * sequence. This is what the sample assignment dropdown offers.
 *
 * Each option carries the containers above it, because names are not unique
 * and nothing stops them colliding -- bootstrapModelTree mints "Phase 1" from
 * two different paths, and a bare list of names left two of them
 * indistinguishable at the moment of choosing between them. The TYPE is
 * carried too, since "Timber 1" says nothing about whether putting a date in
 * it also asserts a ring offset for it.
 */
export function phaseOptions(
  root: ModelElement
): Array<{ id: string; name: string; path: string; element_type: ElementType }> {
  const out: Array<{ id: string; name: string; path: string; element_type: ElementType }> = []
  const consider = (el: ModelElement, trail: string[]) => {
    const name = el.name || TYPE_LABEL[el.element_type]
    if (holdsDates(el.element_type)) {
      out.push({ id: el.id, name, path: trail.join(' › '), element_type: el.element_type })
    }
    for (const child of el.children || []) consider(child, [...trail, name])
  }
  // The root can itself be a wiggle match or a deposition sequence -- the one
  // a preset builds is exactly that -- so it is considered rather than only
  // walked through.
  consider(root, [])
  return out
}

/** Client-side mirror of the backend's validate_tree, so problems surface
 *  while editing rather than on submit. See services/model_tree.py. */
export function validateTree(
  root: ModelElement,
  assignments: Record<string, string>,
  measurements: MatrixMeasurement[]
): string[] {
  const problems: string[] = []
  const seen = new Set<string>()
  const phaseIds = new Set<string>()
  const datesIn = new Map<string, number>()
  for (const m of measurements) {
    const target = assignments[m.id]
    if (target) datesIn.set(target, (datesIn.get(target) || 0) + 1)
  }

  walk(root, null, (el, parent) => {
    if (seen.has(el.id)) problems.push(`Element "${el.name || el.id}" appears more than once`)
    seen.add(el.id)
    if (holdsDates(el.element_type)) phaseIds.add(el.id)
    const label = el.name || TYPE_LABEL[el.element_type]

    if (el.element_type === 'wiggle_sequence') {
      if ((el.children || []).length > 0) {
        problems.push(
          `Wiggle match "${label}" contains other elements — its members are separated by measured gaps, so there is no position in it for anything but dates`
        )
      }
      if (!datesIn.get(el.id)) {
        problems.push(`Wiggle match "${label}" has no dates in it`)
      }
      // The gaps ARE the model, so a member without one says no more than it
      // would on its own. Checked here as well as on the server, because this
      // is the screen the date was moved in on.
      const missing = measurements.filter(
        (m) => assignments[m.id] === el.id && m.ring_offset == null
      )
      if (missing.length > 0) {
        problems.push(
          `${missing.map((m) => `"${m.sample_name}"`).slice(0, 3).join(', ')} ${
            missing.length === 1 ? 'has' : 'have'
          } no ring offset, which wiggle match "${label}" needs`
        )
      }
      const offsets = measurements
        .filter((m) => assignments[m.id] === el.id && m.ring_offset != null)
        .map((m) => m.ring_offset as number)
      if (new Set(offsets).size !== offsets.length) {
        problems.push(
          `Wiggle match "${label}" has two dates at the same gap — two samples from one ring are one event measured twice`
        )
      }
      // A wiggle match cannot apply an outlier model: its gaps are asserted,
      // so there is no room for a date to sit apart from the rest. Refused
      // rather than dropped, on the server too — a member that does not
      // belong shows up in the agreement index instead.
      const flagged = measurements.filter(
        (m) => assignments[m.id] === el.id && (m.outlier_prior ?? 0) > 0
      )
      if (flagged.length > 0) {
        problems.push(
          `${flagged.map((m) => `"${m.sample_name}"`).slice(0, 3).join(', ')} ${
            flagged.length === 1 ? 'has' : 'have'
          } an outlier model, which wiggle match "${label}" cannot apply — its gaps are asserted, so a date that does not belong shows up in the agreement index instead`
        )
      }
    }

    if (el.element_type === 'deposition_sequence') {
      for (const child of el.children || []) {
        if (child.element_type !== 'interpolated_depth') {
          problems.push(
            `Deposition sequence "${label}" contains a ${TYPE_LABEL[child.element_type]} — it holds dates and undated levels only`
          )
        }
      }
      const depths = measurements
        .filter((m) => assignments[m.id] === el.id)
        .map((m) => m.depth_cm)
      if (depths.some((d) => d == null)) {
        problems.push(
          `Deposition sequence "${label}" has dates with no depth — depth is the axis the model runs along`
        )
      }
      const levelDepths = [
        ...depths.filter((d): d is number => d != null),
        ...(el.children || []).map((c) => c.params?.depth_cm).filter((d): d is number => d != null),
      ]
      if (new Set(levelDepths).size !== levelDepths.length) {
        problems.push(
          `Deposition sequence "${label}" has two levels at the same depth — a zero interval has no accumulation rate`
        )
      }
      if (levelDepths.length < 2) {
        problems.push(
          `Deposition sequence "${label}" needs at least two levels — a rate is a statement about the interval between two`
        )
      }
    }

    if (el.element_type === 'interpolated_depth' && el.params?.depth_cm == null) {
      problems.push(`Undated level "${label}" has no depth, so there is no age to report`)
    }

    if (parent?.element_type === 'phase' && el.element_type === 'boundary') {
      problems.push(
        `Boundary "${el.name || el.id}" sits directly inside phase "${parent.name}" — a phase is unordered, so put it in a sequence`
      )
    }
    if (el.element_type === 'sequence') {
      const kids = el.children || []
      kids.forEach((child, i) => {
        if (child.element_type !== 'hiatus') return
        const before = kids[i - 1]
        const after = kids[i + 1]
        if (before?.element_type !== 'boundary' || after?.element_type !== 'boundary') {
          problems.push(
            `Hiatus "${child.name || child.id}" must sit between two boundaries — it marks a gap between the end of one group and the start of the next`
          )
        }
      })
    }
    if (el.tpq_age_bp != null && el.taq_age_bp != null && el.taq_age_bp > el.tpq_age_bp) {
      problems.push(`"${el.name || el.id}" has TAQ greater than TPQ, so its valid range is empty`)
    }
  })

  for (const m of measurements) {
    const target = assignments[m.id]
    if (!target) {
      problems.push(`"${m.sample_name}" is not assigned to a phase or sequence`)
    } else if (!phaseIds.has(target)) {
      problems.push(`"${m.sample_name}" is assigned to an element that no longer exists`)
    }
  }
  return problems
}

/** A row in the flattened outline: every descendant of the root, in the
 * same order they'd be visited depth-first, tagged with its indent level. */
export interface FlatNode {
  id: string
  element: ModelElement
  depth: number
  /**
   * The containers this row sits inside, outermost first, ending with its
   * direct parent -- so `ancestors.length === depth` and `ancestors[i]` is the
   * container whose guide occupies indent level `i`.
   *
   * The outline is rendered as a FLAT list, so a row cannot inherit anything
   * from a parent element in the DOM; if it is to draw a guide for each level
   * it is nested under, it has to be told what those levels are.
   */
  ancestors: ModelElement[]
}

/** Flattens everything under (not including) the root into DFS order. Root
 * itself is depth 0, so its direct children start at depth 1. */
export function flattenTree(root: ModelElement): FlatNode[] {
  const out: FlatNode[] = []
  const rec = (el: ModelElement, depth: number, ancestors: ModelElement[]) => {
    for (const child of el.children || []) {
      out.push({ id: child.id, element: child, depth, ancestors })
      rec(child, depth + 1, [...ancestors, child])
    }
  }
  rec(root, 1, [root])
  return out
}

/**
 * Whether this container puts its children in order.
 *
 * The distinction the whole model turns on: a Sequence orders what it holds, a
 * Phase asserts no order between its members. It was carried in the editor by
 * a word and a hue alone, while the outline drew both as the same indented
 * stack -- and a vertical stack reads as an ordering claim whether or not one
 * is being made.
 */
function ordersChildren(el: ModelElement): boolean {
  return el.element_type === 'sequence'
}

/** Rebuilds a tree from a valid flat sequence (each depth at most one more
 * than the previous entry's), using a stack of open ancestors. */
function rebuildFromFlat(root: ModelElement, flat: FlatNode[]): ModelElement {
  const newRoot: ModelElement = { ...root, children: [] }
  const stack: ModelElement[] = [newRoot]
  for (const node of flat) {
    const parent = stack[node.depth - 1]
    if (!parent) continue
    const newNode: ModelElement = { ...node.element, children: [] }
    parent.children = parent.children || []
    parent.children.push(newNode)
    stack[node.depth] = newNode
    stack.length = node.depth + 1
  }
  return newRoot
}

export interface Projection {
  depth: number
  insertIndexInRest: number
  parentId: string
  rest: FlatNode[]
  /** The row the block would land immediately above, or null for the very
   * end of the outline. This is what the indicator line is drawn against. */
  insertBeforeId: string | null
  /** The depth STEP that produced this, projected minus original, fed back
   * into the next call so the chosen step is sticky (see DEPTH_HYSTERESIS). */
  depthDelta: number
}

// How far past the halfway point between two indent steps the pointer must
// travel before the projected depth changes. Plain rounding flips at exactly
// .5, so a hand held on that boundary would flicker between two levels.
const DEPTH_HYSTERESIS = 0.6

/**
 * Where the dragged block would land -- how deep, and just before which item --
 * from the flat outline, what is being hovered, and the horizontal distance.
 *
 * Depth is clamped so a drop can never nest inside a non-container and never
 * skip past what the surrounding items allow, making every candidate position
 * a legal tree with nothing to reject at drop time.
 *
 * `previousDepthDelta` is the step this same drag last settled on, if any.
 * Passing it keeps the indicator from flickering between two levels while
 * the pointer sits on the boundary between them.
 */
export function getProjection(
  flat: FlatNode[],
  activeId: string,
  overId: string,
  dragOffsetX: number,
  rootId: string,
  previousDepthDelta?: number | null
): Projection | null {
  const activeIndex = flat.findIndex((n) => n.id === activeId)
  if (activeIndex === -1) return null
  const activeNode = flat[activeIndex]

  let subtreeEnd = activeIndex + 1
  while (subtreeEnd < flat.length && flat[subtreeEnd].depth > activeNode.depth) subtreeEnd++

  const rest = [...flat.slice(0, activeIndex), ...flat.slice(subtreeEnd)]

  const insertIndexInRest =
    overId === activeId
      ? activeIndex
      : (() => {
          const i = rest.findIndex((n) => n.id === overId)
          return i === -1 ? rest.length : i
        })()

  const previousItem = rest[insertIndexInRest - 1]
  const nextItem = rest[insertIndexInRest]

  const rawDelta = dragOffsetX / INDENT_PX
  const dragDepthDelta =
    previousDepthDelta != null && Math.abs(rawDelta - previousDepthDelta) < DEPTH_HYSTERESIS
      ? previousDepthDelta
      : Math.round(rawDelta)
  const projectedDepth = activeNode.depth + dragDepthDelta

  const maxDepth = previousItem
    ? previousItem.depth + (isContainerType(previousItem.element.element_type) ? 1 : 0)
    : 1
  const minDepth = nextItem ? nextItem.depth : 1

  const depth = Math.max(1, Math.min(maxDepth, Math.max(minDepth, projectedDepth)))

  let parentId = rootId
  for (let i = insertIndexInRest - 1; i >= 0; i--) {
    if (rest[i].depth === depth - 1) {
      parentId = rest[i].id
      break
    }
    if (rest[i].depth < depth - 1) break
  }

  return {
    depth,
    insertIndexInRest,
    parentId,
    rest,
    insertBeforeId: nextItem ? nextItem.id : null,
    depthDelta: dragDepthDelta,
  }
}

/**
 * Reinserts the full dragged subtree, descendants included and shifted by the
 * same depth change, at an already-computed projected position. Null if the
 * drop moved nothing.
 *
 * Split out from getProjection so the indicator the user is looking at and the
 * tree the drop produces come from the SAME projection object.
 */
function applyProjection(
  tree: ModelElement,
  flat: FlatNode[],
  activeId: string,
  projection: Projection
): ModelElement | null {
  const activeIndex = flat.findIndex((n) => n.id === activeId)
  if (activeIndex === -1) return null
  const activeNode = flat[activeIndex]

  const depthDelta = projection.depth - activeNode.depth
  if (depthDelta === 0 && projection.insertIndexInRest === activeIndex) return null

  let subtreeEnd = activeIndex + 1
  while (subtreeEnd < flat.length && flat[subtreeEnd].depth > activeNode.depth) subtreeEnd++
  const subtree = flat
    .slice(activeIndex, subtreeEnd)
    .map((n) => ({ ...n, depth: n.depth + depthDelta }))

  const { rest, insertIndexInRest } = projection
  const newFlat = [...rest.slice(0, insertIndexInRest), ...subtree, ...rest.slice(insertIndexInRest)]
  return reuseUnchanged(rebuildFromFlat(tree, newFlat), indexTree(tree))
}

/** Projects a drag and applies it in one step. Returns null if the drop
 * didn't actually move anything. */
export function commitDrop(
  tree: ModelElement,
  flat: FlatNode[],
  activeId: string,
  overId: string,
  dragOffsetX: number,
  previousDepthDelta?: number | null
): ModelElement | null {
  const projection = getProjection(flat, activeId, overId, dragOffsetX, tree.id, previousDepthDelta)
  if (!projection) return null
  return applyProjection(tree, flat, activeId, projection)
}

/** The dragged row plus every descendant riding along with it -- dimmed
 * together during a drag, since they all move as one block. */
function subtreeIds(flat: FlatNode[], activeId: string): Set<string> {
  const ids = new Set<string>()
  const index = flat.findIndex((n) => n.id === activeId)
  if (index === -1) return ids
  ids.add(activeId)
  for (let i = index + 1; i < flat.length && flat[i].depth > flat[index].depth; i++) {
    ids.add(flat[i].id)
  }
  return ids
}

/** Do two projections describe the same landing place? Used to keep a
 * pointer move that changes nothing from re-rendering the outline. */
function samePlacement(a: Projection | null, b: Projection | null): boolean {
  if (a === b) return true
  if (!a || !b) return false
  return a.depth === b.depth && a.insertBeforeId === b.insertBeforeId && a.parentId === b.parentId
}

/**
 * The line showing where the dragged block will land: indented to the depth it
 * would sit at, tagged with the container it would enter. Absolutely
 * positioned in the gap above a row, so showing it costs no layout -- the
 * outline must not move mid-drag.
 */
function DropIndicator({ depth, label }: { depth: number; label: string }) {
  return (
    <Box
      aria-hidden
      sx={{
        // Centred in the row's 8px top padding, which is where the gap between
        // two rows now lives -- it used to be a margin above the row, and the
        // indicator hung outside the box to reach it.
        top: 3,
        position: 'absolute',
        left: depth * INDENT_PX,
        right: 0,
        height: 0,
        zIndex: 3,
        pointerEvents: 'none',
      }}
    >
      <Box sx={{ position: 'absolute', left: 0, right: 0, height: '2px', bgcolor: 'primary.main', borderRadius: 1 }} />
      <Box
        sx={{
          position: 'absolute',
          left: -3,
          top: -3,
          width: 8,
          height: 8,
          borderRadius: '50%',
          bgcolor: 'primary.main',
        }}
      />
      <Chip
        size="small"
        label={label}
        sx={{
          position: 'absolute',
          right: 0,
          top: -10,
          height: 20,
          fontSize: '0.75rem',
          bgcolor: 'primary.main',
          color: 'primary.contrastText',
          '& .MuiChip-label': { px: 1 },
        }}
      />
    </Box>
  )
}

/**
 * Loaded on demand, and that is load-bearing twice over.
 *
 * It carries a map, so it carries Plotly -- about a megabyte the editor does
 * not need until somebody presses the button, and a module whose UMD wrapper
 * reads `self` at the top level, which is not defined in Node. Importing it
 * eagerly put Plotly in the editor's own module graph and broke this file's
 * unit tests, which never render anything.
 */
const SpatialOrderDialog = lazy(() => import('@/components/SpatialOrderDialog'))

interface Props {
  tree: ModelElement
  measurements: MatrixMeasurement[]
  assignments: Record<string, string>
  onChange: (tree: ModelElement, assignments: Record<string, string>) => void
  /**
   * Reorder the WHOLE date list. Optional because it is only offered where
   * the new order can actually be kept -- see Stratigraphy.tsx.
   *
   * There is deliberately no per-phase sort. A phase's dates are a filter
   * over `measurements` (datesIn below), not a sequence the phase stores, so
   * one sort of the underlying list reorders every phase at once and no two
   * phases can end up ordered by different rules.
   */
  onSortDates?: (key: MeasurementSortKey) => void
  /**
   * Edit one date's ORDINATE -- its ring offset or its depth.
   *
   * Optional, and where it is absent those fields are not offered at all
   * rather than offered and inert. A wiggle match is its gaps and a
   * deposition sequence is its depths, so a host that cannot write them back
   * to the draft cannot let a user build either one here.
   */
  onSetOrdinate?: (measurementId: string, patch: Partial<MatrixMeasurement>) => void
  /**
   * The project's basemap-tiles setting, for the one map this editor can
   * open -- the spatial-order proposal's.
   */
  tiles?: boolean | null
}

/**
 * The settings that belong to ONE kind of element, inline in its row.
 *
 * Only three types have any, and each has few enough to sit on the row rather
 * than behind a disclosure -- unlike TPQ/TAQ, which every container carries
 * and almost none uses. These are not optional extras: a deposition
 * sequence's rigidity and an undated level's depth are the model.
 */
function ElementSettings({
  element,
  onSetParam,
}: {
  element: ModelElement
  onSetParam: (patch: ElementParams) => void
}) {
  const params = element.params || {}

  // NO TOOLTIP ON EITHER SELECT BELOW. A tooltip anchored to a Select opens
  // over the menu the Select just opened -- it sits above the first option and
  // swallows the click meant for it, so the top item cannot be chosen at all.
  // The two dropdowns say what they do in their own option words; what a
  // tooltip would have added is in the user manual, which is where a paragraph
  // belongs anyway. The text fields beside them keep theirs: a field opens
  // nothing for a tooltip to cover.

  if (element.element_type === 'wiggle_sequence') {
    return (
      <Select
        size="small"
        value={params.gap_units || 'rings'}
        onChange={(e) => onSetParam({ gap_units: e.target.value as ElementParams['gap_units'] })}
        inputProps={{ 'aria-label': `Gap units for ${element.name || 'wiggle match'}` }}
        sx={{ minWidth: 104, flexShrink: 0 }}
      >
        <MenuItem value="rings">Rings</MenuItem>
        <MenuItem value="varves">Varves</MenuItem>
        <MenuItem value="years">Years</MenuItem>
      </Select>
    )
  }

  if (element.element_type === 'deposition_sequence') {
    const variable = params.k_variable !== false
    return (
      <>
        <Select
          size="small"
          value={variable ? 'inferred' : 'fixed'}
          onChange={(e) =>
            onSetParam(
              e.target.value === 'inferred'
                ? { k_variable: true, k: null }
                : { k_variable: false, k: params.k ?? 1 }
            )
          }
          inputProps={{ 'aria-label': `Deposition rate for ${element.name || 'sequence'}` }}
          sx={{ minWidth: 128, flexShrink: 0 }}
        >
          <MenuItem value="inferred">Rate inferred</MenuItem>
          <MenuItem value="fixed">Rate fixed</MenuItem>
        </Select>
        {!variable && (
          <TextField
            size="small"
            type="number"
            label="k"
            value={params.k ?? ''}
            onChange={(e) =>
              onSetParam({ k: e.target.value === '' ? null : Number(e.target.value) })
            }
            inputProps={{
              'aria-label': `Rigidity k for ${element.name || 'deposition sequence'}`,
              min: 0,
            }}
            sx={{ width: 90, flexShrink: 0 }}
          />
        )}
      </>
    )
  }

  if (element.element_type === 'interpolated_depth') {
    return (
      <Tooltip title="The depth to report an age at. This level has no date of its own — the model reads its age off the levels either side.">
        <TextField
          size="small"
          type="number"
          label="Depth"
          value={params.depth_cm ?? ''}
          onChange={(e) =>
            onSetParam({ depth_cm: e.target.value === '' ? null : Number(e.target.value) })
          }
          inputProps={{
            'aria-label': `Depth of ${element.name || 'undated level'}`,
            min: 0,
          }}
          sx={{ width: 104, flexShrink: 0 }}
        />
      </Tooltip>
    )
  }

  return null
}

interface ElementFieldsProps {
  element: ModelElement
  typeColor: Record<ElementType, ElementTypeColor>
  onRename: (name: string) => void
  onSetParam: (patch: ElementParams) => void
  onSetLimit: (field: 'tpq_age_bp' | 'taq_age_bp', value: string) => void
  /**
   * Whether this row's TPQ/TAQ pair is showing. Owned by the editor rather
   * than the row, because Undo has to be able to put it away again -- state
   * that lives inside the row is state the snapshots cannot reach.
   */
  limitersOpen: boolean
  onOpenLimiters: () => void
}

function ElementFields({
  element,
  typeColor,
  onRename,
  onSetParam,
  onSetLimit,
  limitersOpen,
  onOpenLimiters,
}: ElementFieldsProps) {
  const limiters = [
    element.tpq_age_bp != null ? `TPQ ${element.tpq_age_bp}` : null,
    element.taq_age_bp != null ? `TAQ ${element.taq_age_bp}` : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <>
      {/* A coloured word, the treatment Results.tsx uses for this exact fact,
          and the one DESIGN.md prescribes: "Don't mark an element's type twice
          -- a coloured word or a glyph, not both -- and prefer the word."

          This was a saturated filled pill, which is the visual grammar of a
          status badge; ten of them stacked read as a column of alerts, the
          reading the Borrowed-Status Rule exists to prevent. The hue was never
          the problem and has not changed -- .ink is the variant DESIGN.md
          assigns to coloured label text. What it buys is that the row's 4px
          left border is now the only mark carrying type, and saturated primary
          belongs to the drag chrome alone: the one filled blue on the surface
          means "you are dropping here" rather than "this is a Sequence".

          Suppressed when the name already says it -- an element called
          "Boundary" does not need "Boundary" beside it. */}
      {/* A coloured word, the treatment Results.tsx uses for this exact fact,
          and the one DESIGN.md prescribes: "Don't mark an element's type twice
          -- a coloured word or a glyph, not both -- and prefer the word."

          The word is unconditional. It used to be suppressed when the name
          contained it, which meant naming a row "First Sequence" deleted the
          only type marker it had -- the type became legible or not depending
          on wording the user chose for other reasons. */}
      <Tooltip title={TYPE_HINT[element.element_type]}>
        {/* No tabIndex, matching Results.tsx: making each hint keyboard
            reachable would put a tab stop in front of every row's name
            field. The word carries the fact, and the add menu teaches all
            four hints where the choice is actually made. */}
        <Box
          component="span"
          sx={{
            color: typeColor[element.element_type].ink,
            fontSize: '0.75rem',
            fontWeight: 500,
            whiteSpace: 'nowrap',
            // One column for the four words, so the name fields beside them
            // share a left edge instead of stepping 20px between a "Phase"
            // row and a "Sequence" row at the same depth.
            minWidth: TYPE_SLOT_PX,
            flexShrink: 0,
          }}
        >
          {TYPE_LABEL[element.element_type]}
        </Box>
      </Tooltip>
      {/* No variant prop -- every other field in the app is the outlined
          TextField/Select default; this one used to be the lone exception
          with a bare underline instead of a bordered box, easy to miss as
          an editable field next to the boxed ones surrounding it. */}
      <TextField
        size="small"
        value={element.name || ''}
        onChange={(e) => onRename(e.target.value)}
        // The field had no label, no aria-label and no placeholder, so a
        // screen reader announced only the current value -- on a thirty-row
        // outline, thirty anonymous text boxes.
        inputProps={{ 'aria-label': `Name of ${TYPE_LABEL[element.element_type]}` }}
        // Everything else in the row is a fixed-size control, so the name is
        // the only thing that can give. It takes the slack when there is any
        // and yields it when there is not, which keeps the action cluster on
        // one line at tablet width and at depth.
        //
        // minWidth: 0 on the input itself is the load-bearing part: a flex
        // item will not shrink below its content's min-content width, and an
        // <input> carries an intrinsic one of ~170px. Without it the row wraps
        // instead of the field narrowing, and the field then GROWS into the
        // space the wrap just freed -- which is what it was doing.
        sx={{
          // 0, not 1, for grow: the name should give space up when the row is
          // tight, never take it when there is slack. Growing to 260px was
          // itself what pushed the 176px action cluster onto a second line --
          // the field claimed the room the buttons needed.
          // Responsive basis, because flexbox WRAPS in preference to shrinking:
          // with wrap enabled the action cluster moves to a second line rather
          // than squeezing this field, so the field has to start smaller at
          // tablet width for the row to stay on one line at depth.
          flex: { xs: '0 1 108px', md: '0 1 180px' },
          minWidth: 0,
          '& .MuiInputBase-root': { minWidth: 0 },
          '& input': { minWidth: 0 },
        }}
      />
      <ElementSettings element={element} onSetParam={onSetParam} />
      {/* Limiters on demand, not two permanently-open number boxes.
          
          Every container row carried both fields at all times -- 180px of
          labelled inputs on every Sequence and Phase, forever, for values the
          page's own help text says to use "only if you know this information".
          They were the largest single contributor to the row height, to the
          scroll length of a thirty-element model, and to the action cluster
          wrapping to a second line at depth.
          
          Set limiters read as the caption Results.tsx already uses for the
          same pair, so the two views state a TPQ the same way. Unset, they are
          one text button. */}
      {isContainerType(element.element_type) &&
        (limitersOpen ? (
          <>
            <TextField
              size="small"
              type="number"
              label="TPQ"
              value={element.tpq_age_bp ?? ''}
              onChange={(e) => onSetLimit('tpq_age_bp', e.target.value)}
              inputProps={{ 'aria-label': `TPQ for ${element.name || TYPE_LABEL[element.element_type]}` }}
              sx={{ width: 90 }}
            />
            <TextField
              size="small"
              type="number"
              label="TAQ"
              value={element.taq_age_bp ?? ''}
              onChange={(e) => onSetLimit('taq_age_bp', e.target.value)}
              inputProps={{ 'aria-label': `TAQ for ${element.name || TYPE_LABEL[element.element_type]}` }}
              sx={{ width: 90 }}
            />
          </>
        ) : (
          // Set limiters collapse to the caption too, not just unset ones --
          // otherwise the rows that carry a TPQ keep the height this change
          // exists to remove. The caption is the control: clicking it reopens
          // the fields.
          <Tooltip
            title={
              limiters
                ? 'Edit the earliest and latest this element could be'
                : 'Earliest and latest this element could be — only if you know them'
            }
          >
            <Button
              size="small"
              onClick={onOpenLimiters}
              aria-label={
                limiters
                  ? `Limiters for ${element.name || TYPE_LABEL[element.element_type]}: ${limiters}`
                  : `Add TPQ/TAQ to ${element.name || TYPE_LABEL[element.element_type]}`
              }
              sx={{
                flexShrink: 0,
                minHeight: 44,
                color: 'text.secondary',
                fontWeight: 400,
                whiteSpace: 'nowrap',
              }}
            >
              {limiters || 'Add TPQ/TAQ'}
            </Button>
          </Tooltip>
        ))}
      {/* No flexGrow spacer here. RowActions already right-aligns itself with
          ml:'auto', so a growing spacer was a second mechanism for the same
          job -- and a harmful one: it absorbed the line's remaining width,
          which both denied the 176px action cluster the room to sit on line
          one and stopped the name field ever being asked to shrink. The row
          wrapped instead. */}
      {/* The count is worth its width only when the dates themselves are
          hidden. Expanded, the row lists every one of them immediately below,
          so the caption was restating what is already on screen -- and on a
          tablet it was the last ~50px that pushed the action cluster onto a
          second line. The collapsed summary already reports the count. */}
    </>
  )
}

/**
 * The ORDINATE a date needs to be a member of the sequence it has been put
 * in: a gap for a wiggle match, a depth for a deposition sequence.
 *
 * Edited here, beside the assignment, rather than back on the data-entry
 * table. Moving a date into a wiggle match is exactly the action that leaves
 * it without a gap, and a wiggle match with a member at gap 0 by default is a
 * model asserting something nobody entered -- so the field appears the moment
 * the date lands somewhere that needs it.
 *
 * The blank state is a blank field, not a zero. Zero is a real, meaningful
 * offset (it is the reference), so defaulting to it would make "not entered"
 * and "the reference" indistinguishable.
 */
function OrdinateField({
  measurement,
  holder,
  onChange,
}: {
  measurement: MatrixMeasurement
  holder: ElementType
  onChange: (patch: Partial<MatrixMeasurement>) => void
}) {
  if (holder === 'wiggle_sequence') {
    return (
      <Tooltip title="Years before the series reference: 0 at the reference and increasing into the past, so a larger gap is an older date.">
        <TextField
          size="small"
          type="number"
          label="Gap"
          value={measurement.ring_offset ?? ''}
          onChange={(e) =>
            onChange({ ring_offset: e.target.value === '' ? null : Number(e.target.value) })
          }
          inputProps={{ 'aria-label': `Gap for ${measurement.sample_name}` }}
          sx={{ width: 96, flexShrink: 0 }}
        />
      </Tooltip>
    )
  }
  if (holder === 'deposition_sequence') {
    return (
      <Tooltip title="Depth below the surface. Deeper is older, and the deposition model runs along this axis.">
        <TextField
          size="small"
          type="number"
          label="Depth"
          value={measurement.depth_cm ?? ''}
          onChange={(e) =>
            onChange({ depth_cm: e.target.value === '' ? null : Number(e.target.value) })
          }
          inputProps={{ 'aria-label': `Depth for ${measurement.sample_name}`, min: 0 }}
          sx={{ width: 96, flexShrink: 0 }}
        />
      </Tooltip>
    )
  }
  return null
}

/**
 * The dates an element holds, listed underneath it.
 *
 * Its own component because the ROOT is not a TreeRow: the outline flattens
 * everything *under* the root and renders the root in a card of its own. That
 * was harmless while only a Phase could hold dates -- a root is a Sequence or
 * a Phase wrapping the model, and nothing is ever assigned to it. It stopped
 * being harmless when a wiggle match or a deposition sequence could BE the
 * root, which is exactly what both presets and the OxCal importer produce:
 * every date was assigned to the root, the root had no list, and the dates
 * were not unassigned either, so they appeared nowhere at all.
 *
 * Membership is edited where it is shown, which is the rule this list already
 * followed: it used to be readable here and changeable only in a panel below
 * the whole outline, which on a large model put the two halves of one task
 * thousands of pixels apart.
 */
function ElementDates({
  element,
  dates,
  childCount,
  onOpenPhaseMenu,
  onSetOrdinate,
  onOpenRingHelper,
}: {
  element: ModelElement
  dates: MatrixMeasurement[]
  childCount: number
  onOpenPhaseMenu: (measurementId: string, anchor: HTMLElement) => void
  onSetOrdinate?: (measurementId: string, patch: Partial<MatrixMeasurement>) => void
  onOpenRingHelper?: (elementId: string) => void
}) {
  const type = element.element_type
  const ordered = useMemo(() => orderedForHolder(type, dates), [type, dates])
  // Length decides whether a wiggle match resolves at all; count only decides
  // how tightly. Said BEFORE the run, where it can still change what gets
  // commissioned, rather than after the answer comes back in two ranges.
  const seriesWarning =
    type === 'wiggle_sequence'
      ? shortSeriesWarning(
          dates.map((m) => m.ring_offset),
          (element.params?.gap_units as string) || 'rings'
        )
      : null

  if (!holdsDates(type)) return null
  // A container holding other elements is a structural wrapper, not somewhere
  // dates were forgotten.
  if (dates.length === 0 && childCount > 0) return null

  return (
    <Box sx={{ mt: 1, pl: 1 }}>
      {/* The gaps ARE the model, and people have ring NUMBERS, so the
          conversion is offered here rather than left to be done by hand in a
          spreadsheet -- which is where the sign goes wrong. */}
      {type === 'wiggle_sequence' && dates.length > 0 && onSetOrdinate && (
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1, flexWrap: 'wrap' }}>
          <Button size="small" onClick={() => onOpenRingHelper?.(element.id)}>
            Set gaps from ring numbers
          </Button>
          {seriesWarning && (
            <Typography variant="caption" color="warning.main" sx={{ flex: '1 1 240px' }}>
              {seriesWarning}
            </Typography>
          )}
        </Stack>
      )}
      {dates.length === 0 ? (
        <Typography variant="caption" color="text.secondary">
          {/* Two routes in, because the list below is often empty: a model
              built from a preset has every date placed already, and the
              only way into a new phase is out of another. */}
          {type === 'phase'
            ? 'No dates in this phase yet — assign them from the unassigned list below, or move them here from another phase.'
            : 'No dates in this sequence yet — assign them from the unassigned list below, or move them here from another phase.'}
        </Typography>
      ) : (
        ordered.map((m) => (
          <Box
            key={m.id}
            sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 1, mb: 0.5 }}
          >
            <Typography variant="body2" sx={{ fontVariantNumeric: 'tabular-nums', minWidth: 200 }}>
              {m.sample_name} — {m.age_bp} ± {m.error_1sigma ?? '?'} BP
            </Typography>
            {/* The dates listed under an element are, by construction, the
                ones assigned to it, so the control's label is this element's
                own name and needs no lookup. */}
            <PhaseButton
              label={element.name || TYPE_LABEL[type]}
              ariaLabel={`Phase for ${m.sample_name}`}
              onOpen={(anchor) => onOpenPhaseMenu(m.id, anchor)}
            />
            {onSetOrdinate && (
              <OrdinateField
                measurement={m}
                holder={type}
                onChange={(patch) => onSetOrdinate(m.id, patch)}
              />
            )}
          </Box>
        ))
      )}
    </Box>
  )
}

/**
 * The order to list an element's dates in.
 *
 * A Phase keeps the reading order the user arranged (utils/sortMeasurements),
 * because a Phase asserts no order and the list is theirs. The other two do
 * not: their ORDINATE is the order, and it is the order the solver uses, so
 * listing a wiggle match's members in anything but gap order shows a series
 * that is not the series being solved.
 *
 * Oldest first in both, matching the outline itself -- ascending gap (a gap
 * counts years before the reference) and descending depth (deeper is older).
 * A date with no ordinate yet goes last rather than to a made-up position, so
 * a half-entered series does not reshuffle on every keystroke.
 */
export function orderedForHolder(
  type: ElementType,
  dates: MatrixMeasurement[]
): MatrixMeasurement[] {
  const key =
    type === 'wiggle_sequence'
      ? (m: MatrixMeasurement) => m.ring_offset
      : type === 'deposition_sequence'
        ? (m: MatrixMeasurement) => m.depth_cm
        : null
  if (!key) return dates
  return [...dates].sort((a, b) => {
    const left = key(a)
    const right = key(b)
    if (left == null && right == null) return 0
    if (left == null) return 1
    if (right == null) return -1
    return right - left
  })
}

/**
 * The one control that moves a date between phases, so the row list and the
 * unassigned panel cannot drift into two different pickers.
 *
 * A button that opens ONE menu owned by the editor, not a Select per date.
 * A Select carries its whole option list as React children and MUI walks
 * them on every render to find the selected one; with 800 dates and 70
 * phases that was 56,000 option elements rebuilt per keystroke, and the
 * single largest cost on the page. The menu itself is PhaseMenu below,
 * rendered once and pointed at whichever date's button was pressed.
 */
function PhaseButton({
  label,
  ariaLabel,
  onOpen,
}: {
  label: string
  ariaLabel: string
  onOpen: (anchor: HTMLElement) => void
}) {
  return (
    <Button
      size="small"
      variant="outlined"
      color="inherit"
      endIcon={<ArrowDropDownIcon />}
      aria-label={ariaLabel}
      aria-haspopup="listbox"
      onClick={(e) => onOpen(e.currentTarget)}
      sx={{
        minWidth: 220,
        justifyContent: 'space-between',
        fontWeight: 400,
        color: 'text.primary',
        borderColor: 'divider',
      }}
    >
      {label}
    </Button>
  )
}

/** One phase choice as the shared menu lists it. */
interface PhaseChoice {
  id: string
  label: string
}

/**
 * The options the phase menu offers, with a path prefixed wherever a name
 * is not unique; showing it on every option would bury the name most models
 * never need disambiguated. Computed once per tree in the editor, not once
 * per date.
 */
function phaseChoicesFor(root: ModelElement): PhaseChoice[] {
  const options = phaseOptions(root)
  const duplicated = new Set(
    options.map((o) => o.name).filter((name, i, all) => all.indexOf(name) !== i)
  )
  return options.map((p) => ({
    id: p.id,
    label: duplicated.has(p.name) ? `${p.path} › ${p.name}` : p.name,
  }))
}

interface RowActionsProps {
  element: ModelElement
  /** Whether the container this row sits in orders its children. */
  parentOrders: boolean
  canMoveUp: boolean
  canMoveDown: boolean
  onMoveUp: () => void
  onMoveDown: () => void
  onAdd: (e: React.MouseEvent<HTMLElement>) => void
  onDelete: () => void
}

function RowActions({
  element,
  parentOrders,
  canMoveUp,
  canMoveDown,
  onMoveUp,
  onMoveDown,
  onAdd,
  onDelete,
}: RowActionsProps) {
  const canContain = acceptsChildren(element.element_type)
  // A cleared name field must not leave "Delete null" on the button.
  const label = element.name || TYPE_LABEL[element.element_type]
  // These arrows move THIS element among its siblings, so what they mean
  // depends on the container it sits in, not on what it is. Inside a Sequence
  // that is a claim about time and says so; inside a Phase -- which is the
  // shape a multi-context model starts in, see bootstrapModelTree -- the model
  // asserts no order, so the same button is only moving a list row and must
  // not call that "earlier".
  const up = parentOrders ? 'Move earlier' : 'Move up'
  const down = parentOrders ? 'Move later' : 'Move down'
  const orderNote = parentOrders ? '' : ' (this phase does not order its contents)'
  return (
    // One group, so the four controls wrap together instead of breaking apart
    // and leaving Delete stranded on a line of its own -- which is what a
    // narrow screen did to them, and it put the destructive control where it
    // read as unrelated to the row above it.
    <Box sx={{ display: 'flex', alignItems: 'center', flexShrink: 0, ml: 'auto' }}>
      <Tooltip title={`${up}${orderNote}`}>
        <span>
          <IconButton size="small" aria-label={up} disabled={!canMoveUp} onClick={onMoveUp} sx={HIT_TARGET}>
            <ArrowUpwardIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
      <Tooltip title={`${down}${orderNote}`}>
        <span>
          <IconButton size="small" aria-label={down} disabled={!canMoveDown} onClick={onMoveDown} sx={HIT_TARGET}>
            <ArrowDownwardIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
      {canContain && (
        <Tooltip title={`Add inside ${label}`}>
          <IconButton size="small" aria-label={`Add inside ${label}`} onClick={onAdd} sx={HIT_TARGET}>
            <AddIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      )}
      <Tooltip title={`Delete ${label}`}>
        <IconButton
          size="small"
          aria-label={`Delete ${label}`}
          onClick={onDelete}
          sx={{ ...HIT_TARGET, '&:hover, &:focus-visible': { color: 'error.main' } }}
        >
          <DeleteIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Box>
  )
}

/**
 * What a row's drag handle takes from dnd-kit, handed down by SortableRow.
 *
 * dnd-kit publishes its state through React context, and every hook that
 * reads it re-renders its component on every change: each pointer move of a
 * drag, and every row mounting or unmounting, which is what a collapse is.
 * A row that called useSortable itself was therefore redrawn -- name field,
 * six tooltips, every date under it -- hundreds of times per drag, and all
 * 275 of them together whenever one collapsed. The hook now lives in a shell
 * the size of one Box, and the row body is memoised on its data alone; only
 * the handle, which actually needs the listeners, reads this.
 */
interface HandleBinding {
  attributes: ReturnType<typeof useSortable>['attributes']
  listeners: ReturnType<typeof useSortable>['listeners']
  /** A dnd-kit keyboard drag is in progress, so the sensor owns the arrow keys. */
  isDragActive: boolean
}

const HandleContext = createContext<HandleBinding>({
  attributes: {} as HandleBinding['attributes'],
  listeners: undefined,
  isDragActive: false,
})

/**
 * The droppable element of one row: registers with dnd-kit, and nothing
 * else. `children` is the row body, created by the editor -- so when only
 * dnd-kit's context changes, the element handed here is the same object as
 * last time and React reuses the body without rendering it.
 */
function SortableRow({
  id,
  level,
  ariaLabel,
  children,
}: {
  id: string
  level: number
  ariaLabel: string
  children: ReactNode
}) {
  const { attributes, listeners, setNodeRef, active } = useSortable({ id })
  const isDragActive = active !== null
  const binding = useMemo(
    () => ({ attributes, listeners, isDragActive }),
    [attributes, listeners, isDragActive]
  )
  return (
    // The row gap lives INSIDE this box as padding, not as a margin above it,
    // so the ancestor guides drawn down its left edge meet the guides of the
    // row above and read as continuous lines rather than a dashed ladder.
    <Box
      ref={setNodeRef}
      role="listitem"
      // The nesting level, which the outline otherwise expresses only as a
      // left margin -- a purely visual cue that reaches nobody using a screen
      // reader. aria-level is 1-based and `depth` already starts at 1 for the
      // root's direct children.
      aria-level={level}
      aria-label={ariaLabel}
      sx={{ position: 'relative', pt: 1 }}
    >
      <HandleContext.Provider value={binding}>{children}</HandleContext.Provider>
    </Box>
  )
}

interface DragHandleProps {
  id: string
  name: string
  canMoveUp: boolean
  canMoveDown: boolean
  onMoveUp: (id: string) => void
  onMoveDown: (id: string) => void
  onIndent: (id: string) => boolean
  onOutdent: (id: string) => boolean
}

function DragHandle({
  id,
  name,
  canMoveUp,
  canMoveDown,
  onMoveUp,
  onMoveDown,
  onIndent,
  onOutdent,
}: DragHandleProps) {
  const { attributes, listeners, isDragActive } = useContext(HandleContext)
  return (
    <Tooltip title={`Reorder ${name} — drag, or use the arrow keys`}>
      <Box
        {...attributes}
        {...listeners}
        onKeyDown={(e) => {
          // Only before a dnd-kit keyboard drag has been activated with
          // Space/Enter -- once it has, the sensor owns the arrow keys
          // and this would fight it for the same gesture.
          if (isDragActive) return
          const handled: Record<string, () => boolean | void> = {
            ArrowLeft: () => onOutdent(id),
            ArrowRight: () => onIndent(id),
            ArrowUp: () => canMoveUp && (onMoveUp(id), true),
            ArrowDown: () => canMoveDown && (onMoveDown(id), true),
          }
          const action = handled[e.key]
          if (!action) return
          e.preventDefault()
          action()
        }}
        aria-label={`Reorder ${name}. Arrow up and down move it among its neighbours; arrow left and right change how deeply it is nested.`}
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'text.secondary',
          cursor: 'grab',
          touchAction: 'none',
          // The affordance the header names as the way to re-nest, and
          // it was a bare 20px glyph -- the smallest target in the app.
          ...HIT_TARGET,
          borderRadius: 1,
          '&:active': { cursor: 'grabbing' },
          '&:focus-visible': {
            outline: (theme) => `2px solid ${theme.palette.primary.main}`,
            outlineOffset: 2,
          },
        }}
      >
        <DragIndicatorIcon fontSize="small" />
      </Box>
    </Tooltip>
  )
}

interface TreeRowProps {
  node: FlatNode
  typeColor: Record<ElementType, ElementTypeColor>
  dates: MatrixMeasurement[]
  /** Open the shared phase menu for one of this row's dates. */
  onOpenPhaseMenu: (measurementId: string, anchor: HTMLElement) => void
  /** Children this row holds, so a collapsed container can say what it hides. */
  childCount: number
  /** Everything under this row at any depth, counted for the collapsed summary. */
  hiddenDescendants: number
  isCollapsed: boolean
  onToggleCollapsed: (id: string) => void
  canMoveUp: boolean
  canMoveDown: boolean
  /** Drawn in the gap above this row when the block would land here. */
  indicator: { depth: number; label: string } | null
  /** This row is the container the block would land inside. */
  isDropTarget: boolean
  /** This row is part of the block being dragged. */
  isDragged: boolean
  onRename: (id: string, name: string) => void
  onSetParam: (id: string, patch: ElementParams) => void
  /**
   * Edit a DATE's ordinate -- its gap or its depth. Optional because not
   * every host of this editor owns the measurements; where it is absent the
   * fields are simply not offered, rather than offered and inert.
   */
  onSetOrdinate?: (measurementId: string, patch: Partial<MatrixMeasurement>) => void
  /** Open the ring-number converter for this wiggle match. */
  onOpenRingHelper?: (elementId: string) => void
  onSetLimit: (id: string, field: 'tpq_age_bp' | 'taq_age_bp', value: string) => void
  limitersOpen: boolean
  onOpenLimiters: (id: string) => void
  onMoveUp: (id: string) => void
  onMoveDown: (id: string) => void
  onIndent: (id: string) => boolean
  onOutdent: (id: string) => boolean
  onAdd: (id: string, e: React.MouseEvent<HTMLElement>) => void
  onDelete: (id: string) => void
}

/**
 * Two rows are the same row when their element is the same object -- see
 * updateElement, which keeps it so for every element an edit did not touch --
 * at the same depth, under ancestors that would draw the same guides and say
 * the same name; and when everything else they were handed is unchanged.
 */
function sameRow(prev: TreeRowProps, next: TreeRowProps): boolean {
  if (prev.node.element !== next.node.element || prev.node.depth !== next.node.depth) return false
  const a = prev.node.ancestors
  const b = next.node.ancestors
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (a[i].id !== b[i].id || a[i].element_type !== b[i].element_type || a[i].name !== b[i].name) {
      return false
    }
  }
  for (const key of Object.keys(next) as Array<keyof TreeRowProps>) {
    if (key === 'node') continue
    if (key === 'indicator') {
      const p = prev.indicator
      const n = next.indicator
      if ((p === null) !== (n === null)) return false
      if (p && n && (p.depth !== n.depth || p.label !== n.label)) return false
      continue
    }
    if (prev[key] !== next[key]) return false
  }
  return true
}

const TreeRow = memo(function TreeRow({
  node,
  typeColor,
  dates,
  onOpenPhaseMenu,
  childCount,
  hiddenDescendants,
  isCollapsed,
  onToggleCollapsed,
  canMoveUp,
  canMoveDown,
  indicator,
  isDropTarget,
  isDragged,
  onRename,
  onSetParam,
  onSetOrdinate,
  onOpenRingHelper,
  onSetLimit,
  limitersOpen,
  onOpenLimiters,
  onMoveUp,
  onMoveDown,
  onIndent,
  onOutdent,
  onAdd,
  onDelete,
}: TreeRowProps) {
  // No transform and no transition: the outline holds still for the whole drag
  // (see the note at the top of this file) and the indicator does the talking.
  // The dnd-kit registration lives in SortableRow, outside this memo.
  const { element, depth } = node
  const canContain = acceptsChildren(element.element_type)
  const label = element.name || TYPE_LABEL[element.element_type]
  // The direct parent is the last ancestor; it decides what this row's move
  // arrows are actually claiming.
  const parentOrders = ordersChildren(node.ancestors[node.ancestors.length - 1])
  // A phase's dates are not child ELEMENTS, but they are what it holds and
  // what fills the screen -- a thirty-date phase is the row most worth
  // collapsing, and counting only nested elements would deny it the control.
  const collapsibleCount = childCount + dates.length

  return (
    <>
      {indicator && <DropIndicator depth={indicator.depth} label={indicator.label} />}
      {/* One guide per level this row is nested under, in that container's own
          hue: solid where the container orders its children, dashed where it
          does not.
 */}
      {node.ancestors.map((ancestor, level) => (
        <Box
          key={ancestor.id}
          aria-hidden
          sx={{
            position: 'absolute',
            top: 0,
            bottom: 0,
            left: level * INDENT_PX + GUTTER_INSET,
            borderLeft: `1px ${ordersChildren(ancestor) ? 'solid' : 'dashed'} ${
              typeColor[ancestor.element_type].main
            }`,
            opacity: 0.45,
            pointerEvents: 'none',
          }}
        />
      ))}
      <Box sx={{ ml: depth * INDENT_UNITS, opacity: isDragged ? 0.4 : 1 }}>
      <Paper
        variant="outlined"
        sx={{
          p: 2,
          borderLeft: `4px solid ${typeColor[element.element_type].main}`,
          ...(isDropTarget
            ? { borderColor: 'primary.main', boxShadow: (theme) => `0 0 0 1px ${theme.palette.primary.main}` }
            : {}),
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
          <DragHandle
            id={node.id}
            name={label}
            canMoveUp={canMoveUp}
            canMoveDown={canMoveDown}
            onMoveUp={onMoveUp}
            onMoveDown={onMoveDown}
            onIndent={onIndent}
            onOutdent={onOutdent}
          />
          {/* Only a container can hide anything, and a leaf reserves nothing
              for the chevron it will never have: a Boundary or Hiatus row has
              one fewer control, so it starts one control earlier rather than
              carrying 44px of empty gutter after the drag handle.

              What the empty slot was buying was a shared left edge for the
              type words. That is now bought properly, by the fixed-width type
              slot in ElementFields -- which also fixes the misalignment the
              spacer never addressed, where "Sequence" and "Phase" differ by
              20px and pushed every name field to a different x. */}
          {canContain && collapsibleCount > 0 ? (
            <Tooltip title={isCollapsed ? `Show ${collapsibleCount} inside ${label}` : `Hide what is inside ${label}`}>
              <IconButton
                size="small"
                aria-label={isCollapsed ? `Expand ${label}` : `Collapse ${label}`}
                aria-expanded={!isCollapsed}
                onClick={() => onToggleCollapsed(node.id)}
                sx={HIT_TARGET}
              >
                {isCollapsed ? <ChevronRightIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
              </IconButton>
            </Tooltip>
          ) : null}
          <ElementFields
            element={element}
            typeColor={typeColor}
            onRename={(name) => onRename(node.id, name)}
            onSetParam={(patch) => onSetParam(node.id, patch)}
            onSetLimit={(field, value) => onSetLimit(node.id, field, value)}
            limitersOpen={limitersOpen}
            onOpenLimiters={() => onOpenLimiters(node.id)}
          />
          <RowActions
            element={element}
            parentOrders={parentOrders}
            canMoveUp={canMoveUp}
            canMoveDown={canMoveDown}
            onMoveUp={() => onMoveUp(node.id)}
            onMoveDown={() => onMoveDown(node.id)}
            onAdd={(e) => onAdd(node.id, e)}
            onDelete={() => onDelete(node.id)}
          />
        </Box>

        {/* A collapsed container says what it is holding. Hiding rows without
            saying how many turns the outline into a place where work can go
            missing silently. */}
        {isCollapsed && (hiddenDescendants > 0 || dates.length > 0) && (
          <Box sx={{ mt: 1, pl: 1 }}>
            <Typography variant="caption" color="text.secondary">
              {[
                hiddenDescendants > 0
                  ? `${hiddenDescendants} element${hiddenDescendants === 1 ? '' : 's'}`
                  : null,
                dates.length > 0 ? `${dates.length} date${dates.length === 1 ? '' : 's'}` : null,
              ]
                .filter(Boolean)
                .join(' · ')}{' '}
              hidden
            </Typography>
          </Box>
        )}

        {!isCollapsed && (
          <ElementDates
            element={element}
            dates={dates}
            childCount={childCount}
            onOpenPhaseMenu={onOpenPhaseMenu}
            onSetOrdinate={onSetOrdinate}
            onOpenRingHelper={onOpenRingHelper}
          />
        )}
      </Paper>
      </Box>
    </>
  )
}, sameRow)

export default function ModelTreeEditor({
  tree,
  measurements,
  assignments,
  onChange,
  onSortDates,
  onSetOrdinate,
  tiles = false,
}: Props) {
  const [addMenu, setAddMenu] = useState<{ anchor: HTMLElement; targetId: string } | null>(null)
  // The one phase picker, and which date it is open for. See PhaseButton.
  const [phaseMenu, setPhaseMenu] = useState<{ anchor: HTMLElement; measurementId: string } | null>(
    null
  )
  const phaseChoices = useMemo(() => phaseChoicesFor(tree), [tree])
  // The wiggle match whose ring-number converter is open, by element id.
  const [ringHelperFor, setRingHelperFor] = useState<string | null>(null)
  // A preset awaiting confirmation. Presets REPLACE the model, so they are
  // confirmed the way a deletion is.
  const [pendingPreset, setPendingPreset] = useState<
    'wiggle_sequence' | 'deposition_sequence' | null
  >(null)
  // The element awaiting a delete confirmation, held by id rather than by node
  // so it cannot go stale against a tree that changed underneath it.
  const [pendingRemoval, setPendingRemoval] = useState<string | null>(null)
  // Whether the spatial-order proposal is open. A PROPOSAL and nothing else:
  // see SpatialOrderDialog for why it is never applied on its own.
  const [spatialOrderOpen, setSpatialOrderOpen] = useState(false)
  // The "build from data" menu, and the grouping key awaiting confirmation.
  // Grouping REPLACES the model, as the presets do, so it is confirmed the
  // same way.
  const [buildMenu, setBuildMenu] = useState<HTMLElement | null>(null)
  const [pendingGrouping, setPendingGrouping] = useState<SiteGroupingKey | null>(null)
  // Containers whose children are hidden. Ids rather than nodes, so a tree
  // edit cannot leave this pointing at something that no longer exists.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  // What the last keyboard move did, announced to assistive technology.
  // Arrow-key moves restructured the tree in silence: dnd-kit's own live
  // region covers ITS drag, and the arrow-key path is ours, so nothing spoke.
  const [announcement, setAnnouncement] = useState('')
  /**
   * Snapshots to step back through, newest last, capped at 20.
   *
   * STRUCTURAL edits only -- add, delete, move, renest, drop, bracket,
   * reassign. Renaming and the TPQ/TAQ fields are deliberately excluded: they
   * go through update() on every keystroke, so recording them would fill the
   * buffer with single characters and push the structural change the user
   * actually wants back out of reach. Text is already undoable by typing.
   */
  // Which rows are showing their TPQ/TAQ pair. Editor state, not row state,
  // so that revealing the pair can be recorded in the snapshots above and
  // taken back by Undo like any other change to the row.
  const [openLimiters, setOpenLimiters] = useState<Set<string>>(new Set())
  const historyRef = useRef<
    Array<{ tree: ModelElement; assignments: Record<string, string>; openLimiters: Set<string> }>
  >([])
  const [undoDepth, setUndoDepth] = useState(0)
  // What was collapsed before a drag expanded everything, so it can be put
  // back afterwards instead of the reader losing every collapse on the first
  // gesture of a long session.
  const collapsedBeforeDragRef = useRef<Set<string> | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  // Where the block would land right now, for the indicator. Replaced only
  // when the LANDING PLACE changes: a drag across one row is hundreds of move
  // events but a handful of distinct placements.
  const [projection, setProjection] = useState<Projection | null>(null)
  // The same projection, where the drop handler can read it without going
  // through React state: a drop landing in the same tick as the last move --
  // what a quick flick does -- would otherwise commit the placement from one
  // event ago, or none at all.
  const projectionRef = useRef<Projection | null>(null)
  // The last row the pointer was actually over. dnd-kit reports no target for
  // the odd frame -- re-measuring, or crossing the gap between two rows -- and
  // treating that as "back where it started" makes the line jump home for a
  // frame. Holding the last real answer keeps it still.
  const lastOverIdRef = useRef<string | null>(null)
  const typeColor = useElementTypeColors()

  // Module-level option objects, not literals: useSensor memoises on its
  // options, so a fresh object each render is a fresh sensor, a fresh
  // DndContext value, and a re-render of every useSortable row in the
  // outline -- which is every row, on every keystroke.
  const sensors = useSensors(
    useSensor(PointerSensor, POINTER_SENSOR_OPTIONS),
    useSensor(KeyboardSensor, KEYBOARD_SENSOR_OPTIONS)
  )

  // One pass over the dates, grouped by the element they sit in, instead of a
  // fresh filter per row. Called inside the row map, it was O(rows x dates) on
  // every render -- and every keystroke in a name field is a render, so a
  // 30-element, 30-date model did ~900 comparisons per character typed.
  const datesByElement = useMemo(() => {
    const map = new Map<string, MatrixMeasurement[]>()
    for (const m of measurements) {
      const target = assignments[m.id]
      if (!target) continue
      const bucket = map.get(target)
      if (bucket) bucket.push(m)
      else map.set(target, [m])
    }
    return map
  }, [measurements, assignments])

  const NO_DATES: MatrixMeasurement[] = EMPTY_DATES
  const datesIn = (elementId: string) => datesByElement.get(elementId) ?? NO_DATES

  const remember = () => {
    historyRef.current.push({ tree, assignments, openLimiters })
    if (historyRef.current.length > 20) historyRef.current.shift()
    setUndoDepth(historyRef.current.length)
  }

  const undo = () => {
    const previous = historyRef.current.pop()
    setUndoDepth(historyRef.current.length)
    if (!previous) return
    // Whatever was hidden may not exist any more, so show everything rather
    // than restore a collapse set that could hide the change being undone.
    setCollapsed(new Set())
    setOpenLimiters(previous.openLimiters)
    setAnnouncement('Undid the last structural change')
    onChange(previous.tree, previous.assignments)
  }

  /**
   * Show a row's TPQ/TAQ pair, remembering first so Undo can put it away --
   * and with it any value typed into the pair since, because the snapshot
   * taken here predates them both.
   */
  const showLimiters = (id: string) => {
    remember()
    setOpenLimiters((open) => new Set(open).add(id))
  }

  /**
   * An edit that may touch any number of elements, applied to a full copy
   * -- then every element it left alone is swapped back for the original
   * (reuseUnchanged), so only the rows it actually changed redraw.
   */
  const update = (mutate: (draft: ModelElement) => void) => {
    const draft = cloneTree(tree)
    mutate(draft)
    onChange(reuseUnchanged(draft, indexTree(tree)), assignments)
  }

  const addChild = (parentId: string, type: ElementType) => {
    remember()
    onChange(
      updateElement(tree, parentId, (parent) => {
        parent.children = [
          ...(parent.children || []),
          {
            id: newElementId(),
            element_type: type,
            name: TYPE_LABEL[type],
            // Only the ordinate-bearing sequences start with settings; every
            // other type's params are empty, and model_tree.element_params
            // fills in whatever a stored element does not carry.
            params: DEFAULT_PARAMS[type] ?? null,
            tpq_age_bp: null,
            taq_age_bp: null,
            children: [],
          },
        ]
      }),
      assignments
    )
  }

  // What deleting this element would actually take with it. Computed once and
  // used for both the confirmation copy and the deletion itself, so the count
  // the user agrees to is the count that is removed.
  const removalImpact = (id: string) => {
    const subtree = findElement(tree, id)
    const descendantIds = new Set<string>()
    if (subtree) walk(subtree, null, (el) => { descendantIds.add(el.id) })
    const orphaned = measurements.filter((m) => descendantIds.has(assignments[m.id] || ''))
    return {
      // The subtree includes the element itself; what is being reported is
      // what goes with it.
      descendants: Math.max(0, descendantIds.size - 1),
      orphaned,
    }
  }

  const performRemove = (id: string) => {
    if (id === tree.id) return
    remember()
    const { orphaned } = removalImpact(id)
    const parent = findParent(tree, id)
    const next = parent
      ? updateElement(tree, parent.id, (copy) => {
          copy.children = (copy.children || []).filter((c) => c.id !== id)
        })
      : tree
    const nextAssignments = { ...assignments }
    for (const m of orphaned) delete nextAssignments[m.id]
    onChange(next, nextAssignments)
  }

  // Deleting a container takes its whole subtree AND drops the phase assignment
  // of every date inside it. Undo can put that back, but only for someone who
  // registered what went -- on a thirty-element model the difference between
  // removing one Boundary and removing a branch is not visible after the fact,
  // which is what the count in the dialog is for. The same ConfirmDialog guards
  // deleting a project, an analysis and a stored date elsewhere in the app.
  //
  // Only when there is something to lose. Removing an empty Boundary is the
  // common case and stays one click; a dialog on it would train the reader to
  // dismiss the one that matters.
  const pendingElement = pendingRemoval ? findElement(tree, pendingRemoval) : null
  const pendingImpact = pendingRemoval
    ? removalImpact(pendingRemoval)
    : { descendants: 0, orphaned: [] }

  const requestRemove = (id: string) => {
    if (id === tree.id) return
    const { descendants, orphaned } = removalImpact(id)
    if (descendants === 0 && orphaned.length === 0) {
      performRemove(id)
      return
    }
    setPendingRemoval(id)
  }

  /**
   * Change how deeply an element is nested, from the keyboard.
   *
   * Routed through commitDrop with a synthetic horizontal offset of exactly one
   * indent step, so the keyboard and the pointer share one set of legality
   * rules: getProjection's clamp decides what is reachable, and a key press can
   * no more produce an illegal tree than a drag can. Returns false when the
   * clamp refuses, so the caller can leave the row where it is.
   *
   * Without this a keyboard user could add children and reorder siblings but
   * never move an element to a different parent -- the one operation this
   * editor exists for. KeyboardSensor was registered, which looked like
   * support, but depth comes only from a horizontal drag delta and a vertical
   * list's keyboard sensor produces none.
   */
  /** Where an element now sits, in words: its depth and what contains it. */
  const describePosition = (root: ModelElement, id: string): string => {
    const node = flattenTree(root).find((n) => n.id === id)
    if (!node) return ''
    const parent = node.ancestors[node.ancestors.length - 1]
    const name = node.element.name || TYPE_LABEL[node.element.element_type]
    const parentName = parent.name || TYPE_LABEL[parent.element_type]
    const siblings = (parent.children || []).length
    const index = (parent.children || []).findIndex((c) => c.id === id) + 1
    return `${name}, ${index} of ${siblings} in ${parentName}, level ${node.depth}`
  }

  const renest = (id: string, direction: 1 | -1): boolean => {
    const next = commitDrop(tree, flat, id, id, direction * INDENT_PX)
    if (next) remember()
    if (!next) {
      setAnnouncement(
        direction === 1
          ? 'Cannot nest any deeper here'
          : 'Cannot move any further out here'
      )
      return false
    }
    // Indenting makes this element a child of the row above it, and if that row
    // is collapsed the element would vanish at the moment it moved. Only that
    // container is opened -- clearing the whole collapse set would throw away
    // every other one the reader had closed, for a move that touched one
    // branch.
    const landedIn = flattenTree(next).find((n) => n.id === id)?.ancestors.slice(-1)[0]
    if (landedIn) {
      setCollapsed((prev) => {
        if (!prev.has(landedIn.id)) return prev
        const nextSet = new Set(prev)
        nextSet.delete(landedIn.id)
        return nextSet
      })
    }
    setAnnouncement(describePosition(next, id))
    onChange(next, assignments)
    return true
  }

  const move = (id: string, delta: number) => {
    const parent = findParent(tree, id)
    if (!parent) return
    const kids = parent.children || []
    const index = kids.findIndex((c) => c.id === id)
    const target = index + delta
    if (index < 0 || target < 0 || target >= kids.length) return
    remember()
    // Only the parent's path is copied; the two swapped rows keep their own
    // element objects and redraw because their position among their
    // siblings changed, not because the tree did.
    const next = updateElement(tree, parent.id, (copy) => {
      const swapped = [...kids]
      ;[swapped[index], swapped[target]] = [swapped[target], swapped[index]]
      copy.children = swapped
    })
    setAnnouncement(describePosition(next, id))
    onChange(next, assignments)
  }

  // The three per-field edits go through updateElement rather than update:
  // they are the ones fired per keystroke, and the ones where copying the
  // whole tree cost every row a re-render.
  const rename = (id: string, name: string) => {
    onChange(updateElement(tree, id, (el) => { el.name = name }), assignments)
  }

  const setLimit = (id: string, field: 'tpq_age_bp' | 'taq_age_bp', value: string) => {
    onChange(
      updateElement(tree, id, (el) => {
        el[field] = value.trim() === '' ? null : Number(value)
      }),
      assignments
    )
  }

  // Merged rather than replaced: a control edits one setting and must leave
  // the rest of that element's params where they were.
  const setParam = (id: string, patch: ElementParams) => {
    onChange(
      updateElement(tree, id, (el) => {
        el.params = { ...(el.params || {}), ...patch }
      }),
      assignments
    )
  }

  const applyPreset = (type: 'wiggle_sequence' | 'deposition_sequence') => {
    remember()
    const ids = measurements.map((m) => m.id)
    const built =
      type === 'wiggle_sequence' ? wiggleMatchTree(ids) : depositionSequenceTree(ids)
    onChange(built.tree, built.assignments)
  }

  /**
   * Replace the model with one site per distinct value of a column the dates
   * carry -- a bracketed Sequence each, so the dispersal models can read them
   * as sites. See utils/siteGrouping for the shape and why it is that one.
   */
  const applyGrouping = (by: SiteGroupingKey) => {
    remember()
    const built = siteModelTree(measurements, by)
    onChange(built.tree, built.assignments)
  }

  // What each key would make of these dates, so the menu can say before the
  // model is replaced -- and so a key with nothing to group on is disabled
  // rather than offered and then producing one phase of unassigned dates.
  const groupingOptions = useMemo(
    () =>
      (
        [
          ['location', 'Location'],
          ['context', 'Context'],
          ['position', 'Position (within 5 km)'],
        ] as Array<[SiteGroupingKey, string]>
      ).map(([key, label]) => ({ key, label, ...groupingPreview(measurements, key) })),
    [measurements],
  )
  const groupingLabel: Record<SiteGroupingKey, string> = {
    location: 'location',
    context: 'context',
    position: 'position',
  }

  /**
   * Apply a proposed spatial order: permute the named elements into it.
   *
   * Through the same `remember()` undo the presets use, because this changes
   * the model and the analyst should be able to take it back after seeing what
   * it looks like. The permutation itself is utils/spatialOrder, which leaves
   * every Boundary where it was -- a spanning tree is a claim about which
   * sites are adjacent, not about how the model should be structured.
   */
  const applySpatialOrder = (orderedIds: string[]) => {
    const next = reorderToProposal(tree, orderedIds)
    if (next === tree) return
    remember()
    onChange(next, assignments)
  }

  const assign = (sampleId: string, elementId: string) => {
    remember()
    onChange(tree, { ...assignments, [sampleId]: elementId })
  }

  // The common case: a boundary before and after every phase in a sequence,
  // which is what makes each phase's start and end reportable. Adjacent phases
  // share the boundary between them.
  const bracketPhases = () => {
    remember()
    update((draft) => {
      walk(draft, null, (el) => {
        if (el.element_type !== 'sequence') return
        const out: ModelElement[] = []
        for (const child of el.children || []) {
          const previous = out[out.length - 1]
          if (child.element_type === 'phase' && previous?.element_type !== 'boundary') {
            out.push({
              id: newElementId(), element_type: 'boundary', name: 'Boundary',
              tpq_age_bp: null, taq_age_bp: null, children: [],
            })
          }
          out.push(child)
        }
        if (out.length && out[out.length - 1].element_type === 'phase') {
          out.push({
            id: newElementId(), element_type: 'boundary', name: 'Boundary',
            tpq_age_bp: null, taq_age_bp: null, children: [],
          })
        }
        el.children = out
      })
    })
  }

  const flat = useMemo(() => flattenTree(tree), [tree])

  // What the outline actually draws. `flat` stays complete because the drag
  // logic rebuilds the whole tree from it -- handing it a filtered list would
  // silently delete every hidden element on the next drop.
  const visible = useMemo(
    () => (collapsed.size === 0 ? flat : flat.filter((n) => !n.ancestors.some((a) => collapsed.has(a.id)))),
    [flat, collapsed]
  )

  // The first paint draws the top of the outline; the rest arrives in a
  // transition, which React renders in slices that yield to input. Mounting
  // all 275 rows of an 800-date model in one pass held the thread for 1.5s
  // with nothing on screen; now the first forty are up in a fraction of
  // that, and the remainder fill in below the fold without blocking a click
  // on the ones already there. The drag logic reads `flat`, never this list,
  // so a partially drawn outline cannot lose a row on a drop.
  //
  // In chunks rather than all at once: the render of a transition is sliced
  // but its commit is not, and committing 230 rows in one go was a 750ms
  // task of its own. A chunk commits in well under 200ms, and the next one
  // is asked for only once the last is on screen.
  const [renderLimit, setRenderLimit] = useState(FIRST_PAINT_ROWS)
  useEffect(() => {
    if (renderLimit >= flat.length) return
    startTransition(() => setRenderLimit((limit) => limit + MOUNT_CHUNK_ROWS))
  }, [renderLimit, flat.length])
  const rendered = renderLimit >= visible.length ? visible : visible.slice(0, renderLimit)

  // How many elements a collapsed container is holding, so its row can say so
  // rather than just hiding them.
  const hiddenCount = useMemo(() => {
    const counts = new Map<string, number>()
    for (const id of collapsed) {
      counts.set(id, flat.filter((n) => n.ancestors.some((a) => a.id === id)).length)
    }
    return counts
  }, [flat, collapsed])

  // A date with no phase, or one pointing at an element that has since been
  // deleted -- both are "not in the model" and both belong in the panel that
  // exists to clear them.
  const unassigned = useMemo(() => {
    const phaseIds = new Set(phaseOptions(tree).map((p) => p.id))
    return measurements.filter((m) => !phaseIds.has(assignments[m.id] || ''))
  }, [measurements, assignments, tree])

  const toggleCollapsed = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const parentMap = useMemo(() => {
    const map = new Map<string, ModelElement>()
    walk(tree, null, (el, parent) => {
      if (parent) map.set(el.id, parent)
    })
    return map
  }, [tree])

  const activeElement = activeId ? flat.find((n) => n.id === activeId)?.element ?? null : null
  const draggedIds = useMemo(
    () => (activeId ? subtreeIds(flat, activeId) : new Set<string>()),
    [flat, activeId]
  )

  /** Name of whatever container the block would land in, for the indicator's
   * tag. The root is not in `flat` -- it is rendered above the list. */
  const containerName = (id: string): string => {
    if (id === tree.id) return tree.name || TYPE_LABEL[tree.element_type]
    const found = flat.find((n) => n.id === id)
    return found ? found.element.name || TYPE_LABEL[found.element.element_type] : 'here'
  }

  const project = (dragged: string, over: string | null, offsetX: number) => {
    if (over) lastOverIdRef.current = over
    const next = getProjection(
      flat,
      dragged,
      over ?? lastOverIdRef.current ?? dragged,
      offsetX,
      tree.id,
      projectionRef.current?.depthDelta
    )
    projectionRef.current = next
    // Returning the previous object when the placement is unchanged lets React
    // bail out of the re-render.
    setProjection((prev) => (samePlacement(prev, next) ? prev : next))
  }

  const endDrag = () => {
    setActiveId(null)
    setProjection(null)
    projectionRef.current = null
    lastOverIdRef.current = null
    // Put back what the drag expanded. Ids that no longer exist are dropped,
    // and the moved element's own ancestors are left open so the block does
    // not vanish into a container the moment it lands.
    const before = collapsedBeforeDragRef.current
    collapsedBeforeDragRef.current = null
    if (before && before.size > 0) {
      setCollapsed((current) => {
        const live = new Set<string>()
        walk(tree, null, (el) => {
          if (before.has(el.id)) live.add(el.id)
        })
        return live.size === current.size && [...live].every((id) => current.has(id))
          ? current
          : live
      })
    }
  }

  const handleDragStart = (event: DragStartEvent) => {
    const id = String(event.active.id)
    // Expand everything for the duration of the drag. The projection and the
    // drop both work on the complete flat list, so a collapsed container would
    // put rows in that list with nothing on screen to represent them -- the
    // indicator would name a landing place the reader cannot see, and offsets
    // would count rows that are not there.
    collapsedBeforeDragRef.current = collapsed
    if (collapsed.size > 0) setCollapsed(new Set())
    setActiveId(id)
    projectionRef.current = null
    lastOverIdRef.current = null
    project(id, id, 0)
  }

  const handleDragMove = (event: DragMoveEvent) => {
    project(String(event.active.id), event.over ? String(event.over.id) : null, event.delta.x)
  }

  const handleDragEnd = (event: DragEndEvent) => {
    const dragged = String(event.active.id)
    // The projection the indicator was LAST SHOWING, not one recomputed from
    // the drop event. The pointer cannot move between the final move and the
    // release, so recomputing learns nothing -- but the two can disagree when
    // dnd-kit resolves a different `over` at release than during the drag, and
    // the block would then land somewhere other than the line the user was
    // looking at. Committing what was displayed makes that promise structural.
    const landing = projectionRef.current
    endDrag()
    if (!landing) return
    const nextTree = applyProjection(tree, flat, dragged, landing)
    if (nextTree) {
      remember()
      onChange(nextTree, assignments)
    }
  }

  const handleDragCancel = () => {
    endDrag()
  }

  // The editor's own element, so the shortcut below can tell a key press
  // aimed at this outline from one aimed at something else on the page.
  const rootRef = useRef<HTMLDivElement | null>(null)
  // The shortcut fires from a listener whose identity must not change between
  // renders, so it reads the current undo through a ref rather than closing
  // over one and re-subscribing on every keystroke.
  const undoRef = useRef(undo)
  undoRef.current = undo

  /**
   * Ctrl/Cmd+Z, listened for on the window.
   *
   * It used to be a React onKeyDown on the row list, which only ever sees
   * keys pressed while focus is INSIDE that list -- and the moment that
   * matters most is exactly when focus is not: clicking a row button (Add
   * TPQ/TAQ, Delete, Bracket phases) either moves focus to a control that
   * then unmounts or leaves it on a button outside the list, so focus sits on
   * <body> and the handler never ran.
   *
   * Text fields are left alone. Renames and TPQ/TAQ values are deliberately
   * absent from the history (see historyRef), so inside a field the browser's
   * own undo is the correct and only useful one; taking the key there would
   * throw away the character the user meant to take back and step the whole
   * outline instead.
   */
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== 'z') return
      // Shift+Cmd+Z is Redo, which this editor does not have. Claiming the
      // chord to do nothing is worse than leaving it unhandled.
      if (e.shiftKey) return
      const target = e.target as HTMLElement | null
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target?.isContentEditable
      ) {
        return
      }
      // Anything focused elsewhere on the page keeps its own Ctrl+Z; only
      // this editor's own keys, and the unfocused case, belong to us.
      if (target && target !== document.body && !rootRef.current?.contains(target)) return
      e.preventDefault()
      undoRef.current()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  // Walks the entire tree and every measurement. Called bare in the render
  // body it ran on each keystroke, alongside the grouping above.
  const problems = useMemo(
    () => validateTree(tree, assignments, measurements),
    [tree, assignments, measurements]
  )

  // Every callback a row is handed, made stable -- see useEvent. The rows are
  // memoised (sameRow), and one fresh function among their props is enough to
  // re-render all of them.
  const rowToggleCollapsed = useEvent(toggleCollapsed)
  const rowRename = useEvent(rename)
  const rowSetParam = useEvent(setParam)
  const rowSetOrdinate = useEvent(
    (measurementId: string, patch: Partial<MatrixMeasurement>) => onSetOrdinate?.(measurementId, patch)
  )
  const rowSetLimit = useEvent(setLimit)
  const rowOpenLimiters = useEvent(showLimiters)
  const rowMoveUp = useEvent((id: string) => move(id, -1))
  const rowMoveDown = useEvent((id: string) => move(id, 1))
  const rowIndent = useEvent((id: string) => renest(id, 1))
  const rowOutdent = useEvent((id: string) => renest(id, -1))
  const rowAdd = useEvent((id: string, e: React.MouseEvent<HTMLElement>) =>
    setAddMenu({ anchor: e.currentTarget, targetId: id })
  )
  const rowDelete = useEvent(requestRemove)
  const rowOpenPhaseMenu = useEvent((measurementId: string, anchor: HTMLElement) =>
    setPhaseMenu({ anchor, measurementId })
  )
  const sortableIds = useStableArray(flat.map((n) => n.id))

  return (
    <Box ref={rootRef}>
      {/* Everything this page is READ from, on the band the diagram tab
          introduces itself with too -- see GuideBand. */}
      <GuideBand>
        {/* The one fact worth restating on every visit, because it is the one
            that is easy to hold backwards -- and the one the diagram tab
            states the other way round. */}
        <Typography variant="body2" color="textSecondary" sx={{ mb: 1.5 }}>
          Build the model using nested containers of the following types, arranged oldest first.
        </Typography>

        {/* The vocabulary as a KEY.
          
            Each entry carries the same 4px coloured bar the rows carry, so the
            key shows what it is describing instead of naming it: a reader who
            meets "Phase" here recognises the green edge on every phase row, and
            the guides those rows hang from.
           */}
        <Box
          component="dl"
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
            gap: 1,
            m: 0,
            // Half a step, not two: the band spaces its own contents, and
            // the disclosure below carries 44px of hit target of its own.
            mb: 0.5,
          }}
        >
          {OFFERED_TYPES.map((type) => (
            <Box key={type} sx={{ display: 'flex', gap: 1, alignItems: 'baseline' }}>
              <Box
                aria-hidden
                sx={{
                  flexShrink: 0,
                  width: '4px',
                  alignSelf: 'stretch',
                  borderRadius: 1,
                  backgroundColor: typeColor[type].main,
                }}
              />
              <Box>
                <Box
                  component="dt"
                  sx={{
                    display: 'inline',
                    color: typeColor[type].ink,
                    fontSize: '0.75rem',
                    fontWeight: 500,
                  }}
                >
                  {TYPE_LABEL[type]}
                </Box>{' '}
                <Typography component="dd" variant="caption" color="text.secondary" sx={{ display: 'inline', m: 0 }}>
                  {TYPE_HINT[type]}
                </Typography>
              </Box>
            </Box>
          ))}
        </Box>

        <Accordion
          disableGutters
          elevation={0}
          sx={{
            backgroundColor: 'transparent',
            '&:before': { display: 'none' },
          }}
        >
          <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ minHeight: 44, px: 0 }}>
            <Typography variant="body2">How to reorder, nest and constrain elements</Typography>
          </AccordionSummary>
          <AccordionDetails sx={{ px: 0, pt: 0 }}>
            <Typography variant="body2" color="text.secondary" paragraph>
              Drag the handle on the left to reorder or renest an element — drag sideways to change
              how deeply it is nested. The blue line shows where it will land, and which container it
              will land in. From the keyboard, focus a handle and use the arrow keys: up and down
              move an element among its neighbours, left and right change how deeply it is nested.
            </Typography>
            <Typography variant="body2" color="text.secondary" paragraph>
              Nest Sequences inside a Phase for parallel branches, and put a Hiatus between two
              Boundaries for a deliberate gap. A Boundary between two Phases is a single shared
              parameter, giving one transition date rather than two independent ones.
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 0 }}>
              TPQ (terminus post quem) is the earliest date an event could have occurred, and TAQ
              (terminus ante quem) is the latest. Only use these if you know this information, and
              enter them in years BP.
            </Typography>
          </AccordionDetails>
        </Accordion>
      </GuideBand>

      {/* Actions, right-aligned on the white working surface -- below the rule
          with the model they act on, not above it with the material that
          explains it. */}
      <Box
        sx={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 1, mb: 2, flexWrap: 'wrap' }}
      >
        <Tooltip title={undoDepth > 0 ? 'Undo the last structural change (Ctrl+Z / ⌘Z)' : 'Nothing to undo'}>
          <span>
            <Button
              size="small"
              startIcon={<UndoIcon />}
              onClick={undo}
              disabled={undoDepth === 0}
              sx={{ minHeight: 44, flexShrink: 0 }}
            >
              Undo
            </Button>
          </span>
        </Tooltip>
        <Tooltip title="Adds a Boundary before and after each group inside every Sequence in this model">
          <Button
            size="small"
            startIcon={<AutoFixHighIcon />}
            onClick={bracketPhases}
            sx={{ minHeight: 44, flexShrink: 0 }}
          >
            Bracket phases with boundaries
          </Button>
        </Tooltip>
        {/* One menu for everything that derives a structure from what the
            dates already carry -- a site column, coordinates, ring counts,
            depths. These were four text buttons in a row, each wearing the
            same wand icon as the one beside it, so the icon said nothing and
            the bar wrapped to two lines at tablet width before the model was
            in view. Each is a rare, whole-model action; together they are
            one decision, "start from the data", and the menu's captions
            carry what the tooltips used to.

            Three of the four REPLACE the model and are confirmed like a
            deletion; the map proposal is the exception, opening a dialog
            that changes nothing until accepted. */}
        <Tooltip
          title={
            measurements.length === 0
              ? 'Add dates first — these build a structure from what the dates carry'
              : 'Build a structure from what the dates carry: a site column, their positions, ring counts or depths'
          }
        >
          <span>
            <Button
              size="small"
              endIcon={<ArrowDropDownIcon />}
              onClick={(e) => setBuildMenu(e.currentTarget)}
              disabled={measurements.length === 0}
              aria-haspopup="menu"
              aria-expanded={buildMenu ? true : undefined}
              sx={{ minHeight: 44, flexShrink: 0 }}
            >
              Build from data
            </Button>
          </span>
        </Tooltip>
        <Menu
          open={!!buildMenu}
          anchorEl={buildMenu}
          onClose={() => setBuildMenu(null)}
          MenuListProps={{ 'aria-label': 'Build the model from data' }}
        >
          <ListSubheader disableSticky sx={{ lineHeight: '32px' }}>
            Group dates into sites
          </ListSubheader>
          {groupingOptions.map((option) => (
            <MenuItem
              key={option.key}
              disabled={option.sites === 0}
              onClick={() => {
                setBuildMenu(null)
                setPendingGrouping(option.key)
              }}
            >
              <Box>
                <Typography variant="body2">By {option.label.toLowerCase()}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {option.sites === 0
                    ? 'No date carries one'
                    : `${option.sites} site${option.sites === 1 ? '' : 's'}` +
                      (option.unassigned > 0
                        ? `, ${option.unassigned} date${option.unassigned === 1 ? '' : 's'} without one`
                        : '')}
                </Typography>
              </Box>
            </MenuItem>
          ))}
          <Divider />
          <MenuItem
            onClick={() => {
              setBuildMenu(null)
              setSpatialOrderOpen(true)
            }}
          >
            <Box>
              <Typography variant="body2">Propose an order from the map</Typography>
              <Typography variant="caption" color="text.secondary">
                Read off the sites' positions, for you to accept or reject
              </Typography>
            </Box>
          </MenuItem>
          <Divider />
          <MenuItem
            onClick={() => {
              setBuildMenu(null)
              setPendingPreset('wiggle_sequence')
            }}
          >
            <Box>
              <Typography variant="body2">Make it a wiggle match</Typography>
              <Typography variant="caption" color="text.secondary">
                Every date in one series separated by counted rings or varves
              </Typography>
            </Box>
          </MenuItem>
          <MenuItem
            onClick={() => {
              setBuildMenu(null)
              setPendingPreset('deposition_sequence')
            }}
          >
            <Box>
              <Typography variant="body2">Make it an age-depth model</Typography>
              <Typography variant="caption" color="text.secondary">
                Every date in one deposition sequence, ordered by depth
              </Typography>
            </Box>
          </MenuItem>
        </Menu>
      </Box>

      {/* Mounted only once asked for, so the lazy chunk is fetched on the
          click rather than with the page. */}
      {spatialOrderOpen && (
        <Suspense fallback={null}>
          <SpatialOrderDialog
            open
            tree={tree}
            // With the phase assignment MERGED IN. The draft keeps
            // `assignments` beside the measurements rather than on them, so a
            // date here carries a position but no element_id -- and
            // derive_sites groups dates by element_id, so every date would
            // look unassigned and the proposal would refuse for having no
            // placed sites, on a model where every date is placed. Found by
            // driving the real app.
            measurements={measurements.map((m) => ({
              ...m,
              element_id: assignments[m.id] || m.element_id || m.group_id || null,
            }))}
            onClose={() => setSpatialOrderOpen(false)}
            onApply={applySpatialOrder}
            tiles={tiles}
          />
        </Suspense>
      )}

      <ConfirmDialog
        open={pendingPreset !== null}
        title={
          pendingPreset === 'wiggle_sequence'
            ? 'Replace the model with a wiggle match?'
            : 'Replace the model with an age-depth model?'
        }
        message={
          pendingPreset === 'wiggle_sequence'
            ? `Every date goes into one wiggle-matched series and the rest of the structure is discarded. You then give each date its gap — the button in the series does that from ring numbers. Undo restores what is there now.`
            : `Every date goes into one deposition sequence, ordered by the depth each was taken from, and the rest of the structure is discarded. Dates without a depth will need one. Undo restores what is there now.`
        }
        confirmLabel="Replace"
        onCancel={() => setPendingPreset(null)}
        onConfirm={() => {
          if (pendingPreset) applyPreset(pendingPreset)
          setPendingPreset(null)
        }}
      />

      <ConfirmDialog
        open={pendingGrouping !== null}
        title={`Group the dates into sites by ${pendingGrouping ? groupingLabel[pendingGrouping] : ''}?`}
        message={
          pendingGrouping
            ? (() => {
                const option = groupingOptions.find((o) => o.key === pendingGrouping)!
                return (
                  `Every date goes into a Sequence for its ${groupingLabel[pendingGrouping]} — ` +
                  `${option.sites} site${option.sites === 1 ? '' : 's'}, each bracketed by its own ` +
                  `start and end Boundary, inside one unordered phase — and the rest of the structure ` +
                  `is discarded. No order between the sites is asserted; propose one from the map ` +
                  `afterwards if you want it.` +
                  (option.unassigned > 0
                    ? ` ${option.unassigned} date${option.unassigned === 1 ? ' has' : 's have'} no ` +
                      `${groupingLabel[pendingGrouping]} and will sit in an unassigned phase beside the sites.`
                    : '') +
                  ' Undo restores what is there now.'
                )
              })()
            : ''
        }
        confirmLabel="Replace"
        onCancel={() => setPendingGrouping(null)}
        onConfirm={() => {
          if (pendingGrouping) applyGrouping(pendingGrouping)
          setPendingGrouping(null)
        }}
      />

      {/* error, not warning: every line here blocks Save, and the page says
          so in error red beside the button -- one fact was wearing two
          colours. Capped, because deleting a phase that held thirty dates
          produced thirty near-identical lines and pushed the outline the
          reader needs to fix them below the fold. */}
      {problems.length > 0 && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {problems.slice(0, SHOWN_PROBLEMS).map((p, i) => (
            <div key={i}>{p}</div>
          ))}
          {problems.length > SHOWN_PROBLEMS && (
            <Typography variant="body2" sx={{ mt: 0.5, fontWeight: 500 }}>
              and {problems.length - SHOWN_PROBLEMS} more
            </Typography>
          )}
        </Alert>
      )}

      <Box sx={{ mt: 1 }}>
        <Paper
          variant="outlined"
          sx={{
            p: 2,
            borderLeft: `4px solid ${typeColor[tree.element_type].main}`,
            ...(projection?.parentId === tree.id
              ? { borderColor: 'primary.main', boxShadow: (theme) => `0 0 0 1px ${theme.palette.primary.main}` }
              : {}),
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <ElementFields
              element={tree}
              typeColor={typeColor}
              onRename={(name) => rename(tree.id, name)}
              onSetParam={(patch) => setParam(tree.id, patch)}
              onSetLimit={(field, value) => setLimit(tree.id, field, value)}
              limitersOpen={openLimiters.has(tree.id)}
              onOpenLimiters={() => showLimiters(tree.id)}
            />
            <Tooltip title={`Add inside ${tree.name || TYPE_LABEL[tree.element_type]}`}>
              <IconButton
                size="small"
                aria-label={`Add inside ${tree.name || TYPE_LABEL[tree.element_type]}`}
                onClick={(e) => setAddMenu({ anchor: e.currentTarget, targetId: tree.id })}
                // Right-aligned, the way RowActions aligns itself on every
                // other row. Left where it fell, this button sat immediately
                // after the Add TPQ/TAQ caption and read as part of that
                // control rather than as the row's own action.
                sx={{ ...HIT_TARGET, flexShrink: 0, ml: 'auto' }}
              >
                <AddIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Box>
          {/* The root's own dates. Both presets and the OxCal importer make a
              wiggle match or a deposition sequence the ROOT, and the outline
              below renders only what is nested under it -- so without this the
              dates of the commonest shape of either model appear nowhere. */}
          <ElementDates
            element={tree}
            dates={datesIn(tree.id)}
            childCount={(tree.children || []).length}
            onOpenPhaseMenu={rowOpenPhaseMenu}
            onSetOrdinate={onSetOrdinate}
            onOpenRingHelper={setRingHelperFor}
          />
        </Paper>
      </Box>

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={handleDragStart}
        onDragMove={handleDragMove}
        onDragEnd={handleDragEnd}
        onDragCancel={handleDragCancel}
      >
        {/* A no-op strategy: dnd-kit still tracks which row the pointer is
            over, but applies no transform, so nothing shifts under the
            cursor mid-drag. */}
        <SortableContext items={sortableIds} strategy={NO_STRATEGY}>
          {/* A list, not role="tree". A treeitem is expected to be a single
              focusable unit named by its own content; these rows each hold a
              name field, two number fields and up to five buttons, so calling
              them treeitems would describe a widget that is not what is here.
              `listitem` carries aria-level just as `treeitem` does, which is
              the part that was actually missing: nesting was expressed only as
              a left margin, so assistive technology heard a flat run of text
              fields with no depth at all. */}
          <Box role="list" aria-label="Model structure outline">
          {rendered.map((node) => {
            const parent = parentMap.get(node.id)
            const siblings = parent?.children || []
            const index = siblings.findIndex((s) => s.id === node.id)
            const container = node.ancestors[node.ancestors.length - 1]
            return (
              <SortableRow
                key={node.id}
                id={node.id}
                level={node.depth}
                ariaLabel={`${TYPE_LABEL[node.element.element_type]} ${node.element.name || ''}, level ${
                  node.depth
                }, inside ${container.name || TYPE_LABEL[container.element_type]}`}
              >
              <TreeRow
                node={node}
                onOpenPhaseMenu={rowOpenPhaseMenu}
                childCount={(node.element.children || []).length}
                hiddenDescendants={hiddenCount.get(node.id) ?? 0}
                isCollapsed={collapsed.has(node.id)}
                onToggleCollapsed={rowToggleCollapsed}
                typeColor={typeColor}
                dates={datesIn(node.id)}
                canMoveUp={index > 0}
                canMoveDown={index >= 0 && index < siblings.length - 1}
                indicator={
                  projection && projection.insertBeforeId === node.id
                    ? { depth: projection.depth, label: `into ${containerName(projection.parentId)}` }
                    : null
                }
                isDropTarget={!!projection && projection.parentId === node.id}
                isDragged={draggedIds.has(node.id)}
                onRename={rowRename}
                onSetParam={rowSetParam}
                onSetOrdinate={onSetOrdinate ? rowSetOrdinate : undefined}
                onOpenRingHelper={setRingHelperFor}
                onSetLimit={rowSetLimit}
                limitersOpen={openLimiters.has(node.id)}
                onOpenLimiters={rowOpenLimiters}
                onMoveUp={rowMoveUp}
                onMoveDown={rowMoveDown}
                onIndent={rowIndent}
                onOutdent={rowOutdent}
                onAdd={rowAdd}
                onDelete={rowDelete}
              />
              </SortableRow>
            )
          })}
          </Box>
          {/* Landing at the very end has no following row to hang the line
              above, so it gets a zero-height slot of its own. Outside the list:
              it is a drop affordance, not an item in the model. */}
          {projection && projection.insertBeforeId === null && (
            <Box sx={{ position: 'relative', mt: 1, height: 0 }}>
              <DropIndicator
                depth={projection.depth}
                label={`into ${containerName(projection.parentId)}`}
              />
            </Box>
          )}
        </SortableContext>
        <DragOverlay>
          {activeElement && (
            <Paper
              sx={{
                p: 2,
                borderLeft: `4px solid ${typeColor[activeElement.element_type].main}`,
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                boxShadow: 4,
                opacity: 0.95,
              }}
            >
              <DragIndicatorIcon fontSize="small" sx={{ color: 'text.secondary' }} />
              <Box
                component="span"
                sx={{
                  color: typeColor[activeElement.element_type].ink,
                  fontSize: '0.75rem',
                  fontWeight: 500,
                }}
              >
                {TYPE_LABEL[activeElement.element_type]}
              </Box>
              <Typography variant="body2">{activeElement.name}</Typography>
            </Paper>
          )}
        </DragOverlay>
      </DndContext>

      {/* Flat, not elevated: this sits inside Stratigraphy's already-elevated
          Paper, and DESIGN.md draws that boundary with a line rather than
          stacking a second shadow inside the first (the ImportDatesDialog
          precedent). p: 3 is the house padding for a content Paper. */}
      <Paper variant="outlined" elevation={0} sx={{ p: 3, mt: 3 }}>
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={1}
          alignItems={{ sm: 'center' }}
          justifyContent="space-between"
          sx={{ mb: 1 }}
        >
          <Typography variant="subtitle1" component="h2">
            Unassigned dates
          </Typography>
          {onSortDates && (
            <SortDatesButton measurements={measurements} onSort={onSortDates} />
          )}
        </Stack>
        {measurements.length === 0 ? (
          <Typography variant="body2" color="textSecondary">
            No dates in this analysis yet.
          </Typography>
        ) : unassigned.length === 0 ? (
          <Typography variant="body2" color="textSecondary">
            Every date is in a phase. Change one from the phase it is listed under above.
          </Typography>
        ) : (
          unassigned.map((m) => (
            // wrap: the label and the select each have a 220px minimum, so
            // held to one unbroken row the pair needs ~490px -- wider than a
            // phone viewport, forcing a sideways scroll to reach the select.
            <Box key={m.id} sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 2, mb: 1 }}>
              <Typography variant="body2" sx={{ minWidth: 220, fontVariantNumeric: 'tabular-nums' }}>
                {m.sample_name} — {m.age_bp} ± {m.error_1sigma ?? '?'} BP
              </Typography>
              <PhaseButton
                label="Unassigned"
                ariaLabel={`Phase for ${m.sample_name}`}
                onOpen={(anchor) => setPhaseMenu({ anchor, measurementId: m.id })}
              />
            </Box>
          ))
        )}
      </Paper>

      {/* The phase picker, rendered once for every date on the page -- see
          PhaseButton for why it is not a Select per date. */}
      <Menu
        open={phaseMenu !== null}
        anchorEl={phaseMenu?.anchor ?? null}
        onClose={() => setPhaseMenu(null)}
        MenuListProps={{ role: 'listbox', dense: true }}
      >
        {[{ id: '', label: 'Unassigned' }, ...phaseChoices].map((choice) => {
          const current = phaseMenu ? assignments[phaseMenu.measurementId] || '' : ''
          return (
            <MenuItem
              key={choice.id || '__unassigned'}
              role="option"
              selected={choice.id === current}
              aria-selected={choice.id === current}
              onClick={() => {
                if (phaseMenu) assign(phaseMenu.measurementId, choice.id)
                setPhaseMenu(null)
              }}
            >
              {choice.id ? choice.label : <em>{choice.label}</em>}
            </MenuItem>
          )
        })}
      </Menu>

      {/* The converter, rendered once for the whole outline rather than per
          row: only one series can be being converted at a time, and a dialog
          per wiggle match would mount as many as the model holds. */}
      {ringHelperFor && onSetOrdinate && (
        <RingNumberDialog
          open
          onClose={() => setRingHelperFor(null)}
          members={measurements.filter((m) => assignments[m.id] === ringHelperFor)}
          gapUnits={
            (findElement(tree, ringHelperFor)?.params?.gap_units as string) || 'rings'
          }
          onApply={(offsets, referenceSampleId) => {
            remember()
            for (const [measurementId, offset] of Object.entries(offsets)) {
              onSetOrdinate(measurementId, { ring_offset: offset })
            }
            // Recorded on the element, not only implied by the zero: it says
            // which member the gaps were measured FROM, which is what the
            // results panel names and what a sapwood allowance would later
            // hang off.
            setParam(ringHelperFor, { reference_sample_id: referenceSampleId })
          }}
        />
      )}

      <Menu
        open={!!addMenu}
        anchorEl={addMenu?.anchor}
        onClose={() => setAddMenu(null)}
      >
        {/* Only what THIS target can hold. A deposition sequence takes one
            kind of child -- an undated level on its own depth axis -- and
            offering it a Boundary would be offering something the model
            cannot express. */}
        {childTypesFor(
          (addMenu && findElement(tree, addMenu.targetId)?.element_type) || 'phase'
        ).map((type) => (
          <MenuItem
            key={type}
            onClick={() => {
              if (addMenu) addChild(addMenu.targetId, type)
              setAddMenu(null)
            }}
          >
            <Box>
              <Typography variant="body2">{TYPE_LABEL[type]}</Typography>
              <Typography variant="caption" color="textSecondary">
                {TYPE_HINT[type]}
              </Typography>
            </Box>
          </MenuItem>
        ))}
      </Menu>

      {/* Polite, not assertive: a move is a confirmation of something the
          user just did, not an interruption. Rendered once for the whole
          outline rather than per row, so successive moves replace one message
          instead of queueing thirty. */}
      <Box
        aria-live="polite"
        aria-atomic="true"
        sx={{
          // '1px' as a STRING, not 1. MUI's sx treats a bare number between 0
          // and 1 for width/height as a fraction, so `width: 1` compiled to
          // width:100% -- a 753px visually-hidden box offset 48px from the
          // left, which pushed the whole page into horizontal scroll.
          position: 'absolute',
          width: '1px',
          height: '1px',
          overflow: 'hidden',
          clip: 'rect(0 0 0 0)',
          whiteSpace: 'nowrap',
        }}
      >
        {announcement}
      </Box>

      <ConfirmDialog
        open={!!pendingElement}
        title={`Delete ${pendingElement?.name ?? 'element'}?`}
        message={describeRemoval({
          descendants: pendingImpact.descendants,
          orphaned: pendingImpact.orphaned.length,
        })}
        confirmLabel="Delete"
        danger
        onConfirm={() => {
          if (pendingRemoval) performRemove(pendingRemoval)
          setPendingRemoval(null)
        }}
        onCancel={() => setPendingRemoval(null)}
      />
    </Box>
  )
}
