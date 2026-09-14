// Shared draft state for building a Bayesian analysis across two pages:
// NewAnalysis.tsx (measurements) and Stratigraphy.tsx (the model tree and
// TPQ/TAQ). Neither persists anything until "Run Analysis", so this holds the
// draft across the navigation between them. Cleared via reset() after a
// successful run.
//
// Mirrored into sessionStorage because in memory alone was not enough: this
// IS the user's unsaved work, and a reload of either page dropped all of it
// -- typed dates, the authored model tree, the phase assignments -- with no
// warning and no way back. A refresh on /new-analysis/model-structure left
// that page reading "add measurements on the New Analysis page first" for a
// model the user had just finished building.
//
// sessionStorage rather than localStorage on purpose: a draft belongs to the
// tab it is being built in. localStorage would resurrect it in every other
// tab and on every future visit, which is a different promise and the wrong
// one -- reset() after a successful run is what ends a draft's life.
import { create, type StateCreator } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import type { DraftMeasurement, DraftGroup, DraftRelationship, ModelElement } from '@/types'
import type { Concern } from '@/utils/diagnostics'
import { DateFormat, DEFAULT_DATE_FORMAT } from '@/utils/dateFormat'
import { phaseForContext } from '@/utils/modelTreeDefaults'

export type SolverChoice = 'auto' | 'exact' | 'gibbs'

export interface McmcConfig {
  // Which solver to use: 'auto' picks per model, the others override it. See
  // BayesianConfigInput.solver on the backend for what each one does.
  solver: SolverChoice
  mcmc_samples: number
  mcmc_tune: number
  mcmc_chains: number
  // OxCal's UniformSpanPrior (Nicholls and Jones 2001) -- see
  // BayesianConfigInput.uniform_span_prior on the backend for what it does.
  // On by default there and here.
  uniform_span_prior: boolean
  // Also estimate durations and separations. Off by default, matching
  // BayesianConfigInput.estimate_durations: a duration is a claim about a
  // phase's extent the analyst makes, not one the app makes for them.
  estimate_durations: boolean
}

// How many posterior samples a result CARRIES, not how long the sampler runs:
// services/gibbs.py extends its own run until it converges, and the exact
// solver has no run length at all.
export const DEFAULT_MCMC: McmcConfig = {
  solver: 'auto',
  mcmc_samples: 4000,
  mcmc_tune: 1000,
  mcmc_chains: 4,
  uniform_span_prior: true,
  estimate_durations: false,
}

interface AnalysisDraftState {
  name: string
  analysisType: 'calibration' | 'group_calibration' | 'bayesian'
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
  // What the LAST run said about each date, keyed by measurement id, for the
  // dates it objected to. Carried into the edit form so the reader arriving
  // from the results table's "Edit analysis" button does not have to
  // remember which dates were flagged -- the reason to be editing at all is
  // usually one of these, and the table it names them in is a page away.
  //
  // Only the flagged ones: an entry here IS the flag. Empty for a new draft,
  // and stale by definition once a date is changed -- the form says so
  // rather than dropping it, since a date being edited BECAUSE it was
  // flagged should not lose the note the moment it is touched.
  lastRunConcerns: Record<string, Concern>
  // The explicit model structure being authored, and which phase each date
  // sits in. This is the stratigraphy for a new Bayesian analysis -- it is
  // submitted as-is rather than being derived from contexts and pairwise
  // relationships. Null until the Stratigraphy page initialises one.
  modelTree: ModelElement | null
  elementAssignments: Record<string, string>
  setModelTree: (tree: ModelElement | null, assignments?: Record<string, string>) => void

  setName: (name: string) => void
  setAnalysisType: (analysisType: 'calibration' | 'group_calibration' | 'bayesian') => void
  setProjectId: (projectId: string) => void
  setDateFormat: (dateFormat: DateFormat) => void
  setMcmc: (mcmc: Partial<McmcConfig>) => void
  loadFromAnalysis: (payload: {
    analysisId: string
    name: string
    analysisType: 'calibration' | 'group_calibration' | 'bayesian'
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
    lastRunConcerns?: Record<string, Concern>
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

const createDraftState: StateCreator<AnalysisDraftState> = (set) => ({
  name: '',
  analysisType: 'calibration',
  projectId: '',
  dateFormat: DEFAULT_DATE_FORMAT,
  measurements: [],
  groups: [],
  relationships: [],
  mcmc: DEFAULT_MCMC,
  editingAnalysisId: null,
  lastRunConcerns: {},
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
    projectId, modelTree, elementAssignments, lastRunConcerns,
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
      lastRunConcerns: lastRunConcerns ?? {},
    }),

  setMeasurements: (measurements) => set({ measurements }),

  addMeasurement: (measurement) =>
    set((state) => {
      // A date added once the tree exists is filed under the phase its
      // context names, when there is exactly one. Otherwise it arrives
      // unassigned and the Model Structure page asks where it goes.
      const phaseId = phaseForContext(state.modelTree, measurement.default_context_name)
      return {
        measurements: [...state.measurements, measurement],
        elementAssignments: phaseId
          ? { ...state.elementAssignments, [measurement.id]: phaseId }
          : state.elementAssignments,
      }
    }),

  removeMeasurement: (id) =>
    set((state) => ({
      measurements: state.measurements.filter((m) => m.id !== id),
      elementAssignments: Object.fromEntries(
        Object.entries(state.elementAssignments).filter(([mid]) => mid !== id)
      ),
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
      lastRunConcerns: {},
      modelTree: null,
      elementAssignments: {},
    }),
})

export const useAnalysisDraftStore = create<AnalysisDraftState>()(
  persist(createDraftState, {
    name: 'radical-analysis-draft',
    storage: createJSONStorage(() => sessionStorage),
    // Data only. The actions are rebuilt by the creator on every load, so
    // persisting them would write dead function references into storage and
    // rehydrate over the live ones.
    partialize: (state) => ({
      name: state.name,
      analysisType: state.analysisType,
      projectId: state.projectId,
      dateFormat: state.dateFormat,
      measurements: state.measurements,
      groups: state.groups,
      relationships: state.relationships,
      mcmc: state.mcmc,
      editingAnalysisId: state.editingAnalysisId,
      lastRunConcerns: state.lastRunConcerns,
      modelTree: state.modelTree,
      elementAssignments: state.elementAssignments,
    }),
  })
)
