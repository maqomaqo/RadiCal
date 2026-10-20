//! Wave of advance: how fast a thing moved across a landscape, and when it
//! set out. Port of `services/dispersal.py`.
//!
//! The unit of observation is **the Boundary that opens a site**, never a
//! date: a site's oldest date is a terminus ante quem for its arrival, and a
//! front fitted to oldest dates is late by an amount that varies with how
//! hard each site was dug. A front speed is a function of many modelled ages
//! at once, so every fit here runs per JOINT draw.
//!
//! For a site *i* with arrival age *t_i* (cal BP) at great-circle distance
//! *d_i* from an origin, `t_i = t0 - d_i / v + eps_i`, fitted by OLS per
//! draw. Older is a LARGER cal BP, so a front spreading outwards has a
//! negative slope and `v = -1/slope` positive; nothing here flips a sign to
//! make the answer look like a dispersal.
//!
//! The per-draw fits are a dot product over the sites, which NumPy hands to
//! BLAS; no fixed summation order reproduces that, so these numbers agree
//! with the Python's to rounding rather than to the bit. Everything else --
//! the site derivation, refusals, grids and orders -- is exact.

use std::collections::{HashMap, HashSet};

use crate::Measurement;
use crate::calibration::SIGMA_2;
use crate::intervals::{self, CalendarBand, MAX_BINS, SPREAD_PERCENTILES, YearsSummary};
use crate::model_tree::Element;
use crate::numeric::{
    arange, argsort_quicksort, linspace, mean, median, percentile, quantile, sum,
};
use crate::py_pow;
use crate::reservoir::{EARTH_RADIUS_KM, haversine_km};

/// How far a date may sit from the first date of its site and still be
/// grouped with it.
pub const SITE_SCATTER_KM: f64 = 5.0;
/// How far apart two dates in one Phase may be before it is refused: the
/// diameter of a group the rule above builds.
pub const SITE_SPREAD_LIMIT_KM: f64 = 2.0 * SITE_SCATTER_KM;
/// Sites below which no speed is reported at all.
pub const MIN_DISPERSAL_SITES: usize = 6;
/// What a site IS, appended to every "this model has N sites" refusal.
pub const SITE_DEFINITION: &str = "A site is an element opened by its own Boundary — a Sequence bracketed by a start and an end, one per place — whose dates carry coordinates. Dates with coordinates in one shared phase are not sites: group them into sites in the model editor, by location, context or position.";
/// Distinct site distances needed before a slope means anything.
pub const MIN_DISTINCT_DISTANCES: usize = 3;

/// One site: where it is, and which parameter carries its arrival.
///
/// `basis` travels with the site everywhere: 'boundary' (an arrival
/// estimate) or 'earliest_dated_event' (a terminus ante quem, for a group
/// calibration with no model).
#[derive(Debug, Clone, PartialEq)]
pub struct SiteSpec {
    pub element_id: String,
    pub name: String,
    pub parameter_id: String,
    pub basis: &'static str,
    pub latitude: Option<f64>,
    pub longitude: Option<f64>,
    pub date_ids: Vec<String>,
    /// Largest distance between any two of its dates, in km.
    pub scatter_km: Option<f64>,
    /// Why this site cannot be used, in the user's words.
    pub refusal: Option<String>,
}

impl SiteSpec {
    pub fn usable(&self) -> bool {
        self.refusal.is_none() && self.latitude.is_some() && self.longitude.is_some()
    }

    fn position(&self) -> (f64, f64) {
        (self.latitude.unwrap(), self.longitude.unwrap())
    }
}

/// Joint draws by parameter id.
pub use crate::intervals::Draws;

/// What a run was asked for: `origin` buys the wave as well as the trend
/// and the profile, which assume none.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct DispersalSpec {
    pub origin: Option<(f64, f64)>,
}

// --------------------------------------------------------------------------
// Deriving sites from the model tree
// --------------------------------------------------------------------------

fn name_of(element: &Element) -> String {
    match element.given_name() {
        Some(n) => n.to_owned(),
        None if !element.element_type.is_empty() => element.element_type.clone(),
        None => element.id.clone(),
    }
}

/// Every element a Boundary opens, mapped to that Boundary's id: a Sequence
/// by its first Boundary child, anything else by the innermost bracketing
/// pair of Boundary siblings.
fn opening_boundaries(root: &Element) -> HashMap<String, String> {
    fn walk(element: &Element, inherited: Option<&str>, opening: &mut HashMap<String, String>) {
        let children = &element.children;
        let boundary_at: Vec<usize> = children
            .iter()
            .enumerate()
            .filter(|(_, c)| c.is("boundary"))
            .map(|(i, _)| i)
            .collect();
        let is_sequence = element.is("sequence");
        if is_sequence && boundary_at.len() >= 2 {
            opening
                .entry(element.id.clone())
                .or_insert_with(|| children[boundary_at[0]].id.clone());
        } else if let Some(inherited) = inherited {
            opening
                .entry(element.id.clone())
                .or_insert_with(|| inherited.to_owned());
        }
        for (index, child) in children.iter().enumerate() {
            if child.is("boundary") {
                continue;
            }
            let mut own = inherited;
            if is_sequence {
                let before = boundary_at.iter().rev().find(|&&i| i < index);
                let after = boundary_at.iter().any(|&i| i > index);
                if let (Some(&b), true) = (before, after) {
                    own = Some(children[b].id.as_str());
                }
            }
            walk(child, own, opening);
        }
    }
    let mut opening = HashMap::new();
    walk(root, None, &mut opening);
    opening
}

fn subtree_ids(element: &Element, out: &mut Vec<String>) {
    out.push(element.id.clone());
    for child in &element.children {
        subtree_ids(child, out);
    }
}

/// (largest distance between any two points, the pair that produced it).
/// A pair of (lat, lon) positions.
type Pair = ((f64, f64), (f64, f64));

fn scatter(points: &[(f64, f64)]) -> (f64, Option<Pair>) {
    let (mut worst, mut pair) = (0.0, None);
    for i in 0..points.len() {
        for j in i + 1..points.len() {
            let km = haversine_km(points[i].0, points[i].1, points[j].0, points[j].1);
            if km > worst {
                worst = km;
                pair = Some((points[i], points[j]));
            }
        }
    }
    (worst, pair)
}

