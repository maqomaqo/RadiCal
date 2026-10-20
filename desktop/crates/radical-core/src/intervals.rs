//! Durations and differences -- the quantities that are functions of SEVERAL
//! modelled ages at once, rather than of one. Port of `services/intervals.py`.
//!
//! # Why this is not just more of exact_chain
//!
//! Every other number the solvers report is a MARGINAL: this date's calendar
//! age, that boundary's. A duration is not. "How long did Phase 2 last" is
//! `older boundary - younger boundary`, and the two boundaries are not
//! independent -- the model requires one to be older, and the same dates hold
//! them apart.
//!
//! Pairing two marginals answers a different question, and wrongly in SHAPE
//! rather than scale, which an eyeball check would not catch: on a single phase
//! of six dates it puts 8.2% of the pairs below zero, a phase ending before it
//! starts, where the joint posterior gives that no probability at all. So this
//! module is fed joint draws rather than marginals.
//!
//! The quantities, in the vocabulary the rest of the app uses:
//!
//! ```text
//! sequence duration  the timespan of a Sequence: its first Boundary minus its
//!                    last. The site-wide "how long was this place in use".
//! phase duration     the timespan a pair of Boundaries encloses -- OxCal's
//!                    Interval(). It is the modelled duration of the phase,
//!                    so it can and usually does exceed the spread of the dates
//!                    inside it: the dated events are a sample of the phase, not
//!                    the whole of it.
//! hiatus duration    the gap between two adjacent Boundaries with nothing
//!                    but a Hiatus between them: the end of one group to the
//!                    start of the next. OxCal's Interval() placed in a gap.
//! sample duration    the timespan of the DATED EVENTS in a phase, max minus min
//!                    -- OxCal's Span(). Always shorter than the phase duration
//!                    above, and the gap between the two is exactly how much of
//!                    the phase the dates did not catch.
//! difference         between two phases the model does not put in order --
//!                    parallel branches: the signed years between their
//!                    modelled intervals, POSITIVE for a gap and NEGATIVE for an
//!                    overlap. Between two dates in a group calibration: the
//!                    signed years between them, positive when the first is the
//!                    older.
//! ```
//!
//! # Overlap, and the word attached to it
//!
//! `overlap` on a difference record is a probability in [0, 1], and it means two
//! slightly different things because a phase is an interval and a date is a
//! point:
//!
//! ```text
//! two phases  P(their modelled intervals intersect at all).
//! two dates   P(the ordering is unresolved) = 2 * min(P(A older), P(B older)).
//!             A pair whose posterior says "A is older" 50/50 scores 1.0; a pair
//!             the model has fully separated scores 0.
//! ```
//!
//! Both are on the same scale and read the same way -- 1 means the model cannot
//! tell them apart in time, 0 that it separates them cleanly -- so one severity
//! vocabulary serves both. The bands are 1 - SIGMA_2, 1 - SIGMA_1, SIGMA_1 and
//! SIGMA_2: the same coverages every HPD here is quoted at, and their
//! complements. So "none" means separated at 2 sigma and "extensive"
//! overlapping at 2 sigma, rather than a new convention the reader has to learn.
//!
//! # Monte Carlo, deliberately
//!
//! These are computed from draws even on the exact path, which is a real
//! qualification on numbers this app otherwise reports with no Monte Carlo
//! error. Taken knowingly:
//!
//! - Only a single bounded group's phase duration is cheaply exact, the joint of
//!   one adjacent boundary pair already being on hand as a matrix. A sequence
//!   duration couples the two ends of the chain and a sample duration is the
//!   range of K conditionally-independent dates; neither factorises, and both
//!   would cost an O(M^3) pass.
//! - Mixing an exact phase duration with a sampled sample duration in one table,
//!   where the point is comparing the two, would put a systematic difference
//!   between them that has nothing to do with the archaeology.
//! - The exact path's draws are INDEPENDENT draws from the true joint posterior
//!   (forward-filter/backward-sample over the chain the solver already built),
//!   not an MCMC chain, so the error is plain 1/sqrt(N).
//!
//! `draws` and `independent` are reported alongside the numbers, so the
//! qualification travels with them.
//!
//! The dispersal section `report` adds when asked is [`crate::dispersal`]'s.

use std::collections::{HashMap, HashSet};

use crate::calibration::{HpdRange, SIGMA_1, SIGMA_2, hpd_ranges_from_distribution};
use crate::model_tree::{Element, element_label, plan_model};
use crate::numeric::{
    arange, histogram, mean, median, percentile, py_max, py_min, quantile, round_ndigits,
    searchsorted_left, searchsorted_right, std, sum, unique,
};
use crate::rng::Generator;
use crate::{Measurement, pyfmt};

pub use crate::kde::draws_from_distribution;

/// Joint draws taken on the exact path and for a group calibration, where they
/// are independent and therefore cost only their own arithmetic. The sampled
/// path keeps whatever its own run gives it.
pub const DEFAULT_DRAWS: usize = 20000;

/// Most bins a reported duration is broken into. A duration posterior can be
/// thousands of years wide, and an HPD at the 5-year step a calendar date is
/// quoted at would claim resolution the draws do not have. The step widens
/// instead, and is reported as `step` on every summary.
///
/// Forty rather than a couple of hundred, because an HPD region is built by
/// taking bins in order of density until the confidence is met, so bins near
/// the cut go in or out on their own Monte Carlo error and the region comes
/// back in pieces. On a two-phase model's gap at 20,000 draws, a 5-year step
/// reported the 68% region as eight fragments where the posterior is one
/// long-tailed hump; at 60 bins it was still in two, at 40 it is one range.
/// More draws do NOT fix it, since the density really is flat where the cut
/// falls.
///
/// Forty also lands the ordinary case -- a duration a couple of hundred years
/// wide -- back on the five-year step exactly, so only a genuinely wide one is
/// coarsened. Same failure exact_chain's reported ranges rebin to fix, one step
/// further along because these bins hold draws rather than an exact density.
pub const MAX_BINS: f64 = 40.0;

/// The spread the bin width is taken from, as percentiles rather than full
/// min-to-max: a couple of draws far out in a boundary's heavy tail would
/// otherwise make the reported RESOLUTION a function of the most extreme draw,
/// so more draws would coarsen the answer. Measured from min/max, 20,000 to
/// 60,000 draws moved the step from 10 years to 15.
pub const SPREAD_PERCENTILES: (f64, f64) = (0.5, 99.5);
/// ...but the bins still have to cover every draw, so the full range is
/// allowed this multiple of MAX_BINS before the step widens to fit it.
pub const MAX_BINS_WITH_TAILS: f64 = 6.0 * MAX_BINS;

/// Pairs a difference table holds before it drops to consecutive pairs only.
/// 300 is 25 dates, already a table nobody reads across.
pub const MAX_PAIRS: usize = 300;

/// Overlap probability -> word. Upper bound of each band, ordered; anything
/// above the last is [`SEVERITY_TOP`]. See the module docs for the numbers.
pub const SEVERITY_BANDS: [(f64, &str); 4] = [
    (1.0 - SIGMA_2, "none"),
    (1.0 - SIGMA_1, "slight"),
    (SIGMA_1, "moderate"),
    (SIGMA_2, "substantial"),
];
/// "extensive", not "significant": the top band is where the model can say
/// the LEAST about the ordering, and "significant" reads as a finding to
/// anyone who has met the word in statistics -- the one place a reader could
/// mistake a failure to separate for a result. This string is persisted on the
/// analysis and printed verbatim into the CSV and the PDF.
pub const SEVERITY_TOP: &str = "extensive";

/// What a shared-boundary pair says instead of a figure and a severity word.
/// Only analyses stored before ordered pairs were dropped from the differences
/// carry one; the exports still print it for them.
pub const SHARED_BOUNDARY_TEXT: &str = "shares a transition";

