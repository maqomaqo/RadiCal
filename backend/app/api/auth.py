"""
Registering, logging in, and the dependency every other route hangs off.

Registration is OPEN: anyone who can reach the app can create an account. The
gate is expected to be whatever fronts the deployment -- a VPN, an
institutional SSO proxy, a private network -- and what this adds behind that
gate is attribution and separation, not admission control.

**Everything is private to its owner.** A project, an analysis, a job and an
imported or mixed calibration curve belong to one user and are invisible to
every other. There is no sharing, no read-only access and no admin who can see
everything. The only things everyone shares are the three calibration curves
that ship with the app.

Two rules the rest of the API depends on, stated here because they are easy to
get subtly wrong in each individual route:

- **Someone else's row is a 404, not a 403.** A 403 confirms the id exists,
  which turns any endpoint taking an id into a way to enumerate other people's
  work. `owned_or_404` is the only way routes should reach a row.
- **A list endpoint filters, it does not check.** Filtering by owner in the
  query means a new row type cannot leak by someone forgetting to add a check
  to a loop.

A script signs in with a personal access token instead (`rct_...`, made on the
Account page): the same bearer header, a stored and revocable credential, and
optionally read-only. A token cannot make or revoke tokens, or change the
password -- `browser_user` -- so a leaked one cannot entrench itself.
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.security import HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Analysis, PersonalAccessToken, Project, StoredCalibrationCurve, User
from app.services import auth, mail

router = APIRouter()
log = logging.getLogger(__name__)


class RegisterRequest(BaseModel):
    email: EmailStr
    # Eight is a floor, not a policy. Composition rules (a digit, a symbol, a
    # capital) are known to push people towards shorter, more guessable
    # passwords, so length is the only thing checked. The 72-byte ceiling is
    # bcrypt's, and is enforced in services/auth.hash_password with an
    # explanation rather than silently truncating.
    password: str = Field(min_length=8, max_length=72)
    display_name: Optional[str] = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def _user_json(user: User) -> dict:
    """A user as the API reports one. Never includes the hash."""
    return {
        'id': user.id,
        'email': user.email,
        'display_name': user.display_name,
        'is_first_account': bool(user.is_first_account),
        'created_at': user.created_at.isoformat() if user.created_at else None,
    }


def _adopt_existing_work(db: Session, user: User) -> int:
    """
    Give every ownerless project, analysis and stored curve to the first
    account.

    The data that existed before this table has no owner, and an ownerless row
    is visible to nobody -- so without this, installing an update would appear
    to delete every analysis in the database. It runs once, for the first
    account only, and touches nothing that already has an owner.

    Curves are adopted alongside, and have to be: an analysis calibrated
    against an imported curve would otherwise come back to its owner with its
    curve gone, which is worse than the analysis being missing, because the
    analysis is still there and no longer reproducible.

    Not a data migration, though it looks like one: a migration runs before any
    user exists, so there is nobody to adopt the rows TO. This is the earliest
    moment the question can be answered.
    """
    adopted = 0
    for model in (Project, Analysis):
        for row in db.query(model).filter(model.owner_id.is_(None)).all():
            row.owner_id = user.id
            adopted += 1
    # Same idea, different column name.
    for curve in db.query(StoredCalibrationCurve).filter(
            StoredCalibrationCurve.created_by.is_(None)).all():
        curve.created_by = user.id
        adopted += 1
    return adopted


@router.post("/auth/register", status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """
    Create an account and return a token for it.

    Returns the token, so registering logs you in -- there is no useful state
    in which an account exists and its owner is not signed in to it.
    """
    email = auth.normalise_email(payload.email)
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(
            status_code=409,
            detail='An account already exists for that address.',
        )

    try:
        password_hash = auth.hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Two people registering at the same instant could both read zero and both
    # be flagged first. Tolerated rather than locked against: the flag is
    # informational, the adoption below is idempotent (the second finds nothing
    # ownerless left to take), and the alternative -- a partial unique index --
    # would make one of two simultaneous sign-ups fail outright, which is a
    # worse outcome than a duplicate flag on an install that has exactly one
    # such moment in its life.
    first = db.query(User).count() == 0
    user = User(
        email=email,
        password_hash=password_hash,
        display_name=(payload.display_name or '').strip() or None,
        is_first_account=first,
        last_login_at=datetime.utcnow(),
    )
    db.add(user)
    # Flush rather than commit: the adoption below has to be part of the same
    # transaction, so a failure there cannot leave a first account that owns
    # none of the work it was created to own.
    db.flush()

    adopted = _adopt_existing_work(db, user) if first else 0
    db.commit()
    db.refresh(user)

    return {
        'success': True,
        'data': {
            'token': auth.create_token(user.id),
            'user': _user_json(user),
            # Reported rather than silent: on a first install after an upgrade
            # this is the number that tells the user their existing work is
            # still there and now belongs to them.
            'adopted_existing_records': adopted,
        },
    }


@router.post("/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """
    Exchange an address and password for a token.

    One message for both "no such account" and "wrong password", and the
    password is verified even when the account does not exist, so the two
    cannot be told apart by what comes back or by how long it took. Otherwise
    this endpoint is a way to find out who has an account here.
    """
    email = auth.normalise_email(payload.email)
    user = db.query(User).filter(User.email == email).first()

    # A real hash to check against when there is no user, so the bcrypt cost is
    # paid either way. Computed here rather than stored as a constant because
    # bcrypt salts, and a constant would be one fixed hash to compare against.
    reference = user.password_hash if user else auth.hash_password('a dummy password')
    if not auth.verify_password(payload.password, reference) or user is None:
        raise HTTPException(status_code=401, detail='That email or password is not right.')

    user.last_login_at = datetime.utcnow()
    db.commit()
    db.refresh(user)

    return {
        'success': True,
        'data': {'token': auth.create_token(user.id), 'user': _user_json(user)},
    }


# Declares the bearer scheme in the OpenAPI schema, so /api/docs offers an
# Authorize button. current_user reads the header itself; this only documents it.
_bearer_scheme = HTTPBearer(
    auto_error=False,
    description='A sign-in token from POST /api/auth/login, or a personal access '
                'token (rct_...) made on the Account page.')

# What a read-only access token may do.
_READ_METHODS = {'GET', 'HEAD', 'OPTIONS'}
# last_used_at is a guide, not an audit log: written at most this often per
# token, rather than a write on every request a script makes.
_LAST_USED_RESOLUTION = timedelta(minutes=1)
_BAD_ACCESS_TOKEN = 'That access token is not valid: it is wrong, has expired, or was revoked.'


def _access_token_user(request: Request, token: str, db: Session) -> User:
    """current_user for a personal access token."""
    row = db.query(PersonalAccessToken).filter(
        PersonalAccessToken.token_hash == auth.access_token_hash(token)).first()
    now = datetime.utcnow()
    if row is None or row.expires_at <= now:
        raise HTTPException(status_code=401, detail=_BAD_ACCESS_TOKEN,
                            headers={'WWW-Authenticate': 'Bearer'})
    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None:
        raise HTTPException(status_code=401, detail=_BAD_ACCESS_TOKEN,
                            headers={'WWW-Authenticate': 'Bearer'})
    # A read-only token is refused anything but a read, whatever the route --
    # including the few POSTs that only compute, which keeps the rule one a
    # new route cannot get wrong.
    if row.scope != 'full' and request.method.upper() not in _READ_METHODS:
        raise HTTPException(status_code=403, detail='This access token is read-only.')
    if row.last_used_at is None or now - row.last_used_at >= _LAST_USED_RESOLUTION:
        row.last_used_at = now
        db.commit()
    request.state.access_token_id = row.id
    return user


def current_user(request: Request, db: Session = Depends(get_db),
                 _credentials=Depends(_bearer_scheme)) -> User:
    """
    The signed-in user, or 401.

    A dependency rather than middleware, so a route that does not want it
    (health, the docs) simply does not ask -- and so FastAPI shows in the
    generated schema which routes are protected.

    Every failure is the same 401 with the same wording. "Malformed token",
    "expired token" and "user deleted" are different to a developer and
    identical to an attacker, and distinguishing them tells the attacker which
    half of their guess was right.
    """
    header = request.headers.get('Authorization') or ''
    scheme, _, token = header.partition(' ')
    if scheme.lower() != 'bearer' or not token.strip():
        raise HTTPException(
            status_code=401,
            detail='Sign in to use this.',
            headers={'WWW-Authenticate': 'Bearer'},
        )

    if auth.is_access_token(token.strip()):
        return _access_token_user(request, token.strip(), db)
    request.state.access_token_id = None

    try:
        claims = auth.read_token(token.strip())
    except auth.AuthError:
        raise HTTPException(
            status_code=401,
            detail='Your session has expired. Sign in again.',
            headers={'WWW-Authenticate': 'Bearer'},
        )

    user = db.query(User).filter(User.id == claims.user_id).first()
    if user is None:
        # A valid signature for an account that no longer exists. Same answer.
        raise HTTPException(
            status_code=401,
            detail='Your session has expired. Sign in again.',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    return user


def browser_user(request: Request, user: User = Depends(current_user)) -> User:
    """
    current_user, refusing a personal access token: for the routes that manage
    tokens and the password. Otherwise a leaked token could mint itself a
    successor before it was revoked, or lock its owner out.
    """
    if getattr(request.state, 'access_token_id', None) is not None:
        raise HTTPException(
            status_code=403,
            detail='Access tokens and the password are managed from a signed-in browser, '
                   'not with an access token.')
    return user


def owned_or_404(row, user: User, what: str = 'That'):
    """
    `row`, if it belongs to `user`. Otherwise 404 -- for both "no such row" and
    "someone else's row", which is the whole point.

    Returning 403 for the second would confirm the id exists, which turns every
    endpoint taking an id into a way to enumerate other people's work. The two
    cases are indistinguishable from outside by design.
    """
    if row is None or getattr(row, 'owner_id', None) != user.id:
        raise HTTPException(status_code=404, detail=f'{what} was not found.')
    return row


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    """Who the caller is. The client's way to check a stored token still works."""
    return {'success': True, 'data': _user_json(user)}


