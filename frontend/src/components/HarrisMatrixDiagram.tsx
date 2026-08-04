// Drag-and-drop Harris matrix diagram: each node is a stratigraphic
// Context (a named phase/layer that one or more dated samples belong to),
// with above/below/equal relationships as edges between contexts. Nodes
// auto-layout into layers on first render (oldest at the bottom, youngest
// at the top, matching the conventional Harris matrix reading direction)
// but can be freely dragged afterward -- reactflow persists whatever
// position the user leaves a node at for the rest of the session.
//
// Individual dated samples are deliberately NOT drawn as their own boxes
// here -- the workflow is "assign each sample to a Context, then relate
// Contexts to each other," so a context node just shows how many dates it
// contains (see the "N dates" sub-label). A sample not yet assigned to any
// context has nowhere to attach in this diagram; see unassignedCount below.
//
// In editable mode, dragging a connection from one context to another
// creates a new "above" relationship (source = younger, target = older --
// law of superposition: what's above is younger); click an edge to change
// its type or delete it via the panel below the canvas. In read-only mode
// (used on the Results page to show what stratigraphy a saved analysis
// actually used), dragging is limited to repositioning -- no edges can be
// added, changed, or removed.
import React, { useMemo, useState, useEffect } from 'react'
import ReactFlow, {
  Node,
  Edge,
  Connection,
  MarkerType,
  useNodesState,
  useEdgesState,
  Controls,
  Background,
  MiniMap,
} from 'reactflow'
import 'reactflow/dist/style.css'
import { Box, Paper, Typography, FormControl, Select, MenuItem, Button, Alert, Stack } from '@mui/material'
import DeleteIcon from '@mui/icons-material/Delete'
import type { DraftGroup, MatrixMeasurement, DraftRelationship, RelationshipType } from '@/types'
import { expandRelationships, findCycle } from '@/utils/stratigraphy'

const CONTEXT_COLORS = ['#1976d2', '#9c27b0', '#2e7d32', '#ed6c02', '#0288d1', '#c2185b', '#5d4037', '#455a64']

function colorForContext(contextId: string, contexts: DraftGroup[]): string {
  const idx = contexts.findIndex((c) => c.id === contextId)
  return CONTEXT_COLORS[idx % CONTEXT_COLORS.length] || '#666'
}

function nodeLabel(name: string, sub?: string, badges?: string[]) {
  return (
    <Box sx={{ textAlign: 'center', lineHeight: 1.3 }}>
      <Typography sx={{ fontSize: 12, fontWeight: 700 }}>{name}</Typography>
      {sub && (
        <Typography sx={{ fontSize: 10, opacity: 0.75 }}>{sub}</Typography>
      )}
      {badges && badges.length > 0 && (
        <Typography sx={{ fontSize: 9, opacity: 0.8, mt: 0.25 }}>{badges.join(' · ')}</Typography>
      )}
    </Box>
  )
}

function tpqTaqBadges(tpq?: number | null, taq?: number | null): string[] {
  const badges: string[] = []
  if (tpq != null) badges.push(`TPQ ≤${tpq}`)
  if (taq != null) badges.push(`TAQ ≥${taq}`)
  return badges
}