/// (west, east) edges of the shortest arc of longitude holding every value;
/// the east edge may exceed 180 when the arc crosses the antimeridian.
pub fn lon_extent(lons: &[f64]) -> (f64, f64) {
    let mut ordered = lons.to_vec();
    ordered.sort_by(f64::total_cmp);
    ordered.dedup_by(|a, b| a == b);
    if ordered.len() == 1 {
        return (ordered[0], ordered[0]);
    }
    let (mut west, mut east) = (ordered[0], ordered[ordered.len() - 1]);
    let mut largest = west + 360.0 - east;
    for i in 1..ordered.len() {
        let gap = ordered[i] - ordered[i - 1];
        if gap > largest {
            largest = gap;
            west = ordered[i];
            east = ordered[i - 1] + 360.0;
        }
    }
    (west, east)
}

/// Mean position on the sphere, via unit vectors, so it survives the
/// antimeridian.
pub fn centroid(points: &[(f64, f64)]) -> (f64, f64) {
    let (mut xs, mut ys, mut zs) = (0.0, 0.0, 0.0);
    for &(lat, lon) in points {
        let (phi, lam) = (lat.to_radians(), lon.to_radians());
        xs += phi.cos() * lam.cos();
        ys += phi.cos() * lam.sin();
        zs += phi.sin();
    }
    let n = points.len() as f64;
    let (xs, ys, zs) = (xs / n, ys / n, zs / n);
    let hyp = xs.hypot(ys);
    (zs.atan2(hyp).to_degrees(), ys.atan2(xs).to_degrees())
}

/// The model tree read as a set of places, each with the parameter that
/// dates its arrival: outermost first, descending only where a candidate's
/// dates are too scattered to be one place, and refusing where the descent
/// runs out of structure.
pub fn derive_sites(root: Option<&Element>, measurements: &[Measurement]) -> Vec<SiteSpec> {
    let Some(root) = root else { return Vec::new() };
    let mut coords: HashMap<&str, (f64, f64)> = HashMap::new();
    let mut by_element: HashMap<&str, Vec<&str>> = HashMap::new();
    for m in measurements {
        if let (Some(lat), Some(lon)) = (m.latitude, m.longitude) {
            coords.insert(&m.id, (lat, lon));
        }
        if let Some(e) = m.element_id.as_deref().filter(|e| !e.is_empty()) {
            by_element.entry(e).or_default().push(&m.id);
        }
    }
    let opening = opening_boundaries(root);
    let dates_under = |element: &Element| -> Vec<String> {
        let mut ids = Vec::new();
        subtree_ids(element, &mut ids);
        ids.iter()
            .flat_map(|eid| by_element.get(eid.as_str()).cloned().unwrap_or_default())
            .map(str::to_owned)
            .collect()
    };

    struct Walk<'a, F> {
        opening: &'a HashMap<String, String>,
        coords: &'a HashMap<&'a str, (f64, f64)>,
        dates_under: F,
        sites: Vec<SiteSpec>,
    }
    impl<F: Fn(&Element) -> Vec<String>> Walk<'_, F> {
        fn consider(&mut self, element: &Element) {
            let children: Vec<&Element> = element
                .children
                .iter()
                .filter(|c| !c.is("boundary") && !c.is("hiatus"))
                .collect();
            let Some(boundary) = self.opening.get(&element.id).cloned() else {
                for child in children {
                    self.consider(child);
                }
                return;
            };
            let date_ids = (self.dates_under)(element);
            let placed: Vec<(String, (f64, f64))> = date_ids
                .iter()
                .filter_map(|d| self.coords.get(d.as_str()).map(|p| (d.clone(), *p)))
                .collect();
            let descendable: Vec<&Element> = children
                .iter()
                .copied()
                .filter(|c| self.opening.contains_key(&c.id))
                .collect();
            if placed.is_empty() {
                for child in descendable {
                    self.consider(child);
                }
                return;
            }
            let points: Vec<(f64, f64)> = placed.iter().map(|(_, p)| *p).collect();
            let (spread, pair) = scatter(&points);
            if spread > SITE_SPREAD_LIMIT_KM {
                if !descendable.is_empty() {
                    for child in descendable {
                        self.consider(child);
                    }
                    return;
                }
                let ((lat_a, lon_a), (lat_b, lon_b)) = pair.expect("a spread has a pair");
                let name = name_of(element);
                self.sites.push(SiteSpec {
                    element_id: element.id.clone(),
                    name: name.clone(),
                    parameter_id: boundary,
                    basis: "boundary",
                    latitude: None,
                    longitude: None,
                    date_ids,
                    scatter_km: Some(spread),
                    refusal: Some(format!(
                        "\"{name}\" holds dates {spread:.1} km apart ({lat_a:.4}, {lon_a:.4}) and ({lat_b:.4}, {lon_b:.4}). Dates from two sites in one phase is a modelling error, and placing the phase between them would hide it. Split them into separate phases, or correct the coordinates."
                    )),
                });
                return;
            }
            // Descend past a wrapper that holds every placed date under a
            // different opening Boundary: the same place, more tightly bounded.
            let placed_ids: HashSet<&str> = placed.iter().map(|(d, _)| d.as_str()).collect();
            let covering: Vec<&Element> = descendable
                .iter()
                .copied()
                .filter(|c| {
                    self.opening.get(&c.id) != Some(&boundary) && {
                        let under = (self.dates_under)(c);
                        let under: HashSet<&str> = under.iter().map(String::as_str).collect();
                        placed_ids.is_subset(&under)
                    }
                })
                .collect();
            if covering.len() == 1 {
                self.consider(covering[0]);
                return;
            }
            let (lat, lon) = centroid(&points);
            self.sites.push(SiteSpec {
                element_id: element.id.clone(),
                name: name_of(element),
                parameter_id: boundary,
                basis: "boundary",
                latitude: Some(lat),
                longitude: Some(lon),
                date_ids: placed.into_iter().map(|(d, _)| d).collect(),
                scatter_km: Some(spread),
                refusal: None,
            });
        }
    }
    let mut walk = Walk {
        opening: &opening,
        coords: &coords,
        dates_under,
        sites: Vec::new(),
    };
    walk.consider(root);
    walk.sites
}