/// The finest step a duration is reported at by default: the same five years
/// every calendar HPD here is.
fn reporting_step() -> f64 {
    crate::exact_chain::REPORTING_STEP_YEARS
}

/// The word for an overlap probability.
pub fn severity(overlap: f64) -> &'static str {
    for (upper, word) in SEVERITY_BANDS {
        if overlap < upper {
            return word;
        }
    }
    SEVERITY_TOP
}

// --------------------------------------------------------------------------
// Summarising a derived quantity
// --------------------------------------------------------------------------

/// Bin width for a set of draws: the reporting step, widened until MAX_BINS
/// covers the draws' central spread (and MAX_BINS_WITH_TAILS the whole of
/// them), and always a whole multiple of the reporting step so two quantities
/// on one page are quoted on compatible grids.
///
/// `base` overrides the five-year floor: for a quantity in years it is the
/// analysis's own reporting resolution, when that is not the default. Every
/// quantity in years in one analysis gets the same base, so they all stay on
/// one grid.
pub fn step_for(values: &[f64], base: Option<f64>) -> f64 {
    let base = base.unwrap_or_else(reporting_step);
    let mut sorted = values.to_vec();
    sorted.sort_by(f64::total_cmp);
    let low = crate::numeric::quantile_sorted(&sorted, SPREAD_PERCENTILES.0 / 100.0);
    let high = crate::numeric::quantile_sorted(&sorted, SPREAD_PERCENTILES.1 / 100.0);
    let (min, max) = (sorted[0], sorted[sorted.len() - 1]);
    let bins = [
        (high - low) / (MAX_BINS * base),
        (max - min) / (MAX_BINS_WITH_TAILS * base),
    ];
    base * py_max(1.0, py_max(bins[0], bins[1]).ceil())
}

/// Bin counts under a [1, 2, 1]/4 kernel, ends held rather than zero-padded
/// so no mass leaves the support -- which matters because a duration piles up
/// hard against zero.
///
/// An HPD region is assembled bin by bin in order of density, so two adjacent
/// bins straddling the cut are decided by their own sampling noise and the
/// region fragments. Widening the bins (MAX_BINS) is most of the fix and this
/// is the rest. It widens the reported region slightly, the conservative
/// direction, and touches only the region: mean, median and standard deviation
/// all come from the raw draws.
pub fn smoothed(counts: &[f64]) -> Vec<f64> {
    if counts.len() < 3 {
        return counts.to_vec();
    }
    let n = counts.len();
    let mut padded = Vec::with_capacity(n + 2);
    padded.push(counts[0]);
    padded.extend_from_slice(counts);
    padded.push(counts[n - 1]);
    (0..n)
        .map(|i| 0.25 * padded[i] + 0.5 * padded[i + 1] + 0.25 * padded[i + 2])
        .collect()
}

/// HPD ranges over binned draws, each widened to the edges of its bins -- the
/// same correction exact_chain's reported ranges apply.
fn ranges(centres: &[f64], weights: &[f64], confidence: f64, step: f64) -> Vec<HpdRange> {
    let (kept_centres, kept_weights): (Vec<f64>, Vec<f64>) = centres
        .iter()
        .zip(weights)
        .filter(|(_, w)| **w > 0.0)
        .map(|(c, w)| (*c, *w))
        .unzip();
    if kept_weights.is_empty() {
        return Vec::new();
    }
    let mut found = hpd_ranges_from_distribution(&kept_centres, &kept_weights, confidence, None)
        .expect("positive weights make a distribution");
    for entry in &mut found {
        entry.min -= step / 2.0;
        entry.max += step / 2.0;
    }
    found
}

/// A quantity measured in YEARS, as the reported shape.
///
/// Deliberately NOT the key names a calendar posterior uses. A duration is not
/// a date, converting it to BC/AD would be nonsense, and the exports format
/// calendar-age columns BY NAME -- so different names are what stops a
/// duration reaching a calendar formatter by accident.
#[derive(Debug, Clone, PartialEq)]
pub struct YearsSummary {
    pub mean: f64,
    pub median: f64,
    pub std: f64,
    pub hpd_68: Option<[f64; 2]>,
    pub hpd_95: Option<[f64; 2]>,
    pub hpd_68_ranges: Vec<HpdRange>,
    pub hpd_95_ranges: Vec<HpdRange>,
    /// The step the ranges above are quoted at -- see [`MAX_BINS`].
    pub step: f64,
    /// [bin centre, density], trimmed to where there is mass but CONTIGUOUS
    /// across it, empty bins in the middle kept: the frontend re-derives its
    /// own HPD from this series and reads the bin width off the first two
    /// points, so holes would be measured against the wrong width.
    pub distribution: Vec<[f64; 2]>,
}

fn envelope(found: &[HpdRange]) -> Option<[f64; 2]> {
    if found.is_empty() {
        return None;
    }
    let lo = found
        .iter()
        .map(|r| r.min)
        .reduce(py_min)
        .expect("not empty");
    let hi = found
        .iter()
        .map(|r| r.max)
        .reduce(py_max)
        .expect("not empty");
    Some([lo, hi])
}

/// Draws of a quantity measured in YEARS, turned into the reported shape; None
/// when none of them is finite.
///
/// `base_step` is the finest resolution to quote it at: the analysis's
/// reporting resolution for a quantity in years. See [`step_for`].
pub fn summarise(values: &[f64], base_step: Option<f64>) -> Option<YearsSummary> {
    let values: Vec<f64> = values.iter().copied().filter(|v| v.is_finite()).collect();
    if values.is_empty() {
        return None;
    }
    let step = step_for(&values, base_step);
    let min = values.iter().copied().fold(f64::INFINITY, f64::min);
    let max = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let lo = (min / step).floor() * step;
    let hi = (max / step).ceil() * step + step;
    let edges = arange(lo, hi + step, step);
    let counts: Vec<f64> = histogram(&values, &edges)
        .into_iter()
        .map(|c| c as f64)
        .collect();
    let centres: Vec<f64> = edges.windows(2).map(|e| (e[0] + e[1]) / 2.0).collect();
    let smooth = smoothed(&counts);
    let total = sum(&smooth);
    let density: Vec<f64> = if total > 0.0 {
        smooth.iter().map(|s| s / (total * step)).collect()
    } else {
        smooth.clone()
    };

    let hpd_68 = ranges(&centres, &smooth, SIGMA_1, step);
    let hpd_95 = ranges(&centres, &smooth, SIGMA_2, step);
    let occupied: Vec<usize> = (0..smooth.len()).filter(|&i| smooth[i] > 0.0).collect();
    let span = match (occupied.first(), occupied.last()) {
        (Some(&a), Some(&b)) => a..b + 1,
        _ => 0..0,
    };
    Some(YearsSummary {
        mean: mean(&values),
        median: median(&values),
        std: if values.len() > 1 {
            std(&values, 1)
        } else {
            0.0
        },
        hpd_68: envelope(&hpd_68),
        hpd_95: envelope(&hpd_95),
        hpd_68_ranges: hpd_68,
        hpd_95_ranges: hpd_95,
        step,
        distribution: span.map(|i| [centres[i], density[i]]).collect(),
    })
}

// --------------------------------------------------------------------------
// What a model has to report -- pure structure, no draws involved
// --------------------------------------------------------------------------

/// A bounded group: one consecutive pair of Boundary siblings and the
/// non-Boundary children between them.
#[derive(Debug, Clone)]
struct BoundedGroup<'a> {
    older: String,
    younger: String,
    elements: Vec<&'a Element>,
}

