"""
What calibration curves this app has, and what is true about each of them.

Everything here is served from services/curve_registry.py, which measures a
curve's countable properties from the curve that actually loaded. This module
used to carry its own hand-written table instead, and every row of it had
drifted: it reported 5,141 points for IntCal20 and SHCal20 and 1,341 for
Marine20, where the loaded files hold 9,501, 9,501 and 5,501. Nothing called
these endpoints, so nothing caught it -- the frontend had its own two
hardcoded lists, which is what the registry and these endpoints now replace.

Builtin curves are read-only and will stay so. Imported ones can be added,
described, archived and removed here, under two rules that the endpoints
enforce rather than merely document:

  A stored curve's numbers never change. PATCH writes editorial fields only;
  correcting a curve means importing a new one. An analysis run last month has
  to keep meaning what it said.

  A curve in use cannot be deleted. The refusal names what is using it, and
  offers archiving -- which hides the curve from every picker while leaving it
  resolvable, so rerunning an older analysis still works.

Importing follows the same parse-then-store shape as api/imports.py: a preview
endpoint that writes nothing and answers 200 even for an unreadable file, with
`errors`/`warnings`/`notes` as three separate channels, and a store endpoint
that takes the same file again. The file rather than the parsed rows, so
there is exactly one parser and no second path by which curve data can reach
the database.

A stored curve is PRIVATE to the account that imported it. Only the three
builtins are shared.

That is a reversal of how this started, and the reasons are the ones that only
show up once more than one person is using an install: a curve someone is
still developing must not sit in everybody's picker looking like a supported
option, and a curve imported years ago for one site must not quietly become
an app-wide default that a newcomer picks by mistake. Reference data that
everyone can see is only reference data if somebody vouched for it, and
nothing here does.

So every endpoint below is scoped, and the two rules from api/auth.py apply
unchanged: another account's curve is a 404, never a 403, and the listing
endpoints FILTER rather than check. `services/curve_registry.py` carries the
matching split between the functions that choose (scoped) and the functions
that load (not scoped, because a solver re-running a stored analysis has no
user in scope). `authorise_curve_ids` below is the boundary between them.
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import StoredCalibrationCurve, User
from app.api.auth import current_user
from app.services.calibration import SIGMA_1, SIGMA_2, CalibrationCurve
from app.schemas.curve import (
    CurveMetadata, CurveMetadataUpdate, MixedCurveRequest, SimulateRequest,
    SLUG_PATTERN, slugify,
)
from app.services import curve_import, curve_mix, curve_registry, curve_view, resolution

router = APIRouter()


def visible_or_404(curve_id: str, owner_id: str) -> None:
    """
    Refuse unless `owner_id` may see this curve.

    404 rather than 403, for the reason api/auth.owned_or_404 gives: a 403
    confirms the curve exists, which would turn every id-taking endpoint here
    into a way to enumerate what other people have imported. "No such curve"
    is the same answer for a typo and for somebody else's curve, deliberately.

    Called at the TOP of each route that takes a curve id, before anything
    resolves. The registry's loaders do not check -- see its docstring -- so
    this is the only thing between a guessed id and another user's curve data.
    """
    if not curve_registry.visible_to(curve_id, owner_id):
        raise HTTPException(status_code=404, detail=f"No calibration curve '{curve_id}'.")


def authorise_curve_ids(curve_ids, owner_id: str) -> None:
    """
    Refuse the request unless `owner_id` may name every one of these curves.

    The boundary the module docstring names. `schemas.sample.validate_curve_id`
    has already rejected ids that name no curve at all; what is left for this
    to catch is an id that names a real curve belonging to somebody else --
    which arrives looking exactly like a valid request, because it is one for
    its owner.

    A 422 naming the curves the caller CAN use, rather than a 403: they asked
    for something that is, from where they stand, not there. The wording does
    not say the curve exists for someone else, and the list is theirs alone.

    Imported by api/analysis.py and api/project_samples.py as well as used
    here, so that every path by which a curve id reaches a stored row goes
    through one check.
    """
    wanted = {c for c in curve_ids if c}
    if not wanted:
        return
    unusable = sorted(c for c in wanted
                      if not curve_registry.visible_to(c, owner_id))
    if not unusable:
        return

    available = ', '.join(curve_registry.curve_ids(owner_id))
    named = ', '.join(f"'{c}'" for c in unusable)
    raise HTTPException(
        status_code=422,
        detail=(
            f"No calibration curve {named} is available to you. You can "
            f"calibrate against: {available}. Import the curve to add it to "
            "that list -- a curve this app does not have for you cannot be "
            "substituted for one it does."
        ),
    )

# Only three curves exist today, but the response has always been paginated
# and callers may rely on that shape. The default is well clear of the curve
# count rather than the previous 10, so that adding curves cannot silently
# truncate the list a picker is built from.
DEFAULT_LIMIT = 100


@router.get("/curves")
def list_curves(limit: int = DEFAULT_LIMIT, include_archived: bool = False,
                user: User = Depends(current_user)):
    """
    Every curve available for calibration, in the order they should be
    offered. This is the list the frontend's curve pickers are built from,
    which is why archived curves are left out by default: they still resolve,
    so old analyses rerun, but they are not offered for new work.
    """
    specs = curve_registry.list_specs(user.id, include_archived=include_archived)
    items = [spec.as_dict() for spec in specs[:limit]]
    return {
        "success": True,
        "data": {
            "items": items,
            "total": len(specs),
            "page": 1,
            "per_page": limit,
            "total_pages": 1 if specs else 0,
        }
    }


@router.get("/curves/compare")
def compare_curves(
    a: str = Query(..., description="First curve id"),
    b: str = Query(..., description="Second curve id"),
    cal_from: Optional[float] = Query(None),
    cal_to: Optional[float] = Query(None),
    max_points: int = Query(curve_view.DEFAULT_MAX_POINTS, ge=2, le=curve_view.MAX_MAX_POINTS),
    user: User = Depends(current_user),
):
    """
    Two curves on one grid, with their difference.

    The difference is the point. Drawn over each other, two calibration curves
    look alike at any zoom that fits a millennium on screen; the hemispheric
    offset between IntCal20 and SHCal20 is a few decades against an axis
    spanning thousands of years. As A - B it reads immediately.

    Declared before /curves/{curve_id} on purpose: routes match in declaration
    order, and "compare" is a perfectly good curve id as far as the path
    pattern is concerned, so the other way round this endpoint is a 404 for a
    curve that does not exist.
    """
    visible_or_404(a, user.id)
    visible_or_404(b, user.id)
    try:
        curve_a = curve_registry.resolve(a)
        curve_b = curve_registry.resolve(b)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        data = curve_view.compare(curve_a, curve_b, cal_from, cal_to, max_points)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    data['a'] = a
    data['b'] = b
    return {"success": True, "data": data}


@router.get("/curves/{curve_id}")
def get_curve(curve_id: str, user: User = Depends(current_user)):
    """One curve's full description, including its measured metadata."""
    visible_or_404(curve_id, user.id)
    try:
        spec = curve_registry.get_spec(curve_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"success": True, "data": spec.as_dict()}


