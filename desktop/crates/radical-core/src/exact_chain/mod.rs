//! Exact solver for chain-shaped chronological models -- a Sequence of Phases
//! separated by Boundaries, which is the ordinary shape of an archaeological
//! model and the one OxCal's documentation recommends. Port of
//! `services/exact_chain.py`.
//!
//! # Why integrate rather than sample
//!
//! For this shape the posterior can be integrated exactly, so there is no
//! sampler to converge and no mode to miss. That matters because calibration
//! curves wiggle: a single radiocarbon age routinely calibrates to two or more
//! separated calendar ranges, and resolving which one is right is much of the
//! reason to build a Bayesian model at all.
//!
//! # The maths
//!
//! Conditional on the boundaries enclosing it, a group's dated events are
//! independent and uniform between them, so for boundaries b_1 > b_2 > ... >
//! b_n (cal BP, oldest first) with group i holding K_i dates between b_i and
//! b_(i+1):
//!
//! ```text
//! p(t, b)  ∝  ∏_i (b_i - b_(i+1))^(-K_i) ∏_(dates) L(t)
//! ```
//!
//! Every date other than the one being marginalised enters only through its
//! integrated likelihood over an interval, which a cumulative sum gives for all
//! intervals at once. What is left is a chain over the boundaries, so the exact
//! marginals come from forward/backward message passing:
//!
//! ```text
//! T_i(b_i, b_(i+1)) = (b_i - b_(i+1))^(-K_i) ∏_(j in group i) F_j(b_(i+1), b_i)
//! alpha_(i+1)(b)    = sum over b_i > b of alpha_i(b_i) T_i(b_i, b)
//! beta_i(b)         = sum over b_(i+1) < b of T_i(b, b_(i+1)) beta_(i+1)(b_(i+1))
//! ```
//!
//! That is O(M^2) per group on an M-point calendar grid: seconds,
//! deterministic, and exact up to the grid step.
//!
//! # What it does NOT cover
//!
//! The OUTLIER SCALE is approximated, though no longer ignored. OxCal's
//! Outlier_Model carries one offset scale shared by every date using it and
//! constrained by the data. A shared hyperparameter couples the dates, and the
//! message passing above needs each to enter through its own likelihood alone;
//! the exact answer, one whole solve per scale mixed by evidence, costs 24
//! solves per outlier model. Instead the scale is WEIGHED from the data with
//! cheap evidence passes on a coarse grid ([`solve::weigh_scales`]), and
//! each flagged date's offset is then mixed over that posterior. What is
//! dropped is only the link a common scale makes between the dates; the
//! sampler samples that too.
//!
//! What it costs, and what it buys: 3-17% more run time on the test models. On
//! the outlier fixture the outlier probabilities land 0.0016 from OxCal's
//! (0.0158 with the scale mixed over its prior; 0.0012 for the exact mixture;
//! the sampler 0.0016), and on the charcoal fixture the modelled dates 3.5
//! years rms from OxCal (7.1 over the prior; the sampler 4.7), its span 29.4
//! years against OxCal's 29.2.
//!
//! PHASE WIDTH depends on the interval quadrature being right to second order
//! -- see [`ordered::interval_integral`], which carries the measurements. The
//! sampler carries the same rule, and the two agree to about 0.6 years on the
//! chain fixtures.
//!
//! [`layout`] returns None for anything that is not a single chain. The caller
//! then tries [`crate::exact_parallel`], which integrates a chain whose stages
//! hold areas side by side with this module's own pieces, and falls back to
//! the sampler only when that declines too.
//!
//! A Sequence that orders two groups with NO Boundary between them is a chain
//! too, of one group whose dates are ordered inside it as BLOCKS, one after
//! another: the shape OxCal runs without comment. The order does not factorise
//! along the Boundaries, but it does along cuts at each block's oldest date, so
//! the group is a small chain of its own ([`ordered::OrderedGroup`]). Solved as
//! written it lands 0.56 years rms from OxCal on the `ordered` reference run,
//! where supplying the missing Boundary instead -- a different model -- gave
//! 2.33. Adding the Boundary is still better practice. Dates ordered inside one
//! pair of Boundaries in any other way -- two Sequences side by side with no
//! Boundaries of their own -- are not blocks, and are sampled.
//!
//! A Sequence of Sequences, each with its own Boundaries, IS a chain: it is the
//! same model as its children written out in place, with an empty group between
//! one inner Sequence's last Boundary and the next one's first. See
//! `stitched`.
//!
//! So is a chain with dates outside every Boundary at either end -- one group
//! older than the first Boundary, one younger than the last, each with nothing
//! ordering its dates against each other. Such a date has a flat prior and is
//! held only by its end Boundary, so given that Boundary it is independent of
//! everything else, and the whole group enters as one weight on it:
//!
//! ```text
//! g(b_1) = ∏_(dates beyond b_1) G_j(b_1),   G_j = L_j integrated beyond b_1
//! ```
//!
//! See [`tails::Tails`]. Several groups in a row out there are blocks, as inside
//! a group. A date ordered against neither end, and a date out there with an
//! outlier model, are not taken ([`tails_with_reason`]).
//!
//! # Agreement with NumPy
//!
//! Everything elementwise, every cumulative sum and every pairwise reduction
//! follows NumPy's order, and the seeded draws use the same generator with
//! the same uniforms in the same order. Two things cannot follow it: matrix
//! products (BLAS's order is its own; see [`crate::matrix`]) and SciPy's
//! `fftconvolve`, which is replaced by the direct sum it approximates
//! ([`tables::convolve_on_grid`]). So the marginals agree with Python's to
//! rounding, and a draw lands on another grid cell only when its uniform falls
//! within rounding of a cell edge.

