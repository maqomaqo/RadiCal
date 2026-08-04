import pymc as pm
import pytensor.tensor as pt
import numpy as np
from collections import defaultdict, deque
from typing import List, Dict, Optional, Tuple

from app.services.calibration import get_curve, CalibrationCurve, hpd_ranges_from_distribution
from app.services.stratigraphy import expand_relationships, effective_bounds


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

        Callers are expected to have already validated that `relationships`
        contains no stratigraphic cycle (BayesianRequest does this before
        the request ever reaches here); this function does not re-check.
        """
        groups = groups or []
        groups_by_id = {g['id']: g for g in groups}

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

            lower = max(curve_lower, taq) if taq is not None else curve_lower
            upper = min(curve_upper, tpq) if tpq is not None else curve_upper
            if lower >= upper:
                raise ValueError(
                    f"Sample '{m.get('sample_name', m['id'])}' has no valid age range: "
                    f"its TPQ/TAQ limiters ([{taq}, {tpq}]) don't overlap the "
                    f"calibration curve's range ([{curve_lower}, {curve_upper}])"
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

            for older_id, younger_id in older_younger:
                if older_id not in ages or younger_id not in ages:
                    continue
                pm.Potential(
                    f"constraint_{older_id}_{younger_id}",
                    pm.math.switch(ages[older_id] > ages[younger_id], 0, -np.inf)
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

    def extract_results(self, idata, ages: Dict):
        """Extract and summarize posterior distributions"""
        results = {}

        for age_id, age_var in ages.items():
            posterior = idata.posterior[age_var.name].values.flatten()

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

            # HPD ranges from the binned posterior, using the same
            # multi-range logic as the direct-calibration path
            # (CalibrationCurve.get_hpd_ranges): MCMC posteriors for
            # radiocarbon dates can be just as multimodal as the raw
            # calibration likelihood, since the same curve plateaus/wiggles
            # drive both. A single min/max span (what az.hdi's default,
            # unimodal HDI returns) would misrepresent a two-peaked
            # posterior as one mostly-empty contiguous range.
            #
            # hpd_ranges_from_distribution reports each selected segment's
            # boundaries as ages[s]/ages[e] -- correct when `ages` are
            # themselves the discrete supported points of the distribution
            # (true for the direct-calibration path, where they're the
            # curve's own grid points), but bin_centers here are an
            # artifact of histogramming a continuous posterior: using them
            # as boundaries silently shrinks every range by a full bin
            # width (half lost on each side), and collapses any single-bin
            # segment -- common for a spread-out or multimodal posterior
            # sampled into only 100 bins -- to a reported width of exactly
            # zero. Expanding each returned range by half a bin width
            # restores the actual continuous span the bins approximate.
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

            results[age_id] = {
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

        return results
