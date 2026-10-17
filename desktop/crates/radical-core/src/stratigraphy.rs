//! Turning samples, groups and relationships into the pairwise sample
//! constraints a Bayesian model needs, and catching stratigraphically
//! impossible input before it reaches a solver. Port of
//! `services/stratigraphy.py`.
//!
//! A relationship's endpoints can each name a sample or a group, per
//! from_type/to_type. This module expands a group reference into the full
//! cross-product of its members, so the rest of the codebase only deals with
//! sample-to-sample edges.
//!
//! A directed edge (u, v) means "u must be strictly OLDER than v", i.e.
//! age_u > age_v in cal BP. Both relationship types are normalised to this one
//! direction so the rest of this module reasons about a single kind of edge.

use std::collections::{BTreeSet, HashMap, VecDeque};

use crate::measurement::Measurement;
use crate::numeric::{py_max, py_min};

/// (older, younger).
pub type Edge = (String, String);

/// A context of the older contexts-and-relationships representation.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Group {
    pub id: String,
    pub name: Option<String>,
    pub tpq_age_bp: Option<f64>,
    pub taq_age_bp: Option<f64>,
}

/// One stated relationship between two samples or groups.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Relationship {
    pub from_sample_id: String,
    pub to_sample_id: String,
    /// 'sample' (the default) or 'group'.
    pub from_type: Option<String>,
    pub to_type: Option<String>,
    /// 'above', 'below' or 'equal'.
    pub relationship: String,
}

/// Each group id with the ids of its measurements, groups in the order given
/// and then any group a measurement names that was not given. A group with no
/// members maps to an empty list.
pub fn group_members(measurements: &[Measurement], groups: &[Group]) -> Vec<(String, Vec<String>)> {
    let mut members: Vec<(String, Vec<String>)> = Vec::new();
    for g in groups {
        if !members.iter().any(|(id, _)| *id == g.id) {
            members.push((g.id.clone(), Vec::new()));
        }
    }
    for m in measurements {
        let Some(gid) = m.group_id.as_deref().filter(|g| !g.is_empty()) else {
            continue;
        };
        match members.iter_mut().find(|(id, _)| id == gid) {
            Some((_, list)) => list.push(m.id.clone()),
            None => members.push((gid.to_owned(), vec![m.id.clone()])),
        }
    }
    members
}

/// Expand every relationship (which may reference a sample OR a group on
/// either side) into plain sample-to-sample pairs.
///
/// Returns (older_younger_edges, equal_pairs), both deduplicated: (u, v)
/// meaning u is older than v, and (a, b) with a < b. Python returns them in a
/// set's order; here they are sorted.
///
/// Direction follows the law of superposition: a sample stratigraphically
/// ABOVE another was deposited later and is YOUNGER, one BELOW is OLDER.
///
/// A relationship whose endpoint resolves to no samples -- an empty group --
/// contributes no edges. That is a modelling no-op rather than an error;
/// emptiness is warned about client-side.
pub fn expand_relationships(
    measurements: &[Measurement],
    groups: &[Group],
    relationships: &[Relationship],
) -> (Vec<Edge>, Vec<Edge>) {
    let members = group_members(measurements, groups);
    let resolve = |node_id: &str, node_type: Option<&str>| -> Vec<String> {
        if node_type == Some("group") {
            members
                .iter()
                .find(|(id, _)| id == node_id)
                .map(|(_, m)| m.clone())
                .unwrap_or_default()
        } else {
            vec![node_id.to_owned()]
        }
    };

    let mut older_younger: BTreeSet<Edge> = BTreeSet::new();
    let mut equal: BTreeSet<Edge> = BTreeSet::new();
    for r in relationships {
        let from_members = resolve(&r.from_sample_id, r.from_type.as_deref());
        let to_members = resolve(&r.to_sample_id, r.to_type.as_deref());
        for a in &from_members {
            for b in &to_members {
                if a == b {
                    // A self-relationship, or a group holding both endpoints
                    // of its own. Skipped rather than made a self-cycle.
                    continue;
                }
                match r.relationship.as_str() {
                    // from is above to, so from is younger and to is older.
                    "above" => {
                        older_younger.insert((b.clone(), a.clone()));
                    }
                    // from is below to, so from is older.
                    "below" => {
                        older_younger.insert((a.clone(), b.clone()));
                    }
                    "equal" => {
                        equal.insert(if a < b {
                            (a.clone(), b.clone())
                        } else {
                            (b.clone(), a.clone())
                        });
                    }
                    _ => {}
                }
            }
        }
    }
    (
        older_younger.into_iter().collect(),
        equal.into_iter().collect(),
    )
}

/// Detect a cycle in the "older than" graph by Kahn's topological sort.
/// Returns the node ids left once everything with no remaining incoming edge
/// is peeled away -- what is in or feeds into a cycle -- sorted, or None for
/// a DAG.
///
/// A cycle means no set of calendar ages can satisfy every constraint at once,
/// which a solver would only surface as a failure or a hang minutes into a run.
pub fn find_cycle(edges: &[Edge]) -> Option<Vec<String>> {
    if edges.is_empty() {
        return None;
    }
    let mut adj: HashMap<&str, Vec<&str>> = HashMap::new();
    let mut indeg: HashMap<&str, usize> = HashMap::new();
    let mut nodes: BTreeSet<&str> = BTreeSet::new();
    for (u, v) in edges {
        nodes.insert(u);
        nodes.insert(v);
        adj.entry(u).or_default().push(v);
        *indeg.entry(v).or_default() += 1;
        indeg.entry(u).or_default();
    }
    let mut queue: VecDeque<&str> = nodes
        .iter()
        .copied()
        .filter(|n| indeg.get(n).copied().unwrap_or(0) == 0)
        .collect();
    let mut visited = 0;
    while let Some(n) = queue.pop_front() {
        visited += 1;
        for &m in adj.get(n).into_iter().flatten() {
            let d = indeg.get_mut(m).expect("every target has an in-degree");
            *d -= 1;
            if *d == 0 {
                queue.push_back(m);
            }
        }
    }
    if visited == nodes.len() {
        return None;
    }
    Some(
        nodes
            .into_iter()
            .filter(|n| indeg.get(n).copied().unwrap_or(0) > 0)
            .map(str::to_owned)
            .collect(),
    )
}

/// A sample's own TPQ/TAQ combined with its group's, tightest bound winning:
/// (taq_age_bp, tpq_age_bp) -- (min, max) -- either possibly None, meaning
/// unbounded on that side. Each argument is a (taq, tpq) pair.
pub fn effective_bounds(
    node: (Option<f64>, Option<f64>),
    group: Option<(Option<f64>, Option<f64>)>,
) -> (Option<f64>, Option<f64>) {
    let (group_taq, group_tpq) = group.unwrap_or((None, None));
    // tightest = largest minimum, smallest maximum
    let taq = [node.0, group_taq].into_iter().flatten().reduce(py_max);
    let tpq = [node.1, group_tpq].into_iter().flatten().reduce(py_min);
    (taq, tpq)
}

/// [`effective_bounds`] for a measurement and the (taq, tpq) its element
/// inherits.
pub fn measurement_bounds(
    m: &Measurement,
    element: (Option<f64>, Option<f64>),
) -> (Option<f64>, Option<f64>) {
    effective_bounds((m.taq_age_bp, m.tpq_age_bp), Some(element))
}
