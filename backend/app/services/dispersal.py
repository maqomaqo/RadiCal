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
from app.services.calibration import SIGMA_2
from app.services.reservoir import EARTH_RADIUS_KM, haversine_km

# How far a date may sit from the first date of its site and still be grouped
# with it -- the RADIUS the grouping rules use, here (derive_sites_from_dates)
# and in the frontend's "Group dates into sites" (utils/siteGrouping.ts).
#
# Five kilometres is a generous reading of "the same site" -- it is a large
# multiple of any excavation -- and the point of the number is not precision
# but catching a MODELLING ERROR: a Phase holding dates from two different
# sites. Quietly averaging those two coordinates would place the phase in a
# field between them and report a front that passed through somewhere nobody
# excavated. So the app refuses and names both coordinates and the distance,
# rather than producing a centroid nobody asked for.
SITE_SCATTER_KM = 5.0

# How far apart two dates in one Phase may be before that refusal.
#
# The DIAMETER, not the radius: a group the rule above builds has every member
# within SITE_SCATTER_KM of its first member, so two members on opposite
# sides of it can be twice that apart -- and were. "Group dates into sites"
# at 5 km built an Anakena phase whose dates were 5.3 km apart, and this check
# at 5 km then refused the phase the app itself had just built. The two
# figures have to agree, and this is the one derived from the other.
SITE_SPREAD_LIMIT_KM = 2 * SITE_SCATTER_KM

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

# What a site IS, appended to every "this model has N sites" refusal. The
# count on its own was read as "the dates have no coordinates" by a user whose
# 224 dates all carried them: they had been deployed into the one starting
# phase every analysis begins with, which no Boundary of its own opens, so
# the tree had nothing for the coordinates to hang on. The number has to say
# what would raise it.
SITE_DEFINITION = (
    'A site is an element opened by its own Boundary — a Sequence bracketed '
    'by a start and an end, one per place — whose dates carry coordinates. '
    'Dates with coordinates in one shared phase are not sites: group them '
    'into sites in the model editor, by location, context or position.')

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
        if spread > SITE_SPREAD_LIMIT_KM:
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
            f'it. The number is withheld rather than shown with a caveat. '
            f'{SITE_DEFINITION}'))

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


# --------------------------------------------------------------------------
# Model 2 -- the trend surface
# --------------------------------------------------------------------------

# How square a set of sites must be before a plane through them means
# anything: the across-axis spread as a fraction of the along-axis spread.
#
# Five per cent is nowhere near either measured case -- a 480 km transect along
# a parallel scores 0.013 and a deliberately squat 2x6 grid scores 0.29 -- so
# it separates a transect from a survey without being a judgement call about
# borderline layouts, of which there are none in practice.
MIN_TREND_ASPECT = 0.05

# Isochrons drawn across the fitted surface. Five is enough to read a gradient
# and few enough to label; more would be a contour plot of a PLANE, which has
# nothing further to say.
ISOCHRON_COUNT = 5


