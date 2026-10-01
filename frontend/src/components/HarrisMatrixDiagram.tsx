// The Harris matrix: a read-only rendering of the model tree, oldest at the
// bottom.
//
// Deliberately thin: everything about WHERE things go lives in
// utils/harrisLayout.ts as pure geometry, because reactflow draws into a
// measured viewport and no DOM assertion says "the boxes line up" -- the nodes
// are present at whatever coordinates they were given. Keeping the layout
// separate is what lets utils/harrisLayout.test.ts hold it to its properties.
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
// Nodes are not draggable: the layout means something -- a child sits centred
// beneath its parent, siblings share a row because the model says they are
// unordered -- and dragging would pull that apart while still looking
// authoritative. Pan, zoom and fit remain.
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
import type { MatrixMeasurement, ModelElement } from '@/types'
import { CONTEXT_PALETTE, contextShades, tint, useElementTypeColors } from '@/theme/diagramColors'
import {
  LABEL_FONT_SIZE,
  RULE_LABEL_GAP,
  RULE_STUB,
  layoutHarrisMatrix,
  type LaidOutNode,
} from '@/utils/harrisLayout'
import { DEFAULT_DATE_FORMAT, formatCalendarRange, type DateFormat } from '@/utils/dateFormat'
import { SIGMA_2, posteriorEnvelope, type RangeReporting } from '@/utils/hpd'

interface NodeData {
  node: LaidOutNode
  color: string
  /** What a box is painted over. Fills are composited against this rather
   *  than left translucent -- see `tint`. */
  surface: string
}

// Handles are hidden but must exist: reactflow anchors every edge to one, and
// an edge whose handle is missing silently does not render.
const HIDDEN_HANDLE = { opacity: 0, width: 1, height: 1, border: 'none', background: 'transparent' }

