"""
Influence analysis: how far one date moves a model's answer.

A conclusion can rest on one date, and finding which by hand is a series of
edited copies run one at a time. This plans those copies -- each date left
out, moved to the neighbouring Phase older and younger, or treated as an
outlier -- and reduces each one's result to what the comparison needs. The
runs themselves are api/influence.py's, through the same solver path as any
analysis.

Each variant keeps every Boundary and duration (median, 95% range and a
compact distribution), so the question -- which date moves the Start's
median most, which the occupation's length, which P(Start before AD 1360) --
is chosen after the run, not before it.

Pure: no database, no HTTP.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

KINDS = ('leave_out', 'move', 'outlier')
# The outlier model a date treated as an outlier takes when it has none:
# OxCal's General, which says nothing about which way the date is wrong.
DEFAULT_OUTLIER_MODEL = 'general'
# Points a stored distribution is reduced to. Enough for a probability before
# a year to within about 0.002 on the test models, and small enough that a
# report of fifty variants stays well under a megabyte.
DISTRIBUTION_POINTS = 120


def _walk(element: Dict, parent: Optional[Dict], out: Dict[str, Dict]) -> None:
    out[element['id']] = {'element': element, 'parent': parent}
    for child in element.get('children') or []:
        _walk(child, element, out)


def _is_leaf_phase(element: Dict) -> bool:
    return element.get('element_type') == 'phase' and not element.get('children')


def neighbouring_phases(tree: Dict, phase_id: str) -> Dict[str, Optional[Dict]]:
    """
    The Phases either side of `phase_id` in the Sequence that holds it, as
    {'older': element or None, 'younger': element or None}: the nearest sibling
    Phase that holds dates directly, across any Boundary or Hiatus between.
    None where the Phase is not in a Sequence, or has no such neighbour.
    """
    index: Dict[str, Dict] = {}
    _walk(tree, None, index)
    found = index.get(phase_id)
    out: Dict[str, Optional[Dict]] = {'older': None, 'younger': None}
    if found is None or found['parent'] is None:
        return out
    parent = found['parent']
    if parent.get('element_type') != 'sequence':
        return out
    siblings = parent.get('children') or []
    position = next(i for i, child in enumerate(siblings) if child['id'] == phase_id)
    # A Sequence's children run oldest first (model_tree).
    for child in reversed(siblings[:position]):
        if child.get('element_type') in ('boundary', 'hiatus'):
            continue
        out['older'] = child if _is_leaf_phase(child) else None
        break
    for child in siblings[position + 1:]:
        if child.get('element_type') in ('boundary', 'hiatus'):
            continue
        out['younger'] = child if _is_leaf_phase(child) else None
        break
    return out


def plan(measurements: Sequence[Dict], tree: Dict, kinds: Sequence[str]) -> List[Dict]:
    """
    Every variant `kinds` asks for, in date order:

        {'id', 'kind', 'date_id', 'date_name', 'label',
         and for a move: 'to_phase_id', 'to_phase_name', 'direction'}

    Only dates in a plain Phase are varied. A member of a wiggle match or a
    deposition sequence is part of one fitted curve, and leaving it out or
    moving it is a different kind of question.
    """
    index: Dict[str, Dict] = {}
    _walk(tree, None, index)
    variants: List[Dict] = []
    for m in measurements:
        holder = index.get(m.get('element_id') or '')
        if holder is None or not _is_leaf_phase(holder['element']):
            continue
        name = m.get('sample_name') or m['id']
        if 'leave_out' in kinds:
            variants.append({'id': f"leave_out:{m['id']}", 'kind': 'leave_out',
                             'date_id': m['id'], 'date_name': name,
                             'label': f'{name} left out'})
        if 'move' in kinds:
            for direction, phase in neighbouring_phases(tree, m['element_id']).items():
                if phase is None:
                    continue
                phase_name = phase.get('name') or 'the next Phase'
                variants.append({
                    'id': f"move_{direction}:{m['id']}", 'kind': 'move',
                    'date_id': m['id'], 'date_name': name,
                    'to_phase_id': phase['id'], 'to_phase_name': phase_name,
                    'direction': direction,
                    'label': f'{name} moved to {phase_name} ({direction})'})
        if 'outlier' in kinds and (m.get('outlier_prior') or 0) < 1:
            variants.append({'id': f"outlier:{m['id']}", 'kind': 'outlier',
                             'date_id': m['id'], 'date_name': name,
                             'label': f'{name} as an outlier'})
    return variants


def apply(variant: Dict, measurements: Sequence[Dict]) -> List[Dict]:
    """The measurements with `variant` applied; the input is left alone."""
    out = []
    for m in measurements:
        if m['id'] != variant['date_id']:
            out.append(m)
        elif variant['kind'] == 'leave_out':
            continue
        elif variant['kind'] == 'move':
            out.append({**m, 'element_id': variant['to_phase_id']})
        elif variant['kind'] == 'outlier':
            out.append({**m, 'outlier_prior': 1.0,
                        'outlier_model': m.get('outlier_model') or DEFAULT_OUTLIER_MODEL})
        else:
            raise ValueError(f"unknown variant kind '{variant['kind']}'")
    return out


def compact_distribution(distribution: Optional[Sequence[Sequence[float]]],
                         points: int = DISTRIBUTION_POINTS) -> Optional[List[List[float]]]:
    """
    A [x, density] series resampled onto at most `points` evenly spaced x,
    over the span it has mass. statistics.p_before reads it as it reads the
    full series.
    """
    if not distribution or len(distribution) < 2:
        return None
    series = np.asarray(distribution, dtype=float)
    series = series[np.argsort(series[:, 0])]
    if len(series) <= points:
        return [[float(x), float(y)] for x, y in series]
    xs = np.linspace(series[0, 0], series[-1, 0], points)
    ys = np.interp(xs, series[:, 0], series[:, 1])
    return [[round(float(x), 3), float(y)] for x, y in zip(xs, ys)]


def _outer(hpd) -> Optional[List[float]]:
    if not hpd or len(hpd) < 2 or hpd[0] is None or hpd[1] is None:
        return None
    lo, hi = float(hpd[0]), float(hpd[1])
    return [min(lo, hi), max(lo, hi)]


def summarise(boundary_results: Dict, interval_block: Optional[Dict]) -> Dict:
    """
    One run reduced to what an influence table reads:

        {'boundaries': {id: {'name', 'median', 'hpd_95', 'distribution'}},
         'durations':  {key: {'name', 'kind', 'median', 'hpd_95', 'distribution'}}}

    Boundaries in cal BP, durations in years. A duration's key is its kind and
    element, so the same duration is matched across variants.
    """
    boundaries = {}
    for bid, found in (boundary_results or {}).items():
        posterior = (found or {}).get('posterior') or {}
        if posterior.get('posterior_median') is None:
            continue
        boundaries[bid] = {
            'name': found.get('name'),
            'median': float(posterior['posterior_median']),
            'hpd_95': _outer(posterior.get('hpd_95')),
            'distribution': compact_distribution(posterior.get('probability_distribution')),
        }
    durations = {}
    for row in (interval_block or {}).get('durations') or []:
        years = row.get('years') or {}
        if years.get('median') is None:
            continue
        key = f"{row.get('kind')}:{row.get('element_id') or row.get('id')}"
        durations[key] = {
            'name': row.get('name'),
            'kind': row.get('kind'),
            'median': float(years['median']),
            'hpd_95': _outer(years.get('hpd_95')),
            'distribution': compact_distribution(years.get('distribution')),
        }
    return {'boundaries': boundaries, 'durations': durations}
