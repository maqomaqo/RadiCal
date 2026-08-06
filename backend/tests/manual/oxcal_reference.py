"""
An exact, MCMC-free reference for the OxCal-style uniform-phase model, used
to verify what app/services/bayesian.py actually samples.

Deliberately independent of that module: the boundaries are integrated
NUMERICALLY over a finite window here, where bayesian.py marginalises the
outermost ones analytically and samples the interior ones. Agreement between
the two is therefore evidence about the model rather than a shared-bug
tautology. See tests/manual/README.md for how to drive it.

Two models are covered, both following the OxCal formulation
(https://c14.arch.ox.ac.uk/oxcal3/math_gi.htm; Buck et al. 1992; Nicholls and
Jones 2001):

  phase_marginals     one Phase between two Boundaries
  sequence_marginals  a Sequence of Phases separated by shared Boundaries

Conditional on its enclosing boundaries a group's dated events are
independent and uniform between them, so

    p(t, b) ∝ ∏_i (b_i - b_(i+1))^(-K_i) ∏_(dates) L(t)

with each t in its own group's interval and flat priors on the boundaries
over the model window. Because every date other than the one being
marginalised enters only through its integrated likelihood over an interval,
the exact marginals need no K-dimensional integral -- just interval integrals
from a cumulative sum, and a 2D sum (one phase) or forward/backward message
passing over the boundaries (a sequence).
"""

import numpy as np


def likelihood_on_grid(curve, grid, age_bp, error_1sigma, delta_r=0.0, delta_r_error=0.0):
    """Unnormalised calibration likelihood L(t) on an arbitrary calendar grid."""
    c14, curve_err = curve.interpolate(grid)
    sigma2 = curve_err ** 2 + error_1sigma ** 2 + delta_r_error ** 2
    return np.exp(-0.5 * (c14 - (age_bp - delta_r)) ** 2 / sigma2) / np.sqrt(sigma2)


def unmodelled_marginals(curve, grid, samples):
    """Plain calibration, normalised on the same grid the reference uses."""
    dt = grid[1] - grid[0]
    out = []
    for s in samples:
        lik = likelihood_on_grid(curve, grid, s['age_bp'], s['error_1sigma'],
                                 s.get('delta_r', 0.0) or 0.0,
                                 s.get('delta_r_error', 0.0) or 0.0)
        out.append(lik / (lik.sum() * dt))
    return out


def phase_marginals(curve, grid, samples):
    """
    Exact marginal posteriors for K dates in one bounded phase.

    Returns (date_marginals, ta_marginal, tb_marginal), each normalised as a
    density on `grid`. Index order is ascending cal BP, so a LARGER index is
    OLDER: ta (the older boundary) is the upper index of an interval and tb
    the lower.
    """
    dt = grid[1] - grid[0]
    M = len(grid)
    K = len(samples)

    # Per-date likelihood, each normalised so the interval integrals F_j stay
    # in [0, 1] and the products below can't underflow.
    L = np.array([
        likelihood_on_grid(curve, grid, s['age_bp'], s['error_1sigma'],
                           s.get('delta_r', 0.0) or 0.0,
                           s.get('delta_r_error', 0.0) or 0.0)
        for s in samples
    ])
    L /= (L.sum(axis=1, keepdims=True) * dt)

    # F[j][b, a] = integral of L_j over [grid[b], grid[a]], via cumulative sum.
    cum = np.cumsum(L, axis=1) * dt                       # (K, M)
    padded = np.concatenate([np.zeros((K, 1)), cum], axis=1)
    F = padded[:, None, 1:] - padded[:, :-1, None]        # (K, M_b, M_a)
    np.clip(F, 0.0, None, out=F)

    span = grid[None, :] - grid[:, None]                  # (M_b, M_a), >0 where a > b
    upper = span > 0

    with np.errstate(divide='ignore', invalid='ignore'):
        log_span_term = np.where(upper, -K * np.log(np.where(upper, span, 1.0)), -np.inf)
        log_F = np.where(F > 0, np.log(np.where(F > 0, F, 1.0)), -np.inf)

    log_all_F = log_F.sum(axis=0)                         # (M_b, M_a)
    log_W = log_span_term + log_all_F
    log_W = np.where(np.isfinite(log_W), log_W, -np.inf)
    shift = log_W.max()
    W = np.exp(log_W - shift)                             # joint weight per (tb, ta)

    date_marginals = []
    for j in range(K):
        # G = W / F_j: every other date's contribution, this one left free.
        log_G = np.where(np.isfinite(log_W), log_W - log_F[j], -np.inf)
        G = np.exp(np.where(np.isfinite(log_G), log_G - shift, -np.inf))
        G = np.where(np.isfinite(G), G, 0.0)
        # S[k] = sum over {tb <= k} x {ta >= k}: cumulative down b, reverse-
        # cumulative up a, read off the diagonal.
        quadrant = np.cumsum(G, axis=0)
        quadrant = np.cumsum(quadrant[:, ::-1], axis=1)[:, ::-1]
        S = np.diagonal(quadrant).copy()
        dens = L[j] * S
        total = dens.sum() * dt
        date_marginals.append(dens / total if total > 0 else dens)

    ta = W.sum(axis=0)
    tb = W.sum(axis=1)
    ta = ta / (ta.sum() * dt)
    tb = tb / (tb.sum() * dt)
    return date_marginals, ta, tb


