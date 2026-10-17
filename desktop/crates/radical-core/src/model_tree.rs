//! The explicit model structure -- OxCal's Sequence / Phase / Boundary / Hiatus
//! as a nested, ordered tree -- and the translation of that tree into the
//! pieces the solvers need to turn it into a posterior. Port of
//! `services/model_tree.py`.
//!
//! Supersedes the flat arrangement of Contexts plus pairwise above/below
//! relationships, which could not express a deliberately-placed Boundary, a
//! site-wide Sequence, or any nesting, and made every context's boundaries
//! independent of its neighbours' where OxCal's are one shared transition.
//!
//! Semantics, following the OxCal documentation
//! (<https://c14.arch.ox.ac.uk/oxcalhelp/hlp_analysis_oper.html>):
//!
//! ```text
//! sequence  Children are in chronological order, OLDEST FIRST (ascending
//!           `position`, matching OxCal's reading order and the Harris matrix
//!           convention of oldest at the bottom). Everything in one child is
//!           older than everything in the next.
//! phase     Children are unordered with respect to each other. Dates attach
//!           here. Nest sequences inside a phase to model parallel branches
//!           that are unordered relative to one another.
//! boundary  A single latent transition parameter, and an ordinary ordered
//!           element of its parent sequence. A boundary placed BETWEEN two
//!           phases is therefore ONE shared parameter -- simultaneously the
//!           end of the earlier phase and the start of the later one -- not
//!           two independent ones.
//! hiatus    Marks a deliberate gap. It carries no parameter of its own: a
//!           contiguous transition is one Boundary between two phases, and a
//!           gap is two Boundaries with a `hiatus` between them, so the maths
//!           is identical and only the element count differs.
//! ```
//!
//! Nesting follows the documentation's rule that "the outer boundaries of the
//! inner group are treated in exactly the same way as any other elements within
//! the outer group": an inner sequence's boundaries take part in the outer
//! sequence's ordering like anything else. Each date belongs to exactly ONE
//! bounded group -- the innermost pair of boundaries enclosing it -- so a
//! nested group's dates are not also counted in its parent's span term.
//!
//! # Sequences with a measured ordinate
//!
//! Two more element types order their members by a distance measured on an axis
//! OUTSIDE the model rather than by stratigraphy alone. They are the same idea
//! with two solvers behind them (docs/AGE_DEPTH_AND_WIGGLE_MATCH_PLAN.md):
//!
//! ```text
//! wiggle_sequence      OxCal's D_Sequence / V_Sequence. Its members are
//!                      separated by KNOWN gaps -- tree rings, varves, any
//!                      sampled series -- so the whole series has ONE free
//!                      parameter, the calendar age at the reference point, and
//!                      every member's age is that plus its own `ring_offset`.
//!                      Solved in closed form by wiggle_match.
//! deposition_sequence  OxCal's P_Sequence. Its members are separated by DEPTH,
//!                      and deposition is modelled as a Poisson process along
//!                      it, so the intervals carry their own prior with a
//!                      rigidity `k`. Every member is a free parameter; solved
//!                      by the sampler.
//! interpolated_depth   A level inside a deposition_sequence with no
//!                      measurement -- "how old is the level at 47 cm". A
//!                      parameter whose likelihood table is flat, which is
//!                      precisely the right answer, so it needs no special case
//!                      in the sampler.
//! ```
//!
//! Dates attach to a wiggle_sequence and a deposition_sequence DIRECTLY, as they
//! do to a phase, because in both the ordinate is a property of the date and
//! inserting a phase per member would say something the model does not.
//!
//! THE ONE THING BOTH GET WRONG QUIETLY. A deposition_sequence supplies its own
//! prior on the intervals between its members, and a wiggle_sequence fixes them
//! outright. Neither set of dates may ALSO be treated as a uniform scatter
//! between the enclosing Boundaries -- two priors over the same intervals would
//! not crash and would not look wrong. So they bubble up as CONTAINED rather than
//! as members of the span term; see [`SpanGroup`].
//!
//! Departures from the Python shape: a tree is an [`Element`] rather than a
//! dict, and its `params` a map of [`Param`] values so a setting keeps
//! Python's truthiness. `rows_to_tree` and `tree_to_rows` convert to and from
//! database rows, and belong to the store rather than here.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

use crate::Error;
use crate::measurement::Measurement;
use crate::numeric::{py_max, py_min};
use crate::pyfmt;
use crate::stratigraphy::{self, Group, Relationship};

/// Every element type a tree may hold.
pub const ELEMENT_TYPES: [&str; 7] = [
    "sequence",
    "phase",
    "boundary",
    "hiatus",
    "wiggle_sequence",
    "deposition_sequence",
    "interpolated_depth",
];

/// Element types dates may be attached to. A date is a measurement of one
/// event, and these are the elements that say where that event sits.
pub const DATE_HOLDERS: [&str; 3] = ["phase", "wiggle_sequence", "deposition_sequence"];

/// One setting in an element's `params`, as JSON gives it.
#[derive(Debug, Clone, PartialEq)]
pub enum Param {
    Null,
    Bool(bool),
    Number(f64),
    Text(String),
}

impl Param {
    /// Python's truthiness of the value.
    pub fn truthy(&self) -> bool {
        match self {
            Self::Null => false,
            Self::Bool(b) => *b,
            Self::Number(v) => *v != 0.0,
            Self::Text(s) => !s.is_empty(),
        }
    }

    /// `float(value)`, None where Python's would raise.
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Self::Null => None,
            Self::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
            Self::Number(v) => Some(*v),
            Self::Text(s) => s.trim().parse().ok(),
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Self::Text(s) => Some(s),
            _ => None,
        }
    }
}

/// An element's `params`: what is set, by name.
pub type Params = BTreeMap<String, Param>;

/// One node of a model tree.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Element {
    pub id: String,
    pub element_type: String,
    pub name: Option<String>,
    pub tpq_age_bp: Option<f64>,
    pub taq_age_bp: Option<f64>,
    pub params: Option<Params>,
    /// In chronological order, oldest first.
    pub children: Vec<Element>,
}

impl Element {
    /// `element.get('name')` where an empty name counts as none.
    pub fn given_name(&self) -> Option<&str> {
        self.name.as_deref().filter(|n| !n.is_empty())
    }

    pub fn is(&self, element_type: &str) -> bool {
        self.element_type == element_type
    }
}

/// What an unnamed element of each type is called on screen and in the
/// exports. Only the ones that do not title-case into English need an entry.
fn default_label(element_type: &str) -> Option<&'static str> {
    match element_type {
        "wiggle_sequence" => Some("Wiggle match"),
        "deposition_sequence" => Some("Deposition sequence"),
        "interpolated_depth" => Some("Undated level"),
        _ => None,
    }
}

