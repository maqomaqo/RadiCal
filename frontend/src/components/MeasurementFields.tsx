import {
  Autocomplete,
  Box,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Select,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import type { DraftMeasurement } from '@/types'

/**
 * The fields that describe one radiocarbon date.
 *
 * Shared between adding a date and editing one already in the list. They
 * were going to be two copies of the same ninety lines of JSX, which is the
 * arrangement that had just cost us two persistence bugs on the backend --
 * a field added to one copy of a form and not the other is invisible until
 * someone notices a column is empty.
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
 * Which curves suit each sample type. The first is what selecting that
 * sample type switches to; the rest are other defensible choices a
 * deliberate selection should not be overridden by. Marine carbon has to
 * be calibrated against a marine curve -- IntCal20 would be wrong by
 * centuries -- while a terrestrial sample picks between hemispheres.
 */
export const CURVE_FOR_SAMPLE_TYPE: Record<string, string[]> = {
  terrestrial: ['intcal20', 'shcal20'],
  marine: ['marine20'],
}

export type MeasurementDraft = Partial<DraftMeasurement>

interface Props {
  value: MeasurementDraft
  onChange: (next: MeasurementDraft) => void
  /**
   * Outlier settings are only offered for a Bayesian analysis. A single
   * calibration has no model for a date to be an outlier FROM, so the
   * control would be inert.
   */
  showOutlier?: boolean
}

export function isMarineMeasurement(m: MeasurementDraft): boolean {
  return (m.sample_type || 'terrestrial') === 'marine' || m.curve_id === 'marine20'
}

export function deltaRErrorMissing(m: MeasurementDraft): boolean {
  return m.delta_r != null && m.delta_r_error == null
}

export default function MeasurementFields({ value, onChange, showOutlier = false }: Props) {
  const set = (patch: MeasurementDraft) => onChange({ ...value, ...patch })
  const marine = isMarineMeasurement(value)
  const missingError = deltaRErrorMissing(value)
  const outlierOn = (value.outlier_prior ?? 0) > 0

  return (
    <>
      <Box sx={{ mb: 2 }}>
        <TextField
          fullWidth
          label="Sample Name"
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
          label="Age (BP)"
          value={value.age_bp ?? ''}
          onChange={(e) =>
            set({ age_bp: e.target.value === '' ? undefined : parseFloat(e.target.value) })
          }
          size="small"
          sx={{ flex: 1 }}
        />
        <TextField
          type="number"
          label="1σ Error"
          value={value.error_1sigma ?? ''}
          onChange={(e) =>
            set({ error_1sigma: e.target.value === '' ? undefined : parseFloat(e.target.value) })
          }
          size="small"
          sx={{ flex: 1 }}
        />
      </Box>

      <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
        <FormControl size="small" sx={{ flex: 1 }}>
          <InputLabel>Sample Type</InputLabel>
          <Select
            value={value.sample_type || 'terrestrial'}
            label="Sample Type"
            onChange={(e) => {
              const sampleType = e.target.value as 'terrestrial' | 'marine'
              set({
                sample_type: sampleType,
                // Follow the sample type with a curve that suits it, unless
                // a deliberate choice has already been made that does.
                curve_id: CURVE_FOR_SAMPLE_TYPE[sampleType].includes(value.curve_id || '')
                  ? value.curve_id
                  : CURVE_FOR_SAMPLE_TYPE[sampleType][0],
              })
            }}
          >
            <MenuItem value="terrestrial">Terrestrial</MenuItem>
            <MenuItem value="marine">Marine</MenuItem>
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ flex: 1 }}>
          <InputLabel>Curve</InputLabel>
          <Select
            value={value.curve_id || 'intcal20'}
            label="Curve"
            onChange={(e) => set({ curve_id: e.target.value })}
          >
            <MenuItem value="intcal20">IntCal20</MenuItem>
            <MenuItem value="marine20">Marine20</MenuItem>
            <MenuItem value="shcal20">SHCal20</MenuItem>
          </Select>
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
            Local marine reservoir offset from the global average the curve already models --
            not a whole reservoir age. Leave blank to apply no correction.
          </Typography>
        </Box>
      )}

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
                <InputLabel>Outlier model</InputLabel>
                <Select
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
                inputProps={{ min: 0, max: 50, step: 1 }}
              />
            </Box>
          )}
          <Typography variant="caption" color="textSecondary" component="div" sx={{ mt: 0.5 }}>
            {outlierOn
              ? value.outlier_model === 'charcoal'
                ? 'For old wood and reworked material: the sample can be older than the context it was found in, never younger.'
                : 'For a date wrong in an unknown way — lab error, contamination, a mislabelled bag.'
              : 'Off. The date is taken at face value, and the model will move it to fit its neighbours however far that is.'}
          </Typography>
        </Box>
      )}
    </>
  )
}