/// (enclosing, groups).
///
/// `enclosing` maps every element id to the INNERMOST pair of Boundary
/// siblings bracketing it, as (older, younger). An element with no such pair
/// above it is absent from the map.
///
/// `groups` is one entry per consecutive pair of Boundary siblings.
///
/// The same innermost-pair-wins rule the planner applies to dates, rederived
/// here rather than threaded through the plan because a duration needs the
/// ELEMENT each pair brackets and a span group records only dates.
fn enclosures(root: &Element) -> (HashMap<String, (String, String)>, Vec<BoundedGroup<'_>>) {
    fn walk<'a>(
        element: &'a Element,
        inherited: Option<(String, String)>,
        enclosing: &mut HashMap<String, (String, String)>,
        groups: &mut Vec<BoundedGroup<'a>>,
    ) {
        let children = &element.children;
        let boundary_at: Vec<usize> = (0..children.len())
            .filter(|&i| children[i].is("boundary"))
            .collect();
        let sequence = element.is("sequence");
        if sequence {
            for pair in boundary_at.windows(2) {
                groups.push(BoundedGroup {
                    older: children[pair[0]].id.clone(),
                    younger: children[pair[1]].id.clone(),
                    elements: children[pair[0] + 1..pair[1]].iter().collect(),
                });
            }
        }
        for (index, child) in children.iter().enumerate() {
            if child.is("boundary") {
                continue;
            }
            let mut pair = inherited.clone();
            if sequence {
                let before = boundary_at.iter().rev().find(|&&i| i < index);
                let after = boundary_at.iter().find(|&&i| i > index);
                if let (Some(&b), Some(&a)) = (before, after) {
                    pair = Some((children[b].id.clone(), children[a].id.clone()));
                }
            }
            if let Some(p) = &pair {
                enclosing.insert(child.id.clone(), p.clone());
            }
            walk(child, pair, enclosing, groups);
        }
    }
    let mut enclosing = HashMap::new();
    let mut groups = Vec::new();
    if !root.id.is_empty() {
        walk(root, None, &mut enclosing, &mut groups);
    }
    (enclosing, groups)
}

/// A bounded group's members other than a Hiatus, which carries no parameter
/// and so encloses nothing.
fn substantive<'a>(group: &BoundedGroup<'a>) -> Vec<&'a Element> {
    group
        .elements
        .iter()
        .copied()
        .filter(|e| !e.is("hiatus"))
        .collect()
}

fn group_name(group: &BoundedGroup<'_>, names: &HashMap<String, String>) -> String {
    substantive(group)
        .iter()
        .map(|e| names[&e.id].as_str())
        .collect::<Vec<_>>()
        .join(" + ")
}

/// What a duration is, in the order [`plan_durations`] reports them.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DurationKind {
    Sequence,
    Phase,
    Hiatus,
    Sample,
}

impl DurationKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Sequence => "sequence_duration",
            Self::Phase => "phase_duration",
            Self::Hiatus => "hiatus_duration",
            Self::Sample => "sample_duration",
        }
    }
}

/// One duration the model can report: what to compute it from
/// (`boundaries`, or `dates`) and how to label it.
#[derive(Debug, Clone, PartialEq)]
pub struct DurationSpec {
    pub kind: DurationKind,
    pub id: String,
    pub name: String,
    pub element_id: Option<String>,
    /// (older, younger). For a sample duration, the pair innermost bracketing
    /// its phase, when there is one.
    pub boundaries: Option<(String, String)>,
    pub dates: Vec<String>,
}

