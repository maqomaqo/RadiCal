//! The arithmetic of a local marine reservoir offset taken from the reservoir
//! database: the feeding classes its organisms are filed under, distances
//! between points and the weighted mean of nearby Delta-R values. A port of
//! `normalise_feeding`, `haversine_km` and `combine` from
//! `backend/app/services/reservoir.py`. It needs no network: a mirror of the
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

/// One measurement, as a combination needs it.
#[derive(Debug, Clone, PartialEq)]
pub struct ReservoirPoint {
    pub map_no: i64,
    pub latitude: f64,
    pub longitude: f64,
    pub delta_r: Option<f64>,
    pub delta_r_error: Option<f64>,
}

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
