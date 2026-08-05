import pymc as pm
import pytensor.tensor as pt
import numpy as np
from collections import defaultdict, deque
from typing import List, Dict, Optional, Tuple

from app.services.calibration import get_curve, CalibrationCurve, hpd_ranges_from_distribution
from app.services.stratigraphy import expand_relationships, effective_bounds

# How far beyond a sample's own calibration likelihood its Uniform prior
# is allowed to extend by default (see _practical_bounds below) -- wide
# enough to comfortably accommodate a genuine stratigraphic correction
# (order constraints pulling a sample's age away from what its own raw
# measurement alone suggests, including a real "date inversion"), while
# still being a small fraction of the full curve range (0-55,000 cal BP)
# that every sample's prior defaulted to before this fix existed.
_DEFAULT_PRIOR_PAD_YEARS = 5000.0
# Points on a sample's own calibration curve below this fraction of its
# peak probability are treated as negligible when finding that
# likelihood's practical extent (see _practical_bounds) -- looser than
# the 1e-4 CalibrationCurve.get_display_distribution uses for plotting,
# since here the window sets a hard prior bound, not just a plot's
# x-axis, and being too aggressive would risk excluding real tail mass.
_PRACTICAL_WINDOW_MASS_THRESHOLD = 1e-6
# Steepness (1/years) of the smooth softplus penalty used for every soft
# ordering constraint in this module (above/below sample constraints, and
# each context's Boundary-vs-member-age containment below) -- see the long
# comment above the above/below constraint loop in build_model for why a
# smooth penalty is used instead of a hard switch(..., 0, -inf).
ORDER_CONSTRAINT_STEEPNESS = 10.0


def _practical_bounds(curve: CalibrationCurve, age_bp: float, error_1sigma: float) -> Tuple[float, float]:
    """
    The calendar-age window a sample's OWN calibration likelihood
    actually has non-negligible mass in, padded generously -- used as
    every sample's default Uniform prior bounds instead of the full
    curve range.

    Why this matters: previously every sample's calendar-age prior was
    Uniform over the *entire* curve (e.g. 0-55,000 cal BP for IntCal20)
    unless TPQ/TAQ narrowed it, even though a real measurement's
    likelihood is normally concentrated in a window a few hundred to a
    couple of thousand years wide. That mismatch (an enormous prior
    against a tiny likelihood-informed posterior) is fine for one or two
    samples, but running many such samples together in one SMC model
    breaks down well before realistic analysis sizes: confirmed on an
    11-sample/no-constraints reproduction where roughly half the samples'
    posteriors diverged to nonsense spanning tens of thousands of years
    (PyMC's IMH kernel fits a shared multivariate-normal proposal to the
    whole particle population each stage; estimating that covariance
    reliably across many enormous, mismatched-scale dimensions from a
    finite particle budget falls apart -- clean at 8 simultaneous samples
    in that reproduction, badly broken at 10-11). Narrowing each sample's
    prior to where its own data actually says something, before any
    stratigraphic constraints are even considered, fixed it completely
    (r_hat 1.0, thousands of effective samples, on the same 11-sample
    case that previously produced means in the tens of thousands of cal
    BP against true ages in the 500s-600s cal BP).
    """
    cal_ages, probs = curve.calibrate(age_bp, error_1sigma)
    peak = float(np.max(probs))
    if peak <= 0:
        return float(curve.cal_ages[0]), float(curve.cal_ages[-1])

    significant = np.where(probs >= peak * _PRACTICAL_WINDOW_MASS_THRESHOLD)[0]
    if len(significant) == 0:
        return float(curve.cal_ages[0]), float(curve.cal_ages[-1])

    lower = float(cal_ages[significant[0]]) - _DEFAULT_PRIOR_PAD_YEARS
    upper = float(cal_ages[significant[-1]]) + _DEFAULT_PRIOR_PAD_YEARS
    return lower, upper


