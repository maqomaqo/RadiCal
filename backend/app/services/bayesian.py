import pymc as pm
import pytensor.tensor as pt
import numpy as np
from collections import defaultdict, deque
from typing import List, Dict, Optional, Tuple

from app.services.calibration import get_curve, CalibrationCurve, hpd_ranges_from_distribution
from app.services.stratigraphy import effective_bounds
from app.services.model_tree import (
    validate_tree,
    plan_model,
    inherited_bounds,
)

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

    def build_model(self, measurements: List[Dict], model_tree: Dict):
        """
        Build the PyMC model from an explicit OxCal-style model tree
        (services/model_tree.py): nested Sequences, Phases, Boundaries and
        Hiatuses, replacing the previous flat contexts + pairwise
        above/below relationships.

        Each measurement's calendar-age prior is calibrated against its own
        selected curve (curve_id, defaulting to intcal20), so mixed
        terrestrial/marine samples in the same analysis are each compared
        against the correct curve, and is tightened by TPQ/TAQ limiters --
        the sample's own combined with every enclosing element's, tightest
        winning (model_tree.inherited_bounds).

        The tree contributes two things:

        * **Ordering.** A sequence's children run oldest first, and
          everything in one child must be older than everything in the next.
          Emitted as one min/max constraint per adjacent pair rather than a
          full pairwise cross-product. A phase orders nothing, so nesting
          sequences inside a phase models parallel branches.
        * **Uniform-scatter (Boundary) priors.** Each consecutive pair of
          Boundary siblings encloses a group of K dates that are, conditional
          on those boundaries, uniformly scattered between them -- density
          (older - younger)^(-K). A boundary between two phases is therefore
          a single SHARED transition, not two independent ones.

        Boundaries are split by model_tree.classify_boundaries into ones
        sampled as ordinary PyMC variables and ones integrated out
        analytically; see the span-term loop below and that function's
        docstring for why that split is what makes explicit boundaries
        workable under SMC at all. Marginalized boundaries are recovered
        exactly in extract_results(); this method records what it needs for
        that in self.boundary_recovery.
        """
        validate_tree(model_tree, measurements)
        plan = plan_model(model_tree, measurements)
        element_bounds = inherited_bounds(model_tree)
        # What extract_results() needs to recover the marginalized boundaries:
        # {boundary_id: (role, span-group info)}. Filled in below.
        self.boundary_recovery: Dict[str, Dict] = {}
        self.model_plan = plan

        # Load (and cache) each distinct curve referenced by the measurements
        # up front, so we don't re-parse the same .14c file per sample.
        curves: Dict[str, CalibrationCurve] = {}
        for m in measurements:
            curve_id = m.get('curve_id', 'intcal20')
            if curve_id not in curves:
                curves[curve_id] = get_curve(curve_id)

        # Bounds and a naive initial guess (own raw radiocarbon age, clipped
        # to bounds) for every sample, computed before creating any PyMC
        # variable so the naive guesses can be repaired against the ordering
        # constraints first -- see the initval repair below.
        bounds: Dict[str, Tuple[float, float]] = {}
        naive_initvals: Dict[str, float] = {}
        for m in measurements:
            curve = curves[m.get('curve_id', 'intcal20')]
            curve_lower = float(curve.cal_ages[0])
            curve_upper = float(curve.cal_ages[-1])

            # The sample's own limiters combined with every enclosing model
            # element's, tightest winning.
            el_taq, el_tpq = element_bounds.get(m.get('element_id'), (None, None))
            taq, tpq = effective_bounds(m, {'taq_age_bp': el_taq, 'tpq_age_bp': el_tpq})

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
        # This only changes the STARTING point: each constraint's younger
        # side is pushed just below its older side's current guess, sweeping
        # the constraints in tree order (which is already oldest-first within
        # each sequence) a few times so nested structures settle. It does not
        # touch the Potential constraints themselves or the posterior they
        # define.
        initvals = dict(naive_initvals)
        margin = 1.0  # years -- keeps the repaired guess strictly inside the constraint, not right on its boundary

        def _date_ids(terms):
            return [tid for kind, tid in terms if kind == 'date' and tid in initvals]

        for _sweep in range(3):
            for older_terms, younger_terms in plan.ordering:
                older_ids, younger_ids = _date_ids(older_terms), _date_ids(younger_terms)
                if not older_ids or not younger_ids:
                    continue
                ceiling = min(initvals[i] for i in older_ids) - margin
                for yid in younger_ids:
                    lower, upper = bounds[yid]
                    initvals[yid] = float(np.clip(min(initvals[yid], ceiling), lower, upper))

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

            # Boundary parameters and the uniform-scatter (OxCal "Phase")
            # priors they induce.
            #
            # The generative model is OxCal's (see
            # https://c14.arch.ox.ac.uk/oxcalhelp/hlp_analysis_oper.html;
            # Buck et al. 1992; Nicholls and Jones 2001, Appl. Statist.
            # 50(4)): boundaries have uniform priors, and conditional on the
            # pair enclosing it each dated event is uniform between them, so
            # a group of K dates between boundaries O (older) and Y younger
            # contributes (O - Y)^(-K). A boundary shared between two
            # adjacent groups is ONE parameter serving as the end of the
            # first and the start of the second.
            #
            # Which boundaries are sampled and which are integrated out is
            # decided by model_tree.classify_boundaries -- read that
            # docstring first, it explains why this split exists. In short:
            # an interior boundary is pinned between its neighbouring
            # groups' extreme dates and samples fine, whereas an outermost
            # boundary runs off into a region with no data, giving the
            # heavy tail that SMC provably collapses. An outermost boundary
            # appears in exactly one span term, so it also integrates out in
            # closed form -- which is what the four cases below are.
            #
            # Every integral is taken in the wide-window limit (the window
            # is thousands of years wide against spans of hundreds), the
            # same approximation the previous single-context implementation
            # made; the marginalized boundaries are then recovered WITH the
            # exact finite-window truncation in extract_results.
            boundary_vars: Dict[str, object] = {}
            marginalized = plan.marginalized_boundaries

            # Bounds for the sampled boundaries: the union of every date's
            # window, so a boundary is never confined more tightly than the
            # evidence it sits among.
            if bounds:
                model_lower = min(b[0] for b in bounds.values())
                model_upper = max(b[1] for b in bounds.values())
            else:
                model_lower, model_upper = 0.0, 55000.0
            # extract_results() needs the same window to recover the
            # marginalized boundaries with the correct truncation.
            self.model_window = (model_lower, model_upper)

            for boundary_id in plan.sampled_boundaries:
                # Start it in the middle of the dates it separates, so the
                # ordering constraints are satisfiable from the first draw.
                enclosing = [g for g in plan.span_groups
                             if boundary_id in (g.older_boundary_id, g.younger_boundary_id)]
                neighbour_vals = [initvals[d] for g in enclosing for d in g.date_ids if d in initvals]
                init = float(np.mean(neighbour_vals)) if neighbour_vals else (model_lower + model_upper) / 2.0
                boundary_vars[boundary_id] = pm.Uniform(
                    f"boundary_{boundary_id}",
                    lower=model_lower,
                    upper=model_upper,
                    initval=float(np.clip(init, model_lower + 1.0, model_upper - 1.0)),
                )

            for group in plan.span_groups:
                date_ids = [d for d in group.date_ids if d in ages]
                num_dates = len(date_ids)
                older_id, younger_id = group.older_boundary_id, group.younger_boundary_id
                older_marginal = older_id in marginalized
                younger_marginal = younger_id in marginalized

                if num_dates:
                    stacked = pt.stack([ages[d] for d in date_ids])
                    oldest, youngest = pt.max(stacked), pt.min(stacked)

                if older_marginal and younger_marginal:
                    # Both integrated out. Jointly this is the classic
                    # result: \int\int (w + a + b)^(-K) da db  ~  w^(2-K),
                    # w being the dates' own span. Exponent 2-K is 0 at K=2
                    # (flat -- and computing 0*log(w) would only risk a NaN
                    # as w -> 0), and a span means nothing below that.
                    if num_dates > 2:
                        span = pt.maximum(oldest - youngest, 1e-6)
                        pm.Potential(f"phase_{older_id}_{younger_id}",
                                     (2 - num_dates) * pt.log(span))
                elif older_marginal or younger_marginal:
                    # Exactly one boundary integrated out, over the interval
                    # between the extreme date it must clear and the edge of
                    # the model window:
                    #     \int_{near}^{far} s^(-K) ds
                    #         = [ near^(1-K) - far^(1-K) ] / (K - 1)
                    # Kept EXACT rather than taking the wide-window limit
                    # near^(1-K): unlike the two-sided case that limit is
                    # weak at small K (at K=2 with a window ~10x the span it
                    # is a >10% correction), and the exact form costs one
                    # extra term. Written as
                    #     e*log(near) + log1p(-exp(e*(log far - log near)))
                    # with e = 1-K < 0, which is stable because far > near
                    # makes the exponential lie strictly inside (0, 1).
                    if num_dates >= 2:
                        if older_marginal:
                            anchor = boundary_vars[younger_id]
                            near = pt.maximum(oldest - anchor, 1e-6)
                            far = pt.maximum(model_upper - anchor, near + 1e-6)
                        else:
                            anchor = boundary_vars[older_id]
                            near = pt.maximum(anchor - youngest, 1e-6)
                            far = pt.maximum(anchor - model_lower, near + 1e-6)
                        exponent = 1 - num_dates
                        log_near = pt.log(near)
                        pm.Potential(
                            f"phase_{older_id}_{younger_id}",
                            exponent * log_near
                            + pt.log1p(-pt.exp(exponent * (pt.log(far) - log_near)))
                        )
                else:
                    # Both sampled: the density applies directly.
                    if num_dates >= 1:
                        span = pt.maximum(
                            boundary_vars[older_id] - boundary_vars[younger_id], 1e-6)
                        pm.Potential(f"phase_{older_id}_{younger_id}",
                                     -num_dates * pt.log(span))

            # Ordering, as a smooth steep penalty rather than a hard
            # switch(..., 0, -inf). A literal -inf Potential gives SMC's
            # beta-tempering nothing to work with: -inf * beta is still
            # -inf for every beta > 0, so there's no smooth path from
            # "unconstrained prior" to "constraint enforced" -- the
            # constraint switches on discontinuously at the very first
            # tempering step. For a short chain enough prior draws satisfy
            # it by chance for SMC to proceed; for a longer one (confirmed
            # on a straightforward 11-sample/10-constraint chain) the
            # probability that a particle satisfies EVERY constraint at
            # once becomes astronomically small (1/11! for a full strict
            # ordering), so every particle's logp goes to -inf together and
            # PyMC's SMC weight computation (a difference of logsumexps
            # over -inf values) divides out to NaN and crashes with
            # "ValueError: cannot convert float NaN to integer" --
            # reproduced directly, and confirmed independent of SMC kernel
            # choice (IMH or MH).
            #
            # -softplus(k * (younger - older)) is smooth everywhere, so
            # tempering has an actual gradient to climb from the first
            # stage on: a violating particle gets a large but FINITE
            # penalty (worse the larger the violation) rather than every
            # violating particle being equally, infinitely bad. k=10
            # (1/years) keeps compliant states at essentially zero penalty
            # (softplus of a several-year-negative margin is already ~1e-4)
            # while still making even a same-order-as-typical-measurement-
            # error violation costly enough to be practically excluded.
            #
            # One constraint per adjacent pair of sequence children, over
            # the min of the older side and the max of the younger side --
            # equivalent to the full pairwise cross-product and far cheaper
            # for large groups.
            def _terms_to_vars(terms):
                out = []
                for kind, tid in terms:
                    if kind == 'date' and tid in ages:
                        out.append(ages[tid])
                    elif kind == 'boundary':
                        if tid in boundary_vars:
                            out.append(boundary_vars[tid])
                        # A marginalized boundary carries no variable to
                        # constrain: its ordering IS the limit of the
                        # integral that removed it.
                return out

            for index, (older_terms, younger_terms) in enumerate(plan.ordering):
                older_vars = _terms_to_vars(older_terms)
                younger_vars = _terms_to_vars(younger_terms)
                if not older_vars or not younger_vars:
                    continue
                min_older = pt.min(pt.stack(older_vars)) if len(older_vars) > 1 else older_vars[0]
                max_younger = pt.max(pt.stack(younger_vars)) if len(younger_vars) > 1 else younger_vars[0]
                pm.Potential(
                    f"order_{index}",
                    -pt.softplus(ORDER_CONSTRAINT_STEEPNESS * (max_younger - min_older))
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

    @staticmethod
    def _sample_power_law_distance(c: np.ndarray, d: np.ndarray, K: int, u: np.ndarray) -> np.ndarray:
        """
        Draw the distance s between a marginalized boundary and the boundary
        opposite it, from s ~ s^(-K) truncated to [c, d], by exact inverse
        CDF. `u` is uniform(0,1), one per posterior draw.

        This is the one-sided counterpart of _sample_boundaries: it applies
        when only ONE of a group's two boundaries was integrated out (the
        other is a sampled variable whose drawn value fixes the origin).
        """
        c = np.maximum(c, 1e-9)
        d = np.maximum(d, c + 1e-9)
        if K == 1:
            # \int s^-1 ds = log s
            return c * (d / c) ** u
        if K == 0:
            return c + u * (d - c)
        e = 1.0 - K
        return (c ** e + u * (d ** e - c ** e)) ** (1.0 / e)

    def extract_results(
        self,
        idata,
        ages: Dict,
        measurements: Optional[List[Dict]] = None,
        groups: Optional[List[Dict]] = None,
    ) -> Dict:
        """
        Summarize the posterior: one entry per sample's calendar age, plus
        one per named Boundary in the model tree.

        Boundaries that build_model() sampled are read straight out of the
        trace. Boundaries it integrated out are drawn here, per posterior
        draw, from their exact conditional given that draw -- completing the
        marginalize-then-recover scheme (see model_tree.classify_boundaries
        for why they had to be integrated out at all). Either way the result
        is a genuine joint posterior sample of (dates, boundaries), with the
        boundaries free to extend beyond the outermost dated sample exactly
        as OxCal's do.

        Two recovery cases, matching the span terms in build_model:
          * both of a group's boundaries marginalized -> the joint
            conditional p(a, b) ~ (w + a + b)^(-K), via _sample_boundaries.
          * one marginalized, the other sampled -> a one-sided power law
            anchored on the sampled one, via _sample_power_law_distance.

        Returns {'measurements': {...}, 'boundaries': {boundary_id:
        {'name': ..., 'posterior': {...}}}}, every stats dict having the
        shape documented in _summarize_posterior.
        """
        measurements = measurements or []

        posteriors: Dict[str, np.ndarray] = {
            age_id: idata.posterior[age_var.name].values.flatten()
            for age_id, age_var in ages.items()
        }
        measurement_results = {
            age_id: self._summarize_posterior(posterior)
            for age_id, posterior in posteriors.items()
        }

        plan = getattr(self, 'model_plan', None)
        if plan is None:
            return {'measurements': measurement_results, 'boundaries': {}}

        num_draws = len(next(iter(posteriors.values()))) if posteriors else 0
        rng = np.random.default_rng(42)
        window_lower = getattr(self, 'model_window', (0.0, 55000.0))[0]
        window_upper = getattr(self, 'model_window', (0.0, 55000.0))[1]

        boundary_draws: Dict[str, np.ndarray] = {}
        # Sampled boundaries come straight from the trace.
        for boundary_id in plan.sampled_boundaries:
            name = f"boundary_{boundary_id}"
            if name in idata.posterior:
                boundary_draws[boundary_id] = idata.posterior[name].values.flatten()

        for group in plan.span_groups:
            date_ids = [d for d in group.date_ids if d in posteriors]
            older_id, younger_id = group.older_boundary_id, group.younger_boundary_id
            older_marginal = older_id in plan.marginalized_boundaries
            younger_marginal = younger_id in plan.marginalized_boundaries
            if not (older_marginal or younger_marginal):
                continue
            if not date_ids:
                # Nothing between these boundaries to pin them to; leave any
                # marginalized one unreported rather than invent a value.
                continue

            member_draws = np.stack([posteriors[d] for d in date_ids])
            oldest = member_draws.max(axis=0)
            youngest = member_draws.min(axis=0)
            num_dates = len(date_ids)

            if older_marginal and younger_marginal:
                start, end = self._sample_boundaries(member_draws, window_lower, window_upper)
                boundary_draws[older_id] = start
                boundary_draws[younger_id] = end
            elif older_marginal:
                anchor = boundary_draws.get(younger_id)
                if anchor is None:
                    continue
                s = self._sample_power_law_distance(
                    oldest - anchor, np.full(num_draws, window_upper) - anchor,
                    num_dates, rng.random(num_draws))
                boundary_draws[older_id] = anchor + s
            else:
                anchor = boundary_draws.get(older_id)
                if anchor is None:
                    continue
                s = self._sample_power_law_distance(
                    anchor - youngest, anchor - np.full(num_draws, window_lower),
                    num_dates, rng.random(num_draws))
                boundary_draws[younger_id] = anchor - s

        boundary_results = {
            boundary_id: {
                'name': plan.boundary_names.get(boundary_id, 'Boundary'),
                'posterior': self._summarize_posterior(draws),
            }
            for boundary_id, draws in boundary_draws.items()
        }

        return {'measurements': measurement_results, 'boundaries': boundary_results}
