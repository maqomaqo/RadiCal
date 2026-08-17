// The Harris matrix: a read-only rendering of the model tree, oldest at the
// bottom.
//
// This component is deliberately thin. Everything about WHERE things go lives
// in utils/harrisLayout.ts as pure geometry, because reactflow draws into a
// measured viewport and there is no DOM assertion that says "the boxes line
// up" -- the nodes are present at whatever coordinates they were given,
// sensible or not. Keeping the layout separate is what lets
// utils/harrisLayout.test.ts hold it to the properties that matter.
//
// What the drawing means:
//
//   context   a Phase that dates are attached to -- the thing a Harris matrix
//             is actually made of. Shows its date count and any TPQ/TAQ.
//   group     a Sequence, or a Phase that contains other Phases. Drawn as a
//             box AROUND its children rather than a node beside them, because
//             "Fires" contains the fire features; it is not one of them.
//   boundary  a modelled transition, drawn as a labelled rule spanning the
//             group it divides, with its posterior range once the analysis has
//             run.
//   arrow     an ordering between two adjacent blocks. Only drawn where no
//             Boundary rule already separates them, and never expanded to the
//             phases nested inside -- the containing box carries that.
//
// Nodes are not draggable. The layout now means something (a child sits
// centred beneath its parent, siblings share a row because the model says
// they are unordered), and dragging would let that be pulled apart while
// still looking authoritative. Pan, zoom and fit remain.
import { useMemo } from 'react'
import ReactFlow, {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  type Edge,
  type Node,
  type NodeProps,
} from 'reactflow'
import 'reactflow/dist/style.css'
import { Alert, Box, Typography } from '@mui/material'
import { useTheme } from '@mui/material/styles'
import { alpha } from '@mui/material/styles'
import type { MatrixMeasurement, ModelElement } from '@/types'
import { CONTEXT_PALETTE, useElementTypeColors } from '@/theme/diagramColors'
import {
  layoutHarrisMatrix,
  type LaidOutNode,
} from '@/utils/harrisLayout'
import { DEFAULT_DATE_FORMAT, formatCalendarRange, type DateFormat } from '@/utils/dateFormat'

interface NodeData {
  node: LaidOutNode
  color: string
  dateFormat: DateFormat
}

// Handles are hidden but must exist: reactflow anchors every edge to one, and
// an edge whose handle is missing silently does not render.
const HIDDEN_HANDLE = { opacity: 0, width: 1, height: 1, border: 'none', background: 'transparent' }

function ContextNode({ data }: NodeProps<NodeData>) {
  const { node, color } = data
  const badges: string[] = []
  if (node.tpqAgeBp != null) badges.push(`TPQ ≤${node.tpqAgeBp}`)
  if (node.taqAgeBp != null) badges.push(`TAQ ≥${node.taqAgeBp}`)

  return (
    <Box
      sx={{
        width: '100%',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        border: `2px solid ${color}`,
        borderRadius: 1.5,
        backgroundColor: alpha(color, 0.1),
        px: 1,
        boxSizing: 'border-box',
      }}
    >
      <Handle type="target" position={Position.Top} style={HIDDEN_HANDLE} />
      <Typography sx={{ fontSize: 12, fontWeight: 700, lineHeight: 1.2, textAlign: 'center' }}>
        {node.name}
      </Typography>
      <Typography sx={{ fontSize: 10, opacity: 0.75 }}>
        {node.dateCount} date{node.dateCount === 1 ? '' : 's'}
      </Typography>
      {badges.length > 0 && (
        <Typography sx={{ fontSize: 9, opacity: 0.8 }}>{badges.join(' · ')}</Typography>
      )}
      <Handle type="source" position={Position.Bottom} style={HIDDEN_HANDLE} />
    </Box>
  )
}

function GroupNode({ data }: NodeProps<NodeData>) {
  const { node, color } = data
  return (
    <Box
      sx={{
        width: '100%',
        height: '100%',
        border: `1px dashed ${alpha(color, 0.6)}`,
        borderRadius: 2,
        // Each level of nesting is a touch darker, so depth reads at a glance
        // without needing to trace the borders.
        backgroundColor: alpha(color, 0.04 + Math.min(node.depth, 4) * 0.02),
        boxSizing: 'border-box',
      }}
    >
      <Handle type="target" position={Position.Top} style={HIDDEN_HANDLE} />
      <Typography
        sx={{
          fontSize: 10,
          fontWeight: 700,
          textTransform: 'uppercase',
          letterSpacing: 0.5,
          opacity: 0.7,
          px: 1,
          pt: 0.5,
        }}
      >
        {node.name}
      </Typography>
      <Handle type="source" position={Position.Bottom} style={HIDDEN_HANDLE} />
    </Box>
  )
}

