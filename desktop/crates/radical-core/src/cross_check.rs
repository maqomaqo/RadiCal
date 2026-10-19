//! A direct calculation of a sampled model, to check the sampler against.
//! Port of `services/cross_check.py`.
//!
//! The sampler's own diagnostics compare it with itself -- one half of a run
//! with the other, one chain with another -- and neither sees a calibration
//! lobe no chain is in, or a target wrong in the same way for every chain.
//! Only a calculation that does not sample can, so this makes one wherever it
//! is cheap enough, for three shapes and their combinations: a CHAIN the
//! exact solver does not recognise because it is written with nested
//! Sequences; PARALLEL CHAINS between an outer pair of Boundaries; ORDERED
//! BLOCKS of dates inside a pair; and dates OUTSIDE EVERY BOUNDARY at either
//! end. Anything else is reported as not checked, with the reason.
//!
//! The same model, not a neighbour: the likelihood at every outlier scale,
//! the Boundaries' windows and the span-correction rows all come from a
//! sampler [`Model`] built on this calculation's grid, and the interval
//! integrals are the exact solver's. An outlier model's SHARED scale is
//! handled by conditioning on it: each combination of scale nodes is
//! integrated and the results mixed by their evidence.
//!
//! What is compared is the Boundaries, each on REPORTING_STEP_YEARS bins, by
//! total variation distance and by the largest difference in the probability
//! of being older than a given year.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::time::Instant;

use crate::Error;
use crate::exact_chain::ordered::{OrderedGroup, log_interval_integral};
use crate::exact_chain::tables::{build_grid, date_tables};
use crate::exact_chain::{BOUNDARY_TIE_WEIGHT, Chain, REPORTING_STEP_YEARS, layout};
use crate::exact_parallel::{flattened, ids, outside, within};
use crate::gibbs::Model;
use crate::gibbs::solve::Sampled;
use crate::matrix::Matrix;
use crate::measurement::{Calibrator, Measurement};
use crate::model_tree::{Element, inherited_bounds};
use crate::numeric::{
    arange, argsort_quicksort, cumsum, median, py_max, py_min, searchsorted_left, sum,
};

/// Total variation distance above which a sampled Boundary is reported as
/// differing from the direct one.
pub const DISAGREEMENT: f64 = 0.15;
/// The largest difference in the probability of a Boundary being older than
/// a given year above which the two also differ, on runs of several chains.
pub const THRESHOLD_DISAGREEMENT: f64 = 0.05;
/// Most cells on the direct calculation's grid; the cost goes as M^3.
pub const MAX_CELLS: f64 = 800.0;
/// Room beyond the dates' calibrated ranges for the outermost Boundaries.
pub const PAD_YEARS: f64 = 300.0;
pub const MAX_SCALE_COMBINATIONS: usize = 1024;
/// Marginals are formed for the most probable scale combinations until they
/// hold this much of the evidence, and for no more than this many.
pub const SCALE_MASS: f64 = 0.995;
pub const MAX_MARGINAL_COMBINATIONS: usize = 48;
/// Mass a Boundary may have in the grid's outermost cells before the grid is
/// judged to have cut it off.
pub const EDGE_MASS: f64 = 1e-3;

/// Why a model was not checked.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NotChecked(pub String);

impl From<Error> for NotChecked {
    fn from(error: Error) -> Self {
        Self(error.to_string())
    }
}

fn not_checked<T>(reason: &str) -> Result<T, NotChecked> {
    Err(NotChecked(reason.to_owned()))
}

/// Densities on the grid by Boundary id, in the order they were met.
type Densities = Vec<(String, Vec<f64>)>;

/// The dates outside every Boundary, (older end, younger end).
type Tails = (Vec<String>, Vec<String>);
/// The outer pair (S, E, dates directly between them).
type Outer = (String, String, Vec<String>);

fn has_boundary(element: &Element) -> bool {
    element.is("boundary") || element.children.iter().any(has_boundary)
}

fn ids_of(elements: &[Element]) -> HashSet<String> {
    let mut found = HashSet::new();
    for element in elements {
        ids(element, &mut found);
    }
    found
}

/// The flattened model with the dates outside every Boundary at its two ends
/// taken off, and those dates.
fn tails(flat: &Element, measurements: &[Measurement]) -> Result<(Element, Tails), NotChecked> {
    let positions: Vec<usize> = (0..flat.children.len())
        .filter(|&i| has_boundary(&flat.children[i]))
        .collect();
    let (Some(&first), Some(&last)) = (positions.first(), positions.last()) else {
        return not_checked("the model has no Boundary to check");
    };
    let dates_of = |elements: &[Element]| -> Vec<String> {
        let inside = ids_of(elements);
        measurements
            .iter()
            .filter(|m| m.element_id.as_ref().is_some_and(|e| inside.contains(e)))
            .map(|m| m.id.clone())
            .collect()
    };
    let older = dates_of(&flat.children[..first]);
    let younger = dates_of(&flat.children[last + 1..]);
    let outside: HashSet<&String> = older.iter().chain(&younger).collect();
    if measurements
        .iter()
        .any(|m| outside.contains(&m.id) && m.outlier_prior() > 0.0)
    {
        return not_checked("a date outside every Boundary carries an outlier model");
    }
    let mut trimmed = flat.clone();
    trimmed.children = flat.children[first..=last].to_vec();
    Ok((trimmed, (older, younger)))
}