@router.post("/auth/password")
def change_password(
    payload: dict,
    user: User = Depends(browser_user),
    db: Session = Depends(get_db),
):
    """
    Change your own password, proving you know the current one.

    Requiring the current password is not ceremony: without it, a token left
    behind on a shared machine is enough to lock its owner out of their own
    account permanently.
    """
    current = str(payload.get('current_password') or '')
    new = str(payload.get('new_password') or '')

    # 403, not 401: the session is fine, the answer is wrong. The browser
    # client treats any 401 as an expired session and signs out, which would
    # end the session of someone who only mistyped.
    if not auth.verify_password(current, user.password_hash):
        raise HTTPException(status_code=403, detail='That current password is not right.')
    if len(new) < 8:
        raise HTTPException(status_code=422, detail='A password needs at least 8 characters.')

    try:
        user.password_hash = auth.hash_password(new)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    db.commit()
    # No new token: the existing one still identifies the same account, and
    # issuing one here would imply the old one had been invalidated, which
    # without a session table it has not been. See services/auth.py.
    return {'success': True}


# --------------------------------------------------------------------------
# A forgotten password
# --------------------------------------------------------------------------
#
# By email when the installation can send it (services/mail.py), and otherwise
# by whoever runs the server, with `python -m app.reset_password`. Both end in
# the same place: the person sets a new password themselves.

