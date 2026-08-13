import { useState } from 'react'
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

export function newElementId(): string {
  return 'el_' + Math.random().toString(36).slice(2, 11)
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

interface Props {
  tree: ModelElement
  measurements: MatrixMeasurement[]
  assignments: Record<string, string>
  onChange: (tree: ModelElement, assignments: Record<string, string>) => void
}

export default function ModelTreeEditor({ tree, measurements, assignments, onChange }: Props) {
  const [addMenu, setAddMenu] = useState<{ anchor: HTMLElement; targetId: string } | null>(null)
  const typeColor = useElementTypeColors()

  const datesIn = (elementId: string) =>
    measurements.filter((m) => assignments[m.id] === elementId)

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

  const problems = validateTree(tree, assignments, measurements)

  const renderElement = (element: ModelElement, depth: number, siblings: ModelElement[]) => {
    const index = siblings.findIndex((s) => s.id === element.id)
    const isRoot = element.id === tree.id
    const canContain = element.element_type === 'sequence' || element.element_type === 'phase'
    const dates = datesIn(element.id)

    return (
      <Box key={element.id} sx={{ ml: depth === 0 ? 0 : 3, mt: 1 }}>
        <Paper
          variant="outlined"
          sx={{ p: 1.5, borderLeft: `4px solid ${typeColor[element.element_type]}` }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Tooltip title={TYPE_HINT[element.element_type]}>
              <Chip
                size="small"
                label={TYPE_LABEL[element.element_type]}
                sx={{ backgroundColor: typeColor[element.element_type], color: 'white' }}
              />
            </Tooltip>
            {/* No variant prop -- every other field in the app is the
                outlined TextField/Select default; this one used to be the
                lone exception with a bare underline instead of a bordered
                box, easy to miss as an editable field next to the boxed
                ones surrounding it. */}
            <TextField
              size="small"
              value={element.name || ''}
              onChange={(e) => rename(element.id, e.target.value)}
              sx={{ minWidth: 180 }}
            />
            {(element.element_type === 'phase' || element.element_type === 'sequence') && (
              <>
                <TextField
                  size="small"
                  type="number"
                  label="TPQ"
                  value={element.tpq_age_bp ?? ''}
                  onChange={(e) => setLimit(element.id, 'tpq_age_bp', e.target.value)}
                  sx={{ width: 90 }}
                />
                <TextField
                  size="small"
                  type="number"
                  label="TAQ"
                  value={element.taq_age_bp ?? ''}
                  onChange={(e) => setLimit(element.id, 'taq_age_bp', e.target.value)}
                  sx={{ width: 90 }}
                />
              </>
            )}
            <Box sx={{ flexGrow: 1 }} />
            {!isRoot && (
              <>
                <IconButton size="small" aria-label="Move earlier" disabled={index <= 0}
                  onClick={() => move(element.id, -1)}>
                  <ArrowUpwardIcon fontSize="small" />
                </IconButton>
                <IconButton size="small" aria-label="Move later" disabled={index >= siblings.length - 1}
                  onClick={() => move(element.id, 1)}>
                  <ArrowDownwardIcon fontSize="small" />
                </IconButton>
              </>
            )}
            {canContain && (
              <IconButton
                size="small"
                aria-label={`Add inside ${element.name}`}
                onClick={(e) => setAddMenu({ anchor: e.currentTarget, targetId: element.id })}
              >
                <AddIcon fontSize="small" />
              </IconButton>
            )}
            {!isRoot && (
              <Tooltip title={`Delete ${element.name}`}>
                <IconButton
                  size="small"
                  color="error"
                  aria-label={`Delete ${element.name}`}
                  onClick={() => remove(element.id)}
                >
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
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

        {(element.children || []).map((child) =>
          renderElement(child, depth + 1, element.children || [])
        )}
      </Box>
    )
  }

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2, flexWrap: 'wrap' }}>
        <Typography variant="body2" color="textSecondary" sx={{ flexGrow: 1 }}>
          Oldest first — the top of this list is the bottom of the stratigraphy. A Boundary between
          two Phases is a single shared transition: the end of one and the start of the next.
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

      {renderElement(tree, 0, [tree])}

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