function BoundaryNode({ data }: NodeProps<NodeData>) {
  const { node, color, dateFormat } = data
  // The 95% modelled range for this transition, once the analysis has run.
  // Shown on the rule itself so the diagram says WHEN a transition happened,
  // not merely that there is one -- the same figure the results table reports.
  const range = node.posterior
    ? formatCalendarRange(node.posterior.hpd_95[0], node.posterior.hpd_95[1], dateFormat)
    : null

  return (
    <Box
      sx={{
        width: '100%',
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        gap: 1,
        boxSizing: 'border-box',
      }}
    >
      <Handle type="target" position={Position.Top} style={HIDDEN_HANDLE} />
      <Box sx={{ flex: 1, borderTop: `2px solid ${color}` }} />
      <Typography sx={{ fontSize: 10, fontWeight: 700, color, whiteSpace: 'nowrap' }}>
        {node.name}
        {range && (
          <Typography component="span" sx={{ fontSize: 10, fontWeight: 400, opacity: 0.8, ml: 0.75 }}>
            {range}
          </Typography>
        )}
      </Typography>
      <Box sx={{ flex: 1, borderTop: `2px solid ${color}` }} />
      <Handle type="source" position={Position.Bottom} style={HIDDEN_HANDLE} />
    </Box>
  )
}

const NODE_TYPES = { context: ContextNode, group: GroupNode, boundary: BoundaryNode }

// Module-level so the default prop is referentially stable. A fresh `{}` per
// render would change the layout memo's dependency every time and rebuild the
// whole geometry on each paint.
const NO_ASSIGNMENTS: Record<string, string> = {}

interface Props {
  tree: ModelElement | null | undefined
  measurements: MatrixMeasurement[]
  /** Date id -> element id, for a model still being drafted. Falls back to
   *  each date's own element_id/group_id when absent. */
  assignments?: Record<string, string>
  dateFormat?: DateFormat
  height?: number
}

export default function HarrisMatrixDiagram({
  tree,
  measurements,
  assignments = NO_ASSIGNMENTS,
  dateFormat = DEFAULT_DATE_FORMAT,
  height = 520,
}: Props) {
  const theme = useTheme()
  const elementTypeColors = useElementTypeColors()

  const layout = useMemo(
    () => layoutHarrisMatrix(tree, measurements, assignments),
    [tree, measurements, assignments]
  )

  const nodes = useMemo<Node<NodeData>[]>(() => {
    // Contexts are coloured categorically so two of them are told apart at a
    // glance; containers and rules take a structural colour instead, since
    // what matters about them is what they ARE, not which one they are.
    let contextIndex = 0
    return layout.nodes.map((node) => {
      let color: string
      if (node.kind === 'context') {
        color = CONTEXT_PALETTE[contextIndex++ % CONTEXT_PALETTE.length]
      } else if (node.kind === 'boundary') {
        // The deeper coral, not the flat one: a boundary here is a rule AND
        // its own label, so this colour has to carry 10px text as well.
        color = elementTypeColors.boundary.ink
      } else {
        // Keyed off elementType rather than kind, so a Phase drawn as a box
        // around other elements is the same green as that Phase in the tree
        // editor. Every container used to be the Sequence blue, which made
        // the two views disagree about what colour a Phase is.
        color = elementTypeColors[node.elementType].main
      }

      return {
        id: node.id,
        type: node.kind,
        // Relative to parentNode when there is one -- which is what
        // harrisLayout already produces.
        position: { x: node.x, y: node.y },
        style: { width: node.width, height: node.height },
        data: { node, color, dateFormat },
        draggable: false,
        selectable: false,
        connectable: false,
        ...(node.parentId ? { parentNode: node.parentId, extent: 'parent' as const } : {}),
      }
    })
    // layout.nodes is already parent-before-child (the layout emits each
    // container before descending into it), which reactflow requires.
  }, [layout, theme, elementTypeColors, dateFormat])

  const edges = useMemo<Edge[]>(
    () =>
      layout.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: 'smoothstep',
        style: { stroke: theme.palette.text.secondary, strokeWidth: 1.5 },
        markerEnd: { type: MarkerType.ArrowClosed, color: theme.palette.text.secondary },
      })),
    [layout, theme]
  )

  if (!tree || layout.nodes.length === 0) {
    return (
      <Alert severity="info">
        No model structure to draw yet — add Phases and Boundaries in the Model Structure editor.
      </Alert>
    )
  }

  return (
    <Box>
      {layout.unassignedCount > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {layout.unassignedCount} date{layout.unassignedCount === 1 ? '' : 's'} not assigned to any
          phase — not shown here, and unconstrained by the model. Assign{' '}
          {layout.unassignedCount === 1 ? 'it' : 'them'} to a Phase to include{' '}
          {layout.unassignedCount === 1 ? 'it' : 'them'}.
        </Alert>
      )}

      <Box sx={{ width: '100%', height, border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          proOptions={{ hideAttribution: false }}
          minZoom={0.1}
          fitView
        >
          <Background />
          <Controls showInteractive={false} />
          {layout.nodes.length > 12 && <MiniMap pannable zoomable />}
        </ReactFlow>
      </Box>

      <Typography variant="caption" color="textSecondary" sx={{ display: 'block', mt: 1 }}>
        Oldest at the bottom. A box drawn around others groups them without ordering them; an arrow
        or a labelled rule between two blocks means the lower one is older.
      </Typography>
    </Box>
  )
}