pub mod messages;
pub mod ordered;
pub mod prior;
pub mod solve;
pub mod summary;
pub mod tables;
pub mod tails;

use std::collections::{HashMap, HashSet};

use crate::measurement::Measurement;
use crate::model_tree::{Element, ModelPlan, SpanGroup, TermKind, plan_model};

pub use solve::{Evidence, OutlierScale, Solution, SolveOptions, solve};
pub use summary::Posterior;
pub use tables::{
    DEFAULT_OUTLIER_MODEL, OUTLIER_MODELS, convolve_on_grid, event_likelihood, inbuilt_kernel,
    inbuilt_reach, outlier_kernel, outlier_kernel_at_scale, scale_nodes,
};

/// Largest calendar grid the solver will build. Every step of the algorithm is
/// O(M^2) in space as well as time, so this caps one intermediate array at
/// about 30 MB and the whole solve at a few seconds.
pub const MAX_GRID_POINTS: f64 = 2000.0;
/// How many groups fit at that point count before the grid coarsens. One M x M
/// array of doubles is 32 MB at M = 2000 and one is held per group, so a long
/// sequence trades calendar resolution for staying inside the memory budget.
pub const GROUPS_AT_FULL_RESOLUTION: f64 = 6.0;
/// Finest calendar step the first pass uses. IntCal20/SHCal20 are themselves
/// tabulated at 1-20 year intervals.
pub const MIN_GRID_STEP_YEARS: f64 = 1.0;
/// The second pass may go finer, because the DISCRETISATION, not the curve,
/// sets how fine is worth having: on the shared-boundary reference model the
/// separation of the two ordered groups moved 15.2 -> 14.2 -> 13.8 -> 13.6
/// years at steps of 2.5, 1.0, 0.45 and 0.23, converging near the 13.2 OxCal
/// gives, and still 1 year off at 1.0.
pub const REFINED_MIN_STEP_YEARS: f64 = 0.25;
/// Where the second pass's grid must reach: every posterior out to this much
/// mass in either tail, every date's own likelihood support, and a margin of
/// this fraction of that width, or this many first-pass steps if more.
pub const REFINE_TAIL_MASS: f64 = 1e-6;
pub const REFINE_MARGIN_FRACTION: f64 = 0.05;
pub const REFINE_MARGIN_STEPS: f64 = 3.0;
/// A second pass is only run when it is at least this much finer, and only
/// kept when no distribution has more than REFINED_EDGE_MASS in the outermost
/// 1% of its grid (a margin that cut a tail off would show there).
pub const REFINE_MIN_GAIN: f64 = 1.5;
pub const REFINED_EDGE_MASS: f64 = 1e-4;
/// How far beyond the dates' own likelihood support the boundaries are allowed
/// to range, as a multiple of that support's width. Boundaries genuinely
/// extend past the outermost date -- that is what they are for -- and the tail
/// is heavy for a group of few dates, so this is deliberately generous.
pub const BOUNDARY_WINDOW_FACTOR: f64 = 6.0;
pub const MIN_BOUNDARY_PAD_YEARS: f64 = 800.0;
/// Likelihood below this fraction of a date's peak is treated as absent when
/// working out where the grid needs to reach. OxCal instead cuts at a fixed
/// |z| > 5.26 (exp(-z^2/2) < 1e-6) in F14C, relative to the measurement rather
/// than each date's own peak; the two differ by about 0.1 years on a span.
pub const SUPPORT_THRESHOLD: f64 = 1e-6;
/// Resolution the reported HPD ranges are computed at, independent of the
/// solver's internal grid step -- see [`summary::reported_ranges`]. Five years
/// is OxCal's own default and about as fine as a calibrated date is
/// meaningful.
///
/// The DEFAULT: an analysis can ask for another (OxCal's Options Resolution)
/// -- finer for a recent model whose ranges are a few decades wide, coarser
/// for a Pleistocene one. Only the reporting moves; the internal grid stays
/// aligned to this default whatever is asked, so a model's posteriors,
/// agreement indices and draws do not depend on it.
pub const REPORTING_STEP_YEARS: f64 = 5.0;
/// Density below this fraction of the peak is left off the plotted series,
/// and this much padding is kept either side of what remains -- see
/// [`summary::plottable`].
pub const DISPLAY_THRESHOLD: f64 = 1e-4;
pub const PLOT_MARGIN_YEARS: f64 = 50.0;
/// The weight of two neighbouring Boundaries in one grid cell: a half, the
/// trapezoid's end, as for a date on a Boundary. See
/// [`prior::boundary_pair_term`].
pub const BOUNDARY_TIE_WEIGHT: f64 = 0.5;
/// A limits-factor g this close to zero, in years, IS zero: rounding of two
/// equal differences of grid ages, not a gap. See
/// [`prior::log_limits_factor_of_span`].
pub const LIMITS_ROUNDING_YEARS: f64 = 1e-6;