/// The gap between two adjacent Boundaries as a duration.
///
/// Named by the Hiatus when the analyst gave it a name of their own, and
/// otherwise by what it separates -- "Lower -> Upper" -- since a bare
/// "Hiatus" in a table of several says nothing about which one. The editor
/// names a new Hiatus "Hiatus", so that counts as no name. A gap at either
/// end of a sequence has no group on that side, and the Boundary there names
/// it instead.
fn hiatus_spec(
    gap: &BoundedGroup<'_>,
    groups: &[BoundedGroup<'_>],
    names: &HashMap<String, String>,
) -> DurationSpec {
    let hiatuses: Vec<&Element> = gap
        .elements
        .iter()
        .copied()
        .filter(|e| e.is("hiatus"))
        .collect();
    let named: Vec<&Element> = hiatuses
        .iter()
        .copied()
        .filter(|e| {
            let n = e.name.as_deref().unwrap_or("").trim().to_lowercase();
            !n.is_empty() && n != "hiatus"
        })
        .collect();
    let name = if named.len() == 1 {
        named[0].name.clone().unwrap_or_default()
    } else {
        let before = groups
            .iter()
            .find(|g| g.younger == gap.older && !substantive(g).is_empty());
        let after = groups
            .iter()
            .find(|g| g.older == gap.younger && !substantive(g).is_empty());
        let older = before.map_or_else(|| names[&gap.older].clone(), |g| group_name(g, names));
        let younger = after.map_or_else(|| names[&gap.younger].clone(), |g| group_name(g, names));
        format!("{older} \u{2192} {younger}")
    };
    DurationSpec {
        kind: DurationKind::Hiatus,
        id: format!("hiatus:{}:{}", gap.older, gap.younger),
        name,
        element_id: (hiatuses.len() == 1).then(|| hiatuses[0].id.clone()),
        boundaries: Some((gap.older.clone(), gap.younger.clone())),
        dates: Vec::new(),
    }
}

fn names_of(root: &Element) -> (Vec<&Element>, HashMap<String, String>) {
    let elements = crate::model_tree::iter_elements(root);
    let names = elements
        .iter()
        .map(|e| (e.id.clone(), element_label(e)))
        .collect();
    (elements, names)
}

fn dates_by_element(measurements: &[Measurement]) -> HashMap<String, Vec<String>> {
    let mut out: HashMap<String, Vec<String>> = HashMap::new();
    for m in measurements {
        if let Some(e) = m.element_id.as_deref().filter(|e| !e.is_empty()) {
            out.entry(e.to_owned()).or_default().push(m.id.clone());
        }
    }
    out
}

/// Every duration this model can report, as specs carrying what to compute
/// each from and how to label it.
///
/// Four kinds, in the order they are reported:
///
/// ```text
/// sequence_duration  first to last Boundary CHILD of a Sequence.
///                    Suppressed when those are its only two boundaries with
///                    one phase between them, since the phase duration is
///                    then the same quantity under another name.
/// phase_duration     each consecutive pair of Boundary siblings, provided
///                    something other than a Hiatus sits between them.
/// hiatus_duration    each consecutive pair of Boundary siblings with
///                    nothing but a Hiatus between them, or nothing at all:
///                    the GAP between the group before and the group after.
///                    The Hiatus element itself carries no parameter, so
///                    two adjacent Boundaries are a gap whether or not one
///                    is drawn between them. Listed in place, between the
///                    phase durations either side of it.
/// sample_duration    each phase holding two or more dates.
/// ```
pub fn plan_durations(root: Option<&Element>, measurements: &[Measurement]) -> Vec<DurationSpec> {
    let Some(root) = root else {
        return Vec::new();
    };
    let (enclosing, groups) = enclosures(root);
    let dates = dates_by_element(measurements);
    let (elements, names) = names_of(root);
    let group_pairs: HashSet<(&str, &str)> = groups
        .iter()
        .map(|g| (g.older.as_str(), g.younger.as_str()))
        .collect();
    let mut specs = Vec::new();

    for element in &elements {
        if !element.is("sequence") {
            continue;
        }
        let boundaries: Vec<&str> = element
            .children
            .iter()
            .filter(|c| c.is("boundary"))
            .map(|c| c.id.as_str())
            .collect();
        if boundaries.len() < 2 {
            continue;
        }
        let pair = (boundaries[0], boundaries[boundaries.len() - 1]);
        if boundaries.len() == 2 && group_pairs.contains(&pair) {
            // The sequence IS one bounded group; the phase duration says it.
            continue;
        }
        specs.push(DurationSpec {
            kind: DurationKind::Sequence,
            id: format!("sequence:{}", element.id),
            name: names[&element.id].clone(),
            element_id: Some(element.id.clone()),
            boundaries: Some((pair.0.to_owned(), pair.1.to_owned())),
            dates: Vec::new(),
        });
    }

    for group in &groups {
        let members = substantive(group);
        if members.is_empty() {
            specs.push(hiatus_spec(group, &groups, &names));
            continue;
        }
        let (name, element_id) = if members.len() == 1 {
            (names[&members[0].id].clone(), Some(members[0].id.clone()))
        } else {
            (group_name(group, &names), None)
        };
        specs.push(DurationSpec {
            kind: DurationKind::Phase,
            id: format!("phase:{}:{}", group.older, group.younger),
            name,
            element_id,
            boundaries: Some((group.older.clone(), group.younger.clone())),
            dates: Vec::new(),
        });
    }

    for element in &elements {
        if !element.is("phase") {
            continue;
        }
        let ids = dates.get(&element.id).cloned().unwrap_or_default();
        if ids.len() < 2 {
            continue;
        }
        specs.push(DurationSpec {
            kind: DurationKind::Sample,
            id: format!("sample:{}", element.id),
            name: names[&element.id].clone(),
            element_id: Some(element.id.clone()),
            boundaries: enclosing.get(&element.id).cloned(),
            dates: ids,
        });
    }
    specs
}

/// How one phase stands in for itself in a comparison: its own pair of
/// Boundaries, or its dated events.
#[derive(Debug, Clone, PartialEq)]
pub enum Side {
    /// (older, younger).
    Boundaries(String, String),
    Dates(Vec<String>),
}

impl Side {
    /// The candidates for its older end.
    fn older(&self) -> Vec<&str> {
        match self {
            Self::Boundaries(o, _) => vec![o],
            Self::Dates(d) => d.iter().map(String::as_str).collect(),
        }
    }

    /// The candidates for its younger end.
    fn younger(&self) -> Vec<&str> {
        match self {
            Self::Boundaries(_, y) => vec![y],
            Self::Dates(d) => d.iter().map(String::as_str).collect(),
        }
    }

    fn dates(&self) -> Option<&[String]> {
        match self {
            Self::Dates(d) => Some(d),
            Self::Boundaries(..) => None,
        }
    }
}

/// `at_or_before(x, y)`: whether the structure puts parameter x at or before
/// parameter y in time -- the same one, or x in an earlier child of some
/// Sequence than y, followed transitively. The parameters are Boundaries and
/// dates. Nested ones take part in their parent sequence's order like
/// anything else, as the planner has it.
struct BoundaryOrder {
    later: HashMap<String, HashSet<String>>,
    reachable: std::cell::RefCell<HashMap<String, HashSet<String>>>,
}

impl BoundaryOrder {
    fn new(root: &Element, dates_by_element: &HashMap<String, Vec<String>>) -> Self {
        fn parameters_in(element: &Element, dates: &HashMap<String, Vec<String>>) -> Vec<String> {
            if element.is("boundary") {
                return vec![element.id.clone()];
            }
            let mut out: Vec<String> = dates.get(&element.id).cloned().unwrap_or_default();
            for child in &element.children {
                out.extend(parameters_in(child, dates));
            }
            out
        }
        fn walk(
            element: &Element,
            dates: &HashMap<String, Vec<String>>,
            later: &mut HashMap<String, HashSet<String>>,
        ) {
            if element.is("sequence") {
                let inside: Vec<Vec<String>> = element
                    .children
                    .iter()
                    .map(|c| parameters_in(c, dates))
                    .collect();
                for (i, earlier) in inside.iter().enumerate() {
                    let following: HashSet<String> =
                        inside[i + 1..].iter().flatten().cloned().collect();
                    for p in earlier {
                        later
                            .entry(p.clone())
                            .or_default()
                            .extend(following.iter().cloned());
                    }
                }
            }
            for child in &element.children {
                walk(child, dates, later);
            }
        }
        let mut later = HashMap::new();
        walk(root, dates_by_element, &mut later);
        Self {
            later,
            reachable: Default::default(),
        }
    }

    fn at_or_before(&self, x: &str, y: &str) -> bool {
        if x == y {
            return true;
        }
        let mut cache = self.reachable.borrow_mut();
        let found = cache.entry(x.to_owned()).or_insert_with(|| {
            let mut found: HashSet<String> = HashSet::new();
            let mut stack: Vec<&String> = self.later.get(x).into_iter().flatten().collect();
            while let Some(y) = stack.pop() {
                if found.insert(y.clone()) {
                    stack.extend(self.later.get(y).into_iter().flatten());
                }
            }
            found
        });
        found.contains(y)
    }
}

/// (compared, sides, order): the phases that can be placed in time, depth
/// first and siblings oldest first; how each is represented; and the
/// structural order over Boundaries and dates. See [`plan_phase_differences`]
/// for which representation is used.
fn phase_sides<'a>(
    root: &'a Element,
    measurements: &[Measurement],
) -> (Vec<&'a Element>, HashMap<String, Side>, BoundaryOrder) {
    let (enclosing, _groups) = enclosures(root);
    let direct = dates_by_element(measurements);
    fn dates_in(element: &Element, direct: &HashMap<String, Vec<String>>) -> Vec<String> {
        let mut out = direct.get(&element.id).cloned().unwrap_or_default();
        for child in &element.children {
            out.extend(dates_in(child, direct));
        }
        out
    }
    let order = BoundaryOrder::new(root, &direct);

    // Depth first, siblings oldest first -- the order the results table and
    // every export put the model in. Each phase is kept with the phases
    // enclosing it, to tell a bracket's own phase from those nested in it.
    fn walk<'a>(
        element: &'a Element,
        ancestors: Vec<String>,
        out: &mut Vec<(&'a Element, Vec<String>)>,
    ) {
        let mut ancestors = ancestors;
        if element.is("phase") {
            out.push((element, ancestors.clone()));
            ancestors.push(element.id.clone());
        }
        for child in &element.children {
            walk(child, ancestors.clone(), out);
        }
    }
    let mut phases = Vec::new();
    walk(root, Vec::new(), &mut phases);

    let mut sharing: HashMap<(String, String), Vec<String>> = HashMap::new();
    for (phase, ancestors) in &phases {
        if let Some(pair) = enclosing.get(&phase.id)
            && !ancestors.iter().any(|a| enclosing.get(a) == Some(pair))
        {
            sharing
                .entry(pair.clone())
                .or_default()
                .push(phase.id.clone());
        }
    }

    let mut sides = HashMap::new();
    for (phase, _) in &phases {
        if let Some(pair) = enclosing.get(&phase.id)
            && sharing
                .get(pair)
                .is_some_and(|ids| ids == std::slice::from_ref(&phase.id))
        {
            sides.insert(
                phase.id.clone(),
                Side::Boundaries(pair.0.clone(), pair.1.clone()),
            );
            continue;
        }
        let dates = dates_in(phase, &direct);
        if !dates.is_empty() {
            sides.insert(phase.id.clone(), Side::Dates(dates));
        }
    }
    let compared = phases
        .into_iter()
        .map(|(p, _)| p)
        .filter(|p| sides.contains_key(&p.id))
        .collect();
    (compared, sides, order)
}

/// Which pair a difference compares.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DifferenceKind {
    Phase,
    Date,
}

impl DifferenceKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Phase => "phase_difference",
            Self::Date => "date_difference",
        }
    }
}

/// One pair to compare. A phase difference says how each phase stands in for
/// itself; a date difference compares the two dates' own draws.
#[derive(Debug, Clone, PartialEq)]
pub struct DifferenceSpec {
    pub kind: DifferenceKind,
    pub id: String,
    pub from_id: String,
    pub from_name: String,
    pub to_id: String,
    pub to_name: String,
    pub from: Option<Side>,
    pub to: Option<Side>,
}

