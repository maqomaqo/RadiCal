"""
Statistics computed from the measurements and finished results of an
analysis, with no solver involved. Everything here is cheap and pure, so it is
computed on read: an analysis saved before a statistic existed reports it
without being re-run.

The same-event test
-------------------
Whether a set of radiocarbon measurements could all date one event (Ward and
Wilson 1978; OxCal's R_Combine). Pool the ages with inverse-variance weights,

    x = sum(x_i / s_i^2) / sum(1 / s_i^2),     s_x = (sum(1 / s_i^2))^(-1/2)

and test their scatter about that pool:

    T = sum((x_i - x)^2 / s_i^2)  ~  chi^2 with n - 1 degrees of freedom

The dates are consistent with one event when T is under the chi^2 critical
value at 5%, which is how OxCal reports it ("T=5.7(5% 14.1)").

The test runs on conventional radiocarbon ages, so it needs every date to
share one curve and one reservoir offset. Dates on different curves are not
measuring the same thing, and different Delta R values would shift some ages
against others. A Delta R shared by every date shifts them all alike, which
leaves T unchanged; it is applied once, when the pooled age is calibrated.
Adding its error to each date before pooling instead would count one
uncertainty n times and understate T.
"""
from typing import Dict, List, Mapping, Optional, Sequence

import numpy as np
from scipy.stats import chi2

from app.services.calibration import REPORTING_CONFIDENCE_LEVELS, get_curve
from app.services.intervals import plan_durations

# Significance level the test is reported at -- OxCal's, and Ward and Wilson's.
SAME_EVENT_LEVEL = 0.05


def _curve_key(m: Mapping):
    return m.get('curve_id')


def _reservoir_key(m: Mapping):
    return (m.get('delta_r') or 0.0, m.get('delta_r_error') or 0.0)


def same_event(measurements: Sequence[Mapping]) -> Optional[Dict]:
    """
    The Ward and Wilson test over `measurements` (dicts carrying age_bp,
    error_1sigma, curve_id, delta_r, delta_r_error), or None for fewer than
    two dates.

    Dates that cannot be pooled return the count and a `refused` reason with
    every statistic None, so a caller can say why there is no test.
    """
    if len(measurements) < 2:
        return None
    n = len(measurements)
    base = {'n': n, 'df': n - 1, 'level': SAME_EVENT_LEVEL,
            'mean_bp': None, 'error_bp': None, 't': None, 'critical': None,
            'consistent': None, 'pooled_hpd_ranges': None, 'refused': None}

    if len({_curve_key(m) for m in measurements}) > 1:
        return {**base, 'refused': 'Dates are on more than one curve.'}
    if len({_reservoir_key(m) for m in measurements}) > 1:
        return {**base, 'refused': 'Dates have different ΔR values.'}

    ages = np.array([float(m['age_bp']) for m in measurements])
    errors = np.array([float(m['error_1sigma']) for m in measurements])
    if np.any(errors <= 0):
        return {**base, 'refused': 'A date has no measurement error.'}

    weights = 1.0 / errors ** 2
    mean = float(np.sum(ages * weights) / np.sum(weights))
    error = float(np.sum(weights) ** -0.5)
    t = float(np.sum((ages - mean) ** 2 * weights))
    critical = float(chi2.ppf(1.0 - SAME_EVENT_LEVEL, n - 1))
    consistent = t < critical

    return {
        **base,
        'mean_bp': mean,
        'error_bp': error,
        't': t,
        'critical': critical,
        'consistent': consistent,
        # The pooled age calibrated, only where pooling is justified: a pool of
        # dates from more than one event is a date of nothing.
        'pooled_hpd_ranges': _pooled_ranges(measurements[0], mean, error) if consistent else None,
    }


def _pooled_ranges(reference: Mapping, mean: float, error: float) -> Optional[List[Dict]]:
    """[{confidence, ranges}] for the pooled age, at each level the results
    page offers -- the shape of a Bayesian date's unmodelled_hpd_ranges."""
    try:
        curve = get_curve(reference['curve_id'])
        cal_ages, probs = curve.calibrate(
            mean, error, reference.get('delta_r'), reference.get('delta_r_error'))
    except Exception:                            # noqa: BLE001 - the test stands without it
        return None
    return [{'confidence': level, 'ranges': curve.get_hpd_ranges(cal_ages, probs, level)}
            for level in REPORTING_CONFIDENCE_LEVELS]


def same_event_report(analysis_type: str, model_tree: Optional[Dict],
                      measurements: List[Dict]) -> List[Dict]:
    """
    Every same-event test an analysis reports, each carrying the element it
    is about: one over the whole set for a group calibration (element_id
    None), and one per Phase holding two or more dates for a Bayesian model.
    The phases are the ones services/intervals.py reports a dated-events
    duration for and services/kde.py a KDE for, so every section of the page
    names the same groups. A single calibration has nothing to test.
    """
    if analysis_type == 'group_calibration':
        test = same_event(measurements)
        return [{'element_id': None, 'name': None, **test}] if test else []
    if analysis_type != 'bayesian' or not model_tree:
        return []

    by_id = {m['id']: m for m in measurements}
    report: List[Dict] = []
    for spec in plan_durations(model_tree, measurements):
        if spec['kind'] != 'sample_duration':
            continue
        dates = [by_id[d] for d in spec['dates'] if d in by_id]
        test = same_event(dates)
        if test:
            report.append({'element_id': spec['element_id'], 'name': spec['name'], **test})
    return report


def verdict(test: Mapping) -> str:
    """The result in words, as the Results page states it, or why there is
    none."""
    if test.get('refused') or test.get('consistent') is None:
        return test.get('refused') or ''
    return ('Consistent with one event' if test['consistent']
            else 'More scatter than one event allows')


def pooled_ranges_at(test: Mapping, confidence: float) -> Optional[List[Dict]]:
    """The pooled age's calibrated ranges at one reported level, or None."""
    for entry in test.get('pooled_hpd_ranges') or []:
        if abs(entry['confidence'] - confidence) < 1e-9:
            return entry['ranges']
    return None


_SAMPLE_FIELDS = ('id', 'age_bp', 'error_1sigma', 'curve_id', 'delta_r',
                  'delta_r_error', 'element_id')


def same_event_for_samples(analysis_type: str, model_tree: Optional[Dict],
                           samples) -> List[Dict]:
    """same_event_report over stored RadiocarbonSample rows -- the one call the
    results page and the JSON export share, so the two cannot disagree."""
    measurements = [{f: getattr(s, f) for f in _SAMPLE_FIELDS} for s in samples]
    return same_event_report(analysis_type, model_tree, measurements)
