import { useMemo, useState } from 'react'
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
  type DragOverEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { useElementTypeColors } from '@/theme/diagramColors'
import type { ModelElement, ElementType, MatrixMeasurement } from '@/types'

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

export function newElementId(): string {
  return 'el_' + Math.random().toString(36).slice(2, 11)
}

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

interface Projection {
  depth: number
  insertIndexInRest: number
  parentId: string
  rest: FlatNode[]
}

/**
 * Given the flat outline, the item being dragged, what it's currently
 * hovering over, and how far it's been dragged horizontally, works out
 * where it would land: how deep, and just before which item. Depth is
 * clamped so a drop can never nest inside a non-container (a boundary or
 * hiatus) and never skip past what the surrounding items structurally
 * allow -- so every candidate drop position is a legal tree, and nothing
 * needs to be rejected at drop time.
 */
function getProjection(
  flat: FlatNode[],
  activeId: string,
  overId: string,
  dragOffsetX: number,
  rootId: string
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

  const dragDepthDelta = Math.round(dragOffsetX / INDENT_PX)
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

  return { depth, insertIndexInRest, parentId, rest }
}

/** Live preview order while dragging: the dragged item collapses to a
 * single row (its descendants ride along invisibly) at its projected slot,
 * so the rest of the list can animate out of the way. */
function buildPreviewFlat(
  flat: FlatNode[],
  activeId: string,
  overId: string,
  dragOffsetX: number,
  rootId: string
): FlatNode[] {
  const projection = getProjection(flat, activeId, overId, dragOffsetX, rootId)
  if (!projection) return flat
  const activeNode = flat.find((n) => n.id === activeId)
  if (!activeNode) return flat
  const previewActiveNode: FlatNode = { ...activeNode, depth: projection.depth }
  const { rest, insertIndexInRest } = projection
  return [...rest.slice(0, insertIndexInRest), previewActiveNode, ...rest.slice(insertIndexInRest)]
}

/** Commits a drag: reinserts the full dragged subtree (descendants
 * included, shifted by the same depth change) at its projected position.
 * Returns null if the drop didn't actually move anything. */
export function commitDrop(
  tree: ModelElement,
  flat: FlatNode[],
  activeId: string,
  overId: string,
  dragOffsetX: number
): ModelElement | null {
  const projection = getProjection(flat, activeId, overId, dragOffsetX, tree.id)
  if (!projection) return null
  const activeIndex = flat.findIndex((n) => n.id === activeId)
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

interface Props {
  tree: ModelElement
  measurements: MatrixMeasurement[]
  assignments: Record<string, string>
  onChange: (tree: ModelElement, assignments: Record<string, string>) => void
}

interface ElementFieldsProps {
  element: ModelElement
  typeColor: Record<ElementType, string>
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
          sx={{ backgroundColor: typeColor[element.element_type], color: 'white' }}
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
        <IconButton size="small" color="error" aria-label={`Delete ${element.name}`} onClick={onDelete}>
          <DeleteIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </>
  )
}

interface TreeRowProps {
  node: FlatNode
  typeColor: Record<ElementType, string>
  dates: MatrixMeasurement[]
  canMoveUp: boolean
  canMoveDown: boolean
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
  onRename,
  onSetLimit,
  onMoveUp,
  onMoveDown,
  onAdd,
  onDelete,
}: TreeRowProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: node.id,
  })
  const { element, depth } = node

  return (
    <Box
      ref={setNodeRef}
      sx={{
        ml: depth * INDENT_UNITS,
        mt: 1,
        transform: CSS.Transform.toString(transform),
        transition: transition ?? undefined,
        opacity: isDragging ? 0.4 : 1,
      }}
    >
      <Paper variant="outlined" sx={{ p: 1.5, borderLeft: `4px solid ${typeColor[element.element_type]}` }}>
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
  )
}

export default function ModelTreeEditor({ tree, measurements, assignments, onChange }: Props) {
  const [addMenu, setAddMenu] = useState<{ anchor: HTMLElement; targetId: string } | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [overId, setOverId] = useState<string | null>(null)
  const [dragOffsetX, setDragOffsetX] = useState(0)
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

  const displayFlat = useMemo(() => {
    if (!activeId) return flat
    return buildPreviewFlat(flat, activeId, overId ?? activeId, dragOffsetX, tree.id)
  }, [flat, activeId, overId, dragOffsetX, tree.id])

  const activeElement = activeId ? flat.find((n) => n.id === activeId)?.element ?? null : null

  const handleDragStart = (event: DragStartEvent) => {
    const id = String(event.active.id)
    setActiveId(id)
    setOverId(id)
    setDragOffsetX(0)
  }

  const handleDragMove = (event: DragMoveEvent) => {
    setDragOffsetX(event.delta.x)
  }

  const handleDragOver = (event: DragOverEvent) => {
    setOverId(event.over ? String(event.over.id) : null)
  }

  const handleDragEnd = (event: DragEndEvent) => {
    const currentActiveId = activeId
    const currentOverId = event.over ? String(event.over.id) : overId
    const finalOffsetX = event.delta.x
    setActiveId(null)
    setOverId(null)
    setDragOffsetX(0)
    if (!currentActiveId || !currentOverId) return
    const nextTree = commitDrop(tree, flat, currentActiveId, currentOverId, finalOffsetX)
    if (nextTree) onChange(nextTree, assignments)
  }

  const handleDragCancel = () => {
    setActiveId(null)
    setOverId(null)
    setDragOffsetX(0)
  }

  const problems = validateTree(tree, assignments, measurements)

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2, flexWrap: 'wrap' }}>
        <Typography variant="body2" color="textSecondary" sx={{ flexGrow: 1 }}>
          Oldest first — the top of this list is the bottom of the stratigraphy. A Boundary between
          two Phases is a single shared transition: the end of one and the start of the next. Drag
          the handle on the left to reorder or renest an element — drag sideways to change how
          deeply it's nested.
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
        <Paper variant="outlined" sx={{ p: 1.5, borderLeft: `4px solid ${typeColor[tree.element_type]}` }}>
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
        onDragOver={handleDragOver}
        onDragEnd={handleDragEnd}
        onDragCancel={handleDragCancel}
      >
        <SortableContext items={displayFlat.map((n) => n.id)} strategy={verticalListSortingStrategy}>
          {displayFlat.map((node) => {
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
                onRename={rename}
                onSetLimit={setLimit}
                onMoveUp={(id) => move(id, -1)}
                onMoveDown={(id) => move(id, 1)}
                onAdd={(id, e) => setAddMenu({ anchor: e.currentTarget, targetId: id })}
                onDelete={remove}
              />
            )
          })}
        </SortableContext>
        <DragOverlay>
          {activeElement && (
            <Paper
              sx={{
                p: 1.5,
                borderLeft: `4px solid ${typeColor[activeElement.element_type]}`,
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
                sx={{ backgroundColor: typeColor[activeElement.element_type], color: 'white' }}
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
