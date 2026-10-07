import {
  Autocomplete,
  Box,
  FormControl,
  FormControlLabel,
  FormHelperText,
  InputLabel,
  MenuItem,
  Select,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import type { DraftMeasurement } from '@/types'
import { curveLabel, curveOptions, curvesForSampleType, type CurveSpec } from '@/api/curves'
import { INBUILT_SHAPES, describeInbuilt, type InbuiltShape } from '@/utils/inbuiltAge'
import { useCurves } from '@/hooks/useCurves'

/**
 * The fields that describe one radiocarbon date.
 *
 * Shared between adding a date and editing one already in the list, rather
 * than two copies of the same ninety lines of JSX: a field added to one copy
 * and not the other is invisible until someone notices an empty column.
 */

/**
 * Suggestions for the Material field. Free text is still accepted -- this
 * is a convenience list, not a restriction.
 */
export const COMMON_MATERIAL_TYPES = [
  'charcoal', 'wood', 'bone', 'shell', 'seed', 'charred residue',
  'peat', 'sediment', 'textile', 'leather', 'antler', 'tooth',
]

/**
 * Which curve to switch to when a sample type is selected, and whether the
 * curve already chosen may stay.
 *
 * Marine carbon must be calibrated against a marine curve -- IntCal20 would be
 * wrong by centuries -- while a terrestrial sample picks between hemispheres.
 * Which curves are which comes from the backend's registry, so a curve added
 * there is offered here without a code change.
 *
 * A deliberate choice that already suits the sample type is left alone;
 * anything else moves to the first curve the registry lists for it.
 */
export function curveForSampleType(
  curves: CurveSpec[],
  sampleType: string,
  currentCurveId: string | undefined
): string | undefined {
  const suitable = curvesForSampleType(curves, sampleType)
  if (suitable.length === 0) return currentCurveId
  if (currentCurveId && suitable.some((curve) => curve.id === currentCurveId)) {
    return currentCurveId
  }
  return suitable[0].id
}

export type MeasurementDraft = Partial<DraftMeasurement>

interface Props {
  value: MeasurementDraft
  onChange: (next: MeasurementDraft) => void
  /**
   * Outlier settings are Bayesian-only: a single calibration has no model for
   * a date to be an outlier FROM, so the control would be inert.
   */
  showOutlier?: boolean
  /**
   * The ORDINATES -- where this date sits on an axis outside the model.
   *
   * Offered where they mean something rather than on every date, the same rule
   * ΔR follows above: a gap belongs to a wiggle-matched series and a depth to
   * a core, and a stray value on a date in neither would be carried, exported
   * and eventually read as one. The caller decides, because deciding needs the
   * model tree -- see NewAnalysis.ordinatesFor.
   */
  showRingOffset?: boolean
  showDepth?: boolean
}

/**
 * Whether this date is marine carbon, and so whether a reservoir offset
 * applies to it.
 *
 * The sample type answers it outright when set; otherwise the curve does,
 * looked up in the registry rather than compared against a literal, so an
 * imported marine curve counts the same as Marine20. With no curve list to
 * hand it falls back to the one marine curve this app ships.
 */
export function isMarineMeasurement(m: MeasurementDraft, curves: CurveSpec[] = []): boolean {
  if ((m.sample_type || 'terrestrial') === 'marine') return true
  const spec = curves.find((curve) => curve.id === m.curve_id)
  if (spec) return spec.sample_type === 'marine'
  return m.curve_id === 'marine20'
}

export function deltaRErrorMissing(m: MeasurementDraft): boolean {
  return m.delta_r != null && m.delta_r_error == null
}

/**
 * Whether one half of a coordinate has been entered without the other.
 *
 * The same shape as deltaRErrorMissing above, and the same reason: the API
 * refuses half a position, because a date that looks placed but is not would
 * drop silently out of every map and every distance drawn from it. Caught in
 * the form so the reader sees which box to fill rather than a 422.
 */
export function coordinateHalfMissing(m: MeasurementDraft): boolean {
  return (m.latitude == null) !== (m.longitude == null)
}

export default function MeasurementFields({
  value,
  onChange,
  showOutlier = false,
  showRingOffset = false,
  showDepth = false,
}: Props) {
  const { curves, loading: curvesLoading, error: curvesError } = useCurves()
  const set = (patch: MeasurementDraft) => onChange({ ...value, ...patch })
  const marine = isMarineMeasurement(value, curves)
  const missingError = deltaRErrorMissing(value)
  const coordinateMissing = coordinateHalfMissing(value)
  const outlierOn = (value.outlier_prior ?? 0) > 0

  // The curves this sample type can actually be calibrated against. Falls
  // back to the whole list when the registry types none of them for this
  // sample type -- an empty picker would be worse than an unfiltered one, and
  // an imported curve with an unfamiliar type must stay reachable.
  const sampleType = value.sample_type || 'terrestrial'
  const forType = curvesForSampleType(curves, sampleType)
  const suitableCurves = forType.length > 0 ? forType : curves
  // Flagged rather than blocked: an expert may have a deliberate reason, and
  // an imported curve may be typed in a way the registry does not recognise.
  // What is not acceptable is the mismatch passing without a word.
  const currentSpec = curves.find((curve) => curve.id === (value.curve_id || 'intcal20'))
  const curveMismatch = !!currentSpec && currentSpec.sample_type !== sampleType

  return (
    <>
      <Box sx={{ mb: 2 }}>
        <TextField
          fullWidth
          required
          label="Sample name"
          value={value.sample_name || ''}
          onChange={(e) => set({ sample_name: e.target.value })}
          size="small"
        />
      </Box>

      <Box sx={{ mb: 2 }}>
        <Autocomplete
          freeSolo
          options={COMMON_MATERIAL_TYPES}
          value={value.material_type || ''}
          onInputChange={(_, next) => set({ material_type: next })}
          renderInput={(params) => (
            <TextField {...params} label="Material" placeholder="e.g., charcoal, bone, shell" size="small" />
          )}
        />
      </Box>

      <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
        <TextField
          type="number"
          required
          label="Age (BP)"
          value={value.age_bp ?? ''}
          onChange={(e) =>
            set({ age_bp: e.target.value === '' ? undefined : parseFloat(e.target.value) })
          }
          size="small"
          sx={{ flex: 1 }}
          // The range services/schemas/sample.py already enforces. Stated here
          // so it is a constraint on the way in rather than a 422 on the way
          // back -- and so the field gets native spinner and validation
          // behaviour for free.
          inputProps={{ min: 0, max: 60000, step: 10 }}
        />
        <TextField
          type="number"
          required
          // The unit was the one thing this label left implicit, while its own
          // sibling ΔR field names it. Both are radiocarbon years.
          label="1σ error (yr)"
          value={value.error_1sigma ?? ''}
          onChange={(e) =>
            set({ error_1sigma: e.target.value === '' ? undefined : parseFloat(e.target.value) })
          }
          size="small"
          sx={{ flex: 1 }}
          inputProps={{ min: 1, max: 10000, step: 1 }}
        />
      </Box>

      <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
        <FormControl size="small" sx={{ flex: 1 }}>
          <InputLabel id="sample-type-label">Sample type</InputLabel>
          <Select
            labelId="sample-type-label"
            value={value.sample_type || 'terrestrial'}
            label="Sample type"
            onChange={(e) => {
              const sampleType = e.target.value as 'terrestrial' | 'marine'
              set({
                sample_type: sampleType,
                // Follow the sample type with a curve that suits it, unless
                // a deliberate choice has already been made that does.
                curve_id: curveForSampleType(curves, sampleType, value.curve_id),
              })
            }}
          >
            <MenuItem value="terrestrial">Terrestrial</MenuItem>
            <MenuItem value="marine">Marine</MenuItem>
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ flex: 1 }} error={!!curvesError || curveMismatch}>
          <InputLabel id="curve-label">Curve</InputLabel>
          <Select
            labelId="curve-label"
            value={value.curve_id || 'intcal20'}
            label="Curve"
            disabled={curvesLoading}
            onChange={(e) => set({ curve_id: e.target.value })}
          >
            {/* Narrowed to the curves that suit this sample type, because the
                full list made the app's own worst case reachable in silence:
                marine carbon left on IntCal20, ΔR still applying, and a
                clean-looking date wrong by centuries. Following the sample
                type only guarded the other direction -- changing the sample
                type moved the curve, changing the curve checked nothing.

                Still built through curveOptions, so it always includes
                whatever this date is already set to: a slow or failed curve
                list must not blank a curve that was chosen, and an imported
                curve the registry types differently stays selectable rather
                than vanishing from its own date. */}
            {curveOptions(suitableCurves, value.curve_id || 'intcal20').map((option) => (
              <MenuItem key={option.id} value={option.id}>
                {option.label}
              </MenuItem>
            ))}
          </Select>
          {curvesError ? (
            <FormHelperText>{curvesError}</FormHelperText>
          ) : curveMismatch ? (
            <FormHelperText>
              {curveLabel(curves, value.curve_id)} is not a{' '}
              {value.sample_type === 'marine' ? 'marine' : 'terrestrial'} curve
            </FormHelperText>
          ) : null}
        </FormControl>
      </Box>

      {/* Only for marine carbon: a marine curve already carries the global
          average reservoir effect, so what a date needs on top of it is the
          LOCAL deviation. Offered nowhere else, since a stray offset on a
          terrestrial date would shift it silently. */}
      {marine && (
        <Box sx={{ mb: 2 }}>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <TextField
              type="number"
              label="ΔR (yr)"
              value={value.delta_r ?? ''}
              onChange={(e) =>
                set({ delta_r: e.target.value === '' ? null : parseFloat(e.target.value) })
              }
              size="small"
              sx={{ flex: 1 }}
            />
            <TextField
              type="number"
              label="ΔR 1σ error"
              value={value.delta_r_error ?? ''}
              onChange={(e) =>
                set({ delta_r_error: e.target.value === '' ? null : parseFloat(e.target.value) })
              }
              size="small"
              sx={{ flex: 1 }}
              error={missingError}
              helperText={missingError ? 'Required with ΔR' : undefined}
            />
          </Box>
          <Typography variant="caption" color="textSecondary">
            Local marine reservoir offset from the global average the curve already models —
            not a whole reservoir age. Leave blank to apply no correction.
          </Typography>
        </Box>
      )}

      {/* The gap. It is not metadata: a wiggle match IS its gaps, and a member
          without one says no more than it would on its own. The sign is the
          part worth spelling out on the field itself -- rings count forward
          and cal BP counts backward, so an inverted gap gives a tight answer
          in the wrong place rather than an obviously wrong one. */}
      {showRingOffset && (
        <Box sx={{ mb: 2 }}>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <TextField
              type="number"
              label="Gap"
              value={value.ring_offset ?? ''}
              onChange={(e) =>
                set({ ring_offset: e.target.value === '' ? null : parseFloat(e.target.value) })
              }
              size="small"
              sx={{ flex: 1 }}
              inputProps={{ min: -50000, max: 50000, step: 1 }}
            />
            <TextField
              type="number"
              label="Gap 1σ error"
              value={value.ring_offset_error ?? ''}
              onChange={(e) =>
                set({
                  ring_offset_error: e.target.value === '' ? null : parseFloat(e.target.value),
                })
              }
              size="small"
              sx={{ flex: 1 }}
              inputProps={{ min: 0, max: 10000, step: 1 }}
              helperText="Only if measured, not counted"
            />
          </Box>
          <Typography variant="caption" color="textSecondary">
            Years before the series reference point: 0 at the reference and increasing into the
            past, so a larger gap is an older date. The Model Structure page can work these out
            from ring numbers.
          </Typography>
        </Box>
      )}

      {/* The depth. Provenance for an ordinary date, and the axis an age-depth
          model runs along for a date in one. */}
      {showDepth && (
        <Box sx={{ mb: 2 }}>
          <TextField
            type="number"
            label="Depth (cm)"
            value={value.depth_cm ?? ''}
            onChange={(e) =>
              set({ depth_cm: e.target.value === '' ? null : parseFloat(e.target.value) })
            }
            size="small"
            fullWidth
            inputProps={{ min: 0, max: 10000, step: 1 }}
          />
          <Typography variant="caption" color="textSecondary">
            Depth below the surface. Deeper is older, and it is the axis a deposition sequence
            models age against.
          </Typography>
        </Box>
      )}

      <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
        <TextField
          type="number"
          label="Latitude"
          value={value.latitude ?? ''}
          onChange={(e) =>
            set({ latitude: e.target.value === '' ? null : parseFloat(e.target.value) })
          }
          size="small"
          sx={{ flex: 1 }}
          inputProps={{ min: -90, max: 90, step: 'any' }}
          error={coordinateMissing && value.latitude == null}
        />
        <TextField
          type="number"
          label="Longitude"
          value={value.longitude ?? ''}
          onChange={(e) =>
            set({ longitude: e.target.value === '' ? null : parseFloat(e.target.value) })
          }
          size="small"
          sx={{ flex: 1 }}
          inputProps={{ min: -180, max: 180, step: 'any' }}
          error={coordinateMissing && value.longitude == null}
        />
      </Box>
      <Typography variant="caption" color="textSecondary" sx={{ display: 'block', mt: -1.5, mb: 2 }}>
        {coordinateMissing
          ? 'A position needs both halves. Fill the other, or clear this one.'
          : 'Decimal degrees, WGS84 — minus for south and west. Degrees and minutes are not read.'}
      </Typography>

      {showOutlier && (
        <Box sx={{ mb: 2 }}>
          <FormControlLabel
            control={
              <Switch
                size="small"
                checked={outlierOn}
                onChange={(e) =>
                  set({
                    // 5% is OxCal's convention for Outlier().
                    outlier_prior: e.target.checked ? 0.05 : 0,
                    outlier_model: value.outlier_model || 'general',
                  })
                }
              />
            }
            label="Allow this date to be an outlier"
          />
          {outlierOn && (
            <Box sx={{ display: 'flex', gap: 1, mt: 1 }}>
              <FormControl size="small" sx={{ flex: 2 }}>
                <InputLabel id="outlier-model-label">Outlier model</InputLabel>
                <Select
            labelId="outlier-model-label"
                  value={value.outlier_model || 'general'}
                  label="Outlier model"
                  onChange={(e) =>
                    set({ outlier_model: e.target.value as 'general' | 'charcoal' })
                  }
                >
                  <MenuItem value="general">General — wrong in any direction</MenuItem>
                  <MenuItem value="charcoal">Charcoal — older than its context</MenuItem>
                </Select>
              </FormControl>
              {/* Up to 100%, not 50: OxCal's Outlier(model, 1.00) declares
                  material residual outright rather than suspecting it, which is
                  ordinary for charcoal known to be old wood. Capping it made a
                  published model of that shape impossible to enter. */}
              <TextField
                type="number"
                label="Prior %"
                value={
                  value.outlier_prior == null ? '' : Math.round(value.outlier_prior * 1000) / 10
                }
                onChange={(e) => {
                  const percent = e.target.value === '' ? 0 : parseFloat(e.target.value)
                  set({ outlier_prior: Number.isFinite(percent) ? percent / 100 : 0 })
                }}
                size="small"
                sx={{ flex: 1 }}
                inputProps={{ min: 0, max: 100, step: 1 }}
              />
            </Box>
          )}
          <Typography variant="caption" color="textSecondary" component="div" sx={{ mt: 0.5 }}>
            {outlierOn
              ? (value.outlier_prior ?? 0) >= 1
                ? value.outlier_model === 'charcoal'
                  ? 'Declared residual: the sample IS older than its context, not merely suspected of it. The model fits the offset rather than weighing whether there is one.'
                  : 'Declared an outlier outright: the date is treated as wrong, and the model fits how wrong rather than whether.'
                : value.outlier_model === 'charcoal'
                  ? 'For old wood and reworked material: the sample can be older than the context it was found in, never younger.'
                  : 'For a date wrong in an unknown way — lab error, contamination, a mislabelled bag.'
              : 'Off. The date is taken at face value, and the model will move it to fit its neighbours however far that is.'}
          </Typography>
        </Box>
      )}

      {/* Inbuilt age travels with the outlier setting: both are about how the
          dated material relates to the event it is modelled as, and both
          apply only where there is a model. */}
      {showOutlier && (
        <Box sx={{ mb: 2 }}>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <TextField
              type="number"
              label="Inbuilt age (yr)"
              value={value.inbuilt_years ?? ''}
              onChange={(e) => {
                const years = e.target.value === '' ? null : parseFloat(e.target.value)
                set({ inbuilt_years: years != null && Number.isFinite(years) && years > 0 ? years : null })
              }}
              size="small"
              sx={{ flex: 1 }}
              inputProps={{ min: 0, max: 1000, step: 1 }}
            />
            <FormControl size="small" sx={{ flex: 1 }} disabled={!value.inbuilt_years}>
              <InputLabel id="inbuilt-shape-label">Allowance</InputLabel>
              <Select
                labelId="inbuilt-shape-label"
                value={value.inbuilt_shape || 'uniform'}
                label="Allowance"
                onChange={(e) => set({ inbuilt_shape: e.target.value as InbuiltShape })}
              >
                {INBUILT_SHAPES.map((option) => (
                  <MenuItem key={option.value} value={option.value}>
                    {option.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Box>
          <Typography variant="caption" color="textSecondary" component="div" sx={{ mt: 0.5 }}>
            {describeInbuilt(value.inbuilt_years, value.inbuilt_shape)}
          </Typography>
        </Box>
      )}
    </>
  )
}
