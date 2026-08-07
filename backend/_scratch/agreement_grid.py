"""Is the residual ~1.1 offset a grid/normalisation artefact of my reading?"""
import sys, csv
import numpy as np
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/tests')
import oxcal_fixture as ox

reported = {}
for run in sorted(ox.RUNS):
    with open(ox.paths(run)[2], encoding='utf-8-sig') as fh:
        for row in csv.reader(fh):
            if len(row) > 10 and row[0].startswith('R_Date'):
                reported[(run, ox.key(row[0].split(None, 1)[1]))] = float(row[10])

def variants(lc, lp, pc, pp):
    out = {}
    # (a) union grid at OxCal's own 5-year step
    lo, hi = min(lc.min(), pc.min()), max(lc.max(), pc.max())
    g5 = np.arange(lo, hi + 5.0, 5.0)
    o = np.interp(g5, lc, lp, left=0, right=0); m = np.interp(g5, pc, pp, left=0, right=0)
    out['union @5yr, both renormalised'] = (m/m.sum() * (o/o.sum())).sum() / ((o/o.sum())**2).sum()
    # (b) evaluate on the POSTERIOR's grid only -- outside it the posterior is 0
    o2 = np.interp(pc, lc, lp, left=0, right=0)
    out['posterior grid only'] = (pp/pp.sum() * (o2/o2.sum())).sum() / ((o2/o2.sum())**2).sum()
    # (c) union @5yr but normalise the likelihood over its OWN full export
    o3 = np.interp(g5, lc, lp, left=0, right=0)
    m3 = np.interp(g5, pc, pp, left=0, right=0)
    on = o3 / lp.sum(); mn = m3 / pp.sum()
    out['likelihood normalised on its own support'] = (mn*on).sum() / (on**2).sum()
    # (d) as (a) but 1-year, for reference
    g1 = np.arange(lo, hi + 1.0, 1.0)
    o4 = np.interp(g1, lc, lp, left=0, right=0); m4 = np.interp(g1, pc, pp, left=0, right=0)
    out['union @1yr'] = (m4/m4.sum() * (o4/o4.sum())).sum() / ((o4/o4.sum())**2).sum()
    return out

errs = None
for run in sorted(ox.RUNS):
    d = ox.distributions(run)
    for m in ox.measurements():
        n = m['id']
        a = reported.get((run, n))
        if a is None or 'likelihood' not in d[n]:
            continue
        v = variants(*d[n]['likelihood'], *d[n]['posterior'])
        if errs is None:
            errs = {k: [] for k in v}
        for k in v:
            errs[k].append(100 * v[k] - a)
for k, e in errs.items():
    e = np.array(e)
    print(f'  {k:>44}: mean {e.mean():7.2f}  rms {np.sqrt((e**2).mean()):6.2f}  max |{np.abs(e).max():5.2f}|')
