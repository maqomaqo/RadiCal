"""
Drive the exact reference in oxcal_reference.py against what
app/services/bayesian.py actually samples, and print the comparison.

Not a pytest module -- each check runs a real MCMC and takes minutes, which
does not belong in the suite. The fast regression guards these checks led to
live in tests/test_bayesian.py (TestSpanTermsArePresent,
TestSpanTermCannotDiverge); this is the harness for re-verifying the model
itself after any change to it.

Usage, from inside the backend container:

    python tests/manual/verify_model.py phase       # one Phase, 3-6 dates
    python tests/manual/verify_model.py sequence    # two Phases, shared Boundary
    python tests/manual/verify_model.py order       # ordering-dominated
    python tests/manual/verify_model.py multiphase  # three Phases (2 sampled boundaries)
    python tests/manual/verify_model.py nuts        # NUTS as a second opinion on SMC
    python tests/manual/verify_model.py all

What to look for: `app vs reference width ratio` near 1.0 and small
`mean diff`. A ratio far from 1, or a worst r_hat above ~1.05, means the
model or the sampler has drifted from the maths.
"""
import sys
import os
import numpy as np

sys.path.insert(0, '/app')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.calibration import get_curve
from app.services.bayesian import BayesianChronology
from oxcal_reference import (
    phase_marginals,
    sequence_marginals,
    unmodelled_marginals,
    likelihood_on_grid,
    summarise,
)


# --------------------------------------------------------------------------
# Fixtures: dates whose radiocarbon ages sit exactly on the curve
# --------------------------------------------------------------------------

def build(true_ages, element_id, prefix, error=30, curve_id='intcal20'):
    curve = get_curve(curve_id)
    c14, _ = curve.interpolate(np.array(true_ages, dtype=float))
    return [
        {'id': f'{prefix}{i}', 'sample_name': f'{prefix.upper()}{i}',
         'age_bp': float(round(c14[i])), 'error_1sigma': error,
         'curve_id': curve_id, 'element_id': element_id}
        for i in range(len(true_ages))
    ]


def bracketed_sequence(phase_ids, boundary_prefix='b'):
    """Sequence with a Boundary before, between and after every Phase."""
    children = [{'id': f'{boundary_prefix}0', 'element_type': 'boundary',
                 'name': 'Start', 'children': []}]
    for i, pid in enumerate(phase_ids):
        children.append({'id': pid, 'element_type': 'phase',
                         'name': f'Phase {i}', 'children': []})
        children.append({'id': f'{boundary_prefix}{i + 1}', 'element_type': 'boundary',
                         'name': f'B{i + 1}', 'children': []})
    return {'id': 'seq', 'element_type': 'sequence', 'name': 'Site',
            'children': children}


def width(ranges):
    return sum(r['max'] - r['min'] for r in ranges)


def run_app(samples, tree, draws=2000, chains=4):
    chron = BayesianChronology(mcmc_samples=draws, mcmc_tune=1000, mcmc_chains=chains)
    model, ages = chron.build_model(samples, tree)
    idata, ages = chron.sample(model, ages)
    return chron, idata, chron.extract_results(idata, ages, samples)


def report(label, samples, true_ages, grid, unmod, ref, results, extra_cols=()):
    print(f"\n{'=' * 94}\n{label}\n{'=' * 94}")
    print(f"{'date':>8} {'true':>7} | {'unmod 95%':>10} {'ref 95%':>9} {'app 95%':>9} | "
          f"{'ref mean':>9} {'app mean':>9} {'diff':>6}")
    u, r, a, dm = [], [], [], []
    for i, s in enumerate(samples):
        us, rs = summarise(grid, unmod[i]), summarise(grid, ref[i])
        post = results['measurements'][s['id']]
        aw = width(post['hpd_95_ranges'])
        u.append(us['hpd95_width']); r.append(rs['hpd95_width']); a.append(aw)
        dm.append(post['posterior_mean'] - rs['mean'])
        print(f"{s['id']:>8} {true_ages[i]:>7.0f} | {us['hpd95_width']:>10.0f} "
              f"{rs['hpd95_width']:>9.0f} {aw:>9.0f} | {rs['mean']:>9.0f} "
              f"{post['posterior_mean']:>9.0f} {dm[-1]:>6.0f}")
    u, r, a, dm = map(np.array, (u, r, a, dm))
    print(f"\n  mean 95% width  unmodelled {u.mean():6.1f}  reference {r.mean():6.1f}"
          f"  app {a.mean():6.1f}")
    print(f"  shrink vs unmodelled:  reference {1 - r.mean() / u.mean():6.1%}"
          f"   app {1 - a.mean() / u.mean():6.1%}")
    print(f"  app vs reference: width ratio {a.mean() / r.mean():.3f}, "
          f"max |mean diff| {np.abs(dm).max():.1f} yr, rms {np.sqrt((dm ** 2).mean()):.1f} yr")
    return u, r, a, dm


