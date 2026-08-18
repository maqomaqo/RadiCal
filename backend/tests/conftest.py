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


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """
    Create the tables once for the whole run.

    This used to happen only as a side effect of the first test that built the
    `client` fixture, which imports app.main. A test module that exercises a
    service directly -- test_curves.py does, since the curve registry is not
    an endpoint -- would then run against a database with no tables in it.
    """
    from app.database import init_db
    init_db()


@pytest.fixture
def remove_stored_curves():
    """
    Delete any calibration curve a test stored, once it has finished.

    Requested by the test modules that store curves, rather than autouse:
    explicit, and it only costs the modules that need it.

    A stored curve is not inert data -- it changes what the whole application
    will accept. Leaving one behind broke two tests in test_imports.py that
    use *IntCal13* as their example of a curve this app does not have, which
    it no longer was once a test had imported one under that name. They were
    right to fail. The suite shares one database (see above), so anything
    persisted under a chosen id outlives the test that made it.
    """
    from app.database import SessionLocal
    from app.models import StoredCalibrationCurve

    with SessionLocal() as db:
        before = {row[0] for row in db.query(StoredCalibrationCurve.id).all()}
    yield
    with SessionLocal() as db:
        for row in db.query(StoredCalibrationCurve).all():
            if row.id not in before:
                db.delete(row)
        db.commit()

    from app.services import curve_registry
    curve_registry.invalidate()


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
