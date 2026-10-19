"""
Time the Python sampler on the benchmark models, and write those models for
the Rust side (`cargo run --release --example sampler_bench`).

    cd backend && python ../desktop/bench/sampler_bench.py

Three models: the 8-date ordered fixture and the 16-date outlier fixture,
taken from the API corpus as the API ran them, and a synthetic sequence of
60 dates in 12 phases. Each is timed twice: a full solve as the API runs it
(four chains, three temperatures, seed 42, stopping when converged), and one
untempered chain held to a fixed 10,000 sweeps, which is the cost per sweep.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
os.environ.setdefault('DATABASE_URL', 'sqlite://')
for curve in ('intcal20', 'marine20', 'shcal20'):
    os.environ.setdefault(f'{curve[:-2].upper()}_CURVE_PATH',
                          os.path.join(ROOT, 'calibration_data', f'{curve}.14c'))
sys.path.insert(0, os.path.join(ROOT, 'backend'))
sys.path.insert(0, os.path.join(ROOT, 'desktop', 'corpus'))

import numpy as np  # noqa: E402

FIXED_SWEEPS = 10_000


def from_corpus(case_name):
    import generate
    with open(os.path.join(ROOT, 'desktop', 'corpus', 'data', 'api', 'models.json')) as fh:
        cases = json.load(fh)['cases']
    case = next(c for c in cases if c['name'] == case_name)
    request = generate.decode_series(case['inputs'])['request']
    return {'measurements': request['measurements'], 'model_tree': request['model_tree']}


def synthetic(phases=12, per_phase=5, seed=3):
    """Phases a century apart on IntCal20, each between Boundaries, dates
    drawn from the curve with a 25-year error."""
    from app.services.calibration import get_curve
    curve = get_curve('intcal20')
    rng = np.random.default_rng(seed)
    children = [{'id': 'b0', 'element_type': 'boundary', 'name': 'Start', 'children': []}]
    measurements = []
    for p in range(phases):
        centre = 4200.0 - 100.0 * p
        children.append({'id': f'p{p}', 'element_type': 'phase', 'name': f'Phase {p + 1}', 'children': []})
        children.append({'id': f'b{p + 1}', 'element_type': 'boundary',
                         'name': 'End' if p == phases - 1 else f'Transition {p + 1}/{p + 2}', 'children': []})
        for k in range(per_phase):
            truth = centre + rng.uniform(-30.0, 30.0)
            c14, sigma = curve.interpolate(np.array([truth]))
            age = float(c14[0] + rng.normal(0.0, float(np.hypot(sigma[0], 25.0))))
            measurements.append({'id': f'd{p}_{k}', 'sample_name': f'S{p + 1}-{k + 1}',
                                 'age_bp': round(age), 'error_1sigma': 25.0,
                                 'curve_id': 'intcal20', 'element_id': f'p{p}'})
    return {'measurements': measurements,
            'model_tree': {'id': 'site', 'element_type': 'sequence', 'name': 'Site', 'children': children}}


def main():
    from app.services import gibbs
    models = {
        '8 dates, ordered': from_corpus('bayesian/oxcal/Hillside_sites_3.oxcal/gibbs'),
        '16 dates, outlier model': from_corpus('bayesian/oxcal_outlier/Dune_Section.oxcal/gibbs'),
        '60 dates, 12 phases': synthetic(),
    }
    with open(os.path.join(HERE, 'models.json'), 'w') as fh:
        json.dump(models, fh, indent=1)
    print(f'{"model":28} {"solve (4 chains)":>18} {"sweeps":>8} {"10k sweeps, 1 chain":>20}')
    for name, model in models.items():
        measurements = [{k: v for k, v in m.items() if k != 'phase_name'} for m in model['measurements']]
        started = time.perf_counter()
        solved = gibbs.solve(measurements, model['model_tree'], seed=42, chains=4, num_samples=4000)
        solve_seconds = time.perf_counter() - started
        built = gibbs.Model(measurements, model['model_tree'])
        started = time.perf_counter()
        gibbs.run(built, sweeps=FIXED_SWEEPS, max_sweeps=FIXED_SWEEPS, seed=42,
                  target_convergence=2.0, temperatures=1)
        fixed_seconds = time.perf_counter() - started
        print(f'{name:28} {solve_seconds:17.1f}s {solved["sweeps"]:8d} {fixed_seconds:19.1f}s', flush=True)


if __name__ == '__main__':
    main()