def convergence(idata, samples, boundary_names):
    import arviz as az
    names = [f"age_{s['id']}" for s in samples]
    have = [n for n in boundary_names if n in idata.posterior]
    s = az.summary(idata, var_names=names + have)
    print(f"  worst r_hat {s['r_hat'].max():.2f}, lowest ess_bulk {s['ess_bulk'].min():.0f}")
    if len(have) >= 2:
        b = np.stack([idata.posterior[n].values.flatten() for n in have])
        gaps = -np.diff(b, axis=0)
        print(f"  adjacent sampled-boundary gaps: min {gaps.min():.1f} yr, "
              f"1st pct {np.percentile(gaps, 1):.1f}, median {np.median(gaps):.1f}")


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def check_phase(true_ages=(3100, 3080, 3060, 3040), dt=2.0, pad=1200.0):
    """One Phase between two Boundaries -- both are outermost, so both are
    marginalised and no boundary is sampled at all."""
    samples = build(list(true_ages), 'phase1', 's')
    curve = get_curve('intcal20')
    grid = np.arange(min(true_ages) - pad, max(true_ages) + pad + dt, dt)
    tree = bracketed_sequence(['phase1'])

    unmod = unmodelled_marginals(curve, grid, samples)
    ref, ref_ta, ref_tb = phase_marginals(curve, grid, samples)
    _chron, _idata, results = run_app(samples, tree)

    report(f'ONE PHASE, {len(samples)} dates', samples, list(true_ages),
           grid, unmod, ref, results)
    for bid, lbl, rd in (('b0', 'Start', ref_ta), ('b1', 'End', ref_tb)):
        got = results['boundaries'].get(bid)
        rs = summarise(grid, rd)
        if got:
            print(f"  {lbl:<6} ref mean {rs['mean']:7.0f} w {rs['hpd95_width']:6.0f}"
                  f"  | app mean {got['posterior']['posterior_mean']:7.0f} "
                  f"w {width(got['posterior']['hpd_95_ranges']):6.0f}")