/// (outer pair, branches, tails); NotChecked for any other shape.
fn shape(
    root: &Element,
    measurements: &[Measurement],
) -> Result<(Option<Outer>, Vec<Chain>, Tails), NotChecked> {
    if !root.is("sequence") {
        return not_checked("the model is not a Sequence");
    }
    let (flat, tails) = tails(&flattened(root), measurements)?;
    let outside: HashSet<&String> = tails.0.iter().chain(&tails.1).collect();
    let inside: Vec<Measurement> = measurements
        .iter()
        .filter(|m| !outside.contains(&m.id))
        .cloned()
        .collect();
    if let Some(chain) = layout(Some(&flat), &inside, true, false) {
        return Ok((None, vec![chain], tails));
    }
    let kinds: Vec<&str> = flat
        .children
        .iter()
        .map(|c| c.element_type.as_str())
        .collect();
    if kinds != ["boundary", "phase", "boundary"] {
        return not_checked(
            "the model is neither one chain nor parallel chains between two Boundaries",
        );
    }
    let outer_phase = &flat.children[1];
    let phase_name = outer_phase
        .name
        .clone()
        .unwrap_or_else(|| "None".to_owned());
    let mut branches = Vec::new();
    let mut covered: HashSet<String> = HashSet::from([outer_phase.id.clone()]);
    for child in &outer_phase.children {
        let child_name = child.name.clone().unwrap_or_else(|| "None".to_owned());
        if !child.is("sequence") {
            return not_checked(&format!(
                "'{child_name}' inside '{phase_name}' is not a Sequence"
            ));
        }
        let mut within_child = HashSet::new();
        ids(child, &mut within_child);
        let members: Vec<Measurement> = inside
            .iter()
            .filter(|m| {
                m.element_id
                    .as_ref()
                    .is_some_and(|e| within_child.contains(e))
            })
            .cloned()
            .collect();
        let Some(branch) = layout(Some(&flattened(child)), &members, true, false) else {
            return not_checked(&format!("'{child_name}' is not a chain"));
        };
        branches.push(branch);
        covered.extend(within_child);
    }
    if branches.is_empty() {
        return not_checked("the outer Phase holds no Sequences");
    }
    if inside
        .iter()
        .any(|m| !m.element_id.as_ref().is_some_and(|e| covered.contains(e)))
    {
        return not_checked("some dates sit outside every branch");
    }
    let loose = inside
        .iter()
        .filter(|m| m.element_id.as_deref() == Some(outer_phase.id.as_str()))
        .map(|m| m.id.clone())
        .collect();
    Ok((
        Some((
            flat.children[0].id.clone(),
            flat.children[2].id.clone(),
            loose,
        )),
        branches,
        tails,
    ))
}

const NOT_BLOCKS: &str = "some dates in a group are ordered against each other in a way that is not one block after another";

/// A group's dates as BLOCKS, oldest first: unordered within a block, every
/// date of a block ordered against every date of every other. Read from the
/// sampler's own ordering, so it is the model as written.
fn blocks(
    model: &Model,
    reach: &[HashSet<usize>],
    dates: &[String],
) -> Result<Vec<Vec<String>>, NotChecked> {
    let rows: Vec<usize> = dates.iter().map(|d| model.param_row[d]).collect();
    let ordered = |a: usize, b: usize| reach[a].contains(&b) || reach[b].contains(&a);
    let mut parent: HashMap<usize, usize> = rows.iter().map(|&r| (r, r)).collect();
    fn find(parent: &mut HashMap<usize, usize>, mut d: usize) -> usize {
        while parent[&d] != d {
            let grand = parent[&parent[&d]];
            parent.insert(d, grand);
            d = grand;
        }
        d
    }
    for (i, &a) in rows.iter().enumerate() {
        for &b in &rows[i + 1..] {
            if !ordered(a, b) {
                let (ra, rb) = (find(&mut parent, a), find(&mut parent, b));
                parent.insert(ra, rb);
            }
        }
    }
    let mut groups: Vec<(usize, Vec<usize>)> = Vec::new();
    for &d in &rows {
        let root = find(&mut parent, d);
        match groups.iter_mut().find(|(r, _)| *r == root) {
            Some((_, members)) => members.push(d),
            None => groups.push((root, vec![d])),
        }
    }
    let mut found: Vec<Vec<usize>> = groups.into_iter().map(|(_, m)| m).collect();
    for block in &found {
        for (i, &a) in block.iter().enumerate() {
            if block[i + 1..].iter().any(|&b| ordered(a, b)) {
                return not_checked(NOT_BLOCKS);
            }
        }
    }
    // Oldest first: a block is older than the ones its dates all precede.
    let keys: Vec<usize> = (0..found.len())
        .map(|i| {
            (0..found.len())
                .filter(|&o| o != i && reach[found[i][0]].contains(&found[o][0]))
                .count()
        })
        .collect();
    let mut order: Vec<usize> = (0..found.len()).collect();
    order.sort_by_key(|&i| std::cmp::Reverse(keys[i]));
    found = order.into_iter().map(|i| found[i].clone()).collect();
    for (i, older) in found.iter().enumerate() {
        for younger in &found[i + 1..] {
            if !older
                .iter()
                .all(|&o| younger.iter().all(|y| reach[o].contains(y)))
            {
                return not_checked(NOT_BLOCKS);
            }
        }
    }
    Ok(found
        .into_iter()
        .map(|b| b.into_iter().map(|r| model.params[r].clone()).collect())
        .collect())
}

