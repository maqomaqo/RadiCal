"""
Reader for tests/fixtures/oxcal_resolution/ -- OxCal 4.4 runs made to pin the
agreement indices, which OxCal computes at its calculation resolution:

  Hillside_sites_3_res1   tests/fixtures/oxcal's `ordered` model, no outlier
                          model, at Resolution=1
  Dune_Section_res1     tests/fixtures/oxcal_outlier's model (General
                          outlier model, prior 0.05 on all sixteen dates) at
                          Resolution=1
  DS_Layers             Dune Section layered sequence: ten
                          boundaries, an undated phase, General outlier model
                          at 0.05 on twelve dates -- at OxCal's default five
                          years, and reproducing the original run (Amodel
                          38.3 against 38.4, Aoverall 45.9)
  DS_Layers_res1        the same at Resolution=1

OxCal's own indices move with the resolution -- Hillside's Aoverall is 100.4
at five years and 105.4 at one -- and the one-year values are the ones
services/agreement.py is held to (see its module docstring).

Each run is a .oxcal file with ' Raw.csv' and ' Table.csv' exports beside it.
Dates and outlier priors are read from the .oxcal file itself so they cannot
drift from what OxCal ran.
"""
import csv
import os
import re

import numpy as np

from oxcal_fixture import ad_to_calbp

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'fixtures', 'oxcal_resolution')

HILLSIDE = 'Hillside_sites_3_res1'
DUNE = 'Dune_Section_res1'
DS = 'DS_Layers'
DS_ONE_YEAR = 'DS_Layers_res1'
ONE_YEAR_OUTLIER_RUNS = (DUNE, DS_ONE_YEAR)


def _path(stem, suffix):
    return os.path.join(FIXTURES, f'{stem}{suffix}')


def measurements(stem):
    """The run's dates as the app's measurement dicts, keyed by OxCal's names."""
    text = open(_path(stem, '.oxcal'), encoding='utf-8-sig').read()
    out = []
    for match in re.finditer(
            r'R_Date\("([^"]+)",\s*(\d+),\s*(\d+)\)\s*\{([^}]*)\}', text):
        name, age, error, body = match.groups()
        outlier = re.search(r'Outlier\("(\w+)",\s*([\d.]+)\)', body)
        out.append({
            'id': name,
            'sample_name': name,
            'age_bp': float(age),
            'error_1sigma': float(error),
            'curve_id': 'shcal20',
            'outlier_prior': float(outlier.group(2)) if outlier else None,
            'outlier_model': outlier.group(1).lower() if outlier else None,
        })
    return out


def table(stem):
    """
    ({name: (A, inlier percent or None)}, Amodel, Aoverall) from the results
    table. P is the INLIER probability (see dune_section_fixture).
    """
    per_date = {}
    with open(_path(stem, ' Table.csv'), encoding='utf-8-sig') as fh:
        for row in csv.reader(fh):
            if len(row) > 10 and row[0].startswith('R_Date'):
                name = row[0].split(None, 1)[1]
                inlier = float(row[12]) if len(row) > 12 and row[12].strip() else None
                per_date[name] = (float(row[10]), inlier)
    text = open(_path(stem, ' Table.csv'), encoding='utf-8-sig').read()
    return (per_date,
            float(re.search(r'Amodel ([\d.]+)', text).group(1)),
            float(re.search(r'Aoverall ([\d.]+)', text).group(1)))


def distributions(stem):
    """
    {name: {'likelihood': [[calbp, density], ...], 'posterior': [...]}} for the
    dates, ascending in cal BP, plus the outlier scale's posterior under the key
    'U' as (log10 years, weights) where the run has an outlier model.
    """
    grouped, scale = {}, []
    with open(_path(stem, ' Raw.csv'), encoding='utf-8-sig') as fh:
        for row in csv.DictReader(fh):
            if row['op'] == 'U' and row['type'] == 'posterior':
                scale.append((float(row['value']), float(row['probability'])))
            elif row['op'] == 'R_Date':
                grouped.setdefault(row['name'], {}).setdefault(row['type'], []).append(
                    (float(row['value']), float(row['probability'])))
    out = {}
    for name, kinds in grouped.items():
        out[name] = {}
        for kind, pairs in kinds.items():
            pairs.sort()
            calbp = ad_to_calbp([p[0] for p in pairs])[::-1]
            prob = np.array([p[1] for p in pairs])[::-1]
            out[name][kind] = np.column_stack([calbp, prob])
    if scale:
        scale.sort()
        weights = np.array([w for _u, w in scale])
        out['U'] = (np.array([u for u, _w in scale]), weights / weights.sum())
    return out
