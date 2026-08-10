// Shared draft state for building a Bayesian analysis across two pages:
// NewAnalysis.tsx (measurements) and Stratigraphy.tsx (the model tree +
// TPQ/TAQ). Neither page persists anything to the backend until "Run
// Analysis" is pressed back on NewAnalysis.tsx -- this store
// just holds the in-progress draft in memory so navigating between the
// two pages doesn't lose it. Cleared via reset() after a successful run
// (or when starting a fresh calibration-only analysis, where none of this
// applies).
import { create } from 'zustand'
import type { DraftMeasurement, DraftGroup, DraftRelationship, ModelElement } from '@/types'
import { DateFormat, DEFAULT_DATE_FORMAT } from '@/utils/dateFormat'

export interface McmcConfig {
  mcmc_samples: number
  mcmc_tune: number
  mcmc_chains: number
  // OxCal's UniformSpanPrior (Nicholls and Jones 2001) -- see
  // BayesianConfigInput.uniform_span_prior on the backend for what it does.
  // On by default there and here.
  uniform_span_prior: boolean
}

// 4000 draws is how many posterior samples a result carries, not how long the
// sampler runs: services/gibbs.py on the backend extends its own run until it
// converges, and the exact solver has no run length at all.
export const DEFAULT_MCMC: McmcConfig = {
  mcmc_samples: 4000,
  mcmc_tune: 1000,
  mcmc_chains: 4,
  uniform_span_prior: true,
}

interface AnalysisDraftState {
  name: string
  analysisType: 'calibration' | 'bayesian'
  // Project and Result Date Format live here rather than as local useState
  // on NewAnalysis.tsx for the same reason as everything else in this
  // store: navigating to /new-analysis/model-structure and back remounts
  // that page, and local state would silently reset to "no project" /
  // cal BP on the way back.
  projectId: string
  dateFormat: DateFormat
  measurements: DraftMeasurement[]
  groups: DraftGroup[]
  relationships: DraftRelationship[]
  mcmc: McmcConfig
  // Set when the draft is EDITING an already-saved analysis rather than
  // building a new one (loaded via loadFromAnalysis below). Drives where
  // "Run Analysis" submits: PUT /analysis/{id}/inputs + rerun for an edit,
  // vs POST /analysis/bayesian for a new one.
  editingAnalysisId: string | null
  // The explicit model structure being authored, and which phase each date
  // sits in. This is the stratigraphy for a new Bayesian analysis -- it is
  // submitted as-is rather than being derived from contexts and pairwise
  // relationships. Null until the Stratigraphy page initialises one.
  modelTree: ModelElement | null
  elementAssignments: Record<string, string>
  setModelTree: (tree: ModelElement | null, assignments?: Record<string, string>) => void

  setName: (name: string) => void
  setAnalysisType: (analysisType: 'calibration' | 'bayesian') => void
  setProjectId: (projectId: string) => void
  setDateFormat: (dateFormat: DateFormat) => void
  setMcmc: (mcmc: Partial<McmcConfig>) => void
  loadFromAnalysis: (payload: {
    analysisId: string
    name: string
    analysisType: 'calibration' | 'bayesian'
    measurements: DraftMeasurement[]
    groups: DraftGroup[]
    relationships: DraftRelationship[]
    mcmc?: Partial<McmcConfig>
    projectId?: string
    // The saved analysis's own structure. Hydrating these is what lets an
    // edit keep the tree it was saved with: submitting without them sends
    // the legacy groups/relationships instead, and the backend rebuilds a
    // different structure from them.
    modelTree?: ModelElement | null
    elementAssignments?: Record<string, string>
  }) => void

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
  name: '',
  analysisType: 'calibration',
  projectId: '',
  dateFormat: DEFAULT_DATE_FORMAT,
  measurements: [],
  groups: [],
  relationships: [],
  mcmc: DEFAULT_MCMC,
  editingAnalysisId: null,
  modelTree: null,
  elementAssignments: {},

  setModelTree: (modelTree, assignments) =>
    set((state) => ({
      modelTree,
      elementAssignments: assignments ?? state.elementAssignments,
    })),

  setName: (name) => set({ name }),
  setAnalysisType: (analysisType) => set({ analysisType }),
  setProjectId: (projectId) => set({ projectId }),
  setDateFormat: (dateFormat) => set({ dateFormat }),
  setMcmc: (mcmc) => set((state) => ({ mcmc: { ...state.mcmc, ...mcmc } })),

  loadFromAnalysis: ({
    analysisId, name, analysisType, measurements, groups, relationships, mcmc,
    projectId, modelTree, elementAssignments,
  }) =>
    set({
      editingAnalysisId: analysisId,
      name,
      analysisType,
      projectId: projectId ?? '',
      measurements,
      groups,
      relationships,
      mcmc: { ...DEFAULT_MCMC, ...(mcmc || {}) },
      modelTree: modelTree ?? null,
      elementAssignments: elementAssignments ?? {},
    }),

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

  reset: () =>
    set({
      name: '',
      analysisType: 'calibration',
      projectId: '',
      dateFormat: DEFAULT_DATE_FORMAT,
      measurements: [],
      groups: [],
      relationships: [],
      mcmc: DEFAULT_MCMC,
      editingAnalysisId: null,
      modelTree: null,
      elementAssignments: {},
    }),
}))