def check_sequence(early=(3300, 3260, 3220), late=(3050, 3010, 2970),
                   dt=3.0, coarse=15.0, pad=1200.0):
    """Two Phases sharing a Boundary. One sampled boundary, so no adjacent
    sampled pair -- also reports what OxCal's UniformSpanPrior would change."""
    e = build(list(early), 'early', 'e')
    l = build(list(late), 'late', 'l')
    samples = e + l
    curve = get_curve('intcal20')
    all_true = list(early) + list(late)
    grid = np.arange(min(all_true) - pad, max(all_true) + pad + dt, dt)
    tree = bracketed_sequence(['early', 'late'])

    unmod = unmodelled_marginals(curve, grid, samples)
    ref_by_group, ref_bounds = sequence_marginals(curve, grid, [e, l])
    _chron, _idata, results = run_app(samples, tree)
    report('TWO PHASES, shared Boundary', samples, all_true, grid, unmod,
           ref_by_group[0] + ref_by_group[1], results)

    # The span correction sums a full (b1, b2, b3) cube, so it needs a coarser
    # grid to fit in memory; widths in years are only granulated by that.
    cgrid = np.arange(grid[0], grid[-1] + coarse, coarse)
    plain, _ = sequence_marginals(curve, cgrid, [e, l])
    corrected, corr_bounds = sequence_marginals(curve, cgrid, [e, l], span_correction=True)
    pw = np.mean([summarise(cgrid, d)['hpd95_width'] for g in plain for d in g])
    cw = np.mean([summarise(cgrid, d)['hpd95_width'] for g in corrected for d in g])
    print(f"  OxCal UniformSpanPrior on the dates ({coarse:.0f}-yr grid): "
          f"{pw:.1f} -> {cw:.1f} ({1 - cw / pw:+.1%} width)")
    for k, lbl in enumerate(('Start', 'Transition', 'End')):
        got = results['boundaries'].get(f'b{k}')
        rs, cs = summarise(grid, ref_bounds[k]), summarise(cgrid, corr_bounds[k])
        shown = (f"app mean {got['posterior']['posterior_mean']:7.0f} "
                 f"w {width(got['posterior']['hpd_95_ranges']):6.0f}") if got else 'app: absent'
        print(f"  {lbl:<11} ref mean {rs['mean']:7.0f} w {rs['hpd95_width']:6.0f}"
              f"  | +span-prior mean {cs['mean']:7.0f} w {cs['hpd95_width']:6.0f}  | {shown}")


def strict_order_marginals(curve, grid, samples):
    """Exact marginals under a strict ordering constraint and nothing else."""
    dt = grid[1] - grid[0]
    n = len(samples)
    L = np.array([likelihood_on_grid(curve, grid, m['age_bp'], m['error_1sigma'])
                  for m in samples])
    L /= (L.sum(axis=1, keepdims=True) * dt)
    alpha = np.zeros_like(L)
    alpha[0] = L[0]
    for i in range(1, n):
        older = np.cumsum(alpha[i - 1][::-1])[::-1] - alpha[i - 1]
        alpha[i] = L[i] * older
    beta = np.ones_like(L)
    for i in range(n - 2, -1, -1):
        contrib = L[i + 1] * beta[i + 1]
        beta[i] = np.cumsum(contrib) - contrib
    out = []
    for i in range(n):
        d = alpha[i] * beta[i]
        total = d.sum() * dt
        out.append(d / total if total > 0 else d)
    return out


def check_order(true_ages=(3100, 3070, 3040, 3010, 2980), dt=3.0, pad=1000.0):
    """A stratigraphic sequence of single-date phases with NO boundaries:
    isolates the soft (softplus) ordering penalty from the phase prior."""
    curve = get_curve('intcal20')
    samples = []
    for i, t in enumerate(true_ages):
        samples += build([t], f'p{i}', f'd{i}_')
    grid = np.arange(min(true_ages) - pad, max(true_ages) + pad + dt, dt)
    tree = {'id': 'seq', 'element_type': 'sequence', 'name': 'Site', 'children': [
        {'id': f'p{i}', 'element_type': 'phase', 'name': f'Phase {i}', 'children': []}
        for i in range(len(true_ages))]}

    unmod = unmodelled_marginals(curve, grid, samples)
    ref = strict_order_marginals(curve, grid, samples)
    _chron, idata, results = run_app(samples, tree)
    report('SEQUENCE of single-date phases, NO boundaries (pure ordering)',
           samples, list(true_ages), grid, unmod, ref, results)

    stacked = np.stack([np.array(results['measurements'][s['id']]['posterior_samples'])
                        for s in samples])
    bad = (np.diff(stacked, axis=0) > 0).any(axis=0).mean()
    print(f"  ordering violated in {bad:.2%} of draws; "
          f"worst inversion {max(float(np.max(np.diff(stacked, axis=0))), 0.0):.1f} yr")


