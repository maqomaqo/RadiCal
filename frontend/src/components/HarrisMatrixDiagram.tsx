// Drag-and-drop Harris matrix diagram: samples and groups as nodes,
// above/below/equal relationships as edges. Nodes auto-layout into
// layers on first render (oldest at the bottom, youngest at the top,
// matching the conventional Harris matrix reading direction) but can be
// freely dragged afterward -- reactflow persists whatever position the
// user leaves a node at for the rest of the session.
//
// In editable mode, dragging a connection from one node to another
// creates a new "above" relationship (source = younger, target = older --
// law of superposition: what's above is younger); click an edge to change
// its type or delete it via the panel below the
// canvas. In read-only mode (used on the Results page to show what
// stratigraphy a saved analysis actually used), dragging is limited to
// repositioning -- no edges can be added, changed, or removed.
import React, { useCallback, useMemo, useState, useEffect } from 'react'
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

const GROUP_COLORS = ['#1976d2', '#9c27b0', '#2e7d32', '#ed6c02', '#0288d1', '#c2185b', '#5d4037', '#455a64']

function colorForGroup(groupId: string, groups: DraftGroup[]): string {
  const idx = groups.findIndex((g) => g.id === groupId)
  return GROUP_COLORS[idx % GROUP_COLORS.length] || '#666'
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

function layoutNodes(
  measurements: MatrixMeasurement[],
  groups: DraftGroup[],
  relationships: DraftRelationship[]
): { nodeId: string; layer: number }[] {
  // Layer = length of the longest "older than" chain leading to this
  // node; nodes with no known relationship default to layer 0 alongside
  // genuine root nodes. Only nodes that actually appear in a relationship
  // (directly, as a sample or group -- group membership doesn't count)
  // get a computed layer; everything else is placed at layer 0 too so it
  // stays visible rather than landing at the origin.
  const participants = new Set<string>()
  for (const r of relationships) {
    participants.add(r.from_sample_id)
    participants.add(r.to_sample_id)
  }

  // Edges here are always (older, younger), matching utils/stratigraphy.ts
  // -- law of superposition: "above" means younger (so the older node is
  // the *target*), "below" means older (so the older node is the *source*).
  const edges: { from: string; to: string }[] = []
  for (const r of relationships) {
    if (r.relationship === 'above') edges.push({ from: r.to_sample_id, to: r.from_sample_id })
    else if (r.relationship === 'below') edges.push({ from: r.from_sample_id, to: r.to_sample_id })
  }

  const layer = new Map<string, number>()
  const allIds = [...measurements.map((m) => m.id), ...groups.map((g) => g.id)]
  for (const id of allIds) layer.set(id, 0)

  // Iteratively relax layers along edges until stable (equivalent to
  // longest-path in a DAG; if there's a cycle this just stops after a
  // bounded number of passes rather than looping forever).
  for (let pass = 0; pass < allIds.length + 1; pass++) {
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

  void participants
  return allIds.map((id) => ({ nodeId: id, layer: layer.get(id) || 0 }))
}

interface HarrisMatrixDiagramProps {
  measurements: MatrixMeasurement[]
  groups: DraftGroup[]
  relationships: DraftRelationship[]
  editable: boolean
  onAddRelationship?: (rel: Omit<DraftRelationship, 'id'>) => void
  onUpdateRelationshipType?: (id: string, relationship: RelationshipType) => void
  onDeleteRelationship?: (id: string) => void
  formatAge?: (age: number) => string
}

export default function HarrisMatrixDiagram({
  measurements,
  groups,
  relationships,
  editable,
  onAddRelationship,
  onUpdateRelationshipType,
  onDeleteRelationship,
  formatAge,
}: HarrisMatrixDiagramProps) {
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)

  const initialLayout = useMemo(() => layoutNodes(measurements, groups, relationships), [measurements.length, groups.length])

  const flowNodes = useMemo<Node[]>(() => {
    const maxLayer = Math.max(0, ...initialLayout.map((n) => n.layer))
    const byLayer = new Map<number, string[]>()
    for (const { nodeId, layer } of initialLayout) {
      if (!byLayer.has(layer)) byLayer.set(layer, [])
      byLayer.get(layer)!.push(nodeId)
    }

    const nodes: Node[] = []

    for (const g of groups) {
      const layerEntry = initialLayout.find((n) => n.nodeId === g.id)
      const layer = layerEntry?.layer ?? 0
      const siblings = byLayer.get(layer) || [g.id]
      const idx = siblings.indexOf(g.id)
      const color = colorForGroup(g.id, groups)
      const memberCount = measurements.filter((m) => m.group_id === g.id).length
      nodes.push({
        id: g.id,
        position: { x: idx * 220, y: (maxLayer - layer) * 140 },
        data: {
          label: nodeLabel(g.name, `${memberCount} sample${memberCount === 1 ? '' : 's'}`, tpqTaqBadges(g.tpq_age_bp, g.taq_age_bp)),
        },
        style: {
          background: `${color}22`,
          border: `2px solid ${color}`,
          borderRadius: 10,
          padding: 8,
          minWidth: 130,
        },
      })
    }

    for (const m of measurements) {
      const layerEntry = initialLayout.find((n) => n.nodeId === m.id)
      const layer = layerEntry?.layer ?? 0
      const siblings = (byLayer.get(layer) || [m.id]).filter((id) => measurements.some((mm) => mm.id === id))
      const idx = siblings.indexOf(m.id)
      const groupOffset = groups.length > 0 ? 1 : 0
      const color = m.group_id ? colorForGroup(m.group_id, groups) : '#90a4ae'
      const ageLabel = formatAge
        ? formatAge(m.age_bp)
        : m.error_1sigma != null
          ? `${m.age_bp} ± ${m.error_1sigma} BP`
          : `${m.age_bp} BP`
      nodes.push({
        id: m.id,
        position: { x: (idx + groupOffset) * 180, y: (maxLayer - layer) * 140 + (m.group_id ? 60 : 0) },
        data: { label: nodeLabel(m.sample_name, ageLabel, tpqTaqBadges(m.tpq_age_bp, m.taq_age_bp)) },
        style: {
          background: '#fff',
          border: `2px solid ${color}`,
          borderRadius: 8,
          padding: 6,
          minWidth: 110,
        },
      })
    }

    return nodes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [measurements, groups, initialLayout, formatAge])

  const flowEdges = useMemo<Edge[]>(
    () =>
      relationships.map((r) => {
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

  const nodeTypeOf = useCallback(
    (id: string): 'sample' | 'group' => (groups.some((g) => g.id === id) ? 'group' : 'sample'),
    [groups]
  )

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!editable || !onAddRelationship || !connection.source || !connection.target) return
      if (connection.source === connection.target) return
      onAddRelationship({
        from_sample_id: connection.source,
        to_sample_id: connection.target,
        from_type: nodeTypeOf(connection.source),
        to_type: nodeTypeOf(connection.target),
        relationship: 'above',
      })
    },
    [editable, onAddRelationship, nodeTypeOf]
  )

  const cycle = useMemo(() => {
    const { olderYounger } = expandRelationships(measurements, groups, relationships)
    return findCycle(olderYounger.map((e) => ({ from: e.from, to: e.to })))
  }, [measurements, groups, relationships])

  const cycleLabels = useMemo(() => {
    if (!cycle) return []
    const names = new Map<string, string>()
    for (const m of measurements) names.set(m.id, m.sample_name)
    for (const g of groups) names.set(g.id, g.name)
    return cycle.map((id) => names.get(id) || id)
  }, [cycle, measurements, groups])

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
      {editable && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Drag from one node's edge to another to add an "above" relationship (younger → older). Click
          an edge below to change its type or delete it. Drag nodes to rearrange the layout.
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
          {(measurements.length + groups.length) > 8 && <MiniMap />}
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