/// The fallback for a group calibration: dates grouped by position, each
/// group's site its EARLIEST DATED EVENT (a terminus ante quem, labelled so),
/// earliest decided on the median of the draws.
pub fn derive_sites_from_dates(measurements: &[Measurement], draws: &Draws) -> Vec<SiteSpec> {
    struct Member<'a> {
        id: &'a str,
        lat: f64,
        lon: f64,
        name: &'a str,
    }
    let mut groups: Vec<Vec<Member>> = Vec::new();
    for m in measurements {
        let (Some(lat), Some(lon)) = (m.latitude, m.longitude) else {
            continue;
        };
        if !draws.contains_key(&m.id) {
            continue;
        }
        let member = Member {
            id: &m.id,
            lat,
            lon,
            name: m.name_or_id(),
        };
        match groups
            .iter_mut()
            .find(|g| haversine_km(lat, lon, g[0].lat, g[0].lon) <= SITE_SCATTER_KM)
        {
            Some(group) => group.push(member),
            None => groups.push(vec![member]),
        }
    }
    groups
        .iter()
        .map(|group| {
            // max(): the first of equals.
            let mut earliest = &group[0];
            let mut best = median(&draws[group[0].id]);
            for g in &group[1..] {
                let m = median(&draws[g.id]);
                if m > best {
                    best = m;
                    earliest = g;
                }
            }
            let points: Vec<(f64, f64)> = group.iter().map(|g| (g.lat, g.lon)).collect();
            let (lat, lon) = centroid(&points);
            SiteSpec {
                element_id: earliest.id.to_owned(),
                name: earliest.name.to_owned(),
                parameter_id: earliest.id.to_owned(),
                basis: "earliest_dated_event",
                latitude: Some(lat),
                longitude: Some(lon),
                date_ids: group.iter().map(|g| g.id.to_owned()).collect(),
                scatter_km: Some(scatter(&points).0),
                refusal: None,
            }
        })
        .collect()
}

// --------------------------------------------------------------------------
// Shared arithmetic
// --------------------------------------------------------------------------

/// `numpy.round(x, 6)`: scaled, rounded half to even, scaled back.
fn np_round6(x: f64) -> f64 {
    (x * 1e6).round_ties_even() / 1e6
}

fn distinct_distances(distances: &[f64]) -> usize {
    let mut rounded: Vec<f64> = distances.iter().map(|&d| np_round6(d)).collect();
    rounded.sort_by(f64::total_cmp);
    rounded.dedup_by(|a, b| a == b);
    rounded.len()
}

/// A reporting resolution for km/yr: the largest power of ten dividing the
/// central spread into about MAX_BINS pieces.
fn speed_step(values: &[f64]) -> Option<f64> {
    let low = percentile(values, SPREAD_PERCENTILES.0);
    let high = percentile(values, SPREAD_PERCENTILES.1);
    let spread = high - low;
    if !spread.is_finite() || spread <= 0.0 {
        return None;
    }
    Some(py_pow(10.0, (spread / MAX_BINS).log10().floor()))
}

/// (J, n) ages, one row per joint draw, read off the SAME draw for every
/// site.
fn joint_ages(usable: &[&SiteSpec], draws: &Draws) -> Vec<Vec<f64>> {
    let columns: Vec<&Vec<f64>> = usable.iter().map(|s| &draws[&s.parameter_id]).collect();
    let j = columns[0].len();
    (0..j)
        .map(|row| columns.iter().map(|c| c[row]).collect())
        .collect()
}

/// `a.mean(axis=0)` of a (J, n) array: rows added in order.
fn column_means(rows: &[Vec<f64>]) -> Vec<f64> {
    let mut acc = rows[0].clone();
    for row in &rows[1..] {
        for (a, v) in acc.iter_mut().zip(row) {
            *a += v;
        }
    }
    let n = rows.len() as f64;
    acc.into_iter().map(|a| a / n).collect()
}

/// `a.std(axis=0, ddof=1)` of a (J, n) array.
fn column_sds(rows: &[Vec<f64>]) -> Vec<f64> {
    let means = column_means(rows);
    let squared: Vec<Vec<f64>> = rows
        .iter()
        .map(|r| {
            r.iter()
                .zip(&means)
                .map(|(v, m)| (v - m) * (v - m))
                .collect()
        })
        .collect();
    let mut acc = squared[0].clone();
    for row in &squared[1..] {
        for (a, v) in acc.iter_mut().zip(row) {
            *a += v;
        }
    }
    let dof = (rows.len() - 1) as f64;
    acc.into_iter().map(|a| (a / dof).sqrt()).collect()
}

fn dot(a: &[f64], b: &[f64]) -> f64 {
    a.iter().zip(b).fold(0.0, |s, (x, y)| s + x * y)
}

/// (slope, intercept, residual sum of squares) per draw, in closed form.
fn ols_per_draw(distances: &[f64], ages: &[Vec<f64>]) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
    let d_mean = mean(distances);
    let centred: Vec<f64> = distances.iter().map(|d| d - d_mean).collect();
    let denominator = sum(&centred.iter().map(|c| c * c).collect::<Vec<_>>());
    let mut slopes = Vec::with_capacity(ages.len());
    let mut intercepts = Vec::with_capacity(ages.len());
    let mut rss = Vec::with_capacity(ages.len());
    for row in ages {
        let mean_t = mean(row);
        let deviations: Vec<f64> = row.iter().map(|t| t - mean_t).collect();
        let slope = dot(&deviations, &centred) / denominator;
        let intercept = mean_t - slope * d_mean;
        let squares: Vec<f64> = row
            .iter()
            .zip(distances)
            .map(|(t, d)| {
                let r = t - (intercept + slope * d);
                r * r
            })
            .collect();
        slopes.push(slope);
        intercepts.push(intercept);
        rss.push(sum(&squares));
    }
    (slopes, intercepts, rss)
}

/// One site's mean and spread of residual, signed so positive is OLDER than
/// the fit predicts.
#[derive(Debug, Clone, PartialEq)]
pub struct Residual {
    pub element_id: String,
    pub name: String,
    /// Wave only.
    pub distance_km: Option<f64>,
    pub residual_years: f64,
    pub residual_sd: f64,
}

/// Residuals per site, largest (oldest-than-predicted) first.
fn residual_table(
    usable: &[&SiteSpec],
    ages: &[Vec<f64>],
    fitted: &[Vec<f64>],
    distances: Option<&[f64]>,
) -> Vec<Residual> {
    let diffs: Vec<Vec<f64>> = ages
        .iter()
        .zip(fitted)
        .map(|(a, f)| a.iter().zip(f).map(|(x, y)| x - y).collect())
        .collect();
    let means = column_means(&diffs);
    let sds = column_sds(&diffs);
    let negated: Vec<f64> = means.iter().map(|m| -m).collect();
    argsort_quicksort(&negated)
        .into_iter()
        .map(|i| Residual {
            element_id: usable[i].element_id.clone(),
            name: usable[i].name.clone(),
            distance_km: distances.map(|d| d[i]),
            residual_years: means[i],
            residual_sd: sds[i],
        })
        .collect()
}

