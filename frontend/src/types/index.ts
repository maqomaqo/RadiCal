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
// Two of these order their members by a distance measured on an axis OUTSIDE
// the model rather than by stratigraphy:
//
//   wiggle_sequence      OxCal's D_Sequence/V_Sequence. Dates separated by
//                        KNOWN gaps -- tree rings, varves -- so the whole
//                        series has ONE free age and every member's is that
//                        plus its own ring_offset.
//   deposition_sequence  OxCal's P_Sequence. Dates separated by DEPTH, with
//                        deposition modelled as a Poisson process along it.
//   interpolated_depth   A level inside a deposition sequence with no
//                        measurement -- "how old is 47 cm".
//
// Dates attach directly to the first two, as they do to a phase.
export type ElementType =
  | 'sequence'
  | 'phase'
  | 'boundary'
  | 'hiatus'
  | 'wiggle_sequence'
  | 'deposition_sequence'
  | 'interpolated_depth'

// The element types a date can be assigned to. Mirrors
// model_tree.DATE_HOLDERS.
export const DATE_HOLDER_TYPES: ElementType[] = [
  'phase',
  'wiggle_sequence',
  'deposition_sequence',
]

// Settings belonging to ONE kind of element, keyed by element_type. Mirrors
// model_tree.ELEMENT_PARAMS; every field optional, because an element written
// before a setting existed simply does not carry it and the backend fills in
// the default.
export interface ElementParams {
  // wiggle_sequence: what a gap is counted in, and which member offset 0 was
  // measured from.
  gap_units?: 'rings' | 'varves' | 'years'
  reference_sample_id?: string | null
  // deposition_sequence: the rigidity, and the log10 range it is inferred
  // over when it is not fixed.
  k?: number | null
  k_variable?: boolean
  log_k_min?: number
  log_k_max?: number
  depth_units?: string
  // interpolated_depth: the depth the level sits at.
  depth_cm?: number | null
}

