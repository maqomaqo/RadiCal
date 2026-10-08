"""
Does a 95% interval on the front speed contain the truth 95% of the time?

§11.4 is the risk this file exists for, and it is the one risk in the whole
feature that no unit test can retire: *OLS on autocorrelated residuals with an
assumed origin will always return a tidy v.* Every test in test_dispersal.py
checks that the arithmetic does what it claims on draws handed to it. None of
them can tell whether the INTERVAL means what it says once the draws come from
a real calibration and a real solver, because that depends on the whole
pipeline -- curve, likelihood, span prior, boundary posterior, joint draws,
per-draw OLS -- being calibrated end to end.

So this simulates the generative process the app claims to invert. Draw a
front at a known speed, place sites along it, put dates inside each site's
occupation, measure them against IntCal20 with realistic error, hand the
radiocarbon ages to the solver, and ask whether the reported 95% interval
contains the speed the data were built from. Repeat, and count.

The truth is the SPEED, not any date: a run can recover every arrival age
beautifully and still quote a speed interval that is systematically too narrow,
which is precisely the failure mode that makes a dispersal estimate dangerous
rather than merely wrong.

What the tolerance is
---------------------
Coverage is a binomial proportion, so the band scales with the number of
fronts rather than being a figure typed in once: three standard errors either
side of 0.95, which at 200 fronts is about +/- 4.6 points and at the default 32
is about +/- 11.6. A run seeded fixed is deterministic, so this is a tolerance
against SIMULATION noise and not against flakiness -- the same seed gives the
same answer every time, and a failure here is a real change in the pipeline.

Cost
----
One front is about four seconds, so the default 32 is the most that belongs in
an ordinary suite run. `RADICAL_COVERAGE_FRONTS=200
RADICAL_COVERAGE_SAMPLED_FRONTS=40 pytest tests/test_dispersal_coverage.py -s`
is the figure the plan asks for, and takes about thirteen minutes across four
cores.

Measured 2026-09-15: the exact path covered **94.5% of 200 fronts** and the
sampled path **92.5% of 40**, both against a nominal 95%. That is the number
this file exists to produce, and it is recorded in the plan's feature entry
beside the wiggle-match and age-depth ones.
"""
import math
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pytest

from app.services import dispersal
from app.services import exact_chain as ec
from app.services import gibbs
from app.services.calibration import get_curve

