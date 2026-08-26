// Shared types for the "New Analysis" -> "Stratigraphy" draft flow: a Bayesian
// analysis is built client-side across two pages before one POST
// /api/analysis/bayesian submits everything. These mirror the backend's
// schemas/sample.py field names and semantics, so the payload needs no
// reshaping.

export type SampleType = 'terrestrial' | 'marine'

export type RelationshipType = 'above' | 'below' | 'equal'

// A relationship endpoint can point at a sample or a named group. The schema
// stays generic, but the Stratigraphy UI only ever creates 'group' endpoints on
// both sides: the workflow is "assign each sample to a Context, then relate
// Contexts", not sample-to-sample. "Context" is what the user is shown for what
// this codebase calls a "group" -- the archaeological term for a stratigraphic
// layer, cut or fill a set of dated samples come from.
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

// The subset of a measurement's fields the Harris matrix and the client-side
// cycle check need. DraftMeasurement satisfies it structurally, and so does the
// much smaller object GET /api/analysis/:id returns per saved sample -- so
// Results.tsx can pass persisted measurements into the same diagram without
// fabricating fields that view neither has nor needs.
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
  // Prior probability this date does not belong to the population being
  // modelled -- residual charcoal, intrusive material, lab error. Zero or
  // absent means no outlier model applies. `outlier_model` picks which:
  // "general" is symmetric, "charcoal" one-sided (the material predates its
  // context). Declared here rather than only on DraftMeasurement because a
  // saved analysis's results need it too, to tell a posterior outlier
  // probability the data found from one reporting back the analyst's prior.
  outlier_prior?: number | null
  outlier_model?: 'general' | 'charcoal' | null
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
  // The context this date arrived already filed under: a project
  // collection's default_context_name, or the phase named in an imported
  // file. A LABEL, not a reference -- there is no phase for it to point at
  // until a model structure exists, and building that structure is exactly
  // what it feeds (see utils/modelTreeDefaults.bootstrapModelTree, which
  // turns these into the starting phases). Carried through the draft so
  // that grouping survives the trip from the collection to the model
  // instead of having to be re-typed from memory.
  default_context_name?: string | null
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

// --- durations and differences ---
//
// Mirrors backend/app/services/intervals.py. Deliberately NOT shaped like
// PosteriorSummary above: every figure here is a LENGTH OF TIME in years, not
// a calendar age, so none of it may go through formatCalendarAge or the
// BC/AD conversion. Different field names are what makes that mistake
// impossible rather than merely discouraged.
export interface DurationSummary {
  mean: number
  median: number
  std: number
  hpd_68: [number, number] | null
  hpd_95: [number, number] | null
  hpd_68_ranges?: HPDRange[]
  hpd_95_ranges?: HPDRange[]
  // The bin width the ranges above are quoted at, in years. Widens for a
  // duration spanning millennia, so a reader can see what resolution the
  // answer is actually at.
  step: number
  distribution: Array<[number, number]>
}

export type DurationKind = 'sequence_duration' | 'phase_duration' | 'sample_duration'

export interface DurationRecord {
  kind: DurationKind
  id: string
  name: string
  // The model element this is about, when exactly one -- null for a bounded
  // group holding several.
  element_id?: string | null
  // How many dated events a sample duration spans; null for the others.
  dates?: number | null
  years: DurationSummary
}

export interface DifferenceRecord {
  kind: 'phase_difference' | 'date_difference'
  id: string
  from_id: string
  from_name: string
  to_id: string
  to_name: string
  // Signed years. For two phases, positive is a gap and negative an overlap;
  // for two dates, positive means `from` is the older.
  years: DurationSummary
  // 0-1: how far the two are from being separable in time. For phases, the
  // probability their modelled intervals intersect; for dates, the probability
  // the model cannot say which came first.
  overlap: number
  severity: 'none' | 'slight' | 'moderate' | 'substantial' | 'extensive'
  // Dates only: P(`from` is older than `to`).
  older_probability?: number
  // Phases only: the two share a Boundary, so their gap is zero because the
  // model says so rather than because the dates do.
  shared_boundary?: boolean
}

export interface IntervalsBlock {
  durations: DurationRecord[]
  differences: DifferenceRecord[]
  // How many joint draws these were estimated from, and whether those draws
  // were independent (the exact solver, or a group calibration) or an MCMC
  // chain, where the effective count is smaller than the count.
  draws: number
  independent: boolean
  // Set when the difference table had to be cut down -- see MAX_PAIRS on the
  // backend.
  note?: string | null
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

// The two per-date diagnostics a Bayesian run produces, as the shared
// outlier rule in utils/diagnostics reads them. A structural subset of the
// results page's SampleResult, so a stored result can be passed straight in
// -- and small enough that the edit page can carry just these two numbers
// per date without dragging a full result along.
export interface DateDiagnostics {
  agreement_index?: number | null
  outlier_probability?: number | null
}