/// Every pair of phases whose order the model leaves open, plus a note when
/// the table had to be cut down.
///
/// A phase is represented by its modelled interval when it has a pair of
/// Boundaries of its own -- the one phase in its bracket, not counting
/// phases nested inside it. A phase without one, such as a single-date area
/// in a Sequence with no Boundaries, shares its bracket with everything else
/// in it, so that bracket says nothing about the phase. It is represented by
/// its dated events instead: the span from the oldest to the youngest, a
/// single point for one date. A phase with neither is not compared.
///
/// Of the remaining pairs, two kinds are dropped because the structure, not
/// the dates, already gives the answer:
///
/// ```text
/// ordered      one phase ends at or before the other starts -- any two
///              phases in one Sequence. They cannot overlap, and the gap
///              between them is the durations between them, which the
///              Durations table already reports.
/// overlapping  each phase starts before the other ends -- one nested
///              inside the other. They overlap by construction.
/// ```
///
/// What is left is phases in parallel branches, where the overlap is a
/// finding. A model with none -- every phase in one Sequence -- has no
/// differences to report.
pub fn plan_phase_differences(
    root: Option<&Element>,
    measurements: &[Measurement],
) -> (Vec<DifferenceSpec>, Option<String>) {
    let Some(root) = root else {
        return (Vec::new(), None);
    };
    let (compared, sides, order) = phase_sides(root, measurements);
    // Every candidate for a's younger end is at or before b's older one.
    let ends_before = |a: &Side, b: &Side| {
        a.younger()
            .iter()
            .all(|x| b.older().iter().all(|y| order.at_or_before(x, y)))
    };
    // Some candidate for a's older end is at or before b's younger one.
    let starts_before = |a: &Side, b: &Side| {
        a.older()
            .iter()
            .any(|x| b.younger().iter().any(|y| order.at_or_before(x, y)))
    };
    let open_question = |a: &Side, b: &Side| {
        if ends_before(a, b) || ends_before(b, a) {
            return false;
        }
        !(starts_before(a, b) && starts_before(b, a))
    };

    let mut pairs: Vec<(&Element, &Element)> = Vec::new();
    for (i, a) in compared.iter().enumerate() {
        for b in &compared[i + 1..] {
            if open_question(&sides[&a.id], &sides[&b.id]) {
                pairs.push((a, b));
            }
        }
    }
    let mut note = None;
    if pairs.len() > MAX_PAIRS {
        pairs = compared
            .windows(2)
            .filter(|w| open_question(&sides[&w[0].id], &sides[&w[1].id]))
            .map(|w| (w[0], w[1]))
            .collect();
        note = Some(format!(
            "{} phases make more than {MAX_PAIRS} pairs, so only each phase against the next \
             one is reported.",
            compared.len()
        ));
    }
    let specs = pairs
        .into_iter()
        .map(|(older, younger)| DifferenceSpec {
            kind: DifferenceKind::Phase,
            id: format!("phase-diff:{}:{}", older.id, younger.id),
            from_id: older.id.clone(),
            from_name: element_label(older),
            to_id: younger.id.clone(),
            to_name: element_label(younger),
            from: Some(sides[&older.id].clone()),
            to: Some(sides[&younger.id].clone()),
        })
        .collect();
    (specs, note)
}

// The phase-activity figure's finest column, in years: the solvers' calendar
// grid, so a strip is as smooth as the draws allow. Not the reporting step,
// which is for quoting ranges -- at five years a 150-year site is 30 blocks.
pub const ACTIVITY_STEP_YEARS: f64 = 1.0;
/// Most columns it is computed at, the step widening past this. The rows are
/// drawn as strips a few hundred pixels wide, so more would be resolution no
/// one can see, paid for in every stored analysis.
pub const MAX_ACTIVITY_COLUMNS: f64 = 300.0;
/// Spread of the kernel a single date's histogram is smoothed by, in
/// columns. A sampled run holds a few thousand draws, which at one year a
/// column is a ragged histogram rather than the smooth posterior it estimates.
pub const ACTIVITY_DATE_SMOOTHING: f64 = 2.0;

/// Draws that sit on a calendar grid, each spread uniformly across its own
/// cell, as the KDE does. A Boundary's grid can be coarser than the figure's
/// one-year columns -- five years in a sampled run -- and left on its grid
/// points, every strip would step in five-year blocks.
fn spread_over_cells(values: &[f64], rng: &mut Generator) -> Vec<f64> {
    let cells = unique(values);
    if cells.len() < 2 {
        return values.to_vec();
    }
    let width = cells
        .windows(2)
        .map(|w| w[1] - w[0])
        .fold(f64::INFINITY, f64::min);
    values
        .iter()
        .map(|v| v + rng.uniform(-0.5, 0.5) * width)
        .collect()
}

/// `values` under a Gaussian kernel `spread` columns wide, ends held so no
/// mass leaves the support -- [`smoothed`], wider.
fn gaussian_smoothed(values: &[f64], spread: f64) -> Vec<f64> {
    let reach = (3.0 * spread).ceil() as usize;
    if reach < 1 || values.len() < 2 {
        return values.to_vec();
    }
    let kernel: Vec<f64> = (-(reach as i64)..=reach as i64)
        .map(|i| {
            let x = i as f64 / spread;
            (-0.5 * (x * x)).exp()
        })
        .collect();
    let total = sum(&kernel);
    let kernel: Vec<f64> = kernel.iter().map(|k| k / total).collect();
    let n = values.len();
    let mut padded = vec![values[0]; reach];
    padded.extend_from_slice(values);
    padded.extend(std::iter::repeat_n(values[n - 1], reach));
    // np.convolve(padded, kernel, 'valid'), the kernel reversed as a
    // convolution takes it.
    (0..n)
        .map(|i| {
            let mut s = 0.0;
            for (j, k) in kernel.iter().rev().enumerate() {
                s += padded[i + j] * k;
            }
            s
        })
        .collect()
}

/// One row of the phase-activity figure: a phase as
/// [`plan_phase_differences`] represents it.
#[derive(Debug, Clone, PartialEq)]
pub struct ActivityRowSpec {
    pub id: String,
    pub name: String,
    pub side: Side,
}

/// One row per phase that can be placed in time, for the figure that shows
/// which phases were in progress together: depth first, siblings oldest
/// first, each represented as [`plan_phase_differences`] represents it.
pub fn plan_phase_activity(
    root: Option<&Element>,
    measurements: &[Measurement],
) -> Vec<ActivityRowSpec> {
    let Some(root) = root else {
        return Vec::new();
    };
    let (compared, sides, _order) = phase_sides(root, measurements);
    compared
        .into_iter()
        .map(|phase| ActivityRowSpec {
            id: phase.id.clone(),
            name: element_label(phase),
            side: sides[&phase.id].clone(),
        })
        .collect()
}

/// One phase's strip in the figure.
#[derive(Debug, Clone, PartialEq)]
pub struct ActivityRow {
    pub id: String,
    pub name: String,
    /// A single date's row: its density scaled to a peak of 1, not a
    /// probability of being in progress.
    pub relative: bool,
    /// How many dates stand in for a phase with no Boundaries of its own.
    pub dates: Option<usize>,
    /// By column, oldest first, to three decimals.
    pub values: Vec<f64>,
}

/// Which phases were in progress when.
#[derive(Debug, Clone, PartialEq)]
pub struct PhaseActivity {
    /// The oldest column's centre, cal BP.
    pub start: f64,
    pub step: f64,
    pub rows: Vec<ActivityRow>,
}

/// Joint draws: each parameter's calendar ages, cal BP, one per joint state.
/// Every array comes from ONE set of draws.
pub type Draws = HashMap<String, Vec<f64>>;