# Fronts simulated per coverage run. See the module docstring for the cost.
FRONTS = int(os.environ.get('RADICAL_COVERAGE_FRONTS', '32'))
# Fronts for the sampled path, which costs several times more per front and is
# checked for gross miscalibration rather than to a tight band.
SAMPLED_FRONTS = int(os.environ.get('RADICAL_COVERAGE_SAMPLED_FRONTS', '12'))
# Processes the fronts are spread over.
WORKERS = min(4, os.cpu_count() or 1)
# OpenBLAS threads per worker: the worker's share of the machine. The exact
# path's grid-sized matrix products run in every worker at once, and an
# OpenBLAS sized to the whole machine in each of them oversubscribes it.
# Measured 2026-10-08 in the 12-vCPU dev container, the exact test took 77 s
# with every worker at twelve threads, 53 s at one and 36-39 s at three.
# OpenBLAS is what the Linux and Windows wheels use; Accelerate, on macOS, is
# left alone, being slower capped (18 s against 16).
BLAS_THREADS = max(1, (os.cpu_count() or 1) // WORKERS)

NOMINAL = 0.95
N_SITES = 8
DATES_PER_SITE = 2
# Degrees of longitude between sites: about 100 km at 50 degrees north, so the
# front takes about a century to cross each gap at 1 km/yr.
SITE_SPACING_DEG = 1.4
LATITUDE = 50.0
ORIGIN = (LATITUDE, 0.0)
TRUE_SPEED = 1.0          # km/yr, the Neolithic-scale landmark of §10
TRUE_T0 = 6500.0          # cal BP at the origin
OCCUPATION_YEARS = 150.0  # how long each site was in use after arrival
MEASUREMENT_ERROR = 25.0


def _curve():
    """IntCal20 as three sorted arrays, for interpolation."""
    curve = get_curve('intcal20')
    order = np.argsort(curve.cal_ages)
    return (curve.cal_ages[order], curve.c14_ages[order],
            curve.c14_errors[order])


def _simulate(seed):
    """
    One front, as a model tree and a set of measured radiocarbon ages.

    The dates are scattered INSIDE each site's occupation rather than sitting
    on its arrival, which is the whole point of §6.2: the oldest date at a site
    is late by an unknown amount, and the Boundary is what corrects for it. A
    simulation that dated each site by its arrival exactly would be testing a
    model nobody is proposing.
    """
    cal_ages, c14_ages, c14_errors = _curve()
    rng = np.random.default_rng(seed)
    children, measurements = [], []

    for index in range(N_SITES):
        lon = SITE_SPACING_DEG * index
        distance = dispersal.haversine_km(LATITUDE, 0.0, LATITUDE, lon)
        arrival = TRUE_T0 - distance / TRUE_SPEED
        children.append({'id': f'b{index}', 'element_type': 'boundary',
                         'name': f'Start {index}', 'children': []})
        children.append({'id': f'p{index}', 'element_type': 'phase',
                         'name': f'Site {index}', 'children': []})
        for k in range(DATES_PER_SITE):
            # Younger than arrival: the site was occupied after the thing got
            # there, and what was dug is a sample of that occupation.
            cal = arrival - rng.uniform(0.0, OCCUPATION_YEARS)
            mu = float(np.interp(cal, cal_ages, c14_ages))
            curve_error = float(np.interp(cal, cal_ages, c14_errors))
            total = math.sqrt(MEASUREMENT_ERROR ** 2 + curve_error ** 2)
            measurements.append({
                'id': f'd{index}{k}', 'sample_name': f'S{index}-{k}',
                'age_bp': float(rng.normal(mu, total)),
                'error_1sigma': MEASUREMENT_ERROR,
                'curve_id': 'intcal20', 'element_id': f'p{index}',
                'latitude': LATITUDE, 'longitude': lon,
            })

    children.append({'id': f'b{N_SITES}', 'element_type': 'boundary',
                     'name': 'End', 'children': []})
    root = {'id': 'root', 'element_type': 'sequence', 'name': 'Region',
            'children': children}
    return root, measurements


def _covered_exact(seed):
    """True when this front's 95% interval contains the true speed."""
    root, measurements = _simulate(seed)
    solved = ec.solve(measurements, root, ec.layout(root, measurements),
                      num_samples=200, dispersal={'origin': ORIGIN},
                      interval_draws=4000)
    fit = solved['intervals']['dispersal']['wave']
    if fit['refused'] is not None:  # pragma: no cover - would fail the test
        return None
    low, high = fit['speed']['hpd_95']
    return low <= TRUE_SPEED <= high


def _covered_sampled(seed):
    root, measurements = _simulate(seed)
    solved = gibbs.solve(measurements, root, sweeps=4000, num_samples=200,
                         dispersal={'origin': ORIGIN}, interval_draws=4000)
    fit = solved['intervals']['dispersal']['wave']
    if fit['refused'] is not None:  # pragma: no cover - would fail the test
        return None
    low, high = fit['speed']['hpd_95']
    return low <= TRUE_SPEED <= high


def _run(worker, count):
    """
    `count` fronts, across processes because one core would be tedious.

    Spawned rather than forked: OpenBLAS sizes its thread pool once, as numpy
    loads, and a forked worker inherits the parent's already sized. The
    variable is set only while the pool exists, and put back.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('OPENBLAS_NUM_THREADS', str(BLAS_THREADS))
        with ProcessPoolExecutor(max_workers=WORKERS,
                                 mp_context=multiprocessing.get_context('spawn')) as pool:
            return list(pool.map(worker, range(count)))


def _band(count):
    """Three binomial standard errors either side of nominal."""
    sigma = math.sqrt(NOMINAL * (1.0 - NOMINAL) / count)
    return (NOMINAL - 3.0 * sigma, min(1.0, NOMINAL + 3.0 * sigma))


def _report(label, results):
    """
    The measured figure, printed under `-s`.

    A coverage test that only asserts tells you it did not fail. The number is
    the thing worth having -- it is what gets recorded beside the feature, and
    a drift from 95% to 91% that stays inside the band is still worth seeing.
    """
    covered = sum(results) / len(results)
    print(f'\n[coverage] {label}: {covered:.1%} of {len(results)} fronts '
          f'contained the true speed (nominal {NOMINAL:.0%})')
    return covered


@pytest.mark.slow
class TestTheIntervalMeansWhatItSays:

    def test_the_exact_path_covers_at_about_the_nominal_rate(self):
        """
        The headline check, and the one §11.4 asks for. An interval that
        covered 60% of the time would be the feature's worst failure -- a
        confident number that is wrong more often than it admits -- and
        nothing short of a simulation like this would notice.
        """
        results = _run(_covered_exact, FRONTS)
        assert None not in results, 'a simulated front was refused a fit'

        covered = _report('exact', results)
        low, high = _band(len(results))
        assert low <= covered <= high, (
            f'{covered:.1%} of {len(results)} fronts had the true speed inside '
            f'their 95% interval; expected {low:.1%}-{high:.1%}. Below the band '
            'the interval is too narrow and the app is overconfident; above it '
            'the interval is too wide and the feature is claiming less than the '
            'dates support.')

    def test_the_sampled_path_is_not_grossly_miscalibrated(self):
        """
        Fewer fronts and a looser band, because a Gibbs run costs several
        times an exact one. This is not asking whether the sampler is as good
        as the exact solver -- test_intervals.py already cross-checks the two
        -- but whether the speed interval survives the trip through an MCMC
        chain at all.
        """
        results = _run(_covered_sampled, SAMPLED_FRONTS)
        assert None not in results, 'a simulated front was refused a fit'

        covered = _report('sampled', results)
        assert covered >= 0.6, (
            f'only {covered:.1%} of {len(results)} sampled fronts contained the '
            'true speed; the sampled path is overconfident')


@pytest.mark.slow
class TestTheSimulationIsWorthTrusting:
    """
    A coverage test can pass because the pipeline is calibrated, or because
    the simulation is too easy to fail. These separate the two.
    """

    def test_the_fit_is_not_merely_wide_enough_to_contain_anything(self):
        """
        An interval spanning nothing-to-everything would score 100% coverage
        and be useless. The front here is about 1 km/yr and the interval has
        to EXCLUDE a speed twice that.
        """
        root, measurements = _simulate(0)
        fit = ec.solve(measurements, root, ec.layout(root, measurements),
                       num_samples=200, dispersal={'origin': ORIGIN},
                       interval_draws=4000)['intervals']['dispersal']['wave']

        low, high = fit['speed']['hpd_95']
        assert low <= TRUE_SPEED <= high
        assert high < 2.0 * TRUE_SPEED

    def test_the_dates_really_are_later_than_the_arrivals(self):
        """
        The §6.2 premise the whole estimator rests on. If the simulation put
        dates ON each arrival, the naive estimator would work too and this
        file would be testing nothing anybody disputes.
        """
        root, measurements = _simulate(0)
        solved = ec.solve(measurements, root, ec.layout(root, measurements),
                          num_samples=200, dispersal={'origin': ORIGIN},
                          interval_draws=4000)

        # Site 0's opening Boundary against its own oldest date, in cal BP,
        # where older is larger.
        boundary = solved['boundaries']['b0']['posterior']['posterior_median']
        oldest = max(solved['measurements'][f'd0{k}']['posterior_median']
                     for k in range(DATES_PER_SITE))
        assert boundary > oldest, (
            'the Boundary must extrapolate BEYOND the oldest date at the site '
            '-- that extrapolation is the bias correction this feature exists '
            'to apply')
