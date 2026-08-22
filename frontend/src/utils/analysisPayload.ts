// The request body POST /analysis/bayesian and PUT /analysis/{id}/inputs
// both take, built from the New Analysis / Edit Analysis draft.
//
// Extracted from the page rather than inlined there because of one rule that
// is easy to get wrong and impossible to see going wrong: a non-Bayesian
// analysis has no structure, so an edit that switches away from Bayesian has
// to CLEAR its contexts, relationships, model tree and per-date phase
// assignments -- not merely stop sending them. A date left pointing at a
// context that is no longer in the payload is rejected as a dangling
// reference, and a model tree left behind would have the results page drawing
// a Harris matrix for an analysis that no longer has one.
import type { DraftGroup, DraftMeasurement, DraftRelationship, ModelElement } from '@/types'

export type AnalysisType = 'calibration' | 'group_calibration' | 'bayesian'

export interface AnalysisDraft {
  name: string
  analysisType: AnalysisType
  measurements: DraftMeasurement[]
  groups: DraftGroup[]
  relationships: DraftRelationship[]
  // The explicit model structure, when one has been authored on the Model
  // Structure page. Null falls back to contexts + relationships, which the
  // backend converts into an equivalent tree.
  modelTree: ModelElement | null
  // Which phase each date sits in, keyed by measurement id. Only meaningful
  // alongside a model tree.
  elementAssignments: Record<string, string>
  projectId: string
  mcmc: unknown
}

export function buildInputsPayload(draft: AnalysisDraft): Record<string, unknown> {
  const structured = draft.analysisType === 'bayesian'
  const tree = structured ? draft.modelTree : null

  const payload: Record<string, unknown> = {
    name: draft.name,
    // What the analysis should BE. PUT /inputs converts an existing analysis
    // to it; POST /analysis/bayesian ignores it, since that route creates a
    // Bayesian analysis by definition.
    analysis_type: draft.analysisType,
    measurements: structured
      ? tree
        ? draft.measurements.map((m) => ({ ...m, element_id: draft.elementAssignments[m.id] || null }))
        : draft.measurements
      : draft.measurements.map((m) => ({ ...m, group_id: null, element_id: null })),
    groups: structured && !tree ? draft.groups : [],
    relationships:
      structured && !tree
        ? draft.relationships.map(({ id: _id, ...rest }) => rest)
        : [],
    project_id: draft.projectId || null,
    bayesian_config: draft.mcmc,
  }
  if (tree) payload.model_tree = tree
  return payload
}