fn stacked_extremes(draws: &Draws, ids: &[String]) -> Option<(Vec<f64>, Vec<f64>)> {
    if !ids.iter().all(|d| draws.contains_key(d)) || ids.is_empty() {
        return None;
    }
    let first = &draws[&ids[0]];
    let mut older = first.clone();
    let mut younger = first.clone();
    for id in &ids[1..] {
        for (k, &v) in draws[id].iter().enumerate() {
            older[k] = older[k].max(v);
            younger[k] = younger[k].min(v);
        }
    }
    Some((older, younger))
}

/// One side of a phase difference as (older end, younger end) draws: its
/// Boundaries, or the oldest and youngest of its dates -- one date being
/// both ends at once.
fn interval_of(side: &Side, draws: &Draws) -> Option<(Vec<f64>, Vec<f64>)> {
    match side {
        Side::Dates(dates) => stacked_extremes(draws, dates),
        Side::Boundaries(older, younger) => {
            Some((draws.get(older)?.clone(), draws.get(younger)?.clone()))
        }
    }
}

/// For each row, across a shared calendar grid, the probability the phase is
/// IN PROGRESS at each year: P(older end >= t >= younger end) over the joint
/// draws. Along a Sequence it draws the phases succeeding one another; read
/// down a column across parallel branches, it says which phases were
/// probably running at once -- the overlap the difference table reports pair
/// by pair, drawn for every phase together.
///
/// A single date has no extent to be in progress over. Its row is its
/// posterior density instead, smoothed, scaled to a peak of 1 and flagged
/// `relative`, so the figure can draw it as a different kind of mark.
///
/// None with fewer than two rows: one strip shows no overlap.
pub fn evaluate_phase_activity(rows: &[ActivityRowSpec], draws: &Draws) -> Option<PhaseActivity> {
    let mut rng = Generator::new(0u64);
    let mut ends: Vec<(&ActivityRowSpec, Vec<f64>, Vec<f64>)> = Vec::new();
    for row in rows {
        let Some((older, younger)) = interval_of(&row.side, draws) else {
            continue;
        };
        let single = row.side.dates().is_some_and(|d| d.len() == 1);
        if single {
            let spread = spread_over_cells(&older, &mut rng);
            ends.push((row, spread.clone(), spread));
        } else {
            let older = spread_over_cells(&older, &mut rng);
            let younger = spread_over_cells(&younger, &mut rng);
            ends.push((row, older, younger));
        }
    }
    if ends.len() < 2 {
        return None;
    }

    let base = ACTIVITY_STEP_YEARS;
    let all_older: Vec<f64> = ends
        .iter()
        .flat_map(|(_, o, _)| o.iter().copied())
        .collect();
    let all_younger: Vec<f64> = ends
        .iter()
        .flat_map(|(_, _, y)| y.iter().copied())
        .collect();
    let oldest = percentile(&all_older, 99.5);
    let youngest = percentile(&all_younger, 0.5);
    let width = base
        * py_max(
            1.0,
            ((oldest - youngest) / (MAX_ACTIVITY_COLUMNS * base)).ceil(),
        );
    let oldest = (oldest / width).ceil() * width;
    let youngest = (youngest / width).floor() * width;
    // Oldest first, as every calendar axis here reads.
    let centres = arange(oldest, youngest - width / 2.0, -width);

    let mut out = Vec::new();
    for (row, older, younger) in &ends {
        let dates = row.side.dates();
        let single = dates.is_some_and(|d| d.len() == 1);
        let values: Vec<f64> = if single {
            let mut edges: Vec<f64> = centres.iter().map(|c| c + width / 2.0).collect();
            edges.push(centres[centres.len() - 1] - width / 2.0);
            edges.reverse();
            let mut counts: Vec<f64> = histogram(older, &edges)
                .into_iter()
                .map(|c| c as f64)
                .collect();
            counts.reverse();
            let counts = gaussian_smoothed(&counts, ACTIVITY_DATE_SMOOTHING);
            let peak = counts.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            if peak > 0.0 {
                counts.iter().map(|c| c / peak).collect()
            } else {
                counts
            }
        } else {
            // The younger end never passes the older, so "older at or before
            // t and younger after it" is one subtraction of two tail counts.
            let n = older.len() as f64;
            let mut sorted_older = older.clone();
            sorted_older.sort_by(f64::total_cmp);
            let mut sorted_younger = younger.clone();
            sorted_younger.sort_by(f64::total_cmp);
            centres
                .iter()
                .map(|&c| {
                    let older_from = 1.0 - searchsorted_left(&sorted_older, c) as f64 / n;
                    let younger_past = 1.0 - searchsorted_right(&sorted_younger, c) as f64 / n;
                    (older_from - younger_past).clamp(0.0, 1.0)
                })
                .collect()
        };
        out.push(ActivityRow {
            id: row.id.clone(),
            name: row.name.clone(),
            relative: single,
            dates: dates.filter(|d| !d.is_empty()).map(<[String]>::len),
            values: values.into_iter().map(|v| round_ndigits(v, 3)).collect(),
        });
    }
    Some(PhaseActivity {
        start: centres[0],
        step: width,
        rows: out,
    })
}

/// A date to compare in a group calibration.
#[derive(Debug, Clone, PartialEq)]
pub struct NamedDate {
    pub id: String,
    pub name: String,
}

/// Every pair of dates in a group calibration, in the order given. Cut down
/// to consecutive pairs past [`MAX_PAIRS`], as phases are.
pub fn plan_date_differences(dates: &[NamedDate]) -> (Vec<DifferenceSpec>, Option<String>) {
    let mut pairs: Vec<(&NamedDate, &NamedDate)> = Vec::new();
    for (i, a) in dates.iter().enumerate() {
        for b in &dates[i + 1..] {
            pairs.push((a, b));
        }
    }
    let mut note = None;
    if pairs.len() > MAX_PAIRS {
        pairs = dates.windows(2).map(|w| (&w[0], &w[1])).collect();
        note = Some(format!(
            "{} dates make more than {MAX_PAIRS} pairs, so only each date against the next one \
             is reported.",
            dates.len()
        ));
    }
    let specs = pairs
        .into_iter()
        .map(|(a, b)| DifferenceSpec {
            kind: DifferenceKind::Date,
            id: format!("date-diff:{}:{}", a.id, b.id),
            from_id: a.id.clone(),
            from_name: a.name.clone(),
            to_id: b.id.clone(),
            to_name: b.name.clone(),
            from: None,
            to: None,
        })
        .collect();
    (specs, note)
}

// --------------------------------------------------------------------------
// Specs + joint draws -> reported records
// --------------------------------------------------------------------------

fn duration_values(spec: &DurationSpec, draws: &Draws) -> Option<Vec<f64>> {
    if spec.kind == DurationKind::Sample {
        let present: Vec<String> = spec
            .dates
            .iter()
            .filter(|d| draws.contains_key(*d))
            .cloned()
            .collect();
        if present.len() < 2 {
            return None;
        }
        let (older, younger) = stacked_extremes(draws, &present)?;
        return Some(older.iter().zip(&younger).map(|(o, y)| o - y).collect());
    }
    let (older, younger) = spec.boundaries.as_ref()?;
    // cal BP counts backwards, so the older boundary is the larger number.
    let (o, y) = (draws.get(older)?, draws.get(younger)?);
    Some(o.iter().zip(y).map(|(o, y)| o - y).collect())
}

/// A reported duration.
#[derive(Debug, Clone, PartialEq)]
pub struct Duration {
    pub kind: DurationKind,
    pub id: String,
    pub name: String,
    pub element_id: Option<String>,
    /// How many dates a sample duration spans.
    pub dates: Option<usize>,
    pub years: YearsSummary,
}

