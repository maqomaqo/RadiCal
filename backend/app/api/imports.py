"""
Reading dates in from an uploaded file. Nothing here writes anything.

Parsing and storing are separate steps: after the preview, the dates go
through POST /projects/{id}/samples or the ordinary analysis endpoints --
exactly the paths hand-entered dates take -- so an upload adds no new way for
data to reach the database.

A file that fails to parse still comes back 200: the request succeeded, and
its answer is what is wrong with the file, in `data.ok` and `data.errors`.
That keeps "the upload broke" distinguishable from "row 12 has a letter in the
age column".
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.api.auth import current_user
from app.models import User
from app.services.date_import import (
    MAX_CONTENT_BYTES,
    TEMPLATE_COLUMNS,
    parse_upload,
    template_csv,
)

router = APIRouter()


@router.post("/import/dates")
async def import_dates(file: UploadFile = File(...),
                       user: User = Depends(current_user)):
    """
    Parse an uploaded file of dates and return what it held, without saving
    any of it.

    Accepts a delimited table (.csv/.tsv/.txt) or an OxCal model (.oxcal).
    The format is chosen by extension, falling back on the content itself --
    see services/date_import.parse_upload.

    The response carries three channels to show differently: `errors`
    (nothing was imported), `warnings` (imported, but something could not be
    honoured) and `notes` (imported, and here is what was assumed). See
    services/date_import.ImportResult.

    `async def` for the upload read only; parsing is blocking work and goes to
    the threadpool.
    """
    content = await file.read()
    return await run_in_threadpool(_import_dates, file.filename or "", content, user.id)


def _import_dates(filename: str, content: bytes, owner_id: str):
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

    # Curve names in the file resolve against this user's own curves only:
    # see services/oxcal_script.parse_oxcal.
    result = parse_upload(filename, content, owner_id)
    payload = result.as_dict()
    payload["filename"] = filename
    return {"success": True, "data": payload}


@router.get("/import/csv-template")
def csv_template(user: User = Depends(current_user)):
    """
    The CSV template, as a download.

    Includes worked example rows rather than a bare header, since the column
    names cannot say that delta_r requires its uncertainty, or that a residual
    date is outlier_model 'charcoal' at prior 1.0.
    """
    return StreamingResponse(
        iter([template_csv()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="radical-dates-template.csv"'},
    )


@router.get("/import/csv-columns")
def csv_columns(user: User = Depends(current_user)):
    """
    The columns the CSV importer reads, for the upload dialog's format help.
    Served rather than duplicated in the frontend so the help and the parser
    cannot disagree.
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
