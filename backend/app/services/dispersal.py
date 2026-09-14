"""
Wave of advance -- how fast a thing moved across a landscape, and when it set
out.

Why this is not a regression on a table of dates
------------------------------------------------
The naive estimator takes each site's earliest date as its arrival time and
regresses that against distance. It is biased, in a KNOWN DIRECTION and by an
UNKNOWN AMOUNT: you almost never excavate the genuinely first thing at a site,
so the oldest date is a *terminus ante quem* for arrival, not an estimate of
it. The resulting front is systematically late everywhere, and late by an
amount that varies with how hard each site was dug -- which is to say, by an
amount that varies with modern excavation budgets rather than with anything
that happened in the past.

OxCal's Boundary is the standard correction and this app already computes it.
A site modelled as a Phase has a start Boundary whose posterior extrapolates
*beyond* its oldest dated member, by an amount the span and count of its dates
imply. So the unit of observation here is **the Boundary that opens a site**,
never a date.

The second half of the same argument is why this module lives beside
intervals.py rather than anywhere downstream. A front speed is a function of
MANY modelled ages at once, exactly like a duration, so it has to come off
JOINT draws: `AnalysisResult.posterior_samples` holds marginals, and pairing
marginals across sites would give a speed distribution that is wrong in shape
rather than scale. Joint draws exist only inside a solver run, so a dispersal
statistic is computed during the run or not at all.

The fit
-------
For a site *i* with arrival age *t_i* (cal BP) at great-circle distance *d_i*
from an origin:

    t_i  =  t0 - d_i / v  +  eps_i

fitted **per joint draw**: for each of the J draws, take that draw's arrival
age for every site, run an ordinary least-squares fit of *t* on *d*, and keep
`v = -1/slope` and `t0`. The result is J values of *v* -- a posterior for the
front speed with the chronological uncertainty already inside it rather than
bolted on afterwards.

Older is a LARGER cal BP, so a front spreading outwards has arrival ages that
DECREASE with distance: a negative slope, hence `v = -1/slope` positive. A
positive slope means the far sites are older than the near ones, and this
module reports that as a negative speed. Nothing here takes an absolute value
or flips a sign to make the answer look like a dispersal; a front running the
other way, or no front at all, is a finding.

What this is not
----------------
OLS on spatially autocorrelated residuals with an assumed origin will always
return a tidy *v* with a tidy interval. That is the failure mode this module
is most exposed to, and it is answered in three places rather than one: the
refusal below MIN_DISPERSAL_SITES, the per-site residuals (which are the
finding whenever they are structured), and the caveats the panel carries. None
of the three is optional.
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.services import intervals
from app.services.reservoir import haversine_km

# How far two dates in one site may sit apart before the app refuses to treat
# them as one place.
#
# Five kilometres is a generous reading of "the same site" -- it is a large
# multiple of any excavation -- and the point of the number is not precision
# but catching a MODELLING ERROR: a Phase holding dates from two different
# sites. Quietly averaging those two coordinates would place the phase in a
# field between them and report a front that passed through somewhere nobody
# excavated. So the app refuses and names both coordinates and the distance,
# rather than producing a centroid nobody asked for.
SITE_SCATTER_KM = 5.0

# Sites below which no speed is reported at all.
#
# OLS through three points returns a slope, a speed and an interval, all of
# them tidy and none of them worth anything: with n - 2 = 1 residual degree of
# freedom there is nothing left over with which the data could contradict the
# line. Six is where the residual table starts being able to say "this is not
# a front" -- which is the diagnostic §6.6 leans on and the reason the number
# is a refusal rather than a warning. A fit that cannot be contradicted should
# not be printed.
MIN_DISPERSAL_SITES = 6

# Distinct site distances needed before a slope means anything. Two sites at
# the same distance from the origin constrain the intercept and say nothing
# about the gradient, so a fit needs at least two distinct radii; with fewer,
# the slope is either undefined or determined by a single pair.
MIN_DISTINCT_DISTANCES = 3


@dataclass
class SiteSpec:
    """
    One site: where it is, and which parameter carries its arrival.

    `basis` is load-bearing and travels with the site into every panel, plot
    and export:

      'boundary'              the posterior of the Boundary that opens the
                              site. An arrival estimate.
      'earliest_dated_event'  the oldest date at the site, because there is no
                              model and therefore no Boundary. NOT an arrival
                              estimate -- it is a terminus ante quem, and it is
                              labelled that way everywhere rather than in
                              documentation somewhere.
    """
    element_id: str
    name: str
    parameter_id: str
    basis: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    date_ids: Tuple[str, ...] = ()
    # Largest distance between any two of its dates, in km. Zero for a single
    # coordinate; None when the site has no usable coordinates at all.
    scatter_km: Optional[float] = None
    # Why this site cannot be used, in the user's words. None when it can be.
    refusal: Optional[str] = None

    @property
    def usable(self) -> bool:
        return (self.refusal is None
                and self.latitude is not None and self.longitude is not None)

    def as_dict(self) -> Dict:
        return {
            'element_id': self.element_id,
            'name': self.name,
            'parameter_id': self.parameter_id,
            'basis': self.basis,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'date_ids': list(self.date_ids),
            'scatter_km': self.scatter_km,
            'refusal': self.refusal,
        }


# --------------------------------------------------------------------------
# Deriving sites from the model tree
# --------------------------------------------------------------------------

def _children(element: Dict) -> List[Dict]:
    return list(element.get('children') or [])


def _name_of(element: Dict) -> str:
    return element.get('name') or element.get('element_type') or element['id']


def _opening_boundaries(root: Dict) -> Dict[str, str]:
    """
    Every element that has a Boundary opening it, mapped to that Boundary's id.

    Two ways an element gets one, and a Sequence can have both:

    - it IS a Sequence with Boundary children, in which case its FIRST Boundary
      child opens it. This is the case that makes a Sequence of Phases one
      site rather than one site per phase.
    - it sits between a consecutive pair of Boundary siblings, in which case
      the older of that pair opens it. Innermost pair wins, matching the rule
      model_tree._plan_element applies to dates.
    """
    opening: Dict[str, str] = {}

    def walk(element: Dict, inherited: Optional[str]) -> None:
        children = _children(element)
        boundary_at = [i for i, c in enumerate(children)
                       if c.get('element_type') == 'boundary']
        is_sequence = element.get('element_type') == 'sequence'

        if is_sequence and len(boundary_at) >= 2:
            opening.setdefault(element['id'], children[boundary_at[0]]['id'])
        elif inherited is not None:
            opening.setdefault(element['id'], inherited)

        for index, child in enumerate(children):
            if child.get('element_type') == 'boundary':
                continue
            own = inherited
            if is_sequence:
                before = [i for i in boundary_at if i < index]
                after = [i for i in boundary_at if i > index]
                # Both sides required: a group needs a Boundary to open it AND
                # one to close it before it is a bracketed interval at all.
                if before and after:
                    own = children[before[-1]]['id']
            walk(child, own)

    walk(root, None)
    return opening


def _subtree_ids(element: Dict) -> List[str]:
    ids = [element['id']]
    for child in _children(element):
        ids.extend(_subtree_ids(child))
    return ids


def _scatter(points: Sequence[Tuple[float, float]]) -> Tuple[float, Optional[Tuple]]:
    """
    (largest distance between any two points, the pair that produced it).

    Max pairwise rather than max-from-centroid: two clusters 6 km apart have a
    centroid comfortably close to both, and it is the 6 km that is the
    modelling error.
    """
    worst, pair = 0.0, None
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            km = haversine_km(points[i][0], points[i][1],
                              points[j][0], points[j][1])
            if km > worst:
                worst, pair = km, (points[i], points[j])
    return worst, pair


def _centroid(points: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    """
    Mean position on the sphere, via unit vectors.

    Averaging degrees would be wrong across the antimeridian -- 179 and -179
    are two degrees apart and average to zero, the Gulf of Guinea -- and sites
    inside SITE_SCATTER_KM of each other can still straddle it.
    """
    xs = ys = zs = 0.0
    for lat, lon in points:
        phi, lam = math.radians(lat), math.radians(lon)
        xs += math.cos(phi) * math.cos(lam)
        ys += math.cos(phi) * math.sin(lam)
        zs += math.sin(phi)
    n = len(points)
    xs, ys, zs = xs / n, ys / n, zs / n
    hyp = math.hypot(xs, ys)
    return (math.degrees(math.atan2(zs, hyp)), math.degrees(math.atan2(ys, xs)))


def derive_sites(root: Optional[Dict], measurements: List[Dict]) -> List[SiteSpec]:
    """
    The model tree read as a set of places, each with the parameter that dates
    its arrival.

    Outermost first, descending only where a candidate's dates are too
    scattered to be one place. That descent is what makes a region modelled as
    one Sequence of site-Sequences come back as several sites rather than one:
    the top-level candidate fails the scatter test, and its children are tried
    instead. When the descent runs out of structure -- a single Phase holding
    dates from two sites -- there is nothing left to split, and THAT is the
    case the refusal is for.

    So the scatter threshold does two different jobs depending on where it
    fires, and both are intended: higher up it separates sites, at the bottom
    it reports a modelling error.
    """
    if not root:
        return []

    coords: Dict[str, Tuple[float, float]] = {}
    names: Dict[str, str] = {}
    by_element: Dict[str, List[str]] = {}
    for m in measurements:
        names[m['id']] = m.get('sample_name') or m['id']
        lat, lon = m.get('latitude'), m.get('longitude')
        if lat is not None and lon is not None:
            coords[m['id']] = (float(lat), float(lon))
        if m.get('element_id'):
            by_element.setdefault(m['element_id'], []).append(m['id'])

    opening = _opening_boundaries(root)
    sites: List[SiteSpec] = []

    def dates_under(element: Dict) -> List[str]:
        found: List[str] = []
        for eid in _subtree_ids(element):
            found.extend(by_element.get(eid, []))
        return found

    def consider(element: Dict) -> None:
        boundary = opening.get(element['id'])
        children = [c for c in _children(element)
                    if c.get('element_type') not in ('boundary', 'hiatus')]

        if boundary is None:
            for child in children:
                consider(child)
            return

        date_ids = dates_under(element)
        placed = [(did, coords[did]) for did in date_ids if did in coords]
        descendable = [c for c in children if opening.get(c['id'])]

        if not placed:
            # No coordinates anywhere below: nothing to place. Not a refusal --
            # a model that never had coordinates is not an error, it is a model
            # this feature has nothing to say about.
            for child in descendable:
                consider(child)
            return

        spread, pair = _scatter([p for _id, p in placed])
        if spread > SITE_SCATTER_KM:
            if descendable:
                for child in descendable:
                    consider(child)
                return
            # Nowhere left to descend: this really is one element holding two
            # places, which is a modelling error and is named as one.
            (lat_a, lon_a), (lat_b, lon_b) = pair
            sites.append(SiteSpec(
                element_id=element['id'],
                name=_name_of(element),
                parameter_id=boundary,
                basis='boundary',
                date_ids=tuple(date_ids),
                scatter_km=spread,
                refusal=(
                    f'"{_name_of(element)}" holds dates {spread:.1f} km apart '
                    f'({lat_a:.4f}, {lon_a:.4f}) and ({lat_b:.4f}, {lon_b:.4f}). '
                    'Dates from two sites in one phase is a modelling error, and '
                    'placing the phase between them would hide it. Split them '
                    'into separate phases, or correct the coordinates.'),
            ))
            return

        # Descend past a wrapper. A model whose root Sequence contains one site
        # Sequence is coherent at the root, but the root's opening Boundary is
        # the start of the MODEL and the inner one is the start of the site --
        # different parameters, the outer being the older. Where a single child
        # accounts for every placed date AND opens on a different Boundary, it
        # is the same place more tightly bounded, so the site is the child.
        #
        # The second condition is what stops the walk falling through a named
        # Sequence into its one anonymous Phase: they share an opening
        # Boundary, so they are the same arrival, and the outer element is the
        # one the analyst gave a name to.
        covering = [c for c in descendable
                    if opening.get(c['id']) != boundary
                    and set(dates_under(c)) >= {did for did, _p in placed}]
        if len(covering) == 1:
            consider(covering[0])
            return

        lat, lon = _centroid([p for _id, p in placed])
        sites.append(SiteSpec(
            element_id=element['id'],
            name=_name_of(element),
            parameter_id=boundary,
            basis='boundary',
            latitude=lat,
            longitude=lon,
            date_ids=tuple(did for did, _p in placed),
            scatter_km=spread,
        ))

    consider(root)
    return sites


def derive_sites_from_dates(measurements: List[Dict],
                            draws: Dict[str, np.ndarray]) -> List[SiteSpec]:
    """
    The fallback for a group calibration: no model, hence no Boundary, hence
    no arrival estimate.

    Dates are grouped by position -- everything within SITE_SCATTER_KM of an
    existing group joins it -- and each group's site takes its EARLIEST DATED
    EVENT, which is labelled exactly that. It is a terminus ante quem and the
    bias is the one this module's docstring opens with: systematically late,
    by an amount that varies with how hard each site was dug. The label rides
    on `basis` so that no panel can show this number without saying so.

    "Earliest" is decided on the median of the draws, once, rather than per
    draw: a per-draw minimum is a biased estimator of the earliest event
    (taking a minimum over noisy values pulls it low), and it would also let
    the identity of the site's representative date change between draws, which
    makes the residuals uninterpretable.
    """
    groups: List[List[Dict]] = []
    for m in measurements:
        lat, lon = m.get('latitude'), m.get('longitude')
        if lat is None or lon is None or m['id'] not in draws:
            continue
        for group in groups:
            if haversine_km(float(lat), float(lon),
                            group[0]['lat'], group[0]['lon']) <= SITE_SCATTER_KM:
                group.append({'id': m['id'], 'lat': float(lat), 'lon': float(lon),
                              'name': m.get('sample_name') or m['id']})
                break
        else:
            groups.append([{'id': m['id'], 'lat': float(lat), 'lon': float(lon),
                            'name': m.get('sample_name') or m['id']}])

    sites: List[SiteSpec] = []
    for group in groups:
        earliest = max(group, key=lambda g: float(np.median(draws[g['id']])))
        points = [(g['lat'], g['lon']) for g in group]
        lat, lon = _centroid(points)
        spread, _pair = _scatter(points)
        sites.append(SiteSpec(
            element_id=earliest['id'],
            name=earliest['name'],
            parameter_id=earliest['id'],
            basis='earliest_dated_event',
            latitude=lat,
            longitude=lon,
            date_ids=tuple(g['id'] for g in group),
            scatter_km=spread,
        ))
    return sites


# --------------------------------------------------------------------------
# Fitting
# --------------------------------------------------------------------------

def _speed_step(values: np.ndarray) -> Optional[float]:
    """
    A reporting resolution for km/yr.

    intervals._step_for floors at five YEARS, which for a front whose whole
    posterior spans a quarter of a kilometre per year would be a single bin.
    So the floor comes off the draws themselves: the largest power of ten that
    still divides the central spread into roughly MAX_BINS pieces.
    """
    low, high = np.percentile(values, intervals.SPREAD_PERCENTILES)
    spread = float(high - low)
    if not math.isfinite(spread) or spread <= 0:
        return None
    return 10.0 ** math.floor(math.log10(spread / intervals.MAX_BINS))


def fit_wave(sites: Sequence[SiteSpec], draws: Dict[str, np.ndarray],
             origin: Tuple[float, float]) -> Dict:
    """
    A wave-of-advance fit, per joint draw, from an origin the caller chose.

    Returns either `{'refused': <reason>, ...}` or the fit. A refusal is a
    result and not an exception: a dispersal estimate is one optional section
    of a solver run's report, and a model with five sites should still produce
    every other number the run was asked for.
    """
    usable = [s for s in sites if s.usable and s.parameter_id in draws]
    refused_sites = [s for s in sites if s.refusal]

    def block(**extra) -> Dict:
        base = {
            'origin': {'lat': float(origin[0]), 'lon': float(origin[1])},
            'sites': [s.as_dict() for s in sites],
            'n_sites': len(usable),
            'min_sites': MIN_DISPERSAL_SITES,
            'refused': None,
            'speed': None,
            't0': None,
            'residuals': [],
            'basis': (usable[0].basis if usable else None),
        }
        base.update(extra)
        return base

    if refused_sites:
        return block(refused=refused_sites[0].refusal)

    if len(usable) < MIN_DISPERSAL_SITES:
        return block(refused=(
            f'A wave-of-advance fit needs at least {MIN_DISPERSAL_SITES} sites '
            f'and this model has {len(usable)}. A straight line through fewer '
            'points returns a speed and an interval whichever way the dates '
            'fall, with too few residuals left over for the data to contradict '
            'it. The number is withheld rather than shown with a caveat.'))

    distances = np.array(
        [haversine_km(origin[0], origin[1], s.latitude, s.longitude)
         for s in usable], dtype=float)

    if len(np.unique(np.round(distances, 6))) < MIN_DISTINCT_DISTANCES:
        return block(refused=(
            'The sites lie at too few distinct distances from this origin for '
            'a gradient to be estimated -- they are close to a single circle '
            'around it. Move the origin, or place it where the hypothesis '
            'actually puts it.'))

    # (J, n): one row per joint draw, one column per site, all read off the
    # SAME draw. This is the whole point -- a column-wise shuffle would give a
    # tidier answer and a meaningless one.
    ages = np.column_stack([np.asarray(draws[s.parameter_id], dtype=float)
                            for s in usable])

    centred_d = distances - distances.mean()
    denominator = float((centred_d ** 2).sum())
    mean_t = ages.mean(axis=1)
    # Per-draw OLS in closed form. Fitting J of these with lstsq would be the
    # same arithmetic a few thousand times slower, and the design matrix is
    # the same for every draw because the sites do not move between draws.
    slope = (ages - mean_t[:, None]) @ centred_d / denominator
    t0 = mean_t - slope * distances.mean()

    with np.errstate(divide='ignore', invalid='ignore'):
        speed = -1.0 / slope
    # A slope of exactly zero is a front of infinite speed, which is not a
    # number to report. summarise drops non-finite draws; the count of them is
    # reported so that "1% of draws" and "60% of draws" are distinguishable.
    finite = int(np.isfinite(speed).sum())

    fitted = t0[:, None] + slope[:, None] * distances[None, :]
    # Mean over draws of the signed residual, in years. Positive means the site
    # is OLDER than the front predicts -- the thing arrived there before the
    # line says it should have.
    residual_mean = (ages - fitted).mean(axis=0)
    residual_sd = (ages - fitted).std(axis=0, ddof=1)

    order = np.argsort(-residual_mean)
    residuals = [{
        'element_id': usable[i].element_id,
        'name': usable[i].name,
        'distance_km': float(distances[i]),
        'residual_years': float(residual_mean[i]),
        'residual_sd': float(residual_sd[i]),
    } for i in order]

    step = _speed_step(speed[np.isfinite(speed)]) if finite else None
    return block(
        speed=intervals.summarise(speed, base_step=step),
        t0=intervals.calendar_band(t0),
        residuals=residuals,
        distances=[float(d) for d in distances],
        draws=int(ages.shape[0]),
        # Draws whose slope was zero, i.e. no gradient at all in that draw.
        # Excluded from the speed summary, and visible rather than silent.
        degenerate_draws=int(ages.shape[0] - finite),
    )
