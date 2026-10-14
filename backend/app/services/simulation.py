"""
Scenario simulation: dates drawn from a known truth, for testing what a model
does with them.

A simulation takes an existing analysis as its template -- its model, and its
dates' errors, curves, reservoir offsets and inbuilt-age allowances as the
sampling plan -- and a TRUTH: a window of calendar years for each Phase. For
each replicate every date is redrawn:

    event    uniform in its Phase's window
    sample   the event plus the material's inbuilt age (older)
    age      the curve's radiocarbon age at the sample, plus the date's ΔR,
             plus laboratory scatter at the date's own error

r0 is the expected case: each event at its window's centre, no scatter, and
the inbuilt age its expected value. r1..rN draw from a stored seed, so a
simulation regenerates exactly, and two simulations with the same seed and
plan share their draws -- a comparison between truths then differs in the
truth alone. The method is the test regime's own (generate_tests.py), which
draws the curve's mean and scatters at the laboratory error only.

Pure: no database.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

from app.services.calibration import get_curve

# Draws come from three streams per replicate, so changing one thing about a
# plan (a window, say) leaves the others' draws as they were.
_POSITION, _SCATTER, _INBUILT = 0, 1, 2


def _children(element: Dict) -> List[Dict]:
    return list(element.get('children') or [])


def _median(posterior: Optional[Dict]) -> Optional[float]:
    value = (posterior or {}).get('posterior_median')
    return None if value is None else float(value)


def default_windows(tree: Optional[Dict], measurements: Sequence[Dict],
                    date_medians: Dict[str, float]) -> List[Dict]:
    """
    One window per Phase that holds dates directly, per empty Phase and per
    Hiatus in a Sequence, as [{'element_id', 'name', 'kind', 'old_bp',
    'young_bp'}], oldest first where a Sequence orders them. `kind` is
    'phase', 'empty' or 'hiatus'.

    Each takes the posterior medians of the Boundaries either side of it in
    its Sequence. Where it has none, it spans its own dates' modelled
    medians. A model-less analysis (a group calibration) has one window,
    element_id None, over its dates' medians.
    """
    if tree is None:
        values = [date_medians[m['id']] for m in measurements if m['id'] in date_medians]
        if not values:
            return []
        return [{'element_id': None, 'name': 'All dates', 'kind': 'phase',
                 'old_bp': max(values), 'young_bp': min(values)}]

    dated: Dict[str, List[float]] = {}
    for m in measurements:
        if m.get('element_id') and m['id'] in date_medians:
            dated.setdefault(m['element_id'], []).append(date_medians[m['id']])

    windows: List[Dict] = []

    def visit(element: Dict) -> None:
        children = _children(element)
        for index, child in enumerate(children):
            kind = child.get('element_type')
            leaf_phase = kind == 'phase' and not _children(child)
            if leaf_phase or (kind == 'hiatus' and element.get('element_type') == 'sequence'):
                old = young = None
                if element.get('element_type') == 'sequence':
                    for sibling in reversed(children[:index]):
                        if sibling.get('element_type') == 'boundary':
                            old = _median(sibling.get('posterior'))
                            break
                    for sibling in children[index + 1:]:
                        if sibling.get('element_type') == 'boundary':
                            young = _median(sibling.get('posterior'))
                            break
                own = dated.get(child['id'])
                if (old is None or young is None) and own:
                    old = max(own) if old is None else old
                    young = min(own) if young is None else young
                if old is None or young is None:
                    continue
                windows.append({
                    'element_id': child['id'],
                    'name': child.get('name') or ('Hiatus' if kind == 'hiatus' else 'Phase'),
                    'kind': 'hiatus' if kind == 'hiatus' else ('phase' if own else 'empty'),
                    'old_bp': float(max(old, young)),
                    'young_bp': float(min(old, young)),
                })
            visit(child)

    visit(tree)
    return windows


def close_gaps(tree: Optional[Dict], windows: Sequence[Dict]) -> List[Dict]:
    """
    `windows` with every empty Phase and Hiatus given zero length, and the
    Phases after it in its Sequence moved older to close the space: the
    truth of a site with no gaps. Each dated Phase keeps its own length; a
    Sequence starts where its oldest window did.
    """
    by_id = {w['element_id']: dict(w) for w in windows}
    if tree is None:
        return list(by_id.values())

    def visit(element: Dict) -> None:
        children = _children(element)
        if element.get('element_type') == 'sequence':
            ordered = [by_id[c['id']] for c in children if c['id'] in by_id]
            if ordered:
                cursor = ordered[0]['old_bp']
                for window in ordered:
                    width = 0.0 if window['kind'] in ('empty', 'hiatus') else (
                        window['old_bp'] - window['young_bp'])
                    window['old_bp'], window['young_bp'] = cursor, cursor - width
                    cursor -= width
        for child in children:
            visit(child)

    visit(tree)
    return [by_id[w['element_id']] for w in windows]


def _expected_inbuilt(m: Dict) -> float:
    years = float(m.get('inbuilt_years') or 0.0)
    if years <= 0:
        return 0.0
    return years if (m.get('inbuilt_shape') or 'uniform') == 'exponential' else years / 2.0


def simulate(measurements: Sequence[Dict], windows: Sequence[Dict], seed: int,
             replicate: int, scatter: bool = True, inbuilt: bool = True) -> List[Dict]:
    """
    One replicate's dates, in `measurements`' order, as the truth key rows:

        {'date_id', 'sample_name', 'element_id', 'window_old_bp',
         'window_young_bp', 'event_bp', 'inbuilt_years', 'sample_bp',
         'curve_bp', 'delta_r', 'z', 'error', 'age_bp'}

    `age_bp` is what the replicate's date reads, rounded to a whole year as a
    laboratory reports it. Replicate 0 is the expected case (module
    docstring). With `scatter` off every replicate reads the curve exactly;
    with `inbuilt` off no date carries inbuilt age.
    """
    by_element = {w['element_id']: w for w in windows}
    position = np.random.default_rng([seed, replicate, _POSITION])
    noise = np.random.default_rng([seed, replicate, _SCATTER])
    offsets = np.random.default_rng([seed, replicate, _INBUILT])
    rows = []
    for m in measurements:
        window = by_element.get(m.get('element_id'))
        if window is None:
            window = by_element.get(None)
        if window is None:
            raise ValueError(f"sample '{m.get('sample_name', m['id'])}' has no truth window")
        # Every draw is taken whether or not it is used, so the streams stay
        # aligned date by date whatever the settings.
        u, z = float(position.random()), float(noise.standard_normal())
        unit_offset = float(offsets.random())
        if replicate == 0:
            u, z = 0.5, 0.0
        if not scatter:
            z = 0.0
        years = float(m.get('inbuilt_years') or 0.0)
        if not inbuilt or years <= 0:
            offset = 0.0
        elif replicate == 0:
            offset = _expected_inbuilt(m)
        elif (m.get('inbuilt_shape') or 'uniform') == 'exponential':
            offset = -years * float(np.log1p(-unit_offset))
        else:
            offset = years * unit_offset
        old, young = window['old_bp'], window['young_bp']
        event = old - u * (old - young)
        sample = event + offset
        c14, _error = get_curve(m.get('curve_id') or 'intcal20').interpolate(np.array([sample]))
        curve_bp = float(np.asarray(c14).ravel()[0])
        delta_r = float(m.get('delta_r') or 0.0)
        error = float(m['error_1sigma'])
        rows.append({
            'date_id': m['id'],
            'sample_name': m.get('sample_name'),
            'element_id': m.get('element_id'),
            'window_old_bp': old,
            'window_young_bp': young,
            'event_bp': round(event, 2),
            'inbuilt_years': round(offset, 2),
            'sample_bp': round(sample, 2),
            'curve_bp': round(curve_bp, 2),
            'delta_r': delta_r,
            'z': round(z, 4),
            'error': error,
            'age_bp': int(np.floor(curve_bp + delta_r + error * z + 0.5)),
        })
    return rows