/// NotChecked unless the shape is the model AS WRITTEN: the same span groups
/// and the same ordering between every pair of parameters, transitively.
fn same_model(
    model: &Model,
    reach: &[HashSet<usize>],
    outer: Option<&Outer>,
    branches: &[Chain],
    tails: &Tails,
) -> Result<(), NotChecked> {
    type Group = (String, String, BTreeSet<String>);
    let mut groups: Vec<Group> = Vec::new();
    for chain in branches {
        for (index, dates) in chain.groups.iter().enumerate() {
            groups.push((
                chain.boundary_ids[index].clone(),
                chain.boundary_ids[index + 1].clone(),
                dates.iter().cloned().collect(),
            ));
        }
    }
    if let Some((s, e, loose)) = outer {
        groups.push((s.clone(), e.clone(), loose.iter().cloned().collect()));
    }
    let written: BTreeSet<Group> = model
        .group_dates
        .iter()
        .filter(|(_, _, dates)| !dates.is_empty())
        .map(|(o, y, dates)| {
            (
                model.params[*o].clone(),
                model.params[*y].clone(),
                dates.iter().map(|&d| model.params[d].clone()).collect(),
            )
        })
        .collect();
    let stated: BTreeSet<Group> = groups.into_iter().filter(|g| !g.2.is_empty()).collect();
    if written != stated {
        return not_checked("its groups are not the ones the written model has");
    }

    let n = model.params.len();
    let mut younger_than: Vec<HashSet<usize>> = vec![HashSet::new(); n];
    let row = |id: &str| model.param_row[id];
    let mut order = |older: usize, younger: usize| {
        younger_than[older].insert(younger);
    };
    let inner = |dates: &[String], order: &mut dyn FnMut(usize, usize)| -> Result<(), NotChecked> {
        if dates.len() > 1 {
            let found = blocks(model, reach, dates)?;
            for (i, older) in found.iter().enumerate() {
                for younger in &found[i + 1..] {
                    for o in older {
                        for y in younger {
                            order(row(o), row(y));
                        }
                    }
                }
            }
        }
        Ok(())
    };
    for chain in branches {
        for (index, dates) in chain.groups.iter().enumerate() {
            let (older, younger) = (
                row(&chain.boundary_ids[index]),
                row(&chain.boundary_ids[index + 1]),
            );
            order(older, younger);
            for d in dates {
                order(older, row(d));
                order(row(d), younger);
            }
            inner(dates, &mut order)?;
        }
    }
    if let Some((s, e, loose)) = outer {
        for chain in branches {
            order(row(s), row(&chain.boundary_ids[0]));
            order(
                row(&chain.boundary_ids[chain.boundary_ids.len() - 1]),
                row(e),
            );
        }
        for d in loose {
            order(row(s), row(d));
            order(row(d), row(e));
        }
        inner(loose, &mut order)?;
    }
    let first = outer.map_or_else(|| row(&branches[0].boundary_ids[0]), |o| row(&o.0));
    let last_chain = &branches[branches.len() - 1];
    let last = outer.map_or_else(
        || row(&last_chain.boundary_ids[last_chain.boundary_ids.len() - 1]),
        |o| row(&o.1),
    );
    for d in &tails.0 {
        order(row(d), first);
    }
    for d in &tails.1 {
        order(last, row(d));
    }
    inner(&tails.0, &mut order)?;
    inner(&tails.1, &mut order)?;

    fn closure(
        row: usize,
        edges: &[HashSet<usize>],
        closed: &mut Vec<Option<HashSet<usize>>>,
    ) -> HashSet<usize> {
        if let Some(found) = &closed[row] {
            return found.clone();
        }
        closed[row] = Some(HashSet::new());
        let mut found = HashSet::new();
        for &y in &edges[row] {
            found.insert(y);
            found.extend(closure(y, edges, closed));
        }
        closed[row] = Some(found.clone());
        found
    }
    let mut closed = vec![None; n];
    let implied: Vec<HashSet<usize>> = (0..n)
        .map(|r| closure(r, &younger_than, &mut closed))
        .collect();
    if implied != reach {
        return not_checked("its ordering is not the one the written model has");
    }
    Ok(())
}

