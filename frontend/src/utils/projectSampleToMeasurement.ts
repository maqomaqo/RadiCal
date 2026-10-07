// Turning a date from a project's collection into a draft measurement, for
// the New Analysis picker that adds collection dates one at a time.
//
// The backend's deploy endpoint (api/project_samples.deploy_samples_to_analysis)
// does the same job for a whole selection at once, and the two must agree:
// a date picked here and a date deployed there are the same date, and an
// analysis built either way should not be able to tell them apart. So every
// sample field the deploy path copies is copied here too, and the test pins
// the list.
import type { ProjectSample } from '@/components/ProjectSampleTable'
import type { DraftMeasurement } from '@/types'

export function projectSampleToMeasurement(s: ProjectSample): DraftMeasurement {
  return {
    id: Math.random().toString(36).substr(2, 9),
    sample_name: s.sample_name,
    age_bp: s.age_bp,
    error_1sigma: s.error_1sigma,
    material_type: s.material_type || 'unknown',
    sample_type: s.sample_type,
    curve_id: s.curve_id || 'intcal20',
    reservoir_age: s.reservoir_age ?? null,
    // Carried rather than blanked: a reservoir offset belongs to where the
    // sample came from, so dropping it recalibrates a marine date without
    // its local correction -- which moves the answer, not just a label.
    delta_r: s.delta_r ?? null,
    delta_r_error: s.delta_r_error ?? null,
    // Carried too: a date whose ΔR kept its value but lost its derivation
    // would be an offset nobody could account for.
    delta_r_source: s.delta_r_source ?? null,
    // And for the same reason: where the sample came from travels with it.
    // Dropped here, a picked date would be invisible to a dispersal model and
    // to site grouping, both of which read the draft's own coordinates.
    latitude: s.latitude ?? null,
    longitude: s.longitude ?? null,
    location: s.location ?? null,
    // The two ordinates. A date that lost its depth cannot sit in a
    // deposition sequence, and one that lost its ring offset is no longer a
    // member of the wiggle match it was measured for.
    depth_cm: s.depth_cm ?? null,
    ring_offset: s.ring_offset ?? null,
    ring_offset_error: s.ring_offset_error ?? null,
    outlier_prior: 0,
    outlier_model: 'general',
    // How old the material may be is a fact about the sample too: dropped,
    // the date would be modelled with no inbuilt-age allowance.
    inbuilt_years: s.inbuilt_years ?? null,
    inbuilt_shape: s.inbuilt_shape ?? null,
    notes: s.notes ?? null,
    // The context the collection files this date under. Deploying turns
    // these into the analysis's contexts, and adding one at a time means the
    // same thing -- so it is carried, and bootstrapModelTree starts from it
    // rather than dropping every date into one phase.
    default_context_name: s.default_context_name ?? null,
    tpq_age_bp: s.tpq_age_bp ?? null,
    taq_age_bp: s.taq_age_bp ?? null,
  }
}