/// Python's `str.title()`: each run of letters capitalised, the rest lowered.
fn title(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut previous_cased = false;
    for c in text.chars() {
        if c.is_alphabetic() {
            if previous_cased {
                out.extend(c.to_lowercase());
            } else {
                out.extend(c.to_uppercase());
            }
            previous_cased = true;
        } else {
            out.push(c);
            previous_cased = false;
        }
    }
    out
}

/// An element's name, or a readable default for its type.
pub fn element_label(element: &Element) -> String {
    let etype = if element.element_type.is_empty() {
        "element"
    } else {
        &element.element_type
    };
    if let Some(name) = element.given_name() {
        return name.to_owned();
    }
    default_label(etype).map_or_else(|| title(etype), str::to_owned)
}

/// What `params` holds, per element type, with the default for each setting.
///
/// wiggle_sequence: `gap_units` ('rings' | 'varves' | 'years' -- what a gap is
/// counted in; carrying it makes the axis label honest rather than calling
/// everything "rings") and `reference_sample_id` (which member `ring_offset` 0
/// refers to; the solved parameter is the age AT offset zero whether or not a
/// member sits there, so this is for labelling).
///
/// deposition_sequence: `k`, the rigidity in events per unit depth (large k
/// drives the ages onto a straight line in depth; small k leaves little but the
/// ordering), inferred over `log_k_min`..`log_k_max` unless `k_variable` is
/// false, because a fixed k chosen badly dominates the answer; and
/// `depth_units`.
///
/// interpolated_depth: `depth_cm`, the depth the undated level sits at, in the
/// sequence's own units.
pub fn element_param_defaults(element_type: &str) -> Params {
    let entries: Vec<(&str, Param)> = match element_type {
        "wiggle_sequence" => vec![
            ("gap_units", Param::Text("rings".into())),
            ("reference_sample_id", Param::Null),
        ],
        "deposition_sequence" => vec![
            ("k", Param::Null),
            ("k_variable", Param::Bool(true)),
            ("log_k_min", Param::Number(-2.0)),
            ("log_k_max", Param::Number(2.0)),
            ("depth_units", Param::Text("cm".into())),
        ],
        "interpolated_depth" => vec![("depth_cm", Param::Null)],
        _ => vec![],
    };
    entries
        .into_iter()
        .map(|(k, v)| (k.to_owned(), v))
        .collect()
}

/// One element's `params`, with the defaults for its type filled in.
///
/// Read through this rather than off the element, so a tree written before a
/// setting existed reads as that setting's default rather than as None --
/// which is what lets the defaults gain an entry without a migration.
pub fn element_params(element: &Element) -> Params {
    let mut merged = element_param_defaults(&element.element_type);
    if let Some(params) = &element.params {
        for (key, value) in params {
            if *value != Param::Null || merged.contains_key(key) {
                merged.insert(key.clone(), value.clone());
            }
        }
    }
    merged
}

fn param<'p>(params: &'p Params, key: &str) -> &'p Param {
    params.get(key).unwrap_or(&Param::Null)
}

/// A term carrying a calendar age in the model: either a dated sample or a
/// boundary parameter. Ordering constraints are expressed over these. An
/// interpolated level is a date too -- it is a free calendar parameter with a
/// flat likelihood, and every ordering rule that applies to a date applies to
/// it unchanged.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum TermKind {
    Date,
    Boundary,
}

pub type Term = (TermKind, String);

/// A set of parameters enclosed by a consecutive pair of Boundary siblings.
///
/// `date_ids` are the UNIFORM SCATTER: conditional on its boundaries each of
/// those K dates is uniform between them, which together contribute OxCal's
/// Phase density (older - younger)^(-K).
///
/// `contained_ids` must also lie between the two boundaries but are NOT part
/// of that term, because something else already prices the intervals between
/// them -- a deposition sequence's Poisson prior, or a wiggle sequence's
/// fixed gaps. Counting them twice would leave phase widths wrong by a factor
/// nobody could eyeball, so the two are kept apart at the point the structure
/// is planned rather than at the point it is solved.
#[derive(Debug, Clone, PartialEq)]
pub struct SpanGroup {
    pub older_boundary_id: String,
    pub younger_boundary_id: String,
    pub date_ids: Vec<String>,
    pub contained_ids: Vec<String>,
}

/// One wiggle_sequence: a series whose members are separated by known gaps,
/// and therefore ONE free calendar parameter.
///
/// `members` are (date_id, ring_offset, ring_offset_error) ordered YOUNGEST
/// FIRST -- ascending offset, since an offset counts years BEFORE the
/// reference and so increases into the past. `element_id` names the parameter
/// itself: the calendar age at offset zero.
#[derive(Debug, Clone, PartialEq)]
pub struct WiggleGroup {
    pub element_id: String,
    pub name: String,
    pub members: Vec<(String, f64, f64)>,
    pub gap_units: String,
    pub reference_sample_id: Option<String>,
}

impl WiggleGroup {
    pub fn date_ids(&self) -> Vec<String> {
        self.members.iter().map(|(id, _, _)| id.clone()).collect()
    }
}

/// One deposition_sequence: levels at measured depths whose ages are coupled
/// by a Poisson deposition model rather than only by their ordering.
///
/// `levels` are (parameter_id, depth, is_dated) ordered OLDEST FIRST --
/// descending depth, since deeper is older, which is the same convention
/// `position` uses for a Sequence's children. A level with is_dated false is
/// an interpolated_depth: a free parameter with a flat likelihood.
///
/// `k` is the rigidity: a number when the model fixes it, None when the run
/// should infer it over [log_k_min, log_k_max].
#[derive(Debug, Clone, PartialEq)]
pub struct DepositionGroup {
    pub element_id: String,
    pub name: String,
    pub levels: Vec<(String, f64, bool)>,
    pub k: Option<f64>,
    pub k_variable: bool,
    pub log_k_min: f64,
    pub log_k_max: f64,
    pub depth_units: String,
    /// An undated level's own name where it was given one, so a reported
    /// posterior can say "Tephra layer" rather than only "47 cm".
    pub level_names: HashMap<String, String>,
}

impl DepositionGroup {
    pub fn parameter_ids(&self) -> Vec<String> {
        self.levels.iter().map(|(id, _, _)| id.clone()).collect()
    }
}

/// One ordering constraint: every term on the left must be older (larger cal
/// BP) than every term on the right.
pub type Ordering = (Vec<Term>, Vec<Term>);

