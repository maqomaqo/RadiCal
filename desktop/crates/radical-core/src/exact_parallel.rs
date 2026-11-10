//! Exact solver for a Sequence whose stages may hold areas side by side: a
//! site model with trenches or areas, each its own Sequence of Phases and
//! Boundaries, between Boundaries the whole site shares. Port of
//! `services/exact_parallel.py`.
//!
//! ```text
//! Sequence("Site") {
//!   Boundary("Start");
//!   Phase("Early");                      <- an ordinary stage
//!   Boundary("Transition");
//!   Phase("Areas") {                     <- a stage holding areas side by side
//!     Sequence("Area A") { Boundary; Phase; Boundary; Phase; Boundary };
//!     Sequence("Area B") { Boundary; Phase; Boundary };
//!   };
//!   Boundary("End");
//! };
//! ```
//!
//! The chain solver integrates a single line of Boundaries and declines this
//! ("the model branches"). It is still a chain, one level up: given the two
//! Boundaries either side of a stage, its areas are independent of each other
//! and of everything else, so each enters the outer chain through one matrix
//! over that pair.
//!
//! # The maths
//!
//! Outer Boundaries B_0 > ... > B_n, oldest first; stage k lies between
//! S = B_k and E = B_(k+1). An area is a chain with first Boundary f and last
//! Boundary l, and its transfer matrices are built exactly as a chain's are, so
//!
//! ```text
//! J(f, l)  = the area's weight with its ends held: the product of its
//!            transfer matrices (ChainProducts::end_to_end)
//! Z(S, E)  = sum over f <= S and l >= E of J(f, l)         (within)
//! ```
//!
//! with f on S and l on E each a tie between neighbouring Boundaries at
//! BOUNDARY_TIE_WEIGHT, as everywhere else. The stage's transfer matrix is then
//! its own dates' term, as for any group, times the product of its areas' Z,
//! and the outer chain is solved by the chain solver's own message passing.
//!
//! The span correction counts EVERY Boundary in the model, the areas'
//! included, over the outermost pair (B_0, B_n). That is what the sampler
//! applies -- one correction per connected component of the Boundary ordering
//! -- and what keeps the two answering the same model.
//!
//! S and E themselves are not neighbouring Boundaries when an area lies between
//! them, so a stage holding areas gives their meeting no tie weight of its own:
//! the areas' Boundaries, all in that one cell, carry it.
//!
//! Each area is then solved given everything outside it. The weight on its
//! ends is
//!
//! ```text
//! W(f, l)  = sum over S >= f and E <= l of
//!            outside(S, E) * own(S, E) * product of the other areas' Z(S, E)
//!                                                                (outside)
//! ```
//!
//! where outside is the chain solver's pair weight for the stage -- everything
//! beyond S and E -- and own the stage's own dates' term. W is exactly what the
//! span correction is on a chain: a factor coupling the area's two ends. So the
//! area is solved by `messages_with_end_factor` with W in its place, which
//! gives its Boundaries' marginals and the pair weights its dates' marginals
//! are read from, as on a chain.
//!
//! Joint draws follow the same order: the outer Boundaries from the outer
//! chain; each area's first Boundary given its stage's (S, E), from the sum of
//! J over l >= E; its last given that and E, from J; its interior given both
//! ends by the chain solver's own interior fill; and every date given its
//! enclosing pair.
//!
//! # Areas inside an area
//!
//! A stage of an area may hold areas of its own, to any depth: a trench whose
//! one phase is dug as several cuts, each with its own sequence. Nothing new is
//! needed, because every statement above is about a chain and the pair of
//! Boundaries either side of one of its stages, and an area is a chain.
//!
//! Upwards, an area's stage holding areas has the transfer matrix an outer
//! stage has -- its own dates' term, no tie of its own, times its areas' Z --
//! so the area's J and Z are formed from the innermost areas outwards.
//!
//! Downwards, an area solved with its end factor W gives pair weights for its
//! own stages that already hold everything outside each, W included: the rest
//! of the area, and through W the rest of the model. So an area inside it has
//!
//! ```text
//! W'(f', l') = sum over S' >= f' and E' <= l' of
//!              pair(S', E') * own(S', E') * product of its siblings' Z(S', E')
//! ```
//!
//! with pair the enclosing AREA's pair weight for that stage in place of the
//! outer chain's: the same sum one level down, and exact for the same reason
//! -- given the two Boundaries either side of a stage, what the stage holds is
//! independent of everything else. The areas are solved outermost first, and
//! joint draws follow that order: an area's ends given its stage's pair, its
//! interior given its ends, and then the areas inside each of its stages given
//! that stage's pair as just drawn.
//!
//! # What it does NOT cover
//!
//! [`layout_with_reason`] declines, with a reason, a model whose areas are side
//! by side but which is not this shape: areas with no Boundary before and after
//! them that they share (separate models, in effect), an area that is neither
//! a single line nor a line whose stages hold areas, an area with dates outside
//! its own first and last Boundary, or anything ordering one area against
//! another. Those are sampled. Dates outside every outer Boundary are
//! taken under the chain solver's rules for them, as the same weight on the
//! first or last outer Boundary.
//!
//! The outlier scale is weighed and mixed per date, as on a chain.

use std::collections::{BTreeMap, HashMap, HashSet};

use crate::Error;
use crate::exact_chain::messages::{
    ChainProducts, EndFactorMessages, Messages, exp_rescaled, finite_peak, messages,
    messages_with_end_factor, messages_with_span_prior,
};
use crate::exact_chain::ordered::OrderedGroup;
use crate::exact_chain::prior::{pair_limits, span_prior_correction};
use crate::exact_chain::solve::{
    GridSolve, JointIndices, Marginals, ScaleWeights, Solution, SolveOptions, allowed_windows,
    chain_date_marginals, chain_draw_dates, chain_log_transfers, results, two_pass,
};
use crate::exact_chain::tables::{ElementBounds, date_tables, flat_date_tables};
use crate::exact_chain::tails::Tails;
use crate::exact_chain::{
    BOUNDARY_TIE_WEIGHT, Blocks, Chain, NO_BOUNDARY_BETWEEN, Names, Order, blocks_with_reason,
    closure, coupled_dates_reason, edge, layout_with_reason as chain_layout, one_block,
    order_chain, order_tails, outside_every_boundary_reason, tails_with_reason, written_order,
};
use crate::matrix::Matrix;
use crate::measurement::{Calibrator, Measurement};
use crate::model_tree::{Element, ModelPlan, inherited_bounds, plan_model};
use crate::numeric::{python_sum, sum};
use crate::rng::Generator;

// --------------------------------------------------------------------------
// Tree helpers
// --------------------------------------------------------------------------

/// A copy of `element` with every nested Sequence spelled out in place.
pub fn flattened(element: &Element) -> Element {
    let mut out = element.clone();
    out.children = Vec::new();
    for child in &element.children {
        if child.is("sequence") {
            out.children.extend(flattened(child).children);
        } else {
            out.children.push(child.clone());
        }
    }
    out
}

pub(crate) fn ids(element: &Element, found: &mut HashSet<String>) {
    found.insert(element.id.clone());
    for child in &element.children {
        ids(child, found);
    }
}

// --------------------------------------------------------------------------
// Sums over an area's ends
// --------------------------------------------------------------------------

/// Cumulative sum down the columns (`axis` 0) or along the rows (`axis` 1)
/// with the last term at the tie's weight: sum over i < n, plus a half of term
/// n.
pub fn tied_cumsum(matrix: &Matrix, axis: usize) -> Matrix {
    let (rows, cols) = (matrix.rows(), matrix.cols());
    let mut cum = matrix.clone();
    if axis == 0 {
        for r in 1..rows {
            for c in 0..cols {
                let v = cum.get(r - 1, c) + cum.get(r, c);
                cum.set(r, c, v);
            }
        }
    } else {
        for r in 0..rows {
            let row = cum.row_mut(r);
            for c in 1..cols {
                row[c] += row[c - 1];
            }
        }
    }
    cum.zip_inplace(matrix, |s, m| s - (1.0 - BOUNDARY_TIE_WEIGHT) * m);
    cum
}

fn reversed_rows(m: &Matrix) -> Matrix {
    Matrix::from_fn(m.rows(), m.cols(), |r, c| m.get(m.rows() - 1 - r, c))
}

fn reversed_columns(m: &Matrix) -> Matrix {
    Matrix::from_fn(m.rows(), m.cols(), |r, c| m.get(r, m.cols() - 1 - c))
}

/// Z[S, E] = sum over first <= S and last >= E of J[first, last]: an area
/// with its ends inside the pair (S, E), an end on S or on E at the half a tie
/// between neighbouring Boundaries carries.
pub fn within(j: &Matrix) -> Matrix {
    let below_s = tied_cumsum(j, 0);
    reversed_columns(&tied_cumsum(&reversed_columns(&below_s), 1))
}

/// W[first, last] = sum over S >= first and E <= last of A[S, E], a tie at
/// either end at half weight.
pub fn outside(a: &Matrix) -> Matrix {
    let older = reversed_rows(&tied_cumsum(&reversed_rows(a), 0));
    tied_cumsum(&older, 1)
}

/// (matrix scaled to a peak of one, log of the peak), unchanged where the
/// peak is not finite and positive.
fn scaled(mut matrix: Matrix) -> (Matrix, f64) {
    let peak = matrix.max();
    if !peak.is_finite() || peak <= 0.0 {
        return (matrix, 0.0);
    }
    matrix.map_inplace(|v| v / peak);
    (matrix, peak.ln())
}

// --------------------------------------------------------------------------
// Layout
// --------------------------------------------------------------------------

/// One link of the outer chain: the outer Boundaries either side, the dates
/// lying directly between them, as blocks where they are ordered against each
/// other, and any areas side by side there.
#[derive(Debug, Clone, PartialEq)]
pub struct Stage {
    pub older_id: String,
    pub younger_id: String,
    pub dates: Vec<String>,
    pub areas: Vec<Area>,
    pub blocks: Blocks,
}

/// One area: its own line of Boundaries and groups, and the areas side by
/// side inside each of its groups -- `inner`, one list per group, empty for a
/// group that holds only dates.
#[derive(Debug, Clone, PartialEq)]
pub struct Area {
    pub chain: Chain,
    pub inner: Vec<Vec<Area>>,
}

impl Area {
    /// An area that is a single line, with no areas inside it.
    pub fn line(chain: Chain) -> Self {
        let inner = vec![Vec::new(); chain.groups.len()];
        Self { chain, inner }
    }

    /// Whether any of its groups holds areas.
    pub fn nested(&self) -> bool {
        self.inner.iter().any(|areas| !areas.is_empty())
    }

    /// Its Boundaries in model order: each of its own, then those of the areas
    /// in the group after it.
    fn boundary_ids_into(&self, out: &mut Vec<String>) {
        for (index, id) in self.chain.boundary_ids.iter().enumerate() {
            out.push(id.clone());
            for area in self.inner.get(index).into_iter().flatten() {
                area.boundary_ids_into(out);
            }
        }
    }

    /// Its groups of dates, and those of every area inside it.
    fn num_groups(&self) -> usize {
        self.chain.groups.len()
            + self
                .inner
                .iter()
                .flatten()
                .map(Area::num_groups)
                .sum::<usize>()
    }
}

/// A chain of outer Boundaries whose stages may hold areas, oldest first, and
/// the dates outside every Boundary at either end, as a [`Chain`] has.
#[derive(Debug, Clone, PartialEq)]
pub struct Stages {
    pub boundary_ids: Vec<String>,
    pub stages: Vec<Stage>,
    pub boundary_names: Names,
    pub older_tail: Vec<String>,
    pub younger_tail: Vec<String>,
    pub older_tail_blocks: Blocks,
    pub younger_tail_blocks: Blocks,
}

