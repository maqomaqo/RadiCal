//! The arithmetic of a local marine reservoir offset taken from the reservoir
//! database: the feeding classes its organisms are filed under, distances
//! between points, the weighted mean of nearby Delta-R values, and the
//! lookup that derives a site's Delta-R with what is needed to judge it. A
//! port of `backend/app/services/reservoir.py`. It needs no network: a mirror of the
//! database is what lets a site's Delta-R be derived without sending its
//! coordinates to a third party on every lookup.

use crate::numeric::python_sum;
use crate::py_pow;

/// The classes a point's feeding habit is filed under. 'unknown' is a real
/// answer, not a suspect class.
pub const FEEDING_CLASSES: [&str; 7] = [
    "suspension",
    "deposit",
    "carnivore",
    "grazer",
    "migratory",
    "ambiguous",
    "unknown",
];

/// Every spelling the source has used (lowercased, stripped) -> class.
const FEEDING_SPELLINGS: [(&str, &str); 15] = [
    ("suspension", "suspension"),
    ("filter", "suspension"),
    ("deposit", "deposit"),
    ("carnivore", "carnivore"),
    ("predator", "carnivore"),
    ("scavenger", "carnivore"),
    ("omnivore", "carnivore"),
    ("herbivore", "grazer"),
    ("browser", "grazer"),
    ("algal grazer", "grazer"),
    ("commensalism", "grazer"),
    ("migratory", "migratory"),
    ("unknown", "unknown"),
    ("null", "unknown"),
    ("", "unknown"),
];

/// Compound judgements the source truncates at sixteen characters, matched
/// as prefixes: they cannot be filed under one class without deciding what
/// their author declined to.
pub const AMBIGUOUS_PREFIXES: [&str; 5] = [
    "deposit or suspe",
    "suspension/depos",
    "deposit feeder o",
    "herbivore/deposi",
    "carnivore/scaven",
];

/// Python's `str.strip().lower()`.
fn folded(raw: &str) -> String {
    raw.trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
        .to_lowercase()
}

/// One of [`FEEDING_CLASSES`], from whatever the source said. Prefixes
/// first: 'deposit or suspe' starts with 'deposit', and is a hedge.
pub fn normalise_feeding(raw: Option<&str>) -> &'static str {
    let Some(raw) = raw else { return "unknown" };
    let text = folded(raw);
    if text.is_empty() {
        return "unknown";
    }
    if AMBIGUOUS_PREFIXES.iter().any(|p| text.starts_with(p)) {
        return "ambiguous";
    }
    FEEDING_SPELLINGS
        .iter()
        .find(|(k, _)| *k == text)
        .map_or("unknown", |(_, v)| v)
}

/// Whether the source's spelling is one the table knows, as distinct from a
/// novel one that also normalises to 'unknown'.
pub fn is_recognised_feeding(raw: Option<&str>) -> bool {
    let Some(raw) = raw else { return true };
    let text = folded(raw);
    AMBIGUOUS_PREFIXES.iter().any(|p| text.starts_with(p))
        || FEEDING_SPELLINGS.iter().any(|(k, _)| *k == text)
}

/// Mean Earth radius, IUGG. The same figure calib.org's "nearest points"
/// ranks by, so the point list this produces is the one their page would show
/// -- which is what makes a derived value checkable against the source.
pub const EARTH_RADIUS_KM: f64 = 6371.0088;

/// One measurement, as the lookup needs it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ReservoirPoint {
    pub map_no: i64,
    pub latitude: f64,
    pub longitude: f64,
    pub delta_r: Option<f64>,
    pub delta_r_error: Option<f64>,
    pub feeding: Option<String>,
    pub feeding_raw: Option<String>,
    pub locality: Option<String>,
    pub reference: Option<String>,
    /// From the position asked about, once [`nearest`] has measured it.
    pub distance_km: Option<f64>,
}

/// The habit classes a "suspension-equivalent" counterfactual drops: the
/// ones whose carbon is not the local water's.
pub const BIASED_FEEDING_CLASSES: [&str; 3] = ["deposit", "migratory", "carnivore"];

/// When the counterfactual's distance from the applied value is said plainly
/// rather than in small type, in years.
pub const FEEDING_SHIFT_WARN_YEARS: f64 = 35.0;

/// Below this, a "weighted mean" is not one.
pub const MIN_WEIGHTED_POINTS: usize = 2;

/// A weighted mean and what went into it.
#[derive(Debug, Clone, PartialEq)]
pub struct Combination {
    pub delta_r: Option<f64>,
    pub delta_r_error: Option<f64>,
    pub n: usize,
    pub map_nos: Vec<i64>,
    /// The two candidates for the reported uncertainty, kept so the panel can
    /// say which one won. See [`combine`].
    pub internal: Option<f64>,
    pub scatter: Option<f64>,
    /// Points dropped because they carry no uncertainty and cannot be
    /// weighted.
    pub excluded_no_error: Vec<i64>,
}

/// Great-circle distance in kilometres.
///
/// Great-circle rather than planar because the database is global and a
/// Euclidean distance in degrees would rank points by a measure that means
/// different things at different latitudes -- at 70 degrees north a degree of
/// longitude is a third of what it is at the equator, so a planar "nearest"
/// would reach three times too far east and west of a site there.
pub fn haversine_km(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let (phi1, phi2) = (lat1.to_radians(), lat2.to_radians());
    let d_phi = phi2 - phi1;
    let d_lambda = (lon2 - lon1).to_radians();
    let a = py_pow((d_phi / 2.0).sin(), 2.0)
        + phi1.cos() * phi2.cos() * py_pow((d_lambda / 2.0).sin(), 2.0);
    2.0 * EARTH_RADIUS_KM * a.sqrt().min(1.0).asin()
}

/// Python's truthiness of an optional error: present and not zero.
fn has_error(e: Option<f64>) -> bool {
    e.is_some_and(|e| e != 0.0)
}

/// calib.org's own weighted mean, reproduced exactly.
///
/// ```text
/// w_i      = 1 / sigma_i^2
/// delta_R  = sum(w_i dR_i) / sum(w_i)
/// internal = 1 / sqrt(sum(w_i))
/// scatter  = sqrt( sum(w_i (dR_i - dR)^2) / sum(w_i) * n/(n-1) )   [0 at n=1]
/// sigma    = MAX(internal, scatter)
/// ```
///
/// The max is the part worth stating. Their explanation page cites Bevington
/// 1969 for "the maximum of the standard deviation and the weighted
/// uncertainty in the mean", and published results discriminate it from the
/// alternatives: for one published set of three the site prints 29, which is
/// max(17.13, 28.65); adding the two in quadrature would give 33. Reproducing
/// this exactly is what lets the app derive a value a reviewer can check on
/// calib.org's own page without the app calling calib.org.
///
/// A point with no uncertainty cannot be weighted and is excluded, by map
/// number, rather than being given an invented error.
pub fn combine(points: &[ReservoirPoint]) -> Combination {
    let usable: Vec<(i64, f64, f64)> = points
        .iter()
        .filter_map(|p| match (p.delta_r, p.delta_r_error) {
            (Some(dr), Some(e)) if e != 0.0 => Some((p.map_no, dr, e)),
            _ => None,
        })
        .collect();
    let excluded: Vec<i64> = points
        .iter()
        .filter(|p| p.delta_r.is_some() && !has_error(p.delta_r_error))
        .map(|p| p.map_no)
        .collect();

    if usable.is_empty() {
        return Combination {
            delta_r: None,
            delta_r_error: None,
            n: 0,
            map_nos: Vec::new(),
            internal: None,
            scatter: None,
            excluded_no_error: excluded,
        };
    }

    // Python's builtin sum(), which compensates; see python_sum.
    let weights: Vec<f64> = usable
        .iter()
        .map(|&(_, _, e)| 1.0 / py_pow(e, 2.0))
        .collect();
    let total = python_sum(weights.iter().copied());
    let mean = python_sum(weights.iter().zip(&usable).map(|(w, &(_, dr, _))| w * dr)) / total;

    let internal = 1.0 / total.sqrt();
    let n = usable.len();
    let scatter = if n > 1 {
        let spread = python_sum(
            weights
                .iter()
                .zip(&usable)
                .map(|(w, &(_, dr, _))| w * py_pow(dr - mean, 2.0)),
        ) / total;
        (spread * n as f64 / (n - 1) as f64).sqrt()
    } else {
        // One point has nothing to disagree with. Zero rather than None so the
        // max below is still the reported sigma -- which is then that point's
        // own error, as it should be.
        0.0
    };

    Combination {
        delta_r: Some(mean),
        // Python's max(internal, scatter): the first unless the second is
        // larger.
        delta_r_error: Some(if scatter > internal {
            scatter
        } else {
            internal
        }),
        n,
        map_nos: usable.iter().map(|&(m, _, _)| m).collect(),
        internal: Some(internal),
        scatter: Some(scatter),
        excluded_no_error: excluded,
    }
}

/// The `n` points closest to a position, nearest first, each carrying its
/// distance. A point with no Delta-R is not a candidate; one with a Delta-R
/// but no uncertainty is, and [`combine`] names it. Map number breaks a tie,
/// so a provenance record is reproducible.
pub fn nearest(
    latitude: f64,
    longitude: f64,
    points: &[ReservoirPoint],
    n: usize,
    max_km: Option<f64>,
) -> Vec<ReservoirPoint> {
    let mut with_distance: Vec<ReservoirPoint> = points
        .iter()
        .filter(|p| p.delta_r.is_some())
        .filter_map(|p| {
            let d = haversine_km(latitude, longitude, p.latitude, p.longitude);
            if max_km.is_some_and(|m| d > m) {
                return None;
            }
            Some(ReservoirPoint {
                distance_km: Some(d),
                ..p.clone()
            })
        })
        .collect();
    with_distance.sort_by(|a, b| {
        a.distance_km
            .unwrap_or(0.0)
            .total_cmp(&b.distance_km.unwrap_or(0.0))
            .then(a.map_no.cmp(&b.map_no))
    });
    with_distance.truncate(n);
    with_distance
}

/// A derived Delta-R and everything needed to judge it before applying it.
#[derive(Debug, Clone, PartialEq)]
pub struct Lookup {
    pub delta_r: Option<f64>,
    pub delta_r_error: Option<f64>,
    pub n: usize,
    pub points: Vec<ReservoirPoint>,
    pub included_all: Combination,
    /// The same arithmetic with the biased habit classes dropped; None when
    /// that leaves too few points to weight.
    pub suspension_equivalent: Option<Combination>,
    pub nearest_km: Option<f64>,
    pub farthest_km: Option<f64>,
    pub excluded_no_error: Vec<i64>,
    pub excluded_by_user: Vec<i64>,
    /// Habit class -> count, over the points the value came from, in the
    /// order first met.
    pub feeding_counts: Vec<(String, usize)>,
    /// Map number -> what the source said, for those points.
    pub feeding_raw: Vec<(i64, String)>,
    pub snapshot_id: Option<String>,
    pub centre: (f64, f64),
}

impl Lookup {
    /// How far the counterfactual sits from the applied value, in years.
    pub fn feeding_shift(&self) -> Option<f64> {
        let s = self.suspension_equivalent.as_ref()?;
        Some((self.included_all.delta_r? - s.delta_r?).abs())
    }

    /// Whether the panel must say so plainly.
    pub fn feeding_shift_matters(&self) -> bool {
        self.feeding_shift()
            .is_some_and(|s| s >= FEEDING_SHIFT_WARN_YEARS)
    }
}

fn habit(p: &ReservoirPoint) -> &str {
    p.feeding
        .as_deref()
        .filter(|f| !f.is_empty())
        .unwrap_or("unknown")
}

/// [`combine`] over the points whose habit is not biased, or None when that
/// leaves too few to weight (`_combination_for`).
fn without_biased(points: &[ReservoirPoint]) -> Option<Combination> {
    let kept: Vec<ReservoirPoint> = points
        .iter()
        .filter(|p| !BIASED_FEEDING_CLASSES.contains(&habit(p)))
        .cloned()
        .collect();
    let weighted = kept
        .iter()
        .filter(|p| p.delta_r.is_some() && has_error(p.delta_r_error))
        .count();
    (weighted >= MIN_WEIGHTED_POINTS).then(|| combine(&kept))
}

/// What a lookup asks for besides the position.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct LookupOptions {
    pub n: usize,
    pub snapshot_id: Option<String>,
    pub max_km: Option<f64>,
    pub exclude_map_nos: Vec<i64>,
    /// Apply the counterfactual as the answer: a choice, never a default.
    pub suspension_only: bool,
}