@router.get("/curves/type/{curve_type}")
def get_curves_by_type(curve_type: str, user: User = Depends(current_user)):
    """
    Curves of one display type ('terrestrial', 'marine',
    'southern_hemisphere'). An unknown type is an empty list, not a 404: it
    is a filter that matched nothing, not a missing resource.
    """
    curves = [
        spec.as_dict() for spec in curve_registry.list_specs(user.id)
        if spec.type == curve_type
    ]
    return {"success": True, "data": curves}


@router.get("/curves/{curve_id}/points")
def curve_points(
    curve_id: str,
    cal_from: Optional[float] = Query(None, description="Window start, cal BP"),
    cal_to: Optional[float] = Query(None, description="Window end, cal BP"),
    max_points: int = Query(curve_view.DEFAULT_MAX_POINTS, ge=2, le=curve_view.MAX_MAX_POINTS),
    user: User = Depends(current_user),
):
    """
    A drawable slice of a curve: [cal BP, 14C age, 1-sigma, slope] rows.

    Windowed and decimated server-side, because the browser asks for this again
    on every zoom and a 9,501-point payload per zoom step would be wasteful --
    and because the decimation has to be min/max per bucket (see
    services/curve_view) rather than every Nth point, which is a decision that
    belongs with the curve rather than with whichever client is drawing it.
    """
    visible_or_404(curve_id, user.id)
    try:
        curve = curve_registry.resolve(curve_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    data = curve_view.window(curve, cal_from, cal_to, max_points)
    data['curve_id'] = curve_id
    return {"success": True, "data": data}


@router.post("/curves/{curve_id}/simulate")
def simulate_date(curve_id: str, request: SimulateRequest,
                  user: User = Depends(current_user)):
    """
    What a date from a given calendar year would look like.

    The curve's own radiocarbon age at that year, calibrated back through the
    curve with the stated laboratory error -- OxCal's C_Simulate, without its
    random scatter. Deliberately without: a drawn measurement answers "what
    might one particular sample give", which is a different question every
    time it is asked, where this answers "how well does this year date", which
    is the question a curve browser is for. It is also the single-date
    counterpart of the aggregate the resolution panel reports.
    """
    visible_or_404(curve_id, user.id)
    try:
        curve = curve_registry.resolve(curve_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if not (curve.cal_ages.min() <= request.cal_age <= curve.cal_ages.max()):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{request.cal_age:g} cal BP is outside this curve, which "
                f"covers {curve.cal_ages.min():g}-{curve.cal_ages.max():g} cal BP."
            ),
        )

    c14_age, curve_sigma = curve.interpolate(float(request.cal_age))
    cal_ages, probabilities = curve.calibrate(
        float(c14_age), request.error, request.delta_r, request.delta_r_error
    )

    return {
        "success": True,
        "data": {
            "curve_id": curve_id,
            "cal_age": request.cal_age,
            "c14_age": float(c14_age),
            "curve_error": float(curve_sigma),
            "error": request.error,
            "hpd_68_ranges": curve.get_hpd_ranges(cal_ages, probabilities, SIGMA_1),
            "hpd_95_ranges": curve.get_hpd_ranges(cal_ages, probabilities, SIGMA_2),
            "point_estimates": curve.get_point_estimate(cal_ages, probabilities),
            "probability_distribution": curve.get_display_distribution(cal_ages, probabilities),
        },
    }


