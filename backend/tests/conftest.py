"""
Shared pytest fixtures.

Sets DATABASE_URL to a throwaway SQLite file *before* any `app.*` module
is imported -- app/database.py reads DATABASE_URL at import time to build
its engine, so this has to happen at conftest collection time (conftest.py
is always imported before test modules), not inside a fixture.

There is no longer anything to stub out here. The Bayesian endpoint used to
import pymc/pytensor/arviz through app.services.bayesian, which are heavy
(PyTensor expects a C compiler toolchain) and not installed everywhere, so this
file used to install stand-in modules just to keep collection working, and any
test needing a real MCMC run carried a `requires_pymc` mark that skipped it.

Both solvers are now pure numpy -- services/exact_chain.py integrates a
chain-shaped model exactly and services/gibbs.py samples anything else -- so
every test runs everywhere, and a Bayesian analysis in the suite is a real one
rather than a skipped one.
"""
import os
import tempfile

import pytest

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="radiocarbon_test_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

def _curve_path(filename):
    return os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "calibration_data", filename)
    )


# calibration.py's own fallbacks look inside backend/, where the curves are
# not checked in -- they live at the repo root and are mounted to /app/data in
# the container. Point every curve at the repo copy so tests find them whether
# or not they run under docker compose.
os.environ.setdefault("INTCAL_CURVE_PATH", _curve_path("intcal20.14c"))
os.environ.setdefault("MARINE_CURVE_PATH", _curve_path("marine20.14c"))
os.environ.setdefault("SHCAL_CURVE_PATH", _curve_path("shcal20.14c"))


@pytest.fixture
def client():
    """
    FastAPI TestClient against a throwaway SQLite database.

    The database is created once for the whole session (above) and shared
    by every test, not rebuilt per test -- so anything a test persists
    under a client-chosen id has to be unique across the entire suite.
    Model element ids are the usual trap: they are a primary key, so two
    tests posting a tree with an element called "p1" collide on the second.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def pytest_sessionfinish(session, exitstatus):
    try:
        os.remove(_DB_PATH)
    except OSError:
        pass
