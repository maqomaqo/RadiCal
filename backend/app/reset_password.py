"""
Reset an account's password from the server.

    python -m app.reset_password someone@example.org

For an installation that cannot send email, which is most of them: the person
who runs RadiCal sets a new password for whoever forgot theirs. Prompts for it
twice, without echoing it. In the published image:

    docker compose exec radical python -m app.reset_password someone@example.org

There is no admin account in the app to do this from, deliberately (see
api/auth.py). Access to the server is the authority here, and anyone with
that already has the database.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from app.database import SessionLocal
from app.models import User
from app.services import auth


def reset(email: str, new_password: str) -> None:
    """Set the password for `email`. LookupError if no account; ValueError if unusable."""
    if len(new_password) < 8:
        raise ValueError('A password needs at least 8 characters.')
    password_hash = auth.hash_password(new_password)
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == auth.normalise_email(email)).first()
        if user is None:
            raise LookupError(f'There is no account for {email}.')
        user.password_hash = password_hash
        db.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='python -m app.reset_password',
        description="Set a new password for a RadiCal account.")
    parser.add_argument('email', help="the account's email address")
    args = parser.parse_args(argv)

    if not sys.stdin.isatty():
        print('Run this in a terminal: it asks for the new password.', file=sys.stderr)
        return 2
    first = getpass.getpass('New password: ')
    if getpass.getpass('Same again: ') != first:
        print('The two did not match. Nothing changed.', file=sys.stderr)
        return 1
    try:
        reset(args.email, first)
    except (LookupError, ValueError) as exc:
        print(f'{exc} Nothing changed.', file=sys.stderr)
        return 1
    print(f'Done. {args.email} can sign in with the new password.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