/// Everything a solver needs to turn a tree into a posterior.
#[derive(Debug, Clone, PartialEq)]
pub struct ModelPlan {
    /// One constraint per adjacent pair of sequence children rather than the
    /// full cross-product, which min(older) >= max(younger) makes equivalent.
    pub ordering: Vec<Ordering>,
    pub span_groups: Vec<SpanGroup>,
    /// Boundaries integrated out analytically rather than sampled; see
    /// [`classify_boundaries`].
    pub marginalized_boundaries: BTreeSet<String>,
    pub sampled_boundaries: Vec<String>,
    /// Every Boundary's name, in tree order.
    pub boundary_names: Vec<(String, String)>,
    pub wiggle_groups: Vec<WiggleGroup>,
    pub deposition_groups: Vec<DepositionGroup>,
}

impl ModelPlan {
    /// A Boundary's name, `boundary_names.get(id)`.
    pub fn boundary_name(&self, id: &str) -> Option<&str> {
        self.boundary_names
            .iter()
            .find(|(b, _)| b == id)
            .map(|(_, n)| n.as_str())
    }
}

// --------------------------------------------------------------------------
// Tree walking
// --------------------------------------------------------------------------

/// Depth-first walk over every element in the tree, a parent before its
/// children and siblings oldest first.
pub fn iter_elements(root: &Element) -> Vec<&Element> {
    let mut out = Vec::new();
    let mut stack = vec![root];
    while let Some(element) = stack.pop() {
        out.push(element);
        stack.extend(element.children.iter().rev());
    }
    out
}

fn fail(message: String) -> Error {
    Error::Model(message)
}

/// Reject a structurally impossible model before any run, as
/// BayesianRequest's validators do. No cycle check: a tree cannot contain
/// one.
///
/// Structure only. Whether each date carries the ORDINATE its element is
/// built on -- a ring offset, a depth -- is [`validate_ordinates`]' job,
/// because most callers of this function have only the ids and the element
/// assignments to hand.
pub fn validate_tree(root: Option<&Element>, measurements: &[Measurement]) -> Result<(), Error> {
    let Some(root) = root else {
        return Err(fail("model tree is empty".into()));
    };
    if ![
        "sequence",
        "phase",
        "wiggle_sequence",
        "deposition_sequence",
    ]
    .contains(&root.element_type.as_str())
    {
        return Err(fail(
            "the root element must be a sequence, a phase, a wiggle match or a deposition sequence"
                .into(),
        ));
    }

    let mut seen_ids: HashSet<&str> = HashSet::new();
    let mut element_types: HashMap<&str, &str> = HashMap::new();
    let mut dates_per_element: HashMap<&str, usize> = HashMap::new();
    for m in measurements {
        if let Some(e) = m.element_id.as_deref().filter(|e| !e.is_empty()) {
            *dates_per_element.entry(e).or_default() += 1;
        }
    }
    let dated = |eid: &str| dates_per_element.get(eid).copied().unwrap_or(0);

    for element in iter_elements(root) {
        let eid = element.id.as_str();
        if eid.is_empty() {
            return Err(fail("every model element needs an id".into()));
        }
        if !seen_ids.insert(eid) {
            return Err(fail(format!(
                "model element id '{eid}' appears more than once (a tree cannot revisit a node)"
            )));
        }
        let etype = element.element_type.as_str();
        if !ELEMENT_TYPES.contains(&etype) {
            return Err(fail(format!(
                "unknown element_type '{etype}' on element '{eid}'"
            )));
        }
        element_types.insert(eid, etype);
        let children = &element.children;
        let shown = element.given_name().unwrap_or(eid);

        if ["boundary", "hiatus", "interpolated_depth"].contains(&etype) && !children.is_empty() {
            return Err(fail(format!(
                "a {etype} cannot contain other elements (element '{eid}')"
            )));
        }

        if etype == "wiggle_sequence" {
            if !children.is_empty() {
                return Err(fail(format!(
                    "a wiggle match holds dates and nothing else (element '{eid}') -- its \
                     members are separated by measured gaps, so there is no position in it \
                     for a phase or a boundary"
                )));
            }
            if dated(eid) == 0 {
                return Err(fail(format!(
                    "the wiggle match '{shown}' has no dates in it; a series of gaps with \
                     nothing measured at them says nothing about the calendar"
                )));
            }
        }

        if etype == "deposition_sequence" {
            for child in children {
                if !child.is("interpolated_depth") {
                    return Err(fail(format!(
                        "a deposition sequence holds dates and undated levels only (element \
                         '{eid}' contains a {})",
                        child.element_type
                    )));
                }
            }
            if dated(eid) == 0 {
                return Err(fail(format!(
                    "the deposition sequence '{shown}' has no dates in it; deposition cannot \
                     be modelled against depth with nothing measured at any level"
                )));
            }
            if dated(eid) + children.len() < 2 {
                return Err(fail(format!(
                    "the deposition sequence '{shown}' has one level in it; a deposition rate \
                     is a statement about the interval between two, so a single level has \
                     nothing to model"
                )));
            }
        }

        if etype == "interpolated_depth" {
            if dated(eid) > 0 {
                return Err(fail(format!(
                    "the undated level '{shown}' has a date attached to it. It exists to \
                     report an age where nothing was dated; a level with a measurement is an \
                     ordinary date at that depth"
                )));
            }
            if *param(&element_params(element), "depth_cm") == Param::Null {
                return Err(fail(format!(
                    "the undated level '{shown}' has no depth. A level with no depth has no \
                     place in a deposition model and no age to report"
                )));
            }
        }

        if etype == "phase" && children.iter().any(|c| c.is("boundary")) {
            return Err(fail(format!(
                "a boundary cannot sit directly inside a phase (element '{eid}') -- a phase \
                 is unordered, so there is no position for a transition to mark. Put the \
                 boundary in a sequence."
            )));
        }

        // A hiatus only means anything between two boundaries.
        if etype == "sequence" {
            for (i, child) in children.iter().enumerate() {
                if !child.is("hiatus") {
                    continue;
                }
                let before = (i > 0).then(|| children[i - 1].element_type.as_str());
                let after = children.get(i + 1).map(|c| c.element_type.as_str());
                if before != Some("boundary") || after != Some("boundary") {
                    return Err(fail(format!(
                        "a hiatus must sit between two boundaries (element '{}') -- it marks \
                         a gap between the end of one group and the start of the next",
                        child.id
                    )));
                }
            }
        }

        if let (Some(tpq), Some(taq)) = (element.tpq_age_bp, element.taq_age_bp)
            && taq > tpq
        {
            return Err(fail(format!(
                "element '{eid}' has taq_age_bp ({}) greater than tpq_age_bp ({}); the valid \
                 range would be empty",
                pyfmt::repr(taq),
                pyfmt::repr(tpq)
            )));
        }
    }

    // Every date must live somewhere that gives its event a position.
    for m in measurements {
        let name = m.name_or_id();
        let Some(element_id) = m.element_id.as_deref().filter(|e| !e.is_empty()) else {
            return Err(fail(format!(
                "sample '{name}' is not assigned to any model element"
            )));
        };
        if !seen_ids.contains(element_id) {
            return Err(fail(format!(
                "sample '{name}' references unknown model element '{element_id}'"
            )));
        }
        let etype = element_types[element_id];
        if !DATE_HOLDERS.contains(&etype) {
            return Err(fail(format!(
                "sample '{name}' is attached to a {etype}; dates belong to a phase, a wiggle \
                 match or a deposition sequence"
            )));
        }
    }
    Ok(())
}

