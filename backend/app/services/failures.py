"""
What to tell someone whose run did not finish.

A failed run used to report `str(exception)` verbatim, wherever it came from.
That reads correctly for the errors this app raises on purpose -- those carry
sentences written for an analyst -- and badly for everything else, because
"'NoneType' object is not subscriptable" and "Calibration curve 'x' has no
stored data" arrive through the same field and look equally authoritative. The
reader cannot tell which of them is something they can fix.

So the job is not to invent messages. It is to tell the two apart, keep the
deliberate ones, and say plainly of the rest that they are a fault in the app
rather than in the model.

How the two are told apart: a deliberate failure is a ValueError raised inside
this package's own modules, which is the app's existing convention -- every
raise in services/ and api/ is one, each with a written message. An exception
from numpy, SQLAlchemy or the standard library is not, however similar its type
happens to be. That is a property of the traceback, not of the exception, which
is why this reads frames rather than matching on message text.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Optional

# The categories a caller may act on. Deliberately few: each one exists because
# the reader's next step differs, not because the causes differ.
#
#   curve     -- a calibration curve the analysis names is gone or unreadable.
#                Fixable: pick another curve, or restore the one it wants.
#   model     -- the model as specified cannot be solved. Fixable: change the
#                structure, the limiters or the dates.
#   cancelled -- someone stopped it. Not a failure at all.
#   internal  -- a fault in the app. Not fixable by the analyst, and saying so
#                is more use than showing them the repr.
CATEGORIES = ('curve', 'model', 'cancelled', 'internal')

# Where a deliberately-raised error comes from. A frame inside these is the
# app's own code choosing to fail with something it wrote.
_OWN_PACKAGE = ('/app/services/', '/app/api/', '\\app\\services\\', '\\app\\api\\')

# Modules whose deliberate ValueErrors are about a curve rather than a model.
_CURVE_MODULES = ('curve_registry.py', 'curve_import.py', 'curve_mix.py')


@dataclass(frozen=True)
class Failure:
    """A classified exception, ready to store and to show."""

    category: str
    """One of CATEGORIES."""

    message: str
    """What to show. Always a complete sentence, never a bare repr."""

    detail: Optional[str]
    """
    The original `str(exception)` when it was NOT shown -- kept so a log or a
    bug report has the thing the message replaced. None when `message` already
    is the original, which is the deliberate-failure case.
    """


def _raised_by_us(exc: BaseException) -> Optional[str]:
    """
    The basename of the module that raised, if the raise happened in this
    package. None for anything raised by a library or the interpreter.

    The LAST matching frame, not the first: a curve failure surfacing through
    an api/ handler should be attributed to the curve module that raised it,
    and the innermost frame is the one that chose the message.
    """
    found = None
    for frame in traceback.extract_tb(exc.__traceback__):
        filename = frame.filename.replace('\\', '/')
        if any(marker.replace('\\', '/') in filename for marker in _OWN_PACKAGE):
            found = filename.rsplit('/', 1)[-1]
    return found


def classify(exc: BaseException) -> Failure:
    """
    Turn an exception into something worth showing a user.

    Never raises: a classifier that can fail while reporting a failure would
    replace a bad message with no message at all.
    """
    try:
        return _classify(exc)
    except Exception:  # pragma: no cover -- belt and braces, see docstring
        return Failure(
            category='internal',
            message=(
                'The run stopped because of an unexpected error inside the app. '
                'The server log has the details.'
            ),
            detail=None,
        )


def _classify(exc: BaseException) -> Failure:
    raw = str(exc).strip()

    # Not an error. Included so callers have one place to ask rather than
    # checking the type themselves and then calling this for everything else.
    if type(exc).__name__ == 'Cancelled':
        return Failure(
            category='cancelled',
            message='The run was stopped before it finished.',
            detail=None,
        )

    origin = _raised_by_us(exc)
    deliberate = isinstance(exc, ValueError) and origin is not None and bool(raw)

    if deliberate:
        # The app's own sentence, kept exactly, with one clause of framing so
        # it reads as a reason rather than as a fragment. Curve failures are
        # separated because the remedy is different and the analyst can act on
        # it without touching the model.
        if origin in _CURVE_MODULES:
            return Failure(
                category='curve',
                message=(
                    f'The run could not use the calibration curve it was given: {raw} '
                    'Choosing a different curve, or restoring that one, should let it run.'
                ),
                detail=None,
            )
        return Failure(
            category='model',
            message=(
                f'The model could not be solved: {raw} '
                'This is about the model as specified, so changing the structure, '
                'the limiters or the dates is what will move it.'
            ),
            detail=None,
        )

    # Everything else. The distinction that matters to the reader is only that
    # it is not their fault, so this does not enumerate causes it cannot
    # reliably tell apart -- a MemoryError and a broken connection would both
    # be guesses from here.
    return Failure(
        category='internal',
        message=(
            'The run stopped because of an unexpected error inside the app, not '
            'because of anything about this model. Nothing was saved over, so the '
            'analysis still holds whatever results it had. The server log has the '
            'details; re-running may simply work.'
        ),
        # The repr is kept rather than shown: it is what a bug report needs and
        # what a user cannot use.
        detail=f'{type(exc).__name__}: {raw}' if raw else type(exc).__name__,
    )