// Layer = length of the longest "older than" chain leading to this context;
// a context with no known relationship defaults to layer 0. Only
// context-to-context relationships participate here -- see the module
// comment above for why individual samples don't get their own nodes.
function layoutNodes(contexts: DraftGroup[], relationships: DraftRelationship[]): { nodeId: string; layer: number }[] {
  // Edges here are always (older, younger), matching utils/stratigraphy.ts
  // -- law of superposition: "above" means younger (so the older node is
  // the *target*), "below" means older (so the older node is the *source*).
  const edges: { from: string; to: string }[] = []
  for (const r of relationships) {
    if (r.from_type !== 'group' || r.to_type !== 'group') continue
    if (r.relationship === 'above') edges.push({ from: r.to_sample_id, to: r.from_sample_id })
    else if (r.relationship === 'below') edges.push({ from: r.from_sample_id, to: r.to_sample_id })
  }

  const layer = new Map<string, number>()
  for (const c of contexts) layer.set(c.id, 0)

  // Iteratively relax layers along edges until stable (equivalent to
  // longest-path in a DAG; if there's a cycle this just stops after a
  // bounded number of passes rather than looping forever).
  for (let pass = 0; pass < contexts.length + 1; pass++) {
    let changed = false
    for (const { from, to } of edges) {
      const candidate = (layer.get(from) || 0) + 1
      if (candidate > (layer.get(to) || 0)) {
        layer.set(to, candidate)
        changed = true
      }
    }
    if (!changed) break
  }

  return contexts.map((c) => ({ nodeId: c.id, layer: layer.get(c.id) || 0 }))
}

interface HarrisMatrixDiagramProps {
  measurements: MatrixMeasurement[]
  groups: DraftGroup[]
  relationships: DraftRelationship[]
  editable: boolean
  onAddRelationship?: (rel: Omit<DraftRelationship, 'id'>) => void
  onUpdateRelationshipType?: (id: string, relationship: RelationshipType) => void
  onDeleteRelationship?: (id: string) => void
}