fn usable_sites<'s>(sites: &'s [SiteSpec], draws: &Draws) -> Vec<&'s SiteSpec> {
    sites
        .iter()
        .filter(|s| s.usable() && draws.contains_key(&s.parameter_id))
        .collect()
}

fn first_refusal(sites: &[SiteSpec]) -> Option<String> {
    sites.iter().find_map(|s| s.refusal.clone())
}

// --------------------------------------------------------------------------
// Model 1 -- the wave from a placed origin
// --------------------------------------------------------------------------

/// A wave-of-advance fit, or why there is none.
#[derive(Debug, Clone, PartialEq)]
pub struct Wave {
    pub origin: (f64, f64),
    pub sites: Vec<SiteSpec>,
    pub n_sites: usize,
    pub refused: Option<String>,
    pub speed: Option<YearsSummary>,
    pub t0: Option<CalendarBand>,
    pub residuals: Vec<Residual>,
    pub basis: Option<&'static str>,
    pub distances: Option<Vec<f64>>,
    pub draws: Option<usize>,
    /// Draws whose slope was zero, excluded from the speed and counted.
    pub degenerate_draws: Option<usize>,
}

/// A wave-of-advance fit, per joint draw, from an origin the caller chose.
/// A refusal is a result, not an error.
pub fn fit_wave(sites: &[SiteSpec], draws: &Draws, origin: (f64, f64)) -> Wave {
    let usable = usable_sites(sites, draws);
    let block = |refused: Option<String>| Wave {
        origin,
        sites: sites.to_vec(),
        n_sites: usable.len(),
        refused,
        speed: None,
        t0: None,
        residuals: Vec::new(),
        basis: usable.first().map(|s| s.basis),
        distances: None,
        draws: None,
        degenerate_draws: None,
    };
    if let Some(refusal) = first_refusal(sites) {
        return block(Some(refusal));
    }
    if usable.len() < MIN_DISPERSAL_SITES {
        return block(Some(format!(
            "A wave-of-advance fit needs at least {MIN_DISPERSAL_SITES} sites and this model has {}. A straight line through fewer points returns a speed and an interval whichever way the dates fall, with too few residuals left over for the data to contradict it. The number is withheld rather than shown with a caveat. {SITE_DEFINITION}",
            usable.len()
        )));
    }
    let distances: Vec<f64> = usable
        .iter()
        .map(|s| {
            let (lat, lon) = s.position();
            haversine_km(origin.0, origin.1, lat, lon)
        })
        .collect();
    if distinct_distances(&distances) < MIN_DISTINCT_DISTANCES {
        return block(Some(
            "The sites lie at too few distinct distances from this origin for a gradient to be estimated -- they are close to a single circle around it. Move the origin, or place it where the hypothesis actually puts it.".to_owned(),
        ));
    }
    let ages = joint_ages(&usable, draws);
    let (slope, t0, _rss) = ols_per_draw(&distances, &ages);
    let speed: Vec<f64> = slope.iter().map(|s| -1.0 / s).collect();
    let finite: Vec<f64> = speed.iter().copied().filter(|v| v.is_finite()).collect();
    let fitted: Vec<Vec<f64>> = slope
        .iter()
        .zip(&t0)
        .map(|(s, t)| distances.iter().map(|d| t + s * d).collect())
        .collect();
    let residuals = residual_table(&usable, &ages, &fitted, Some(&distances));
    let step = if finite.is_empty() {
        None
    } else {
        speed_step(&finite)
    };
    Wave {
        speed: intervals::summarise(&speed, step),
        t0: Some(intervals::calendar_band(&t0)),
        residuals,
        distances: Some(distances),
        draws: Some(ages.len()),
        degenerate_draws: Some(ages.len() - finite.len()),
        ..block(None)
    }
}

// --------------------------------------------------------------------------
// Model 2 -- the trend surface
// --------------------------------------------------------------------------

/// How square a set of sites must be before a plane through them means
/// anything: across-axis spread over along-axis spread.
pub const MIN_TREND_ASPECT: f64 = 0.05;
/// Isochrons drawn across the fitted surface.
pub const ISOCHRON_COUNT: usize = 5;
/// Below this spread of fitted ages, in years, the surface is flat.
pub const ISOCHRON_MIN_SPAN_YEARS: f64 = 1.0;
/// How far apart, at most, the points along a drawn isochron are.
pub const ISOCHRON_SAMPLE_KM: f64 = 250.0;

/// Site positions as km east and north of their own centroid, on a Lambert
/// azimuthal equal-area projection centred there.
fn lambert_azimuthal(sites: &[&SiteSpec]) -> (Vec<f64>, Vec<f64>, (f64, f64)) {
    let points: Vec<(f64, f64)> = sites.iter().map(|s| s.position()).collect();
    let centre = centroid(&points);
    let (lat0, lon0) = (centre.0.to_radians(), centre.1.to_radians());
    let mut x = Vec::with_capacity(points.len());
    let mut y = Vec::with_capacity(points.len());
    for &(lat, lon) in &points {
        let (phi, lam) = (lat.to_radians(), lon.to_radians());
        let cos_c = lat0.sin() * phi.sin() + lat0.cos() * phi.cos() * (lam - lon0).cos();
        let k = (2.0 / (1.0 + cos_c).max(1e-12)).max(0.0).sqrt();
        x.push(EARTH_RADIUS_KM * k * phi.cos() * (lam - lon0).sin());
        y.push(
            EARTH_RADIUS_KM
                * k
                * (lat0.cos() * phi.sin() - lat0.sin() * phi.cos() * (lam - lon0).cos()),
        );
    }
    (x, y, centre)
}

/// A direction's mean and 95% interval, on the circle.
#[derive(Debug, Clone, PartialEq)]
pub struct Bearing {
    pub mean_deg: f64,
    pub hpd_95_deg: [f64; 2],
    pub width_deg: f64,
    /// The mean resultant length R: near zero, the mean is an artefact.
    pub concentration: f64,
}

