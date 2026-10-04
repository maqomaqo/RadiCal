"""
A direct calculation of a sampled model, to check services/gibbs.py against.

Why a sampled answer needs one
------------------------------
The sampler's own diagnostics are both comparisons of the sampler with itself:
the first half of a run against the second, and (with several chains) one
chain against another. Neither can see a calibration lobe that no chain is in,
and neither can see a target that is wrong in the same way for every chain.
A site model of parallel sequences had both: boundaries allowed to share a grid cell
packed every chain into a late lobe holding under 1% of the posterior, and
four chains agreed on it at R-hat 1.001. Only a calculation that does not
sample can catch that, so this module makes one wherever it is cheap enough
and the results page reports whether the two agree.

Shapes
------
Two, which between them cover what the sampler is most often given:

  * A CHAIN that exact_chain.layout does not recognise because it is written
    with nested Sequences -- one area then another, a Sequence of two Sequences.
    Spelled out in place it is one line of Boundaries and Phases.

  * PARALLEL CHAINS inside an outer pair: a Sequence of Boundary S, a Phase
    whose children are Sequences that are each a chain, and Boundary E -- a
    site's Start and End around several areas. Given S and E the branches are
    independent, so

        p(S, E)  proportional to  C(S, E) T0(S, E) prod_b Z_b(S, E)

    where C is the span correction over the outer pair, T0 the span factor and
    likelihoods of any dates sitting directly in the Phase, and Z_b(S, E) the
    integral of branch b with its first Boundary strictly younger than S and
    its last strictly older than E. Z_b is a cumulative sum of the branch's
    end-to-end transfer product, so the whole thing is a few matrix products
    per branch. Each branch's interior is then exact_chain's own chain with an
    end-to-end factor W_b in place of its span correction.

Anything else is reported as not checked, with the reason.

The same model, not a neighbour
-------------------------------
A cross-check against a different model reports the difference between the
models, which is not what anyone wants to read. So nothing that defines the
target is written again here: the likelihood at every outlier scale, the
Boundaries' windows and the span-correction rows all come from a gibbs.Model
built on this module's grid, and the interval integrals are exact_chain's,
which the sampler's endpoint weighting is pinned to.

The one thing exact_chain cannot do -- an outlier model's SHARED offset scale,
which does not factorise over dates -- is handled by conditioning on it. Given
every outlier model's scale the model is an ordinary one, so each combination
of scale nodes is integrated and the results mixed by their evidence under the
uniform prior the sampler draws the scale from. Two outlier models of 24 nodes
each is 576 combinations; the evidence alone is cheap for all of them, and the
marginals are formed only for the combinations that hold almost all of it.

What is compared
----------------
The Boundaries. A group in the wrong lobe takes its Boundaries with it, and a
Boundary's marginal is what the transfer products give without integrating
any date back out. Each is compared on REPORTING_STEP_YEARS bins by total
variation distance, which reads directly as the probability mass in the wrong
place.
"""
import copy
import itertools
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.services import exact_chain, gibbs
from app.services.exact_chain import REPORTING_STEP_YEARS, _log_interval_integral
from app.services.model_tree import inherited_bounds

# Total variation distance between a sampled Boundary's marginal and the direct
# one above which the two are reported as differing. Agreeing runs on the
# test models sit under 0.08 -- Monte Carlo noise plus the grid -- and
# the lobe failure this exists for sat at 0.6 to 1.0.
DISAGREEMENT = 0.15
# Most cells on the direct calculation's grid. The cost is a few M x M matrix
# products per group per scale combination, so it goes as M^3.
MAX_CELLS = 800
# Room left beyond the dates' calibrated ranges for the outermost Boundaries,
# in years, and as a fraction of the dates' own spread when that is larger.
PAD_YEARS = 300.0
# Most combinations of outlier-scale nodes the evidence is computed over; a
# model past it is not checked.
MAX_SCALE_COMBINATIONS = 1024
# The marginals are formed for the most probable combinations until they hold
# this much of the evidence, and for no more than MAX_MARGINAL_COMBINATIONS.
SCALE_MASS = 0.995
MAX_MARGINAL_COMBINATIONS = 48
# Posterior mass a Boundary may have in the outermost cells of the grid before
# the grid is judged to have cut it off.
EDGE_MASS = 1e-3


class NotChecked(Exception):
    """The model is not one this module can integrate; the message says why."""


def _flattened(element: Dict) -> Dict:
    """A copy of `element` with every nested Sequence spelled out in place."""
    out = copy.copy(element)
    children = []
    for child in element.get('children') or []:
        if child.get('element_type') == 'sequence':
            children.extend(_flattened(child)['children'])
        else:
            children.append(child)
    out['children'] = children
    return out