impl Stages {
    /// The outer Boundaries as a Chain, each stage's own dates its group, in
    /// their blocks, with the model's tails.
    pub fn outer_chain(&self) -> Chain {
        let mut chain = Chain::with_tails(
            self.boundary_ids.clone(),
            self.stages.iter().map(|s| s.dates.clone()).collect(),
            self.boundary_names.clone(),
            self.older_tail.clone(),
            self.younger_tail.clone(),
        );
        chain.blocks = self.stages.iter().map(|s| s.blocks.clone()).collect();
        if !self.older_tail_blocks.is_empty() {
            chain.older_tail_blocks = self.older_tail_blocks.clone();
        }
        if !self.younger_tail_blocks.is_empty() {
            chain.younger_tail_blocks = self.younger_tail_blocks.clone();
        }
        chain
    }

    /// Every Boundary in the model, in model order: each outer Boundary, then
    /// the Boundaries of the areas in the stage after it, and of the areas
    /// inside those.
    pub fn all_boundary_ids(&self) -> Vec<String> {
        let mut out = Vec::new();
        for (index, outer) in self.boundary_ids.iter().enumerate() {
            out.push(outer.clone());
            if let Some(stage) = self.stages.get(index) {
                for area in &stage.areas {
                    area.boundary_ids_into(&mut out);
                }
            }
        }
        out
    }

    /// Groups of dates in the model, for the grid's point budget: every stage,
    /// and every group inside every area, at every depth.
    pub fn num_groups(&self) -> usize {
        self.stages.len()
            + self
                .stages
                .iter()
                .flat_map(|s| &s.areas)
                .map(Area::num_groups)
                .sum::<usize>()
    }
}

/// Whether any Phase in `root` holds a Sequence: areas side by side.
pub fn has_areas(root: Option<&Element>) -> bool {
    let Some(root) = root else {
        return false;
    };
    if root.is("phase") && root.children.iter().any(|c| c.is("sequence")) {
        return true;
    }
    root.children.iter().any(|c| has_areas(Some(c)))
}

fn shown(element: &Element) -> String {
    element
        .given_name()
        .unwrap_or(element.element_type.as_str())
        .to_owned()
}

/// `Stages` for a model this module can integrate; `None` for a model with no
/// areas side by side at all, whose reason, if it has one, is the chain
/// solver's to give; a reason for one whose areas sit side by side but which
/// it cannot take.
///
/// Recognised from the tree, as written, and then held to the plan: the
/// groups of dates and the ordering of every Boundary and date must be exactly
/// the ones the model states, or integrating it would answer a different
/// model. Reasons are phrased for an archaeologist, as the chain solver's are.
pub fn layout_with_reason(
    root: Option<&Element>,
    measurements: &[Measurement],
) -> Result<Option<Stages>, String> {
    if !has_areas(root) {
        return Ok(None);
    }
    let root = root.expect("has areas");
    let plan = plan_model(root, measurements);
    if let Some(coupled) = coupled_dates_reason(&plan) {
        return Err(coupled.into());
    }
    if !root.is("sequence") {
        return Err(
            "its side-by-side Sequences have no Boundary before and after them that \
                    they share, so they are separate models rather than areas of one site. Put \
                    them in a Sequence between a Start and an End Boundary to make the model \
                    exactly solvable"
                .into(),
        );
    }

    let mut dates_of: HashMap<Option<String>, Vec<String>> = HashMap::new();
    for m in measurements {
        dates_of
            .entry(m.element_id.clone())
            .or_default()
            .push(m.id.clone());
    }
    let written = Written {
        measurements,
        dates_of,
        closed: closure(&written_order(&plan)),
        names: &plan.boundary_names,
    };
    let subtree_dates = |element: &Element| written.subtree_dates(element);

    let children: Vec<Element> = flattened(root)
        .children
        .into_iter()
        .filter(|c| !c.is("hiatus"))
        .collect();
    let positions: Vec<usize> = (0..children.len())
        .filter(|&i| children[i].is("boundary"))
        .collect();
    if positions.len() < 2 {
        return Err(
            "no Boundary pair encloses its side-by-side areas. Add a Boundary before \
                    and after them"
                .into(),
        );
    }
    let (first, last) = (positions[0], positions[positions.len() - 1]);
    // Dates before the first outer Boundary or after the last are the model's
    // tails, under the chain solver's rules for them.
    let outside_dates: Vec<String> = children[..first]
        .iter()
        .chain(&children[last + 1..])
        .flat_map(&subtree_dates)
        .collect();
    let (mut older_blocks, mut younger_blocks) = (Vec::new(), Vec::new());
    if !outside_dates.is_empty() {
        (older_blocks, younger_blocks) = tails_with_reason(
            &plan,
            measurements,
            &outside_dates,
            &children[first].id,
            &children[last].id,
        )?;
    }
    let older_tail: Vec<String> = older_blocks.iter().flatten().cloned().collect();
    let younger_tail: Vec<String> = younger_blocks.iter().flatten().cloned().collect();

    let stages = written.stages(&children, &positions)?;

    let layout = Stages {
        boundary_ids: positions.iter().map(|&i| children[i].id.clone()).collect(),
        stages,
        boundary_names: plan.boundary_names.clone(),
        older_tail: older_tail.clone(),
        younger_tail: younger_tail.clone(),
        older_tail_blocks: if older_blocks.is_empty() {
            one_block(&older_tail)
        } else {
            older_blocks
        },
        younger_tail_blocks: if younger_blocks.is_empty() {
            one_block(&younger_tail)
        } else {
            younger_blocks
        },
    };
    as_written(&layout, &plan, measurements)?;
    Ok(Some(layout))
}

/// The model as written, for laying out a line of Boundaries and what lies
/// between each pair: the outer Sequence's, or an area's that holds areas.
struct Written<'a> {
    measurements: &'a [Measurement],
    /// {element id: the dates directly in it}.
    dates_of: HashMap<Option<String>, Vec<String>>,
    /// Everything each parameter is older than ([`closure`]).
    closed: Order,
    names: &'a Names,
}