/// The first four of `names`, sorted, joined: how the messages below list
/// offending samples.
fn first_four_sorted(mut names: Vec<String>) -> String {
    names.sort();
    names.truncate(4);
    names.join(", ")
}

/// What each element requires of the DATES inside it: the ordinate it is
/// built on, and the settings it can and cannot honour.
///
/// Separate from [`validate_tree`] because all of this lives on the date rather
/// than on the element, and most callers of validate_tree hand it only ids
/// and element assignments. Called wherever the full measurements are to hand
/// -- the request schema, the importer, the model-tree editor's endpoint and
/// the solvers -- so a model that cannot be solved is refused before anything
/// is written rather than minutes into a run.
pub fn validate_ordinates(
    root: Option<&Element>,
    measurements: &[Measurement],
) -> Result<(), Error> {
    let Some(root) = root else {
        return Ok(());
    };
    let mut by_element: HashMap<&str, Vec<&Measurement>> = HashMap::new();
    for m in measurements {
        if let Some(e) = m.element_id.as_deref().filter(|e| !e.is_empty()) {
            by_element.entry(e).or_default().push(m);
        }
    }
    let label = |m: &Measurement| m.label().to_owned();

    for element in iter_elements(root) {
        let name = element.given_name().unwrap_or(&element.id);
        let members: &[&Measurement] = by_element
            .get(element.id.as_str())
            .map_or(&[], Vec::as_slice);

        if element.is("wiggle_sequence") {
            let missing: Vec<String> = members
                .iter()
                .filter(|m| m.ring_offset.is_none())
                .map(|m| label(m))
                .collect();
            if !missing.is_empty() {
                return Err(fail(format!(
                    "the wiggle match '{name}' has dates with no gap recorded ({}). A wiggle \
                     match IS the series of gaps: without one, a date says no more than it \
                     would on its own",
                    first_four_sorted(missing)
                )));
            }
            let offsets: Vec<f64> = members.iter().map(|m| m.ring_offset.unwrap()).collect();
            let distinct: HashSet<u64> = offsets.iter().map(|o| (o + 0.0).to_bits()).collect();
            if distinct.len() != offsets.len() {
                return Err(fail(format!(
                    "the wiggle match '{name}' has two dates at the same gap. Two samples from \
                     the same ring are one event measured twice, not two members of a series"
                )));
            }
            for m in members {
                if m.ring_offset_error.is_some_and(|e| e < 0.0) {
                    return Err(fail(format!(
                        "sample '{}' has a negative gap uncertainty",
                        label(m)
                    )));
                }
            }
            let params = element_params(element);
            if let Param::Text(reference) = param(&params, "reference_sample_id")
                && !members.iter().any(|m| &m.id == reference)
            {
                return Err(fail(format!(
                    "the wiggle match '{name}' names a reference sample that is not one of \
                     its members"
                )));
            }
            // REFUSED, not ignored. A wiggle match has one free parameter and
            // no room for a date to sit apart from the others: the gaps are
            // asserted, so a member that does not belong cannot be absorbed --
            // which is the whole reason the combination agreement index is a
            // required output here rather than a nicety. Accepting the setting
            // and dropping it would be the silent substitution this app
            // refuses everywhere else. The same for an inbuilt-age allowance:
            // the members are rings of one tree, a measured series, and an
            // offset applied to each on its own would let the series slide
            // apart.
            let allowed: Vec<String> = members
                .iter()
                .filter(|m| m.inbuilt_years() > 0.0)
                .map(|m| label(m))
                .collect();
            if !allowed.is_empty() {
                return Err(fail(format!(
                    "the wiggle match '{name}' has dates carrying an inbuilt-age allowance \
                     ({}), which it cannot apply: its members are rings of one series at \
                     measured gaps. Remove the allowance from those dates",
                    first_four_sorted(allowed)
                )));
            }
            let flagged: Vec<String> = members
                .iter()
                .filter(|m| m.outlier_prior() > 0.0)
                .map(|m| label(m))
                .collect();
            if !flagged.is_empty() {
                return Err(fail(format!(
                    "the wiggle match '{name}' has dates carrying an outlier model ({}), which \
                     it cannot apply. Its gaps are asserted, so there is nothing for a date to \
                     be an outlier from -- a member that does not belong shows up in the \
                     agreement index instead. Turn the outlier setting off on those dates, or \
                     model them outside the series",
                    first_four_sorted(flagged)
                )));
            }
        }

        if element.is("deposition_sequence") {
            let missing: Vec<String> = members
                .iter()
                .filter(|m| m.depth_cm.is_none())
                .map(|m| label(m))
                .collect();
            if !missing.is_empty() {
                return Err(fail(format!(
                    "the deposition sequence '{name}' has dates with no depth ({}). Depth is \
                     the axis the deposition model runs along, so a date without one cannot \
                     take part in it",
                    first_four_sorted(missing)
                )));
            }
            let mut depths: Vec<f64> = members.iter().map(|m| m.depth_cm.unwrap()).collect();
            depths.extend(
                element
                    .children
                    .iter()
                    .filter_map(|c| param(&element_params(c), "depth_cm").as_f64()),
            );
            let distinct: HashSet<u64> = depths.iter().map(|d| (d + 0.0).to_bits()).collect();
            if distinct.len() != depths.len() {
                return Err(fail(format!(
                    "the deposition sequence '{name}' has two levels at the same depth. The \
                     model prices the interval between successive depths, and a zero \
                     interval has no rate"
                )));
            }
            let params = element_params(element);
            if let Some(k) = param(&params, "k").as_f64()
                && k <= 0.0
            {
                return Err(fail(format!(
                    "the deposition sequence '{name}' has a rigidity of {}. k is events per \
                     unit depth and must be positive",
                    match param(&params, "k") {
                        Param::Number(v) => pyfmt::repr(*v),
                        other => format!("{other:?}"),
                    }
                )));
            }
            if let (Some(lo), Some(hi)) = (
                param(&params, "log_k_min").as_f64(),
                param(&params, "log_k_max").as_f64(),
            ) && lo >= hi
            {
                return Err(fail(format!(
                    "the deposition sequence '{name}' has an empty range for its rigidity: \
                     log_k_min is not below log_k_max"
                )));
            }
        }
    }
    Ok(())
}