def _ids(element: Dict, found: Optional[set] = None) -> set:
    found = set() if found is None else found
    found.add(element['id'])
    for child in element.get('children') or []:
        _ids(child, found)
    return found


def shape(root: Dict, measurements: List[Dict]
          ) -> Tuple[Optional[Tuple[str, str, List[str]]], List[exact_chain.Chain]]:
    """
    (outer pair, branches): the outer pair as (S, E, dates directly between
    them), or None for a single chain; the branches as exact_chain Chains.
    Raises NotChecked for any other shape.
    """
    if root.get('element_type') != 'sequence':
        raise NotChecked('the model is not a Sequence')
    flat = _flattened(root)
    chain = exact_chain.layout(flat, measurements)
    if chain is not None:
        return None, [chain]

    children = flat['children']
    kinds = [c.get('element_type') for c in children]
    if kinds != ['boundary', 'phase', 'boundary']:
        raise NotChecked('the model is neither one chain nor parallel chains '
                         'between two Boundaries')
    outer_phase = children[1]
    branches = []
    covered = {outer_phase['id']}
    for child in outer_phase.get('children') or []:
        if child.get('element_type') != 'sequence':
            raise NotChecked(f"'{child.get('name')}' inside "
                             f"'{outer_phase.get('name')}' is not a Sequence")
        inside = _ids(child)
        members = [m for m in measurements if m.get('element_id') in inside]
        branch = exact_chain.layout(_flattened(child), members)
        if branch is None:
            raise NotChecked(f"'{child.get('name')}' is not a chain")
        branches.append(branch)
        covered |= inside
    if not branches:
        raise NotChecked('the outer Phase holds no Sequences')
    stray = [m for m in measurements if m.get('element_id') not in covered]
    if stray:
        raise NotChecked('some dates sit outside every branch')
    loose = [m['id'] for m in measurements if m.get('element_id') == outer_phase['id']]
    return (children[0]['id'], children[2]['id'], loose), branches


def _grid(measurements: List[Dict], root: Dict) -> np.ndarray:
    """
    The direct calculation's grid: the sampler's own step where that fits in
    MAX_CELLS, over the dates' calibrated ranges plus room for the Boundaries.

    Narrower than the sampler's, which runs out to wherever an outlier model's
    broad component reaches. Dates are contained by their Boundaries, so no
    date needs the far tail, and a Boundary that does is caught by EDGE_MASS.
    """
    element_bounds = inherited_bounds(root)
    base = exact_chain._build_grid(measurements, element_bounds, 1)
    likelihood, _cumulative, outlier_parts = exact_chain.date_tables(
        base, measurements, element_bounds)
    lows, highs = [], []
    for date_id, row in likelihood.items():
        inlier = outlier_parts[date_id][0] if date_id in outlier_parts else row
        cdf = np.cumsum(inlier)
        if cdf[-1] <= 0:
            continue
        cdf = cdf / cdf[-1]
        lows.append(base[np.searchsorted(cdf, 1e-4)])
        highs.append(base[min(len(base) - 1, np.searchsorted(cdf, 1 - 1e-4))])
    if not lows:
        raise NotChecked('no date has a likelihood on the grid')
    spread = max(highs) - min(lows)
    pad = max(PAD_YEARS, spread)
    low = max(float(base[0]), min(lows) - pad)
    high = min(float(base[-1]), max(highs) + pad)
    base_step = float(base[1] - base[0])
    step = base_step * max(1, int(np.ceil((high - low) / MAX_CELLS / base_step)))
    return np.arange(low, high + step / 2, step)