/// Python's float `%` against a positive divisor.
fn py_mod(x: f64, m: f64) -> f64 {
    let r = x % m;
    if r != 0.0 && r < 0.0 {
        r + m
    } else if r == 0.0 {
        0.0
    } else {
        r
    }
}

fn circular_summary(angles: &[f64]) -> Bearing {
    let radians: Vec<f64> = angles.iter().map(|a| a.to_radians()).collect();
    let sin_mean = mean(&radians.iter().map(|r| r.sin()).collect::<Vec<_>>());
    let cos_mean = mean(&radians.iter().map(|r| r.cos()).collect::<Vec<_>>());
    let concentration = sin_mean.hypot(cos_mean);
    let mean_deg = py_mod(sin_mean.atan2(cos_mean).to_degrees(), 360.0);
    let deviation: Vec<f64> = angles
        .iter()
        .map(|a| py_mod(a - mean_deg + 180.0, 360.0) - 180.0)
        .collect();
    let tail = (1.0 - SIGMA_2) / 2.0;
    let low = quantile(&deviation, tail);
    let high = quantile(&deviation, 1.0 - tail);
    Bearing {
        mean_deg,
        hpd_95_deg: [
            py_mod(mean_deg + low, 360.0),
            py_mod(mean_deg + high, 360.0),
        ],
        width_deg: high - low,
        concentration,
    }
}

/// One line of equal arrival age, as lat/lon points.
#[derive(Debug, Clone, PartialEq)]
pub struct Isochron {
    pub age: f64,
    pub points: Vec<(f64, f64)>,
}

/// A trend-surface fit, or why there is none.
#[derive(Debug, Clone, PartialEq)]
pub struct Trend {
    pub sites: Vec<SiteSpec>,
    pub n_sites: usize,
    pub refused: Option<String>,
    pub speed: Option<YearsSummary>,
    pub azimuth: Option<Bearing>,
    pub centre: Option<(f64, f64)>,
    pub isochrons: Vec<Isochron>,
    pub residuals: Vec<Residual>,
    pub basis: Option<&'static str>,
    pub draws: Option<usize>,
    pub degenerate_draws: Option<usize>,
}

/// The singular values of an (n, 2) matrix, larger first.
fn singular_values_2(a: &[[f64; 2]]) -> (f64, f64) {
    let (mut p, mut q, mut r) = (0.0, 0.0, 0.0);
    for row in a {
        p += row[0] * row[0];
        q += row[0] * row[1];
        r += row[1] * row[1];
    }
    // Eigenvalues of the 2x2 Gram matrix [[p, q], [q, r]].
    let half_trace = (p + r) / 2.0;
    let gap = ((p - r) / 2.0).hypot(q);
    let big = half_trace + gap;
    let small = (p * r - q * q) / big;
    (big.max(0.0).sqrt(), small.max(0.0).sqrt())
}

/// The (3, n) least-squares solver for the design [1, x, y]: what
/// `np.linalg.pinv(design)` is for a design of full column rank.
fn pseudo_inverse(x: &[f64], y: &[f64]) -> Vec<[f64; 3]> {
    let n = x.len() as f64;
    let (sx, sy) = (x.iter().sum::<f64>(), y.iter().sum::<f64>());
    let sxx: f64 = x.iter().map(|v| v * v).sum();
    let syy: f64 = y.iter().map(|v| v * v).sum();
    let sxy: f64 = x.iter().zip(y).map(|(a, b)| a * b).sum();
    let m = [[n, sx, sy], [sx, sxx, sxy], [sy, sxy, syy]];
    let det = m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    let cof = |r: usize, c: usize| {
        let rows: Vec<usize> = (0..3).filter(|&i| i != r).collect();
        let cols: Vec<usize> = (0..3).filter(|&i| i != c).collect();
        let minor =
            m[rows[0]][cols[0]] * m[rows[1]][cols[1]] - m[rows[0]][cols[1]] * m[rows[1]][cols[0]];
        if (r + c).is_multiple_of(2) {
            minor
        } else {
            -minor
        }
    };
    // inverse = adjugate / det; symmetric, so the cofactor matrix is its own
    // transpose.
    let inv: Vec<[f64; 3]> = (0..3)
        .map(|r| [cof(r, 0) / det, cof(r, 1) / det, cof(r, 2) / det])
        .collect();
    x.iter()
        .zip(y)
        .map(|(&xi, &yi)| {
            let row = [1.0, xi, yi];
            [dot(&inv[0], &row), dot(&inv[1], &row), dot(&inv[2], &row)]
        })
        .collect()
}

/// A plane through arrival age against position, `t = a + b*x + c*y`,
/// assuming no origin. The direction of ADVANCE is down the gradient.
pub fn fit_trend(sites: &[SiteSpec], draws: &Draws) -> Trend {
    let usable = usable_sites(sites, draws);
    let block = |refused: Option<String>| Trend {
        sites: sites.to_vec(),
        n_sites: usable.len(),
        refused,
        speed: None,
        azimuth: None,
        centre: None,
        isochrons: Vec::new(),
        residuals: Vec::new(),
        basis: usable.first().map(|s| s.basis),
        draws: None,
        degenerate_draws: None,
    };
    if let Some(refusal) = first_refusal(sites) {
        return block(Some(refusal));
    }
    if usable.len() < MIN_DISPERSAL_SITES {
        return block(Some(format!(
            "A trend surface needs at least {MIN_DISPERSAL_SITES} sites and this model has {}. A plane through three points passes through them exactly, whatever they are, and reports a gradient with nothing left over to contradict it. {SITE_DEFINITION}",
            usable.len()
        )));
    }
    let (x, y, centre) = lambert_azimuthal(&usable);
    let (xm, ym) = (mean(&x), mean(&y));
    let spread: Vec<[f64; 2]> = x.iter().zip(&y).map(|(a, b)| [a - xm, b - ym]).collect();
    let (along, across) = singular_values_2(&spread);
    if along <= 0.0 || across / along < MIN_TREND_ASPECT {
        return block(Some(
            "These sites lie too close to a straight line for a plane through them to be determined: the gradient along the line is fixed and the gradient across it would come from rounding rather than from the dates. A wave of advance from a placed origin is the model for a transect; a trend surface needs sites spread in two directions.".to_owned(),
        ));
    }
    let ages = joint_ages(&usable, draws);
    let solver = pseudo_inverse(&x, &y);
    let coefficients: Vec<[f64; 3]> = ages
        .iter()
        .map(|row| {
            let mut out = [0.0; 3];
            for (k, slot) in out.iter_mut().enumerate() {
                *slot = row.iter().zip(&solver).fold(0.0, |s, (t, p)| s + t * p[k]);
            }
            out
        })
        .collect();
    let speed: Vec<f64> = coefficients
        .iter()
        .map(|c| 1.0 / c[1].hypot(c[2]))
        .collect();
    let finite: Vec<f64> = speed.iter().copied().filter(|v| v.is_finite()).collect();
    let azimuth: Vec<f64> = coefficients
        .iter()
        .zip(&speed)
        .filter(|(_, s)| s.is_finite())
        .map(|(c, _)| py_mod((-c[1]).atan2(-c[2]).to_degrees(), 360.0))
        .collect();
    let fitted: Vec<Vec<f64>> = coefficients
        .iter()
        .map(|c| {
            x.iter()
                .zip(&y)
                .map(|(xi, yi)| c[0] + c[1] * xi + c[2] * yi)
                .collect()
        })
        .collect();
    let residuals = residual_table(&usable, &ages, &fitted, None);
    let step = if finite.is_empty() {
        None
    } else {
        speed_step(&finite)
    };
    let column = |k: usize| coefficients.iter().map(|c| c[k]).collect::<Vec<_>>();
    Trend {
        speed: intervals::summarise(&finite, step),
        azimuth: Some(circular_summary(&azimuth)),
        centre: Some(centre),
        isochrons: isochrons(
            median(&column(0)),
            median(&column(1)),
            median(&column(2)),
            &x,
            &y,
            centre,
        ),
        residuals,
        draws: Some(ages.len()),
        degenerate_draws: Some(ages.len() - finite.len()),
        ..block(None)
    }
}