/// The direct calculation's grid: the sampler's step where that fits in
/// MAX_CELLS, over the dates' calibrated ranges plus room for the Boundaries.
fn grid(
    measurements: &[Measurement],
    root: &Element,
    cal: &Calibrator,
) -> Result<Vec<f64>, NotChecked> {
    let element_bounds = inherited_bounds(root);
    let base = build_grid(measurements, &element_bounds, 1, cal)?;
    let tables = date_tables(&base, measurements, &element_bounds, None, None, cal)?;
    let (mut lows, mut highs) = (Vec::new(), Vec::new());
    for m in measurements {
        let row = tables
            .outlier_parts
            .get(&m.id)
            .map_or(&tables.likelihood[&m.id], |p| &p.0);
        let raw = cumsum(row);
        let last = raw[raw.len() - 1];
        if last <= 0.0 {
            continue;
        }
        let cdf: Vec<f64> = raw.iter().map(|c| c / last).collect();
        lows.push(base[searchsorted_left(&cdf, 1e-4)]);
        highs.push(base[(base.len() - 1).min(searchsorted_left(&cdf, 1.0 - 1e-4))]);
    }
    if lows.is_empty() {
        return not_checked("no date has a likelihood on the grid");
    }
    let lowest = lows.iter().copied().fold(f64::INFINITY, f64::min);
    let highest = highs.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let pad = py_max(PAD_YEARS, highest - lowest);
    let low = py_max(base[0], lowest - pad);
    let high = py_min(base[base.len() - 1], highest + pad);
    let base_step = base[1] - base[0];
    let step = base_step * (((high - low) / MAX_CELLS / base_step).ceil() as usize).max(1) as f64;
    Ok(arange(low, high + step / 2.0, step))
}

/// (matrix of exp(log - peak) where finite, else 0; the peak), or zeros.
fn exp_scaled(log_matrix: &Matrix) -> (Matrix, f64) {
    let peak = log_matrix
        .data()
        .iter()
        .copied()
        .filter(|v| v.is_finite())
        .fold(f64::NEG_INFINITY, f64::max);
    if peak == f64::NEG_INFINITY {
        return (Matrix::zeros(log_matrix.rows(), log_matrix.cols()), 0.0);
    }
    (
        log_matrix.map(|v| if v.is_finite() { (v - peak).exp() } else { 0.0 }),
        peak,
    )
}

/// (matrix over its peak, log of the peak), unchanged where the peak is not
/// positive.
fn scaled(matrix: Matrix) -> (Matrix, f64) {
    let peak = matrix.max();
    if peak <= 0.0 {
        return (matrix, 0.0);
    }
    (matrix.map(|v| v / peak), peak.ln())
}

/// One branch's end-to-end product and, with messages, its prefix and suffix
/// products.
struct Branch {
    j: Matrix,
    log_scale: f64,
    prefix: Vec<Matrix>,
    suffix: Vec<Matrix>,
}

/// The integration itself, on one grid, for one model.
struct Direct<'a> {
    model: &'a Model,
    reach: &'a [HashSet<usize>],
    outer: Option<&'a Outer>,
    branches: &'a [Chain],
    m: usize,
    dt: f64,
    ok: HashMap<String, Vec<bool>>,
    tail_older: Option<Vec<f64>>,
    tail_younger: Option<Vec<f64>>,
}

impl<'a> Direct<'a> {
    fn new(
        model: &'a Model,
        reach: &'a [HashSet<usize>],
        outer: Option<&'a Outer>,
        branches: &'a [Chain],
        tails: &Tails,
    ) -> Result<Self, NotChecked> {
        let m = model.m;
        let ok = model
            .boundary_rows
            .iter()
            .map(|&b| {
                let (lo, hi) = model.window[b];
                (
                    model.params[b].clone(),
                    (0..m as i64).map(|i| i >= lo && i <= hi).collect(),
                )
            })
            .collect();
        let mut direct = Self {
            model,
            reach,
            outer,
            branches,
            m,
            dt: model.dt,
            ok,
            tail_older: None,
            tail_younger: None,
        };
        direct.tail_older = direct.tail_weight(&tails.0, true)?;
        direct.tail_younger = direct.tail_weight(&tails.1, false)?;
        Ok(direct)
    }

    fn cells(&self, a: usize, b: usize) -> i64 {
        a as i64 - b as i64
    }