class _Direct:
    """The integration itself, on one grid, for one model."""

    def __init__(self, model: gibbs.Model, outer, branches: List[exact_chain.Chain]):
        self.model = model
        self.outer = outer
        self.branches = branches
        self.M = model.M
        self.dt = model.dt
        index = np.arange(self.M)
        # cells[a, b] = a - b: how far a sits older than b.
        self.cells = index[:, None] - index[None, :]
        self.older = self.cells > 0
        self.ok = {b: (index >= model.window[b][0]) & (index <= model.window[b][1])
                   for b in model.boundary_ids}

    # -- building blocks ---------------------------------------------------

    def _likelihood(self, date_id: str, nodes: Dict[str, int]) -> np.ndarray:
        model = self.model.model_of_date.get(date_id)
        if model is None:
            return self.model.likelihood[date_id]
        return self.model.likelihood_at[date_id][nodes[model]]

    def _log_group(self, dates: List[str], nodes: Dict[str, int]) -> np.ndarray:
        """
        A group's span factor and its dates' interval integrals, in the log,
        indexed [older boundary, younger boundary] and -inf unless older > younger.
        """
        K = len(dates)
        with np.errstate(divide='ignore'):
            log_span = np.where(self.older,
                                np.log(np.maximum(self.cells, 1) * self.dt), -np.inf)
        term = np.where(self.older, -K * log_span if K else 0.0, -np.inf)
        for date_id in dates:
            row = self._likelihood(date_id, nodes)
            # exact_chain's is indexed [younger, older].
            term = term + _log_interval_integral(np.cumsum(row) * self.dt, row, self.dt).T
        return term

    @staticmethod
    def _exp(log_matrix: np.ndarray) -> Tuple[np.ndarray, float]:
        finite = np.isfinite(log_matrix)
        if not finite.any():
            return np.zeros_like(log_matrix), 0.0
        peak = float(log_matrix[finite].max())
        return np.where(finite, np.exp(np.where(finite, log_matrix, 0.0) - peak), 0.0), peak

    @staticmethod
    def _scaled(matrix: np.ndarray) -> Tuple[np.ndarray, float]:
        peak = float(matrix.max())
        if peak <= 0:
            return matrix, 0.0
        return matrix / peak, float(np.log(peak))

    def _span_correction(self, oldest: str, youngest: str) -> np.ndarray:
        """The span prior over a pair, in the log, [oldest, youngest]."""
        rows = [row for (o, y, _n), row in zip(self.model.span_priors,
                                               self.model.span_prior_rows)
                if (o, y) == (oldest, youngest)]
        if not rows:
            # No correction: every strictly ordered pair counts the same.
            return np.where(self.older, 0.0, -np.inf)
        row = rows[0]
        return np.where(self.older, row[np.clip(self.cells, 0, self.M - 1)], -np.inf)

    def _ends_ok(self, first: str, last: str) -> np.ndarray:
        return self.ok[first][:, None] & self.ok[last][None, :]

    def _branch(self, chain: exact_chain.Chain, nodes: Dict[str, int],
                with_messages: bool) -> Dict:
        """
        A branch's end-to-end product J[first, last], its log scale, and with
        `with_messages` the prefix and suffix products its interior Boundaries'
        marginals are read from.
        """
        transfers = []
        log_scale = 0.0
        for gi, dates in enumerate(chain.groups):
            older_id, younger_id = chain.boundary_ids[gi], chain.boundary_ids[gi + 1]
            log_t = self._log_group(dates, nodes)
            log_t = np.where(self.ok[older_id][:, None] & self.ok[younger_id][None, :],
                             log_t, -np.inf)
            matrix, peak = self._exp(log_t)
            transfers.append(matrix)
            log_scale += peak
        prefix = [np.diag(self.ok[chain.boundary_ids[0]].astype(float))]
        for matrix in transfers:
            product, factor = self._scaled(prefix[-1] @ matrix)
            log_scale += factor
            prefix.append(product)
        out = {'J': prefix[-1], 'log_scale': log_scale}
        if with_messages:
            suffix = [np.diag(self.ok[chain.boundary_ids[-1]].astype(float))]
            for matrix in reversed(transfers):
                suffix.append(self._scaled(matrix @ suffix[-1])[0])
            out['prefix'] = prefix
            out['suffix'] = suffix[::-1]
        return out

    def _within(self, J: np.ndarray) -> Tuple[np.ndarray, float]:
        """
        Z[S, E] = sum over first < S and last > E of J[first, last]: the branch
        with its ends strictly inside the outer pair.
        """
        below_s = np.cumsum(J, axis=0)
        below_s = np.vstack([np.zeros((1, self.M)), below_s[:-1]])
        above_e = np.cumsum(below_s[:, ::-1], axis=1)[:, ::-1]
        above_e = np.hstack([above_e[:, 1:], np.zeros((self.M, 1))])
        return self._scaled(above_e)

    def _outside(self, A: np.ndarray) -> np.ndarray:
        """W[first, last] = sum over S > first and E < last of A[S, E]."""
        older = np.cumsum(A[::-1], axis=0)[::-1]
        older = np.vstack([older[1:], np.zeros((1, self.M))])
        younger = np.cumsum(older, axis=1)
        return np.hstack([np.zeros((self.M, 1)), younger[:, :-1]])

    # -- one combination of outlier scales -----------------------------------

    def solve(self, nodes: Dict[str, int], with_marginals: bool, cache: Dict
              ) -> Tuple[float, Dict[str, np.ndarray]]:
        """(log evidence, Boundary marginals) with every outlier scale fixed."""
        def branch(chain):
            uses = sorted({self.model.model_of_date[d] for g in chain.groups for d in g
                           if d in self.model.model_of_date})
            key = (id(chain), with_marginals) + tuple(nodes[m] for m in uses)
            if key not in cache:
                if len(cache) >= 32:
                    cache.pop(next(iter(cache)))
                cache[key] = self._branch(chain, nodes, with_marginals)
            return cache[key]

        parts = [branch(chain) for chain in self.branches]
        marginals: Dict[str, np.ndarray] = {}

        if self.outer is None:
            chain, part = self.branches[0], parts[0]
            first, last = chain.boundary_ids[0], chain.boundary_ids[-1]
            log_c = np.where(self._ends_ok(first, last),
                             self._span_correction(first, last), -np.inf)
            W, w_scale = self._exp(log_c)
            total = float((W * part['J']).sum())
            if total <= 0:
                return -np.inf, {}
            log_evidence = np.log(total) + w_scale + part['log_scale']
            weights = [W]
        else:
            S, E, loose = self.outer
            log_a = (self._span_correction(S, E) + self._log_group(loose, nodes))
            log_a = np.where(self._ends_ok(S, E), log_a, -np.inf)
            A0, a_scale = self._exp(log_a)
            within = []
            log_evidence = a_scale
            for part in parts:
                Z, z_scale = self._within(part['J'])
                within.append(Z)
                log_evidence += z_scale + part['log_scale']
            joint = A0 * np.prod(within, axis=0)
            total = float(joint.sum())
            if total <= 0:
                return -np.inf, {}
            log_evidence += np.log(total)
            if with_marginals:
                marginals[S] = joint.sum(axis=1)
                marginals[E] = joint.sum(axis=0)
                weights = []
                for b in range(len(parts)):
                    others = A0.copy()
                    for o, Z in enumerate(within):
                        if o != b:
                            others = others * Z
                    weights.append(self._outside(others))

        if with_marginals:
            for chain, part, W in zip(self.branches, parts, weights):
                for k, boundary_id in enumerate(chain.boundary_ids):
                    # p(b_k = x) = sum_{f, l} P_k[f, x] W[f, l] D_k[x, l]
                    left = part['prefix'][k].T @ W
                    marginals[boundary_id] = (left * part['suffix'][k]).sum(axis=1)
        return float(log_evidence), marginals