@router.get("/curves/{curve_id}/points.csv")
def curve_points_csv(
    curve_id: str,
    cal_from: Optional[float] = Query(None),
    cal_to: Optional[float] = Query(None),
    max_points: int = Query(curve_view.MAX_MAX_POINTS, ge=2, le=curve_view.MAX_MAX_POINTS),
    user: User = Depends(current_user),
):
    """The visible window as CSV, so a stretch of curve can leave this app as
    data rather than only as a picture."""
    visible_or_404(curve_id, user.id)
    try:
        curve = curve_registry.resolve(curve_id)
        spec = curve_registry.get_spec(curve_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    window = curve_view.window(curve, cal_from, cal_to, max_points)
    name = f"{curve_id}-{window['cal_from']:.0f}-{window['cal_to']:.0f}.csv"
    return StreamingResponse(
        iter([curve_view.to_csv(window, spec.name)]),
        media_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{name}"'},
    )


# Beyond this span, the calculation takes long enough (about eight seconds
# for the whole 55,000 years) that it is served from the cached whole-curve
# profile instead. Slicing that is exact, not an approximation -- a windowed
# calculation pads far enough to return identical numbers.
WIDE_SPAN_YEARS = 20000


@router.get("/curves/{curve_id}/resolution")
def curve_resolution(
    curve_id: str,
    cal_from: Optional[float] = Query(None, description="Window start, cal BP"),
    cal_to: Optional[float] = Query(None, description="Window end, cal BP"),
    u0: float = Query(resolution.DEFAULT_U0, gt=0, le=500,
                      description="Laboratory 1-sigma at 0 BP, scaled with age from there"),
    stride: int = Query(5, ge=1, le=200, description="Report every N calendar years"),
    user: User = Depends(current_user),
):
    """
    How tightly a date from each calendar age could be known, before anyone
    measures anything -- Svetlik et al. (2019); see services/resolution.py.

    Each point is [cal BP, mean resolution, 5th percentile, 95th percentile,
    laboratory sigma assumed], all in years. The percentiles are the spread
    over what a single date might return: how lucky or unlucky one measurement
    of a sample from that year can be.
    """
    visible_or_404(curve_id, user.id)
    try:
        curve = curve_registry.resolve(curve_id)
        spec = curve_registry.get_spec(curve_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    lo = spec.cal_min if cal_from is None else max(cal_from, spec.cal_min)
    hi = spec.cal_max if cal_to is None else min(cal_to, spec.cal_max)
    if hi < lo:
        lo, hi = hi, lo

    # Once a whole curve has been computed for this laboratory uncertainty --
    # which opening the page on the full range does -- every later window is a
    # slice of it, and panning and zooming cost nothing. Windows asking for a
    # finer lattice than the cached one still compute directly, since that is
    # the detail they zoomed in for and a narrow window is a few hundredths of
    # a second.
    warm = (
        resolution.has_full_profile(spec.content_sha256, u0)
        and stride >= resolution.BASE_STRIDE
    )
    if warm or hi - lo > WIDE_SPAN_YEARS:
        whole = resolution.full_profile(curve, spec.content_sha256, u0=u0, stride=stride)
        computed = resolution.slice_profile(whole, lo, hi)
    else:
        computed = resolution.profile(curve, lo, hi, u0=u0, stride=stride)

    data = computed.as_dict()
    data['curve_id'] = curve_id
    data['cal_from'] = lo
    data['cal_to'] = hi
    return {"success": True, "data": data}


@router.get("/curves/{curve_id}/resolution.csv")
def curve_resolution_csv(
    curve_id: str,
    u0: float = Query(resolution.DEFAULT_U0, gt=0, le=500),
    stride: int = Query(5, ge=1, le=200),
    user: User = Depends(current_user),
):
    """
    The whole curve's resolution profile as a CSV, in the column order res14c
    publishes so the two can be compared directly.
    """
    visible_or_404(curve_id, user.id)
    try:
        curve = curve_registry.resolve(curve_id)
        spec = curve_registry.get_spec(curve_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    computed = resolution.full_profile(curve, spec.content_sha256, u0=u0, stride=stride)
    return StreamingResponse(
        iter([resolution.to_csv(computed)]),
        media_type='text/csv',
        headers={
            'Content-Disposition':
                f'attachment; filename="{curve_id}-resolution-u{u0:g}.csv"'
        },
    )


# --------------------------------------------------------------------------
# Importing
# --------------------------------------------------------------------------


def _check_size(content: bytes) -> None:
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(content) > curve_import.MAX_CONTENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"The file is {len(content) // 1024} KB; the limit is "
                f"{curve_import.MAX_CONTENT_BYTES // 1024} KB."
            ),
        )


@router.post("/curves/import/preview")
async def preview_curve(
    file: UploadFile = File(...),
    calendar: str = Form('cal_bp'),
    user: User = Depends(current_user),
):
    """
    Read an uploaded curve file and report what it holds, storing nothing.

    An unreadable file still answers 200: the request succeeded, and its
    answer is the list of what is wrong. `data.ok` carries the verdict, and
    `data.preview` a downsampled series so the dialog can draw the curve
    before it is accepted.

    One of the three handlers that must stay `async def`, since reading an
    upload is genuinely awaitable. Everything after that read is parsing --
    a whole .14c file's worth -- so it goes to the threadpool rather than
    running on the event loop. See _preview_curve.
    """
    content = await file.read()
    return await run_in_threadpool(
        _preview_curve, file.filename or '', content, calendar)


def _preview_curve(filename: str, content: bytes, calendar: str) -> Dict:
    if calendar not in ('cal_bp', 'bc_ad'):
        raise HTTPException(
            status_code=422,
            detail="calendar must be 'cal_bp' or 'bc_ad'.",
        )
    _check_size(content)

    result = curve_import.parse_upload(filename, content, calendar=calendar)
    payload = result.as_dict()
    payload['filename'] = filename
    payload['suggested_id'] = slugify(result.suggested_name or filename or 'curve')
    return {"success": True, "data": payload}


@router.post("/curves", status_code=201)
async def create_curve(
    file: UploadFile = File(...),
    metadata: str = Form(..., description="CurveMetadata as JSON"),
    calendar: str = Form('cal_bp'),
    curve_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    Store a previewed curve.

    Takes the file again rather than the rows the preview returned. Sending
    the parsed numbers back would make the client a second, unvalidated route
    by which curve data could reach the database -- the arrangement
    api/imports.py's docstring argues against at length. Re-parsing costs
    milliseconds and cannot disagree with the preview.

    `async def` for the upload read only; the parse and the insert are
    handed to the threadpool, for the reason preview_curve above gives.
    """
    content = await file.read()
    return await run_in_threadpool(
        _create_curve, file.filename, content, metadata, calendar, curve_id, db,
        user.id)


def _free_slug(slug: str, db: Session) -> str:
    """
    `slug`, or the first `slug-2`, `slug-3`... not already taken by SOMEBODY.

    Curve ids are one global primary key while curves themselves are per user,
    so two accounts each importing "shcal20-local" would collide on an insert
    even though neither can see the other's. Refusing the second would be both
    a confusing dead end and a disclosure -- "that id is taken" says a curve
    exists that the caller cannot see.

    Suffixing instead. The id is an internal handle: pickers, tables and the
    PDF all show the curve's NAME, which is the user's own and untouched, so
    the only visible effect is a slightly longer id in a URL. The caller's own
    clash with their own curve is checked separately and still refused, because
    that one they can see and act on.
    """
    taken = {row[0] for row in db.query(StoredCalibrationCurve.id).all()}
    taken.update(curve_registry.BUILTIN_CURVE_IDS)
    if slug not in taken:
        return slug
    for n in range(2, 1000):
        candidate = f'{slug}-{n}'
        if candidate not in taken:
            return candidate
    # A thousand curves sharing one slug is not a case worth coding around,
    # but silently reusing an id would corrupt whichever curve holds it.
    raise HTTPException(
        status_code=409,
        detail=f"Could not find a free id based on '{slug}'. Choose another id.",
    )


def _create_curve(filename: Optional[str], content: bytes, metadata: str,
                  calendar: str, curve_id: Optional[str], db: Session,
                  owner_id: str) -> Dict:
    meta = CurveMetadata.model_validate_json(metadata)

    _check_size(content)
    text = curve_import.decode(content)
    result = curve_import.parse_curve(text, calendar=calendar)
    if not result.ok:
        raise HTTPException(
            status_code=422,
            detail={"message": "The curve file could not be read.", "errors": result.errors},
        )

    slug = (curve_id or slugify(meta.name)).strip().lower()
    if not SLUG_PATTERN.match(slug):
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{slug}' is not a usable curve id. Use 2-64 characters: "
                "lowercase letters, digits, hyphens or underscores."
            ),
        )
    if curve_registry.resolve_alias(slug, owner_id) is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"You already have a calibration curve '{slug}'. Curve data is "
                "immutable, so a correction is imported as a new curve under "
                "a new id rather than replacing this one."
            ),
        )
    # Past their own curves, the id still has to be unique across the table.
    # Someone else's identical slug is not the caller's problem to solve.
    slug = _free_slug(slug, db)
    for alias in meta.aliases:
        clash = curve_registry.resolve_alias(alias, owner_id)
        if clash is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"The alias '{alias}' already refers to the curve "
                    f"'{clash}'. An alias that names two curves would make an "
                    "imported file's curve column ambiguous."
                ),
            )

    curve = CalibrationCurve(
        [p[0] for p in result.points],
        [p[1] for p in result.points],
        [p[2] for p in result.points],
    )

    row = StoredCalibrationCurve(
        id=slug,
        kind='imported',
        name=meta.name,
        description=meta.description,
        reference=meta.reference,
        doi=meta.doi,
        type=meta.type,
        sample_type=meta.sample_type,
        source_text=text,
        source_filename=filename,
        parse_options=result.parse_options,
        content_sha256=curve_registry.content_hash(curve),
        aliases=meta.aliases,
        archived=False,
        created_by=owner_id,
    )
    db.add(row)
    db.commit()
    curve_registry.invalidate()

    return {
        "success": True,
        "data": curve_registry.get_spec(slug).as_dict(),
        "notes": result.notes,
        "warnings": result.warnings,
    }


@router.post("/curves/mixed", status_code=201)
def create_mixed_curve(request: MixedCurveRequest, db: Session = Depends(get_db),
                       user: User = Depends(current_user)):
    """
    Define a curve as a proportional mixture of two others, for samples whose
    carbon came from more than one reservoir -- most often a human or animal
    with a partly marine diet, whose bone is neither terrestrial nor marine
    carbon and is wrong by centuries against either curve alone.

    Stored as the recipe, not as the resulting numbers: it stays honest about
    its parents, and they count as in use for as long as it exists.
    """
    for curve_id in (request.a, request.b):
        if not curve_registry.exists(curve_id, user.id):
            raise HTTPException(
                status_code=422,
                detail=f"There is no calibration curve '{curve_id}' to mix.",
            )
    if request.a == request.b:
        raise HTTPException(
            status_code=422,
            detail="A curve mixed with itself is just that curve.",
        )

    spec_a = curve_registry.get_spec(request.a)
    spec_b = curve_registry.get_spec(request.b)
    name = request.name or f"{spec_a.name}/{spec_b.name} {request.percent_a:g}%"
    slug = (request.curve_id or slugify(name)).strip().lower()

    if not SLUG_PATTERN.match(slug):
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{slug}' is not a usable curve id. Use 2-64 characters: "
                "lowercase letters, digits, hyphens or underscores."
            ),
        )
    if curve_registry.resolve_alias(slug, user.id) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"You already have a calibration curve '{slug}'.",
        )
    slug = _free_slug(slug, db)

    existing = curve_registry.find_mixed(
        request.a, request.b, request.percent_a, request.percent_a_error, user.id
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{existing}' is already this mixture. Two curves with the "
                "same recipe would be the same numbers under two names, and "
                "would make matching an .oxcal file's Mix_Curves to one of "
                "them arbitrary."
            ),
        )

    try:
        mixed = curve_mix.mix(
            curve_registry.resolve(request.a),
            curve_registry.resolve(request.b),
            request.percent_a,
            request.percent_a_error,
            name=name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # A mixture containing any marine carbon is a marine sample's curve, and
    # so should offer a reservoir offset -- which is what sample_type decides.
    marine = 'marine' in (spec_a.sample_type, spec_b.sample_type)

    row = StoredCalibrationCurve(
        id=slug,
        kind='mixed',
        name=name,
        description=request.description or curve_mix.describe(
            spec_a.name, spec_b.name, request.percent_a, request.percent_a_error
        ),
        reference=request.reference,
        type='mixed',
        sample_type='marine' if marine else 'terrestrial',
        recipe={
            'a': request.a,
            'b': request.b,
            'percent_a': request.percent_a,
            'percent_a_error': request.percent_a_error,
        },
        content_sha256=curve_registry.content_hash(mixed),
        aliases=request.aliases,
        archived=False,
        created_by=user.id,
    )
    db.add(row)
    db.commit()
    curve_registry.invalidate()

    return {"success": True, "data": curve_registry.get_spec(slug).as_dict()}


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


def _stored_or_404(curve_id: str, db: Session, owner_id: str) -> StoredCalibrationCurve:
    """
    A stored curve of `owner_id`'s, for the routes that change or remove one.

    The builtin case keeps its 403 and its explanation: those three ids are
    public knowledge, so saying "that one ships with the app" reveals nothing
    and is far more use than a 404.

    Another account's curve gets the plain 404, exactly as an id that names
    nothing does -- the distinction this whole module now maintains.
    """
    row = db.query(StoredCalibrationCurve).filter(
        StoredCalibrationCurve.id == curve_id
    ).one_or_none()
    if row is None:
        if curve_id in curve_registry.BUILTIN_CURVE_IDS:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"'{curve_id}' is one of the curves shipped with this app "
                    "and cannot be edited or removed."
                ),
            )
        raise HTTPException(status_code=404, detail=f"Unknown calibration curve '{curve_id}'.")
    if row.created_by != owner_id:
        raise HTTPException(status_code=404, detail=f"Unknown calibration curve '{curve_id}'.")
    return row


@router.patch("/curves/{curve_id}")
def update_curve(
    curve_id: str,
    update: CurveMetadataUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    Correct a stored curve's description, citation or aliases.

    Its numbers are not editable here and never will be: an analysis already
    run against this curve has to keep meaning what it said. Importing a
    corrected file creates a new curve.
    """
    row = _stored_or_404(curve_id, db, user.id)
    fields = update.model_dump(exclude_unset=True)

    for alias in fields.get('aliases') or []:
        clash = curve_registry.resolve_alias(alias, user.id)
        if clash is not None and clash != curve_id:
            raise HTTPException(
                status_code=409,
                detail=f"The alias '{alias}' already refers to the curve '{clash}'.",
            )

    for key, value in fields.items():
        setattr(row, key, value)
    db.commit()
    curve_registry.invalidate(curve_id)
    return {"success": True, "data": curve_registry.get_spec(curve_id).as_dict()}


@router.get("/curves/{curve_id}/usage")
def curve_usage(curve_id: str, user: User = Depends(current_user)):
    """What is already calibrated against this curve. Empty for a curve
    nothing uses, which is the only kind that can be deleted outright.

    `in_use` counts EVERY user's work, because that is what decides whether the
    curve may be deleted. The id lists hold only this user's own analyses and
    projects; everyone else's are reduced to `others_count`, so the answer is
    complete without handing one user a list of another's row ids. See
    curve_registry.usage."""
    if not curve_registry.visible_to(curve_id, user.id):
        raise HTTPException(status_code=404, detail=f"Unknown calibration curve '{curve_id}'.")
    return {"success": True, "data": curve_registry.usage(curve_id, owner_id=user.id)}


@router.delete("/curves/{curve_id}")
def delete_curve(
    curve_id: str,
    archive: bool = Query(False, description="Archive instead of deleting"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """
    Remove a stored curve, or archive it.

    A curve something has been calibrated against is refused, and the refusal
    names what is using it: deleting it would leave saved results that can be
    displayed but never reproduced. Archiving is the way out -- the curve
    leaves every picker but still resolves, so those analyses still rerun.
    """
    row = _stored_or_404(curve_id, db, user.id)

    if archive:
        row.archived = True
        db.commit()
        curve_registry.invalidate(curve_id)
        return {"success": True, "data": {"id": curve_id, "archived": True}}

    # No owner_id: the guard is about everyone's work, not this user's. A
    # curve another analyst has calibrated against must not be deletable just
    # because the person deleting it has not used it themselves.
    in_use = curve_registry.usage(curve_id, owner_id=user.id)
    if in_use['in_use']:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"'{curve_id}' is in use and cannot be deleted. Archive it "
                    "instead: it will disappear from the curve pickers, and "
                    "the analyses below will still rerun."
                ),
                "usage": in_use,
            },
        )

    db.delete(row)
    db.commit()
    curve_registry.invalidate(curve_id)
    return {"success": True, "data": {"id": curve_id, "deleted": True}}


@router.post("/curves/{curve_id}/restore")
def restore_curve(curve_id: str, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    """Bring an archived curve back into the pickers."""
    row = _stored_or_404(curve_id, db, user.id)
    row.archived = False
    db.commit()
    curve_registry.invalidate(curve_id)
    return {"success": True, "data": curve_registry.get_spec(curve_id).as_dict()}


@router.get("/curves/{curve_id}/export")
def export_curve(curve_id: str, user: User = Depends(current_user)):
    """
    The curve as a .14c file.

    An imported curve comes back byte-for-byte as it was uploaded -- that is
    what storing the source text rather than a parsed array buys, and it means
    a curve can leave this app in exactly the state it arrived. Anything else
    (a builtin, later a mixed curve) is rendered from its points into the same
    format.
    """
    visible_or_404(curve_id, user.id)
    try:
        spec = curve_registry.get_spec(curve_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    row = None
    if spec.kind != 'builtin':
        with SessionLocal() as db:
            row = db.query(StoredCalibrationCurve).filter(
                StoredCalibrationCurve.id == curve_id
            ).one_or_none()

    if row is not None and row.source_text:
        text = row.source_text
        filename = row.source_filename or f'{curve_id}.14c'
    else:
        curve = curve_registry.resolve(curve_id)
        text = curve_import.to_14c_text(
            list(zip(curve.cal_ages.tolist(), curve.c14_ages.tolist(), curve.c14_errors.tolist())),
            header=f'{spec.name}\n{spec.reference}',
        )
        filename = f'{curve_id}.14c'

    return StreamingResponse(
        iter([text]),
        media_type='text/plain',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )
