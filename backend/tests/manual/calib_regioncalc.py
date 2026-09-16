"""
Check `services/reservoir.combine` against calib.org's own calculator.

The three cases in `tests/test_reservoir.py` are published results, and they
pin the arithmetic against values typed into the test. This harness is the
other half: it picks random sets of map numbers out of a snapshot, asks
calib.org's `regioncalc.php` what IT makes of them, and compares to the
printed integer. Agreement over a couple of dozen random sets is what says
the formula is right in general rather than right on three cases that were
chosen because they discriminate.

**It reaches the network, which is why it is here and not in the suite.**
`regioncalc.php` is a courtesy interface for interactive use on somebody
else's Apache/PHP host. Running this is one request per set with a pause
between them; running it on every test run would be an abuse of it, and the
three published cases in the suite already guard against a regression.

Usage, from `backend/`:

    python -m tests.manual.calib_regioncalc            # 20 sets, from the fixture
    python -m tests.manual.calib_regioncalc --sets 5 --seed 7

------------------------------------------------------------------------
RESULT, run 2026-09-14 against the 2026-09-14 capture
------------------------------------------------------------------------

    20 sets of 2-8 points, seed 20260914, drawn from the 95 weighable
    points in tests/fixtures/calib_marine/query_response.html:

        20/20 exact, 0 mismatched, 0 unreadable

    Exact on BOTH integers -- the mean and its uncertainty -- across sets
    spanning uncertainties of 22 to 447 years, which is the range where the
    internal/scatter choice actually changes the answer.

Including the cases that discriminate the rule:

    map numbers            calib.org      combine()      internal / scatter
    995, 992               -239 ± 20      -239 ± 20      20.34 / 0.67
    2590, 1006, 2147       -165 ± 29      -165 ± 29      17.13 / 28.65
    2368,1470,2350,2024,834  90 ± 247       90 ± 247     13.15 / 246.91

The second is the one worth keeping: max(internal, scatter) gives 29 and
adding the two in quadrature gives 33. calib.org prints 29.
"""
import argparse
import random
import re
import sys
import time
from typing import Dict, List, Optional, Sequence, Tuple

from app.services import reservoir
from app.services.reservoir import ReservoirPoint


REGIONCALC_URL = 'http://calib.org/marine/regioncalc.php'

# What the page prints. Its markup is minimal and machine-generated:
#   npts: 2<br>Weighted Mean &Delta;R= -239<br>Uncertainty= 20
_MEAN = re.compile(r'Weighted\s+Mean\s*&Delta;R=\s*(-?\d+)', re.I)
_ERROR = re.compile(r'Uncertainty=\s*(-?\d+)', re.I)

# Between requests. Not a rate limit anyone published -- a courtesy, because
# this host owes this app nothing and a tight loop against it is how a
# courtesy interface stops being offered.
PAUSE_SECONDS = 2.0


def load_points(path: str) -> List[ReservoirPoint]:
    """The captured query response, parsed into points."""
    from app.services import reservoir_ingest as ingest

    with open(path, 'rb') as handle:
        records, declared = ingest.extract_records(handle.read())
    rows, report = ingest.records_to_rows(records, declared=declared)
    if not report.ok:
        raise SystemExit(f'The fixture did not parse: {report.errors[:3]}')
    return [
        ReservoirPoint(
            map_no=r.map_no, latitude=r.latitude, longitude=r.longitude,
            delta_r=r.delta_r, delta_r_error=r.delta_r_error,
            feeding=r.feeding, feeding_raw=r.feeding_raw, locality=r.locality,
        )
        for r in rows
    ]


def ask_calib(session, map_nos: Sequence[int]) -> Tuple[Optional[int], Optional[int]]:
    """What calib.org makes of these points, as the two integers it prints."""
    response = session.post(
        REGIONCALC_URL,
        data=[('MapNo[]', str(m)) for m in map_nos],
        timeout=90,
    )
    response.raise_for_status()
    body = response.text
    mean, error = _MEAN.search(body), _ERROR.search(body)
    if not mean or not error:
        return None, None
    return int(mean.group(1)), int(error.group(1))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sets', type=int, default=20)
    parser.add_argument('--seed', type=int, default=20260914)
    parser.add_argument('--min-points', type=int, default=2)
    parser.add_argument('--max-points', type=int, default=8)
    parser.add_argument(
        '--fixture',
        default='tests/fixtures/calib_marine/query_response.html',
        help='A captured query response to draw map numbers from.')
    args = parser.parse_args(argv)

    try:
        import requests
    except ImportError:
        raise SystemExit('requests is needed to reach calib.org')

    points = load_points(args.fixture)
    # Only points that can actually be weighted: a set containing one with no
    # uncertainty is a different question (this app excludes it and says so,
    # and calib.org's page does not offer it).
    weighable = [p for p in points if p.delta_r is not None and p.delta_r_error]
    by_map: Dict[int, ReservoirPoint] = {p.map_no: p for p in weighable}
    print(f'{len(weighable)} weighable points in {args.fixture}')

    rng = random.Random(args.seed)
    session = requests.Session()
    session.headers.update({
        'User-Agent': (
            'RadiCal/1.0 (manual verification of the weighted-mean formula; '
            'one request per set, paused between)'
        ),
    })

    agreed = disagreed = unreadable = 0
    for index in range(1, args.sets + 1):
        size = rng.randint(args.min_points, min(args.max_points, len(weighable)))
        chosen = rng.sample(sorted(by_map), size)

        theirs = ask_calib(session, chosen)
        if theirs[0] is None:
            print(f'{index:3d}. {chosen} -> calib.org said nothing readable')
            unreadable += 1
            time.sleep(PAUSE_SECONDS)
            continue

        ours = reservoir.combine([by_map[m] for m in chosen])
        mine = (round(ours.delta_r), round(ours.delta_r_error))

        if mine == theirs:
            agreed += 1
            print(f'{index:3d}. n={size} {theirs[0]:>6} ± {theirs[1]:<4} ok')
        else:
            disagreed += 1
            print(f'{index:3d}. n={size} MISMATCH  calib.org {theirs}  '
                  f'combine {mine}  maps={chosen}  '
                  f'internal={ours.internal:.2f} scatter={ours.scatter:.2f}')

        time.sleep(PAUSE_SECONDS)

    print()
    print(f'{agreed}/{args.sets} exact, {disagreed} mismatched, '
          f'{unreadable} unreadable')
    return 0 if disagreed == 0 and unreadable == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