def _topological_order(node_ids: set, edges: List[Tuple[str, str]]) -> List[str]:
    """
    Kahn's topological sort of (older_id, younger_id) edges, oldest-first.
    Assumes the edge set is acyclic -- callers (build_model, below) only
    ever pass the already-cycle-validated older_younger edges from
    expand_relationships (BayesianRequest rejects a cyclic request before
    it reaches here). If it somehow isn't acyclic, nodes still stuck in a
    cycle are simply omitted from the returned order rather than looping
    forever.
    """
    adj = defaultdict(list)
    indeg = defaultdict(int)
    for u, v in edges:
        adj[u].append(v)
        indeg[v] += 1
        indeg.setdefault(u, indeg.get(u, 0))

    queue = deque(n for n in node_ids if indeg.get(n, 0) == 0)
    order = []
    indeg_remaining = dict(indeg)
    while queue:
        n = queue.popleft()
        order.append(n)
        for m in adj[n]:
            indeg_remaining[m] -= 1
            if indeg_remaining[m] == 0:
                queue.append(m)
    return order


def _interp_curve(x, xp: np.ndarray, fp: np.ndarray):
    """
    Differentiable linear interpolation of a calibration curve, evaluated at
    a continuous (PyMC/PyTensor) calendar-age variable `x`.

    This is what was missing from the model entirely: without it, a sample's
    calendar age was being compared directly to its radiocarbon age as if the
    two were on the same scale (mu=age, observed=radiocarbon_age). That skips
    the calibration curve altogether, so results ignored curve nonlinearities
    (plateaus, wiggles) and which curve (IntCal20/Marine20/SHCal20) was even
    selected. This function looks up the curve's expected C14 age (and its
    own 1-sigma error) at whatever calendar age the sampler is currently
    exploring, the same way the single-sample `CalibrationCurve.calibrate`
    path does, just expressed so PyMC/NUTS can differentiate through it.

    `xp` must be sorted ascending. Values of `x` outside [xp[0], xp[-1]] are
    clamped to the edge value (no extrapolation), matching
    CalibrationCurve.interpolate's numpy behaviour.
    """
    xp_t = pt.as_tensor_variable(xp)
    fp_t = pt.as_tensor_variable(fp)
    n = xp.shape[0]

    # Index of the curve segment containing x (as an integer -- this is a
    # non-differentiable "which bucket" lookup; the value returned below is
    # differentiable w.r.t. x through the linear interpolation formula, which
    # is the standard/expected way to do table lookups in a NUTS model).
    idx = pt.extra_ops.searchsorted(xp_t, x)
    idx = pt.clip(idx, 1, n - 1)

    x0 = xp_t[idx - 1]
    x1 = xp_t[idx]
    y0 = fp_t[idx - 1]
    y1 = fp_t[idx]

    slope = (y1 - y0) / (x1 - x0)
    interpolated = y0 + slope * (x - x0)

    # Clamp to edge values instead of extrapolating past the curve's range.
    interpolated = pt.switch(x <= xp[0], fp[0], interpolated)
    interpolated = pt.switch(x >= xp[-1], fp[-1], interpolated)
    return interpolated