def reference(measurements: List[Dict], root: Dict,
              uniform_span_prior: bool = True) -> Dict:
    """
    The direct calculation: {'grid', 'shape', 'boundaries': {id: density}}.
    Raises NotChecked when the model is not one this can integrate.
    """
    outer, branches = shape(root, measurements)
    grid = _grid(measurements, root)
    model = gibbs.Model(measurements, root, grid=grid,
                        uniform_span_prior=uniform_span_prior)
    if model.depositions or model.level_ids:
        raise NotChecked('the model has a deposition sequence')
    expected = sum(len(c.boundary_ids) for c in branches) + (2 if outer else 0)
    if set(model.boundary_ids) != (
            {b for c in branches for b in c.boundary_ids}
            | ({outer[0], outer[1]} if outer else set())) or len(model.boundary_ids) != expected:
        raise NotChecked('the Boundaries are not the ones this shape implies')
    ends = ((outer[0], outer[1]) if outer
            else (branches[0].boundary_ids[0], branches[0].boundary_ids[-1]))
    if uniform_span_prior and expected > 2 and [
            (o, y) for o, y, _n in model.span_priors] != [ends]:
        raise NotChecked('the span correction does not span the whole model')

    direct = _Direct(model, outer, branches)
    outlier_models = model.outlier_models
    node_counts = [len(model.scale_grid[m]) for m in outlier_models]
    combinations = list(itertools.product(*[range(n) for n in node_counts]))
    if len(combinations) > MAX_SCALE_COMBINATIONS:
        raise NotChecked('too many outlier models to integrate over')

    cache: Dict = {}
    log_evidence = np.array([
        direct.solve(dict(zip(outlier_models, combo)), False, cache)[0]
        for combo in combinations])
    if not np.isfinite(log_evidence).any():
        raise NotChecked('the direct calculation found no posterior mass')
    weights = np.exp(log_evidence - log_evidence[np.isfinite(log_evidence)].max())
    weights = np.where(np.isfinite(weights), weights, 0.0)
    weights /= weights.sum()

    order = np.argsort(weights)[::-1]
    held = np.cumsum(weights[order])
    chosen = order[:min(MAX_MARGINAL_COMBINATIONS,
                        int(np.searchsorted(held, SCALE_MASS)) + 1)]
    # Grouped so a branch's prefix and suffix products are reused across
    # consecutive combinations that share its scales.
    chosen = sorted(chosen, key=lambda i: combinations[i])
    cache = {}
    boundaries: Dict[str, np.ndarray] = {}
    for i in chosen:
        _log_e, marginals = direct.solve(
            dict(zip(outlier_models, combinations[i])), True, cache)
        for boundary_id, density in marginals.items():
            total = density.sum()
            if total > 0:
                boundaries[boundary_id] = (boundaries.get(boundary_id, 0.0)
                                           + weights[i] * density / total)
    for boundary_id, density in boundaries.items():
        density = density / density.sum()
        if density[:2].sum() + density[-2:].sum() > EDGE_MASS:
            raise NotChecked('a Boundary runs off the edge of the calculation')
        boundaries[boundary_id] = density
    return {'grid': grid, 'shape': 'parallel' if outer else 'chain',
            'boundaries': boundaries,
            'names': dict(model.plan.boundary_names)}