/// The reason given for groups ordered one after another with no Boundary
/// between them where a layout cannot take them as blocks: beside areas side
/// by side, in one stage of [`crate::exact_parallel`].
pub const NO_BOUNDARY_BETWEEN: &str = "two groups are ordered against each other with no \
     Boundary between them. Add a Boundary at that transition to make the model exactly \
     solvable";

/// The reason given for dates inside one pair of Boundaries, or outside every
/// Boundary at one end, ordered against each other other than as groups one
/// after another ([`blocks_with_reason`]).
pub const NOT_ONE_AFTER_ANOTHER: &str = "some dates inside one pair of Boundaries are ordered \
     against each other other than as one group after another -- two Sequences side by side \
     with no Boundaries of their own, say. Give each its own Boundaries to make the model \
     exactly solvable";

const BRANCHED: &str = "the model branches: it has parallel groups rather than one line of \
     phases, so the phases do not share a single chain of Boundaries";

/// Boundary names by id, in model order.
pub type Names = Vec<(String, String)>;

pub(crate) fn name_of<'a>(names: &'a Names, id: &str) -> Option<&'a str> {
    names.iter().find(|(b, _)| b == id).map(|(_, n)| n.as_str())
}

/// A model that is a single line of bounded groups, oldest first, and the
/// dates outside every Boundary at either end: `older_tail` older than the
/// first Boundary, `younger_tail` younger than the last.
///
/// `blocks`, one list per group, holds the group's dates as BLOCKS, oldest
/// first, where they are ordered against each other inside the group --
/// Phases in a row with no Boundary between them ([`blocks_with_reason`]). A
/// group with no such order is one block, the default; so is an empty one, as
/// no blocks. `older_tail_blocks` and `younger_tail_blocks` do the same for
/// the tails.
#[derive(Debug, Clone, PartialEq)]
pub struct Chain {
    /// One more than there are groups.
    pub boundary_ids: Vec<String>,
    pub groups: Vec<Vec<String>>,
    pub boundary_names: Names,
    pub older_tail: Vec<String>,
    pub younger_tail: Vec<String>,
    pub blocks: Vec<Vec<Vec<String>>>,
    pub older_tail_blocks: Vec<Vec<String>>,
    pub younger_tail_blocks: Vec<Vec<String>>,
}

pub(crate) fn one_block(date_ids: &[String]) -> Vec<Vec<String>> {
    if date_ids.is_empty() {
        Vec::new()
    } else {
        vec![date_ids.to_vec()]
    }
}

impl Chain {
    /// A chain with no tails and every group one block.
    pub fn new(boundary_ids: Vec<String>, groups: Vec<Vec<String>>, boundary_names: Names) -> Self {
        Self::with_tails(boundary_ids, groups, boundary_names, Vec::new(), Vec::new())
    }

    /// A chain with tails, every group and each tail one block.
    pub fn with_tails(
        boundary_ids: Vec<String>,
        groups: Vec<Vec<String>>,
        boundary_names: Names,
        older_tail: Vec<String>,
        younger_tail: Vec<String>,
    ) -> Self {
        let blocks = groups.iter().map(|g| one_block(g)).collect();
        let older_tail_blocks = one_block(&older_tail);
        let younger_tail_blocks = one_block(&younger_tail);
        Self {
            boundary_ids,
            groups,
            boundary_names,
            older_tail,
            younger_tail,
            blocks,
            older_tail_blocks,
            younger_tail_blocks,
        }
    }

    /// Whether group `index` has dates ordered inside it.
    pub fn ordered(&self, index: usize) -> bool {
        self.blocks[index].len() > 1
    }

    /// A Boundary's name, 'Boundary' when it has none.
    pub fn boundary_name(&self, id: &str) -> &str {
        name_of(&self.boundary_names, id).unwrap_or("Boundary")
    }
}

