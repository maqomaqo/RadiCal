// Shared types for the "New Analysis" -> "Stratigraphy" draft flow. A
// Bayesian analysis is built up client-side (measurements, then optional
// groups/relationships/TPQ-TAQ limiters) across two pages -- NewAnalysis.tsx
// and Stratigraphy.tsx -- before a single POST /api/analysis/bayesian
// submits everything at once. These mirror the backend's
// schemas/sample.py field names/semantics directly so the payload sent to
// that endpoint doesn't need any reshaping.

export type SampleType = 'terrestrial' | 'marine'

export type RelationshipType = 'above' | 'below' | 'equal'

// A relationship endpoint (from/to) can point at either a single sample
// or a named group of samples -- the schema stays generic, but the
// Stratigraphy UI (see pages/Stratigraphy.tsx) only ever creates 'group'
// endpoints on both sides: the workflow is "assign each sample to a
// Context, then relate Contexts to each other," not sample-to-sample.
// "Context" is the label shown to the user for what this codebase calls
// a "group" everywhere else (DraftGroup, group_id, SampleGroup on the
// backend) -- the archaeological term for a stratigraphic layer/cut/fill
// that a set of dated samples come from.
export type NodeType = 'sample' | 'group'

// --- Explicit model structure (OxCal-style Sequence/Phase/Boundary) ---
//
// Mirrors backend/app/services/model_tree.py. `children` are ordered
// OLDEST FIRST, matching OxCal's reading order and the Harris matrix
// convention of oldest at the bottom. A Boundary placed between two Phases
// inside a Sequence is a SINGLE shared parameter -- the end of the earlier
// phase and the start of the later one at once -- not two independent ones.
export type ElementType = 'sequence' | 'phase' | 'boundary' | 'hiatus'

export interface ModelElement {
  id: string
  element_type: ElementType
  name?: string | null
  // Age limiters in cal BP, applying to everything nested inside this
  // element; the tightest of an element and its ancestors wins.
  tpq_age_bp?: number | null
  taq_age_bp?: number | null
  // Set on boundary elements once the analysis has been run.
  posterior?: PosteriorSummary | null
  children?: ModelElement[]
}

// The subset of a measurement's fields the Harris matrix diagram and the
// client-side cycle check actually need. DraftMeasurement (the full
// in-progress form used on NewAnalysis/Stratigraphy) satisfies this
// structurally, but so does the much smaller object GET /api/analysis/:id
// returns for each already-saved sample -- letting Results.tsx pass real
// persisted measurements straight into the same diagram component (in
// read-only mode) without fabricating fields like material_type/curve_id
// that view doesn't have and doesn't need.
export interface MatrixMeasurement {
  id: string
  sample_name: string
  age_bp: number
  error_1sigma?: number
  group_id?: string | null
  // Which model-tree phase this date sits in; supersedes group_id.
  element_id?: string | null
  tpq_age_bp?: number | null
  taq_age_bp?: number | null
}

export interface DraftMeasurement extends MatrixMeasurement {
  error_1sigma: number
  material_type: string
  sample_type: SampleType
  curve_id: string
  // Local marine reservoir offset (Delta-R) and its 1-sigma uncertainty, in
  // radiocarbon years. Applied to the calibration: a marine curve models
  // only the global average reservoir effect, so this is the local deviation
  // from it. The error is required whenever the offset is set.
  delta_r?: number | null
  delta_r_error?: number | null
  // Recorded against the date but not used in the calculation. Carried
  // through the draft so editing a saved analysis resubmits them instead
  // of blanking them -- dates deployed from a project collection arrive
  // with these already filled in.
  reservoir_age?: number | null
  depth_cm?: number | null
  location?: string | null
  notes?: string | null
  // Stratigraphic phase/group this sample belongs to, if any -- must
  // match a DraftGroup.id.
  group_id?: string | null
  // Age limiters in cal BP: tpq_age_bp (terminus post quem) is a MAXIMUM
  // age bound -- "not older than" -- and taq_age_bp (terminus ante quem)
  // is a MINIMUM age bound -- "not younger than". Valid range is
  // [taq_age_bp, tpq_age_bp].
  tpq_age_bp?: number | null
  taq_age_bp?: number | null
}

// A summarized posterior distribution -- the shape returned for both a
// sample's calendar age and (see DraftGroup.modeled_start/modeled_end
// below) a context's modelled span boundaries. posterior_samples (the
// raw MCMC draws) is deliberately not part of this -- the backend strips
// it before sending a response, since the frontend only ever needs the
// binned distribution/summary stats to render a plot.
export interface PosteriorSummary {
  posterior_mean: number
  posterior_median: number
  posterior_std: number
  hpd_68: [number, number]
  hpd_95: [number, number]
  hpd_68_ranges?: HPDRange[]
  hpd_95_ranges?: HPDRange[]
  probability_distribution: Array<[number, number]>
}

export interface DraftGroup {
  id: string
  name: string
  tpq_age_bp?: number | null
  taq_age_bp?: number | null
  // The MODELLED start/end of this context's own span (the posterior of
  // its oldest/youngest dated member respectively) -- present only for a
  // saved Bayesian analysis's context with 2+ dated members; absent
  // everywhere else (the draft/editing flow never sets these, they only
  // ever come back from GET /api/analysis/:id after a run).
  modeled_start?: PosteriorSummary | null
  modeled_end?: PosteriorSummary | null
}

export interface DraftRelationship {
  // Client-side id, used only for list rendering/deletion -- not sent to
  // the backend (which derives its own persisted relationship ids).
  id: string
  from_sample_id: string
  to_sample_id: string
  from_type: NodeType
  to_type: NodeType
  relationship: RelationshipType
}

// A single disjoint range within a (possibly multimodal) HPD region, with
// its own share of the total probability. Calibration curves are
// non-monotonic, so a sample's posterior can split into two or more of
// these instead of one contiguous span -- see hpd_68_ranges/hpd_95_ranges
// on results returned by the API.
export interface HPDRange {
  min: number
  max: number
  probability_percent: number
}
