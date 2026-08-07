"""
Work out OxCal's agreement index from its own exports.

For every date in all three runs we hold OxCal's unmodelled likelihood, its
modelled posterior, and the agreement index it reported. That is 24 known
answers -- enough to identify the formula rather than paraphrase it.
"""
import sys
import numpy as np
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/tests')
import oxcal_fixture as ox

rows = []
for run in sorted(ox.RUNS):
    d, t = ox.distributions(run), ox.table_ranges(run)
    for m in ox.measurements():
        name = m['id']
        if 'likelihood' not in d[name]:
            continue
        # OxCal exports the two on different grids; put them on a shared one.
        lc, lp = d[name]['likelihood']
        pc, pp = d[name]['posterior']
        lo, hi = min(lc.min(), pc.min()), max(lc.max(), pc.max())
        grid = np.arange(lo, hi + 1.0, 1.0)
        obs = np.interp(grid, lc, lp, left=0, right=0)
        mod = np.interp(grid, pc, pp, left=0, right=0)
        obs = obs / obs.sum()
        mod = mod / mod.sum()
        # OxCal's own reported value, from the table's "A" column.
        rows.append((run, name, grid, obs, mod))

# The A column is the 10th field; table_ranges doesn't keep it, so re-read.
import csv, re
reported = {}
for run in sorted(ox.RUNS):
    with open(ox.paths(run)[2], encoding='utf-8-sig') as fh:
        for row in csv.reader(fh):
            if len(row) > 10 and row[0].startswith('R_Date'):
                reported[(run, ox.key(row[0].split(None, 1)[1]))] = float(row[10])

def candidates(obs, mod):
    return {
        'sum(mod*obs)/sum(obs^2)': (mod * obs).sum() / (obs ** 2).sum(),
        'sum(mod*obs)/sum(mod^2)': (mod * obs).sum() / (mod ** 2).sum(),
        'cosine': (mod * obs).sum() / np.sqrt((obs ** 2).sum() * (mod ** 2).sum()),
        'overlap sum(min)': np.minimum(mod, obs).sum(),
        'sqrt of first': np.sqrt((mod * obs).sum() / (obs ** 2).sum()),
    }

names = list(candidates(rows[0][3], rows[0][4]))
errs = {n: [] for n in names}
print(f"{'run':>16} {'date':>10} {'OxCal A':>8} | " + ' '.join(f'{n:>24}' for n in names))
for run, name, grid, obs, mod in rows:
    a = reported.get((run, name))
    if a is None:
        continue
    c = candidates(obs, mod)
    for n in names:
        errs[n].append(100 * c[n] - a)
    print(f'{run:>16} {name:>10} {a:8.1f} | ' + ' '.join(f'{100*c[n]:24.1f}' for n in names))
print()
for n in names:
    e = np.array(errs[n])
    print(f'  {n:>26}: mean error {e.mean():8.2f}, rms {np.sqrt((e**2).mean()):8.2f}')