/// The chain this model reduces to, or None if it does not reduce to one.
/// See [`layout_with_reason`].
pub fn layout(
    root: Option<&Element>,
    measurements: &[Measurement],
    allow_inner_order: bool,
    allow_tails: bool,
) -> Option<Chain> {
    layout_with_reason(root, measurements, allow_inner_order, allow_tails).ok()
}

/// The chain this model reduces to, or a plain-English reason it does not, so
/// the API can say WHY it fell back to sampling.
///
/// Semantic rather than syntactic: it works from the plan, so nesting that
/// adds no ordering of its own -- a Sequence wrapping a single Phase, say --
/// is still recognised as a chain.
///
/// `allow_inner_order` is for the cross-check, which integrates groups whose
/// dates are ordered against each other inside one pair of Boundaries. It
/// returns the chain of Boundaries and groups without insisting that the chain
/// implies every ordering the tree asks for; the caller must check the extra
/// ordering is one it can integrate. This solver never asks.
///
/// `allow_tails` lets dates outside every Boundary at the model's two ends in,
/// as `older_tail` and `younger_tail`; see [`tails_with_reason`]. Off for a
/// caller laying out PART of a model -- an area of a site
/// ([`crate::exact_parallel`]), or the cross-check's branches -- which has
/// dates outside that part's Boundaries that are not outside the model's.
///
/// Produced here at the point of decision rather than by a separate
/// diagnostic that inspects the tree afterwards, which would drift from these
/// rules and then confidently explain a fallback that happened for another
/// reason. Each reason says what the model does and, where practical, what to
/// change: these strings are shown to an archaeologist, not a developer.
pub fn layout_with_reason(
    root: Option<&Element>,
    measurements: &[Measurement],
    allow_inner_order: bool,
    allow_tails: bool,
) -> Result<Chain, String> {
    let Some(root) = root else {
        return Err("the analysis has no model structure".into());
    };
    let plan = plan_model(root, measurements);
    if let Some(coupled) = coupled_dates_reason(&plan) {
        return Err(coupled.into());
    }

    if plan.span_groups.is_empty() {
        let what = match plan.boundary_names.len() {
            0 => "a Boundary at the start and end of the Sequence",
            1 => "another Boundary, so that two of them enclose the dated Phases",
            _ => "Boundaries either side of the dated Phases",
        };
        return Err(format!(
            "no pair of Boundaries encloses any date. Add {what} to make the model exactly \
             solvable"
        ));
    }

    // Link the groups into paths: each group's younger boundary must be the
    // next group's older boundary, and no boundary may start or end two groups.
    let mut by_older: HashMap<&str, usize> = HashMap::new();
    let mut by_younger: HashSet<&str> = HashSet::new();
    for (index, group) in plan.span_groups.iter().enumerate() {
        if by_older.contains_key(group.older_boundary_id.as_str())
            || by_younger.contains(group.younger_boundary_id.as_str())
        {
            return Err(BRANCHED.into()); // a boundary starting/ending two groups
        }
        by_older.insert(&group.older_boundary_id, index);
        by_younger.insert(&group.younger_boundary_id);
    }
    let heads: Vec<usize> = (0..plan.span_groups.len())
        .filter(|&i| !by_younger.contains(plan.span_groups[i].older_boundary_id.as_str()))
        .collect();
    if heads.is_empty() {
        return Err(BRANCHED.into()); // every group is in a cycle
    }

    let mut segments: Vec<Vec<SpanGroup>> = Vec::new();
    let mut seen: HashSet<usize> = HashSet::new();
    for head in heads {
        let mut segment = Vec::new();
        let mut current = Some(head);
        while let Some(index) = current {
            if !seen.insert(index) {
                return Err(BRANCHED.into());
            }
            let group = &plan.span_groups[index];
            segment.push(group.clone());
            current = by_older.get(group.younger_boundary_id.as_str()).copied();
        }
        segments.push(segment);
    }
    if seen.len() != plan.span_groups.len() {
        return Err(BRANCHED.into()); // a cycle hanging off a path
    }

    // Paths not ordered against each other.
    let ordered_groups = stitched(segments, &plan).ok_or_else(|| BRANCHED.to_owned())?;

    let mut boundary_ids: Vec<String> = ordered_groups
        .iter()
        .map(|g| g.older_boundary_id.clone())
        .collect();
    boundary_ids.push(
        ordered_groups[ordered_groups.len() - 1]
            .younger_boundary_id
            .clone(),
    );
    let distinct: HashSet<&String> = boundary_ids.iter().collect();
    if distinct.len() != boundary_ids.len() {
        return Err(BRANCHED.into());
    }
    let named: HashSet<&String> = plan.boundary_names.iter().map(|(id, _)| id).collect();
    if distinct != named {
        return Err(
            "a Boundary sits outside the sequence of phases, so it is not part of a \
                    single chain of transitions"
                .into(),
        );
    }

    // Every date must sit in exactly one group.
    let mut placed: HashMap<&str, usize> = HashMap::new();
    for (index, group) in ordered_groups.iter().enumerate() {
        for date_id in &group.date_ids {
            if placed.insert(date_id, index).is_some() {
                return Err(BRANCHED.into());
            }
        }
    }
    let unenclosed: Vec<String> = measurements
        .iter()
        .filter(|m| !placed.contains_key(m.id.as_str()))
        .map(|m| m.id.clone())
        .collect();
    let mut older_blocks: Vec<Vec<String>> = Vec::new();
    let mut younger_blocks: Vec<Vec<String>> = Vec::new();
    if !unenclosed.is_empty() {
        if !allow_tails {
            return Err(outside_every_boundary_reason(&unenclosed, measurements));
        }
        (older_blocks, younger_blocks) = tails_with_reason(
            &plan,
            measurements,
            &unenclosed,
            &boundary_ids[0],
            &boundary_ids[boundary_ids.len() - 1],
        )?;
    }
    let older_tail: Vec<String> = older_blocks.iter().flatten().cloned().collect();
    let younger_tail: Vec<String> = younger_blocks.iter().flatten().cloned().collect();

    // The chain must already imply every ordering constraint the tree asks
    // for, but for dates ordered against each other INSIDE one boundary pair,
    // which are that group's blocks. Rank boundary i at 2i and group i at
    // 2i+1, oldest first; a constraint is implied when everything on its
    // older side outranks everything on its younger side.
    let rank_of_boundary: HashMap<&str, i64> = boundary_ids
        .iter()
        .enumerate()
        .map(|(i, b)| (b.as_str(), 2 * i as i64))
        .collect();
    // A tail sits beyond its end Boundary, one rank further out, and its
    // dates share that rank, as a group's do.
    let mut tail_rank: HashMap<&str, i64> = HashMap::new();
    for d in &older_tail {
        tail_rank.insert(d, -1);
    }
    for d in &younger_tail {
        tail_rank.insert(d, 2 * boundary_ids.len() as i64 - 1);
    }
    let rank = |kind: TermKind, tid: &str| -> Option<i64> {
        if kind == TermKind::Boundary {
            return rank_of_boundary.get(tid).copied();
        }
        if let Some(&r) = tail_rank.get(tid) {
            return Some(r);
        }
        placed.get(tid).map(|&index| 2 * index as i64 + 1)
    };

    let mut inner_order = older_blocks.len() > 1 || younger_blocks.len() > 1;
    for (older_terms, younger_terms) in &plan.ordering {
        let older: Option<Vec<i64>> = older_terms.iter().map(|(k, t)| rank(*k, t)).collect();
        let younger: Option<Vec<i64>> = younger_terms.iter().map(|(k, t)| rank(*k, t)).collect();
        let (Some(older), Some(younger)) = (older, younger) else {
            return Err(BRANCHED.into());
        };
        if older.iter().max() >= younger.iter().min() {
            inner_order = true;
        }
    }
    let groups: Vec<Vec<String>> = ordered_groups.iter().map(|g| g.date_ids.clone()).collect();
    let names = plan.boundary_names.clone();
    if allow_inner_order || !inner_order {
        return Ok(Chain::with_tails(
            boundary_ids,
            groups,
            names,
            older_tail,
            younger_tail,
        ));
    }

    // Order inside a group: its dates as blocks, and then the whole layout --
    // the chain, each group's blocks, the tails -- held to the model as
    // written, so integrating it cannot answer a different model.
    let closed = closure(&written_order(&plan));
    let mut blocks = Vec::new();
    for group in &groups {
        blocks.push(blocks_with_reason(group, &closed)?);
    }
    let mut laid = Order::new();
    order_chain(&mut laid, &boundary_ids, &blocks);
    order_tails(
        &mut laid,
        &boundary_ids[0],
        &boundary_ids[boundary_ids.len() - 1],
        &older_blocks,
        &younger_blocks,
    );
    if closure(&laid) != closed {
        return Err(
            "its dates are ordered in a way one line of phases between Boundaries \
                    cannot express"
                .into(),
        );
    }
    let mut chain = Chain::with_tails(boundary_ids, groups, names, older_tail, younger_tail);
    chain.blocks = blocks;
    if !older_blocks.is_empty() {
        chain.older_tail_blocks = older_blocks;
    }
    if !younger_blocks.is_empty() {
        chain.younger_tail_blocks = younger_blocks;
    }
    Ok(chain)
}