    fn likelihood(&self, date: &str, nodes: &HashMap<String, usize>) -> &'a [f64] {
        let row = self.model.param_row[date];
        match self.model.model_of[row] {
            None => &self.model.likelihood[row],
            Some(m) => &self.model.likelihood_at[&row][nodes[&self.model.outlier_models[m]]],
        }
    }

    /// g over the end Boundary: the dates outside it, all older (or all
    /// younger), in their own order, with a flat prior. Peak one.
    fn tail_weight(
        &self,
        dates: &[String],
        oldest_end: bool,
    ) -> Result<Option<Vec<f64>>, NotChecked> {
        if dates.is_empty() {
            return Ok(None);
        }
        let found = if dates.len() > 1 {
            blocks(self.model, self.reach, dates)?
        } else {
            vec![dates.to_vec()]
        };
        let log_z = if found.len() > 1 {
            self.log_ordered(&found, &HashMap::new())
        } else {
            let mut z = Matrix::zeros(self.m, self.m);
            for d in &found[0] {
                z.zip_inplace(&self.interval(d, &HashMap::new()), |a, b| a + b);
            }
            z
        };
        let log_g: Vec<f64> = if oldest_end {
            log_z.row(self.m - 1).to_vec()
        } else {
            log_z.column(0)
        };
        let peak = log_g
            .iter()
            .copied()
            .filter(|v| v.is_finite())
            .fold(f64::NEG_INFINITY, f64::max);
        if peak == f64::NEG_INFINITY {
            return not_checked("a date outside every Boundary has no room beside it");
        }
        Ok(Some(
            log_g
                .iter()
                .map(|&v| if v.is_finite() { (v - peak).exp() } else { 0.0 })
                .collect(),
        ))
    }

    fn with_tails(&self, weight: Matrix) -> Matrix {
        let mut weight = weight;
        if let Some(t) = &self.tail_older {
            weight = Matrix::from_fn(self.m, self.m, |r, c| weight.get(r, c) * t[r]);
        }
        if let Some(t) = &self.tail_younger {
            weight = Matrix::from_fn(self.m, self.m, |r, c| weight.get(r, c) * t[c]);
        }
        weight
    }

    /// A date's interval integrals, [older, younger], in the log.
    fn interval(&self, date: &str, nodes: &HashMap<String, usize>) -> Matrix {
        let row = self.likelihood(date, nodes);
        let cumulative: Vec<f64> = cumsum(row).into_iter().map(|c| c * self.dt).collect();
        log_interval_integral(&cumulative, row, self.dt).transpose()
    }

    /// The dates' integral over a group of ordered blocks, [older, younger].
    fn log_ordered(&self, found: &[Vec<String>], nodes: &HashMap<String, usize>) -> Matrix {
        let mut likelihood = HashMap::new();
        let mut cumulative = HashMap::new();
        for d in found.iter().flatten() {
            let row = self.likelihood(d, nodes).to_vec();
            cumulative.insert(
                d.clone(),
                cumsum(&row).into_iter().map(|c| c * self.dt).collect(),
            );
            likelihood.insert(d.clone(), row);
        }
        OrderedGroup::new(found, &self.model.grid, &cumulative, &likelihood)
            .log_inner
            .transpose()
    }

    /// A group's span factor and its dates' interval integrals, in the log,
    /// [older boundary, younger boundary], with the tie weights on the
    /// diagonal the sampler gives them.
    fn log_group(
        &self,
        dates: &[String],
        nodes: &HashMap<String, usize>,
    ) -> Result<Matrix, NotChecked> {
        let k = dates.len() as f64;
        let mut term = Matrix::from_fn(self.m, self.m, |a, b| {
            let cells = self.cells(a, b);
            if cells > 0 {
                if dates.is_empty() {
                    0.0
                } else {
                    -k * ((cells.max(1) as f64) * self.dt).ln()
                }
            } else {
                f64::NEG_INFINITY
            }
        });
        let found = if dates.len() > 1 {
            blocks(self.model, self.reach, dates)?
        } else {
            Vec::new()
        };
        if found.len() > 1 {
            term.zip_inplace(&self.log_ordered(&found, nodes), |a, b| a + b);
        } else {
            for d in dates {
                term.zip_inplace(&self.interval(d, nodes), |a, b| a + b);
            }
        }
        let members: HashSet<usize> = dates.iter().map(|d| self.model.param_row[d]).collect();
        let tied = self
            .model
            .tie_pairs
            .iter()
            .filter(|(y, o)| members.contains(y) && members.contains(o))
            .count();
        let mut tie = vec![BOUNDARY_TIE_WEIGHT.ln() - tied as f64 * std::f64::consts::LN_2; self.m];
        for d in dates {
            for (t, l) in tie.iter_mut().zip(self.likelihood(d, nodes)) {
                *t += l.ln();
            }
        }
        term.set_diagonal(&tie);
        Ok(term)
    }

    /// The span prior over a pair, in the log, [oldest, youngest].
    fn span_correction(&self, oldest: &str, youngest: &str) -> Matrix {
        let (o, y) = (self.model.param_row[oldest], self.model.param_row[youngest]);
        let row = self
            .model
            .span_priors
            .iter()
            .zip(&self.model.span_prior_rows)
            .find(|((po, py, _), _)| (*po, *py) == (o, y))
            .map(|(_, r)| r);
        Matrix::from_fn(self.m, self.m, |a, b| {
            let cells = self.cells(a, b);
            if cells < 0 {
                f64::NEG_INFINITY
            } else {
                row.map_or(0.0, |r| r[(cells as usize).min(self.m - 1)])
            }
        })
    }

    fn masked(&self, log: Matrix, first: &str, last: &str) -> Matrix {
        let (f, l) = (&self.ok[first], &self.ok[last]);
        Matrix::from_fn(self.m, self.m, |a, b| {
            if f[a] && l[b] {
                log.get(a, b)
            } else {
                f64::NEG_INFINITY
            }
        })
    }

    fn branch(
        &self,
        chain: &Chain,
        nodes: &HashMap<String, usize>,
        with_messages: bool,
    ) -> Result<Branch, NotChecked> {
        let mut transfers = Vec::new();
        let mut log_scale = 0.0;
        for (gi, dates) in chain.groups.iter().enumerate() {
            let log_t = self.masked(
                self.log_group(dates, nodes)?,
                &chain.boundary_ids[gi],
                &chain.boundary_ids[gi + 1],
            );
            let (matrix, peak) = exp_scaled(&log_t);
            transfers.push(matrix);
            log_scale += peak;
        }
        let diag = |id: &str| {
            Matrix::diag(
                &self.ok[id]
                    .iter()
                    .map(|&o| if o { 1.0 } else { 0.0 })
                    .collect::<Vec<_>>(),
            )
        };
        let mut prefix = vec![diag(&chain.boundary_ids[0])];
        for matrix in &transfers {
            let (product, factor) = scaled(prefix[prefix.len() - 1].matmul(matrix));
            log_scale += factor;
            prefix.push(product);
        }
        let j = prefix[prefix.len() - 1].clone();
        let mut suffix = Vec::new();
        if with_messages {
            suffix.push(diag(&chain.boundary_ids[chain.boundary_ids.len() - 1]));
            for matrix in transfers.iter().rev() {
                let next = scaled(matrix.matmul(&suffix[suffix.len() - 1])).0;
                suffix.push(next);
            }
            suffix.reverse();
        }
        Ok(Branch {
            j,
            log_scale,
            prefix,
            suffix,
        })
    }

    /// (log evidence, Boundary marginals in order) with every scale fixed.
    fn solve(
        &self,
        nodes: &HashMap<String, usize>,
        with_marginals: bool,
    ) -> Result<(f64, Densities), NotChecked> {
        let parts = self
            .branches
            .iter()
            .map(|c| self.branch(c, nodes, with_marginals))
            .collect::<Result<Vec<_>, _>>()?;
        let mut marginals: Vec<(String, Vec<f64>)> = Vec::new();
        let weights: Vec<Matrix>;
        let log_evidence;
        match self.outer {
            None => {
                let chain = &self.branches[0];
                let (first, last) = (
                    &chain.boundary_ids[0],
                    &chain.boundary_ids[chain.boundary_ids.len() - 1],
                );
                let log_c = self.masked(self.span_correction(first, last), first, last);
                let (w, w_scale) = exp_scaled(&log_c);
                let w = self.with_tails(w);
                let total = w.zip_map(&parts[0].j, |a, b| a * b).sum();
                if total <= 0.0 {
                    return Ok((f64::NEG_INFINITY, Vec::new()));
                }
                log_evidence = total.ln() + w_scale + parts[0].log_scale;
                weights = vec![w];
            }
            Some((s, e, loose)) => {
                let mut log_a = self.span_correction(s, e);
                log_a.zip_inplace(&self.log_group(loose, nodes)?, |a, b| a + b);
                let mut diagonal = log_a.diagonal();
                for v in &mut diagonal {
                    *v -= BOUNDARY_TIE_WEIGHT.ln();
                }
                log_a.set_diagonal(&diagonal);
                let (a0, a_scale) = exp_scaled(&self.masked(log_a, s, e));
                let a0 = self.with_tails(a0);
                let mut evidence = a_scale;
                let mut inner = Vec::new();
                for part in &parts {
                    let (z, z_scale) = scaled(within(&part.j));
                    inner.push(z);
                    evidence += z_scale + part.log_scale;
                }
                let mut product = inner[0].clone();
                for z in &inner[1..] {
                    product.zip_inplace(z, |a, b| a * b);
                }
                let joint = a0.zip_map(&product, |a, b| a * b);
                let total = joint.sum();
                if total <= 0.0 {
                    return Ok((f64::NEG_INFINITY, Vec::new()));
                }
                log_evidence = evidence + total.ln();
                let mut w = Vec::new();
                if with_marginals {
                    marginals.push((s.clone(), joint.row_sums()));
                    marginals.push((e.clone(), joint.column_sums()));
                    for b in 0..parts.len() {
                        let mut others = a0.clone();
                        for (o, z) in inner.iter().enumerate() {
                            if o != b {
                                others.zip_inplace(z, |x, y| x * y);
                            }
                        }
                        w.push(outside(&others));
                    }
                }
                weights = w;
            }
        }
        if with_marginals {
            for ((chain, part), w) in self.branches.iter().zip(&parts).zip(&weights) {
                for (k, id) in chain.boundary_ids.iter().enumerate() {
                    let left = part.prefix[k].transpose().matmul(w);
                    marginals.push((
                        id.clone(),
                        left.zip_map(&part.suffix[k], |a, b| a * b).row_sums(),
                    ));
                }
            }
        }
        Ok((log_evidence, marginals))
    }
}