/// A point in the local plane back to latitude and longitude.
pub fn unproject(x: f64, y: f64, centre: (f64, f64)) -> (f64, f64) {
    let (lat0, lon0) = (centre.0.to_radians(), centre.1.to_radians());
    let rho = x.hypot(y);
    if rho < 1e-9 {
        return centre;
    }
    let angle = 2.0 * (rho / (2.0 * EARTH_RADIUS_KM)).min(1.0).asin();
    let (sin_c, cos_c) = (angle.sin(), angle.cos());
    let lat = (cos_c * lat0.sin() + y * sin_c * lat0.cos() / rho).asin();
    let lon = lon0 + (x * sin_c).atan2(rho * lat0.cos() * cos_c - y * lat0.sin() * sin_c);
    (
        lat.to_degrees(),
        py_mod(lon.to_degrees() + 180.0, 360.0) - 180.0,
    )
}

/// A spacing a reader would have chosen: 1, 2 or 5 times a power of ten.
pub fn round_step(raw: f64) -> f64 {
    if !raw.is_finite() || raw <= 0.0 {
        return 1.0;
    }
    let magnitude = py_pow(10.0, raw.log10().floor());
    for multiple in [1.0, 2.0, 5.0] {
        if raw <= multiple * magnitude {
            return multiple * magnitude;
        }
    }
    10.0 * magnitude
}

/// Lines of equal arrival age across the median plane, at round ages, each
/// sampled along the sites' own extent in its direction.
fn isochrons(a: f64, b: f64, c: f64, x: &[f64], y: &[f64], centre: (f64, f64)) -> Vec<Isochron> {
    let gradient_sq = b * b + c * c;
    if gradient_sq <= 0.0 {
        return Vec::new();
    }
    let fitted: Vec<f64> = x
        .iter()
        .zip(y)
        .map(|(xi, yi)| a + b * xi + c * yi)
        .collect();
    let low = fitted.iter().copied().fold(f64::INFINITY, f64::min);
    let high = fitted.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    if !low.is_finite() || high - low < ISOCHRON_MIN_SPAN_YEARS {
        return Vec::new();
    }
    let step = round_step((high - low) / ISOCHRON_COUNT as f64);
    let first = (low / step).ceil() * step;
    let norm = gradient_sq.sqrt();
    let along = [-c / norm, b / norm];
    let positions: Vec<f64> = x
        .iter()
        .zip(y)
        .map(|(xi, yi)| xi * along[0] + yi * along[1])
        .collect();
    let pmin = positions.iter().copied().fold(f64::INFINITY, f64::min);
    let pmax = positions.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let extent = pmax - pmin;
    let margin = 0.125 * extent.max(1.0);
    let (start, stop) = (pmin - margin, pmax + margin);
    let mut samples = 9.max(2 * (extent / ISOCHRON_SAMPLE_KM) as usize + 1);
    if samples % 2 == 0 {
        samples += 1;
    }
    let ts = linspace(start, stop, samples);
    let mut lines = Vec::new();
    let mut age = first;
    while age <= high && lines.len() < ISOCHRON_COUNT + 1 {
        let base = [b * (age - a) / gradient_sq, c * (age - a) / gradient_sq];
        lines.push(Isochron {
            age,
            points: ts
                .iter()
                .map(|t| unproject(base[0] + t * along[0], base[1] + t * along[1], centre))
                .collect(),
        });
        age += step;
    }
    lines
}

// --------------------------------------------------------------------------
// Profiling the origin
// --------------------------------------------------------------------------

/// The candidate grid's step, in degrees.
pub const ORIGIN_GRID_DEG: f64 = 1.0;
/// How far beyond the nearest site a candidate origin may lie, in km.
pub const ORIGIN_REACH_KM: f64 = 700.0;
/// Extra reach beyond half the longest spanning-tree link, so the zone's
/// discs join with room for grid cells.
pub const ORIGIN_NECK_KM: f64 = 150.0;
/// One degree of latitude, in km.
pub const KM_PER_DEG_LAT: f64 = 111.2;
/// Most candidates to evaluate before the step widens.
pub const MAX_ORIGIN_CANDIDATES: usize = 2500;
/// The support contour the reported region is drawn at.
pub const ORIGIN_SUPPORT_CUTOFF: f64 = 0.125;
/// Candidates within this, relative to the best, are one peak.
pub const PEAK_TIE_TOLERANCE: f64 = 1e-9;

/// One grid cell inside the reported region.
#[derive(Debug, Clone, PartialEq)]
pub struct Support {
    pub lat: f64,
    pub lon: f64,
    /// Likelihood ratio against the best candidate, in (0, 1].
    pub support: f64,
}