export default function HarrisMatrixDiagram({
  measurements,
  groups: contexts,
  relationships,
  editable,
  onAddRelationship,
  onUpdateRelationshipType,
  onDeleteRelationship,
}: HarrisMatrixDiagramProps) {
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)

  const unassignedCount = useMemo(
    () => measurements.filter((m) => !m.group_id).length,
    [measurements]
  )

  const initialLayout = useMemo(() => layoutNodes(contexts, relationships), [contexts.length, relationships])

  const flowNodes = useMemo<Node[]>(() => {
    const maxLayer = Math.max(0, ...initialLayout.map((n) => n.layer))
    const byLayer = new Map<number, string[]>()
    for (const { nodeId, layer } of initialLayout) {
      if (!byLayer.has(layer)) byLayer.set(layer, [])
      byLayer.get(layer)!.push(nodeId)
    }

    return contexts.map((c) => {
      const layerEntry = initialLayout.find((n) => n.nodeId === c.id)
      const layer = layerEntry?.layer ?? 0
      const siblings = byLayer.get(layer) || [c.id]
      const idx = siblings.indexOf(c.id)
      const color = colorForContext(c.id, contexts)
      const dateCount = measurements.filter((m) => m.group_id === c.id).length
      return {
        id: c.id,
        position: { x: idx * 220, y: (maxLayer - layer) * 140 },
        data: {
          label: nodeLabel(c.name, `${dateCount} date${dateCount === 1 ? '' : 's'}`, tpqTaqBadges(c.tpq_age_bp, c.taq_age_bp)),
        },
        style: {
          background: `${color}22`,
          border: `2px solid ${color}`,
          borderRadius: 10,
          padding: 8,
          minWidth: 150,
        },
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contexts, measurements, initialLayout])

  const flowEdges = useMemo<Edge[]>(
    () =>
      relationships
        .filter((r) => r.from_type === 'group' && r.to_type === 'group')
        .map((r) => {
          const isEqual = r.relationship === 'equal'
          return {
            id: r.id,
            source: r.from_sample_id,
            target: r.to_sample_id,
            label: r.relationship,
            animated: false,
            style: {
              stroke: isEqual ? '#9c27b0' : '#1976d2',
              strokeDasharray: isEqual ? '6 4' : undefined,
              strokeWidth: r.id === selectedEdgeId ? 3 : 1.5,
            },
            markerEnd: isEqual ? undefined : { type: MarkerType.ArrowClosed, color: '#1976d2' },
          }
        }),
    [relationships, selectedEdgeId]
  )

  const [nodes, setNodes, onNodesChange] = useNodesState(flowNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(flowEdges)

  // Keep the flow's internal state in sync whenever the underlying draft
  // data changes (e.g. a relationship added from the List Editor tab)
  // without clobbering positions the user has already dragged.
  useEffect(() => {
    setNodes((current: Node[]) => {
      const byId = new Map(current.map((n: Node) => [n.id, n]))
      return flowNodes.map((n) => (byId.has(n.id) ? { ...n, data: n.data, style: n.style } : n))
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flowNodes])

  useEffect(() => {
    setEdges(flowEdges)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flowEdges])

  const onConnect = (connection: Connection) => {
    if (!editable || !onAddRelationship || !connection.source || !connection.target) return
    if (connection.source === connection.target) return
    onAddRelationship({
      from_sample_id: connection.source,
      to_sample_id: connection.target,
      from_type: 'group',
      to_type: 'group',
      relationship: 'above',
    })
  }

  const cycle = useMemo(() => {
    const { olderYounger } = expandRelationships(measurements, contexts, relationships)
    return findCycle(olderYounger.map((e) => ({ from: e.from, to: e.to })))
  }, [measurements, contexts, relationships])

  const cycleLabels = useMemo(() => {
    if (!cycle) return []
    const names = new Map<string, string>()
    for (const m of measurements) names.set(m.id, m.sample_name)
    for (const c of contexts) names.set(c.id, c.name)
    return cycle.map((id) => names.get(id) || id)
  }, [cycle, measurements, contexts])

  const selectedRelationship = relationships.find((r) => r.id === selectedEdgeId) || null

  return (
    <Box>
      {cycle && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Circular relationship detected involving: {cycleLabels.join(', ')}. No set of calendar ages
          can satisfy every constraint at once -- fix or remove one of these relationships before
          running the analysis.
        </Alert>
      )}
      {unassignedCount > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {unassignedCount} sample{unassignedCount === 1 ? '' : 's'} not yet assigned to a context --
          not shown here, and unconstrained by any relationship. Assign {unassignedCount === 1 ? 'it' : 'them'} to a
          context in the Samples table to include {unassignedCount === 1 ? 'it' : 'them'} in this diagram.
        </Alert>
      )}
      {editable && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Drag from one context's edge to another to add an "above" relationship (younger → older).
          Click an edge below to change its type or delete it. Drag contexts to rearrange the layout.
        </Alert>
      )}

      <Box sx={{ width: '100%', height: 480, border: '1px solid #ddd', borderRadius: 1 }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={editable ? onEdgesChange : undefined}
          nodesDraggable
          nodesConnectable={editable}
          elementsSelectable={editable}
          onConnect={editable ? onConnect : undefined}
          onEdgeClick={(_: React.MouseEvent, edge: Edge) => setSelectedEdgeId(edge.id)}
          onPaneClick={() => setSelectedEdgeId(null)}
          fitView
        >
          <Background />
          <Controls showInteractive={false} />
          {contexts.length > 8 && <MiniMap />}
        </ReactFlow>
      </Box>

      {editable && selectedRelationship && (
        <Paper sx={{ mt: 2, p: 2 }}>
          <Stack direction="row" spacing={2} alignItems="center">
            <Typography variant="body2" sx={{ flex: 1 }}>
              Selected relationship
            </Typography>
            <FormControl size="small" sx={{ minWidth: 140 }}>
              <Select
                value={selectedRelationship.relationship}
                onChange={(e) => onUpdateRelationshipType?.(selectedRelationship.id, e.target.value as RelationshipType)}
              >
                <MenuItem value="above">Above (younger)</MenuItem>
                <MenuItem value="below">Below (older)</MenuItem>
                <MenuItem value="equal">Equal (same phase)</MenuItem>
              </Select>
            </FormControl>
            <Button
              size="small"
              color="error"
              startIcon={<DeleteIcon />}
              onClick={() => {
                onDeleteRelationship?.(selectedRelationship.id)
                setSelectedEdgeId(null)
              }}
            >
              Delete
            </Button>
          </Stack>
        </Paper>
      )}
    </Box>
  )
}