/// A reported difference.
#[derive(Debug, Clone, PartialEq)]
pub struct Difference {
    pub kind: DifferenceKind,
    pub id: String,
    pub from_id: String,
    pub from_name: String,
    pub to_id: String,
    pub to_name: String,
    pub years: YearsSummary,
    pub overlap: f64,
    pub severity: &'static str,
    /// A date difference's P(the first is the older).
    pub older_probability: Option<f64>,
    /// How many dates stand in for a phase with no Boundaries of its own.
    pub from_dates: Option<usize>,
    pub to_dates: Option<usize>,
}

/// `np.mean` of a boolean condition: the share of draws meeting it.
fn share(flags: impl Iterator<Item = bool>, n: usize) -> f64 {
    let hits: Vec<f64> = flags.map(|f| if f { 1.0 } else { 0.0 }).collect();
    sum(&hits) / n as f64
}

/// Turn specs plus joint draws into the records the API stores.
///
/// Every array in `draws` must be the same length and come from ONE set of
/// draws: per-parameter marginals give the wrong answer for every quantity
/// here. `step` is the analysis's reporting resolution, None for the default.
pub fn evaluate(
    duration_specs: &[DurationSpec],
    difference_specs: &[DifferenceSpec],
    draws: &Draws,
    step: Option<f64>,
) -> (Vec<Duration>, Vec<Difference>) {
    let mut durations = Vec::new();
    for spec in duration_specs {
        let Some(values) = duration_values(spec, draws) else {
            continue;
        };
        let Some(years) = summarise(&values, step) else {
            continue;
        };
        durations.push(Duration {
            kind: spec.kind,
            id: spec.id.clone(),
            name: spec.name.clone(),
            element_id: spec.element_id.clone(),
            dates: (!spec.dates.is_empty()).then_some(spec.dates.len()),
            years,
        });
    }

    let mut differences = Vec::new();
    for spec in difference_specs {
        let (signed, overlap, older_probability) = match spec.kind {
            DifferenceKind::Phase => {
                let (Some(first), Some(second)) = (
                    spec.from.as_ref().and_then(|s| interval_of(s, draws)),
                    spec.to.as_ref().and_then(|s| interval_of(s, draws)),
                ) else {
                    continue;
                };
                // Each interval is [younger end, older end] on the cal BP
                // number line, so the intersection runs from the younger of
                // the older ends to the older of the younger ends. Its length
                // is positive where they overlap and negative where they are
                // apart -- one quantity covering both, so there is no separate
                // gap column to keep in step.
                let n = first.0.len();
                let intersection: Vec<f64> = (0..n)
                    .map(|k| first.0[k].min(second.0[k]) - first.1[k].max(second.1[k]))
                    .collect();
                let signed: Vec<f64> = intersection.iter().map(|v| -v).collect();
                let point = |side: &Option<Side>| {
                    side.as_ref()
                        .and_then(Side::dates)
                        .is_some_and(|d| d.len() == 1)
                };
                let points = [point(&spec.from), point(&spec.to)];
                let overlap = if points.iter().all(|&p| p) {
                    // Two single dates never intersect, so overlap is the date
                    // pair's measure instead: how far the model is from
                    // ordering them. The separation is the gap between them
                    // either way.
                    let older = share((0..n).map(|k| first.0[k] > second.0[k]), n);
                    2.0 * py_min(older, 1.0 - older)
                } else if points.iter().any(|&p| p) {
                    // A date ON a phase's end is inside it, so the zero counts.
                    share(intersection.iter().map(|&v| v >= 0.0), n)
                } else {
                    share(intersection.iter().map(|&v| v > 0.0), n)
                };
                (signed, overlap, None)
            }
            DifferenceKind::Date => {
                let (Some(first), Some(second)) =
                    (draws.get(&spec.from_id), draws.get(&spec.to_id))
                else {
                    continue;
                };
                let signed: Vec<f64> = first.iter().zip(second).map(|(a, b)| a - b).collect();
                let older = share(signed.iter().map(|&v| v > 0.0), signed.len());
                // How far the ordering is from settled: 1 at a coin flip on
                // which came first, 0 when it is certain.
                (signed, 2.0 * py_min(older, 1.0 - older), Some(older))
            }
        };
        let Some(years) = summarise(&signed, step) else {
            continue;
        };
        let count = |side: &Option<Side>| {
            side.as_ref()
                .and_then(Side::dates)
                .filter(|d| !d.is_empty())
                .map(<[String]>::len)
        };
        differences.push(Difference {
            kind: spec.kind,
            id: spec.id.clone(),
            from_id: spec.from_id.clone(),
            from_name: spec.from_name.clone(),
            to_id: spec.to_id.clone(),
            to_name: spec.to_name.clone(),
            years,
            overlap,
            severity: severity(overlap),
            older_probability,
            from_dates: count(&spec.from),
            to_dates: count(&spec.to),
        });
    }
    (durations, differences)
}

// --------------------------------------------------------------------------
// Age against depth
// --------------------------------------------------------------------------

/// Where the age-depth envelopes are cut. QUANTILES, not HPD regions: an
/// envelope is drawn as a band and has to nest, and two HPD regions on a
/// multi-peaked posterior need not -- the 68.3% one can reach outside the
/// 95.4% one at a different depth and the band crosses itself. The coverages
/// are the same two every other figure here is quoted at.
pub const BAND_LEVELS: [(f64, u32); 2] = [(SIGMA_1, 68), (SIGMA_2, 95)];

/// A calendar age from joint draws, as a median and two bands.
///
/// Calendar-age key names, deliberately unlike a duration's: these ARE dates
/// and the exports format them as such, where every other figure here is a
/// length in years and must never be converted to BC/AD.
#[derive(Debug, Clone, PartialEq)]
pub struct CalendarBand {
    pub median: f64,
    pub mean: f64,
    pub band_68: [f64; 2],
    pub band_95: [f64; 2],
}

pub fn calendar_band(values: &[f64]) -> CalendarBand {
    let band = |coverage: f64| {
        let tail = (1.0 - coverage) / 2.0;
        [quantile(values, tail), quantile(values, 1.0 - tail)]
    };
    CalendarBand {
        median: median(values),
        mean: mean(values),
        band_68: band(BAND_LEVELS[0].0),
        band_95: band(BAND_LEVELS[1].0),
    }
}

/// One level of a deposition sequence, to report.
#[derive(Debug, Clone, PartialEq)]
pub struct LevelSpec {
    pub id: String,
    pub depth: f64,
    pub dated: bool,
    pub name: String,
}

/// One deposition sequence, as the levels an age-depth answer is made of.
#[derive(Debug, Clone, PartialEq)]
pub struct AgeDepthSpec {
    pub element_id: String,
    pub name: String,
    pub depth_units: String,
    pub levels: Vec<LevelSpec>,
}

/// Every deposition sequence in the model, as the levels and intervals an
/// age-depth answer is made of.
///
/// A stack of per-date posteriors is NOT the deliverable of an age-depth
/// model: what the reader wants is age against depth, with a band round it,
/// and how fast the deposit accumulated between one level and the next.
/// Every one of those is a function of SEVERAL ages at once, which is why it
/// lives in this module and comes off joint draws.
pub fn plan_age_depth(root: Option<&Element>, measurements: &[Measurement]) -> Vec<AgeDepthSpec> {
    let Some(root) = root else {
        return Vec::new();
    };
    let names: HashMap<&str, &str> = measurements
        .iter()
        .map(|m| (m.id.as_str(), m.label()))
        .collect();
    plan_model(root, measurements)
        .deposition_groups
        .into_iter()
        .map(|group| AgeDepthSpec {
            element_id: group.element_id.clone(),
            name: group.name.clone(),
            depth_units: group.depth_units.clone(),
            levels: group
                .levels
                .iter()
                .map(|(pid, depth, dated)| LevelSpec {
                    id: pid.clone(),
                    depth: *depth,
                    dated: *dated,
                    name: names
                        .get(pid.as_str())
                        .filter(|n| !n.is_empty())
                        .map(|n| (*n).to_owned())
                        .or_else(|| {
                            group
                                .level_names
                                .get(pid)
                                .filter(|n| !n.is_empty())
                                .cloned()
                        })
                        .unwrap_or_else(|| {
                            format!("{} {}", pyfmt::g(*depth, 6), group.depth_units)
                        }),
                })
                .collect(),
        })
        .collect()
}

