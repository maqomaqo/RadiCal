//! Influence analysis: how far one date moves a model's answer. Port of
//! `services/influence.py`.
//!
//! A conclusion can rest on one date, and finding which by hand is a series
//! of edited copies run one at a time. This plans those copies -- each date
//! left out, moved to the neighbouring Phase older and younger, or treated as
//! an outlier -- and reduces each one's result to what the comparison needs.
//! The runs themselves are the caller's, through the same solver path as any
//! analysis. Each variant keeps every Boundary and duration (median, 95%
//! range and a compact distribution), so the question is chosen after the
//! run, not before it.

use std::collections::HashMap;

use crate::Error;
use crate::measurement::Measurement;
use crate::model_tree::Element;
use crate::numeric::{argsort_quicksort, interp, linspace};

pub const KINDS: [&str; 3] = ["leave_out", "move", "outlier"];
/// The outlier model a date treated as an outlier takes when it has none:
/// OxCal's General, which says nothing about which way the date is wrong.
pub const DEFAULT_OUTLIER_MODEL: &str = "general";
/// Points a stored distribution is reduced to: a probability before a year to
/// within about 0.002, and fifty variants well under a megabyte.
pub const DISTRIBUTION_POINTS: usize = 120;

/// One variant of the analysis.
#[derive(Debug, Clone, PartialEq)]
pub struct Variant {
    pub id: String,
    pub kind: &'static str,
    pub date_id: String,
    pub date_name: String,
    pub label: String,
    /// For a move: the Phase it goes to, and which way that is.
    pub to_phase_id: Option<String>,
    pub to_phase_name: Option<String>,
    pub direction: Option<&'static str>,
}

/// Every element's parent, by id.
fn index(tree: &Element) -> HashMap<&str, (&Element, Option<&Element>)> {
    fn walk<'a>(
        e: &'a Element,
        parent: Option<&'a Element>,
        out: &mut HashMap<&'a str, (&'a Element, Option<&'a Element>)>,
    ) {
        out.insert(&e.id, (e, parent));
        for child in &e.children {
            walk(child, Some(e), out);
        }
    }
    let mut out = HashMap::new();
    walk(tree, None, &mut out);
    out
}

fn is_leaf_phase(e: &Element) -> bool {
    e.is("phase") && e.children.is_empty()
}

/// The Phases either side of `phase_id` in the Sequence that holds it, as
/// (older, younger): the nearest sibling that holds dates directly, across
/// any Boundary or Hiatus between -- None where that sibling is not a plain
/// Phase, or the Phase is not in a Sequence.
pub fn neighbouring_phases<'a>(
    tree: &'a Element,
    phase_id: &str,
) -> (Option<&'a Element>, Option<&'a Element>) {
    let found = index(tree);
    let Some(&(_, Some(parent))) = found.get(phase_id) else {
        return (None, None);
    };
    if !parent.is("sequence") {
        return (None, None);
    }
    let siblings = &parent.children;
    let position = siblings.iter().position(|c| c.id == phase_id).unwrap();
    let skip = |c: &&Element| c.is("boundary") || c.is("hiatus");
    let older = siblings[..position]
        .iter()
        .rev()
        .find(|c| !skip(c))
        .filter(|c| is_leaf_phase(c));
    let younger = siblings[position + 1..]
        .iter()
        .find(|c| !skip(c))
        .filter(|c| is_leaf_phase(c));
    (older, younger)
}

/// Every variant `kinds` asks for, in date order. Only dates in a plain Phase
/// are varied: a member of a wiggle match or a deposition sequence is part of
/// one fitted curve.
pub fn plan(measurements: &[Measurement], tree: &Element, kinds: &[&str]) -> Vec<Variant> {
    let found = index(tree);
    let mut variants = Vec::new();
    for m in measurements {
        let holder = found.get(m.element_id.as_deref().unwrap_or(""));
        if !holder.is_some_and(|(e, _)| is_leaf_phase(e)) {
            continue;
        }
        let name = m.label().to_owned();
        let base = |kind: &'static str, id: String, label: String| Variant {
            id,
            kind,
            date_id: m.id.clone(),
            date_name: name.clone(),
            label,
            to_phase_id: None,
            to_phase_name: None,
            direction: None,
        };
        if kinds.contains(&"leave_out") {
            variants.push(base(
                "leave_out",
                format!("leave_out:{}", m.id),
                format!("{name} left out"),
            ));
        }
        if kinds.contains(&"move") {
            let (older, younger) = neighbouring_phases(tree, m.element_id.as_deref().unwrap());
            for (direction, phase) in [("older", older), ("younger", younger)] {
                let Some(phase) = phase else { continue };
                let phase_name = phase
                    .name
                    .clone()
                    .filter(|n| !n.is_empty())
                    .unwrap_or_else(|| "the next Phase".to_owned());
                let mut v = base(
                    "move",
                    format!("move_{direction}:{}", m.id),
                    // "the older Phase", not "(older)": beside a change that
                    // reads older or younger, a bare word would read as the
                    // direction the answer moved.
                    format!("{name} moved to {phase_name}, the {direction} Phase"),
                );
                v.to_phase_id = Some(phase.id.clone());
                v.to_phase_name = Some(phase_name);
                v.direction = Some(direction);
                variants.push(v);
            }
        }
        if kinds.contains(&"outlier") && m.outlier_prior() < 1.0 {
            variants.push(base(
                "outlier",
                format!("outlier:{}", m.id),
                format!("{name} as an outlier"),
            ));
        }
    }
    variants
}