// --------------------------------------------------------------------------
// Tree -> model plan
// --------------------------------------------------------------------------

type DatesByElement = HashMap<String, Vec<String>>;

/// Every age-bearing term in this subtree: its dates and its boundaries.
///
/// An interpolated level counts as a date. It is a free calendar parameter
/// with a flat likelihood, so every ordering rule that applies to a date
/// applies to it -- and leaving it out would let a Sequence order the
/// deposition sequence around it while the level itself floated free.
fn subtree_terms(element: &Element, dates_by_element: &DatesByElement) -> Vec<Term> {
    let mut terms = Vec::new();
    for node in iter_elements(element) {
        if node.is("boundary") {
            terms.push((TermKind::Boundary, node.id.clone()));
        } else if node.is("interpolated_depth") {
            terms.push((TermKind::Date, node.id.clone()));
        }
        for date_id in dates_by_element.get(&node.id).into_iter().flatten() {
            terms.push((TermKind::Date, date_id.clone()));
        }
    }
    terms
}

/// What an element hands up to whatever encloses it: parameters that are a
/// UNIFORM scatter between the enclosing boundaries, and parameters that must
/// merely be CONTAINED by them because their spacing is already priced
/// elsewhere. See [`SpanGroup`].
type Unclaimed = (Vec<String>, Vec<String>);

struct Planner<'a> {
    dates_by_element: DatesByElement,
    measurements_by_id: HashMap<&'a str, &'a Measurement>,
    ordering: Vec<Ordering>,
    span_groups: Vec<SpanGroup>,
    wiggle_groups: Vec<WiggleGroup>,
    deposition_groups: Vec<DepositionGroup>,
}

impl Planner<'_> {
    /// Plan one element. Returns the parameters in this subtree not yet
    /// enclosed by any boundary pair, as (uniform, contained), so they bubble up
    /// to an enclosing group -- or, unclaimed, get no uniform-scatter term,
    /// matching OxCal, where a date outside every boundary is unconstrained by
    /// a phase prior.
    fn plan(&mut self, element: &Element) -> Unclaimed {
        let etype = element.element_type.as_str();
        match etype {
            "boundary" | "hiatus" => return (vec![], vec![]),
            // Its own deposition sequence records it and orders it. It hands
            // itself up as CONTAINED so an enclosing Boundary pair still pens
            // it in without pricing its spacing a second time.
            "interpolated_depth" => return (vec![], vec![element.id.clone()]),
            "wiggle_sequence" => {
                let group = self.wiggle_group(element);
                let ids = group.date_ids();
                self.wiggle_groups.push(group);
                // Ordered among themselves by construction -- the gaps ARE
                // the ordering -- so no ordering constraints are emitted for
                // them. Their spacing is fixed, so they are contained rather
                // than uniform.
                return (vec![], ids);
            }
            "deposition_sequence" => {
                let group = self.deposition_group(element);
                // Deeper is older, and the deposition prior is written over
                // ADJACENT levels, so the ordering is emitted pairwise here
                // rather than left to a Sequence that is not there.
                let ids = group.parameter_ids();
                for pair in ids.windows(2) {
                    self.ordering.push((
                        vec![(TermKind::Date, pair[0].clone())],
                        vec![(TermKind::Date, pair[1].clone())],
                    ));
                }
                self.deposition_groups.push(group);
                return (vec![], ids);
            }
            _ => {}
        }

        // Plan children first so nested groups claim their own dates before
        // an outer group gets a chance to.
        let children = &element.children;
        let unclaimed_by_child: Vec<Unclaimed> = children.iter().map(|c| self.plan(c)).collect();
        let own_dates = self
            .dates_by_element
            .get(&element.id)
            .cloned()
            .unwrap_or_default();

        if etype == "phase" {
            // Unordered: no constraints between children. Everything bubbles up.
            let mut uniform = own_dates;
            let mut contained = Vec::new();
            for (u, c) in unclaimed_by_child {
                uniform.extend(u);
                contained.extend(c);
            }
            return (uniform, contained);
        }

        // sequence: children are ordered oldest -> youngest. A child with no
        // terms -- an undated Phase between two Boundaries, say -- is skipped
        // over rather than breaking the chain: ordering only its neighbours
        // against it would leave the Boundaries either side of it unordered,
        // splitting one sequence into two independent halves.
        let child_terms: Vec<Vec<Term>> = children
            .iter()
            .map(|c| subtree_terms(c, &self.dates_by_element))
            .filter(|t| !t.is_empty())
            .collect();
        for pair in child_terms.windows(2) {
            self.ordering.push((pair[0].clone(), pair[1].clone()));
        }

        // Bounded groups: dates between each consecutive pair of Boundary
        // children.
        let boundary_positions: Vec<usize> = children
            .iter()
            .enumerate()
            .filter(|(_, c)| c.is("boundary"))
            .map(|(i, _)| i)
            .collect();
        let mut claimed: HashSet<String> = HashSet::new();
        for pair in boundary_positions.windows(2) {
            let (older_idx, younger_idx) = (pair[0], pair[1]);
            let mut enclosed_uniform = Vec::new();
            let mut enclosed_contained = Vec::new();
            for unclaimed in &unclaimed_by_child[older_idx + 1..younger_idx] {
                enclosed_uniform.extend(unclaimed.0.iter().cloned());
                enclosed_contained.extend(unclaimed.1.iter().cloned());
            }
            claimed.extend(enclosed_uniform.iter().cloned());
            claimed.extend(enclosed_contained.iter().cloned());
            self.span_groups.push(SpanGroup {
                older_boundary_id: children[older_idx].id.clone(),
                younger_boundary_id: children[younger_idx].id.clone(),
                date_ids: enclosed_uniform,
                contained_ids: enclosed_contained,
            });
        }

        let mut uniform = own_dates;
        let mut contained = Vec::new();
        for (u, c) in unclaimed_by_child {
            uniform.extend(u.into_iter().filter(|d| !claimed.contains(d)));
            contained.extend(c.into_iter().filter(|d| !claimed.contains(d)));
        }
        (uniform, contained)
    }

    /// One wiggle_sequence as a [`WiggleGroup`], members ordered YOUNGEST
    /// FIRST.
    ///
    /// Ascending offset is youngest first, because an offset counts years
    /// BEFORE the reference: 0 at the reference, increasing into the past. A
    /// missing offset reads as zero here rather than raising --
    /// [`validate_ordinates`] is where that is refused, and it can say which
    /// sample and why.
    fn wiggle_group(&self, element: &Element) -> WiggleGroup {
        let params = element_params(element);
        let mut members: Vec<(String, f64, f64)> = self
            .dates_by_element
            .get(&element.id)
            .into_iter()
            .flatten()
            .map(|date_id| {
                let m = self.measurements_by_id.get(date_id.as_str());
                (
                    date_id.clone(),
                    m.and_then(|m| m.ring_offset).unwrap_or(0.0),
                    m.and_then(|m| m.ring_offset_error).unwrap_or(0.0),
                )
            })
            .collect();
        members.sort_by(|a, b| a.1.total_cmp(&b.1));
        let gap_units = param(&params, "gap_units");
        WiggleGroup {
            element_id: element.id.clone(),
            name: element_label(element),
            members,
            gap_units: if gap_units.truthy() {
                gap_units.as_str().unwrap_or("rings").to_owned()
            } else {
                "rings".to_owned()
            },
            reference_sample_id: param(&params, "reference_sample_id")
                .as_str()
                .map(str::to_owned),
        }
    }

    /// One deposition_sequence as a [`DepositionGroup`], levels ordered OLDEST
    /// FIRST.
    ///
    /// Descending depth is oldest first: deeper is older, which is the same
    /// convention `position` uses for a Sequence's children and the Harris
    /// matrix uses for its rows. Undated levels take their depth from their own
    /// params.
    fn deposition_group(&self, element: &Element) -> DepositionGroup {
        let params = element_params(element);
        let mut levels: Vec<(String, f64, bool)> = Vec::new();
        let mut level_names = HashMap::new();
        for date_id in self.dates_by_element.get(&element.id).into_iter().flatten() {
            let depth = self
                .measurements_by_id
                .get(date_id.as_str())
                .and_then(|m| m.depth_cm)
                .unwrap_or(0.0);
            levels.push((date_id.clone(), depth, true));
        }
        for child in &element.children {
            if !child.is("interpolated_depth") {
                continue;
            }
            let depth = param(&element_params(child), "depth_cm")
                .as_f64()
                .unwrap_or(0.0);
            levels.push((child.id.clone(), depth, false));
            if let Some(name) = child.given_name() {
                level_names.insert(child.id.clone(), name.to_owned());
            }
        }
        levels.sort_by(|a, b| (-a.1).total_cmp(&-b.1));

        // A fixed k only when the model asked for one: `k_variable` is what
        // decides, so a value left behind by an earlier edit cannot quietly pin
        // the rigidity of a run that meant to infer it.
        let k = param(&params, "k").as_f64();
        let variable = param(&params, "k_variable").truthy() || k.is_none();
        let depth_units = param(&params, "depth_units");
        DepositionGroup {
            element_id: element.id.clone(),
            name: element_label(element),
            levels,
            k: if variable { None } else { k },
            k_variable: variable,
            log_k_min: param(&params, "log_k_min").as_f64().unwrap_or(-2.0),
            log_k_max: param(&params, "log_k_max").as_f64().unwrap_or(2.0),
            depth_units: if depth_units.truthy() {
                depth_units.as_str().unwrap_or("cm").to_owned()
            } else {
                "cm".to_owned()
            },
            level_names,
        }
    }
}