/// Where the front could have started, as a surface.
#[derive(Debug, Clone, PartialEq)]
pub struct OriginProfile {
    pub n_sites: usize,
    pub refused: Option<String>,
    pub step_deg: Option<f64>,
    pub reach_km: Option<f64>,
    pub candidates: usize,
    pub candidates_ahead: usize,
    pub peak: Option<(f64, f64)>,
    pub peak_at_reach: Option<bool>,
    pub region: Vec<Support>,
    pub share_inside: Option<f64>,
}

/// For every candidate origin on a coarse grid of discs around the sites,
/// how well a wave fits from there, averaged over draws; reported as the
/// region within [`ORIGIN_SUPPORT_CUTOFF`] of the best, never as a point.
pub fn profile_origin(sites: &[SiteSpec], draws: &Draws, step_deg: f64) -> OriginProfile {
    let usable = usable_sites(sites, draws);
    let block = |refused: Option<String>| OriginProfile {
        n_sites: usable.len(),
        refused,
        step_deg: None,
        reach_km: None,
        candidates: 0,
        candidates_ahead: 0,
        peak: None,
        peak_at_reach: None,
        region: Vec::new(),
        share_inside: None,
    };
    if let Some(refusal) = first_refusal(sites) {
        return block(Some(refusal));
    }
    if usable.len() < MIN_DISPERSAL_SITES {
        return block(Some(format!(
            "Profiling an origin needs at least {MIN_DISPERSAL_SITES} sites and this model has {}. With fewer, every candidate origin fits about as well as every other and the surface says nothing. {SITE_DEFINITION}",
            usable.len()
        )));
    }
    let lats: Vec<f64> = usable.iter().map(|s| s.position().0).collect();
    let lons: Vec<f64> = usable.iter().map(|s| s.position().1).collect();
    let ages = joint_ages(&usable, draws);
    let n_sites = usable.len() as f64;
    let (west, east) = lon_extent(&lons);
    let longest_link = minimum_spanning_tree(&usable)
        .iter()
        .map(|e| e.2)
        .fold(0.0, |a: f64, b| if b > a { b } else { a });
    let reach_km = ORIGIN_REACH_KM.max(longest_link / 2.0 + ORIGIN_NECK_KM);
    let reach_lat = reach_km / KM_PER_DEG_LAT;
    let lat_min = lats.iter().copied().fold(f64::INFINITY, f64::min);
    let lat_max = lats.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let mid_lat = (lat_min + lat_max) / 2.0;
    let reach_lon = 180.0_f64.min(reach_lat / mid_lat.to_radians().cos().max(0.1));
    let mut step = step_deg;
    let cells = loop {
        let lat_axis: Vec<f64> = arange(
            ((lat_min - reach_lat) / step).floor() * step,
            lat_max + reach_lat + step / 2.0,
            step,
        )
        .into_iter()
        .filter(|l| (-90.0..=90.0).contains(l))
        .collect();
        let lon_axis = arange(
            ((west - reach_lon) / step).floor() * step,
            east + reach_lon + step / 2.0,
            step,
        );
        let mut cells: Vec<(f64, f64, Vec<f64>)> = Vec::new();
        for &lat in &lat_axis {
            for &lon in &lon_axis {
                let wrapped = py_mod(lon + 180.0, 360.0) - 180.0;
                let distances: Vec<f64> = usable
                    .iter()
                    .map(|s| haversine_km(lat, wrapped, s.position().0, s.position().1))
                    .collect();
                if distances.iter().copied().fold(f64::INFINITY, f64::min) <= reach_km {
                    cells.push((lat, wrapped, distances));
                }
            }
        }
        if cells.len() <= MAX_ORIGIN_CANDIDATES {
            break cells;
        }
        step *= 2.0;
    };

    let mut scores: Vec<(f64, f64, f64)> = Vec::new();
    let mut ahead = 0;
    let rim_km = reach_km - step * KM_PER_DEG_LAT;
    let mut rim: Vec<(f64, f64)> = Vec::new();
    for (lat, wrapped, distances) in &cells {
        if distances.iter().copied().fold(f64::INFINITY, f64::min) >= rim_km {
            rim.push((*lat, *wrapped));
        }
        if distinct_distances(distances) < MIN_DISTINCT_DISTANCES {
            continue;
        }
        let (slope, _intercept, rss) = ols_per_draw(distances, &ages);
        if median(&slope) >= 0.0 {
            ahead += 1;
            continue;
        }
        let logs: Vec<f64> = rss
            .iter()
            .map(|r| -0.5 * n_sites * r.max(1e-12).ln())
            .collect();
        scores.push((*lat, *wrapped, mean(&logs)));
    }
    if scores.is_empty() {
        return OriginProfile {
            step_deg: Some(step),
            reach_km: Some(reach_km),
            candidates_ahead: ahead,
            ..block(Some(if ahead == 0 {
                "No candidate origin gave the sites distinct enough distances for a front to be fitted from it.".to_owned()
            } else {
                "From every candidate on the grid the sites get older with distance, so none is somewhere the front could have started: whatever gradient these dates carry runs towards the grid, not away from any point on it.".to_owned()
            }))
        };
    }
    let best = scores
        .iter()
        .map(|s| s.2)
        .fold(f64::NEG_INFINITY, |a, b| if b > a { b } else { a });
    let region: Vec<Support> = scores
        .iter()
        .map(|&(lat, lon, score)| Support {
            lat,
            lon,
            support: (score - best).min(0.0).exp(),
        })
        .collect();
    let inside: Vec<Support> = region
        .iter()
        .filter(|p| p.support >= ORIGIN_SUPPORT_CUTOFF)
        .cloned()
        .collect();
    let tie = PEAK_TIE_TOLERANCE * best.abs().max(1.0);
    let tied: Vec<&Support> = region
        .iter()
        .zip(&scores)
        .filter(|(_, s)| best - s.2 <= tie)
        .map(|(p, _)| p)
        .collect();
    let peak = tied[0];
    OriginProfile {
        step_deg: Some(step),
        reach_km: Some(reach_km),
        candidates: region.len(),
        candidates_ahead: ahead,
        peak: Some((peak.lat, peak.lon)),
        peak_at_reach: Some(tied.iter().any(|p| rim.contains(&(p.lat, p.lon)))),
        share_inside: Some(inside.len() as f64 / region.len() as f64),
        region: inside,
        ..block(None)
    }
}

// --------------------------------------------------------------------------
// Model 3 -- a spatial order, offered as a PROPOSAL
// --------------------------------------------------------------------------

