"""
Shared pytest fixtures.

Sets DATABASE_URL to a throwaway SQLite file *before* any `app.*` module
is imported -- app/database.py reads DATABASE_URL at import time to build
its engine, so this has to happen at conftest collection time (conftest.py
is always imported before test modules), not inside a fixture.

pymc/pytensor/arviz are real dependencies (see requirements.txt) needed
for the Bayesian analysis endpoint, but they're heavy -- PyTensor in
particular expects a C compiler toolchain -- and not every environment
running these tests will have them installed. Validation tests for
/analysis/bayesian don't actually need real MCMC to run: FastAPI rejects
an invalid request body via Pydantic before the route handler (and so
before any pymc code) ever executes. So if the real packages aren't
importable, minimal stand-in modules are installed just so the app's
import chain (app.api.analysis -> app.services.bayesian -> pymc/pytensor)
doesn't fail at collection time. Tests that need a *real* MCMC run to
pass (there aren't any here yet) should be marked with `requires_pymc`
and will be skipped in that case.
"""
import os
import sys
import types
import tempfile
import importlib.util
import pytest

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="radiocarbon_test_")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

os.environ.setdefault(
    "INTCAL_CURVE_PATH",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "calibration_data", "intcal20.14c")),
)

PYMC_AVAILABLE = importlib.util.find_spec("pymc") is not None

if not PYMC_AVAILABLE:
    for _name in ["pymc", "pytensor", "pytensor.tensor", "arviz"]:
        sys.modules.setdefault(_name, types.ModuleType(_name))
    sys.modules["pymc"].Model = object
    sys.modules["pymc"].Uniform = object
    sys.modules["pymc"].Potential = object
    sys.modules["pymc"].Normal = object
    sys.modules["pymc"].math = types.SimpleNamespace(switch=lambda *a, **k: None)
    sys.modules["pymc"].logp = lambda *a, **k: None
    sys.modules["pymc"].sample = lambda *a, **k: None
    sys.modules["pytensor"].tensor = sys.modules["pytensor.tensor"]
    sys.modules["pytensor.tensor"].as_tensor_variable = lambda x: x
    sys.modules["pytensor.tensor"].extra_ops = types.SimpleNamespace(searchsorted=lambda *a, **k: None)
    sys.modules["pytensor.tensor"].clip = lambda *a, **k: None
    sys.modules["pytensor.tensor"].switch = lambda *a, **k: None
    sys.modules["pytensor.tensor"].sqrt = lambda *a, **k: None

requires_pymc = pytest.mark.skipif(
    not PYMC_AVAILABLE,
    reason="pymc not installed in this environment -- skipping tests that need a real MCMC run",
)


@pytest.fixture
def client():
    """FastAPI TestClient against a fresh throwaway SQLite database."""
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def pytest_sessionfinish(session, exitstatus):
    try:
        os.remove(_DB_PATH)
    except OSError:
        pass