impl Written<'_> {
    /// Every date in `element` or anything inside it.
    fn subtree_dates(&self, element: &Element) -> Vec<String> {
        let mut inside = HashSet::new();
        ids(element, &mut inside);
        self.measurements
            .iter()
            .filter(|m| m.element_id.as_ref().is_some_and(|e| inside.contains(e)))
            .map(|m| m.id.clone())
            .collect()
    }

    /// One stage per neighbouring pair of the Boundaries at `positions` in
    /// `children`, a Sequence's children with nested Sequences spelled out.
    fn stages(&self, children: &[Element], positions: &[usize]) -> Result<Vec<Stage>, String> {
        let mut stages = Vec::new();
        for pair in positions.windows(2) {
            let (older, younger) = (pair[0], pair[1]);
            let between: Vec<&Element> = children[older + 1..younger]
                .iter()
                .filter(|c| {
                    !self.subtree_dates(c).is_empty() || has_areas(Some(c)) || c.is("sequence")
                })
                .collect();
            let mut dates: Vec<String> = Vec::new();
            let mut areas: Vec<Area> = Vec::new();
            if between.len() > 1 {
                // Phases in a row with no Boundary between them: one stage of
                // dates in blocks, unless areas are among them.
                if between.iter().any(|c| has_areas(Some(c))) {
                    let names: Vec<String> =
                        between.iter().map(|c| format!("'{}'", shown(c))).collect();
                    return Err(format!(
                        "{} sit one after another with no Boundary between them, and areas side \
                         by side are among them. Add a Boundary between them to make the model \
                         exactly solvable",
                        names.join(" and ")
                    ));
                }
                dates = between.iter().flat_map(|c| self.subtree_dates(c)).collect();
            } else if let Some(element) = between.first() {
                if !element.is("phase") {
                    return Err(format!(
                        "'{}' is not a Phase, so it cannot sit between two Boundaries of the outer \
                         Sequence",
                        shown(element)
                    ));
                }
                if has_areas(Some(element)) {
                    self.areas_of(element, &mut dates, &mut areas)?;
                } else {
                    dates = self.subtree_dates(element);
                }
            }
            let blocks = blocks_with_reason(&dates, &self.closed)?;
            if !areas.is_empty() && blocks.len() > 1 {
                return Err(NO_BOUNDARY_BETWEEN.into());
            }
            let blocks = if blocks.is_empty() {
                one_block(&dates)
            } else {
                blocks
            };
            stages.push(Stage {
                older_id: children[older].id.clone(),
                younger_id: children[younger].id.clone(),
                dates,
                areas,
                blocks,
            });
        }
        Ok(stages)
    }

    /// Fill `dates` with the dates lying directly in a stage's Phase and
    /// `areas` with its Sequences as areas, looking through Phases nested in
    /// it, which add no ordering. A reason when that is not possible.
    fn areas_of(
        &self,
        phase: &Element,
        dates: &mut Vec<String>,
        areas: &mut Vec<Area>,
    ) -> Result<(), String> {
        dates.extend(
            self.dates_of
                .get(&Some(phase.id.clone()))
                .into_iter()
                .flatten()
                .cloned(),
        );
        for child in &phase.children {
            match child.element_type.as_str() {
                "sequence" => {
                    let name = child.given_name().unwrap_or("an area");
                    let area = if has_areas(Some(child)) {
                        self.area_holding_areas(child)
                    } else {
                        let inside: HashSet<String> =
                            self.subtree_dates(child).into_iter().collect();
                        let members: Vec<Measurement> = self
                            .measurements
                            .iter()
                            .filter(|m| inside.contains(&m.id))
                            .cloned()
                            .collect();
                        chain_layout(Some(child), &members, false, false).map(Area::line)
                    };
                    // An area inside this one names itself.
                    areas.push(area.map_err(|reason| {
                        if reason.starts_with("in '") {
                            reason
                        } else {
                            format!("in '{name}', {reason}")
                        }
                    })?);
                }
                "phase" => self.areas_of(child, dates, areas)?,
                "hiatus" => {}
                kind => {
                    return Err(format!(
                        "'{}' holds a {} outside any Sequence, so its areas are not each a single \
                         line of phases",
                        phase.given_name().unwrap_or("a Phase"),
                        kind.replace('_', " ")
                    ));
                }
            }
        }
        Ok(())
    }

    /// An area one of whose stages holds areas: laid out from the tree as the
    /// outer Sequence is, a line of its own Boundaries with a stage between
    /// each pair. It has no tails -- a date outside its first and last
    /// Boundary is not outside the model's, and nothing here weighs it.
    fn area_holding_areas(&self, sequence: &Element) -> Result<Area, String> {
        let children: Vec<Element> = flattened(sequence)
            .children
            .into_iter()
            .filter(|c| !c.is("hiatus"))
            .collect();
        let positions: Vec<usize> = (0..children.len())
            .filter(|&i| children[i].is("boundary"))
            .collect();
        if positions.len() < 2 {
            return Err(
                "no Boundary pair encloses the areas side by side inside it. Add a Boundary \
                 before and after them"
                    .into(),
            );
        }
        let (first, last) = (positions[0], positions[positions.len() - 1]);
        let outside_dates: Vec<String> = children[..first]
            .iter()
            .chain(&children[last + 1..])
            .flat_map(|c| self.subtree_dates(c))
            .collect();
        if !outside_dates.is_empty() {
            return Err(outside_every_boundary_reason(
                &outside_dates,
                self.measurements,
            ));
        }
        if let Some(stray) = children[..first]
            .iter()
            .chain(&children[last + 1..])
            .find(|c| has_areas(Some(c)))
        {
            return Err(format!(
                "'{}' holds areas side by side outside its first and last Boundary. Add a \
                 Boundary before and after them",
                shown(stray)
            ));
        }
        let stages = self.stages(&children, &positions)?;
        let boundary_ids: Vec<String> = positions.iter().map(|&i| children[i].id.clone()).collect();
        let names: Names = self
            .names
            .iter()
            .filter(|(id, _)| boundary_ids.contains(id))
            .cloned()
            .collect();
        let mut chain = Chain::new(
            boundary_ids,
            stages.iter().map(|s| s.dates.clone()).collect(),
            names,
        );
        chain.blocks = stages.iter().map(|s| s.blocks.clone()).collect();
        Ok(Area {
            chain,
            inner: stages.into_iter().map(|s| s.areas).collect(),
        })
    }
}