/// One level's age.
#[derive(Debug, Clone, PartialEq)]
pub struct ReportedLevel {
    pub id: String,
    pub name: String,
    pub depth: f64,
    pub dated: bool,
    pub band: CalendarBand,
}

/// The accumulation between two neighbouring levels.
#[derive(Debug, Clone, PartialEq)]
pub struct Accumulation {
    pub from_depth: f64,
    pub to_depth: f64,
    pub from_name: String,
    pub to_name: String,
    pub depth_gap: f64,
    /// The elapsed time, in the same shape every other duration here takes,
    /// so it renders and exports through the same code.
    pub years: YearsSummary,
    /// ...and the same thing per unit depth, which is what an accumulation
    /// rate IS and what the literature quotes.
    pub rate_median: f64,
    pub rate_band_68: [f64; 2],
}

/// One deposition sequence's age-depth answer.
#[derive(Debug, Clone, PartialEq)]
pub struct AgeDepth {
    pub element_id: String,
    pub name: String,
    pub depth_units: String,
    pub levels: Vec<ReportedLevel>,
    pub accumulation: Vec<Accumulation>,
}

/// Specs plus joint draws into the age-depth records the API stores.
///
/// The accumulation rate between two levels is (older age - younger age)
/// divided by the depth between them, and it is read off the SAME draw of
/// both ages. Pairing their marginals would give a rate distribution that is
/// wrong in shape rather than scale -- the identical trap this module opens
/// with for durations, and the reason nothing here is computed from a
/// marginal.
pub fn evaluate_age_depth(
    specs: &[AgeDepthSpec],
    draws: &Draws,
    step: Option<f64>,
) -> Vec<AgeDepth> {
    let mut records = Vec::new();
    for spec in specs {
        let levels: Vec<&LevelSpec> = spec
            .levels
            .iter()
            .filter(|l| draws.contains_key(&l.id))
            .collect();
        if levels.len() < 2 {
            continue;
        }
        let reported = levels
            .iter()
            .map(|l| ReportedLevel {
                id: l.id.clone(),
                name: l.name.clone(),
                depth: l.depth,
                dated: l.dated,
                band: calendar_band(&draws[&l.id]),
            })
            .collect();
        let mut accumulation = Vec::new();
        for pair in levels.windows(2) {
            let (older, younger) = (pair[0], pair[1]);
            let depth_gap = (older.depth - younger.depth).abs();
            if depth_gap <= 0.0 {
                continue;
            }
            let elapsed: Vec<f64> = draws[&older.id]
                .iter()
                .zip(&draws[&younger.id])
                .map(|(o, y)| o - y)
                .collect();
            let Some(years) = summarise(&elapsed, step) else {
                continue;
            };
            let rate: Vec<f64> = elapsed.iter().map(|e| e / depth_gap).collect();
            let tail = (1.0 - SIGMA_1) / 2.0;
            accumulation.push(Accumulation {
                from_depth: older.depth,
                to_depth: younger.depth,
                from_name: older.name.clone(),
                to_name: younger.name.clone(),
                depth_gap,
                years,
                rate_median: median(&rate),
                rate_band_68: [quantile(&rate, tail), quantile(&rate, 1.0 - tail)],
            });
        }
        records.push(AgeDepth {
            element_id: spec.element_id.clone(),
            name: spec.name.clone(),
            depth_units: spec.depth_units.clone(),
            levels: reported,
            accumulation,
        });
    }
    records
}

/// The whole durations-and-differences block for a modelled analysis, as the
/// API stores it on the analysis.
#[derive(Debug, Clone, PartialEq)]
pub struct Block {
    pub durations: Vec<Duration>,
    pub differences: Vec<Difference>,
    pub age_depth: Vec<AgeDepth>,
    /// Which phases were in progress when, year by year: set wherever
    /// durations were asked for and two or more phases can be placed in time.
    pub phase_activity: Option<PhaseActivity>,
    /// The fewest draws any parameter had.
    pub draws: usize,
    /// True when the draws are independent rather than an MCMC chain, where
    /// autocorrelation makes the effective count smaller than the count.
    pub independent: bool,
    pub note: Option<String>,
    /// The spatial models, where a run asked for them.
    pub dispersal: Option<crate::dispersal::Section>,
}

fn block(
    durations: Vec<Duration>,
    differences: Vec<Difference>,
    draws: &Draws,
    independent: bool,
    note: Option<String>,
) -> Block {
    Block {
        durations,
        differences,
        age_depth: Vec::new(),
        phase_activity: None,
        draws: draws.values().map(Vec::len).min().unwrap_or(0),
        independent,
        note,
        dispersal: None,
    }
}

/// The whole durations-and-differences block for a modelled analysis.
///
/// `durations` false leaves the durations and differences out and reports
/// only the age-depth section. That is for a run that has joint draws for a
/// reason of its own -- an age-depth model always does -- without the analyst
/// having asked for phase durations, which are a modelling claim of their own.
///
/// `step` is the analysis's reporting resolution in years (None for the
/// default), which every span here is quoted on.
pub fn report(
    root: Option<&Element>,
    measurements: &[Measurement],
    draws: &Draws,
    independent: bool,
    durations: bool,
    dispersal: Option<&crate::dispersal::DispersalSpec>,
    step: Option<f64>,
) -> Block {
    let wanted = root.is_some() && durations;
    let duration_specs = if wanted {
        plan_durations(root, measurements)
    } else {
        Vec::new()
    };
    let (difference_specs, note) = if wanted {
        plan_phase_differences(root, measurements)
    } else {
        (Vec::new(), None)
    };
    let (found, differences) = evaluate(&duration_specs, &difference_specs, draws, step);
    let mut out = block(found, differences, draws, independent, note);
    if wanted {
        out.phase_activity =
            evaluate_phase_activity(&plan_phase_activity(root, measurements), draws);
    }
    out.age_depth = evaluate_age_depth(&plan_age_depth(root, measurements), draws, step);
    if let Some(spec) = dispersal {
        let sites = crate::dispersal::derive_sites(root, measurements);
        out.dispersal = Some(crate::dispersal::section(&sites, draws, spec));
    }
    out
}

/// The same block for a group calibration: differences only.
///
/// Dates with no relationship asserted between them belong to no phase and
/// no sequence, so they have no duration: the table is pairwise differences
/// only. Independent by construction: a group calibration asserts no
/// relationship, so drawing each date separately IS drawing from the joint.
///
/// A dispersal fit here runs on the fallback -- each place's earliest dated
/// event, a terminus ante quem -- and needs the dates' coordinates.
pub fn report_dates(
    dates: &[NamedDate],
    draws: &Draws,
    dispersal: Option<(&crate::dispersal::DispersalSpec, &[Measurement])>,
) -> Block {
    let (specs, note) = plan_date_differences(dates);
    let (_durations, differences) = evaluate(&[], &specs, draws, None);
    let mut out = block(Vec::new(), differences, draws, true, note);
    if let Some((spec, measurements)) = dispersal {
        let sites = crate::dispersal::derive_sites_from_dates(measurements, draws);
        out.dispersal = Some(crate::dispersal::section(&sites, draws, spec));
    }
    out
}
