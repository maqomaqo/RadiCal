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
  tpq_age_bp?: number | null
  taq_age_bp?: number | null
}

export interface DraftMeasurement extends MatrixMeasurement {
  error_1sigma: number
  material_type: string
  sample_type: SampleType
  curve_id: string
  reservoir_age?: number
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

export interface DraftGroup {
  id: string
  name: string
  tpq_age_bp?: number | null
  taq_age_bp?: number | null
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