/// `area`'s groups of dates, and those of the areas inside it.
fn lay_groups(area: &Area, laid: &mut HashSet<Group>) {
    for (index, group) in area.chain.groups.iter().enumerate() {
        if !group.is_empty() {
            laid.insert(sorted_group(
                &area.chain.boundary_ids[index],
                &area.chain.boundary_ids[index + 1],
                group,
            ));
        }
    }
    for inner in area.inner.iter().flatten() {
        lay_groups(inner, laid);
    }
}

/// `area`'s ordering between `older` and `younger`, the Boundaries either side
/// of its stage: its ends inside them, its own line, and the areas inside each
/// of its groups between that group's Boundaries.
fn order_area(order: &mut Order, older: &str, younger: &str, area: &Area) {
    let ids = &area.chain.boundary_ids;
    edge(order, older, &ids[0]);
    edge(order, &ids[ids.len() - 1], younger);
    order_chain(order, ids, &area.chain.blocks);
    for (index, areas) in area.inner.iter().enumerate() {
        for inner in areas {
            order_area(order, &ids[index], &ids[index + 1], inner);
        }
    }
}

type Group = (String, String, Vec<String>);

fn sorted_group(older: &str, younger: &str, dates: &[String]) -> Group {
    let mut d = dates.to_vec();
    d.sort();
    d.dedup();
    (older.to_owned(), younger.to_owned(), d)
}

/// Whether `layout` is the model as written: the same groups of dates, each
/// between the same two Boundaries, and the same ordering between every pair
/// of Boundaries and dates, transitively. Otherwise the reason it is not.
fn as_written(
    layout: &Stages,
    plan: &ModelPlan,
    measurements: &[Measurement],
) -> Result<(), String> {
    let written: HashSet<Group> = plan
        .span_groups
        .iter()
        .filter(|g| !g.date_ids.is_empty())
        .map(|g| sorted_group(&g.older_boundary_id, &g.younger_boundary_id, &g.date_ids))
        .collect();
    let mut laid: HashSet<Group> = HashSet::new();
    for stage in &layout.stages {
        if !stage.dates.is_empty() {
            laid.insert(sorted_group(
                &stage.older_id,
                &stage.younger_id,
                &stage.dates,
            ));
        }
        for area in &stage.areas {
            lay_groups(area, &mut laid);
        }
    }
    let mut placed: HashSet<&String> = laid.iter().flat_map(|(_, _, g)| g).collect();
    placed.extend(&layout.older_tail);
    placed.extend(&layout.younger_tail);
    let stray: Vec<String> = measurements
        .iter()
        .filter(|m| !placed.contains(&m.id))
        .map(|m| m.id.clone())
        .collect();
    if !stray.is_empty() {
        return Err(outside_every_boundary_reason(&stray, measurements));
    }
    if written != laid {
        return Err(
            "its groups of dates are not the ones its Boundaries enclose as \
                    side-by-side areas"
                .into(),
        );
    }
    let mut laid_order = Order::new();
    order_chain(
        &mut laid_order,
        &layout.boundary_ids,
        &layout
            .stages
            .iter()
            .map(|s| s.blocks.clone())
            .collect::<Vec<_>>(),
    );
    order_tails(
        &mut laid_order,
        &layout.boundary_ids[0],
        &layout.boundary_ids[layout.boundary_ids.len() - 1],
        &layout.older_tail_blocks,
        &layout.younger_tail_blocks,
    );
    for stage in &layout.stages {
        for area in &stage.areas {
            order_area(&mut laid_order, &stage.older_id, &stage.younger_id, area);
        }
    }
    if closure(&written_order(plan)) != closure(&laid_order) {
        return Err(
            "some of its areas, or dates in them, are ordered against each other, \
                    which side-by-side areas between shared Boundaries cannot express"
                .into(),
        );
    }
    Ok(())
}

// --------------------------------------------------------------------------
// Solving
// --------------------------------------------------------------------------

/// Exact marginal posteriors for every date and Boundary in `stages`, in the
/// shape the chain solver returns, with its joint draws and on its two grids.
/// The first pass needs only the outermost Boundaries, which bound every date
/// and every area.
pub fn solve(
    measurements: &[Measurement],
    root: &Element,
    stages: &Stages,
    options: &SolveOptions,
    cal: &Calibrator,
) -> Result<Solution, Error> {
    let element_bounds = inherited_bounds(root);
    let outer = stages.outer_chain();
    let solver = ParallelSolver {
        measurements,
        root,
        stages,
        outer: &outer,
        element_bounds: &element_bounds,
        options,
        cal,
    };
    two_pass(
        &solver,
        measurements,
        &element_bounds,
        stages.num_groups(),
        options.seed,
        Some(&outer),
        cal,
    )
}

/// `term`, a stage's own log transfer, less the tie between its two
/// Boundaries, which are not neighbours where areas lie between them.
fn untied(term: &mut Matrix) {
    let tie = BOUNDARY_TIE_WEIGHT.ln();
    for i in 0..term.rows() {
        let v = term.get(i, i) - tie;
        term.set(i, i, v);
    }
}

/// A stage's log transfer, [younger, older]: its own term and its areas' Z.
fn with_areas(term: &Matrix, areas: &[AreaSolve]) -> Matrix {
    let mut term = term.clone();
    for area in areas {
        let zt = area.z.transpose();
        term.zip_inplace(&zt, |t, z| t + (z.ln() + area.log_z_scale));
    }
    term
}

/// Solve each of a stage's areas given everything outside it: `pair_weight`,
/// [E, S], the weight of everything outside the stage, and `own`, the stage's
/// own term, with the other areas' Z.
fn solve_areas(pair_weight: Matrix, own: &Matrix, areas: &mut [AreaSolve]) {
    let m = own.rows();
    let mut beyond = pair_weight; // [E, S]
    beyond.map_inplace(f64::ln);
    beyond.zip_inplace(own, |a, b| a + b);
    let rescaled = Matrix::from_rows(m, m, exp_rescaled(beyond.data()));
    let (beyond, _) = scaled(rescaled.transpose()); // [S, E]
    for a in 0..areas.len() {
        let mut rest = beyond.clone();
        for (o, other) in areas.iter().enumerate() {
            if o != a {
                rest = scaled(rest.zip_map(&other.z, |x, y| x * y)).0;
            }
        }
        let (w, _) = scaled(outside(&rest)); // [first, last]
        areas[a].solve_given(&w.transpose());
    }
}