/// Split boundaries into those that can be integrated out analytically and
/// those that must be sampled.
///
/// Only the OUTERMOST boundaries are heavy-tailed: an interior one is squeezed
/// between the youngest date of the group before it and the oldest of the
/// group after, while an outermost one extends into a region with no data at
/// all. Appearing in only ONE span term, an outermost boundary also integrates
/// out in closed form.
///
/// "Outermost" is structural, not positional: a boundary is outermost-old if
/// nothing in the model must be older than it, outermost-young if nothing must
/// be younger. That covers nested and branching models, where the extreme
/// boundaries need not be the root sequence's first and last children.
pub fn classify_boundaries(
    boundary_ids: &[String],
    span_groups: &[SpanGroup],
    ordering: &[Ordering],
) -> (BTreeSet<String>, Vec<String>) {
    let mut has_something_older: HashSet<&str> = HashSet::new();
    let mut has_something_younger: HashSet<&str> = HashSet::new();
    for (older_terms, younger_terms) in ordering {
        for (kind, tid) in younger_terms {
            if *kind == TermKind::Boundary {
                has_something_older.insert(tid);
            }
        }
        for (kind, tid) in older_terms {
            if *kind == TermKind::Boundary {
                has_something_younger.insert(tid);
            }
        }
    }

    // Only integrable out when it appears in exactly one span term; otherwise
    // it couples two groups and the integral stops factorising.
    let mut appearances: HashMap<&str, usize> = HashMap::new();
    for group in span_groups {
        *appearances.entry(&group.older_boundary_id).or_default() += 1;
        *appearances.entry(&group.younger_boundary_id).or_default() += 1;
    }

    let mut marginalized = BTreeSet::new();
    for bid in boundary_ids {
        if appearances.get(bid.as_str()).copied().unwrap_or(0) != 1 {
            continue;
        }
        if !has_something_older.contains(bid.as_str())
            || !has_something_younger.contains(bid.as_str())
        {
            marginalized.insert(bid.clone());
        }
    }
    let sampled = boundary_ids
        .iter()
        .filter(|b| !marginalized.contains(*b))
        .cloned()
        .collect();
    (marginalized, sampled)
}

/// (taq, tpq): a minimum and a maximum calendar age, cal BP, either None for
/// unbounded.
pub type Limits = (Option<f64>, Option<f64>);

/// The effective (taq, tpq) limiters for every element, combining its own with
/// every ancestor's, tightest winning -- [`stratigraphy::effective_bounds`]'s
/// rule at arbitrary nesting depth. Either bound may be None, meaning
/// unbounded.
pub fn inherited_bounds(root: &Element) -> HashMap<String, Limits> {
    fn walk(
        element: &Element,
        taq: Option<f64>,
        tpq: Option<f64>,
        out: &mut HashMap<String, Limits>,
    ) {
        // taq is a minimum age, so the tightest is the largest; tpq is a
        // maximum age, so the tightest is the smallest.
        let combined_taq = match (taq, element.taq_age_bp) {
            (None, own) => own,
            (Some(t), None) => Some(t),
            (Some(t), Some(own)) => Some(py_max(t, own)),
        };
        let combined_tpq = match (tpq, element.tpq_age_bp) {
            (None, own) => own,
            (Some(t), None) => Some(t),
            (Some(t), Some(own)) => Some(py_min(t, own)),
        };
        out.insert(element.id.clone(), (combined_taq, combined_tpq));
        for child in &element.children {
            walk(child, combined_taq, combined_tpq, out);
        }
    }
    let mut out = HashMap::new();
    walk(root, None, None, &mut out);
    out
}