class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=72)


# One email per address per this long. Without it, the form is a way to fill
# somebody's inbox. In memory rather than a column: the app runs as one
# process, and a restart forgetting a few minutes of it costs nothing.
_RESET_EMAIL_INTERVAL = 300.0
_last_reset_email: dict[str, float] = {}
_last_reset_email_lock = threading.Lock()


def _reset_email_allowed(email: str) -> bool:
    now = time.monotonic()
    with _last_reset_email_lock:
        for address, sent in list(_last_reset_email.items()):
            if now - sent >= _RESET_EMAIL_INTERVAL:
                del _last_reset_email[address]
        if email in _last_reset_email:
            return False
        _last_reset_email[email] = now
        return True


def _send_reset_email(config: mail.MailSettings, to: str, token: str) -> None:
    # After the response, so a reply does not take longer when the account
    # exists. A failure can only be logged: the person was already told
    # "if there is an account, an email is on its way".
    try:
        mail.send_reset_link(config, to, token)
    except Exception:
        log.exception('Could not send a password-reset email')


@router.get("/auth/options")
def auth_options():
    """What the sign-in page can offer. Open: it is read before anyone signs in."""
    return {'success': True, 'data': {'password_reset_email': mail.is_configured()}}


@router.post("/auth/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, background: BackgroundTasks,
                    db: Session = Depends(get_db)):
    """
    Email a one-hour reset link to the address, if it has an account.

    The same answer whether or not it does, for the reason login gives one
    message for both: otherwise this is a way to find out who has an account.
    """
    config = mail.settings()
    if config is None:
        raise HTTPException(
            status_code=503,
            detail='This installation cannot send email. Ask whoever runs it to reset '
                   'your password.')
    email = auth.normalise_email(payload.email)
    user = db.query(User).filter(User.email == email).first()
    if user is not None and _reset_email_allowed(email):
        token = auth.create_reset_token(user.id, user.password_hash)
        background.add_task(_send_reset_email, config, user.email, token)
    return {'success': True}