def check_multiphase(per_phase=4, step=30, dt=2.0, pad=900.0):
    """Three Phases with a Boundary between each -- the first shape with two
    ADJACENT sampled boundaries, so the first where a gap between them can
    close (see _MIN_GROUP_SPAN_YEARS in services/bayesian.py)."""
    curve = get_curve('intcal20')
    groups_true = [[3300 - g * 200 - i * step for i in range(per_phase)] for g in range(3)]
    groups = [build(t, f'ph{g}', f'm{g}_') for g, t in enumerate(groups_true)]
    samples = [m for g in groups for m in g]
    flat_true = [t for g in groups_true for t in g]
    tree = bracketed_sequence(['ph0', 'ph1', 'ph2'])

    chron, idata, results = run_app(samples, tree)
    # The reference must integrate the boundaries over the app's OWN window,
    # or the comparison confounds a modelling difference with a different
    # integration range.
    wlo, whi = chron.model_window
    bgrid = np.arange(wlo, whi + 5.0, 5.0)
    grid = np.arange(min(flat_true) - pad, max(flat_true) + pad, dt)
    unmod = unmodelled_marginals(curve, grid, samples)
    ref_by_group, ref_bounds = sequence_marginals(curve, bgrid, groups)
    ref = [np.interp(grid, bgrid, d) for g in ref_by_group for d in g]

    report(f'THREE PHASES x {per_phase} dates, Boundary between each',
           samples, flat_true, grid, unmod, ref, results)
    convergence(idata, samples, ['boundary_b1', 'boundary_b2'])
    for k in range(4):
        got = results['boundaries'].get(f'b{k}')
        rs = summarise(bgrid, ref_bounds[k])
        if got:
            print(f"  b{k}: ref mean {rs['mean']:7.0f} w {rs['hpd95_width']:6.0f}"
                  f"  | app mean {got['posterior']['posterior_mean']:7.0f} "
                  f"w {width(got['posterior']['hpd_95_ranges']):6.0f}")


def check_nuts(per_phase=1, draws=3000, tune=2000):
    """
    Run the same model through NUTS as a second opinion on SMC. NUTS is not
    what the app uses (it handles calibration-curve multimodality badly, see
    BayesianChronology.sample) but it exploits pathological geometry far more
    efficiently, so it exposes a defective posterior that SMC only degrades on.
    """
    import arviz as az
    import pymc as pm

    groups_true = [[3300 - g * 200 - i * 30 for i in range(per_phase)] for g in range(3)]
    groups = [build(t, f'ph{g}', f'm{g}_') for g, t in enumerate(groups_true)]
    samples = [m for g in groups for m in g]

    chron = BayesianChronology()
    model, _ages = chron.build_model(samples, bracketed_sequence(['ph0', 'ph1', 'ph2']))
    names = [f"age_{s['id']}" for s in samples]
    bnames = [f'boundary_b{i}' for i in (1, 2) if f'boundary_b{i}' in model.named_vars]
    with model:
        idata = pm.sample(draws=draws, tune=tune, chains=2, cores=2,
                          target_accept=0.9, random_seed=1, progressbar=False)
    print(f"\nNUTS on 3 phases x {per_phase} dates:")
    print(az.summary(idata, var_names=names + bnames)[
        ['mean', 'sd', 'ess_bulk', 'r_hat']].to_string())
    if len(bnames) >= 2:
        gap = (idata.posterior[bnames[0]].values.flatten()
               - idata.posterior[bnames[1]].values.flatten())
        print(f"  sampled boundary gap: min {gap.min():.2f} yr, "
              f"1st pct {np.percentile(gap, 1):.2f}, median {np.median(gap):.1f}")


