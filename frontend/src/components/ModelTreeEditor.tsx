import { useMemo, useRef, useState } from 'react'
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
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward'
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward'
import AutoFixHighIcon from '@mui/icons-material/AutoFixHigh'
import DragIndicatorIcon from '@mui/icons-material/DragIndicator'
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
import type { ModelElement, ElementType, MatrixMeasurement } from '@/types'
// Lives in utils because the starting structure built there (see
// bootstrapModelTree) mints ids too, and both must mint the same kind.
// Re-exported so this module stays the one place the editor is imported from.
import { newElementId } from '@/utils/modelTreeDefaults'

export { newElementId }

// Editor for the explicit OxCal-style model structure: a nested, ordered
// tree of Sequences, Phases, Boundaries and Hiatuses. Rendered as an
// indented outline so it reads like a published OxCal model, oldest first
// (top of the list = bottom of the stratigraphy), which is the convention
// both OxCal and the Harris matrix use.
//
// Replaces the old flat "contexts + above/below relationships" editor: a
// Boundary here is placed deliberately and, when it sits between two
// Phases, is a SINGLE shared transition rather than two independent
// per-context boundaries.
//
// Rows are dragged with @dnd-kit rather than the tree being rendered
// recursively: the whole subtree is flattened into an ordered, depth-tagged
// list (matching the outline's own DFS order), which turns "move this
// element to a different nesting level" into an index+depth projection on a
// flat array -- the same technique dnd-kit's own sortable-tree example uses
// -- instead of juggling parent pointers by hand.
//
// The rows themselves DO NOT move while a drag is in progress: the sortable
// strategy is a no-op and no transform is applied, so the outline stays
// exactly where it was and a DropIndicator line shows where the block will
// land instead. An earlier version re-flattened and re-ordered the whole
// list on every pointer move to preview the drop in place, which fought
// dnd-kit's own shifting animation for control of the same rows -- the
// visible result was jitter, and rows changing height/order mid-drag
// invalidated the rects dnd-kit had measured for collision, so a drop could
// land on a stale target or refuse to register at all. A static list also
// suits a TREE better than a list: a gap opening between two rows cannot
// express which of the surrounding levels the block would land at, whereas
// an indented line can.

const TYPE_LABEL: Record<ElementType, string> = {
  sequence: 'Sequence',
  phase: 'Phase',
  boundary: 'Boundary',
  hiatus: 'Hiatus',
}

const TYPE_HINT: Record<ElementType, string> = {
  sequence: 'Children are in chronological order, oldest first',
  phase: 'Children are unordered; dates belong here',
  boundary: 'A transition. Between two phases it is one shared parameter',
  hiatus: 'A deliberate gap; must sit between two boundaries',
}

// One MUI spacing unit (8px) times 3, matching the indent step every depth
// level rendered at before this was flattened -- kept identical so the
// outline doesn't jump when dragging is introduced.
const INDENT_UNITS = 3
export const INDENT_PX = INDENT_UNITS * 8