@router.post("/auth/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    """
    Set a new password with a reset link's token, and sign in.

    Signing in is what the person was trying to do when they found they could
    not, so the response is the same as login's.
    """
    def password_hash_of(user_id: str) -> Optional[str]:
        row = db.query(User).filter(User.id == user_id).first()
        return row.password_hash if row else None

    try:
        user_id = auth.read_reset_token(payload.token, password_hash_of)
    except auth.AuthError:
        raise HTTPException(
            status_code=400,
            detail='This reset link has expired or has already been used. Ask for a new one.')

    user = db.query(User).filter(User.id == user_id).one()
    try:
        user.password_hash = auth.hash_password(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    user.last_login_at = datetime.utcnow()
    db.commit()
    db.refresh(user)

    return {
        'success': True,
        'data': {'token': auth.create_token(user.id), 'user': _user_json(user)},
    }


# --------------------------------------------------------------------------
# Personal access tokens
# --------------------------------------------------------------------------

# Enough for every script a person runs; a cap so a loop cannot fill the table.
MAX_ACCESS_TOKENS = 50


class AccessTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    days: int = auth.ACCESS_TOKEN_DEFAULT_LIFETIME
    scope: Literal['read', 'full'] = 'read'


def _access_token_json(row: PersonalAccessToken) -> dict:
    """A token as the list shows it: never the secret, which is not stored."""
    return {
        'id': row.id,
        'name': row.name,
        'prefix': row.prefix,
        'scope': row.scope,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'expires_at': row.expires_at.isoformat(),
        'last_used_at': row.last_used_at.isoformat() if row.last_used_at else None,
        'expired': row.expires_at <= datetime.utcnow(),
    }


@router.get("/auth/tokens")
def list_access_tokens(user: User = Depends(browser_user), db: Session = Depends(get_db)):
    """Your personal access tokens, newest first."""
    rows = (db.query(PersonalAccessToken)
            .filter(PersonalAccessToken.user_id == user.id)
            .order_by(PersonalAccessToken.created_at.desc()).all())
    return {'success': True, 'data': [_access_token_json(row) for row in rows],
            'lifetimes': list(auth.ACCESS_TOKEN_LIFETIMES)}


@router.post("/auth/tokens", status_code=201)
def create_access_token(payload: AccessTokenRequest, user: User = Depends(browser_user),
                        db: Session = Depends(get_db)):
    """
    Make a personal access token. The secret is in this response and nowhere
    else: it is not stored, so it cannot be shown again.
    """
    if payload.days not in auth.ACCESS_TOKEN_LIFETIMES:
        raise HTTPException(
            status_code=422,
            detail='A token lasts ' + ', '.join(map(str, auth.ACCESS_TOKEN_LIFETIMES[:-1]))
                   + f' or {auth.ACCESS_TOKEN_LIFETIMES[-1]} days.')
    count = db.query(PersonalAccessToken).filter(
        PersonalAccessToken.user_id == user.id).count()
    if count >= MAX_ACCESS_TOKENS:
        raise HTTPException(
            status_code=409,
            detail=f'You have {MAX_ACCESS_TOKENS} access tokens. Revoke one you no longer use first.')
    secret = auth.new_access_token()
    now = datetime.utcnow()
    row = PersonalAccessToken(
        user_id=user.id, name=payload.name.strip(), scope=payload.scope,
        token_hash=auth.access_token_hash(secret), prefix=auth.access_token_prefix(secret),
        created_at=now, expires_at=now + timedelta(days=payload.days))
    db.add(row)
    db.commit()
    db.refresh(row)
    return {'success': True, 'data': {**_access_token_json(row), 'token': secret}}


@router.delete("/auth/tokens/{token_id}")
def revoke_access_token(token_id: str, user: User = Depends(browser_user),
                        db: Session = Depends(get_db)):
    """Revoke a token. It stops working at once; the row goes with it."""
    row = db.query(PersonalAccessToken).filter(PersonalAccessToken.id == token_id).first()
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail='That token was not found.')
    db.delete(row)
    db.commit()
    return {'success': True}