/// Turn a validated model tree into ordering constraints, bounded groups and
/// a boundary classification. Pure structure, directly unit-testable without
/// a solver.
pub fn plan_model(root: &Element, measurements: &[Measurement]) -> ModelPlan {
    let mut dates_by_element: DatesByElement = HashMap::new();
    for m in measurements {
        if let Some(e) = m.element_id.as_deref().filter(|e| !e.is_empty()) {
            dates_by_element
                .entry(e.to_owned())
                .or_default()
                .push(m.id.clone());
        }
    }
    let mut planner = Planner {
        dates_by_element,
        measurements_by_id: measurements.iter().map(|m| (m.id.as_str(), m)).collect(),
        ordering: Vec::new(),
        span_groups: Vec::new(),
        wiggle_groups: Vec::new(),
        deposition_groups: Vec::new(),
    };
    planner.plan(root);

    let boundaries: Vec<&Element> = iter_elements(root)
        .into_iter()
        .filter(|e| e.is("boundary"))
        .collect();
    let boundary_ids: Vec<String> = boundaries.iter().map(|e| e.id.clone()).collect();
    let mut boundary_names: Vec<(String, String)> = Vec::new();
    for e in &boundaries {
        let name = e.given_name().unwrap_or("Boundary").to_owned();
        match boundary_names.iter_mut().find(|(id, _)| *id == e.id) {
            Some(entry) => entry.1 = name,
            None => boundary_names.push((e.id.clone(), name)),
        }
    }
    let (marginalized, sampled) =
        classify_boundaries(&boundary_ids, &planner.span_groups, &planner.ordering);

    ModelPlan {
        ordering: planner.ordering,
        span_groups: planner.span_groups,
        marginalized_boundaries: marginalized,
        sampled_boundaries: sampled,
        boundary_names,
        wiggle_groups: planner.wiggle_groups,
        deposition_groups: planner.deposition_groups,
    }
}

// --------------------------------------------------------------------------
// Finding the ordinate-bearing sequences
// --------------------------------------------------------------------------

/// Every element of one type, in depth-first order.
pub fn elements_of_type<'a>(root: Option<&'a Element>, etype: &str) -> Vec<&'a Element> {
    root.map(|r| {
        iter_elements(r)
            .into_iter()
            .filter(|e| e.is(etype))
            .collect()
    })
    .unwrap_or_default()
}

/// The single wiggle_sequence this model consists of, or None.
///
/// "Consists of" is deliberately strict: exactly one wiggle sequence, every
/// date in it, and nothing else in the tree carrying an age -- no Boundary,
/// no second phase with dates in it. Anything looser would be a model whose
/// OTHER parameters the closed-form solver has no way to report, and the
/// right answer to that is to say so rather than to solve part of it.
///
/// A Sequence or Phase wrapping one wiggle sequence and nothing else still
/// counts: the wrapper adds no ordering and no parameter of its own, and both
/// the New Analysis preset and the OxCal importer build exactly that shape.
pub fn standalone_wiggle_match<'a>(
    root: Option<&'a Element>,
    measurements: &[Measurement],
) -> Option<&'a Element> {
    let root = root?;
    let wiggles = elements_of_type(Some(root), "wiggle_sequence");
    if wiggles.len() != 1 {
        return None;
    }
    let only = wiggles[0];
    if iter_elements(root).iter().any(|e| {
        ["boundary", "deposition_sequence", "interpolated_depth"].contains(&e.element_type.as_str())
    }) {
        return None;
    }
    if measurements
        .iter()
        .any(|m| m.element_id.as_deref() != Some(only.id.as_str()))
    {
        return None;
    }
    Some(only)
}

// --------------------------------------------------------------------------
// Presentation order
// --------------------------------------------------------------------------

/// One row of [`flatten_structure`]'s reading of a model.
#[derive(Debug, Clone, PartialEq)]
pub struct FlatRow {
    /// An element type, `date`, or `unassigned`.
    pub kind: String,
    pub element_id: Option<String>,
    pub name: String,
    pub depth: usize,
    pub path: String,
    pub tpq_age_bp: Option<f64>,
    pub taq_age_bp: Option<f64>,
    /// The date on a date row, as an index into the measurements given.
    pub sample: Option<usize>,
}

/// Flatten a model tree into the order it should be READ in: depth first,
/// siblings oldest first, each phase's own dates before any branch nested
/// inside it.
///
/// The single definition of "model order" behind the results table and every
/// export, so a date cannot be filed under one phase on screen and another in
/// the CSV.
///
/// A date is placed by element_id, falling back to group_id for a migrated
/// tree where each phase reuses its context's id; anything resolving to
/// neither is collected under a trailing "Unassigned" heading rather than
/// dropped. No structure at all is not the same as dates missing from a
/// structure: it gives no rows, and callers fall back to a flat list.
///
/// Python's rows also carry each element's stored `posterior`; that belongs to
/// the store, which attaches it by `element_id`.
pub fn flatten_structure(root: Option<&Element>, measurements: &[Measurement]) -> Vec<FlatRow> {
    let mut rows = Vec::new();
    let Some(root) = root else {
        return rows;
    };
    let mut dates_by_element: HashMap<&str, Vec<usize>> = HashMap::new();
    for (i, m) in measurements.iter().enumerate() {
        let key = m
            .element_id
            .as_deref()
            .filter(|e| !e.is_empty())
            .or(m.group_id.as_deref().filter(|g| !g.is_empty()));
        if let Some(key) = key {
            dates_by_element.entry(key).or_default().push(i);
        }
    }
    let mut placed: HashSet<&str> = HashSet::new();

    fn date_row(
        m: &Measurement,
        index: usize,
        depth: usize,
        path: &[String],
        element_id: Option<&str>,
    ) -> FlatRow {
        FlatRow {
            kind: "date".into(),
            element_id: element_id.map(str::to_owned),
            name: m.label().to_owned(),
            depth,
            path: path.join(" > "),
            tpq_age_bp: m.tpq_age_bp,
            taq_age_bp: m.taq_age_bp,
            sample: Some(index),
        }
    }

    let mut stack: Vec<(&Element, usize, Vec<String>)> = vec![(root, 0, Vec::new())];
    while let Some((element, depth, ancestors)) = stack.pop() {
        let name = element_label(element);
        let mut path = ancestors;
        path.push(name.clone());
        rows.push(FlatRow {
            kind: element.element_type.clone(),
            element_id: Some(element.id.clone()),
            name,
            depth,
            path: path.join(" > "),
            tpq_age_bp: element.tpq_age_bp,
            taq_age_bp: element.taq_age_bp,
            sample: None,
        });
        for &i in dates_by_element
            .get(element.id.as_str())
            .into_iter()
            .flatten()
        {
            placed.insert(&measurements[i].id);
            rows.push(date_row(
                &measurements[i],
                i,
                depth + 1,
                &path,
                Some(&element.id),
            ));
        }
        for child in element.children.iter().rev() {
            stack.push((child, depth + 1, path.clone()));
        }
    }

    let orphans: Vec<usize> = (0..measurements.len())
        .filter(|&i| !placed.contains(measurements[i].id.as_str()))
        .collect();
    if !orphans.is_empty() {
        rows.push(FlatRow {
            kind: "unassigned".into(),
            element_id: None,
            name: "Unassigned".into(),
            depth: 0,
            path: "Unassigned".into(),
            tpq_age_bp: None,
            taq_age_bp: None,
            sample: None,
        });
        let path = vec!["Unassigned".to_owned()];
        for i in orphans {
            rows.push(date_row(&measurements[i], i, 1, &path, None));
        }
    }
    rows
}