function ContextNode({ data }: NodeProps<NodeData>) {
  const { node, color, surface } = data
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
        backgroundColor: tint(color, 0.1, surface),
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
  const { node, color, surface } = data
  return (
    <Box
      sx={{
        width: '100%',
        height: '100%',
        // Solid where the container ORDERS its children, dashed where it does
        // not -- the same pair the tree editor's depth guides use, so the two
        // views of one model teach one convention. Previously every group box
        // was dashed regardless, which made "dashed" mean nothing here while
        // meaning "unordered" next door.
        border: `1px ${node.elementType === 'sequence' ? 'solid' : 'dashed'} ${tint(
          color,
          0.6,
          surface
        )}`,
        borderRadius: 2,
        // Each level of nesting is a touch darker, so depth reads at a glance
        // without tracing borders. Composited rather than translucent, so this
        // is the ONLY thing setting the shade -- stacked tints would add a
        // container's ancestors on top of its own.
        backgroundColor: tint(color, 0.04 + Math.min(node.depth, 4) * 0.02, surface),
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

// The rule either side of the label: it grows to fill a wide box and never
// shrinks below RULE_STUB. The layout has already found room for both stubs
// (harrisLayout's boundarySpan), so a stub shrinking in proportion with the
// label would spend that room twice.
const stubSx = (color: string) => ({
  flex: `1 0 ${RULE_STUB}px`,
  borderTop: `2px solid ${color}`,
})

function BoundaryNode({ data }: NodeProps<NodeData>) {
  const { node, color } = data

  return (
    <Box
      sx={{
        width: '100%',
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        gap: `${RULE_LABEL_GAP}px`,
        boxSizing: 'border-box',
      }}
    >
      <Handle type="target" position={Position.Top} style={HIDDEN_HANDLE} />
      <Box sx={stubSx(color)} />
      {/*
        The name and the range are siblings that wrap, not one line of text.

        The label used to be a single nowrap run, and a run of text that
        cannot wrap cannot be made narrower than itself -- so on a sequence
        holding one context, a 172px box carried a 219px label and 47px of red
        hung outside the box. The layout now sizes every container to hold the
        wider of these two lines (see harrisLayout's boundarySpan), which is
        what fixes it. This wraps as the backstop for the case the estimate is
        wrong: the range drops below the name, both stay whole, and neither
        can be pushed past the edge.
      */}
      <Box
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'center',
          alignItems: 'baseline',
          columnGap: 0.75,
          minWidth: 0,
          maxWidth: '100%',
        }}
      >
        <Typography
          sx={{
            fontSize: LABEL_FONT_SIZE,
            fontWeight: 700,
            lineHeight: 1.3,
            color,
            whiteSpace: 'nowrap',
            maxWidth: '100%',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {node.name}
        </Typography>
        {node.rangeLabel && (
          <Typography
            sx={{
              fontSize: LABEL_FONT_SIZE,
              fontWeight: 400,
              lineHeight: 1.3,
              color,
              opacity: 0.8,
              whiteSpace: 'nowrap',
              maxWidth: '100%',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {node.rangeLabel}
          </Typography>
        )}
      </Box>
      <Box sx={stubSx(color)} />
      <Handle type="source" position={Position.Bottom} style={HIDDEN_HANDLE} />
    </Box>
  )
}

/**
 * Layout `kind` -> reactflow node type.
 *
 * A container is registered as 'container', NOT 'group'. 'group' is one of
 * reactflow's own built-in type names, so registering a custom component under
 * it still matches the vendor stylesheet's
 * `.react-flow__node-default, ..., .react-flow__node-group` rule -- which put a
 * 1px #1a192b border, a 3px radius, 10px of padding, a 150px width and a
 * background wash around every Sequence and Phase box. That drew a second,
 * squared-off near-black rectangle outside each container's own rounded
 * coloured border, and put an unaccounted-for fill underneath the container
 * tint, which GroupNode's shading math assumes is the only thing setting the
 * shade. Only containers were affected, because 'context' and 'boundary' are
 * not names reactflow ships styles for.
 */
const CONTAINER_TYPE = 'container'
const NODE_TYPES = { context: ContextNode, container: GroupNode, boundary: BoundaryNode }

// Module-level so the default prop is referentially stable: a fresh `{}` per
// render would change the layout memo's dependency and rebuild the whole
// geometry on each paint.
const NO_ASSIGNMENTS: Record<string, string> = {}

interface Props {
  tree: ModelElement | null | undefined
  measurements: MatrixMeasurement[]
  /** Date id -> element id, for a model still being drafted. Falls back to
   *  each date's own element_id/group_id when absent. */
  assignments?: Record<string, string>
  dateFormat?: DateFormat
  /** The confidence the rest of the page is reporting at. Defaults to 2 sigma
   *  for the draft-model callers that have no selector of their own. */
  confidence?: number
  confidenceLabel?: string
  /** The calendar step the analysis reported its ranges at; `true` for the
   *  default. See utils/hpd.reportingOf. */
  reporting?: RangeReporting
  height?: number
}

export default function HarrisMatrixDiagram({
  tree,
  measurements,
  assignments = NO_ASSIGNMENTS,
  dateFormat = DEFAULT_DATE_FORMAT,
  confidence = SIGMA_2,
  confidenceLabel = '95.4%',
  reporting = true,
  height = 520,
}: Props) {
  const theme = useTheme()
  const elementTypeColors = useElementTypeColors()

  /**
   * The modelled range each boundary carries, once the analysis has run.
   * Formatted here, before the layout runs, because the layout sizes each
   * container around the label it will hold (harrisLayout's `boundaryRanges`);
   * BoundaryNode then draws this same string rather than deriving its own.
   *
   * Re-selected from the boundary's own distribution at the page's current
   * confidence, through the SAME helper the results table calls, rather than
   * read off the stored hpd_95 -- which would freeze the diagram at 95% while
   * the selector said 99.7%. The level is printed beside the figure so the
   * number carries its qualifier into a screenshot.
   */
  const boundaryRanges = useMemo(() => {
    const ranges: Record<string, string> = {}
    const walk = (element: ModelElement) => {
      if (element.element_type === 'boundary' && element.posterior) {
        const envelope = posteriorEnvelope(
          element.posterior.probability_distribution,
          confidence,
          reporting
        )
        if (envelope) {
          ranges[element.id] =
            `${formatCalendarRange(envelope[0], envelope[1], dateFormat)} (${confidenceLabel})`
        }
      }
      for (const child of element.children || []) walk(child)
    }
    if (tree) walk(tree)
    return ranges
  }, [tree, dateFormat, confidence, confidenceLabel, reporting])

  const layout = useMemo(
    () => layoutHarrisMatrix(tree, measurements, assignments, boundaryRanges),
    [tree, measurements, assignments, boundaryRanges]
  )

  const nodes = useMemo<Node<NodeData>[]>(() => {
    // Contexts share the Phase hue and differ only in strength (see
    // contextShades). The shades come from the COUNT first, so a three-context
    // model gets three widely spaced steps rather than the three most similar
    // ones at the head of the ramp.
    const shades = contextShades(layout.nodes.filter((n) => n.kind === 'context').length)
    let contextIndex = 0
    return layout.nodes.map((node) => {
      let color: string
      if (node.kind === 'context') {
        color = shades[contextIndex++] ?? CONTEXT_PALETTE[0]
      } else if (node.kind === 'boundary') {
        // The deeper coral: a boundary here is a rule AND its label, so the
        // colour has to carry 10px text too.
        color = elementTypeColors.boundary.ink
      } else {
        // Keyed off elementType rather than kind, so a Phase drawn as a box
        // around other elements is the same green as that Phase in the tree
        // editor.
        color = elementTypeColors[node.elementType].main
      }

      return {
        id: node.id,
        type: node.kind === 'group' ? CONTAINER_TYPE : node.kind,
        // Relative to parentNode when there is one -- which is what
        // harrisLayout already produces.
        position: { x: node.x, y: node.y },
        style: { width: node.width, height: node.height },
        data: {
          node,
          color,
          surface: theme.palette.background.paper,
        },
        draggable: false,
        selectable: false,
        connectable: false,
        ...(node.parentId ? { parentNode: node.parentId, extent: 'parent' as const } : {}),
      }
    })
    // layout.nodes is already parent-before-child (the layout emits each
    // container before descending into it), which reactflow requires.
  }, [layout, theme, elementTypeColors])

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

      {/* The React Flow attribution stays -- hiding it is theirs to license,
          not ours to decide -- but its default #999 measures 2.85:1 on white,
          the last text on this page below AA. Restyled to the page's own
          secondary ink, which is a presentation change, not a removal. */}
      <Box
        sx={{
          width: '100%',
          height,
          border: '1px solid',
          borderColor: 'divider',
          borderRadius: 1,
          '& .react-flow__attribution a': { color: 'text.secondary' },
        }}
      >
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
        Read as a stratigraphic matrix: oldest at the bottom.
      </Typography>
    </Box>
  )
}