/// Prim's algorithm over great-circle distance: (from, to, km) per link.
fn minimum_spanning_tree(sites: &[&SiteSpec]) -> Vec<(usize, usize, f64)> {
    let n = sites.len();
    let at = |i: usize| sites[i].position();
    let mut edges = Vec::new();
    // Cheapest known link to each unreached site, in Python's dict order: a
    // key keeps its place when its value is replaced.
    let mut best: Vec<(usize, (f64, usize))> = (1..n)
        .map(|j| (j, (haversine_km(at(0).0, at(0).1, at(j).0, at(j).1), 0)))
        .collect();
    while !best.is_empty() {
        let mut pick = 0;
        for k in 1..best.len() {
            if best[k].1.0 < best[pick].1.0 {
                pick = k;
            }
        }
        let (j, (distance, i)) = best.remove(pick);
        edges.push((i, j, distance));
        for entry in &mut best {
            let k = entry.0;
            let step = haversine_km(at(j).0, at(j).1, at(k).0, at(k).1);
            if step < entry.1.0 {
                entry.1 = (step, j);
            }
        }
    }
    edges
}

/// The tree node farthest from `start`, by summed edge length.
fn farthest_from(start: usize, neighbours: &[Vec<(usize, f64)>]) -> (usize, f64) {
    let mut seen = HashSet::from([start]);
    let mut stack = vec![(start, 0.0)];
    let (mut farthest, mut best) = (start, 0.0);
    while let Some((node, travelled)) = stack.pop() {
        if travelled > best {
            farthest = node;
            best = travelled;
        }
        for &(other, step) in &neighbours[node] {
            if seen.insert(other) {
                stack.push((other, travelled + step));
            }
        }
    }
    (farthest, best)
}

/// One link of the spanning tree an order was read off.
#[derive(Debug, Clone, PartialEq)]
pub struct Edge {
    pub from: String,
    pub to: String,
    pub from_point: (f64, f64),
    pub to_point: (f64, f64),
    pub km: f64,
}

/// A spatial order for the sites, offered to be asserted and tested.
#[derive(Debug, Clone, PartialEq)]
pub struct Proposal {
    /// (element_id, name, latitude, longitude), in the proposed order.
    pub order: Vec<(String, String, f64, f64)>,
    pub edges: Vec<Edge>,
    pub refused: Option<String>,
    pub n_sites: usize,
    pub total_km: Option<f64>,
    pub rooted_at: Option<String>,
    pub origin: Option<(f64, f64)>,
}

/// An order for the sites, read off a minimum spanning tree: a PROPOSAL,
/// since spatial adjacency is not chronology. It needs no draws.
pub fn propose_sequence(sites: &[SiteSpec], origin: Option<(f64, f64)>) -> Proposal {
    let usable: Vec<&SiteSpec> = sites.iter().filter(|s| s.usable()).collect();
    let block = |refused: Option<String>| Proposal {
        order: Vec::new(),
        edges: Vec::new(),
        refused,
        n_sites: usable.len(),
        total_km: None,
        rooted_at: None,
        origin,
    };
    if let Some(refusal) = first_refusal(sites) {
        return block(Some(refusal));
    }
    if usable.len() < 2 {
        return block(Some(format!(
            "A spatial order needs at least two placed sites and this model has {}. {SITE_DEFINITION}",
            usable.len()
        )));
    }
    let edges = minimum_spanning_tree(&usable);
    let mut neighbours: Vec<Vec<(usize, f64)>> = vec![Vec::new(); usable.len()];
    for &(i, j, distance) in &edges {
        neighbours[i].push((j, distance));
        neighbours[j].push((i, distance));
    }
    let root = match origin {
        Some((lat, lon)) => {
            let mut root = 0;
            let mut best = f64::INFINITY;
            for (i, s) in usable.iter().enumerate() {
                let d = haversine_km(lat, lon, s.position().0, s.position().1);
                if d < best {
                    best = d;
                    root = i;
                }
            }
            root
        }
        None => farthest_from(farthest_from(0, &neighbours).0, &neighbours).0,
    };
    let mut order = Vec::new();
    let mut seen = HashSet::from([root]);
    let mut stack = vec![root];
    while let Some(node) = stack.pop() {
        order.push(node);
        let mut next = neighbours[node].clone();
        // sorted(key=-km): stable, farthest pushed first.
        next.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        for (other, _) in next {
            if seen.insert(other) {
                stack.push(other);
            }
        }
    }
    let total: f64 = crate::numeric::python_sum(edges.iter().map(|e| e.2));
    Proposal {
        order: order
            .iter()
            .map(|&i| {
                let s = usable[i];
                (
                    s.element_id.clone(),
                    s.name.clone(),
                    s.position().0,
                    s.position().1,
                )
            })
            .collect(),
        edges: edges
            .iter()
            .map(|&(i, j, km)| Edge {
                from: usable[i].element_id.clone(),
                to: usable[j].element_id.clone(),
                from_point: usable[i].position(),
                to_point: usable[j].position(),
                km,
            })
            .collect(),
        total_km: Some(total),
        rooted_at: Some(usable[root].element_id.clone()),
        ..block(None)
    }
}

/// The dispersal section of an interval report: a wave only where an origin
/// was placed, the trend and the origin profile always.
#[derive(Debug, Clone, PartialEq)]
pub struct Section {
    pub wave: Option<Wave>,
    pub trend: Trend,
    pub origin_profile: OriginProfile,
}

pub fn section(sites: &[SiteSpec], draws: &Draws, spec: &DispersalSpec) -> Section {
    let origin = spec.origin;
    Section {
        wave: origin.map(|o| fit_wave(sites, draws, o)),
        trend: fit_trend(sites, draws),
        origin_profile: profile_origin(sites, draws, ORIGIN_GRID_DEG),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_spread_across_the_antimeridian_is_the_short_way_round() {
        assert_eq!(lon_extent(&[10.0, 20.0, 15.0]), (10.0, 20.0));
        assert_eq!(lon_extent(&[178.0, -109.0]), (178.0, 251.0));
        assert_eq!(lon_extent(&[5.0]), (5.0, 5.0));
    }

    #[test]
    fn steps_are_round() {
        assert_eq!(round_step(137.0), 200.0);
        assert_eq!(round_step(0.0), 1.0);
        assert_eq!(round_step(600.0), 1000.0);
    }
}