def _lambert_azimuthal(sites: Sequence[SiteSpec]) -> Tuple[np.ndarray, np.ndarray, Tuple[float, float]]:
    """
    Site positions as kilometres east and north of their own centroid.

    Lambert azimuthal EQUAL-AREA, centred on the data rather than on a global
    default. Two reasons, and the second is the one that matters:

    - A plane fitted in degrees is fitted in a coordinate system whose units
      change with latitude -- a degree of longitude is 111 km at the equator
      and 38 km at 70 degrees north -- so the gradient would mean different
      things in different parts of one map.
    - Choosing the projection from the data is what keeps it honest at high
      latitude. A Nordic dataset projected on a global default is distorted
      exactly where the archaeology is; centred on its own centroid it is not.

    Equal-area rather than conformal because the quantity being fitted is
    spread over ground, and preserving area is what keeps a gradient in
    years-per-kilometre meaning the same thing across the map.
    """
    lats = np.array([s.latitude for s in sites], dtype=float)
    lons = np.array([s.longitude for s in sites], dtype=float)
    centre = _centroid(list(zip(lats, lons)))
    lat0, lon0 = math.radians(centre[0]), math.radians(centre[1])

    phi, lam = np.radians(lats), np.radians(lons)
    cos_c = (math.sin(lat0) * np.sin(phi)
             + math.cos(lat0) * np.cos(phi) * np.cos(lam - lon0))
    # The antipode of the projection centre, where the scale factor diverges.
    # Unreachable for any real dataset -- sites 20,000 km apart are not one
    # study -- but a silent inf here would surface as a nonsense gradient.
    k = np.sqrt(np.maximum(2.0 / np.maximum(1.0 + cos_c, 1e-12), 0.0))
    x = EARTH_RADIUS_KM * k * np.cos(phi) * np.sin(lam - lon0)
    y = EARTH_RADIUS_KM * k * (math.cos(lat0) * np.sin(phi)
                               - math.sin(lat0) * np.cos(phi) * np.cos(lam - lon0))
    return x, y, centre


def _circular_summary(angles: np.ndarray) -> Dict:
    """
    A direction's mean and 95% interval, on the circle.

    A direction near due north CANNOT be averaged as a number: draws at 359 and
    1 degree average to 180, which is due south -- the exact opposite of the
    answer, and it would look perfectly plausible on screen. So the mean is the
    argument of the mean unit vector, and the interval is taken on deviations
    from it wrapped into (-180, 180].

    `concentration` is R, the mean resultant length, in [0, 1]. It is not
    decoration: R near zero means the draws point every which way and the
    "mean direction" is an artefact of the arithmetic rather than a finding.
    """
    radians = np.radians(angles)
    sin_mean = float(np.mean(np.sin(radians)))
    cos_mean = float(np.mean(np.cos(radians)))
    concentration = math.hypot(sin_mean, cos_mean)
    mean = math.degrees(math.atan2(sin_mean, cos_mean)) % 360.0

    deviation = (angles - mean + 180.0) % 360.0 - 180.0
    low, high = np.quantile(deviation, [(1.0 - SIGMA_2) / 2.0,
                                        1.0 - (1.0 - SIGMA_2) / 2.0])
    return {
        'mean_deg': mean,
        'hpd_95_deg': [float((mean + low) % 360.0), float((mean + high) % 360.0)],
        # How wide that interval is, which a pair of wrapped bearings does not
        # show at a glance: 350 to 10 is twenty degrees, not three hundred.
        'width_deg': float(high - low),
        'concentration': concentration,
    }