/// Dates as blocks, oldest first.
pub type Blocks = Vec<Vec<String>>;

/// {parameter: the parameters it is ordered directly older than}.
pub type Order = HashMap<String, HashSet<String>>;

/// `date_ids` as BLOCKS, oldest first, given `closed`, everything each
/// parameter is older than ([`closure`]). Dates in one block are not ordered
/// against each other, and every date of a block is older than every date of
/// the next: Phases in a row with no Boundary between them, or a Sequence of
/// Phases with none, inside one pair of Boundaries. One block when nothing
/// orders them. A reason for any other order -- two such Sequences side by
/// side, say -- which does not reduce to blocks.
pub fn blocks_with_reason(date_ids: &[String], closed: &Order) -> Result<Vec<Vec<String>>, String> {
    let members = date_ids;
    if members.len() < 2 {
        return Ok(one_block(members));
    }
    let older_than = |a: &str, b: &str| closed.get(a).is_some_and(|s| s.contains(b));
    let ordered = |a: &str, b: &str| older_than(a, b) || older_than(b, a);

    let mut parent: Vec<usize> = (0..members.len()).collect();
    fn find(parent: &mut [usize], mut d: usize) -> usize {
        while parent[d] != d {
            parent[d] = parent[parent[d]];
            d = parent[d];
        }
        d
    }
    for i in 0..members.len() {
        for j in i + 1..members.len() {
            if !ordered(&members[i], &members[j]) {
                let (ri, rj) = (find(&mut parent, i), find(&mut parent, j));
                parent[ri] = rj;
            }
        }
    }
    let mut roots: Vec<usize> = Vec::new();
    let mut blocks: Vec<Vec<String>> = Vec::new();
    #[allow(clippy::needless_range_loop)]
    for i in 0..members.len() {
        let root = find(&mut parent, i);
        match roots.iter().position(|&r| r == root) {
            Some(k) => blocks[k].push(members[i].clone()),
            None => {
                roots.push(root);
                blocks.push(vec![members[i].clone()]);
            }
        }
    }
    for block in &blocks {
        for (i, a) in block.iter().enumerate() {
            if block[i + 1..].iter().any(|b| ordered(a, b)) {
                return Err(NOT_ONE_AFTER_ANOTHER.into());
            }
        }
    }
    // Oldest first: a block is older than every block its first date precedes.
    let counts: Vec<usize> = (0..blocks.len())
        .map(|k| {
            (0..blocks.len())
                .filter(|&o| o != k && older_than(&blocks[k][0], &blocks[o][0]))
                .count()
        })
        .collect();
    let mut order: Vec<usize> = (0..blocks.len()).collect();
    order.sort_by_key(|&k| std::cmp::Reverse(counts[k]));
    let blocks: Vec<Vec<String>> = order.into_iter().map(|k| blocks[k].clone()).collect();
    for (i, older) in blocks.iter().enumerate() {
        for younger in &blocks[i + 1..] {
            if !older
                .iter()
                .all(|o| younger.iter().all(|y| older_than(o, y)))
            {
                return Err(NOT_ONE_AFTER_ANOTHER.into());
            }
        }
    }
    Ok(blocks)
}

