//! Reading dates in from an uploaded file (`api/imports.py`). Nothing here
//! writes: after the preview the dates go through the ordinary routes, the
//! ones hand-entered dates take.
//!
//! A file that fails to parse still answers 200: its answer is what is wrong
//! with it, in `data.ok` and `data.errors`.

use axum::Router;
use axum::body::Bytes;
use axum::extract::State as Extract;
use axum::http::{HeaderMap, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use radical_io::date_import::{
    COLUMN_ALIASES, MAX_CONTENT_BYTES, REQUIRED_COLUMNS, TEMPLATE_COLUMNS, parse_upload,
    template_csv,
};
use radical_io::ids::RandomIds;
use serde_json::json;

use crate::accounts::Caller;
use crate::form::{self, Form};
use crate::routes::results::library;
use crate::{ApiError, ApiResult, State, ok};

/// Parse an uploaded file of dates and say what it held, saving none of it.
async fn import_dates(
    Extract(state): Extract<State>,
    caller: Caller,
    headers: HeaderMap,
    body: Bytes,
) -> ApiResult {
    let form = Form::parse(
        headers
            .get(header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok()),
        &body,
    );
    let Some(file) = form.file("file") else {
        return Err(form::missing(&["file"]));
    };
    if file.data.is_empty() {
        return Err(ApiError::bad_request("The uploaded file is empty."));
    }
    if file.data.len() > MAX_CONTENT_BYTES {
        return Err(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            format!(
                "The file is {} KB; the limit is {} KB.",
                file.data.len() / 1024,
                MAX_CONTENT_BYTES / 1024
            ),
        ));
    }
    let filename = file.filename.clone().unwrap_or_default();
    let library = library(&state, &crate::owner::Scope::of(&caller))?;
    let result = parse_upload(&filename, &file.data, &library, &mut RandomIds::default());
    let mut payload = result.to_json();
    payload["filename"] = json!(filename);
    Ok(ok(payload))
}

/// The CSV template, with worked example rows.
async fn csv_template() -> Response {
    (
        [
            (header::CONTENT_TYPE, "text/csv; charset=utf-8"),
            (
                header::CONTENT_DISPOSITION,
                "attachment; filename=\"radical-dates-template.csv\"",
            ),
        ],
        template_csv(),
    )
        .into_response()
}

/// What each column the CSV importer reads means, for the upload dialog.
const DESCRIBED: [(&str, &str); 23] = [
    ("sample_name", "Lab code or sample name."),
    ("age_bp", "Uncalibrated radiocarbon age, in years BP."),
    (
        "error_1sigma",
        "Measurement uncertainty, 1 sigma, in years.",
    ),
    (
        "material_type",
        "What was dated (charcoal, bone, shell...).",
    ),
    (
        "curve_id",
        "intcal20, shcal20 or marine20. Defaults to intcal20.",
    ),
    (
        "sample_type",
        "terrestrial or marine. Inferred from the curve if omitted.",
    ),
    (
        "delta_r",
        "Local marine reservoir offset. Marine dates only.",
    ),
    (
        "delta_r_error",
        "Uncertainty on delta_r. Required whenever delta_r is given.",
    ),
    (
        "phase",
        "Context/phase name. Dates sharing one are grouped together.",
    ),
    (
        "tpq_age_bp",
        "Terminus post quem: the date cannot be older than this (cal BP).",
    ),
    (
        "taq_age_bp",
        "Terminus ante quem: the date cannot be younger than this (cal BP).",
    ),
    ("outlier_model", "general or charcoal."),
    (
        "outlier_prior",
        "Prior probability the date is an outlier (0.05, or 5%).",
    ),
    (
        "depth_cm",
        "Depth below surface, in centimetres. The axis an age-depth model runs along.",
    ),
    (
        "ring_offset",
        "Years before the series reference point, for a wiggle match: 0 at the reference and increasing into the past.",
    ),
    (
        "ring_offset_error",
        "Uncertainty on ring_offset, where the gap was measured rather than counted.",
    ),
    (
        "latitude",
        "Decimal degrees, WGS84, minus for south. Degrees and minutes (54°30'N) are refused, not converted.",
    ),
    (
        "longitude",
        "Decimal degrees, WGS84, minus for west. Both halves of a coordinate are needed, or neither.",
    ),
    (
        "inbuilt_years",
        "Most years older than its event the material may be (the mean, for an exponential allowance).",
    ),
    (
        "inbuilt_shape",
        "uniform or exponential. How the inbuilt age is spread.",
    ),
    (
        "delta_r_source",
        "How a looked-up ΔR was derived, as the app exports it. Leave blank for a ΔR you entered.",
    ),
    ("location", "Site or find location."),
    ("notes", "Free text."),
];

async fn csv_columns() -> ApiResult {
    let columns: Vec<_> = DESCRIBED
        .iter()
        .map(|(name, description)| {
            let aliases = COLUMN_ALIASES
                .iter()
                .find(|(n, _)| n == name)
                .map(|(_, a)| a.iter().filter(|a| *a != name).collect::<Vec<_>>())
                .unwrap_or_default();
            json!({
                "name": name,
                "required": REQUIRED_COLUMNS.contains(name),
                "description": description,
                "also_accepted": aliases,
            })
        })
        .collect();
    Ok(ok(json!({
        "required": REQUIRED_COLUMNS,
        "template_order": TEMPLATE_COLUMNS,
        "columns": columns,
    })))
}

pub fn router() -> Router<State> {
    Router::new()
        .route("/api/import/dates", post(import_dates))
        .route("/api/import/csv-template", get(csv_template))
        .route("/api/import/csv-columns", get(csv_columns))
}