/// The direct calculation's answer.
#[derive(Debug, Clone, PartialEq)]
pub struct Reference {
    pub grid: Vec<f64>,
    pub shape: &'static str,
    pub outside: usize,
    /// Each Boundary's density on `grid`, in the order the calculation met
    /// them.
    pub boundaries: Vec<(String, Vec<f64>)>,
    pub names: HashMap<String, String>,
}

/// The direct calculation, or why it was not made.
pub fn reference(
    measurements: &[Measurement],
    root: &Element,
    uniform_span_prior: bool,
    cal: &Calibrator,
) -> Result<Reference, NotChecked> {
    let (outer, branches, tails) = shape(root, measurements)?;
    let grid = grid(measurements, root, cal)?;
    let model = Model::new(
        measurements,
        root,
        Some(grid.clone()),
        uniform_span_prior,
        cal,
    )?;
    if !model.depositions.is_empty() || !model.level_rows.is_empty() {
        return not_checked("the model has a deposition sequence");
    }
    let reach = model.younger_reachable();
    same_model(&model, &reach, outer.as_ref(), &branches, &tails)?;
    let expected = branches.iter().map(|c| c.boundary_ids.len()).sum::<usize>()
        + if outer.is_some() { 2 } else { 0 };
    let mut implied: HashSet<String> = branches
        .iter()
        .flat_map(|c| c.boundary_ids.iter().cloned())
        .collect();
    if let Some((s, e, _)) = &outer {
        implied.insert(s.clone());
        implied.insert(e.clone());
    }
    let actual: HashSet<String> = model
        .boundary_rows
        .iter()
        .map(|&b| model.params[b].clone())
        .collect();
    if actual != implied || model.boundary_rows.len() != expected {
        return not_checked("the Boundaries are not the ones this shape implies");
    }
    let ends = match &outer {
        Some((s, e, _)) => (model.param_row[s], model.param_row[e]),
        None => (
            model.param_row[&branches[0].boundary_ids[0]],
            model.param_row[&branches[0].boundary_ids[branches[0].boundary_ids.len() - 1]],
        ),
    };
    let priors: Vec<(usize, usize)> = model.span_priors.iter().map(|&(o, y, _)| (o, y)).collect();
    if uniform_span_prior && expected > 2 && priors != [ends] {
        return not_checked("the span correction does not span the whole model");
    }

    let direct = Direct::new(&model, &reach, outer.as_ref(), &branches, &tails)?;
    let counts: Vec<usize> = model.scale_grid.iter().map(Vec::len).collect();
    let total: usize = counts.iter().product();
    if total > MAX_SCALE_COMBINATIONS {
        return not_checked("too many outlier models to integrate over");
    }
    // itertools.product order: the last model's node varying fastest.
    let combinations: Vec<Vec<usize>> = (0..total)
        .map(|mut i| {
            let mut combo = vec![0; counts.len()];
            for (slot, &n) in combo.iter_mut().zip(&counts).rev() {
                *slot = i % n;
                i /= n;
            }
            combo
        })
        .collect();
    let nodes_of = |combo: &[usize]| -> HashMap<String, usize> {
        model
            .outlier_models
            .iter()
            .cloned()
            .zip(combo.iter().copied())
            .collect()
    };
    let log_evidence = combinations
        .iter()
        .map(|c| direct.solve(&nodes_of(c), false).map(|r| r.0))
        .collect::<Result<Vec<f64>, _>>()?;
    let best = log_evidence
        .iter()
        .copied()
        .filter(|v| v.is_finite())
        .fold(f64::NEG_INFINITY, f64::max);
    if best == f64::NEG_INFINITY {
        return not_checked("the direct calculation found no posterior mass");
    }
    let raw: Vec<f64> = log_evidence
        .iter()
        .map(|&v| (v - best).exp())
        .map(|w| if w.is_finite() { w } else { 0.0 })
        .collect();
    let mass = sum(&raw);
    let weights: Vec<f64> = raw.iter().map(|w| w / mass).collect();
    let mut order = argsort_quicksort(&weights);
    order.reverse();
    let held = cumsum(&order.iter().map(|&i| weights[i]).collect::<Vec<_>>());
    let take = MAX_MARGINAL_COMBINATIONS.min(searchsorted_left(&held, SCALE_MASS) + 1);
    let mut chosen: Vec<usize> = order[..take.min(order.len())].to_vec();
    chosen.sort_by(|&a, &b| combinations[a].cmp(&combinations[b]));
    let mut boundaries: Vec<(String, Vec<f64>)> = Vec::new();
    for i in chosen {
        let (_, marginals) = direct.solve(&nodes_of(&combinations[i]), true)?;
        for (id, density) in marginals {
            let total = sum(&density);
            if total > 0.0 {
                let add: Vec<f64> = density.iter().map(|d| weights[i] * d / total).collect();
                match boundaries.iter_mut().find(|(b, _)| *b == id) {
                    Some((_, acc)) => {
                        for (a, x) in acc.iter_mut().zip(&add) {
                            *a += x;
                        }
                    }
                    None => boundaries.push((id, add)),
                }
            }
        }
    }
    for (_, density) in &mut boundaries {
        let total = sum(density);
        for d in density.iter_mut() {
            *d /= total;
        }
        let n = density.len();
        if sum(&density[..2]) + sum(&density[n - 2..]) > EDGE_MASS {
            return not_checked("a Boundary runs off the edge of the calculation");
        }
    }
    Ok(Reference {
        grid,
        shape: if outer.is_some() { "parallel" } else { "chain" },
        outside: tails.0.len() + tails.1.len(),
        boundaries,
        names: model.plan.boundary_names.iter().cloned().collect(),
    })
}