def fit_trend(sites: Sequence[SiteSpec], draws: Dict[str, np.ndarray]) -> Dict:
    """
    A plane through arrival age against position: t = a + b*x + c*y.

    **This model assumes no origin**, which is what makes it the right first
    look at a dataset -- before any hypothesis has been imposed on it. Where
    fit_wave answers "how fast did it move away from HERE", this answers "is
    there a gradient at all, and which way does it point", and the two can
    disagree in a way that is informative rather than contradictory: a tidy
    wave from an assumed origin and a trend pointing somewhere else means the
    origin is wrong, not that the arithmetic is.

    Speed is 1/|gradient| and the direction of ADVANCE is the direction in
    which arrival age DECREASES -- older is a larger cal BP, so the front
    travels down the gradient, and the azimuth reported is that of -grad(t)
    rather than of the gradient itself.
    """
    usable = [s for s in sites if s.usable and s.parameter_id in draws]
    refused_sites = [s for s in sites if s.refusal]

    def block(**extra) -> Dict:
        base = {
            'sites': [s.as_dict() for s in sites],
            'n_sites': len(usable),
            'min_sites': MIN_DISPERSAL_SITES,
            'refused': None,
            'speed': None,
            'azimuth': None,
            'centre': None,
            'isochrons': [],
            'residuals': [],
            'basis': (usable[0].basis if usable else None),
        }
        base.update(extra)
        return base

    if refused_sites:
        return block(refused=refused_sites[0].refusal)

    if len(usable) < MIN_DISPERSAL_SITES:
        return block(refused=(
            f'A trend surface needs at least {MIN_DISPERSAL_SITES} sites and '
            f'this model has {len(usable)}. A plane through three points passes '
            'through them exactly, whatever they are, and reports a gradient '
            f'with nothing left over to contradict it. {SITE_DEFINITION}'))

    x, y, centre = _lambert_azimuthal(usable)

    # A plane needs the sites to span two dimensions, and a rank check is not
    # enough to establish that. A transect along a MERIDIAN is exactly
    # rank-deficient and would be caught; a transect along a PARALLEL is not,
    # because a parallel is a curve in this projection -- so the design matrix
    # has full rank and the cross-gradient is estimated from the curvature of
    # the map rather than from anything anybody excavated. Measured at 50
    # degrees north over 480 km, that gives an aspect ratio of 0.013 where a
    # squat 2x6 grid gives 0.29, so the two are not close and the threshold
    # does not have to be delicate.
    spread = np.column_stack([x - x.mean(), y - y.mean()])
    along, across = np.linalg.svd(spread, compute_uv=False)
    if along <= 0 or across / along < MIN_TREND_ASPECT:
        return block(refused=(
            'These sites lie too close to a straight line for a plane through '
            'them to be determined: the gradient along the line is fixed and '
            'the gradient across it would come from rounding rather than from '
            'the dates. A wave of advance from a placed origin is the model '
            'for a transect; a trend surface needs sites spread in two '
            'directions.'))

    design = np.column_stack([np.ones_like(x), x, y])

    # (J, n) as in fit_wave, and for the same reason: every site's age read off
    # the SAME draw. The design matrix is identical across draws because the
    # sites do not move, so one pseudo-inverse serves all of them.
    ages = np.column_stack([np.asarray(draws[s.parameter_id], dtype=float)
                            for s in usable])
    coefficients = ages @ np.linalg.pinv(design).T      # (J, 3): a, b, c
    a, b, c = coefficients[:, 0], coefficients[:, 1], coefficients[:, 2]

    gradient = np.hypot(b, c)
    with np.errstate(divide='ignore', invalid='ignore'):
        speed = 1.0 / gradient
    finite = np.isfinite(speed)

    # Direction of ADVANCE: down the gradient, since arrival age decreases the
    # way the front is going. Compass bearing, clockwise from north, so x
    # (east) is the sine and y (north) the cosine.
    azimuth = np.degrees(np.arctan2(-b, -c)) % 360.0

    fitted = coefficients @ design.T                     # (J, n)
    residual_mean = (ages - fitted).mean(axis=0)
    residual_sd = (ages - fitted).std(axis=0, ddof=1)
    order = np.argsort(-residual_mean)
    residuals = [{
        'element_id': usable[i].element_id,
        'name': usable[i].name,
        'residual_years': float(residual_mean[i]),
        'residual_sd': float(residual_sd[i]),
    } for i in order]

    step = _speed_step(speed[finite]) if finite.any() else None
    return block(
        speed=intervals.summarise(speed[finite], base_step=step),
        azimuth=_circular_summary(azimuth[finite]),
        centre={'lat': float(centre[0]), 'lon': float(centre[1])},
        isochrons=_isochrons(
            float(np.median(a)), float(np.median(b)), float(np.median(c)),
            x, y, centre),
        residuals=residuals,
        draws=int(ages.shape[0]),
        degenerate_draws=int(ages.shape[0] - int(finite.sum())),
    )