/// One area's parts on one grid: its log transfer matrices, its products and
/// its weight with its ends held, J, and within its stage, Z; the same for the
/// areas inside each of its groups; and, once it is solved given everything
/// outside it, its messages.
struct AreaSolve {
    chain: Chain,
    /// Each group's log transfer, the areas inside it folded in.
    log_t: Vec<Matrix>,
    /// {group index: its own term, without its areas}, for the groups that
    /// hold areas.
    own: BTreeMap<usize, Matrix>,
    ordered: BTreeMap<usize, OrderedGroup>,
    products: Option<ChainProducts>,
    j: Matrix,
    z: Matrix,
    /// log of Z's true scale: the products', every transfer's own rescaling,
    /// and Z's.
    log_z_scale: f64,
    inner: Vec<Vec<AreaSolve>>,
    messages: Option<EndFactorMessages>,
}

impl AreaSolve {
    /// The upward pass: the areas inside it first, whose Z its own transfers
    /// hold.
    fn new(
        area: &Area,
        grid: &[f64],
        allowed: &HashMap<String, Vec<bool>>,
        cumulative: &HashMap<String, Vec<f64>>,
        likelihood: &HashMap<String, Vec<f64>>,
    ) -> Self {
        let chain = &area.chain;
        let inner: Vec<Vec<AreaSolve>> = area
            .inner
            .iter()
            .map(|areas| {
                areas
                    .iter()
                    .map(|a| Self::new(a, grid, allowed, cumulative, likelihood))
                    .collect()
            })
            .collect();
        // Ordered groups inside the area too.
        let (mut log_t, ordered) =
            chain_log_transfers(grid, chain, cumulative, likelihood, allowed);
        let mut own = BTreeMap::new();
        for (index, areas) in inner.iter().enumerate() {
            if !areas.is_empty() {
                untied(&mut log_t[index]);
                let folded = with_areas(&log_t[index], areas);
                own.insert(index, std::mem::replace(&mut log_t[index], folded));
            }
        }
        let products = ChainProducts::new(chain, allowed, &log_t);
        let j = products.end_to_end().clone(); // [first, last]
        let (z, z_factor) = scaled(within(&j)); // [S, E]
        let log_z_scale =
            products.log_scale + z_factor + python_sum(log_t.iter().map(|t| finite_peak(t.data())));
        Self {
            chain: chain.clone(),
            log_t,
            own,
            ordered,
            products: Some(products),
            j,
            z,
            log_z_scale,
            inner,
            messages: None,
        }
    }

    /// The downward pass: this area given `end_factor`, [last, first], the
    /// weight of everything outside it, and then the areas inside each of its
    /// groups given everything outside them -- its own pair weights, which
    /// hold the end factor.
    fn solve_given(&mut self, end_factor: &Matrix) {
        let products = self.products.take().expect("solved once");
        let messages = messages_with_end_factor(products, end_factor);
        for (index, areas) in self.inner.iter_mut().enumerate() {
            if !areas.is_empty() {
                solve_areas(messages.pair_weight(index), &self.own[&index], areas);
            }
        }
        self.messages = Some(messages);
    }

    /// Its dates' and Boundaries' unnormalised marginals, and those of the
    /// areas inside it: each Boundary, then the areas in the group after it.
    fn marginals(
        &mut self,
        cumulative: &HashMap<String, Vec<f64>>,
        likelihood: &HashMap<String, Vec<f64>>,
        dt: f64,
        dates: &mut Vec<(String, Vec<f64>)>,
        boundaries: &mut Vec<(String, Vec<f64>)>,
    ) {
        let messages = self.messages.as_ref().expect("solved");
        let pair = |k: usize| messages.pair_weight(k);
        dates.extend(chain_date_marginals(
            &self.chain,
            &mut self.ordered,
            &self.log_t,
            &pair,
            cumulative,
            likelihood,
            dt,
        ));
        for (i, bid) in self.chain.boundary_ids.iter().enumerate() {
            boundaries.push((bid.clone(), messages.boundary_density(i)));
            for area in self.inner.get_mut(i).into_iter().flatten() {
                area.marginals(cumulative, likelihood, dt, dates, boundaries);
            }
        }
    }

    /// Joint draws of its Boundaries and dates given its stage's (S, E), `s`
    /// and `e`, one per draw, and then of the areas inside each of its groups
    /// given that group's Boundaries as drawn.
    #[allow(clippy::too_many_arguments)]
    fn draw(
        &self,
        rng: &mut Generator,
        s: &[usize],
        e: &[usize],
        cumulative: &HashMap<String, Vec<f64>>,
        likelihood: &HashMap<String, Vec<f64>>,
        dt: f64,
        draws: &mut HashMap<String, Vec<usize>>,
        date_draws: &mut HashMap<String, Vec<usize>>,
    ) -> Result<(), Error> {
        let messages = self.messages.as_ref().expect("solved");
        let (first, last) = draw_area_ends(rng, &self.j, s, e)?;
        let inner = messages.draw_boundaries(&self.log_t, rng, s.len(), Some((first, last)))?;
        for (i, bid) in self.chain.boundary_ids.iter().enumerate() {
            draws.insert(bid.clone(), inner[i].clone());
        }
        date_draws.extend(chain_draw_dates(
            rng,
            &self.chain,
            &self.ordered,
            cumulative,
            likelihood,
            &inner,
            dt,
        )?);
        for (index, areas) in self.inner.iter().enumerate() {
            for area in areas {
                area.draw(
                    rng,
                    &inner[index],
                    &inner[index + 1],
                    cumulative,
                    likelihood,
                    dt,
                    draws,
                    date_draws,
                )?;
            }
        }
        Ok(())
    }
}

