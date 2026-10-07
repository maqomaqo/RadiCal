// A date picked from the collection must arrive carrying what a deployed one
// does. The list below is the deploy endpoint's
// (api/project_samples.deploy_samples_to_analysis), field by field.
import { describe, expect, it } from 'vitest'

import type { ProjectSample } from '@/components/ProjectSampleTable'
import { projectSampleToMeasurement } from './projectSampleToMeasurement'

// Every value distinct from the defaults, so a field that is dropped or
// replaced cannot pass by coincidence.
const sample: ProjectSample = {
  id: 'ps-1',
  sample_name: 'OxA-1234',
  age_bp: 3210,
  error_1sigma: 35,
  material_type: 'shell',
  sample_type: 'marine',
  curve_id: 'marine20',
  delta_r: -42,
  delta_r_error: 18,
  delta_r_source: { kind: 'calib_marine', snapshot: 'snap-7', points: [101, 102] },
  reservoir_age: 400,
  depth_cm: 125.5,
  ring_offset: 40,
  ring_offset_error: 2,
  inbuilt_years: 30,
  inbuilt_shape: 'exponential',
  latitude: 54.5,
  longitude: -5.6,
  location: 'Strangford Lough',
  default_context_name: 'Midden A',
  tpq_age_bp: 4000,
  taq_age_bp: 3000,
  notes: 'Articulated valve',
}

// What the deploy endpoint copies from a ProjectSample onto the analysis's
// own date. default_context_name is not on the list there because deploying
// turns it into a SampleGroup; here it is carried as the label that becomes
// one, and is checked separately below.
const DEPLOYED_FIELDS = [
  'sample_name',
  'age_bp',
  'error_1sigma',
  'material_type',
  'sample_type',
  'curve_id',
  'reservoir_age',
  'delta_r',
  'delta_r_error',
  'delta_r_source',
  'latitude',
  'longitude',
  'depth_cm',
  'ring_offset',
  'ring_offset_error',
  'inbuilt_years',
  'inbuilt_shape',
  'location',
  'notes',
  'tpq_age_bp',
  'taq_age_bp',
] as const

describe('projectSampleToMeasurement', () => {
  it('carries every field the deploy endpoint carries', () => {
    const m = projectSampleToMeasurement(sample)
    for (const field of DEPLOYED_FIELDS) {
      expect(m[field], field).toEqual(sample[field])
    }
  })

  it('carries the context the collection files the date under', () => {
    expect(projectSampleToMeasurement(sample).default_context_name).toBe('Midden A')
  })

  it('leaves nothing on the collection row behind but its id', () => {
    // A field added to ProjectSample later lands here as a failure, so the
    // picker cannot quietly fall behind the deploy path again.
    const m = projectSampleToMeasurement(sample) as unknown as Record<string, unknown>
    const dropped = Object.keys(sample).filter((k) => k !== 'id' && !(k in m))
    expect(dropped).toEqual([])
  })

  it('gives the draft date its own id, not the collection row\'s', () => {
    const a = projectSampleToMeasurement(sample)
    const b = projectSampleToMeasurement(sample)
    expect(a.id).not.toBe(sample.id)
    expect(a.id).not.toBe(b.id)
  })

  it('starts with no outlier model, as a typed-in date does', () => {
    const m = projectSampleToMeasurement(sample)
    expect(m.outlier_prior).toBe(0)
    expect(m.outlier_model).toBe('general')
  })

  it('fills the defaults a sparse row leaves blank', () => {
    const m = projectSampleToMeasurement({
      ...sample,
      material_type: '',
      curve_id: '',
      delta_r: null,
      delta_r_error: null,
      delta_r_source: null,
      latitude: null,
      longitude: null,
      depth_cm: null,
      ring_offset: null,
      ring_offset_error: null,
    })
    expect(m.material_type).toBe('unknown')
    expect(m.curve_id).toBe('intcal20')
    expect(m.delta_r_source).toBeNull()
    expect(m.latitude).toBeNull()
    expect(m.longitude).toBeNull()
    expect(m.ring_offset).toBeNull()
  })
})
