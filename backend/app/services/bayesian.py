import pymc as pm
import pytensor.tensor as pt
import numpy as np
from typing import List, Dict, Optional, Tuple

from app.services.calibration import get_curve, CalibrationCurve, hpd_ranges_from_distribution
from app.services.stratigraphy import expand_relationships, effective_bounds


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

        with pm.Model() as model:
            # Create calendar age variables for each sample, bounded to the
            # range actually covered by that sample's calibration curve
            # (rather than an arbitrary fixed 0-50000 window) so the sampler
            # never explores/initializes ages the curve can't inform --
            # further tightened by any TPQ/TAQ limiter on the sample itself
            # or its group.
            ages = {}
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

                initval = float(np.clip(m['age_bp'], lower, upper))
                ages[m['id']] = pm.Uniform(
                    f"age_{m['id']}",
                    lower=lower,
                    upper=upper,
                    initval=initval
                )

            # Stratigraphic constraints using Potential. Group-to-group and
            # group-to-sample relationships are expanded to plain
            # sample-to-sample pairs (and deduplicated) here, so e.g. two
            # relationships that both imply "A is above B" don't create two
            # PyMC variables with the same name.
            older_younger, equal_pairs = expand_relationships(measurements, groups, relationships)

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
        """Run MCMC sampling"""
        with model:
            idata = pm.sample(
                draws=self.samples,
                tune=self.tune,
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
            hpd_68_ranges = hpd_ranges_from_distribution(bin_centers, counts, 0.68)
            hpd_95_ranges = hpd_ranges_from_distribution(bin_centers, counts, 0.95)
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