pub(crate) fn edge(order: &mut Order, older: &str, younger: &str) {
    order
        .entry(older.to_owned())
        .or_default()
        .insert(younger.to_owned());
    order.entry(younger.to_owned()).or_default();
}

/// Each block's dates older than every date of the next.
fn order_blocks(order: &mut Order, blocks: &[Vec<String>]) {
    for pair in blocks.windows(2) {
        for o in &pair[0] {
            for y in &pair[1] {
                edge(order, o, y);
            }
        }
    }
}

/// A chain's ordering: each group between its two Boundaries, in blocks.
pub(crate) fn order_chain(order: &mut Order, boundary_ids: &[String], blocks: &[Vec<Vec<String>>]) {
    for (index, group_blocks) in blocks.iter().enumerate() {
        let (older, younger) = (&boundary_ids[index], &boundary_ids[index + 1]);
        edge(order, older, younger);
        for block in group_blocks {
            for date_id in block {
                edge(order, older, date_id);
                edge(order, date_id, younger);
            }
        }
        order_blocks(order, group_blocks);
    }
}

/// Tails' ordering: beyond their end Boundary, in blocks.
pub(crate) fn order_tails(
    order: &mut Order,
    first_id: &str,
    last_id: &str,
    older_blocks: &[Vec<String>],
    younger_blocks: &[Vec<String>],
) {
    for date_id in older_blocks.iter().flatten() {
        edge(order, date_id, first_id);
    }
    for date_id in younger_blocks.iter().flatten() {
        edge(order, last_id, date_id);
    }
    order_blocks(order, older_blocks);
    order_blocks(order, younger_blocks);
}