/// The Delta-R for a position, with everything needed to judge it; Err,
/// saying what it would have taken, rather than a value from too little.
pub fn lookup(
    latitude: f64,
    longitude: f64,
    points: &[ReservoirPoint],
    options: &LookupOptions,
) -> Result<Lookup, String> {
    let excluded_by_user = options.exclude_map_nos.clone();
    let chosen: Vec<ReservoirPoint> =
        nearest(latitude, longitude, points, options.n, options.max_km)
            .into_iter()
            .filter(|p| !excluded_by_user.contains(&p.map_no))
            .collect();
    if chosen.is_empty() {
        let within = options
            .max_km
            .filter(|m| *m != 0.0)
            .map(|m| format!(" within {} km", crate::pyfmt::g(m, 6)))
            .unwrap_or_default();
        return Err(format!(
            "No reservoir measurements near this position{within}. Widen the search, or enter a ΔR by hand."
        ));
    }
    let included_all = combine(&chosen);
    let suspension_equivalent = without_biased(&chosen);
    let applied = if options.suspension_only {
        match &suspension_equivalent {
            Some(s) => s.clone(),
            None => {
                let mut dropped: Vec<&str> = chosen
                    .iter()
                    .filter(|p| BIASED_FEEDING_CLASSES.contains(&habit(p)))
                    .filter_map(|p| p.feeding.as_deref())
                    .collect();
                dropped.sort();
                dropped.dedup();
                let what = if dropped.is_empty() {
                    "the biased habit classes".to_owned()
                } else {
                    dropped.join(", ")
                };
                return Err(format!(
                    "Excluding {what} would leave fewer than {MIN_WEIGHTED_POINTS} points with an uncertainty to weight, and a weighted mean of one point is just that point. Raise N, widen the search, or include them."
                ));
            }
        }
    } else {
        included_all.clone()
    };
    if applied.n < MIN_WEIGHTED_POINTS {
        return Err(format!(
            "Only {} of the {} nearest points carry an uncertainty, and a weighted mean needs at least {MIN_WEIGHTED_POINTS}. Raise N or widen the search.",
            applied.n,
            chosen.len()
        ));
    }
    let used = &applied.map_nos;
    let mut counts: Vec<(String, usize)> = Vec::new();
    for p in chosen.iter().filter(|p| used.contains(&p.map_no)) {
        let h = habit(p).to_owned();
        match counts.iter_mut().find(|(k, _)| *k == h) {
            Some((_, c)) => *c += 1,
            None => counts.push((h, 1)),
        }
    }
    let fold = |values: Vec<f64>, pick: fn(f64, f64) -> f64| values.into_iter().reduce(pick);
    let distances: Vec<f64> = chosen.iter().filter_map(|p| p.distance_km).collect();
    let used_distances: Vec<f64> = chosen
        .iter()
        .filter(|p| used.contains(&p.map_no))
        .filter_map(|p| p.distance_km)
        .collect();
    Ok(Lookup {
        delta_r: applied.delta_r,
        delta_r_error: applied.delta_r_error,
        n: applied.n,
        nearest_km: fold(distances, |a, b| if b < a { b } else { a }),
        farthest_km: fold(used_distances, |a, b| if b > a { b } else { a }),
        excluded_no_error: applied.excluded_no_error.clone(),
        excluded_by_user,
        feeding_counts: counts,
        feeding_raw: chosen
            .iter()
            .filter(|p| used.contains(&p.map_no))
            .filter_map(|p| {
                p.feeding_raw
                    .clone()
                    .filter(|r| !r.is_empty())
                    .map(|r| (p.map_no, r))
            })
            .collect(),
        points: chosen,
        included_all,
        suspension_equivalent,
        snapshot_id: options.snapshot_id.clone(),
        centre: (latitude, longitude),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_point_with_no_error_is_named_not_weighted() {
        let p = |map_no, delta_r, delta_r_error| ReservoirPoint {
            map_no,
            latitude: 0.0,
            longitude: 0.0,
            delta_r,
            delta_r_error,
            ..ReservoirPoint::default()
        };
        let c = combine(&[
            p(1, Some(10.0), Some(0.0)),
            p(2, None, None),
            p(3, Some(20.0), Some(5.0)),
        ]);
        assert_eq!(c.excluded_no_error, [1]);
        assert_eq!(c.map_nos, [3]);
        assert_eq!(c.delta_r, Some(20.0));
        assert_eq!(c.delta_r_error, Some(5.0));
    }
}