// --------------------------------------------------------------------------
// Migration from contexts + pairwise relationships
// --------------------------------------------------------------------------

/// What [`migrate_from_relationships`] builds.
#[derive(Debug, Clone, PartialEq)]
pub struct Migrated {
    pub root: Element,
    /// True when the relationship graph could not be expressed as a tree and
    /// had to be flattened into one order; callers should surface that, since
    /// a linearization is strictly MORE constrained than the graph it came
    /// from.
    pub linearized: bool,
    /// Each context id to the (start, end) boundaries now bracketing it, so
    /// results can still be reported per context.
    pub group_boundaries: Vec<(String, (String, String))>,
}

fn new_element(id: String, element_type: &str, name: String) -> Element {
    Element {
        id,
        element_type: element_type.to_owned(),
        name: Some(name),
        ..Element::default()
    }
}

/// Build an equivalent model tree from the contexts + relationships
/// representation, so saved analyses keep working. `new_id` names each
/// element it adds.
pub fn migrate_from_relationships(
    measurements: &[Measurement],
    groups: &[Group],
    relationships: &[Relationship],
    mut new_id: impl FnMut() -> String,
) -> Migrated {
    let members = stratigraphy::group_members(measurements, groups);
    let group_ids: Vec<&str> = groups.iter().map(|g| g.id.as_str()).collect();

    // Edges are between individual samples; lift them to their contexts.
    let (older_younger, _equal) =
        stratigraphy::expand_relationships(measurements, groups, relationships);
    let mut context_of: HashMap<&str, &str> = HashMap::new();
    for (gid, member_ids) in &members {
        for mid in member_ids {
            context_of.insert(mid, gid);
        }
    }
    let mut context_edges: BTreeSet<(&str, &str)> = BTreeSet::new();
    for (older, younger) in &older_younger {
        if let (Some(&a), Some(&b)) = (
            context_of.get(older.as_str()),
            context_of.get(younger.as_str()),
        ) && a != b
        {
            context_edges.insert((a, b));
        }
    }

    // Topological order, oldest first, with ties broken by the order the
    // CALLER gave the contexts in.
    //
    // That tie-break is the whole of this paragraph's subtlety. Sorting the
    // queue by context id instead makes the result deterministic and
    // meaningless: ids are random uuids, so a deploy with no relationships yet
    // -- where every context has indegree zero and the tie-break decides
    // everything -- came out in uuid order. Eight sites deployed west to east
    // arrived as a Sequence claiming 5 before 3 before 4, which is not merely
    // untidy: a Sequence ASSERTS that order, so the model contradicted its own
    // dates and the solver refused it as unsolvable.
    //
    // The caller's order is the one the analyst chose -- the deploy carries
    // the collection's order into `groups`, and the collection is arranged
    // deliberately. Using it keeps the sort just as deterministic.
    let mut rank: HashMap<&str, usize> = HashMap::new();
    for (index, gid) in group_ids.iter().enumerate() {
        rank.insert(gid, index);
    }
    let rank_of = |gid: &str| rank.get(gid).copied().unwrap_or(rank.len());
    let mut indeg: HashMap<&str, i64> = group_ids.iter().map(|&g| (g, 0)).collect();
    let mut adj: HashMap<&str, Vec<&str>> = HashMap::new();
    for &(a, b) in &context_edges {
        adj.entry(a).or_default().push(b);
        *indeg.entry(b).or_insert(0) += 1;
    }

    let mut ordered: Vec<&str> = Vec::new();
    let mut queue: Vec<&str> = group_ids
        .iter()
        .copied()
        .filter(|g| indeg.get(g).copied().unwrap_or(0) == 0)
        .collect();
    queue.sort_by_key(|g| rank_of(g));
    while !queue.is_empty() {
        let node = queue.remove(0);
        ordered.push(node);
        let mut next: Vec<&str> = adj.get(node).cloned().unwrap_or_default();
        next.sort_by_key(|g| rank_of(g));
        for n in next {
            let d = indeg.entry(n).or_insert(0);
            *d -= 1;
            if *d == 0 {
                queue.push(n);
            }
        }
        // Re-sorted because a node released later can rank before one already
        // waiting: a plain append would let arrival time outrank the
        // analyst's order.
        queue.sort_by_key(|g| rank_of(g));
    }
    // Anything left was in a cycle; append it so no context is silently lost.
    let leftover: Vec<&str> = group_ids
        .iter()
        .copied()
        .filter(|g| !ordered.contains(g))
        .collect();
    ordered.extend(leftover.iter().copied());

    // Was the ordering forced, or an arbitrary linearization of a branching
    // set? Consecutive contexts not related by an edge mean constraints were
    // added that the original did not have.
    let linearized = !leftover.is_empty()
        || ordered
            .windows(2)
            .any(|w| !context_edges.contains(&(w[0], w[1])));

    let start_id = new_id();
    let mut children = vec![new_element(
        start_id.clone(),
        "boundary",
        "Start of sequence".into(),
    )];
    let mut group_boundaries = Vec::new();
    let mut previous_boundary = start_id;
    for gid in ordered {
        let group = groups.iter().find(|g| g.id == gid);
        let name = group
            .and_then(|g| g.name.as_deref())
            .filter(|n| !n.is_empty());
        children.push(Element {
            id: gid.to_owned(),
            element_type: "phase".into(),
            name: Some(name.unwrap_or("Context").to_owned()),
            tpq_age_bp: group.and_then(|g| g.tpq_age_bp),
            taq_age_bp: group.and_then(|g| g.taq_age_bp),
            ..Element::default()
        });
        let end_id = new_id();
        children.push(new_element(
            end_id.clone(),
            "boundary",
            format!("End of {}", name.unwrap_or("context")),
        ));
        group_boundaries.push((gid.to_owned(), (previous_boundary, end_id.clone())));
        previous_boundary = end_id;
    }
    if children.len() > 1 {
        let last = children.len() - 1;
        children[last].name = Some("End of sequence".into());
    }
    let root = Element {
        id: new_id(),
        element_type: "sequence".into(),
        name: Some("Site sequence".into()),
        children,
        ..Element::default()
    };
    Migrated {
        root,
        linearized,
        group_boundaries,
    }
}