def _unproject(x: float, y: float, centre: Tuple[float, float]) -> Tuple[float, float]:
    """A point in the local plane back to latitude and longitude."""
    lat0, lon0 = math.radians(centre[0]), math.radians(centre[1])
    rho = math.hypot(x, y)
    if rho < 1e-9:
        return centre
    angle = 2.0 * math.asin(min(1.0, rho / (2.0 * EARTH_RADIUS_KM)))
    sin_c, cos_c = math.sin(angle), math.cos(angle)
    lat = math.asin(cos_c * math.sin(lat0) + y * sin_c * math.cos(lat0) / rho)
    lon = lon0 + math.atan2(
        x * sin_c,
        rho * math.cos(lat0) * cos_c - y * math.sin(lat0) * sin_c)
    return (math.degrees(lat), (math.degrees(lon) + 180.0) % 360.0 - 180.0)


def _isochrons(a: float, b: float, c: float,
               x: np.ndarray, y: np.ndarray,
               centre: Tuple[float, float]) -> List[Dict]:
    """
    Lines of equal arrival age across the fitted surface.

    The classic representation, and the one a reader of Ammerman &
    Cavalli-Sforza will recognise. On a PLANE they are straight, parallel and
    evenly spaced, so drawing more of them says nothing more -- which is why
    there are five rather than a contour density.

    Drawn from the MEDIAN plane, not from a plane fitted to median ages: the
    first is a draw-wise summary of the surface and the second is a surface
    fitted to a summary, and only the first has the joint structure in it.

    Each line is returned as lat/lon points so the map can draw it without
    knowing the projection -- the projection is this module's business and
    should not leak into a figure.
    """
    gradient_sq = b * b + c * c
    if gradient_sq <= 0:
        return []

    fitted = a + b * x + c * y
    low, high = float(fitted.min()), float(fitted.max())
    if not math.isfinite(low) or high <= low:
        return []

    # Round ages rather than the fitted extremes: "6200 cal BP" is a line a
    # reader can reason about and "6187.4 cal BP" is an artefact of which sites
    # happened to be dug.
    step = _round_step((high - low) / ISOCHRON_COUNT)
    first = math.ceil(low / step) * step

    # Half-length of each drawn line: enough to cross the sites, plus a margin
    # so the lines do not stop exactly at the outermost marker.
    reach = 1.25 * max(float(np.hypot(x - x.mean(), y - y.mean()).max()), 1.0)
    along = np.array([-c, b]) / math.sqrt(gradient_sq)

    lines = []
    age = first
    while age <= high and len(lines) < ISOCHRON_COUNT + 1:
        # The point on this isochron closest to the projection's origin.
        base = np.array([b, c]) * (age - a) / gradient_sq
        points = [base - reach * along, base + reach * along]
        lines.append({
            'age': float(age),
            'points': [
                {'latitude': lat, 'longitude': lon}
                for lat, lon in (_unproject(float(p[0]), float(p[1]), centre)
                                 for p in points)
            ],
        })
        age += step
    return lines


def _round_step(raw: float) -> float:
    """
    A spacing a reader would have chosen: 1, 2 or 5 times a power of ten.

    Same reasoning as a plot's axis ticks. An isochron every 137 years is
    arithmetically fine and reads as noise.
    """
    if not math.isfinite(raw) or raw <= 0:
        return 1.0
    magnitude = 10.0 ** math.floor(math.log10(raw))
    for multiple in (1.0, 2.0, 5.0):
        if raw <= multiple * magnitude:
            return multiple * magnitude
    return 10.0 * magnitude


# --------------------------------------------------------------------------
# Profiling the origin (§6.4, mode 2)
# --------------------------------------------------------------------------

# How coarse the candidate grid is, in degrees. One degree is about 111 km
# north-south, which is the right order for a question whose answer is a
# REGION: a finer grid would compute a more precise location for a maximum
# that is not precisely located, and invite exactly the false precision this
# mode exists to avoid.
ORIGIN_GRID_DEG = 1.0

# How far outside the sites' own bounding box to look, in degrees. A front's
# origin is very often outside the excavated area -- that is usually the whole
# question -- so a grid clipped to the sites could not find it.
ORIGIN_MARGIN_DEG = 6.0