function isContainerType(type: ElementType): boolean {
  return type === 'sequence' || type === 'phase'
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
 * A phase is only a legal home for dates, so this is what the sample
 * assignment dropdown offers.
 */
export function phaseOptions(root: ModelElement): Array<{ id: string; name: string }> {
  const out: Array<{ id: string; name: string }> = []
  walk(root, null, (el) => {
    if (el.element_type === 'phase') out.push({ id: el.id, name: el.name || 'Phase' })
  })
  return out
}

/**
 * Client-side mirror of the backend's validate_tree, so problems surface
 * while editing rather than on submit. Deliberately the same rules --
 * see services/model_tree.py.
 */
export function validateTree(
  root: ModelElement,
  assignments: Record<string, string>,
  measurements: MatrixMeasurement[]
): string[] {
  const problems: string[] = []
  const seen = new Set<string>()
  const phaseIds = new Set<string>()

  walk(root, null, (el, parent) => {
    if (seen.has(el.id)) problems.push(`Element "${el.name || el.id}" appears more than once`)
    seen.add(el.id)
    if (el.element_type === 'phase') phaseIds.add(el.id)
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
      problems.push(`"${m.sample_name}" is not assigned to a phase`)
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
}

/** Flattens everything under (not including) the root into DFS order. Root
 * itself is depth 0, so its direct children start at depth 1. */
export function flattenTree(root: ModelElement): FlatNode[] {
  const out: FlatNode[] = []
  const rec = (el: ModelElement, depth: number) => {
    for (const child of el.children || []) {
      out.push({ id: child.id, element: child, depth })
      rec(child, depth + 1)
    }
  }
  rec(root, 1)
  return out
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
  /** The depth STEP that produced this (projected minus original depth),
   * fed back into the next call so the chosen step is sticky -- see
   * DEPTH_HYSTERESIS. */
  depthDelta: number
}

// How far past the halfway point between two indent steps the pointer has
// to travel before the projected depth changes. Plain rounding flips at
// exactly .5, so a hand holding still on that boundary would flicker the
// indicator between two levels; this makes the step the pointer is already
// on that much stickier.
const DEPTH_HYSTERESIS = 0.6

/**
 * Given the flat outline, the item being dragged, what it's currently
 * hovering over, and how far it's been dragged horizontally, works out
 * where it would land: how deep, and just before which item. Depth is
 * clamped so a drop can never nest inside a non-container (a boundary or
 * hiatus) and never skip past what the surrounding items structurally
 * allow -- so every candidate drop position is a legal tree, and nothing
 * needs to be rejected at drop time.
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
 * Reinserts the full dragged subtree (descendants included, shifted by the
 * same depth change) at an already-computed projected position. Returns
 * null if the drop didn't actually move anything.
 *
 * Split out from getProjection so the indicator the user is looking at and
 * the tree the drop produces are built from the SAME projection object,
 * rather than each deriving one and hoping they agree.
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
  return rebuildFromFlat(tree, newFlat)
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
 * The line showing where the dragged block will land: indented to the depth
 * it would sit at, tagged with the container it would go into. Absolutely
 * positioned in the gap above a row so that showing it costs no layout --
 * the outline must not move while a drag is in progress.
 */
function DropIndicator({ depth, label }: { depth: number; label: string }) {
  return (
    <Box
      aria-hidden
      sx={{
        position: 'absolute',
        top: -5,
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
          fontSize: '0.7rem',
          bgcolor: 'primary.main',
          color: 'primary.contrastText',
          '& .MuiChip-label': { px: 1 },
        }}
      />
    </Box>
  )
}

interface Props {
  tree: ModelElement
  measurements: MatrixMeasurement[]
  assignments: Record<string, string>
  onChange: (tree: ModelElement, assignments: Record<string, string>) => void
}

interface ElementFieldsProps {
  element: ModelElement
  typeColor: Record<ElementType, ElementTypeColor>
  onRename: (name: string) => void
  onSetLimit: (field: 'tpq_age_bp' | 'taq_age_bp', value: string) => void
  dates: MatrixMeasurement[]
}

function ElementFields({ element, typeColor, onRename, onSetLimit, dates }: ElementFieldsProps) {
  return (
    <>
      <Tooltip title={TYPE_HINT[element.element_type]}>
        <Chip
          size="small"
          label={TYPE_LABEL[element.element_type]}
          sx={{ backgroundColor: typeColor[element.element_type].ink, color: 'white' }}
        />
      </Tooltip>
      {/* No variant prop -- every other field in the app is the outlined
          TextField/Select default; this one used to be the lone exception
          with a bare underline instead of a bordered box, easy to miss as
          an editable field next to the boxed ones surrounding it. */}
      <TextField
        size="small"
        value={element.name || ''}
        onChange={(e) => onRename(e.target.value)}
        sx={{ minWidth: 180 }}
      />
      {(element.element_type === 'phase' || element.element_type === 'sequence') && (
        <>
          <TextField
            size="small"
            type="number"
            label="TPQ"
            value={element.tpq_age_bp ?? ''}
            onChange={(e) => onSetLimit('tpq_age_bp', e.target.value)}
            sx={{ width: 90 }}
          />
          <TextField
            size="small"
            type="number"
            label="TAQ"
            value={element.taq_age_bp ?? ''}
            onChange={(e) => onSetLimit('taq_age_bp', e.target.value)}
            sx={{ width: 90 }}
          />
        </>
      )}
      <Box sx={{ flexGrow: 1 }} />
      {element.element_type === 'phase' && dates.length > 0 && (
        <Typography variant="caption" color="textSecondary">
          {dates.length} date{dates.length === 1 ? '' : 's'}
        </Typography>
      )}
    </>
  )
}

interface RowActionsProps {
  element: ModelElement
  canMoveUp: boolean
  canMoveDown: boolean
  onMoveUp: () => void
  onMoveDown: () => void
  onAdd: (e: React.MouseEvent<HTMLElement>) => void
  onDelete: () => void
}

function RowActions({ element, canMoveUp, canMoveDown, onMoveUp, onMoveDown, onAdd, onDelete }: RowActionsProps) {
  const canContain = isContainerType(element.element_type)
  return (
    <>
      <IconButton size="small" aria-label="Move earlier" disabled={!canMoveUp} onClick={onMoveUp}>
        <ArrowUpwardIcon fontSize="small" />
      </IconButton>
      <IconButton size="small" aria-label="Move later" disabled={!canMoveDown} onClick={onMoveDown}>
        <ArrowDownwardIcon fontSize="small" />
      </IconButton>
      {canContain && (
        <IconButton size="small" aria-label={`Add inside ${element.name}`} onClick={onAdd}>
          <AddIcon fontSize="small" />
        </IconButton>
      )}
      <Tooltip title={`Delete ${element.name}`}>
        <IconButton
          size="small"
          aria-label={`Delete ${element.name}`}
          onClick={onDelete}
          sx={{ '&:hover, &:focus-visible': { color: 'error.main' } }}
        >
          <DeleteIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </>
  )
}

interface TreeRowProps {
  node: FlatNode
  typeColor: Record<ElementType, ElementTypeColor>
  dates: MatrixMeasurement[]
  canMoveUp: boolean
  canMoveDown: boolean
  /** Drawn in the gap above this row when the block would land here. */
  indicator: { depth: number; label: string } | null
  /** This row is the container the block would land inside. */
  isDropTarget: boolean
  /** This row is part of the block being dragged. */
  isDragged: boolean
  onRename: (id: string, name: string) => void
  onSetLimit: (id: string, field: 'tpq_age_bp' | 'taq_age_bp', value: string) => void
  onMoveUp: (id: string) => void
  onMoveDown: (id: string) => void
  onAdd: (id: string, e: React.MouseEvent<HTMLElement>) => void
  onDelete: (id: string) => void
}

function TreeRow({
  node,
  typeColor,
  dates,
  canMoveUp,
  canMoveDown,
  indicator,
  isDropTarget,
  isDragged,
  onRename,
  onSetLimit,
  onMoveUp,
  onMoveDown,
  onAdd,
  onDelete,
}: TreeRowProps) {
  // No transform and no transition: the outline holds still for the whole
  // drag (see the note at the top of this file) and the indicator does the
  // talking. useSortable is still what registers this row as a drop target
  // and gives the drag handle its keyboard support.
  const { attributes, listeners, setNodeRef } = useSortable({ id: node.id })
  const { element, depth } = node

  return (
    <Box ref={setNodeRef} sx={{ position: 'relative', mt: 1 }}>
      {indicator && <DropIndicator depth={indicator.depth} label={indicator.label} />}
      <Box sx={{ ml: depth * INDENT_UNITS, opacity: isDragged ? 0.4 : 1 }}>
      <Paper
        variant="outlined"
        sx={{
          p: 1.5,
          borderLeft: `4px solid ${typeColor[element.element_type].main}`,
          ...(isDropTarget
            ? { borderColor: 'primary.main', boxShadow: (theme) => `0 0 0 1px ${theme.palette.primary.main}` }
            : {}),
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
          <Box
            {...attributes}
            {...listeners}
            aria-label={`Drag ${element.name}`}
            sx={{
              display: 'flex',
              alignItems: 'center',
              color: 'text.secondary',
              cursor: 'grab',
              touchAction: 'none',
              '&:active': { cursor: 'grabbing' },
            }}
          >
            <DragIndicatorIcon fontSize="small" />
          </Box>
          <ElementFields
            element={element}
            typeColor={typeColor}
            onRename={(name) => onRename(node.id, name)}
            onSetLimit={(field, value) => onSetLimit(node.id, field, value)}
            dates={dates}
          />
          <RowActions
            element={element}
            canMoveUp={canMoveUp}
            canMoveDown={canMoveDown}
            onMoveUp={() => onMoveUp(node.id)}
            onMoveDown={() => onMoveDown(node.id)}
            onAdd={(e) => onAdd(node.id, e)}
            onDelete={() => onDelete(node.id)}
          />
        </Box>

        {element.element_type === 'phase' && (
          <Box sx={{ mt: 1, pl: 1 }}>
            {dates.length === 0 ? (
              <Typography variant="caption" color="textSecondary">
                No dates in this phase yet — assign them below.
              </Typography>
            ) : (
              dates.map((m) => (
                <Typography key={m.id} variant="body2" sx={{ fontFamily: 'monospace' }}>
                  {m.sample_name} — {m.age_bp} ± {m.error_1sigma ?? '?'} BP
                </Typography>
              ))
            )}
          </Box>
        )}
      </Paper>
      </Box>
    </Box>
  )
}

export default function ModelTreeEditor({ tree, measurements, assignments, onChange }: Props) {
  const [addMenu, setAddMenu] = useState<{ anchor: HTMLElement; targetId: string } | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  // Where the block would land right now, for the indicator. Only replaced
  // when the LANDING PLACE changes, not on every pointer move -- a drag
  // across one row is hundreds of move events but only a handful of
  // distinct placements, and re-rendering the outline for the rest of them
  // is what a static list is trying to avoid.
  const [projection, setProjection] = useState<Projection | null>(null)
  // The same projection, kept where the drop handler can read it without
  // going through React state. onDragEnd used to read the placement out of
  // state, which meant a drop landing in the same tick as the last move --
  // exactly what a quick flick does -- committed the placement from one
  // event ago, or none at all if the drag had only just started.
  const projectionRef = useRef<Projection | null>(null)
  // The last row the pointer was actually over. dnd-kit reports no target
  // for the odd frame -- while it re-measures, or crossing the gap between
  // two rows -- and treating that as "back where it started" made the line
  // jump home and back for one frame. Holding the last real answer instead
  // keeps it still.
  const lastOverIdRef = useRef<string | null>(null)
  const typeColor = useElementTypeColors()

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )

  const datesIn = (elementId: string) => measurements.filter((m) => assignments[m.id] === elementId)

  const update = (mutate: (draft: ModelElement) => void) => {
    const draft = cloneTree(tree)
    mutate(draft)
    onChange(draft, assignments)
  }

  const addChild = (parentId: string, type: ElementType) => {
    update((draft) => {
      const parent = findElement(draft, parentId)
      if (!parent) return
      parent.children = parent.children || []
      parent.children.push({
        id: newElementId(),
        element_type: type,
        name: type === 'boundary' ? 'Boundary' : type === 'hiatus' ? 'Hiatus' : TYPE_LABEL[type],
        tpq_age_bp: null,
        taq_age_bp: null,
        children: [],
      })
    })
  }

  const remove = (id: string) => {
    if (id === tree.id) return
    const orphaned = measurements.filter((m) => {
      const target = findElement(tree, assignments[m.id] || '')
      if (!target) return false
      let inside = false
      const subtree = findElement(tree, id)
      if (subtree) walk(subtree, null, (el) => { if (el.id === target.id) inside = true })
      return inside
    })
    const draft = cloneTree(tree)
    const parent = findParent(draft, id)
    if (parent) parent.children = (parent.children || []).filter((c) => c.id !== id)
    const nextAssignments = { ...assignments }
    for (const m of orphaned) delete nextAssignments[m.id]
    onChange(draft, nextAssignments)
  }

  const move = (id: string, delta: number) => {
    update((draft) => {
      const parent = findParent(draft, id)
      if (!parent) return
      const kids = parent.children || []
      const index = kids.findIndex((c) => c.id === id)
      const next = index + delta
      if (index < 0 || next < 0 || next >= kids.length) return
      ;[kids[index], kids[next]] = [kids[next], kids[index]]
    })
  }

  const rename = (id: string, name: string) => {
    update((draft) => {
      const el = findElement(draft, id)
      if (el) el.name = name
    })
  }

  const setLimit = (id: string, field: 'tpq_age_bp' | 'taq_age_bp', value: string) => {
    update((draft) => {
      const el = findElement(draft, id)
      if (el) el[field] = value.trim() === '' ? null : Number(value)
    })
  }

  const assign = (sampleId: string, elementId: string) => {
    onChange(tree, { ...assignments, [sampleId]: elementId })
  }

  // Convenience for the common case: put a boundary before and after every
  // phase in a sequence, which is what makes each phase's start and end
  // reportable. Adjacent phases end up sharing the boundary between them.
  const bracketPhases = () => {
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
    // Returning the previous object when the placement is unchanged lets
    // React bail out of the re-render entirely.
    setProjection((prev) => (samePlacement(prev, next) ? prev : next))
  }

  const endDrag = () => {
    setActiveId(null)
    setProjection(null)
    projectionRef.current = null
    lastOverIdRef.current = null
  }

  const handleDragStart = (event: DragStartEvent) => {
    const id = String(event.active.id)
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
    // Deliberately the projection the indicator was LAST SHOWING, not one
    // recomputed from the drop event. The pointer cannot move between the
    // final move event and the release, so there is nothing fresher to
    // learn from recomputing -- but the two can disagree when dnd-kit
    // resolves a different `over` at release than it reported during the
    // drag (it measures droppable rects off the main thread, so a release
    // arriving before that settles resolves against newer rects). Whenever
    // they disagreed the block landed somewhere other than the line the
    // user was looking at, which is the whole thing the indicator exists to
    // promise. Committing what was displayed makes that promise structural.
    const landing = projectionRef.current
    endDrag()
    if (!landing) return
    const nextTree = applyProjection(tree, flat, dragged, landing)
    if (nextTree) onChange(nextTree, assignments)
  }

  const handleDragCancel = () => {
    endDrag()
  }

  const problems = validateTree(tree, assignments, measurements)

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2, flexWrap: 'wrap' }}>
        <Typography variant="body2" color="textSecondary" sx={{ flexGrow: 1 }}>
          Oldest first — the top of this list is the bottom of the stratigraphy. Drag
          the handle on the left to reorder or renest an element — drag sideways to change how
          deeply it's nested. The blue line shows where it will land, and which container it will
          land in.
        </Typography>
        <Button size="small" startIcon={<AutoFixHighIcon />} onClick={bracketPhases}>
          Bracket phases with boundaries
        </Button>
      </Box>

      {problems.length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {problems.map((p, i) => (
            <div key={i}>{p}</div>
          ))}
        </Alert>
      )}

      <Box sx={{ mt: 1 }}>
        <Paper
          variant="outlined"
          sx={{
            p: 1.5,
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
              onSetLimit={(field, value) => setLimit(tree.id, field, value)}
              dates={[]}
            />
            <IconButton
              size="small"
              aria-label={`Add inside ${tree.name}`}
              onClick={(e) => setAddMenu({ anchor: e.currentTarget, targetId: tree.id })}
            >
              <AddIcon fontSize="small" />
            </IconButton>
          </Box>
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
        <SortableContext items={flat.map((n) => n.id)} strategy={() => null}>
          {flat.map((node) => {
            const parent = parentMap.get(node.id)
            const siblings = parent?.children || []
            const index = siblings.findIndex((s) => s.id === node.id)
            return (
              <TreeRow
                key={node.id}
                node={node}
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
                onRename={rename}
                onSetLimit={setLimit}
                onMoveUp={(id) => move(id, -1)}
                onMoveDown={(id) => move(id, 1)}
                onAdd={(id, e) => setAddMenu({ anchor: e.currentTarget, targetId: id })}
                onDelete={remove}
              />
            )
          })}
          {/* Landing at the very end has no following row to hang the line
              above, so it gets a zero-height slot of its own. */}
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
                p: 1.5,
                borderLeft: `4px solid ${typeColor[activeElement.element_type].main}`,
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                boxShadow: 4,
                opacity: 0.95,
              }}
            >
              <DragIndicatorIcon fontSize="small" sx={{ color: 'text.secondary' }} />
              <Chip
                size="small"
                label={TYPE_LABEL[activeElement.element_type]}
                sx={{ backgroundColor: typeColor[activeElement.element_type].ink, color: 'white' }}
              />
              <Typography variant="body2">{activeElement.name}</Typography>
            </Paper>
          )}
        </DragOverlay>
      </DndContext>

      <Paper sx={{ p: 2, mt: 3 }}>
        <Typography variant="subtitle1" gutterBottom>
          Assign dates to phases
        </Typography>
        {measurements.length === 0 ? (
          <Typography variant="body2" color="textSecondary">
            No dates in this analysis yet.
          </Typography>
        ) : (
          measurements.map((m) => (
            // wrap: the label and the select each have their own 220px
            // minimum, and on their own line that's fine -- but held to one
            // unbroken row the pair needs ~490px, wider than a phone's
            // viewport, and forced the page to scroll sideways to reach the
            // select on the right.
            <Box key={m.id} sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 2, mb: 1 }}>
              <Typography variant="body2" sx={{ minWidth: 220, fontFamily: 'monospace' }}>
                {m.sample_name} — {m.age_bp} ± {m.error_1sigma ?? '?'} BP
              </Typography>
              <Select
                size="small"
                displayEmpty
                value={assignments[m.id] || ''}
                onChange={(e) => assign(m.id, e.target.value)}
                sx={{ minWidth: 220 }}
              >
                <MenuItem value="">
                  <em>Unassigned</em>
                </MenuItem>
                {phaseOptions(tree).map((p) => (
                  <MenuItem key={p.id} value={p.id}>
                    {p.name}
                  </MenuItem>
                ))}
              </Select>
            </Box>
          ))
        )}
      </Paper>

      <Menu
        open={!!addMenu}
        anchorEl={addMenu?.anchor}
        onClose={() => setAddMenu(null)}
      >
        {(['sequence', 'phase', 'boundary', 'hiatus'] as ElementType[]).map((type) => (
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
    </Box>
  )
}
