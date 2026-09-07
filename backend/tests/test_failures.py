"""
Telling a user's failed run apart from the app's own bug: services/failures.py.

The thing under test is a distinction, not a message. Before this existed, a
failed run reported `str(exception)` whatever raised it, so "Calibration curve
'x' has no stored data" and "'NoneType' object is not subscriptable" arrived
through one field looking equally authoritative -- and the reader could not
tell which one they could act on.

So these tests are about attribution: a ValueError this package raised on
purpose keeps its own sentence, and anything else is reported as a fault in
the app without putting its repr on screen.

The classifier reads the TRACEBACK rather than the message text, which is why
none of these construct an exception and hand it over. Each one calls a real
function that really raises -- `validate_tree` on a broken tree,
`hpd_ranges_from_distribution` on nothing, `resolve` on a curve that is not
there -- so what is being tested is the frames those calls actually produce.
A message-matching classifier would pass tests written the other way and fail
in production the first time someone's phase was called "internal error".
"""
import pytest

from app.services import failures
from app.services import jobs
from app.services.calibration import hpd_ranges_from_distribution
from app.services.curve_registry import resolve
from app.services.model_tree import validate_tree


def _raised(fn):
    """The exception `fn` raises, traceback attached."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 -- capturing it is the point
        return exc
    raise AssertionError('nothing was raised')


class TestADeliberateFailure:
    """A ValueError this package raised, carrying a sentence written for a user."""

    def test_a_model_error_keeps_its_own_words(self):
        exc = _raised(lambda: validate_tree(None, []))
        failure = failures.classify(exc)
        assert failure.category == 'model'
        assert 'model tree is empty' in failure.message
        # No detail: nothing was withheld, because nothing was replaced.
        assert failure.detail is None

    def test_it_says_what_would_move_the_problem(self):
        exc = _raised(lambda: validate_tree({'element_type': 'boundary', 'id': 'b'}, []))
        message = failures.classify(exc).message
        assert 'the model as specified' in message
        assert 'structure' in message

    def test_a_calibration_error_is_a_model_error_too(self):
        # Same category, different module: the analyst's next move is still to
        # change what they gave the solver.
        exc = _raised(lambda: hpd_ranges_from_distribution([], []))
        failure = failures.classify(exc)
        assert failure.category == 'model'
        assert 'Empty ages or probabilities' in failure.message

    def test_a_missing_curve_is_its_own_category(self):
        exc = _raised(lambda: resolve('no-such-curve-at-all'))
        failure = failures.classify(exc)
        assert failure.category == 'curve'
        assert 'calibration curve' in failure.message.lower()

    def test_a_curve_failure_names_the_remedy(self):
        # The point of separating this from 'model': the fix is a curve, not a
        # change to the archaeology.
        exc = _raised(lambda: resolve('no-such-curve-at-all'))
        assert 'different curve' in failures.classify(exc).message


class TestAnUnexpectedFailure:
    """Anything the app did not raise on purpose."""

    def test_a_library_error_is_reported_as_the_app_s_fault(self):
        exc = _raised(lambda: [1, 2, 3][9])
        failure = failures.classify(exc)
        assert failure.category == 'internal'
        assert 'unexpected error inside the app' in failure.message

    def test_it_does_not_put_the_repr_on_screen(self):
        # Indexing None: a plain TypeError from the interpreter, and the one
        # whose repr used to end up in front of a user most often.
        nothing = None
        exc = _raised(lambda: nothing[0])
        failure = failures.classify(exc)
        assert 'NoneType' not in failure.message
        # ...but keeps it, because a bug report needs it.
        assert 'TypeError' in failure.detail

    def test_it_says_the_stored_results_are_intact(self):
        # Load-bearing reassurance: what alarms people about a failed run is
        # not knowing whether it ate what was already there.
        exc = _raised(lambda: 1 / 0)
        assert 'still holds whatever results it had' in failures.classify(exc).message

    def test_a_value_error_from_outside_this_package_is_not_deliberate(self):
        # Same type as every deliberate failure, raised by the standard
        # library. Matching on type alone would misclassify this.
        exc = _raised(lambda: int('not a number'))
        assert failures.classify(exc).category == 'internal'

    def test_an_exception_with_no_traceback_is_not_deliberate(self):
        # Nothing to attribute it to, so it cannot be claimed as ours.
        assert failures.classify(ValueError('bare')).category == 'internal'


class TestCancellation:
    def test_a_stopped_run_is_not_an_error(self):
        failure = failures.classify(jobs.Cancelled())
        assert failure.category == 'cancelled'
        assert 'stopped' in failure.message


class TestTheClassifierItself:
    def test_every_result_declares_a_known_category(self):
        for exc in (jobs.Cancelled(), ValueError('x'), TypeError('y'), RuntimeError()):
            assert failures.classify(exc).category in failures.CATEGORIES

    def test_it_always_returns_a_complete_sentence(self):
        # The field is shown verbatim in an alert, so a fragment or an empty
        # string is a blank alert.
        cases = [jobs.Cancelled(), ValueError(''), TypeError('y'), RuntimeError()]
        cases.append(_raised(lambda: validate_tree(None, [])))
        cases.append(_raised(lambda: resolve('no-such-curve-at-all')))
        for exc in cases:
            message = failures.classify(exc).message
            assert message, exc
            assert message[0].isupper(), message
            assert message.rstrip().endswith('.'), message

    def test_a_deliberate_error_with_nothing_to_say_falls_back(self):
        # An empty message is nothing to prefer over the generic text, so it
        # is reported the same way an unexpected failure would be.
        assert failures.classify(ValueError('')).category == 'internal'

    def test_it_attributes_to_the_innermost_frame_of_ours(self):
        # A curve failure surfacing through another service must still be a
        # curve failure: the module that chose the message is the one that
        # names the category, not whichever of ours is outermost.
        def through_a_wrapper():
            try:
                resolve('no-such-curve-at-all')
            except ValueError:
                raise
        assert failures.classify(_raised(through_a_wrapper)).category == 'curve'