/// One Boundary compared.
#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub id: String,
    pub name: String,
    pub distance: f64,
    pub threshold_gap: f64,
    pub sampled_mean: f64,
    pub direct_mean: f64,
}

fn binned(xs: &[f64], mass: &[f64], low: f64, count: usize) -> Vec<f64> {
    let mut out = vec![0.0; count];
    for (&x, &m) in xs.iter().zip(mass) {
        let bin = ((x - low) / REPORTING_STEP_YEARS).floor() as i64;
        if bin >= 0 && (bin as usize) < count {
            out[bin as usize] += m;
        }
    }
    out
}

/// Per Boundary, the total variation distance between the sampled marginal
/// (its plotted density) and the direct one, worst first, and the largest
/// difference in the probability of being older than a given year.
pub fn compare(direct: &Reference, sampled: &Sampled) -> Vec<Row> {
    let grid = &direct.grid;
    let low = grid[0];
    let count = ((grid[grid.len() - 1] - low) / REPORTING_STEP_YEARS).ceil() as usize + 1;
    let mut rows = Vec::new();
    for (id, density) in &direct.boundaries {
        let Some((_, _, posterior)) = sampled.boundaries.iter().find(|(b, _, _)| b == id) else {
            continue;
        };
        let points = &posterior.probability_distribution;
        if points.is_empty() {
            continue;
        }
        let xs: Vec<f64> = points.iter().map(|p| p[0]).collect();
        let step = if xs.len() > 1 {
            median(&xs.windows(2).map(|w| w[1] - w[0]).collect::<Vec<_>>())
        } else {
            1.0
        };
        let mass: Vec<f64> = points.iter().map(|p| p[1] * step).collect();
        let total = sum(&mass);
        if total <= 0.0 {
            continue;
        }
        let theirs = binned(
            &xs,
            &mass.iter().map(|m| m / total).collect::<Vec<_>>(),
            low,
            count,
        );
        let ours = binned(grid, density, low, count);
        let differences: Vec<f64> = theirs
            .iter()
            .zip(&ours)
            .map(|(a, b)| (a - b).abs())
            .collect();
        let distance = 0.5 * (sum(&differences) + (1.0 - sum(&theirs)).max(0.0));
        let threshold_gap = cumsum(&theirs)
            .iter()
            .zip(cumsum(&ours))
            .map(|(a, b)| (a - b).abs())
            .fold(f64::NEG_INFINITY, f64::max);
        let weighted: Vec<f64> = xs.iter().zip(&mass).map(|(x, m)| x * m).collect();
        let direct_weighted: Vec<f64> = grid.iter().zip(density).map(|(g, d)| g * d).collect();
        rows.push(Row {
            id: id.clone(),
            name: direct
                .names
                .get(id)
                .cloned()
                .unwrap_or_else(|| "Boundary".to_owned()),
            distance,
            threshold_gap,
            sampled_mean: sum(&weighted) / total,
            direct_mean: sum(&direct_weighted),
        });
    }
    rows.sort_by(|a, b| b.distance.total_cmp(&a.distance));
    rows
}