def check_nenthorn(draws=4000):
    """
    The app's modelled result against a real OxCal 4.4 run
    (tests/fixtures/oxcal; SHCal20, default UniformSpanPrior and resolution,
    no outlier model). The unmodelled half of this comparison is asserted in
    tests/test_oxcal_agreement.py; only the modelled half needs an MCMC and
    so lives here.

    Runs the model as the .oxcal file defines it (Quarry and Rockshelter are
    sibling Sequences, hence ordered) and also with that constraint dropped,
    because the two differ sharply and only one resembles OxCal's output.
    """
    sys.path.insert(0, os.path.join('/app', 'tests'))
    import oxcal_fixture as ox

    samples = ox.measurements()
    table = ox.table_ranges()
    dists = ox.distributions()
    late = (1950 - 1420, 1950 - 1370)          # the secondary lobe, in cal BP

    for ordered in (True, False):
        chron, idata, results = run_app(samples, ox.model_tree(ordered), draws=draws)
        label = ('as the .oxcal file defines it: Quarry ordered before Rockshelter'
                 if ordered else 'with the Quarry/Rockshelter ordering dropped')
        print(f"\n{'=' * 100}\nNENTHORN vs OxCal 4.4 -- {label}\n{'=' * 100}")
        print(f"{'date':>10} {'phase':>16} | {'OxCal 95.4% AD':>16} {'app 95.4% AD':>16}"
              f" | {'ox w':>5} {'app w':>5} | {'overlap':>7} | {'late lobe ox/app':>17}")
        ow, aw, ov, ol_ox, ol_app = [], [], [], [], []
        for m in samples:
            post = results['measurements'][m['id']]
            d = np.asarray(post['posterior_samples'])
            app_env = ox.envelope(ox.hpd_from_draws(d, 0.954))
            ox_from, ox_to = table[m['id']]['modelled'][95][0]
            oc, op_ = dists[m['id']]['posterior']

            step = float(np.median(np.diff(oc)))
            hist, _ = np.histogram(d, bins=np.append(oc - step / 2, oc[-1] + step / 2))
            hist = hist / max(hist.sum(), 1)
            om = op_ / op_.sum()
            inlobe = (oc >= late[0]) & (oc <= late[1])
            ol_ox.append(float(om[inlobe].sum()))
            ol_app.append(float(hist[inlobe].sum()))
            ov.append(ox.overlap(oc, op_, *[np.asarray(x) for x in (oc, hist)]))
            ow.append((1950 - ox_from) - (1950 - ox_to))
            aw.append(app_env[1] - app_env[0])
            print(f"{m['sample_name']:>10} {m['oxcal_phase']:>16} | "
                  f"{ox_from:7.0f}-{ox_to:<8.0f} "
                  f"{1950-app_env[1]:7.0f}-{1950-app_env[0]:<8.0f} | "
                  f"{ow[-1]:5.0f} {aw[-1]:5.0f} | {ov[-1]:7.3f} | "
                  f"{ol_ox[-1]:7.3f} {ol_app[-1]:8.3f}")
        ow, aw = np.array(ow), np.array(aw)
        print(f"\n  mean 95.4% width: OxCal {ow.mean():.1f} yr, app {aw.mean():.1f} yr"
              f" (ratio {aw.mean()/ow.mean():.3f})")
        print(f"  posterior overlap with OxCal: mean {np.mean(ov):.3f}")
        print(f"  secondary lobe (AD 1370-1420): OxCal {np.mean(ol_ox):.3f}, "
              f"app {np.mean(ol_app):.3f}")
        for bid, nm in (('b_start', 'STARTNENTHORN'), ('b_end', 'ENDNENTHORN')):
            got = results['boundaries'].get(bid)
            if not got:
                continue
            d = np.asarray(got['posterior']['posterior_samples'])
            for conf, level in ((68, 0.683), (95, 0.954)):
                e = ox.envelope(ox.hpd_from_draws(d, level))
                f, t = table[nm]['modelled'][conf][0]
                print(f"  {table[nm]['label']:<16} {conf}%  OxCal {f:.0f}-{t:.0f}"
                      f"   app {1950-e[1]:.0f}-{1950-e[0]:.0f}")
        convergence(idata, samples, ['boundary_b_start', 'boundary_b_end'])


CHECKS = {
    'phase': check_phase,
    'sequence': check_sequence,
    'order': check_order,
    'multiphase': check_multiphase,
    'nenthorn': check_nenthorn,
    'nuts': check_nuts,
}

if __name__ == '__main__':
    wanted = sys.argv[1:] or ['all']
    if wanted == ['all']:
        wanted = list(CHECKS)
    for name in wanted:
        if name not in CHECKS:
            sys.exit(f"unknown check {name!r}; pick from {', '.join(CHECKS)} or all")
        CHECKS[name]()