/// The reason given for dates no Boundary pair encloses, naming them.
pub fn outside_every_boundary_reason(date_ids: &[String], measurements: &[Measurement]) -> String {
    let by_id: HashMap<&str, &Measurement> =
        measurements.iter().map(|m| (m.id.as_str(), m)).collect();
    let mut names: Vec<&str> = date_ids
        .iter()
        .take(4)
        .map(|d| by_id.get(d.as_str()).map_or(d.as_str(), |m| m.label()))
        .collect();
    names.sort_unstable();
    let mut listed = names.join(", ");
    if date_ids.len() > 4 {
        listed += &format!(" and {} more", date_ids.len() - 4);
    }
    format!(
        "{} date(s) sit outside every Boundary ({listed}). Add a Boundary either side of the \
         phases holding them so they fall inside the chain",
        date_ids.len()
    )
}

/// (older tail, younger tail), each tail as its blocks oldest first
/// ([`blocks_with_reason`]), for dates outside every Boundary that this solver
/// can take, else why not.
///
/// A date outside every Boundary gets no span term -- its prior is flat, as
/// OxCal's is -- and is held only by its order against the model's first or
/// last Boundary. So it must be ordered older than the first or younger than
/// the last; one ordered against neither floats free of the model and is left
/// to the sampler. One carrying an outlier model is too: nothing holds its
/// outlier component, so its answer would depend on how far the calculation
/// window happens to reach. Dates at one end ordered against each other must
/// be groups one after another, as inside a group.
pub fn tails_with_reason(
    plan: &ModelPlan,
    measurements: &[Measurement],
    date_ids: &[String],
    first_id: &str,
    last_id: &str,
) -> Result<(Blocks, Blocks), String> {
    let by_id: HashMap<&str, &Measurement> =
        measurements.iter().map(|m| (m.id.as_str(), m)).collect();
    let closed = closure(&written_order(plan));
    let (mut older, mut younger, mut loose) = (Vec::new(), Vec::new(), Vec::new());
    for date_id in date_ids {
        if closed.get(date_id).is_some_and(|s| s.contains(first_id)) {
            older.push(date_id.clone());
        } else if closed.get(last_id).is_some_and(|s| s.contains(date_id)) {
            younger.push(date_id.clone());
        } else {
            loose.push(date_id.clone());
        }
    }
    if !loose.is_empty() {
        return Err(outside_every_boundary_reason(&loose, measurements));
    }
    let older_blocks = blocks_with_reason(&older, &closed)?;
    let younger_blocks = blocks_with_reason(&younger, &closed)?;
    let flagged: Vec<&String> = older
        .iter()
        .chain(&younger)
        .filter(|d| by_id[d.as_str()].outlier_prior() > 0.0)
        .collect();
    if !flagged.is_empty() {
        let mut names: Vec<&str> = flagged
            .iter()
            .take(4)
            .map(|d| by_id[d.as_str()].label())
            .collect();
        names.sort_unstable();
        return Err(format!(
            "{} date(s) outside every Boundary carry an outlier model ({}). With nothing on \
             their far side to hold it, the outlier component reaches the edge of the \
             calculation, so they are sampled",
            flagged.len(),
            names.join(", ")
        ));
    }
    Ok((older_blocks, younger_blocks))
}

/// {parameter: the parameters the model as written orders directly younger
/// than it}: the plan's ordering, and each group's containment of its dates
/// between its two Boundaries.
pub fn written_order(plan: &ModelPlan) -> Order {
    let mut order = Order::new();
    for (older_terms, younger_terms) in &plan.ordering {
        for (_, older) in older_terms {
            for (_, younger) in younger_terms {
                edge(&mut order, older, younger);
            }
        }
    }
    for group in &plan.span_groups {
        edge(
            &mut order,
            &group.older_boundary_id,
            &group.younger_boundary_id,
        );
        for date_id in group.date_ids.iter().chain(&group.contained_ids) {
            edge(&mut order, &group.older_boundary_id, date_id);
            edge(&mut order, date_id, &group.younger_boundary_id);
        }
    }
    order
}

/// Everything each parameter is older than, transitively. Iterative, youngest
/// first, so a long model cannot exhaust the stack; a cycle, which no
/// well-formed model has, leaves its members unclosed and so unequal to
/// anything a layout gives.
pub fn closure(order: &Order) -> Order {
    let mut pending: HashMap<&str, usize> =
        order.iter().map(|(p, y)| (p.as_str(), y.len())).collect();
    let mut older_of: HashMap<&str, Vec<&str>> = HashMap::new();
    for (param, younger) in order {
        for y in younger {
            older_of.entry(y).or_default().push(param);
        }
    }
    let mut ready: Vec<&str> = pending
        .iter()
        .filter(|(_, c)| **c == 0)
        .map(|(p, _)| *p)
        .collect();
    let mut closed = Order::new();
    while let Some(param) = ready.pop() {
        let mut found: HashSet<String> = HashSet::new();
        for younger in order.get(param).into_iter().flatten() {
            found.insert(younger.clone());
            found.extend(closed[younger].iter().cloned());
        }
        closed.insert(param.to_owned(), found);
        for older in older_of.get(param).into_iter().flatten() {
            let count = pending.get_mut(older).expect("every parameter is pending");
            *count -= 1;
            if *count == 0 {
                ready.push(older);
            }
        }
    }
    closed
}

