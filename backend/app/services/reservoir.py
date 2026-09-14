"""
The vocabulary of the marine reservoir database: what a feeding habit is, and
what one of the source's twenty-five spellings means.

Separate from reservoir_ingest.py, which fetches and parses, because this is
the part that encodes a JUDGEMENT about the source data rather than a reading
of it -- and because the lookup in §5.4 filters on these classes without
wanting anything to do with HTTP. See
docs/SPATIAL_METADATA_AND_DISPERSAL_PLAN.md §2.7 for the measurements behind
the table below.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence
import math


# The seven classes the source's spellings fold into.
#
# 'ambiguous' is the one that is not a habit. It is where the compound
# descriptions go -- see AMBIGUOUS_PREFIXES -- and it exists so that a hedge
# the original author wrote deliberately is never silently resolved into one
# side of the thing they were hedging about.
#
# 'unknown' is a real answer and not a suspect class: measured against the
# suspension pool it is mostly drawn from, it sits at +18 yr [-8.7, +43],
# which is to say indistinguishable. Excluding it by default would throw away
# 14% of the database to fix nothing.
FEEDING_CLASSES = (
    'suspension',
    'deposit',
    'carnivore',
    'grazer',
    'migratory',
    'ambiguous',
    'unknown',
)

# Source spelling (lowercased, stripped) -> class.
#
# Every spelling observed in the 2060 rows of the 2026-05-11 database. The
# case variants are the source's own inconsistency; 'filter' is a synonym for
# suspension feeding; the literal string 'NULL' is a null that came through as
# text.
#
# Grazers fold in with herbivores and browsers because algae and herbivorous
# gastropods eat the same recent carbon, and the paired test finds them
# indistinguishable from suspension feeders at every radius. 'commensalism' is
# not a feeding habit at all, but the organisms so described in this database
# are commensal on algal grazers and behave like them.
_FEEDING_SPELLINGS: Dict[str, str] = {
    # suspension
    'suspension': 'suspension',
    'filter': 'suspension',
    # deposit
    'deposit': 'deposit',
    # carnivore
    'carnivore': 'carnivore',
    'predator': 'carnivore',
    'scavenger': 'carnivore',
    'omnivore': 'carnivore',
    # grazer
    'herbivore': 'grazer',
    'browser': 'grazer',
    'algal grazer': 'grazer',
    'commensalism': 'grazer',
    # migratory
    'migratory': 'migratory',
    # unknown
    'unknown': 'unknown',
    'null': 'unknown',
    '': 'unknown',
}

# The five values the source truncates at sixteen characters.
#
# These are compound judgements -- "deposit or suspension feeder",
# "carnivore/scavenger" -- cut off mid-word by the source's column width. They
# cannot be assigned to one class without deciding the question their author
# declined to decide, so they become 'ambiguous' and the lookup treats them as
# what they are: points whose habit is not known well enough to filter on.
#
# Matched as prefixes rather than as exact strings because the truncation is
# an artefact of the column, and a source that widened the column tomorrow
# would start sending the untruncated text.
AMBIGUOUS_PREFIXES = (
    'deposit or suspe',
    'suspension/depos',
    'deposit feeder o',
    'herbivore/deposi',
    'carnivore/scaven',
)


def normalise_feeding(raw: Optional[str]) -> str:
    """
    One of FEEDING_CLASSES, from whatever the source said.

    Never raises and never returns None: an unrecognised spelling is
    'unknown', which is the honest reading -- the app does not know this
    organism's habit -- and reservoir_ingest counts those separately so a
    source that grows a new vocabulary is noticed rather than absorbed.
    """
    if raw is None:
        return 'unknown'
    text = raw.strip().lower()
    if not text:
        return 'unknown'

    # Prefixes first: 'deposit or suspe' starts with 'deposit', and matching
    # the exact table before the prefixes would file a deliberate hedge as a
    # deposit feeder -- the exact mistake this class exists to prevent.
    for prefix in AMBIGUOUS_PREFIXES:
        if text.startswith(prefix):
            return 'ambiguous'

    return _FEEDING_SPELLINGS.get(text, 'unknown')


def is_recognised_feeding(raw: Optional[str]) -> bool:
    """
    Whether the source's spelling is one this table knows.

    Distinct from normalising to 'unknown', which a NOVEL spelling also does:
    the ingest report counts what this returns False for, so a new vocabulary
    in the source shows up as a number a person can look at rather than as 300
    more rows quietly filed under unknown.
    """
    if raw is None:
        return True
    text = raw.strip().lower()
    if any(text.startswith(prefix) for prefix in AMBIGUOUS_PREFIXES):
        return True
    return text in _FEEDING_SPELLINGS


# --------------------------------------------------------------------------
# Deriving a Delta-R from a position
# --------------------------------------------------------------------------
#
# Everything below reads a snapshot's points and combines them. It imports
# nothing that talks to a network: the mirror is what makes this possible
# without sending a site's coordinates to a third party on every lookup, which
# is the whole argument of the plan's §5.

# Mean Earth radius, IUGG. The same figure calib.org's "nearest points" ranks
# by, so the point list this produces is the one their page would show -- which
# is what makes a derived value checkable against the source.
EARTH_RADIUS_KM = 6371.0088

# The classes a "suspension-equivalent" counterfactual drops.
#
# Measured, not assumed (plan §2.7): against the suspension points near them,
# deposit feeders sit +35 to +42 yr older, carnivores +34 to +40, and migratory
# +42 to +55. Grazers and unknown do NOT -- both behave like the suspension
# pool they are mostly drawn from, and dropping `unknown` alone would discard
# 14% of the database to fix nothing. `ambiguous` stays too: those five values
# are truncated compound judgements, and excluding them would be deciding the
# question their author declined to decide.
BIASED_FEEDING_CLASSES = ('deposit', 'migratory', 'carnivore')

# When the app says so out loud.
#
# Excluding the biased classes moves a derived Delta-R by a median of 8 yr,
# p90 52 yr, max 465 yr, and 61% of nearest-10 sets contain at least one such
# point. Prompting on PRESENCE would therefore fire on three lookups in five
# to change the answer materially in about one of those five -- a prompt
# trained to be dismissed, arriving in front of the cases that matter.
#
# So: never prompt, never exclude by default, compute both values every time,
# and escalate only when the difference is big enough to matter. 35 yr is the
# median reported Delta-R error in the database -- the precision an individual
# published value claims for itself, and therefore the point at which swapping
# one set of points for another stops being absorbed by the value's own stated
# uncertainty. It fires on about 11% of lookups.
#
# Not the 84 yr co-located nugget (§2.4): that is the noise floor of the
# DATABASE, the disagreement two shells from one beach show regardless of
# habit, and a bar set there would stay silent on more than half the cases
# where the derived value moves by more than its own sigma.
FEEDING_SHIFT_WARN_YEARS = 35.0

# Below this, a "weighted mean" is not one.
#
# With a single point the formula returns that point's own error and calls it
# a scatter estimate, which reads as a measurement of agreement where there is
# nothing to agree. Forty query points in the jackknife fall below this once
# the biased classes are excluded, which is why exclusion refuses rather than
# quietly returning one point.
MIN_WEIGHTED_POINTS = 2


@dataclass
class ReservoirPoint:
    """One measurement, as the lookup needs it. See models.MarineReservoirPoint."""
    map_no: int
    latitude: float
    longitude: float
    delta_r: Optional[float]
    delta_r_error: Optional[float]
    feeding: Optional[str] = None
    feeding_raw: Optional[str] = None
    locality: Optional[str] = None
    reference: Optional[str] = None
    distance_km: Optional[float] = None


@dataclass
class Combination:
    """A weighted mean and what went into it."""
    delta_r: Optional[float]
    delta_r_error: Optional[float]
    n: int
    map_nos: List[int] = field(default_factory=list)
    # The two candidates for the reported uncertainty, kept so the panel can
    # say which one won. See combine().
    internal: Optional[float] = None
    scatter: Optional[float] = None
    # Points dropped because they carry no uncertainty and cannot be weighted.
    excluded_no_error: List[int] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            'delta_r': self.delta_r,
            'delta_r_error': self.delta_r_error,
            'n': self.n,
            'map_nos': list(self.map_nos),
            'internal': self.internal,
            'scatter': self.scatter,
            'excluded_no_error': list(self.excluded_no_error),
        }


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Great-circle distance in kilometres.

    Great-circle rather than planar because the database is global and a
    Euclidean distance in degrees would rank points by a measure that means
    different things at different latitudes -- at 70 degrees north a degree of
    longitude is a third of what it is at the equator, so a planar "nearest"
    would reach three times too far east and west of a Norwegian site.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def nearest(
    latitude: float,
    longitude: float,
    points: Sequence[ReservoirPoint],
    n: int,
    *,
    max_km: Optional[float] = None,
) -> List[ReservoirPoint]:
    """
    The `n` points closest to a position, nearest first, each carrying its
    distance.

    Takes the points rather than a snapshot id: the ranking is arithmetic and
    has no business opening a database, which is also what lets it be tested
    against a hand-built list.

    Points with no Delta-R at all are not candidates -- they are rows of the
    database that have a position and nothing to say about the reservoir.
    Points with a Delta-R but no uncertainty ARE candidates, and are excluded
    later, by combine, which names them: a nearest-5 that silently became a
    nearest-4 would misreport how much evidence is behind the number.
    """
    with_distance = []
    for point in points:
        if point.delta_r is None:
            continue
        distance = haversine_km(latitude, longitude, point.latitude, point.longitude)
        if max_km is not None and distance > max_km:
            continue
        with_distance.append(
            ReservoirPoint(**{**point.__dict__, 'distance_km': distance}))

    # Map number breaks a tie, so two equidistant points come back in the same
    # order on every call and a provenance record is reproducible.
    with_distance.sort(key=lambda p: (p.distance_km, p.map_no))
    return with_distance[:n] if n is not None else with_distance


def combine(points: Sequence[ReservoirPoint]) -> Combination:
    """
    calib.org's own weighted mean, reproduced exactly.

        w_i      = 1 / sigma_i^2
        delta_R  = sum(w_i dR_i) / sum(w_i)
        internal = 1 / sqrt(sum(w_i))
        scatter  = sqrt( sum(w_i (dR_i - dR)^2) / sum(w_i) * n/(n-1) )   [0 at n=1]
        sigma    = MAX(internal, scatter)

    The max is the part worth stating. Their explanation page cites Bevington
    1969 for "the maximum of the standard deviation and the weighted
    uncertainty in the mean", and three published results discriminate it from
    the alternatives: for map numbers 2590, 1006 and 2147 the site prints 29,
    which is max(17.13, 28.65); adding the two in quadrature would give 33.
    Reproducing this exactly is what lets the app derive a value a reviewer can
    check on calib.org's own page without the app calling calib.org.

    A point with no uncertainty cannot be weighted and is excluded, by map
    number, rather than being given an invented error. Thirty rows of 2060
    are in that position.
    """
    usable = [p for p in points if p.delta_r is not None and p.delta_r_error]
    excluded = [p.map_no for p in points
                if p.delta_r is not None and not p.delta_r_error]

    if not usable:
        return Combination(delta_r=None, delta_r_error=None, n=0,
                           excluded_no_error=excluded)

    weights = [1.0 / (p.delta_r_error ** 2) for p in usable]
    total = sum(weights)
    mean = sum(w * p.delta_r for w, p in zip(weights, usable)) / total

    internal = 1.0 / math.sqrt(total)
    if len(usable) > 1:
        spread = sum(w * (p.delta_r - mean) ** 2 for w, p in zip(weights, usable)) / total
        scatter = math.sqrt(spread * len(usable) / (len(usable) - 1))
    else:
        # One point has nothing to disagree with. Zero rather than None so the
        # max below is still the reported sigma -- which is then that point's
        # own error, as it should be.
        scatter = 0.0

    return Combination(
        delta_r=mean,
        delta_r_error=max(internal, scatter),
        n=len(usable),
        map_nos=[p.map_no for p in usable],
        internal=internal,
        scatter=scatter,
        excluded_no_error=excluded,
    )


class LookupRefused(Exception):
    """The lookup cannot answer, and says what it would have taken."""


@dataclass
class Lookup:
    """
    A derived Delta-R and everything needed to judge it before applying it.

    Deliberately not just a number. §11.3's risk is that a derived value gets
    trusted more than it deserves: the database's irreducible floor is about
    84 yr, the answer moves with N, and habit can move it 465. So this carries
    the sigma, the N, the distance to the nearest point, the point list with
    habits, and the counterfactual -- and the panel that shows it is obliged to
    show them too.
    """
    delta_r: Optional[float]
    delta_r_error: Optional[float]
    n: int
    points: List[ReservoirPoint] = field(default_factory=list)
    included_all: Optional[Combination] = None
    # The same arithmetic with the biased habit classes dropped. Computed
    # ALWAYS, whether or not anyone asked, because the alternative designs --
    # prompting on presence, excluding by default -- were measured and
    # rejected (see BIASED_FEEDING_CLASSES and FEEDING_SHIFT_WARN_YEARS).
    suspension_equivalent: Optional[Combination] = None
    nearest_km: Optional[float] = None
    farthest_km: Optional[float] = None
    excluded_no_error: List[int] = field(default_factory=list)
    excluded_by_user: List[int] = field(default_factory=list)
    feeding_counts: Dict[str, int] = field(default_factory=dict)
    feeding_raw: Dict[int, str] = field(default_factory=dict)
    snapshot_id: Optional[str] = None
    centre: Optional[Dict[str, float]] = None

    @property
    def feeding_shift(self) -> Optional[float]:
        """
        How far the counterfactual sits from the applied value, in years.

        None when there is no counterfactual to compare -- either nothing
        biased was in the set, or dropping it would have left too few points
        to weight.
        """
        if not self.included_all or not self.suspension_equivalent:
            return None
        if self.included_all.delta_r is None or self.suspension_equivalent.delta_r is None:
            return None
        return abs(self.included_all.delta_r - self.suspension_equivalent.delta_r)

    @property
    def feeding_shift_matters(self) -> bool:
        """Whether the panel must say so plainly rather than in small type."""
        shift = self.feeding_shift
        return shift is not None and shift >= FEEDING_SHIFT_WARN_YEARS

    def as_dict(self) -> Dict[str, object]:
        return {
            'delta_r': self.delta_r,
            'delta_r_error': self.delta_r_error,
            'n': self.n,
            'snapshot_id': self.snapshot_id,
            'centre': self.centre,
            'nearest_km': self.nearest_km,
            'farthest_km': self.farthest_km,
            'excluded_no_error': list(self.excluded_no_error),
            'excluded_by_user': list(self.excluded_by_user),
            'feeding': dict(self.feeding_counts),
            'feeding_raw': {str(k): v for k, v in self.feeding_raw.items()},
            'included_all': self.included_all.as_dict() if self.included_all else None,
            'suspension_equivalent': (
                self.suspension_equivalent.as_dict()
                if self.suspension_equivalent else None),
            'feeding_shift': self.feeding_shift,
            'feeding_shift_matters': self.feeding_shift_matters,
            'feeding_shift_warn_years': FEEDING_SHIFT_WARN_YEARS,
            'points': [
                {
                    'map_no': p.map_no,
                    'latitude': p.latitude,
                    'longitude': p.longitude,
                    'delta_r': p.delta_r,
                    'delta_r_error': p.delta_r_error,
                    'feeding': p.feeding,
                    'feeding_raw': p.feeding_raw,
                    'locality': p.locality,
                    'reference': p.reference,
                    'distance_km': p.distance_km,
                }
                for p in self.points
            ],
        }


def _combination_for(points: Sequence[ReservoirPoint],
                     drop_classes: Sequence[str]) -> Optional[Combination]:
    """
    combine() over the points whose habit is not in `drop_classes`, or None
    when that would leave too few to weight.

    None rather than a one-point "mean": with a single point the formula
    reports that point's own error as though it were a measure of agreement,
    which reads as precision where there is no evidence of any.
    """
    kept = [p for p in points if (p.feeding or 'unknown') not in drop_classes]
    weighted = [p for p in kept if p.delta_r is not None and p.delta_r_error]
    if len(weighted) < MIN_WEIGHTED_POINTS:
        return None
    return combine(kept)


def lookup(
    latitude: float,
    longitude: float,
    points: Sequence[ReservoirPoint],
    *,
    n: int = 10,
    snapshot_id: Optional[str] = None,
    max_km: Optional[float] = None,
    exclude_map_nos: Sequence[int] = (),
    suspension_only: bool = False,
) -> Lookup:
    """
    The Delta-R for a position, with everything needed to judge it.

    `suspension_only` applies the counterfactual as the answer rather than
    merely reporting it -- a deliberate choice the user makes, never a default.
    The default INCLUDES everything, which is what calib.org's published value
    does: an app whose default disagreed with the reference implementation for
    the same coordinates would throw away the reproducibility that reproducing
    their arithmetic exactly was for.

    Raises LookupRefused rather than returning a value computed from too
    little. The message names what would have had to be dropped, because "no
    answer" and "no answer because you excluded six of the eight points" are
    different things to the person reading it.
    """
    excluded_by_user = [int(m) for m in exclude_map_nos]
    candidates = nearest(latitude, longitude, points, n, max_km=max_km)
    chosen = [p for p in candidates if p.map_no not in set(excluded_by_user)]

    if not chosen:
        raise LookupRefused(
            'No reservoir measurements near this position'
            + (f' within {max_km:g} km' if max_km else '')
            + '. Widen the search, or enter a ΔR by hand.')

    included_all = combine(chosen)
    suspension_equivalent = _combination_for(chosen, BIASED_FEEDING_CLASSES)

    if suspension_only:
        if suspension_equivalent is None:
            dropped = sorted({
                p.feeding for p in chosen
                if (p.feeding or 'unknown') in BIASED_FEEDING_CLASSES
            })
            raise LookupRefused(
                'Excluding '
                + (', '.join(dropped) if dropped else 'the biased habit classes')
                + f' would leave fewer than {MIN_WEIGHTED_POINTS} points with an '
                'uncertainty to weight, and a weighted mean of one point is '
                'just that point. Raise N, widen the search, or include them.')
        applied = suspension_equivalent
    else:
        applied = included_all

    if applied.n < MIN_WEIGHTED_POINTS:
        raise LookupRefused(
            f'Only {applied.n} of the {len(chosen)} nearest points carry an '
            'uncertainty, and a weighted mean needs at least '
            f'{MIN_WEIGHTED_POINTS}. Raise N or widen the search.')

    # The points that actually entered the arithmetic -- not the ones offered.
    # A habit breakdown counting a point that was excluded for having no
    # uncertainty would describe a set the number did not come from.
    used = set(applied.map_nos)
    counts: Dict[str, int] = {}
    for point in chosen:
        if point.map_no not in used:
            continue
        habit = point.feeding or 'unknown'
        counts[habit] = counts.get(habit, 0) + 1

    distances = [p.distance_km for p in chosen if p.distance_km is not None]
    used_distances = [
        p.distance_km for p in chosen
        if p.map_no in used and p.distance_km is not None
    ]

    return Lookup(
        delta_r=applied.delta_r,
        delta_r_error=applied.delta_r_error,
        n=applied.n,
        points=chosen,
        included_all=included_all,
        suspension_equivalent=suspension_equivalent,
        # Nearest over every CANDIDATE, because §2.6's warning is about how
        # far the evidence is -- Tampa's nearest point is 163 km away -- and
        # that is true whether or not the point could be weighted.
        nearest_km=min(distances) if distances else None,
        farthest_km=max(used_distances) if used_distances else None,
        excluded_no_error=list(applied.excluded_no_error),
        excluded_by_user=excluded_by_user,
        feeding_counts=counts,
        feeding_raw={
            p.map_no: p.feeding_raw for p in chosen
            if p.map_no in used and p.feeding_raw
        },
        snapshot_id=snapshot_id,
        centre={'lat': latitude, 'lon': longitude},
    )


def provenance(result: Lookup, *, method: str = 'nearest_n',
               max_km: Optional[float] = None,
               suspension_only: bool = False) -> Dict[str, object]:
    """
    The `delta_r_source` record for a value this lookup produced.

    What makes a derived Delta-R citable: given the snapshot id and the map
    numbers, anyone can reproduce it -- including on calib.org's own page,
    which is the property that makes a private local mirror acceptable.

    The RAW feeding strings travel with the normalised counts, so the
    normalisation table can be corrected later without any stored provenance
    becoming a lie about what the source said.
    """
    counterfactual = None
    if result.suspension_equivalent and result.suspension_equivalent.delta_r is not None:
        counterfactual = {
            'delta_r': result.suspension_equivalent.delta_r,
            'delta_r_error': result.suspension_equivalent.delta_r_error,
            'n': result.suspension_equivalent.n,
        }
    return {
        'method': method,
        'snapshot_id': result.snapshot_id,
        'centre': result.centre,
        'n': result.n,
        'map_nos': list(result.included_all.map_nos) if result.included_all else [],
        'excluded_no_error': list(result.excluded_no_error),
        'excluded_by_user': list(result.excluded_by_user),
        'filters': {
            'feeding_preset': 'suspension_only' if suspension_only else None,
            'max_km': max_km,
        },
        'feeding': dict(result.feeding_counts),
        'feeding_raw': {str(k): v for k, v in result.feeding_raw.items()},
        'counterfactual_suspension_only': counterfactual,
        'nearest_km': result.nearest_km,
        'farthest_km': result.farthest_km,
        'computed_at': datetime.now(timezone.utc).isoformat(),
    }