export interface ModelElement {
  id: string
  element_type: ElementType
  name?: string | null
  // Age limiters in cal BP, applying to everything nested inside this
  // element; the tightest of an element and its ancestors wins.
  tpq_age_bp?: number | null
  taq_age_bp?: number | null
  params?: ElementParams | null
  // Set once the analysis has been run, on the element types that carry a
  // parameter of their own: a boundary's transition, a wiggle match's
  // reference age, an undated level's age.
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
  // The two ORDINATES: where this date sits on an axis outside the model.
  // Declared here rather than only on DraftMeasurement because the tree
  // editor's own validation needs them -- whether a date may go into a
  // deposition sequence or a wiggle match depends on whether it carries the
  // one that sequence is built on, and that check belongs on the screen the
  // date is moved on.
  depth_cm?: number | null
  // Years BEFORE the series reference point: 0 AT the reference and
  // INCREASING INTO THE PAST, so a member's calendar age is the reference's
  // PLUS its offset.
  //
  // The sign is the thing to get right. Rings count forward and cal BP counts
  // backward, so an inverted offset gives a tight answer in the WRONG PLACE
  // rather than an obviously wrong one. utils/ringOffsets converts ring
  // NUMBERS, which is what people actually have, into these.
  ring_offset?: number | null
  // The gap's own 1-sigma uncertainty, where it was measured rather than
  // counted (OxCal's V_Sequence). Absent or zero means the gaps are exact.
  ring_offset_error?: number | null
  // Where the sample is from -- as a coordinate, and as the placename and
  // context it was filed under. Declared here, not only on DraftMeasurement,
  // because the tree editor groups dates into SITES on them
  // (utils/siteGrouping) and sends the coordinates with a spatial-order
  // proposal, and a saved analysis's measurements carry them just as a
  // draft's do.
  latitude?: number | null
  longitude?: number | null
  location?: string | null
  default_context_name?: string | null
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
  // Where the sample came from, in WGS84 decimal degrees. Carried through the
  // draft for the same reason as reservoir_age above -- a date deployed from a
  // collection arrives placed, and an edit that dropped these would unplace it
  // -- and the two are one fact: the API refuses a latitude without its
  // longitude, as it refuses a ΔR without its error.
  latitude?: number | null
  longitude?: number | null
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

// --- age against depth ---
//
// Mirrors services/intervals.py's age_depth section. Every figure here is read
// off JOINT draws: an accumulation rate is a function of two ages at once, and
// pairing their marginals gives a rate distribution wrong in SHAPE rather than
// scale -- which no eyeball check catches.

export interface AgeDepthLevel {
  // A dated sample's id, or an undated level's element id.
  id: string
  name: string
  depth: number
  dated: boolean
  // Calendar ages, cal BP. Bands are QUANTILES rather than HPD regions, so
  // they nest and an envelope drawn from them cannot cross itself.
  median: number
  mean: number
  band_68: [number, number]
  band_95: [number, number]
}

export interface AccumulationStep {
  from_depth: number
  to_depth: number
  from_name: string
  to_name: string
  depth_gap: number
  // How long the interval took, in the same shape every other duration takes.
  years: DurationSummary
  // ...and the same thing per unit depth, which is what an accumulation rate
  // is and what the literature quotes.
  rate_median: number
  rate_band_68: [number, number]
}

export interface AgeDepthRecord {
  element_id: string
  name: string
  depth_units: string
  levels: AgeDepthLevel[]
  accumulation: AccumulationStep[]
}

// --- wave of advance ---
//
// Mirrors services/dispersal.py. A speed here is km/yr and t0 is a CALENDAR
// age, which is why the two take different shapes: t0 carries the calendar key
// names (median/band_68/band_95, as AgeDepthLevel does) so it can be formatted
// BC/AD, and the speed carries the duration names so it cannot be.

export interface DispersalSite {
  element_id: string
  name: string
  // The parameter carrying this site's arrival: an opening Boundary, or --
  // for a group calibration -- its earliest dated event.
  parameter_id: string
  // 'boundary' is an arrival estimate. 'earliest_dated_event' is NOT: it is a
  // terminus ante quem, and the panel must say so wherever it shows one.
  basis: 'boundary' | 'earliest_dated_event'
  latitude: number | null
  longitude: number | null
  date_ids: string[]
  // Largest distance between any two of the site's dates, in km.
  scatter_km: number | null
  // Why this site could not be used, in the user's words. Null when it could.
  refusal: string | null
}

export interface DispersalResidual {
  element_id: string
  name: string
  distance_km: number
  // Signed years. POSITIVE means the site is OLDER than the front predicts --
  // the thing arrived there before the line says it should have.
  residual_years: number
  residual_sd: number
}

export interface DispersalResidualPlain {
  element_id: string
  name: string
  residual_years: number
  residual_sd: number
}

/** A direction, summarised on the circle rather than as a number. */
export interface CircularSummary {
  mean_deg: number
  /** Bearings, so 350–10 is a twenty-degree interval and not a 340-degree one. */
  hpd_95_deg: [number, number]
  /** How wide that interval actually is, which the pair above does not show. */
  width_deg: number
  /**
   * R, the mean resultant length, in [0, 1]. Near zero the draws point every
   * which way and the mean direction is an artefact of the arithmetic — so
   * this is what tells "north, confidently" from "north, meaninglessly".
   */
  concentration: number
}

/** One line of equal arrival age across the fitted surface. */
export interface Isochron {
  age: number
  points: Array<{ latitude: number; longitude: number }>
}

/**
 * Model 2: a plane through arrival age against position.
 *
 * Assumes NO origin, which is what makes it the right first look at a dataset
 * — before a hypothesis has been imposed on it. Where the wave answers "how
 * fast did it move away from here", this answers "is there a gradient at all,
 * and which way does it point".
 */
export interface TrendFit {
  sites: DispersalSite[]
  n_sites: number
  min_sites: number
  refused: string | null
  speed: DurationSummary | null
  azimuth: CircularSummary | null
  /** The projection centre the plane was fitted in. */
  centre: { lat: number; lon: number } | null
  isochrons: Isochron[]
  residuals: DispersalResidualPlain[]
  basis: 'boundary' | 'earliest_dated_event' | null
  draws?: number
  degenerate_draws?: number
}

/**
 * Where the front could have started, as a surface rather than a point.
 *
 * A REGION deliberately. The profile is nearly flat along the front's own
 * axis — sliding the origin backwards along the line of travel and adjusting
 * t₀ to match fits almost as well — which is a real property of the model, so
 * the peak is not a location and reporting it as one would be false
 * precision.
 */
export interface OriginProfile {
  n_sites: number
  min_sites: number
  refused: string | null
  /** Grid spacing actually used; widens for a continental spread. */
  step_deg: number | null
  candidates: number
  /** The peak of something broad — not "the origin". */
  peak: { lat: number; lon: number } | null
  region: Array<{ lat: number; lon: number; support: number }>
  /** The likelihood-ratio contour the region is drawn at. */
  cutoff: number
  /**
   * Share of the searched grid inside the region. Near 1 means the data do
   * not locate an origin at all — a finding, and the thing a single
   * best-guess marker would have hidden.
   */
  share_inside?: number
}

/** The spatial models, side by side. */
export interface DispersalSection {
  /** Null when no origin was placed: there is nothing to measure a front from. */
  wave: DispersalFit | null
  trend: TrendFit | null
  origin_profile?: OriginProfile | null
}

export interface DispersalFit {
  origin: { lat: number; lon: number }
  sites: DispersalSite[]
  // Sites that actually went into the fit, which is not sites.length.
  n_sites: number
  min_sites: number
  // Set when no speed was reported, and says why. A refusal is a result: the
  // rest of the analysis is unaffected.
  refused: string | null
  // km/yr. Negative means the far sites are OLDER than the near ones -- a
  // finding about the origin, not an error to be hidden behind an absolute.
  speed: DurationSummary | null
  // Calendar age at the origin, cal BP.
  t0: { median: number; mean: number; band_68: [number, number]; band_95: [number, number] } | null
  residuals: DispersalResidual[]
  basis: 'boundary' | 'earliest_dated_event' | null
  distances?: number[]
  draws?: number
  // Draws whose slope was exactly zero -- an infinite speed, excluded from the
  // summary. Reported so "one draw" and "most of them" are distinguishable.
  degenerate_draws?: number
}

export interface IntervalsBlock {
  durations: DurationRecord[]
  differences: DifferenceRecord[]
  // One entry per deposition sequence in the model; empty otherwise.
  age_depth?: AgeDepthRecord[]
  // NULL means the fit was not asked for, which the page reads as an offer to
  // re-run. A fit that was asked for and refused is a section whose models
  // carry `refused`, which is a different thing.
  dispersal?: DispersalSection | null
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
