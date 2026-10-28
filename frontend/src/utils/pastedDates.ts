/**
 * The bulk-paste textarea's parser.
 *
 * Its own module rather than a function inside ProjectSampleTable, and for the
 * reason plotExport.ts and siteMapGeometry.ts are their own modules: the table
 * now reaches Plotly transitively (through the map dialog), and
 * plotly.js-dist-min's UMD wrapper reads `self` at module scope, so a Node
 * test importing the component dies with "self is not defined" before
 * collecting an assertion. This is pure and worth asserting, so it lives where
 * a test can reach it.
 */

/**
 * One pasted line, in the shape the collection endpoint takes.
 *
 * Every field a date can hold is written, nulls included, rather than only the
 * ones the paste format carries: the POST body is the whole date, and a row
 * that omitted them would be a different shape from one the form builds.
 */
export interface PastedDate {
  sample_name: string
  age_bp: number
  error_1sigma: number
  material_type: string
  curve_id: string
  default_context_name: string | null
  tpq_age_bp: number | null
  taq_age_bp: number | null
  delta_r: number | null
  delta_r_error: number | null
  depth_cm: number | null
  ring_offset: number | null
  ring_offset_error: number | null
  latitude: number | null
  longitude: number | null
  location: string | null
  notes: string | null
}

/**
 * Parse the bulk-paste textarea: one date per line, comma or tab separated, as
 * `name, age, error[, material[, context[, curve]]]`. Forgiving, since this is
 * for pasting a column range out of a spreadsheet or lab report -- blank lines
 * are skipped and a malformed one is reported by number rather than dropped.
 * Curve is optional and defaults to IntCal20, most batches being a single
 * terrestrial curve.
 *
 * `validCurveIds` is passed in so this stays pure while the curves come from
 * the backend registry. An empty set accepts any curve id and leaves rejection
 * to the backend, which is right when the curve list could not be loaded:
 * refusing every line would blame the paste for a failed request.
 */
export function parsePastedDates(
  text: string,
  validCurveIds: Set<string>
): { rows: PastedDate[]; errors: string[] } {
  const rows: PastedDate[] = []
  const errors: string[] = []

  text.split(/\r?\n/).forEach((rawLine, index) => {
    const line = rawLine.trim()
    if (!line) return
    const parts = line.split(/[\t,;]/).map((p) => p.trim())
    const [name, age, error, material, context, curve] = parts
    if (!name || !age || !error) {
      errors.push(`Line ${index + 1}: expected at least "name, age, error"`)
      return
    }
    if (!Number.isFinite(Number(age)) || !Number.isFinite(Number(error))) {
      errors.push(`Line ${index + 1}: age and error must be numbers`)
      return
    }
    const curveId = curve ? curve.toLowerCase() : 'intcal20'
    if (validCurveIds.size > 0 && !validCurveIds.has(curveId)) {
      errors.push(
        `Line ${index + 1}: unknown curve "${curve}" (expected ` +
          `${[...validCurveIds].join(', ')})`
      )
      return
    }
    rows.push({
      sample_name: name,
      age_bp: Number(age),
      error_1sigma: Number(error),
      material_type: material || 'unknown',
      curve_id: curveId,
      default_context_name: context || null,
      tpq_age_bp: null,
      taq_age_bp: null,
      // The paste format carries name, age, error, material, context and
      // curve; everything else a date can hold is set afterwards, in the row's
      // own dialog.
      delta_r: null,
      delta_r_error: null,
      depth_cm: null,
      ring_offset: null,
      ring_offset_error: null,
      latitude: null,
      longitude: null,
      location: null,
      notes: null,
    })
  })

  return { rows, errors }
}
