"""
Reading dates in from an uploaded file.

Two endpoints, and neither of them writes anything:

  POST /api/import/dates      parse an uploaded .csv or .oxcal and return what
                              it contained, for the user to look at before any
                              of it is kept
  GET  /api/import/csv-template  the column layout, as a file to fill in

Parsing and storing are deliberately separate steps. Once the user has seen
the preview, the dates go into a project's collection through
POST /projects/{id}/samples, or into an analysis through the ordinary
Bayesian/group-calibration endpoints -- exactly the paths hand-entered dates
take. A file upload therefore adds no new way for data to reach the database,
and cannot drift out of step with the validation on the paths that do.

A file that fails to parse still comes back 200. The request succeeded; its
answer is a list of what is wrong with the file, which the UI shows in full so
one round trip fixes it. Reserving the error status for actual request
failures keeps 'the upload broke' distinguishable from 'row 12 has a letter in
the age column'. `data.ok` and `data.errors` carry the verdict.
"""
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.services.date_import import (
    MAX_CONTENT_BYTES,
    TEMPLATE_COLUMNS,
    parse_upload,
    template_csv,
)

router = APIRouter()


@router.post("/import/dates")
async def import_dates(file: UploadFile = File(...)):
    """
    Parse an uploaded file of dates and return what it held, without saving
    any of it.

    Accepts a delimited table (.csv/.tsv/.txt) or an OxCal model (.oxcal).
    The format is chosen by extension, falling back on the content itself --
    see services/date_import.parse_upload.

    The response carries three separate channels the caller should show
    differently: `errors` (nothing was imported), `warnings` (imported, but
    something could not be honoured) and `notes` (imported, and here is what
    was assumed). See services/date_import.ImportResult.
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(content) > MAX_CONTENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"The file is {len(content) // 1024} KB; the limit is "
                f"{MAX_CONTENT_BYTES // 1024} KB."
            ),
        )

    result = parse_upload(file.filename or "", content)
    payload = result.as_dict()
    payload["filename"] = file.filename
    return {"success": True, "data": payload}


@router.get("/import/csv-template")
async def csv_template():
    """
    The CSV template, as a download.

    Includes worked example rows rather than a bare header: the column names
    say what goes in them, but not that delta_r requires its uncertainty, or
    that a date declared residual is outlier_model 'charcoal' at prior 1.0.
    """
    return StreamingResponse(
        iter([template_csv()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="radical-dates-template.csv"'},
    )


@router.get("/import/csv-columns")
async def csv_columns():
    """
    The columns the CSV importer reads, for the format help shown in the
    upload dialog.

    Served rather than duplicated in the frontend so the help and the parser
    cannot disagree -- a documented column the importer does not actually
    read is worse than no documentation.
    """
    from app.services.date_import import COLUMN_ALIASES, REQUIRED_COLUMNS

    described = {
        'sample_name': 'Lab code or sample name.',
        'age_bp': 'Uncalibrated radiocarbon age, in years BP.',
        'error_1sigma': 'Measurement uncertainty, 1 sigma, in years.',
        'material_type': 'What was dated (charcoal, bone, shell...).',
        'curve_id': 'intcal20, shcal20 or marine20. Defaults to intcal20.',
        'sample_type': 'terrestrial or marine. Inferred from the curve if omitted.',
        'delta_r': 'Local marine reservoir offset. Marine dates only.',
        'delta_r_error': 'Uncertainty on delta_r. Required whenever delta_r is given.',
        'phase': 'Context/phase name. Dates sharing one are grouped together.',
        'tpq_age_bp': 'Terminus post quem: the date cannot be older than this (cal BP).',
        'taq_age_bp': 'Terminus ante quem: the date cannot be younger than this (cal BP).',
        'outlier_model': "general or charcoal.",
        'outlier_prior': 'Prior probability the date is an outlier (0.05, or 5%).',
        'depth_cm': 'Depth below surface, in centimetres.',
        'location': 'Site or find location.',
        'notes': 'Free text.',
    }

    return {
        "success": True,
        "data": {
            "required": list(REQUIRED_COLUMNS),
            "template_order": list(TEMPLATE_COLUMNS),
            "columns": [
                {
                    "name": name,
                    "required": name in REQUIRED_COLUMNS,
                    "description": described[name],
                    "also_accepted": [a for a in COLUMN_ALIASES[name] if a != name],
                }
                for name in described
            ],
        },
    }
