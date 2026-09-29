"""
No request handler may block the event loop.

The defect this guards against: FastAPI runs an `async def` handler ON the
event loop and a plain `def` handler in a worker thread. Every handler in
this app is synchronous throughout -- SQLAlchemy sessions, numpy solvers,
matplotlib -- so an `async def` handler holds the loop for as long as its
work takes, and NOTHING else is served in the meantime. Not another user's
page, not the health check.

Measured on the running container before this was fixed: `GET /api/curves`
costs 2.3 ms on an idle server and took 3.85 s when issued during a 4.9 s
PDF render. A Bayesian run holds it far longer -- 12.7 s for eight dates,
43.4 s for sixty, and up to about five minutes for a model that runs to the
sweep cap.

This is asserted structurally rather than by racing two requests, because
the guarantee IS structural: it is FastAPI's dispatch rule, applied to a
declaration. A timing test would be slower, flakier, and would prove less.
"""
import inspect

import pytest

from app.main import app


# The only handlers allowed to be coroutines, and why. Each one reads an
# upload -- `await file.read()` is genuinely awaitable and cannot be done
# from a thread -- and each hands everything after that read to
# run_in_threadpool, so the awaitable part is the only part on the loop.
AWAITS_AN_UPLOAD = {
    'preview_curve',
    'create_curve',
    'import_dates',
}

# Handlers that touch nothing: no database session, no file, no computation.
# They are correct on the loop and cost less there than a thread hop.
TRIVIAL = {
    'health_check',
    'root',
}


def _endpoints():
    """
    This app's own handlers. FastAPI mounts four of its own (/docs, /redoc,
    /openapi.json and the oauth2 redirect) which are coroutines it defines
    and we do not maintain; they serve static strings and a cached schema,
    so they are not the problem this file is about.

    FastAPI no longer copies an included router's routes into app.routes: it
    keeps the router, and hands out each of its routes -- full path, methods,
    endpoint -- through effective_route_contexts(). Both shapes are walked,
    so a route declared on the app itself is not missed either.
    """
    for entry in app.routes:
        expand = getattr(entry, 'effective_route_contexts', None)
        for route in (expand() if expand else [entry]):
            endpoint = getattr(route, 'endpoint', None)
            if endpoint is not None and endpoint.__module__.startswith('app.'):
                yield route, endpoint


def test_every_route_handler_is_accounted_for():
    """
    Fifty-odd handlers, and the count is not the point -- the point is that
    every one of them is either declared sync, or on a list above saying why
    it is not. A new blocking `async def` route fails here.
    """
    offenders = sorted(
        f"{sorted(route.methods or [])} {route.path} -> {endpoint.__name__}"
        for route, endpoint in _endpoints()
        if inspect.iscoroutinefunction(endpoint)
        and endpoint.__name__ not in AWAITS_AN_UPLOAD | TRIVIAL
    )
    assert not offenders, (
        "These handlers run on the event loop and will freeze the whole "
        "server for the length of their work:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize('name', sorted(AWAITS_AN_UPLOAD))
def test_an_upload_handler_only_awaits_the_read(name):
    """
    The exemption above is for reading an upload, not for doing the work.
    Each of these must still hand its body to the threadpool -- otherwise
    the exemption quietly becomes a place for blocking code to accumulate.
    """
    endpoint = next(e for _, e in _endpoints() if e.__name__ == name)
    source = inspect.getsource(endpoint)
    assert 'await file.read()' in source, f"{name} is async but reads no upload"
    assert 'run_in_threadpool' in source, (
        f"{name} awaits its upload but then does its work on the event loop"
    )


def test_the_solver_and_report_routes_are_the_ones_that_matter():
    """
    Names the specific routes whose blocking cost was measured, so that if
    one is ever reintroduced as `async def` the failure says which of them
    and roughly what it costs, rather than only that a rule was broken.
    """
    costly = {
        '/api/analysis/bayesian': 'a Bayesian run: 12.7 s to 43.4 s measured, ~5 min at the sweep cap',
        '/api/analysis/{analysis_id}/rerun': 'the same run again',
        '/api/projects/{project_id}/rerun': 'that, times every analysis in the project',
        '/api/results/{analysis_id}/pdf': 'a matplotlib figure per parameter: 4.9 s measured',
    }
    by_path = {route.path: endpoint for route, endpoint in _endpoints()}
    for path, cost in costly.items():
        assert path in by_path, f"{path} has moved; update this test"
        assert not inspect.iscoroutinefunction(by_path[path]), (
            f"{path} is back on the event loop and will hold it for {cost}"
        )