/// The solver record's `cross_check`.
#[derive(Debug, Clone, PartialEq)]
pub struct CrossCheck {
    /// 'agrees', 'differs' or 'not_checked'.
    pub status: &'static str,
    pub reason: Option<String>,
    pub shape: Option<&'static str>,
    pub threshold: f64,
    /// None on a run of one chain, where only total variation has a limit.
    pub threshold_gap_limit: Option<f64>,
    pub checked: usize,
    pub worst: Option<Row>,
    pub differing: Vec<Row>,
    pub seconds: f64,
}

/// Check a sampled run against the direct calculation, where one can be made.
pub fn check(
    measurements: &[Measurement],
    root: &Element,
    sampled: &Sampled,
    uniform_span_prior: bool,
    cal: &Calibrator,
) -> CrossCheck {
    let started = Instant::now();
    let mut record = CrossCheck {
        status: "not_checked",
        reason: None,
        shape: None,
        threshold: DISAGREEMENT,
        threshold_gap_limit: Some(THRESHOLD_DISAGREEMENT),
        checked: 0,
        worst: None,
        differing: Vec::new(),
        seconds: 0.0,
    };
    let direct = match reference(measurements, root, uniform_span_prior, cal) {
        Ok(d) => d,
        Err(NotChecked(reason)) => {
            record.reason = Some(reason);
            record.seconds = started.elapsed().as_secs_f64();
            return record;
        }
    };
    let rows = compare(&direct, sampled);
    let gap_limit = (sampled.chains > 1).then_some(THRESHOLD_DISAGREEMENT);
    record.threshold_gap_limit = gap_limit;
    record.shape = Some(direct.shape);
    record.checked = rows.len();
    record.worst = rows.first().cloned();
    record.differing = rows
        .iter()
        .filter(|r| r.distance > DISAGREEMENT || gap_limit.is_some_and(|g| r.threshold_gap > g))
        .cloned()
        .collect();
    record.seconds = started.elapsed().as_secs_f64();
    record.status = if !record.differing.is_empty() {
        "differs"
    } else if !rows.is_empty() {
        "agrees"
    } else {
        "not_checked"
    };
    if rows.is_empty() {
        record.reason = Some("no Boundary to compare".to_owned());
    }
    record
}
