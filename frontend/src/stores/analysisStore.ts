// Shared draft state for building a Bayesian analysis across two pages:
// NewAnalysis.tsx (measurements) and Stratigraphy.tsx (groups +
// relationships + TPQ/TAQ). Neither page persists anything to the backend
// until "Run Analysis" is pressed back on NewAnalysis.tsx -- this store
// just holds the in-progress draft in memory so navigating between the
// two pages doesn't lose it. Cleared via reset() after a successful run
// (or when starting a fresh calibration-only analysis, where none of this
// applies).
import { create } from 'zustand'
import type { DraftMeasurement, DraftGroup, DraftRelationship } from '@/types'

interface AnalysisDraftState {
  measurements: DraftMeasurement[]
  groups: DraftGroup[]
  relationships: DraftRelationship[]

  setMeasurements: (measurements: DraftMeasurement[]) => void
  addMeasurement: (measurement: DraftMeasurement) => void
  removeMeasurement: (id: string) => void
  updateMeasurement: (id: string, updates: Partial<DraftMeasurement>) => void

  addGroup: (group: DraftGroup) => void
  removeGroup: (id: string) => void
  updateGroup: (id: string, updates: Partial<DraftGroup>) => void

  addRelationship: (relationship: DraftRelationship) => void
  removeRelationship: (id: string) => void
  updateRelationshipType: (id: string, relationship: DraftRelationship['relationship']) => void

  reset: () => void
}

export const useAnalysisDraftStore = create<AnalysisDraftState>((set) => ({
  measurements: [],
  groups: [],
  relationships: [],

  setMeasurements: (measurements) => set({ measurements }),

  addMeasurement: (measurement) =>
    set((state) => ({ measurements: [...state.measurements, measurement] })),

  removeMeasurement: (id) =>
    set((state) => ({
      measurements: state.measurements.filter((m) => m.id !== id),
      // Unlinking a removed sample's group membership is implicit --
      // it's just gone -- but any relationship that named this sample
      // directly (not via a group) is now dangling and must go too.
      relationships: state.relationships.filter(
        (r) => !((r.from_type === 'sample' && r.from_sample_id === id) || (r.to_type === 'sample' && r.to_sample_id === id))
      ),
    })),

  updateMeasurement: (id, updates) =>
    set((state) => ({
      measurements: state.measurements.map((m) => (m.id === id ? { ...m, ...updates } : m)),
    })),

  addGroup: (group) => set((state) => ({ groups: [...state.groups, group] })),

  removeGroup: (id) =>
    set((state) => ({
      groups: state.groups.filter((g) => g.id !== id),
      measurements: state.measurements.map((m) => (m.group_id === id ? { ...m, group_id: null } : m)),
      relationships: state.relationships.filter(
        (r) => !((r.from_type === 'group' && r.from_sample_id === id) || (r.to_type === 'group' && r.to_sample_id === id))
      ),
    })),

  updateGroup: (id, updates) =>
    set((state) => ({
      groups: state.groups.map((g) => (g.id === id ? { ...g, ...updates } : g)),
    })),

  addRelationship: (relationship) =>
    set((state) => ({ relationships: [...state.relationships, relationship] })),

  removeRelationship: (id) =>
    set((state) => ({ relationships: state.relationships.filter((r) => r.id !== id) })),

  updateRelationshipType: (id, relationship) =>
    set((state) => ({
      relationships: state.relationships.map((r) => (r.id === id ? { ...r, relationship } : r)),
    })),

  reset: () => set({ measurements: [], groups: [], relationships: [] }),
}))