def _binned(xs: np.ndarray, mass: np.ndarray, low: float, count: int) -> np.ndarray:
    bins = np.floor((xs - low) / REPORTING_STEP_YEARS).astype(int)
    inside = (bins >= 0) & (bins < count)
    return np.bincount(bins[inside], weights=mass[inside], minlength=count)


def compare(direct: Dict, sampled_boundaries: Dict[str, Dict]) -> List[Dict]:
    """
    Per Boundary, the total variation distance between the sampled marginal
    and the direct one on REPORTING_STEP_YEARS bins, worst first.

    The sampled side is the results' plotted density, which drops only cells
    under exact_chain.DISPLAY_THRESHOLD of the peak. Mass it puts outside the
    direct grid is mass in the wrong place, and counts as such.
    """
    grid = direct['grid']
    low = float(grid[0])
    count = int(np.ceil((grid[-1] - low) / REPORTING_STEP_YEARS)) + 1
    rows = []
    for boundary_id, density in direct['boundaries'].items():
        sampled = (sampled_boundaries.get(boundary_id) or {}).get('posterior') or {}
        points = np.array(sampled.get('probability_distribution') or [], dtype=float)
        if points.size == 0:
            continue
        xs, ps = points[:, 0], points[:, 1]
        step = float(np.median(np.diff(xs))) if len(xs) > 1 else 1.0
        mass = ps * step
        if mass.sum() <= 0:
            continue
        theirs = _binned(xs, mass / mass.sum(), low, count)
        ours = _binned(grid, density, low, count)
        distance = 0.5 * (float(np.abs(theirs - ours).sum()) + max(0.0, 1.0 - theirs.sum()))
        rows.append({
            'id': boundary_id,
            'name': direct['names'].get(boundary_id, 'Boundary'),
            'distance': distance,
            'sampled_mean': float((xs * mass).sum() / mass.sum()),
            'direct_mean': float((grid * density).sum()),
        })
    rows.sort(key=lambda r: r['distance'], reverse=True)
    return rows


def check(measurements: List[Dict], root: Dict, sampled: Dict,
          uniform_span_prior: bool = True) -> Dict:
    """
    The solver record's `cross_check`:

        {'status': 'agrees' | 'differs' | 'not_checked',
         'reason': why not checked, else None,
         'shape': 'chain' | 'parallel' | None,
         'threshold': DISAGREEMENT,
         'checked': how many Boundaries were compared,
         'worst': the worst row of compare(), or None,
         'differing': every row over the threshold,
         'seconds': how long the direct calculation took}
    """
    started = time.perf_counter()
    record = {'status': 'not_checked', 'reason': None, 'shape': None,
              'threshold': DISAGREEMENT, 'checked': 0, 'worst': None,
              'differing': [], 'seconds': None}
    try:
        direct = reference(measurements, root, uniform_span_prior)
    except NotChecked as reason:
        record['reason'] = str(reason)
        record['seconds'] = time.perf_counter() - started
        return record
    rows = compare(direct, sampled.get('boundaries') or {})
    record.update({
        'shape': direct['shape'],
        'checked': len(rows),
        'worst': rows[0] if rows else None,
        'differing': [r for r in rows if r['distance'] > DISAGREEMENT],
        'seconds': time.perf_counter() - started,
    })
    record['status'] = ('differs' if record['differing']
                        else 'agrees' if rows else 'not_checked')
    if not rows:
        record['reason'] = 'no Boundary to compare'
    return record