struct ParallelSolver<'a> {
    measurements: &'a [Measurement],
    root: &'a Element,
    stages: &'a Stages,
    outer: &'a Chain,
    element_bounds: &'a ElementBounds,
    options: &'a SolveOptions,
    cal: &'a Calibrator<'a>,
}

enum Stage2<'m> {
    Boundaries,
    Evidence {
        prior_only: bool,
        masses: Option<&'m mut HashMap<String, f64>>,
    },
    Full,
}

enum Reached {
    Boundaries(Vec<Vec<f64>>),
    Evidence(f64),
    Full(Box<(Solution, Vec<Vec<f64>>)>),
}

impl ParallelSolver<'_> {
    fn on_grid(
        &self,
        grid: &[f64],
        scale_weights: Option<&ScaleWeights>,
        stage: Stage2<'_>,
    ) -> Result<Reached, Error> {
        let (evidence_only, boundaries_only) = match &stage {
            Stage2::Boundaries => (false, true),
            Stage2::Evidence { .. } => (true, false),
            Stage2::Full => (false, false),
        };
        let (prior_only, mut masses) = match stage {
            Stage2::Evidence { prior_only, masses } => (prior_only, masses),
            _ => (false, None),
        };
        let dt = grid[1] - grid[0];
        let stages = self.stages;
        let outer = self.outer;
        let mut tables = date_tables(
            grid,
            self.measurements,
            self.element_bounds,
            scale_weights,
            if prior_only {
                None
            } else {
                masses.as_deref_mut()
            },
            self.cal,
        )?;
        let all_boundary_ids = stages.all_boundary_ids();
        let allowed = allowed_windows(grid, self.element_bounds, &all_boundary_ids);
        let tail_dates: HashSet<&String> =
            outer.older_tail.iter().chain(&outer.younger_tail).collect();
        let all_dates: Vec<String> = self
            .measurements
            .iter()
            .filter(|m| !tail_dates.contains(&m.id))
            .map(|m| m.id.clone())
            .collect();
        let inliers: HashMap<String, Vec<f64>> = all_dates
            .iter()
            .map(|d| {
                let v = match tables.outlier_parts.get(d) {
                    Some(part) => part.0.clone(),
                    None => tables.likelihood[d].clone(),
                };
                (d.clone(), v)
            })
            .collect();
        if prior_only {
            let (likelihood, cumulative) =
                flat_date_tables(grid, self.measurements, self.element_bounds, masses)?;
            tables.likelihood = likelihood;
            tables.cumulative = cumulative;
        }
        let (cumulative, likelihood) = (&tables.cumulative, &tables.likelihood);

        let mut areas: Vec<Vec<AreaSolve>> = stages
            .stages
            .iter()
            .map(|s| {
                s.areas
                    .iter()
                    .map(|area| AreaSolve::new(area, grid, &allowed, cumulative, likelihood))
                    .collect()
            })
            .collect();

        // The model's tails weigh on its first and last outer Boundaries, as
        // on a chain's, and are not enclosed by them, so they are not in the
        // limits factor.
        let mut tails = Tails::new(outer, grid, cumulative, likelihood);
        let first = &outer.boundary_ids[0];
        let last = &outer.boundary_ids[outer.boundary_ids.len() - 1];
        let limits = pair_limits(self.element_bounds, first, last, grid, &inliers, &all_dates);
        let log_correction = span_prior_correction(
            grid,
            all_boundary_ids.len(),
            self.options.uniform_span_prior,
            Some(limits),
        );

        // Each stage's own term, [younger, older]: its dates' factors, as for
        // any group, and for a stage holding areas no tie between its two
        // Boundaries, which are not neighbours there. A stage of Phases in a
        // row is an ordered group, as on a chain; the layout keeps areas out of
        // one.
        let (mut own, mut ordered) =
            chain_log_transfers(grid, outer, cumulative, likelihood, &allowed);
        for (index, stage) in stages.stages.iter().enumerate() {
            if !stage.areas.is_empty() {
                untied(&mut own[index]);
            }
        }
        let span_prior_applied = log_correction.is_some();
        let mut correction = None;
        let mut correction_offset = 0.0;
        match log_correction {
            Some(c) if stages.stages.len() == 1 => {
                // One stage: the correction's Boundaries are its own, so it
                // folds into its term, as on a chain of one group -- and so
                // reaches the areas through that term below.
                own[0].zip_inplace(&c, |a, b| {
                    a + if b.is_finite() { b } else { f64::NEG_INFINITY }
                });
            }
            Some(c) => {
                correction_offset = finite_peak(c.data());
                correction = Some(Matrix::from_rows(
                    c.rows(),
                    c.cols(),
                    exp_rescaled(c.data()),
                ));
            }
            None => {}
        }

        let log_t: Vec<Matrix> = own
            .iter()
            .enumerate()
            .map(|(index, term)| with_areas(term, &areas[index]))
            .collect();

        let msgs: Messages = match correction {
            Some(c) => messages_with_span_prior(outer, &allowed, &log_t, c, &tails.end_weights),
            None => messages(outer, &allowed, &log_t, &tails.end_weights),
        };
        let log_evidence = msgs.log_mass()
            + correction_offset
            + tails.log_scale
            + python_sum(log_t.iter().map(|t| finite_peak(t.data())));
        if evidence_only {
            return Ok(Reached::Evidence(log_evidence));
        }
        let n = outer.boundary_ids.len();
        if boundaries_only {
            return Ok(Reached::Boundaries(
                [msgs.boundary_density(0), msgs.boundary_density(n - 1)]
                    .into_iter()
                    .filter(|d| sum(d) > 0.0)
                    .collect(),
            ));
        }

        // Every outer stage's own dates, read off the outer chain as on any
        // chain: a stage's log_T already holds its areas' Z, so dividing a
        // date's own factor out of it works unchanged.
        let pair_weight = |k: usize| msgs.pair_weight(k);
        let stage_dates: HashMap<String, Vec<f64>> = chain_date_marginals(
            outer,
            &mut ordered,
            &log_t,
            &pair_weight,
            cumulative,
            likelihood,
            dt,
        )
        .into_iter()
        .collect();
        let outer_densities: Vec<Vec<f64>> = (0..n).map(|i| msgs.boundary_density(i)).collect();

        // Each area given everything outside it, and the areas inside it
        // given that.
        for (index, stage_areas) in areas.iter_mut().enumerate() {
            if !stage_areas.is_empty() {
                solve_areas(msgs.pair_weight(index), &own[index], stage_areas);
            }
        }

        let mut date_densities: Vec<(String, Vec<f64>)> = Vec::new();
        let mut boundary_densities: Vec<(String, Vec<f64>)> = Vec::new();
        for (index, stage) in stages.stages.iter().enumerate() {
            boundary_densities.push((stage.older_id.clone(), outer_densities[index].clone()));
            for date_id in &stage.dates {
                date_densities.push((date_id.clone(), stage_dates[date_id].clone()));
            }
            for area in &mut areas[index] {
                area.marginals(
                    cumulative,
                    likelihood,
                    dt,
                    &mut date_densities,
                    &mut boundary_densities,
                );
            }
        }
        boundary_densities.push((last.clone(), outer_densities[n - 1].clone()));
        date_densities.extend(tails.marginals(
            &outer_densities[0],
            &outer_densities[n - 1],
            cumulative,
            likelihood,
        ));

        let order: Vec<String> = boundary_densities.iter().map(|(b, _)| b.clone()).collect();
        let draw_joint = |rng: &mut Generator, count: usize| -> Result<JointIndices, Error> {
            let outer_draws = msgs.draw_boundaries(&log_t, rng, count)?;
            let mut draws: HashMap<String, Vec<usize>> = outer
                .boundary_ids
                .iter()
                .cloned()
                .zip(outer_draws.iter().cloned())
                .collect();
            let mut date_draws = chain_draw_dates(
                rng,
                outer,
                &ordered,
                cumulative,
                likelihood,
                &outer_draws,
                dt,
            )?;
            date_draws.extend(tails.draw(
                rng,
                &outer_draws[0],
                &outer_draws[n - 1],
                cumulative,
                likelihood,
                dt,
            )?);
            for (index, _stage) in stages.stages.iter().enumerate() {
                let (s, e) = (&outer_draws[index], &outer_draws[index + 1]);
                for area in &areas[index] {
                    area.draw(
                        rng,
                        s,
                        e,
                        cumulative,
                        likelihood,
                        dt,
                        &mut draws,
                        &mut date_draws,
                    )?;
                }
            }
            Ok((
                order
                    .iter()
                    .map(|b| (b.clone(), draws[b].clone()))
                    .collect(),
                date_draws,
            ))
        };
        let solved = results(
            Marginals {
                grid,
                root: self.root,
                measurements: self.measurements,
                date_densities,
                boundary_densities,
                boundary_names: &stages.boundary_names,
                tables: &tables,
                log_evidence,
                span_prior_applied,
            },
            self.options,
            &draw_joint,
        )?;
        Ok(Reached::Full(Box::new(solved)))
    }
}