/// The measurements with `variant` applied.
pub fn apply(variant: &Variant, measurements: &[Measurement]) -> Result<Vec<Measurement>, Error> {
    let mut out = Vec::with_capacity(measurements.len());
    for m in measurements {
        if m.id != variant.date_id {
            out.push(m.clone());
            continue;
        }
        match variant.kind {
            "leave_out" => {}
            "move" => out.push(Measurement {
                element_id: variant.to_phase_id.clone(),
                ..m.clone()
            }),
            "outlier" => out.push(Measurement {
                outlier_prior: Some(1.0),
                outlier_model: Some(
                    m.outlier_model
                        .clone()
                        .filter(|s| !s.is_empty())
                        .unwrap_or_else(|| DEFAULT_OUTLIER_MODEL.to_owned()),
                ),
                ..m.clone()
            }),
            other => return Err(Error::Model(format!("unknown variant kind '{other}'"))),
        }
    }
    Ok(out)
}

/// Python's `round(x, 3)`: correctly rounded to three decimals.
fn round3(x: f64) -> f64 {
    format!("{x:.3}").parse().unwrap()
}

/// A [x, density] series resampled onto at most `points` evenly spaced x,
/// over the span it has mass; sorted by x either way.
pub fn compact_distribution(distribution: &[[f64; 2]], points: usize) -> Option<Vec<[f64; 2]>> {
    if distribution.len() < 2 {
        return None;
    }
    let xs: Vec<f64> = distribution.iter().map(|p| p[0]).collect();
    let order = argsort_quicksort(&xs);
    let series: Vec<[f64; 2]> = order.iter().map(|&i| distribution[i]).collect();
    if series.len() <= points {
        return Some(series);
    }
    let (sx, sy): (Vec<f64>, Vec<f64>) = series.iter().map(|p| (p[0], p[1])).unzip();
    let grid = linspace(sx[0], sx[sx.len() - 1], points);
    let ys = interp(&grid, &sx, &sy);
    Some(
        grid.into_iter()
            .zip(ys)
            .map(|(x, y)| [round3(x), y])
            .collect(),
    )
}

fn outer(hpd: Option<(f64, f64)>) -> Option<[f64; 2]> {
    hpd.map(|(lo, hi)| [lo.min(hi), lo.max(hi)])
}

/// What a variant's run is reduced to, for one Boundary or duration.
#[derive(Debug, Clone, PartialEq)]
pub struct Reduced {
    pub name: Option<String>,
    /// For a duration, its kind.
    pub kind: Option<String>,
    pub median: f64,
    pub hpd_95: Option<[f64; 2]>,
    pub distribution: Option<Vec<[f64; 2]>>,
}

/// Reduced quantities by key, in the order the run gave them.
pub type Keyed = Vec<(String, Reduced)>;

/// One quantity of a run as `summarise` reads it.
#[derive(Debug, Clone, PartialEq)]
pub struct Quantity<'a> {
    pub name: Option<&'a str>,
    pub median: Option<f64>,
    pub hpd_95: Option<(f64, f64)>,
    pub distribution: &'a [[f64; 2]],
}

/// A duration as `summarise` reads it.
#[derive(Debug, Clone, PartialEq)]
pub struct DurationQuantity<'a> {
    pub kind: &'a str,
    pub id: &'a str,
    pub element_id: Option<&'a str>,
    pub quantity: Quantity<'a>,
}

/// One run reduced to what an influence table reads: Boundaries by id in
/// cal BP, durations by "kind:element" in years, so the same duration is
/// matched across variants.
pub fn summarise(
    boundaries: &[(&str, Quantity)],
    durations: &[DurationQuantity],
) -> (Keyed, Keyed) {
    let reduce = |q: &Quantity, kind: Option<&str>| -> Option<Reduced> {
        Some(Reduced {
            name: q.name.map(str::to_owned),
            kind: kind.map(str::to_owned),
            median: q.median?,
            hpd_95: outer(q.hpd_95),
            distribution: compact_distribution(q.distribution, DISTRIBUTION_POINTS),
        })
    };
    let b = boundaries
        .iter()
        .filter_map(|(id, q)| reduce(q, None).map(|r| ((*id).to_owned(), r)))
        .collect();
    let d = durations
        .iter()
        .filter_map(|row| {
            let key = format!("{}:{}", row.kind, row.element_id.unwrap_or(row.id));
            reduce(&row.quantity, Some(row.kind)).map(|r| (key, r))
        })
        .collect();
    (b, d)
}