class BayesianChronology:
    """Bayesian chronological modeling using PyMC"""

    def __init__(self, mcmc_samples: int = 2000, mcmc_tune: int = 1000,
                 mcmc_chains: int = 4):
        self.samples = mcmc_samples
        self.tune = mcmc_tune
        self.chains = mcmc_chains

    def build_model(self, measurements: List[Dict], relationships: List[Dict], groups: Optional[List[Dict]] = None):
        """
        Build PyMC model with radiocarbon measurements and stratigraphic constraints.

        Each measurement's calendar-age prior is calibrated against its own
        selected curve (curve_id, defaulting to intcal20), so mixed
        terrestrial/marine samples in the same analysis are each compared
        against the correct curve.

        `groups` (optional) lets samples be organised into named
        phases/groups -- see app.services.stratigraphy. A relationship in
        `relationships` may reference a sample or a group on either side
        (from_type/to_type); group references are expanded into pairwise
        sample constraints here via expand_relationships(). A sample's
        calendar-age prior is also tightened by TPQ/TAQ age limiters
        (tpq_age_bp/taq_age_bp), combining its own limiter with its
        group's if it belongs to one -- see effective_bounds().

        Every group/context with 2 or more dated members also gets the
        uniform-phase prior implied by OxCal's Boundary model, with the
        boundaries analytically marginalized out -- see the comment above
        the Potential loop below. The boundaries themselves are recovered
        afterwards, exactly, in extract_results(); this method records each
        context's calendar-age window in self.context_windows for it.

        Callers are expected to have already validated that `relationships`
        contains no stratigraphic cycle (BayesianRequest does this before
        the request ever reaches here); this function does not re-check.
        """
        groups = groups or []
        groups_by_id = {g['id']: g for g in groups}
        # Per-context (window_lower, window_upper), consumed by
        # extract_results() to sample that context's Boundary parameters.
        self.context_windows: Dict[str, Tuple[float, float]] = {}

        # Load (and cache) each distinct curve referenced by the measurements
        # up front, so we don't re-parse the same .14c file per sample.
        curves: Dict[str, CalibrationCurve] = {}
        for m in measurements:
            curve_id = m.get('curve_id', 'intcal20')
            if curve_id not in curves:
                curves[curve_id] = get_curve(curve_id)

        # Stratigraphic constraints. Group-to-group and group-to-sample
        # relationships are expanded to plain sample-to-sample pairs (and
        # deduplicated) here, so e.g. two relationships that both imply "A
        # is above B" don't create two PyMC variables with the same name.
        # Computed up front (rather than after building `ages`, as before)
        # because the older_younger edges are also needed to choose a
        # feasible starting point below.
        older_younger, equal_pairs = expand_relationships(measurements, groups, relationships)

        # Bounds and a naive initial guess (own raw radiocarbon age, clipped
        # to bounds) for every sample, computed before creating any PyMC
        # variable so the naive guesses can be repaired against the
        # above/below constraints first -- see the initval repair below.
        bounds: Dict[str, Tuple[float, float]] = {}
        naive_initvals: Dict[str, float] = {}
        for m in measurements:
            curve = curves[m.get('curve_id', 'intcal20')]
            curve_lower = float(curve.cal_ages[0])
            curve_upper = float(curve.cal_ages[-1])

            group = groups_by_id.get(m.get('group_id'))
            taq, tpq = effective_bounds(m, group)  # taq=min bound, tpq=max bound

            practical_lower, practical_upper = _practical_bounds(curve, m['age_bp'], m['error_1sigma'])

            lower = max(curve_lower, practical_lower, taq if taq is not None else -np.inf)
            upper = min(curve_upper, practical_upper, tpq if tpq is not None else np.inf)
            if lower >= upper:
                raise ValueError(
                    f"Sample '{m.get('sample_name', m['id'])}' has no valid age range: "
                    f"its TPQ/TAQ limiters ([{taq}, {tpq}]) don't overlap the "
                    f"calibration curve's range ([{curve_lower}, {curve_upper}]) or leave no "
                    f"room near where its own measurement is actually plausible "
                    f"([{practical_lower:.0f}, {practical_upper:.0f}])"
                )

            bounds[m['id']] = (lower, upper)
            naive_initvals[m['id']] = float(np.clip(m['age_bp'], lower, upper))

        # Repair the naive initial guesses so the MCMC starting point is
        # jointly consistent with every hard above/below constraint below.
        # Without this, a "date inversion" -- raw radiocarbon ages that
        # don't match the stratigraphic order, which measurement
        # uncertainty makes common, and which is exactly what this
        # Bayesian modeling exists to resolve -- leaves each sample's
        # naive guess exactly at its own (contradictory) raw age. PyMC's
        # default init then has to jitter its way into the feasible region
        # from there, which is a matter of luck (works for a small
        # inversion, fails outright for a large one with
        # "SamplingError: Initial evaluation of model at starting point
        # failed!", well before any real MCMC sampling of the posterior
        # this model is supposed to correct that inversion against).
        #
        # This only changes the STARTING point, in topological (oldest
        # constrained sample first) order: each sample's guess is capped
        # just below the tightest of its direct "older than me" neighbors'
        # (already-repaired) guesses. It does not touch the Potential
        # constraints themselves or the posterior they define.
        node_ids = {m['id'] for m in measurements}
        relevant_edges = [(o, y) for o, y in older_younger if o in node_ids and y in node_ids]
        predecessors: Dict[str, List[str]] = defaultdict(list)
        for older_id, younger_id in relevant_edges:
            predecessors[younger_id].append(older_id)

        initvals = dict(naive_initvals)
        margin = 1.0  # years -- keeps the repaired guess strictly inside the constraint, not right on its boundary
        for node_id in _topological_order(node_ids, relevant_edges):
            lower, upper = bounds[node_id]
            ceiling = upper
            for pred_id in predecessors.get(node_id, []):
                ceiling = min(ceiling, initvals[pred_id] - margin)
            initvals[node_id] = float(np.clip(min(initvals[node_id], ceiling), lower, upper))

        with pm.Model() as model:
            # Create calendar age variables for each sample, bounded to the
            # range actually covered by that sample's calibration curve
            # (rather than an arbitrary fixed 0-50000 window) so the sampler
            # never explores/initializes ages the curve can't inform --
            # further tightened by any TPQ/TAQ limiter on the sample itself
            # or its group.
            ages = {}
            for m in measurements:
                lower, upper = bounds[m['id']]
                ages[m['id']] = pm.Uniform(
                    f"age_{m['id']}",
                    lower=lower,
                    upper=upper,
                    initval=initvals[m['id']]
                )

            # Uniform-phase (OxCal "Boundary") prior for each context, with
            # the boundaries analytically MARGINALIZED OUT.
            #
            # The generative model is OxCal's (see
            # https://c14.arch.ox.ac.uk/oxcalhelp/hlp_analysis_oper.html;
            # Buck et al. 1992; Nicholls and Jones 2001, Appl. Statist.
            # 50(4)):
            #     ta, tb   ~ independent uniform priors over the window,
            #                ordered tb <= ta          (the two Boundaries)
            #     t_i | ta, tb ~iid Uniform(tb, ta)     (each dated member)
            # i.e. a context's dates are a RANDOM SCATTER between its start
            # and end boundary: conditional on the boundaries each date has
            # density 1/(ta-tb), so K members contribute (ta-tb)^(-K).
            #
            # Integrating ta, tb out of that (they appear nowhere else in
            # the model) leaves an exact marginal prior on the member ages
            # alone. With a = ta - max(t) >= 0 and b = min(t) - tb >= 0 and
            # span w = max(t) - min(t), the integral is
            #     \int\int (w + a + b)^(-K) da db  \propto  w^(2-K)
            # in the wide-window limit -- so the entire effect of the two
            # Boundaries on the member ages is the single Potential below.
            # (The window here is thousands of years wide while a context's
            # span is typically hundreds, so the finite-window correction
            # to that integral is negligible; the boundaries themselves are
            # recovered WITH the exact finite-window truncation in
            # extract_results.)
            #
            # Why marginalize instead of sampling ta/tb as PyMC variables
            # (as this code did previously): pm.sample_smc cannot sample
            # them. Their posterior is heavy-tailed (p(a+b) ~ u^(1-K)) and
            # SMC's IMH kernel collapses it onto the extreme member dates --
            # measured at E[a+b] = 2.0 years against a true value of 150.9
            # (exact grid integration of this very model), i.e. reporting a
            # context's start as essentially just its oldest date, the
            # order-statistic behaviour Boundaries exist to avoid. It does
            # so silently: r_hat 1.00 and ess_bulk ~8000 on those variables.
            # NUTS on the identical model gets 135.8, confirming the model
            # was right and the sampler was at fault. Marginalizing removes
            # them from the sampler entirely, and the exact conditional
            # p(a, b | t) is then drawn directly in extract_results.
            #
            # Skipped for contexts with fewer than 2 dated members, since a
            # span/boundary isn't meaningful for a single date (or none).
            # For exactly 2 members the exponent (2-K) is 0, so the marginal
            # is flat and no Potential is needed at all -- computing
            # `0 * log(span)` would only risk a NaN as span -> 0.
            group_members: Dict[str, List[str]] = defaultdict(list)
            for m in measurements:
                gid = m.get('group_id')
                if gid and m['id'] in ages:
                    group_members[gid].append(m['id'])

            for group_id, member_ids in group_members.items():
                if len(member_ids) < 2:
                    continue
                num_members = len(member_ids)

                # The window this context's boundaries may range over -- the
                # union of its members' own (already TPQ/TAQ- and curve-
                # bounded) ranges, so a context isn't artificially narrowed
                # just because e.g. one member uses a different calibration
                # curve than another.
                window_lower = min(bounds[mid][0] for mid in member_ids)
                window_upper = max(bounds[mid][1] for mid in member_ids)
                if window_upper <= window_lower:
                    continue
                self.context_windows[group_id] = (window_lower, window_upper)

                if num_members > 2:
                    member_ages = pt.stack([ages[mid] for mid in member_ids])
                    span = pt.maximum(pt.max(member_ages) - pt.min(member_ages), 1e-6)
                    pm.Potential(
                        f"uniform_phase_{group_id}",
                        (2 - num_members) * pt.log(span)
                    )

            # Above/below constraints as a smooth, steep penalty rather than
            # a hard switch(..., 0, -inf). A literal -inf Potential gives
            # SMC's beta-tempering nothing to work with: -inf * beta is
            # still -inf for every beta > 0, so there's no smooth path from
            # "unconstrained prior" to "constraint enforced" -- the
            # constraint switches on discontinuously at the very first
            # tempering step. For a short chain of 1-2 constraints enough
            # prior draws satisfy them by chance for SMC to proceed; for a
            # longer chain (confirmed on a straightforward 11-sample/
            # 10-constraint chain) the probability that a particle
            # satisfies EVERY constraint simultaneously by chance becomes
            # astronomically small (1/11! for a full strict ordering), so
            # every particle's logp goes to -inf at once, and PyMC's SMC
            # weight computation (a difference of logsumexps over -inf
            # values) divides out to NaN and crashes outright with
            # "ValueError: cannot convert float NaN to integer" --
            # reproduced directly, and confirmed independent of SMC kernel
            # choice (IMH or MH) and of the Boundary parameters above.
            #
            # -softplus(k * (younger - older)) is smooth everywhere, so
            # tempering has an actual gradient to climb from the first
            # stage on: a violating particle gets a large but FINITE
            # penalty (worse the larger the violation), rather than every
            # violating particle being equally, infinitely bad. k=10
            # (1/years) keeps compliant states at essentially zero penalty
            # (softplus of a several-year-negative margin is already
            # ~1e-4) while still making even a same-order-as-typical-
            # measurement-error violation costly enough to be practically
            # excluded from the posterior -- confirmed on the same
            # 11-sample chain: 0 crashes, <1% ordering-violation rate in
            # the final posterior draws, sensible per-sample results.
            for older_id, younger_id in older_younger:
                if older_id not in ages or younger_id not in ages:
                    continue
                pm.Potential(
                    f"constraint_{older_id}_{younger_id}",
                    -pt.softplus(ORDER_CONSTRAINT_STEEPNESS * (ages[younger_id] - ages[older_id]))
                )

            for a_id, b_id in equal_pairs:
                if a_id not in ages or b_id not in ages:
                    continue
                # Soft constraint linking the two *latent* age variables.
                # Passing observed=ages[b_id] wouldn't be a valid way to
                # link two random variables (observed must be data, not
                # another RV) -- Potential with an explicit logp evaluation
                # is the correct way to add this as a soft constraint
                # between two continuous latent variables.
                pm.Potential(
                    f"equal_{a_id}_{b_id}",
                    pm.logp(pm.Normal.dist(mu=ages[a_id], sigma=100), ages[b_id])
                )

            # Likelihood: compare each sample's OBSERVED radiocarbon age
            # against the calibration curve's PREDICTED radiocarbon age at
            # the sample's (latent) calendar age -- not against the calendar
            # age itself. The curve's own uncertainty at that point is
            # combined in quadrature with the measurement's 1-sigma error,
            # matching the same physics as the single-sample calibration path.
            for m in measurements:
                sample_id = m['id']
                curve = curves[m.get('curve_id', 'intcal20')]

                predicted_c14 = _interp_curve(ages[sample_id], curve.cal_ages, curve.c14_ages)
                curve_error = _interp_curve(ages[sample_id], curve.cal_ages, curve.c14_errors)
                combined_sigma = pt.sqrt(curve_error ** 2 + m['error_1sigma'] ** 2)

                pm.Normal(
                    f"obs_{sample_id}",
                    mu=predicted_c14,
                    sigma=combined_sigma,
                    observed=m['age_bp']
                )

        return model, ages

    def sample(self, model, ages: Dict):
        """
        Sample the posterior via Sequential Monte Carlo (SMC).

        Radiocarbon calibration curves are non-monotonic (plateaus/
        wiggles), so a sample's likelihood -- and the joint posterior once
        stratigraphic constraints are added -- can be genuinely
        multimodal (several separated, similarly-plausible calendar-year
        matches). NUTS (gradient-based HMC, PyMC's default and what this
        used previously) is well known to struggle with that: each chain
        gets trapped in whichever mode it starts near and never crosses to
        the others, so pooling chains produces several disjoint HPD
        ranges with near-equal weight determined by which mode each chain
        happened to land in, not the modes' true relative posterior mass.
        Confirmed on a plain, non-adversarial 4-sample/2-context test
        case: NUTS gave r_hat of 1.5-2.8 (should be ~1.0) and ess_bulk of
        ~5 out of thousands of draws, i.e. the chains had not actually
        converged to the same distribution at all.

        SMC anneals a population of particles from the prior to the
        posterior through a sequence of tempered intermediate
        distributions, resampling/moving particles at each step -- built
        for exactly this kind of multimodal target. The same test case
        went from r_hat 1.5-2.8 to 1.0 with thousands of effective
        samples, and the reported posteriors came out visibly tighter and
        more coherent (the previous multi-thousand-year spread was an
        artifact of unmixed chains, not real uncertainty).

        SMC has no tuning phase in the NUTS sense -- self.tune
        (mcmc_tune, part of BayesianConfigInput) has no effect here and
        is accepted purely for backward compatibility with existing
        bayesian_config payloads/persisted analyses.
        """
        with model:
            idata = pm.sample_smc(
                draws=self.samples,
                chains=self.chains,
                return_inferencedata=True,
                progressbar=True,
                random_seed=42
            )

        return idata, ages

    @staticmethod
    def _sample_boundaries(
        member_posteriors: np.ndarray,
        window_lower: float,
        window_upper: float,
        seed: int = 42,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Draw a context's Boundary parameters (start, end) from their EXACT
        conditional posterior, one pair per posterior draw of the member
        ages -- the second half of the marginalize-then-recover scheme
        build_model() sets up (see the long comment there).

        Because ta/tb appear nowhere else in the model, their conditional
        given the member ages t is available in closed form. Writing
        a = ta - max(t) >= 0 for how far the start extends beyond the oldest
        dated member, b = min(t) - tb >= 0 likewise for the end, and
        w = max(t) - min(t) for the observed span:

            p(a, b | t)  \\propto  (w + a + b)^(-K),
            a in [0, window_upper - max(t)],  b in [0, min(t) - window_lower]

        directly from the (ta-tb)^(-K) uniform-scatter term, with the flat
        boundary priors contributing only the rectangular support. Sampling
        this per draw (rather than marginally) is what makes the returned
        boundaries a genuine joint posterior sample alongside the member
        ages, exactly as if ta/tb had been sampled by the MCMC -- which is
        precisely what SMC could not do.

        Drawn by substituting u = a + b, whose density is
        (w+u)^(-K) * L(u) with L(u) the length of the {a+b = u} segment
        inside the rectangle (a trapezoid profile, which is what carries
        the finite-window truncation exactly); u is drawn by numerical
        inversion on a quadratically-spaced grid (dense near the mode) and
        a | u is then exactly uniform on its allowed interval.

        Note the tail is heavy: E[a+b] = 2w/(K-3) untruncated, so it is
        finite only for K >= 4. For a context of 2 or 3 dates the span --
        and hence how far the boundaries plausibly extend -- is genuinely
        not pinned down by the dates alone and is governed by the window;
        that is a real property of the uniform-phase model (few dates
        constrain a phase's duration weakly), not an artifact here.
        """
        K = member_posteriors.shape[0]
        mx = member_posteriors.max(axis=0)
        mn = member_posteriors.min(axis=0)
        w = mx - mn
        A = np.clip(window_upper - mx, 0.0, None)   # headroom above the oldest date
        B = np.clip(mn - window_lower, 0.0, None)   # headroom below the youngest date
        U = A + B

        num_draws = member_posteriors.shape[1]
        rng = np.random.default_rng(seed)
        r_u = rng.random(num_draws)
        r_a = rng.random(num_draws)

        a_out = np.zeros(num_draws)
        b_out = np.zeros(num_draws)

        # Quadratic spacing puts most grid points near u = 0, where the
        # density peaks (mode at u = w/(K-2)), while still reaching the far
        # end of a window that can be thousands of years wide.
        grid_n = 2048
        frac = np.linspace(0.0, 1.0, grid_n) ** 2
        chunk = 512

        for lo in range(0, num_draws, chunk):
            hi = min(lo + chunk, num_draws)
            sl = slice(lo, hi)
            u_g = U[sl, None] * frac[None, :]
            valid = U[sl] > 0
            if not np.any(valid):
                continue

            # L(u): length of the {a + b = u} segment within [0,A] x [0,B].
            seg = np.minimum.reduce([
                u_g,
                np.broadcast_to(A[sl, None], u_g.shape),
                np.broadcast_to(B[sl, None], u_g.shape),
                U[sl, None] - u_g,
            ])
            seg = np.clip(seg, 0.0, None)

            with np.errstate(divide='ignore', invalid='ignore'):
                log_dens = -K * np.log(np.maximum(w[sl, None] + u_g, 1e-9)) + np.log(seg)
            log_dens = np.where(seg > 0, log_dens, -np.inf)
            dens = np.exp(log_dens - np.nanmax(
                np.where(np.isfinite(log_dens), log_dens, -np.inf), axis=1, keepdims=True
            ))
            dens = np.where(np.isfinite(dens), dens, 0.0)

            du = np.diff(u_g, axis=1)
            cdf = np.cumsum(0.5 * (dens[:, :-1] + dens[:, 1:]) * du, axis=1)
            totals = cdf[:, -1:]
            ok = valid & (totals[:, 0] > 0)
            cdf = np.where(totals > 0, cdf / np.where(totals > 0, totals, 1.0), 0.0)

            idx = np.clip((cdf < r_u[sl, None]).sum(axis=1), 0, grid_n - 2)
            c1 = np.take_along_axis(cdf, idx[:, None], 1)[:, 0]
            c0 = np.where(idx > 0, np.take_along_axis(cdf, np.maximum(idx - 1, 0)[:, None], 1)[:, 0], 0.0)
            u0 = np.take_along_axis(u_g, idx[:, None], 1)[:, 0]
            u1 = np.take_along_axis(u_g, (idx + 1)[:, None], 1)[:, 0]
            frac_in = np.where(c1 > c0, (r_u[sl] - c0) / np.where(c1 > c0, c1 - c0, 1.0), 0.0)
            u_s = np.where(ok, u0 + np.clip(frac_in, 0.0, 1.0) * (u1 - u0), 0.0)

            # a | u is exactly uniform on the feasible sub-interval.
            a_lo = np.clip(u_s - B[sl], 0.0, None)
            a_hi = np.minimum(u_s, A[sl])
            a = np.where(a_hi > a_lo, a_lo + r_a[sl] * (a_hi - a_lo), np.clip(a_lo, 0.0, None))
            a_out[sl] = np.where(ok, a, 0.0)
            b_out[sl] = np.where(ok, u_s - a, 0.0)

        return mx + a_out, mn - b_out

    @staticmethod
    def _summarize_posterior(posterior: np.ndarray) -> Dict:
        """
        Turn a 1D array of posterior draws (calendar ages, cal BP) into the
        same summary shape used throughout this app for a plottable
        distribution: point estimates, a compact (age, density) series for
        plotting, and a multi-range HPD breakdown. Shared between per-
        sample ages and per-context start/end (see extract_results) so
        both can be rendered with the exact same frontend component.
        """
        # Turn the raw MCMC draws into a compact (cal_age, probability)
        # series the frontend can plot directly -- the same
        # [[age, prob], ...] shape the single-sample calibration endpoint
        # returns as `probability_distribution`. Sending only the raw
        # `posterior_samples` array (as before) isn't itself a density
        # curve, and the previous frontend code was constructing a fake
        # synthetic x-axis (sample index * 10) to plot it against, which
        # doesn't correspond to real calendar ages at all.
        counts, bin_edges = np.histogram(posterior, bins=100, density=True)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
        probability_distribution = [
            [float(age), float(density)] for age, density in zip(bin_centers, counts)
        ]

        # HPD ranges from the binned posterior, using the same multi-range
        # logic as the direct-calibration path (CalibrationCurve.
        # get_hpd_ranges): MCMC posteriors for radiocarbon dates can be
        # just as multimodal as the raw calibration likelihood, since the
        # same curve plateaus/wiggles drive both. A single min/max span
        # (what az.hdi's default, unimodal HDI returns) would misrepresent
        # a two-peaked posterior as one mostly-empty contiguous range.
        #
        # hpd_ranges_from_distribution reports each selected segment's
        # boundaries as ages[s]/ages[e] -- correct when `ages` are
        # themselves the discrete supported points of the distribution
        # (true for the direct-calibration path, where they're the curve's
        # own grid points), but bin_centers here are an artifact of
        # histogramming a continuous posterior: using them as boundaries
        # silently shrinks every range by a full bin width (half lost on
        # each side), and collapses any single-bin segment -- common for a
        # spread-out or multimodal posterior sampled into only 100 bins --
        # to a reported width of exactly zero. Expanding each returned
        # range by half a bin width restores the actual continuous span
        # the bins approximate.
        bin_width = bin_edges[1] - bin_edges[0]
        half_bin = bin_width / 2.0

        def _widen_to_bin_edges(ranges):
            for r in ranges:
                r['min'] -= half_bin
                r['max'] += half_bin
            return ranges

        hpd_68_ranges = _widen_to_bin_edges(hpd_ranges_from_distribution(bin_centers, counts, 0.68))
        hpd_95_ranges = _widen_to_bin_edges(hpd_ranges_from_distribution(bin_centers, counts, 0.95))
        hpd_68 = (
            min(r['min'] for r in hpd_68_ranges),
            max(r['max'] for r in hpd_68_ranges),
        )
        hpd_95 = (
            min(r['min'] for r in hpd_95_ranges),
            max(r['max'] for r in hpd_95_ranges),
        )

        return {
            'posterior_mean': float(np.mean(posterior)),
            'posterior_median': float(np.median(posterior)),
            'posterior_std': float(np.std(posterior)),
            'hpd_68': hpd_68,
            'hpd_95': hpd_95,
            'hpd_68_ranges': hpd_68_ranges,
            'hpd_95_ranges': hpd_95_ranges,
            'probability_distribution': probability_distribution,
            'posterior_samples': posterior.tolist()
        }

    def extract_results(
        self,
        idata,
        ages: Dict,
        measurements: Optional[List[Dict]] = None,
        groups: Optional[List[Dict]] = None,
    ) -> Dict:
        """
        Extract and summarize posterior distributions: one entry per
        sample's calendar age, plus -- for every context/group with 2 or
        more dated members -- the MODELLED start and end of that
        context's own span, i.e. its Boundary parameters (see the comment
        above the Potential loop in build_model for what these are and how
        they differ from the group's own member ages).

        Start/end are drawn per posterior draw from their exact conditional
        given that draw's member ages (see _sample_boundaries), completing
        the marginalize-then-recover scheme build_model() sets up: the two
        Boundaries are integrated out of the MCMC (which cannot sample them
        reliably) and recovered afterwards in closed form. The result is a
        genuine joint posterior sample of (member ages, start, end), free to
        extend beyond the oldest/youngest dated member exactly as OxCal's
        Boundary does. Requires build_model() to have been called on this
        same instance first (it records each context's window); a context
        it skipped is skipped here too, as is any context with fewer than 2
        dated members, since a span/boundary isn't meaningful for a single
        date (or none).

        Returns {'measurements': {sample_id: {...}}, 'contexts': {group_id:
        {'name': ..., 'start': {...}, 'end': {...}}}}, where every {...}
        stats dict has the shape documented in _summarize_posterior.
        """
        measurements = measurements or []
        groups = groups or []

        posteriors: Dict[str, np.ndarray] = {
            age_id: idata.posterior[age_var.name].values.flatten()
            for age_id, age_var in ages.items()
        }

        measurement_results = {
            age_id: self._summarize_posterior(posterior)
            for age_id, posterior in posteriors.items()
        }

        group_members: Dict[str, List[str]] = defaultdict(list)
        for m in measurements:
            gid = m.get('group_id')
            if gid and m['id'] in posteriors:
                group_members[gid].append(m['id'])

        groups_by_id = {g['id']: g for g in groups}
        context_windows = getattr(self, 'context_windows', {})
        context_results = {}
        for group_id, member_ids in group_members.items():
            if len(member_ids) < 2:
                continue
            window = context_windows.get(group_id)
            if window is None:
                # build_model() skipped this context (no usable window), so
                # there are no boundaries to report for it.
                continue
            member_draws = np.stack([posteriors[mid] for mid in member_ids])
            start_draws, end_draws = self._sample_boundaries(member_draws, window[0], window[1])
            context_results[group_id] = {
                'name': groups_by_id.get(group_id, {}).get('name'),
                'start': self._summarize_posterior(start_draws),
                'end': self._summarize_posterior(end_draws),
            }

        return {'measurements': measurement_results, 'contexts': context_results}
