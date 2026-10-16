"""
Passwords and bearer tokens.

The arithmetic of authentication, with no HTTP in it: hashing, verification,
signing and decoding. api/auth.py is the routes and the FastAPI dependency
that use it, and keeping the two apart is what lets the rules below be tested
without a request.

Choices worth stating, since each is a place where the obvious thing is worse:

- **bcrypt, not a general-purpose hash.** A password hash has to be slow on
  purpose. `cost` below is the work factor; 12 is the current sensible default
  and costs about a quarter of a second per verification on this hardware,
  which is the point.

- **The secret comes from the environment and the app refuses to start without
  it in production.** A hardcoded fallback secret is not a convenience, it is
  a published private key: anyone with the source can mint a token for any
  account. The development default exists so `docker compose up` works on a
  fresh checkout, and it is loud about being one.

- **Tokens carry the user id and an expiry, and nothing else.** No email, no
  display name, no role. Everything else is read from the database on each
  request, so revoking or changing an account takes effect immediately rather
  than at the next token refresh.

- **There is no refresh token and no server-side session table.** A token is
  valid until it expires. That is the right trade for this app -- the
  alternative buys revocation, which matters when tokens are long-lived, and
  these are not.

- **Personal access tokens are the long-lived exception, so they are stored.**
  A script that re-runs a project overnight cannot sign in every twelve hours.
  Its token (`rct_...`, up to a year) has a row (models.PersonalAccessToken)
  so it can be revoked, and only a hash of it is kept.

- **A password-reset link is a token too, and needs no table either.** It is
  signed like a session token but marked for its one purpose, lasts an hour,
  and carries a fingerprint of the password hash it was issued against -- so
  it works once, and not at all after the password has changed.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

# How long a token lasts. Long enough that an analyst is not logged out in the
# middle of a day's work, short enough that a stolen one is not permanent.
TOKEN_TTL = timedelta(hours=12)

_ALGORITHM = 'HS256'

# The value RADICAL_SECRET_KEY takes when nobody has set one. Named rather
# than random so a restart does not invalidate every token during development,
# and named THIS so it is obvious in a log, a token, or a code search that the
# deployment has not been configured.
_DEVELOPMENT_SECRET = 'radical-development-secret-do-not-use-in-a-deployment'

# bcrypt work factor. See the module docstring.
_COST = 12


class AuthError(Exception):
    """A token that cannot be trusted. Never says which of the reasons it was."""


@dataclass(frozen=True)
class TokenClaims:
    user_id: str
    expires_at: datetime


def secret_key() -> str:
    """
    The signing key.

    Read on every call rather than captured at import, so a test can set it
    and so the check below cannot be skipped by import order.
    """
    configured = os.getenv('RADICAL_SECRET_KEY', '').strip()
    if configured:
        return configured

    # RADICAL_ENV is the deployment's own declaration. The default is
    # development, so the failure mode of forgetting to set it is a working
    # app on a laptop rather than a broken one -- but anything that has
    # declared itself must also have declared a key.
    if os.getenv('RADICAL_ENV', 'development').lower() not in ('development', 'dev', 'test'):
        raise RuntimeError(
            'RADICAL_SECRET_KEY is not set. It signs every login token, so a '
            'deployment without one is a deployment where anyone holding this '
            'source can mint a token for any account. Set it to a long random '
            'string -- `python -c "import secrets; print(secrets.token_urlsafe(48))"` '
            '-- and keep it out of version control.'
        )
    return _DEVELOPMENT_SECRET


def using_the_development_secret() -> bool:
    """Whether the app is signing tokens with the published default."""
    return secret_key() == _DEVELOPMENT_SECRET


def hash_password(password: str) -> str:
    """
    A bcrypt hash of `password`, as text.

    bcrypt truncates silently at 72 BYTES, which is not the same as 72
    characters once anyone uses an accented letter or an emoji. Rejecting the
    input is better than accepting a password whose tail does not count:
    silently ignoring the end of a long passphrase is exactly the failure a
    long passphrase was chosen to avoid.
    """
    encoded = password.encode('utf-8')
    if len(encoded) > 72:
        raise ValueError(
            'That password is too long for bcrypt, which ignores anything past '
            '72 bytes. Use a shorter one -- a long passphrase whose end is '
            'silently discarded is weaker than it looks.'
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=_COST)).decode('utf-8')


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    """
    Whether `password` matches the stored hash.

    False rather than an exception for every failure, including a malformed or
    missing hash: a caller comparing a password has exactly one useful
    question, and a hash that cannot be parsed is not a match.
    """
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except (ValueError, TypeError):
        return False


def create_token(user_id: str, now: Optional[datetime] = None) -> str:
    """A signed bearer token for `user_id`."""
    issued = now or datetime.now(timezone.utc)
    expires = issued + TOKEN_TTL
    return jwt.encode(
        {'sub': user_id, 'iat': int(issued.timestamp()), 'exp': int(expires.timestamp())},
        secret_key(),
        algorithm=_ALGORITHM,
    )


def read_token(token: str) -> TokenClaims:
    """
    The claims in `token`, or AuthError.

    `algorithms` is pinned to one value deliberately. A decoder that accepts
    whatever the token's own header names will accept `alg: none`, which is
    the classic way a JWT library is turned into an unauthenticated one.
    """
    try:
        payload = jwt.decode(token, secret_key(), algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc

    # A password-reset link is signed with the same key and names the same
    # user, so without this it would also be a twelve-hour session.
    if 'purpose' in payload:
        raise AuthError('not a session token')

    user_id = payload.get('sub')
    if not isinstance(user_id, str) or not user_id:
        raise AuthError('token has no subject')

    return TokenClaims(
        user_id=user_id,
        expires_at=datetime.fromtimestamp(payload['exp'], tz=timezone.utc),
    )


# How long a password-reset link works. Long enough to find the email, short
# enough that one left in an inbox is not a standing way into the account.
RESET_TTL = timedelta(hours=1)

_RESET_PURPOSE = 'password-reset'


def _password_fingerprint(password_hash: str) -> str:
    """
    What a reset token remembers of the password it was issued against.

    A reset link stops working the moment the password changes -- by being
    used, or by its owner remembering the password after all -- without a table
    of issued links: the token carries this, and it no longer matches. A hash
    of the bcrypt hash, so the token (readable by anyone who holds it) shows
    nothing of the hash itself.
    """
    return hashlib.sha256(password_hash.encode('utf-8')).hexdigest()[:32]


def create_reset_token(user_id: str, password_hash: str,
                       now: Optional[datetime] = None) -> str:
    """A signed, one-use, short-lived token for setting a new password."""
    issued = now or datetime.now(timezone.utc)
    return jwt.encode(
        {'sub': user_id, 'purpose': _RESET_PURPOSE,
         'pwd': _password_fingerprint(password_hash),
         'iat': int(issued.timestamp()), 'exp': int((issued + RESET_TTL).timestamp())},
        secret_key(),
        algorithm=_ALGORITHM,
    )


def read_reset_token(token: str, password_hash_of) -> str:
    """
    The user id a reset token is for, or AuthError.

    `password_hash_of(user_id)` returns that user's current hash, or None if
    there is no such user; the token is good only while the hash is the one
    it was issued against.
    """
    try:
        payload = jwt.decode(token, secret_key(), algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc
    if payload.get('purpose') != _RESET_PURPOSE:
        raise AuthError('not a reset token')
    user_id = payload.get('sub')
    if not isinstance(user_id, str) or not user_id:
        raise AuthError('token has no subject')
    current = password_hash_of(user_id)
    if not current or not secrets.compare_digest(
            str(payload.get('pwd') or ''), _password_fingerprint(current)):
        raise AuthError('used, superseded, or for no account')
    return user_id


def normalise_email(email: str) -> str:
    """
    The stored form of an address.

    Case-folded and trimmed at the boundary rather than compared
    case-insensitively at each call site, so `Ann@x.org` and `ann@x.org`
    cannot become two accounts that each believe they are the only one.
    """
    return (email or '').strip().lower()


# Personal access tokens. The prefix marks one at a glance -- in a script, a
# log, or a secret scanner's rules -- and tells current_user which kind of
# bearer token it has been given without trying to decode it as a JWT.
ACCESS_TOKEN_PREFIX = 'rct_'
# The lifetimes offered, in days. Every token expires; a year is the longest,
# enough for a project's re-runs, short enough that a forgotten one lapses.
ACCESS_TOKEN_LIFETIMES = (7, 30, 90, 365)
ACCESS_TOKEN_DEFAULT_LIFETIME = 90
# 'read' may only GET; 'full' may do whatever its owner can.
ACCESS_TOKEN_SCOPES = ('read', 'full')
# Characters of the secret kept to identify a token in the list.
_SHOWN_PREFIX = 12


def new_access_token() -> str:
    """A fresh secret: the prefix and 256 random bits."""
    return ACCESS_TOKEN_PREFIX + secrets.token_urlsafe(32)


def is_access_token(token: str) -> bool:
    return token.startswith(ACCESS_TOKEN_PREFIX)


def access_token_hash(token: str) -> str:
    """What is stored and looked up. A fast hash is enough for a random secret."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def access_token_prefix(token: str) -> str:
    """The part shown in the list, which identifies a token but cannot be used as one."""
    return token[:_SHOWN_PREFIX]