def hpd(grid, density, confidence=0.95):
    """Total width and (min, max) envelope of the HPD region on a grid."""
    dt = grid[1] - grid[0]
    mass = density * dt
    order = np.argsort(mass)[::-1]
    running = np.cumsum(mass[order])
    cut = np.searchsorted(running, confidence) + 1
    chosen = np.sort(order[:cut])
    return len(chosen) * dt, (float(grid[chosen[0]]), float(grid[chosen[-1]]))


def summarise(grid, density):
    dt = grid[1] - grid[0]
    mass = density * dt
    mass = mass / mass.sum()
    mean = float((grid * mass).sum())
    cdf = np.cumsum(mass)
    median = float(np.interp(0.5, cdf, grid))
    w68, _ = hpd(grid, density, 0.68)
    w95, env95 = hpd(grid, density, 0.95)
    return {'mean': mean, 'median': median, 'hpd68_width': w68,
            'hpd95_width': w95, 'hpd95_envelope': env95}


def _interval_integrals(L, dt):
    """F[j][y, o] = integral of L_j over [grid[y], grid[o]] (y younger index)."""
    cum = np.cumsum(L, axis=1) * dt
    padded = np.concatenate([np.zeros((L.shape[0], 1)), cum], axis=1)
    F = padded[:, None, 1:] - padded[:, :-1, None]
    return np.clip(F, 0.0, None)


def _transfer(grid, L_group, dt, num_dates):
    """
    T[y, o]: the weight of a group spanning [grid[y], grid[o]], with its own
    dates integrated out. Zero where the interval is empty or inverted.
    """
    M = len(grid)
    span = grid[None, :] - grid[:, None]          # [y, o]
    valid = span > 0
    with np.errstate(divide='ignore', invalid='ignore'):
        log_T = np.where(valid, -num_dates * np.log(np.where(valid, span, 1.0)), -np.inf)
    if num_dates:
        F = _interval_integrals(L_group, dt)
        with np.errstate(divide='ignore'):
            log_F = np.where(F > 0, np.log(np.where(F > 0, F, 1.0)), -np.inf)
        log_T = log_T + log_F.sum(axis=0)
        return log_T, log_F
    return log_T, np.zeros((0, M, M))