impl GridSolve for ParallelSolver<'_> {
    fn boundaries(&self, grid: &[f64]) -> Result<Vec<Vec<f64>>, Error> {
        match self.on_grid(grid, None, Stage2::Boundaries)? {
            Reached::Boundaries(d) => Ok(d),
            _ => unreachable!("asked for the Boundaries"),
        }
    }

    fn evidence(
        &self,
        grid: &[f64],
        scale_weights: Option<&ScaleWeights>,
        prior_only: bool,
        masses: Option<&mut HashMap<String, f64>>,
    ) -> Result<f64, Error> {
        match self.on_grid(grid, scale_weights, Stage2::Evidence { prior_only, masses })? {
            Reached::Evidence(e) => Ok(e),
            _ => unreachable!("asked for the evidence"),
        }
    }

    fn full(
        &self,
        grid: &[f64],
        scale_weights: Option<&ScaleWeights>,
    ) -> Result<(Solution, Vec<Vec<f64>>), Error> {
        match self.on_grid(grid, scale_weights, Stage2::Full)? {
            Reached::Full(solved) => Ok(*solved),
            _ => unreachable!("asked for the whole solve"),
        }
    }
}

/// An area's (first, last) Boundaries, one per draw, given its stage's (S, E):
/// from h(S, first) h(last, E) J[first, last] over first <= S and last >= E, a
/// tie at either end at half weight, as [`within`] sums it.
///
/// In two steps, so each is a draw from one row: the first from the area's
/// weight summed over every last >= E, and then the last given the first.
fn draw_area_ends(
    rng: &mut Generator,
    j: &Matrix,
    s: &[usize],
    e: &[usize],
) -> Result<(Vec<usize>, Vec<usize>), Error> {
    let m = j.rows();
    // R[f, E] = sum over l >= E of J[f, l], the tie at l == E a half.
    let by_first = reversed_columns(&tied_cumsum(&reversed_columns(j), 1)).transpose(); // [E, f]
    let tie = |c: usize, at: usize, older: bool| -> f64 {
        if c == at {
            BOUNDARY_TIE_WEIGHT
        } else if (c < at) == older {
            1.0
        } else {
            0.0
        }
    };
    let keys: Vec<usize> = s.iter().zip(e).map(|(s, e)| s * m + e).collect();
    let first = crate::exact_chain::messages::draw_from_rows(
        rng,
        |key| {
            let (s, e) = (key / m, key % m);
            by_first
                .row(e)
                .iter()
                .enumerate()
                .map(|(c, v)| v * tie(c, s, true))
                .collect()
        },
        &keys,
    )?;
    let keys: Vec<usize> = first.iter().zip(e).map(|(f, e)| f * m + e).collect();
    let last = crate::exact_chain::messages::draw_from_rows(
        rng,
        |key| {
            let (f, e) = (key / m, key % m);
            j.row(f)
                .iter()
                .enumerate()
                .map(|(c, v)| v * tie(c, e, false))
                .collect()
        },
        &keys,
    )?;
    Ok((first, last))
}
