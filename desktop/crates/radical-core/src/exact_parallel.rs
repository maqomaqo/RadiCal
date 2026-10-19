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
//! # What it does NOT cover
//!
//! [`layout_with_reason`] declines, with a reason, a model whose areas are side
//! by side but which is not this shape: areas with no Boundary before and after
//! them that they share (separate models, in effect), an area that is not
//! itself a single line, areas inside an area, or anything ordering one area
//! against another. Those are sampled. Dates outside every outer Boundary are
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
    pub areas: Vec<Chain>,
    pub blocks: Blocks,
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
    /// the Boundaries of the areas in the stage after it.
    pub fn all_boundary_ids(&self) -> Vec<String> {
        let mut out = Vec::new();
        for (index, outer) in self.boundary_ids.iter().enumerate() {
            out.push(outer.clone());
            if let Some(stage) = self.stages.get(index) {
                for area in &stage.areas {
                    out.extend(area.boundary_ids.iter().cloned());
                }
            }
        }
        out
    }

    /// Groups of dates in the model, for the grid's point budget: every stage,
    /// and every group inside every area.
    pub fn num_groups(&self) -> usize {
        self.stages.len()
            + self
                .stages
                .iter()
                .flat_map(|s| &s.areas)
                .map(|a| a.groups.len())
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
    let subtree_dates = |element: &Element| -> Vec<String> {
        let mut inside = HashSet::new();
        ids(element, &mut inside);
        measurements
            .iter()
            .filter(|m| m.element_id.as_ref().is_some_and(|e| inside.contains(e)))
            .map(|m| m.id.clone())
            .collect()
    };

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

    let closed = closure(&written_order(&plan));
    let mut stages = Vec::new();
    for pair in positions.windows(2) {
        let (older, younger) = (pair[0], pair[1]);
        let between: Vec<&Element> = children[older + 1..younger]
            .iter()
            .filter(|c| !subtree_dates(c).is_empty() || has_areas(Some(c)) || c.is("sequence"))
            .collect();
        let mut dates: Vec<String> = Vec::new();
        let mut areas: Vec<Chain> = Vec::new();
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
            dates = between.iter().flat_map(|c| subtree_dates(c)).collect();
        } else if let Some(element) = between.first() {
            if !element.is("phase") {
                return Err(format!(
                    "'{}' is not a Phase, so it cannot sit between two Boundaries of the outer \
                     Sequence",
                    shown(element)
                ));
            }
            if has_areas(Some(element)) {
                areas_of(
                    element,
                    measurements,
                    &dates_of,
                    &subtree_dates,
                    &mut dates,
                    &mut areas,
                )?;
            } else {
                dates = subtree_dates(element);
            }
        }
        let blocks = blocks_with_reason(&dates, &closed)?;
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

/// Fill `dates` with the dates lying directly in a stage's Phase and `areas`
/// with its Sequences as Chains, looking through Phases nested in it, which
/// add no ordering. A reason when that is not possible.
fn areas_of(
    phase: &Element,
    measurements: &[Measurement],
    dates_of: &HashMap<Option<String>, Vec<String>>,
    subtree_dates: &dyn Fn(&Element) -> Vec<String>,
    dates: &mut Vec<String>,
    areas: &mut Vec<Chain>,
) -> Result<(), String> {
    dates.extend(
        dates_of
            .get(&Some(phase.id.clone()))
            .into_iter()
            .flatten()
            .cloned(),
    );
    for child in &phase.children {
        match child.element_type.as_str() {
            "sequence" => {
                let name = child.given_name().unwrap_or("an area");
                let inside: HashSet<String> = subtree_dates(child).into_iter().collect();
                let members: Vec<Measurement> = measurements
                    .iter()
                    .filter(|m| inside.contains(&m.id))
                    .cloned()
                    .collect();
                let chain = chain_layout(Some(child), &members, false, false)
                    .map_err(|reason| format!("in '{name}', {reason}"))?;
                areas.push(chain);
            }
            "phase" => areas_of(child, measurements, dates_of, subtree_dates, dates, areas)?,
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
            for (index, group) in area.groups.iter().enumerate() {
                if !group.is_empty() {
                    laid.insert(sorted_group(
                        &area.boundary_ids[index],
                        &area.boundary_ids[index + 1],
                        group,
                    ));
                }
            }
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
            edge(&mut laid_order, &stage.older_id, &area.boundary_ids[0]);
            edge(
                &mut laid_order,
                &area.boundary_ids[area.boundary_ids.len() - 1],
                &stage.younger_id,
            );
            order_chain(&mut laid_order, &area.boundary_ids, &area.blocks);
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

/// One area's parts on one grid: its log transfer matrices, its products and
/// its weight with its ends held, J, and within its stage, Z.
struct Area {
    chain: Chain,
    log_t: Vec<Matrix>,
    ordered: BTreeMap<usize, OrderedGroup>,
    products: Option<ChainProducts>,
    j: Matrix,
    z: Matrix,
    /// log of Z's true scale: the products', every transfer's own rescaling,
    /// and Z's.
    log_z_scale: f64,
}

impl Area {
    fn new(
        chain: &Chain,
        grid: &[f64],
        allowed: &HashMap<String, Vec<bool>>,
        cumulative: &HashMap<String, Vec<f64>>,
        likelihood: &HashMap<String, Vec<f64>>,
    ) -> Self {
        // Ordered groups inside the area too.
        let (log_t, ordered) = chain_log_transfers(grid, chain, cumulative, likelihood, allowed);
        let products = ChainProducts::new(chain, allowed, &log_t);
        let j = products.end_to_end().clone(); // [first, last]
        let (z, z_factor) = scaled(within(&j)); // [S, E]
        let log_z_scale =
            products.log_scale + z_factor + python_sum(log_t.iter().map(|t| finite_peak(t.data())));
        Self {
            chain: chain.clone(),
            log_t,
            ordered,
            products: Some(products),
            j,
            z,
            log_z_scale,
        }
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
        let m = grid.len();
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

        let mut areas: Vec<Vec<Area>> = stages
            .stages
            .iter()
            .map(|s| {
                s.areas
                    .iter()
                    .map(|chain| Area::new(chain, grid, &allowed, cumulative, likelihood))
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
        let tie = BOUNDARY_TIE_WEIGHT.ln();
        for (index, stage) in stages.stages.iter().enumerate() {
            if !stage.areas.is_empty() {
                for i in 0..m {
                    let v = own[index].get(i, i) - tie;
                    own[index].set(i, i, v);
                }
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
            .map(|(index, term)| {
                let mut term = term.clone();
                for area in &areas[index] {
                    let zt = area.z.transpose();
                    term.zip_inplace(&zt, |t, z| t + (z.ln() + area.log_z_scale));
                }
                term
            })
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

        // Each area given everything outside it.
        let mut area_messages: Vec<Vec<EndFactorMessages>> = Vec::new();
        for (index, stage_areas) in areas.iter_mut().enumerate() {
            let mut found = Vec::new();
            if !stage_areas.is_empty() {
                let mut beyond = msgs.pair_weight(index); // [E, S]
                beyond.map_inplace(f64::ln);
                beyond.zip_inplace(&own[index], |a, b| a + b);
                let rescaled = Matrix::from_rows(m, m, exp_rescaled(beyond.data()));
                let (beyond, _) = scaled(rescaled.transpose()); // [S, E]
                for a in 0..stage_areas.len() {
                    let mut rest = beyond.clone();
                    for (o, other) in stage_areas.iter().enumerate() {
                        if o != a {
                            rest = scaled(rest.zip_map(&other.z, |x, y| x * y)).0;
                        }
                    }
                    let (w, _) = scaled(outside(&rest)); // [first, last]
                    let products = stage_areas[a].products.take().expect("solved once");
                    found.push(messages_with_end_factor(products, &w.transpose()));
                }
            }
            area_messages.push(found);
        }

        let mut date_densities: Vec<(String, Vec<f64>)> = Vec::new();
        let mut boundary_densities: Vec<(String, Vec<f64>)> = Vec::new();
        for (index, stage) in stages.stages.iter().enumerate() {
            boundary_densities.push((stage.older_id.clone(), outer_densities[index].clone()));
            for date_id in &stage.dates {
                date_densities.push((date_id.clone(), stage_dates[date_id].clone()));
            }
            for (area, area_msgs) in areas[index].iter_mut().zip(&area_messages[index]) {
                let area_pair = |k: usize| area_msgs.pair_weight(k);
                date_densities.extend(chain_date_marginals(
                    &area.chain,
                    &mut area.ordered,
                    &area.log_t,
                    &area_pair,
                    cumulative,
                    likelihood,
                    dt,
                ));
                for (i, bid) in area.chain.boundary_ids.iter().enumerate() {
                    boundary_densities.push((bid.clone(), area_msgs.boundary_density(i)));
                }
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
                for (area, area_msgs) in areas[index].iter().zip(&area_messages[index]) {
                    let (first, last) = draw_area_ends(rng, &area.j, s, e)?;
                    let inner =
                        area_msgs.draw_boundaries(&area.log_t, rng, count, Some((first, last)))?;
                    for (i, bid) in area.chain.boundary_ids.iter().enumerate() {
                        draws.insert(bid.clone(), inner[i].clone());
                    }
                    date_draws.extend(chain_draw_dates(
                        rng,
                        &area.chain,
                        &area.ordered,
                        cumulative,
                        likelihood,
                        &inner,
                        dt,
                    )?);
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