def sequence_marginals(curve, grid, groups, span_correction=False):
    """
    `groups` is a list of lists of measurement dicts, oldest group first.
    Returns (date_marginals_by_group, boundary_marginals) as densities on
    `grid`; there are len(groups)+1 boundaries.
    """
    dt = grid[1] - grid[0]
    M = len(grid)
    n_bounds = len(groups) + 1

    log_T, log_F = [], []
    for members in groups:
        if members:
            L = np.array([
                likelihood_on_grid(curve, grid, m['age_bp'], m['error_1sigma'],
                                   m.get('delta_r', 0.0) or 0.0,
                                   m.get('delta_r_error', 0.0) or 0.0)
                for m in members
            ])
            L /= (L.sum(axis=1, keepdims=True) * dt)
        else:
            L = np.zeros((0, M))
        t, f = _transfer(grid, L, dt, len(members))
        log_T.append(t)
        log_F.append(f)

    scale = max(t[np.isfinite(t)].max() for t in log_T)
    T = [np.exp(np.where(np.isfinite(t), t - scale, -np.inf)) for t in log_T]
    T = [np.where(np.isfinite(x), x, 0.0) for x in T]

    if span_correction and n_bounds > 2:
        return _with_span_correction(curve, grid, groups, T, log_T, log_F, n_bounds, scale)

    # Forward: alpha[i] is a density over b_i given everything older.
    alpha = [np.ones(M)]
    for i in range(len(groups)):
        alpha.append(T[i].T.dot(alpha[i]) if False else (alpha[i][None, :] * T[i]).sum(axis=1))
    beta = [None] * (n_bounds)
    beta[n_bounds - 1] = np.ones(M)
    for i in range(len(groups) - 1, -1, -1):
        beta[i] = (T[i] * beta[i + 1][:, None]).sum(axis=0)

    boundary_marginals = []
    for i in range(n_bounds):
        b = alpha[i] * beta[i]
        total = b.sum() * dt
        boundary_marginals.append(b / total if total > 0 else b)

    date_marginals = []
    for i, members in enumerate(groups):
        per_group = []
        for j, m in enumerate(members):
            # Weight over (b_(i+1), b_i) with this date's own integral removed.
            with np.errstate(invalid='ignore'):
                lg = np.where(np.isfinite(log_T[i]), log_T[i] - log_F[i][j], -np.inf)
            G = np.exp(np.where(np.isfinite(lg), lg - scale, -np.inf))
            G = np.where(np.isfinite(G), G, 0.0)
            W = G * alpha[i][None, :] * beta[i + 1][:, None]
            # Sum over {b_i >= t} x {b_(i+1) <= t}, read off the diagonal.
            q = np.cumsum(W, axis=0)
            q = np.cumsum(q[:, ::-1], axis=1)[:, ::-1]
            L_j = likelihood_on_grid(curve, grid, m['age_bp'], m['error_1sigma'],
                                     m.get('delta_r', 0.0) or 0.0,
                                     m.get('delta_r_error', 0.0) or 0.0)
            dens = L_j * np.diagonal(q).copy()
            total = dens.sum() * dt
            per_group.append(dens / total if total > 0 else dens)
        date_marginals.append(per_group)

    return date_marginals, boundary_marginals


def _with_span_correction(curve, grid, groups, T, log_T, log_F, n_bounds, scale=0.0):
    """
    Same model times OxCal's 1/(b_1 - b_n)^(n-2) span factor. Only implemented
    for three boundaries (two phases), which is enough to size the effect: the
    factor couples the outer pair, so the sum is done over the full
    (b_1, b_2, b_3) cube.
    """
    if n_bounds != 3:
        raise NotImplementedError("span correction reference covers two phases")
    dt = grid[1] - grid[0]
    M = len(grid)
    # W[b3, b2, b1] = T_1(b2, b1) * T_2(b3, b2) / (b1 - b3)^(n-2)
    outer = grid[None, :] - grid[:, None]                    # [b3, b1]
    with np.errstate(divide='ignore', invalid='ignore'):
        corr = np.where(outer > 0, outer ** -(n_bounds - 2), 0.0)
    W = T[0][None, :, :] * T[1][:, :, None] * corr[:, None, :]

    # W's axes are (b3, b2, b1), so summing out the other two leaves each
    # boundary in turn -- collected oldest (b1) first to match the caller.
    boundary_marginals = []
    for axes in ((0, 1), (0, 2), (1, 2)):
        b = W.sum(axis=axes)
        total = b.sum() * dt
        boundary_marginals.append(b / total if total > 0 else b)

    date_marginals = []
    for i, members in enumerate(groups):
        per_group = []
        for j, m in enumerate(members):
            with np.errstate(invalid='ignore'):
                lg = np.where(np.isfinite(log_T[i]), log_T[i] - log_F[i][j], -np.inf)
            G = np.exp(np.where(np.isfinite(lg), lg - scale, -np.inf))
            G = np.where(np.isfinite(G), G, 0.0)
            # Sum the (b3, b2, b1) cube down to the pair of boundaries that
            # actually enclose this date, keeping [younger, older] order.
            if i == 0:
                Wj = G[None, :, :] * T[1][:, :, None] * corr[:, None, :]
                pair = Wj.sum(axis=0)          # -> [b2, b1]
            else:
                Wj = T[0][None, :, :] * G[:, :, None] * corr[:, None, :]
                pair = Wj.sum(axis=2)          # -> [b3, b2]
            q = np.cumsum(pair, axis=0)
            q = np.cumsum(q[:, ::-1], axis=1)[:, ::-1]
            L_j = likelihood_on_grid(curve, grid, m['age_bp'], m['error_1sigma'])
            dens = L_j * np.diagonal(q).copy()
            total = dens.sum() * dt
            per_group.append(dens / total if total > 0 else dens)
        date_marginals.append(per_group)

    return date_marginals, boundary_marginals