/// Why `plan` cannot be integrated exactly whatever its shape, or None.
///
/// Both of these couple the dates to EACH OTHER rather than only through
/// their Boundaries, which is the one thing the message passing cannot
/// express -- the same obstruction the module docs record for the outlier
/// scale, and with the same resolution: say so, and let the caller take the
/// sampler or the closed form. Shared with [`crate::exact_parallel`].
pub fn coupled_dates_reason(plan: &ModelPlan) -> Option<&'static str> {
    if !plan.deposition_groups.is_empty() {
        return Some(
            "this model contains a deposition sequence, whose ages are coupled to each other \
             by the deposition rate rather than only through their Boundaries, so it cannot \
             be integrated exactly",
        );
    }
    if !plan.wiggle_groups.is_empty() {
        return Some(
            "this model contains a wiggle match, whose dates are one calendar age with the \
             measured gaps added to it rather than separate parameters, so it is not a chain \
             of Boundaries. It is solved in closed form instead (services/wiggle_match.py)",
        );
    }
    None
}

/// Paths of bounded groups joined into one line, oldest first, or None when
/// the tree does not order them one after another.
///
/// A Sequence of Sequences, each bracketed by its own Boundaries, plans as one
/// path per inner Sequence: nothing encloses the gap between one's last
/// Boundary and the next one's first, only an ordering constraint across it.
/// That is exactly an EMPTY group there -- (span)^0 = 1 and the ordering, kept
/// strict like every other pair of Boundaries -- which is also what writing
/// the inner children out in place plans to. So the paths are joined by one,
/// but only where the tree's ordering already forces the earlier path's last
/// Boundary older than the later one's first. Anything less would ADD an
/// ordering: two independently bracketed branches inside a Phase are paths
/// too, and they must stay unchained.
///
/// The sampler sees the same prior from the unstitched plan: its span
/// correction groups Boundaries by what the ordering makes comparable, which
/// puts every Boundary of the stitched line in one component with the same
/// outermost pair, and an empty group contributes nothing to it.
fn stitched(segments: Vec<Vec<SpanGroup>>, plan: &ModelPlan) -> Option<Vec<SpanGroup>> {
    if segments.len() == 1 {
        return segments.into_iter().next();
    }
    // Everything forced younger than each term, through the ordering and
    // through each group's containment of its dates.
    let mut younger_of: HashMap<&str, Vec<&str>> = HashMap::new();
    for (older_terms, younger_terms) in &plan.ordering {
        for (_, older) in older_terms {
            for (_, younger) in younger_terms {
                younger_of.entry(older).or_default().push(younger);
            }
        }
    }
    for group in &plan.span_groups {
        for date_id in group.date_ids.iter().chain(&group.contained_ids) {
            younger_of
                .entry(&group.older_boundary_id)
                .or_default()
                .push(date_id);
            younger_of
                .entry(date_id)
                .or_default()
                .push(&group.younger_boundary_id);
        }
    }
    let reachable = |start: &str| -> HashSet<String> {
        let mut found: HashSet<String> = HashSet::new();
        let mut stack = vec![start.to_owned()];
        while let Some(node) = stack.pop() {
            for &next in younger_of.get(node.as_str()).into_iter().flatten() {
                if found.insert(next.to_owned()) {
                    stack.push(next.to_owned());
                }
            }
        }
        found
    };
    let below: Vec<HashSet<String>> = segments
        .iter()
        .map(|s| reachable(&s[s.len() - 1].younger_boundary_id))
        .collect();
    let heads: Vec<&str> = segments
        .iter()
        .map(|s| s[0].older_boundary_id.as_str())
        .collect();
    // A path forced older than k others belongs k places from the end; the
    // check below catches any pair the ordering leaves unrelated.
    let mut order: Vec<usize> = (0..segments.len()).collect();
    order.sort_by_key(|&i| {
        std::cmp::Reverse(heads.iter().filter(|h| below[i].contains(**h)).count())
    });
    let mut joined = segments[order[0]].clone();
    for pair in order.windows(2) {
        let (earlier, later) = (pair[0], pair[1]);
        if !below[earlier].contains(heads[later]) {
            return None;
        }
        let last = &segments[earlier][segments[earlier].len() - 1];
        joined.push(SpanGroup {
            older_boundary_id: last.younger_boundary_id.clone(),
            younger_boundary_id: heads[later].to_owned(),
            date_ids: Vec::new(),
            contained_ids: Vec::new(),
        });
        joined.extend(segments[later].iter().cloned());
    }
    Some(joined)
}