# Most candidates to evaluate. A global dataset would otherwise generate tens
# of thousands of them; past this the step widens instead, and the step that
# was actually used is reported.
MAX_ORIGIN_CANDIDATES = 2500

# The support contour the reported region is drawn at.
#
# 1/8 of the peak, which is the conventional "strong evidence" cut on a
# likelihood ratio and is deliberately NOT a confidence region: this is a
# profile over an assumed model, not a posterior, and calling it 95% would
# claim a coverage property nothing here has established.
ORIGIN_SUPPORT_CUTOFF = 0.125


def _ols_per_draw(distances: np.ndarray,
                  ages: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    (slope, intercept, residual sum of squares), one per draw.

    Closed form rather than lstsq per draw: the design is the same for every
    draw because the sites do not move, so this is one pass of arithmetic
    where a loop would be thousands of decompositions of one matrix.
    """
    centred = distances - distances.mean()
    denominator = float((centred ** 2).sum())
    mean_t = ages.mean(axis=1)
    slope = (ages - mean_t[:, None]) @ centred / denominator
    intercept = mean_t - slope * distances.mean()
    fitted = intercept[:, None] + slope[:, None] * distances[None, :]
    return slope, intercept, ((ages - fitted) ** 2).sum(axis=1)


def profile_origin(sites: Sequence[SiteSpec], draws: Dict[str, np.ndarray],
                   step_deg: float = ORIGIN_GRID_DEG) -> Dict:
    """
    Where the front could have started, as a surface rather than a point.

    Mode 2 of §6.4, and the answer to the objection the whole feature invites:
    a wave of advance fitted from a PLACED origin tests somebody's hypothesis,
    and this asks what the data would have said without one. For every
    candidate origin on a coarse grid, fit the wave and keep how badly it fits;
    the output is the whole surface.

    **A region, deliberately, and not a best guess.** The profile is nearly
    flat along the front's own axis -- moving the origin backwards along the
    line of travel and adjusting t0 to match fits almost exactly as well, which
    is a real property of the model and not a defect of the arithmetic. So the
    peak of this surface is not a location, and reporting it as one would be
    the false precision the mode exists to avoid. What is reported is the
    contour, with the peak named as the peak of something broad.

    Support is a likelihood ratio against the best candidate. For Gaussian
    residuals the profile log-likelihood at a candidate is -n/2 * log(RSS) with
    the scale concentrated out, averaged over draws -- so the chronological
    uncertainty is inside the surface rather than applied to it afterwards.
    """
    usable = [s for s in sites if s.usable and s.parameter_id in draws]
    refused_sites = [s for s in sites if s.refusal]

    def block(**extra) -> Dict:
        base = {
            'n_sites': len(usable),
            'min_sites': MIN_DISPERSAL_SITES,
            'refused': None,
            'step_deg': None,
            'candidates': 0,
            'peak': None,
            'region': [],
            'cutoff': ORIGIN_SUPPORT_CUTOFF,
        }
        base.update(extra)
        return base

    if refused_sites:
        return block(refused=refused_sites[0].refusal)
    if len(usable) < MIN_DISPERSAL_SITES:
        return block(refused=(
            f'Profiling an origin needs at least {MIN_DISPERSAL_SITES} sites '
            f'and this model has {len(usable)}. With fewer, every candidate '
            'origin fits about as well as every other and the surface says '
            f'nothing. {SITE_DEFINITION}'))

    lats = np.array([s.latitude for s in usable], dtype=float)
    lons = np.array([s.longitude for s in usable], dtype=float)
    ages = np.column_stack([np.asarray(draws[s.parameter_id], dtype=float)
                            for s in usable])
    n_sites = len(usable)

    # Widen the step rather than evaluate more candidates: a finer grid over a
    # continental spread would be a lot of arithmetic to locate the maximum of
    # a surface that is flat where it matters.
    step = float(step_deg)
    while True:
        lat_axis = np.arange(lats.min() - ORIGIN_MARGIN_DEG,
                             lats.max() + ORIGIN_MARGIN_DEG + step / 2, step)
        lon_axis = np.arange(lons.min() - ORIGIN_MARGIN_DEG,
                             lons.max() + ORIGIN_MARGIN_DEG + step / 2, step)
        lat_axis = lat_axis[(lat_axis >= -90.0) & (lat_axis <= 90.0)]
        if lat_axis.size * lon_axis.size <= MAX_ORIGIN_CANDIDATES:
            break
        step *= 2.0

    scores: List[Tuple[float, float, float]] = []
    for lat in lat_axis:
        for lon in lon_axis:
            wrapped = (float(lon) + 180.0) % 360.0 - 180.0
            distances = np.array(
                [haversine_km(float(lat), wrapped, s.latitude, s.longitude)
                 for s in usable], dtype=float)
            if len(np.unique(np.round(distances, 6))) < MIN_DISTINCT_DISTANCES:
                # Sites on one circle around this candidate: no gradient to
                # estimate, so it is not a candidate at all rather than a
                # candidate that fits badly.
                continue
            _slope, _intercept, rss = _ols_per_draw(distances, ages)
            # Concentrated-out scale, averaged over draws. Guarded because a
            # perfect fit on a synthetic front really does give RSS = 0.
            score = float(np.mean(-0.5 * n_sites * np.log(np.maximum(rss, 1e-12))))
            scores.append((float(lat), wrapped, score))

    if not scores:
        return block(step_deg=step, refused=(
            'No candidate origin gave the sites distinct enough distances for '
            'a front to be fitted from it.'))

    best = max(score for _lat, _lon, score in scores)
    region = [{
        'lat': lat,
        'lon': lon,
        # Likelihood ratio against the best candidate, in (0, 1].
        'support': float(math.exp(min(0.0, score - best))),
    } for lat, lon, score in scores]
    inside = [point for point in region
              if point['support'] >= ORIGIN_SUPPORT_CUTOFF]
    peak = max(region, key=lambda point: point['support'])

    return block(
        step_deg=step,
        candidates=len(region),
        # Named as the peak of the region rather than as "the origin": see the
        # docstring for why this surface has no single location in it.
        peak={'lat': peak['lat'], 'lon': peak['lon']},
        region=inside,
        # How much of the searched grid survives the cutoff. Near 1 the data
        # do not locate an origin at all, which is a finding and is exactly
        # what a single best-guess marker would have hidden.
        share_inside=(len(inside) / len(region)),
    )


# --------------------------------------------------------------------------
# Model 3 -- a spatial order, offered as a PROPOSAL (§6.7)
# --------------------------------------------------------------------------

def _minimum_spanning_tree(sites: Sequence[SiteSpec]) -> List[Tuple[int, int, float]]:
    """
    Prim's algorithm over great-circle distance: the shortest set of links that
    joins every site to every other.

    Great-circle rather than planar for the reason everything else here is --
    a degree of longitude is a third as long at 70 degrees north as at the
    equator, so a planar tree would link Norwegian sites by a measure that
    means something different from the one it used in Iberia.
    """
    n = len(sites)
    reached = {0}
    edges: List[Tuple[int, int, float]] = []
    # Cheapest known link from the reached set to each unreached site.
    best = {j: (haversine_km(sites[0].latitude, sites[0].longitude,
                             sites[j].latitude, sites[j].longitude), 0)
            for j in range(1, n)}

    while len(reached) < n:
        j = min(best, key=lambda k: best[k][0])
        distance, i = best.pop(j)
        edges.append((i, j, distance))
        reached.add(j)
        for k in list(best):
            step = haversine_km(sites[j].latitude, sites[j].longitude,
                                sites[k].latitude, sites[k].longitude)
            if step < best[k][0]:
                best[k] = (step, j)
    return edges


def _farthest_from(start: int, neighbours: Dict[int, List[Tuple[int, float]]]) -> Tuple[int, float]:
    """The tree node farthest from `start`, by summed edge length."""
    seen, stack, farthest, best = {start}, [(start, 0.0)], start, 0.0
    while stack:
        node, travelled = stack.pop()
        if travelled > best:
            farthest, best = node, travelled
        for other, step in neighbours[node]:
            if other not in seen:
                seen.add(other)
                stack.append((other, travelled + step))
    return farthest, best


def propose_sequence(sites: Sequence[SiteSpec],
                     origin: Optional[Tuple[float, float]] = None) -> Dict:
    """
    An order for the sites, read off a minimum spanning tree.

    **This is a proposal and nothing else.** A spanning tree is SPATIAL
    ADJACENCY, not chronology. Asserting this order as a Sequence asserts that
    everything at one site predates everything at the next, which is a strong
    claim the dates may refute -- and that is exactly its value: accepting it
    makes the claim TESTABLE. Run the model, read the agreement indices, and
    the sites that do not fit the spatial order announce themselves.

    So nothing here applies anything. It needs no draws and no solver run,
    because it is a question about positions rather than about ages -- which
    is also why it can be offered while a model is still being written, before
    there is anything to run.

    The walk starts at the site nearest the origin where one is given. Without
    one it starts at an end of the tree's longest path, which is the natural
    start of a linear arrangement and is found rather than assumed.
    """
    usable = [s for s in sites if s.usable]
    refused_sites = [s for s in sites if s.refusal]

    def block(**extra) -> Dict:
        base = {
            'order': [],
            'edges': [],
            'refused': None,
            'n_sites': len(usable),
            'total_km': None,
            'rooted_at': None,
            'origin': ({'lat': float(origin[0]), 'lon': float(origin[1])}
                       if origin else None),
        }
        base.update(extra)
        return base

    if refused_sites:
        return block(refused=refused_sites[0].refusal)
    if len(usable) < 2:
        return block(refused=(
            f'A spatial order needs at least two placed sites and this model '
            f'has {len(usable)}. {SITE_DEFINITION}'))

    edges = _minimum_spanning_tree(usable)
    neighbours: Dict[int, List[Tuple[int, float]]] = {i: [] for i in range(len(usable))}
    for i, j, distance in edges:
        neighbours[i].append((j, distance))
        neighbours[j].append((i, distance))

    if origin is not None:
        root = min(range(len(usable)),
                   key=lambda i: haversine_km(origin[0], origin[1],
                                              usable[i].latitude, usable[i].longitude))
    else:
        # An end of the tree's diameter: the two-pass trick, farthest from
        # anywhere, then farthest from that.
        root, _ = _farthest_from(_farthest_from(0, neighbours)[0], neighbours)

    # Depth-first from the root, nearest branch first, so the order follows the
    # tree outwards rather than jumping between limbs.
    order: List[int] = []
    seen = {root}
    stack = [root]
    while stack:
        node = stack.pop()
        order.append(node)
        # Reversed because a stack pops last-in first: pushing farthest first
        # leaves the nearest on top.
        for other, _step in sorted(neighbours[node], key=lambda pair: -pair[1]):
            if other not in seen:
                seen.add(other)
                stack.append(other)

    return block(
        order=[{
            'element_id': usable[i].element_id,
            'name': usable[i].name,
            'latitude': usable[i].latitude,
            'longitude': usable[i].longitude,
        } for i in order],
        # The tree itself, so the map can draw what the order was read off
        # rather than only the order.
        edges=[{
            'from': usable[i].element_id,
            'to': usable[j].element_id,
            'from_point': {'latitude': usable[i].latitude, 'longitude': usable[i].longitude},
            'to_point': {'latitude': usable[j].latitude, 'longitude': usable[j].longitude},
            'km': distance,
        } for i, j, distance in edges],
        total_km=float(sum(distance for _i, _j, distance in edges)),
        rooted_at=usable[root].element_id,
    )
