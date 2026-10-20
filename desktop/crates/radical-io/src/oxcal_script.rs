//! Reading OxCal's own model files (.oxcal, the CQL2 language) into the
//! app's model tree. Port of `services/oxcal_script.py`.
//!
//! THE RULE THIS MODULE IS BUILT AROUND: a construct that would change the
//! posterior and cannot be honoured is an ERROR, never a warning or a silent
//! omission. OxCal's language is much larger than what this app models, and
//! reading any of the rest as the nearest thing we do implement would produce
//! a result that looks right, carries no warning, and is wrong. Constructs
//! that only affect what OxCal PLOTS are skipped with a note.

use std::collections::{BTreeMap, HashMap};
use std::sync::LazyLock;

use radical_core::measurement::Measurement;
use radical_core::model_tree::{self, Element, Param, Params};
use radical_core::pyfmt::{g, repr};
use radical_core::statistics::{self, SameEvent};
use radical_core::{Error, Mode};
use regex::Regex;
use serde_json::{Value, json};

use crate::date_import::{self, Date, ImportResult, MAX_DATES, Source};
use crate::ids::Ids;
use crate::library::Library;
use crate::python;

/// `f'{x:g}'`.
fn gf(x: f64) -> String {
    g(x, 6)
}

/// Python's `round(x, 1)`: correctly rounded, ties to even, as Rust's
/// formatting is.
fn round1(x: f64) -> f64 {
    format!("{x:.1}").parse().unwrap()
}

// --------------------------------------------------------------------------
// Tokenizer
// --------------------------------------------------------------------------

/// A file that is not valid CQL2, reported with a line number.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SyntaxError(pub String);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Kind {
    Str,
    Number,
    Name,
    Punct,
}

#[derive(Debug, Clone)]
struct Token {
    kind: Kind,
    value: String,
    line: usize,
}

// Python's `\s` also counts the separators 0x1C-0x1F.
static TOKEN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"(?s)^(?:(?P<space>[\s\x1c-\x1f]+)|(?P<line_comment>//[^\n]*)|(?P<block_comment>/\*.*?\*/)|(?P<string>"(?:[^"\\]|\\.)*")|(?P<number>[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)|(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<punct>[(){},;=]))"#,
    )
    .unwrap()
});

fn tokenize(text: &str) -> Result<Vec<Token>, SyntaxError> {
    let mut tokens = Vec::new();
    let mut position = 0;
    let mut line = 1;
    while position < text.len() {
        let rest = &text[position..];
        let Some(caps) = TOKEN.captures(rest) else {
            let snippet: String = rest.chars().take(20).collect();
            let snippet = snippet.split('\n').next().unwrap();
            return Err(SyntaxError(format!(
                "Line {line}: cannot read '{snippet}' -- this does not look like an OxCal model file."
            )));
        };
        let value = caps.get(0).unwrap().as_str();
        let kind = [
            ("string", Kind::Str),
            ("number", Kind::Number),
            ("name", Kind::Name),
            ("punct", Kind::Punct),
        ]
        .into_iter()
        .find(|(group, _)| caps.name(group).is_some())
        .map(|(_, kind)| kind);
        if let Some(kind) = kind {
            tokens.push(Token {
                kind,
                value: value.to_owned(),
                line,
            });
        }
        line += value.matches('\n').count();
        position += value.len();
    }
    Ok(tokens)
}

// --------------------------------------------------------------------------
// Parser
// --------------------------------------------------------------------------

/// One argument: a quoted string, a bare name, a number, or a nested call.
#[derive(Debug, Clone, PartialEq)]
pub enum Arg {
    Str(String),
    Name(String),
    Number(f64),
    Call(Call),
}

/// A nested function argument -- T(5), U(0,4), Exp(1,-10,0).
#[derive(Debug, Clone, PartialEq)]
pub struct Call {
    pub name: String,
    pub args: Vec<Arg>,
}

impl Arg {
    /// Python's `str(arg)`.
    pub fn text(&self) -> String {
        match self {
            Self::Str(s) | Self::Name(s) => s.clone(),
            Self::Number(x) => repr(*x),
            Self::Call(c) => c.text(),
        }
    }

    fn repr(&self) -> String {
        match self {
            Self::Str(s) | Self::Name(s) => python::repr_str(s),
            Self::Number(x) => repr(*x),
            Self::Call(c) => c.text(),
        }
    }

    /// Python's `float(arg)`, None where it raises.
    fn float(&self) -> Option<f64> {
        match self {
            Self::Number(x) => Some(*x),
            Self::Str(s) | Self::Name(s) => python::float(s),
            Self::Call(_) => None,
        }
    }
}

impl Call {
    fn text(&self) -> String {
        let inner: Vec<String> = self.args.iter().map(Arg::repr).collect();
        format!("{}({})", self.name, inner.join(","))
    }
}

/// One CQL2 statement: a command, its arguments, and its block if it has
/// one. An assignment `Resolution=1;` is command `=` with the name and value
/// as arguments.
#[derive(Debug, Clone, PartialEq)]
pub struct Node {
    pub command: String,
    pub args: Vec<Arg>,
    pub block: Option<Vec<Node>>,
    pub line: usize,
}

impl Node {
    /// The first string argument, the label in every command taking one.
    pub fn name(&self) -> Option<&str> {
        self.args.iter().find_map(|a| match a {
            Arg::Str(s) | Arg::Name(s) => Some(s.as_str()),
            _ => None,
        })
    }

    /// `node.name or default`: an empty label counts as none.
    fn name_or(&self, default: impl FnOnce() -> String) -> String {
        self.name()
            .filter(|n| !n.is_empty())
            .map_or_else(default, str::to_owned)
    }

    pub fn numbers(&self) -> Vec<f64> {
        self.args
            .iter()
            .filter_map(|a| match a {
                Arg::Number(x) => Some(*x),
                _ => None,
            })
            .collect()
    }

    fn block(&self) -> &[Node] {
        self.block.as_deref().unwrap_or(&[])
    }

    fn lower(&self) -> String {
        self.command.to_lowercase()
    }
}

struct Parser {
    tokens: Vec<Token>,
    position: usize,
}

impl Parser {
    fn peek(&self) -> Option<&Token> {
        self.tokens.get(self.position)
    }

    fn peek_is(&self, value: &str) -> bool {
        self.peek().is_some_and(|t| t.value == value)
    }

    /// The next token. The Python indexes past the end here and raises
    /// IndexError, which nothing catches; a truncated file is a syntax error.
    fn next(&mut self) -> Result<Token, SyntaxError> {
        let token =
            self.tokens.get(self.position).cloned().ok_or_else(|| {
                SyntaxError("The file ends where a value was expected.".to_owned())
            })?;
        self.position += 1;
        Ok(token)
    }

    fn expect(&mut self, value: &str) -> Result<Token, SyntaxError> {
        match self.peek() {
            None => Err(SyntaxError(format!(
                "The file ends where '{value}' was expected."
            ))),
            Some(t) if t.value != value => Err(SyntaxError(format!(
                "Line {}: expected '{value}' but found '{}'.",
                t.line, t.value
            ))),
            Some(_) => self.next(),
        }
    }

    fn program(&mut self) -> Result<Vec<Node>, SyntaxError> {
        let mut nodes = Vec::new();
        while let Some(token) = self.peek() {
            if token.value == ";" {
                self.position += 1;
                continue;
            }
            if token.value == "}" {
                break;
            }
            nodes.push(self.statement()?);
        }
        Ok(nodes)
    }

    fn statement(&mut self) -> Result<Node, SyntaxError> {
        let token = self.next()?;
        if token.kind != Kind::Name {
            return Err(SyntaxError(format!(
                "Line {}: expected a command but found '{}'.",
                token.line, token.value
            )));
        }
        let (command, line) = (token.value, token.line);
        if self.peek_is("=") {
            self.position += 1;
            let value = self.value()?;
            if self.peek_is(";") {
                self.position += 1;
            }
            return Ok(Node {
                command: "=".to_owned(),
                args: vec![Arg::Name(command), value],
                block: None,
                line,
            });
        }
        let args = if self.peek_is("(") {
            self.args()?
        } else {
            Vec::new()
        };
        let mut block = None;
        if self.peek_is("{") {
            self.position += 1;
            block = Some(self.program()?);
            self.expect("}")?;
        }
        if self.peek_is(";") {
            self.position += 1;
        }
        Ok(Node {
            command,
            args,
            block,
            line,
        })
    }

    fn args(&mut self) -> Result<Vec<Arg>, SyntaxError> {
        self.expect("(")?;
        let mut args = Vec::new();
        if self.peek_is(")") {
            self.position += 1;
            return Ok(args);
        }
        loop {
            args.push(self.value()?);
            match self.peek() {
                None => {
                    return Err(SyntaxError(
                        "The file ends inside an argument list.".to_owned(),
                    ));
                }
                Some(t) if t.value == "," => {
                    self.position += 1;
                    // An argument OxCal left out: `Delta_R("=LocalMarine", )`.
                    if self.peek_is(")") {
                        self.position += 1;
                        return Ok(args);
                    }
                }
                Some(_) => {
                    self.expect(")")?;
                    return Ok(args);
                }
            }
        }
    }

    fn value(&mut self) -> Result<Arg, SyntaxError> {
        let token = self.next()?;
        match token.kind {
            Kind::Str => Ok(Arg::Str(unquote(&token.value))),
            Kind::Number => Ok(Arg::Number(
                python::float(&token.value).expect("the token is a number"),
            )),
            Kind::Name if self.peek_is("(") => Ok(Arg::Call(Call {
                name: token.value,
                args: self.args()?,
            })),
            Kind::Name => Ok(Arg::Name(token.value)),
            Kind::Punct => Err(SyntaxError(format!(
                "Line {}: expected a value but found '{}'.",
                token.line, token.value
            ))),
        }
    }
}

fn unquote(raw: &str) -> String {
    raw[1..raw.len() - 1]
        .replace("\\\"", "\"")
        .replace("\\\\", "\\")
}

/// Tokenize and parse, with no interpretation of what the commands mean.
pub fn parse_script(text: &str) -> Result<Vec<Node>, SyntaxError> {
    Parser {
        tokens: tokenize(text)?,
        position: 0,
    }
    .program()
}

// --------------------------------------------------------------------------
// Command classification
// --------------------------------------------------------------------------

/// Containers contributing no element of their own.
const TRANSPARENT: [&str; 2] = ["plot", "page"];
const STRUCTURAL: [&str; 4] = ["sequence", "phase", "boundary", "hiatus"];
/// Sequences whose members are separated by measured gaps.
const ORDINATE_SEQUENCES: [&str; 2] = ["d_sequence", "v_sequence"];
const DEPOSITION_SEQUENCE: &str = "p_sequence";
const DATE_COMMANDS: [&str; 2] = ["r_date", "r_simulate"];
const COMBINE_COMMAND: &str = "r_combine";

/// Queries and plots: they read a posterior rather than shape one.
const IGNORABLE: [&str; 24] = [
    "sum",
    "kde_plot",
    "kde_model",
    "span",
    "interval",
    "difference",
    "shift",
    "first",
    "last",
    "order",
    "correl_matrix",
    "correlation",
    "mcmc_sample",
    "label",
    "axis",
    "number",
    "mean",
    "median",
    "sigma",
    "prior",
    "posterior",
    "display",
    "plot_axis",
    "sample",
];

/// Assignments setting how OxCal DRAWS something.
const DISPLAY_ATTRIBUTES: [&str; 2] = ["color", "colour"];

/// Constructs this app has no equivalent for, each with what reading it as
/// something we DO implement would cost.
fn unsupported(command: &str) -> Option<&'static str> {
    Some(match command {
        "tau_boundary" => {
            "an exponentially-distributed transition. This app models a Boundary as a uniform transition, and reading a Tau_Boundary as one would change the posterior without saying so."
        }
        "sigma_boundary" => {
            "a normally-distributed transition. This app models a Boundary as a uniform transition."
        }
        "zero_boundary" => {
            "a transition with a density that falls to zero. This app models a Boundary as a uniform transition."
        }
        "trapezium" | "trapezoid" => "a trapezoidal transition, which this app does not model.",
        "c_date" => {
            "a calendar-age likelihood. Every date in this app is a radiocarbon measurement calibrated through a curve; there is nowhere to put a date that is already calendrical. A fixed calendar limit can be entered as a TPQ or TAQ instead."
        }
        "date" => {
            "an undated event parameter outside a deposition sequence. This app models an undated level only where a P_Sequence gives it a depth to sit at; elsewhere there is nothing to bound it."
        }
        "interpolate" => {
            "an interpolated age. Ask for one by putting a Date() with its own z= inside the P_Sequence, which is what this app imports as an undated level."
        }
        "age" => "a relative-age parameter, which this app does not model.",
        "combine" => "a combination of likelihoods, which this app does not model.",
        "u_date" => "a uranium-series date, which this app does not calibrate.",
        "tl_date" => "a luminescence date, which this app does not calibrate.",
        "after" | "before" => {
            "an open-ended ordering constraint. Express the ordering with a Sequence, or the limit as a TPQ/TAQ."
        }
        "u_sequence" => {
            "a uniform-deposition sequence. This app models the Poisson-process deposition of a P_Sequence, which is a different prior over the same depths -- reading one as the other would change the posterior without saying so."
        }
        _ => return None,
    })
}

static NOT_ALNUM: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^a-z0-9]+").unwrap());

/// A curve name reduced to its comparable core, so `Curve("=Mixed Diet")`
/// finds `Mix_Curves("mixed diet", ...)`.
fn normalise_curve_name(raw: &str) -> String {
    NOT_ALNUM
        .replace_all(
            &python::strip(raw).trim_start_matches('=').to_lowercase(),
            "",
        )
        .into_owned()
}

/// Python's `str.title()`, for a command's default label.
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

// --------------------------------------------------------------------------
// Translation
// --------------------------------------------------------------------------

type Offset = (Option<f64>, Option<f64>, Option<String>);

/// The curve and reservoir offset in force at a point in the file. A
/// reservoir offset belongs to the CURVE it was stated against: switching
/// away leaves it behind, switching back brings it with it.
#[derive(Debug, Clone)]
struct Scope {
    curve_id: String,
    delta_r: Option<f64>,
    delta_r_error: Option<f64>,
    delta_r_name: Option<String>,
    curve_offsets: HashMap<String, Offset>,
}

impl Default for Scope {
    fn default() -> Self {
        Self {
            curve_id: "intcal20".to_owned(),
            delta_r: None,
            delta_r_error: None,
            delta_r_name: None,
            curve_offsets: HashMap::new(),
        }
    }
}

/// One block's contents in file order.
enum Item {
    Element(Value),
    Date(Box<Date>),
    Gap {
        line: usize,
        value: Option<f64>,
        error: f64,
    },
}

struct Translator<'a, 'l> {
    result: ImportResult,
    library: &'a Library<'l>,
    ids: &'a mut dyn Ids,
    declared_outlier_models: Vec<String>,
    declared_delta_r: HashMap<String, (f64, f64)>,
    delta_r_usage: BTreeMap<String, usize>,
    wrapped_sequence_dates: usize,
    simulated_dates: usize,
    blocking_commands: Vec<String>,
    date_holding_commands: Vec<String>,
    seen_ignorable: Vec<String>,
    seen_display_attributes: Vec<String>,
    mixed_curves: HashMap<String, String>,
}

fn sorted_unique(items: &[String]) -> Vec<String> {
    let mut unique: Vec<String> = items.to_vec();
    unique.sort();
    unique.dedup();
    unique
}

impl<'a, 'l> Translator<'a, 'l> {
    fn new(result: ImportResult, library: &'a Library<'l>, ids: &'a mut dyn Ids) -> Self {
        Self {
            result,
            library,
            ids,
            declared_outlier_models: Vec::new(),
            declared_delta_r: HashMap::new(),
            delta_r_usage: BTreeMap::new(),
            wrapped_sequence_dates: 0,
            simulated_dates: 0,
            blocking_commands: Vec::new(),
            date_holding_commands: Vec::new(),
            seen_ignorable: Vec::new(),
            seen_display_attributes: Vec::new(),
            mixed_curves: HashMap::new(),
        }
    }

    fn element(&mut self, etype: &str, name: &str, children: Vec<Value>) -> Value {
        date_import::element(self.ids, etype, name, children)
    }

    fn error(&mut self, node: &Node, message: &str) {
        self.result
            .errors
            .push(format!("Line {}: {message}", node.line));
    }

    // -- top level -------------------------------------------------------

    fn translate(&mut self, nodes: &[Node]) {
        self.collect_declarations(nodes);
        let items = self.translate_block(nodes, &mut Scope::default());
        self.assemble(items);
        self.report();
    }

    fn translate_dates_only(&mut self, nodes: &[Node]) {
        self.collect_declarations(nodes);
        self.result.dates = self.collect_dates(nodes, &mut Scope::default(), None);
        self.report();
        if !self.result.dates.is_empty() {
            self.result.notes.push(format!(
                "Imported without the model: the {} date(s) will be calibrated independently. Each keeps the name of the Phase or other command it sat in as its context.",
                self.result.dates.len()
            ));
        } else if self.result.errors.is_empty() {
            self.result
                .errors
                .push("No dates were found in the file.".to_owned());
        }
    }

    fn collect_dates(
        &mut self,
        nodes: &[Node],
        scope: &mut Scope,
        context: Option<&str>,
    ) -> Vec<Date> {
        let mut dates = Vec::new();
        for node in nodes {
            let command = node.lower();
            match command.as_str() {
                "=" => continue,
                "curve" => {
                    self.select_curve(node, scope);
                    continue;
                }
                "delta_r" => {
                    self.apply_delta_r(node, scope);
                    continue;
                }
                "mix_curves" => {
                    self.define_mixed_curve(node);
                    continue;
                }
                _ => {}
            }
            if DATE_COMMANDS.contains(&command.as_str()) {
                if let Some(mut date) = self.translate_date(node, scope) {
                    date.phase_name = context.map(str::to_owned);
                    dates.push(date);
                }
                continue;
            }
            if command == COMBINE_COMMAND {
                let errors_before = self.result.errors.len();
                let (date, _) = self.translate_combine(node, &scope.clone());
                if let Some(mut date) = date {
                    date.phase_name = context.map(str::to_owned);
                    dates.push(date);
                    continue;
                }
                self.result.errors.truncate(errors_before);
                dates.extend(self.collect_dates(node.block(), &mut scope.clone(), context));
                continue;
            }
            for argument in &node.args {
                if let Arg::Call(call) = argument
                    && DATE_COMMANDS.contains(&call.name.to_lowercase().as_str())
                {
                    let inner = Node {
                        command: call.name.clone(),
                        args: call.args.clone(),
                        block: None,
                        line: node.line,
                    };
                    if let Some(mut date) = self.translate_date(&inner, scope) {
                        date.phase_name = context.map(str::to_owned);
                        dates.push(date);
                    }
                }
            }
            if !node.block().is_empty() {
                let inner = if TRANSPARENT.contains(&command.as_str()) {
                    context.map(str::to_owned)
                } else {
                    node.name()
                        .filter(|n| !n.is_empty())
                        .map(str::to_owned)
                        .or_else(|| context.map(str::to_owned))
                };
                dates.extend(self.collect_dates(
                    node.block(),
                    &mut scope.clone(),
                    inner.as_deref(),
                ));
            }
        }
        dates
    }

    fn report(&mut self) {
        for (label, count) in &self.delta_r_usage {
            let Some((offset, uncertainty)) = self.declared_delta_r.get(&label.to_lowercase())
            else {
                continue;
            };
            self.result.notes.push(format!(
                "Delta_R(\"{label}\") = {} +/- {} was applied to {count} date(s) -- every date calibrated against the curve it was stated for. Dates on another curve carry no offset; the table shows which.",
                gf(*offset),
                gf(*uncertainty)
            ));
        }
        if self.simulated_dates > 0 {
            self.result.notes.push(format!(
                "{} date(s) are R_Simulate. Each was given its curve's radiocarbon age at the stated year, with the stated error. OxCal adds random scatter to a simulated measurement, so its results vary from run to run; these do not.",
                self.simulated_dates
            ));
        }
        if !self.seen_ignorable.is_empty() {
            let names: Vec<String> = sorted_unique(&self.seen_ignorable)
                .iter()
                .map(|n| format!("{n}()"))
                .collect();
            self.result.notes.push(format!(
                "Ignored {} -- these ask OxCal to plot or summarise a result and do not affect the model, so leaving them out changes nothing.",
                names.join(", ")
            ));
        }
        if !self.seen_display_attributes.is_empty() {
            let names: Vec<String> = sorted_unique(&self.seen_display_attributes)
                .iter()
                .map(|n| format!("{n}="))
                .collect();
            self.result.notes.push(format!(
                "Ignored {} -- these set how OxCal draws a date and do not affect the model.",
                names.join(", ")
            ));
        }
        if self.wrapped_sequence_dates > 0 {
            self.result.notes.push(format!(
                "{} date(s) sat directly inside a Sequence. Each is now in a Phase of its own, which keeps the ordering exactly as written -- this app attaches dates to Phases.",
                self.wrapped_sequence_dates
            ));
        }
    }

    fn collect_declarations(&mut self, nodes: &[Node]) {
        for node in nodes {
            let command = node.lower();
            if command == "outlier_model" {
                self.declare_outlier_model(node);
            }
            if command == "delta_r" {
                self.declare_delta_r(node);
            }
            if !node.block().is_empty() {
                self.collect_declarations(node.block());
            }
        }
    }

    fn declare_delta_r(&mut self, node: &Node) {
        let name = python::strip(node.name().unwrap_or(""));
        if name.is_empty() || name.starts_with('=') {
            return;
        }
        let numbers = node.numbers();
        if numbers.len() >= 2 {
            self.declared_delta_r
                .insert(name.to_lowercase(), (numbers[0], numbers[1]));
        }
    }

    fn declare_outlier_model(&mut self, node: &Node) {
        let name = python::strip(node.name().unwrap_or("")).to_owned();
        let key = name.to_lowercase();
        if !self.declared_outlier_models.contains(&key) {
            self.declared_outlier_models.push(key.clone());
        }
        let expected: [&str; 2] = match key.as_str() {
            "general" => ["T(5.0)", "U(0.0,4.0)"],
            "charcoal" => ["Exp(1.0,-10.0,0.0)", "U(0.0,3.0)"],
            _ => {
                self.result.warnings.push(format!(
                    "Line {}: Outlier_Model(\"{name}\") is a custom outlier model. This app implements only \"General\" and \"Charcoal\"; any date referring to this one will be refused.",
                    node.line
                ));
                return;
            }
        };
        let found: Vec<String> = node
            .args
            .iter()
            .filter_map(|a| match a {
                Arg::Call(c) => Some(c.text()),
                _ => None,
            })
            .take(2)
            .collect();
        if !found.is_empty() && found != expected {
            self.result.warnings.push(format!(
                "Line {}: Outlier_Model(\"{name}\") is declared as {}, but this app implements the {key} model as {} (Bronk Ramsey 2009) and cannot vary it. Dates using it are calibrated against the app's own parameters, not the file's.",
                node.line,
                found.join(", "),
                expected.join(", ")
            ));
        }
    }

    // -- blocks ----------------------------------------------------------

    fn translate_block(&mut self, nodes: &[Node], scope: &mut Scope) -> Vec<Item> {
        let mut items = Vec::new();
        for node in nodes {
            let command = node.lower();
            match command.as_str() {
                "=" => {
                    if let Some(first) = node.args.first() {
                        let key = first.text().to_lowercase();
                        if DISPLAY_ATTRIBUTES.contains(&key.as_str()) {
                            self.seen_display_attributes.push(key);
                        }
                    }
                }
                c if TRANSPARENT.contains(&c) => {
                    items.extend(self.translate_block(node.block(), &mut scope.clone()));
                }
                "curve" => {
                    self.select_curve(node, scope);
                }
                "delta_r" => self.apply_delta_r(node, scope),
                "mix_curves" => self.define_mixed_curve(node),
                "outlier_model" => {}
                "options" => self.note_options(node),
                c if DATE_COMMANDS.contains(&c) => {
                    if let Some(date) = self.translate_date(node, scope) {
                        items.push(Item::Date(Box::new(date)));
                    }
                }
                COMBINE_COMMAND => {
                    let (date, reason) = self.translate_combine(node, scope);
                    if let Some(date) = date {
                        items.push(Item::Date(Box::new(date)));
                    } else if let Some(reason) = reason {
                        self.blocking_commands.push(node.command.clone());
                        self.error(node, &reason);
                    }
                }
                "gap" => {
                    let numbers = node.numbers();
                    items.push(Item::Gap {
                        line: node.line,
                        value: numbers.first().copied(),
                        error: numbers.get(1).copied().unwrap_or(0.0),
                    });
                }
                c if ORDINATE_SEQUENCES.contains(&c) => {
                    if let Some(e) = self.translate_wiggle_sequence(node, &mut scope.clone()) {
                        items.push(Item::Element(e));
                    }
                }
                DEPOSITION_SEQUENCE => {
                    if let Some(e) = self.translate_deposition_sequence(node, &scope.clone()) {
                        items.push(Item::Element(e));
                    }
                }
                c if STRUCTURAL.contains(&c) => {
                    if let Some(e) = self.translate_structural(node, scope) {
                        items.push(Item::Element(e));
                    }
                }
                c if unsupported(c).is_some() => {
                    self.blocking_commands.push(node.command.clone());
                    let message = format!("{}() is {}", node.command, unsupported(c).unwrap());
                    self.error(node, &message);
                }
                c if IGNORABLE.contains(&c) => {
                    self.check_ignorable_holds_dates(node);
                    self.seen_ignorable.push(node.command.clone());
                }
                _ => {
                    self.blocking_commands.push(node.command.clone());
                    let message = format!(
                        "{}() is not a command this importer recognises. It has been left out rather than guessed at -- if it does not affect the model, delete the line and import again.",
                        node.command
                    );
                    self.error(node, &message);
                }
            }
        }
        items
    }

    fn check_ignorable_holds_dates(&mut self, node: &Node) {
        let buried = count_dates(node.block());
        if buried > 0 {
            self.date_holding_commands.push(node.command.clone());
            let c = &node.command;
            self.result.warnings.push(format!(
                "Line {}: {c}() contains {buried} date(s). {c}() summarises distributions rather than modelling them, so those dates were NOT imported. Move them into a Phase if they belong in the model.",
                node.line
            ));
        }
    }

    // -- individual commands ---------------------------------------------

    fn select_curve(&mut self, node: &Node, scope: &mut Scope) -> Option<String> {
        let resolved = self.resolve_curve(node)?;
        if resolved != scope.curve_id {
            let (delta_r, error, name) = scope
                .curve_offsets
                .get(&resolved)
                .cloned()
                .unwrap_or((None, None, None));
            scope.delta_r = delta_r;
            scope.delta_r_error = error;
            scope.delta_r_name = name;
        }
        scope.curve_id = resolved.clone();
        Some(resolved)
    }

    fn resolve_curve(&mut self, node: &Node) -> Option<String> {
        let name = node.name().filter(|n| !n.is_empty())?.to_owned();
        if let Some(local) = self.mixed_curves.get(&normalise_curve_name(&name)) {
            return Some(local.clone());
        }
        if let Some(resolved) = self.library.resolve_alias(&name) {
            return Some(resolved);
        }
        let available = self.library.curve_names(true).join(", ");
        self.error(
            node,
            &format!(
                "Curve(\"{name}\") is not a curve this app has. It calibrates against {available}; substituting one of those would produce a real result against the wrong curve. Import the curve on the Curves page and try again."
            ),
        );
        None
    }

    fn define_mixed_curve(&mut self, node: &Node) {
        let args = &node.args;
        if args.len() < 4 {
            self.error(
                node,
                "Mix_Curves() needs a name, two curves and a percentage, e.g. Mix_Curves(\"diet\",\"IntCal20\",\"Marine20\",30,10).",
            );
            return;
        }
        let local_name = python::strip(&args[0].text()).trim_matches('"').to_owned();
        let a_id = self.library.resolve_alias(&args[1].text());
        let b_id = self.library.resolve_alias(&args[2].text());
        let (Some(a_id), Some(b_id)) = (a_id.clone(), b_id) else {
            let missing = if a_id.is_none() {
                args[1].text()
            } else {
                args[2].text()
            };
            self.error(
                node,
                &format!("Mix_Curves() names a curve this app does not have: '{missing}'."),
            );
            return;
        };
        let percent = args[3].float();
        let error = if args.len() > 4 {
            args[4].float()
        } else {
            Some(0.0)
        };
        let (Some(percent), Some(error)) = (percent, error) else {
            self.error(node, "Mix_Curves() needs its percentage as a number.");
            return;
        };
        let Some(found) = self.library.find_mixed(&a_id, &b_id, percent, error) else {
            let (p, e) = (gf(percent), gf(error));
            self.error(
                node,
                &format!(
                    "Mix_Curves(\"{local_name}\", {a_id}, {b_id}, {p}, {e}) has no matching curve in this app. Create it on the Curves page -- New mixed curve, {p}% {a_id} with {b_id}, uncertainty {e}% -- and import this file again."
                ),
            );
            return;
        };
        self.mixed_curves
            .insert(normalise_curve_name(&local_name), found.clone());
        self.result.notes.push(format!(
            "Mix_Curves(\"{local_name}\") was matched to the stored curve '{found}'."
        ));
    }

    fn apply_delta_r(&mut self, node: &Node, scope: &mut Scope) {
        let numbers = node.numbers();
        let name = python::strip(node.name().unwrap_or("")).to_owned();
        if numbers.len() >= 2 {
            scope.delta_r = Some(numbers[0]);
            scope.delta_r_error = Some(numbers[1]);
            let label = python::strip(name.trim_start_matches('='));
            scope.delta_r_name = (!label.is_empty()).then(|| label.to_owned());
            bind_to_curve(scope);
            return;
        }
        if name.starts_with('=') {
            let label = python::strip(name.trim_start_matches('=')).to_owned();
            let Some(&(offset, error)) = self.declared_delta_r.get(&label.to_lowercase()) else {
                self.error(
                    node,
                    &format!(
                        "Delta_R(\"{name}\") cites a reservoir offset that the file never states. Add the offset and its uncertainty where it is defined -- importing the dates without it would calibrate them against the global marine curve alone."
                    ),
                );
                return;
            };
            scope.delta_r = Some(offset);
            scope.delta_r_error = Some(error);
            scope.delta_r_name = Some(label);
            bind_to_curve(scope);
            return;
        }
        self.error(
            node,
            "Delta_R needs a value and its uncertainty. An offset without one would understate the uncertainty of every date using it.",
        );
    }

    fn note_options(&mut self, node: &Node) {
        let mut settings = Vec::new();
        for item in node.block() {
            if item.command != "=" || item.args.is_empty() {
                continue;
            }
            let name = item.args[0].text();
            if name.to_lowercase() == "resolution" && self.take_resolution(item) {
                continue;
            }
            settings.push(name);
        }
        if !settings.is_empty() {
            settings.sort();
            self.result.notes.push(format!(
                "Options({}) was not carried over: those configure OxCal's own grid and run length, and this app integrates exactly where it can and extends its own run until it converges.",
                settings.join(", ")
            ));
        }
    }

    fn take_resolution(&mut self, item: &Node) -> bool {
        let value = item.args.get(1);
        let Some(years) = value.and_then(Arg::float) else {
            return false;
        };
        // Python's int() raises on a non-finite value; read as not whole.
        if !years.is_finite() || years != years.trunc() || !(1.0..=100.0).contains(&years) {
            self.result.notes.push(format!(
                "Resolution={} was not carried over: the reporting resolution here is a whole number of years from 1 to 100. The analysis uses the default of 5.",
                value.unwrap().text()
            ));
            return true;
        }
        let years = years as i64;
        self.result.resolution = Some(years);
        self.result.notes.push(format!(
            "Resolution={years} was carried over as the step the modelled ranges are reported at. OxCal also calculates on that grid; this app's solver picks its own, so agreement indices can differ slightly from an OxCal run at a coarse resolution."
        ));
        true
    }

    fn translate_date(&mut self, node: &Node, scope: &Scope) -> Option<Date> {
        let simulated = node.lower() == "r_simulate";
        let numbers = node.numbers();
        if numbers.len() < 2 {
            let message = format!(
                "{} needs {} and an error; found {} number(s).",
                node.command,
                if simulated {
                    "a calendar year"
                } else {
                    "a radiocarbon age"
                },
                numbers.len()
            );
            self.error(node, &message);
            return None;
        }
        let name = node.name_or(|| format!("{} line {}", node.command, node.line));
        let sample_type = |curve: &str| {
            if curve == "marine20" {
                "marine"
            } else {
                "terrestrial"
            }
        };
        let mut date = Date {
            source: Source::Oxcal,
            id: self.ids.next("d"),
            sample_name: name.clone(),
            age_bp: numbers[0],
            error_1sigma: numbers[1],
            material_type: "unknown".to_owned(),
            curve_id: scope.curve_id.clone(),
            sample_type: Some(sample_type(&scope.curve_id).to_owned()),
            delta_r: scope.delta_r,
            delta_r_error: scope.delta_r_error,
            outlier_model: "general".to_owned(),
            outlier_prior: 0.0,
            ..Date::default()
        };
        let mut inner = scope.clone();
        for child in node.block() {
            match child.lower().as_str() {
                "outlier" => self.apply_outlier(child, &mut date),
                "curve" => {
                    if let Some(resolved) = self
                        .select_curve(child, &mut inner)
                        .filter(|r| !r.is_empty())
                    {
                        date.sample_type = Some(sample_type(&resolved).to_owned());
                        date.curve_id = resolved;
                        date.delta_r = inner.delta_r;
                        date.delta_r_error = inner.delta_r_error;
                    }
                }
                "delta_r" => {
                    self.apply_delta_r(child, &mut inner);
                    date.delta_r = inner.delta_r;
                    date.delta_r_error = inner.delta_r_error;
                }
                "=" => self.apply_date_attribute(child, &mut date, &name),
                c if IGNORABLE.contains(&c) => self.seen_ignorable.push(child.command.clone()),
                _ => {
                    let message = format!(
                        "{}() inside {}(\"{name}\") is not something this importer recognises.",
                        child.command, node.command
                    );
                    self.error(child, &message);
                }
            }
        }
        if simulated && !self.simulate_measurement(node, &mut date, numbers[0]) {
            return None;
        }
        if date.delta_r.is_some()
            && let Some(label) = inner.delta_r_name.as_ref().filter(|l| !l.is_empty())
        {
            *self.delta_r_usage.entry(label.clone()).or_insert(0) += 1;
        }
        Some(date)
    }

    fn translate_combine(&mut self, node: &Node, scope: &Scope) -> (Option<Date>, Option<String>) {
        let name = node.name_or(|| format!("R_Combine line {}", node.line));
        if !node.numbers().is_empty() {
            return (
                None,
                Some(format!(
                    "R_Combine(\"{name}\") has a number after its name, which adds to the pooled error in OxCal. This importer does not apply it; remove it, or pool the dates in the model editor."
                )),
            );
        }
        let mut inner = scope.clone();
        let mut dates: Vec<Date> = Vec::new();
        let mut pooled_outlier: Option<&Node> = None;
        for child in node.block() {
            let command = child.lower();
            if DATE_COMMANDS.contains(&command.as_str()) {
                let Some(date) = self.translate_date(child, &inner) else {
                    return (None, None);
                };
                if date.outlier_prior != 0.0 {
                    return (
                        None,
                        Some(format!(
                            "R_Combine(\"{name}\") has an outlier setting on {}. Its measurements are pooled before calibration, so one of them cannot be an outlier on its own; put the Outlier() on the R_Combine instead.",
                            date.sample_name
                        )),
                    );
                }
                dates.push(date);
            } else if command == "curve" {
                self.select_curve(child, &mut inner);
            } else if command == "delta_r" {
                self.apply_delta_r(child, &mut inner);
            } else if command == "outlier" {
                pooled_outlier = Some(child);
            } else if command == "="
                && child
                    .args
                    .first()
                    .is_some_and(|a| DISPLAY_ATTRIBUTES.contains(&a.text().to_lowercase().as_str()))
            {
                self.seen_display_attributes
                    .push(child.args[0].text().to_lowercase());
            } else if IGNORABLE.contains(&command.as_str()) {
                self.seen_ignorable.push(child.command.clone());
            } else {
                return (
                    None,
                    Some(format!(
                        "{}() inside R_Combine(\"{name}\") is not something this importer recognises.",
                        child.command
                    )),
                );
            }
        }
        if dates.len() < 2 {
            return (
                None,
                Some(format!(
                    "R_Combine(\"{name}\") pools {} date; it needs two or more.",
                    dates.len()
                )),
            );
        }
        let test = self.same_event(&dates);
        if let Some(refused) = test.refused {
            return (
                None,
                Some(format!(
                    "R_Combine(\"{name}\") cannot pool its dates: {refused} The model editor refuses the same pool."
                )),
            );
        }
        let figures = format!(
            "T = {:.1} · {}% critical {:.2} · df {}",
            test.t.unwrap(),
            (test.level * 100.0).round_ties_even(),
            test.critical.unwrap(),
            test.df
        );
        if test.consistent != Some(true) {
            return (
                None,
                Some(format!(
                    "R_Combine(\"{name}\") pools dates that fail the same-event test ({figures}), so they are not one event. OxCal pools them anyway; this app does not. Put them in a Phase as separate dates, or leave out the one that does not belong."
                )),
            );
        }
        let sources: Vec<String> = dates
            .iter()
            .map(|d| {
                format!(
                    "{} {} ± {} BP",
                    d.sample_name,
                    gf(d.age_bp),
                    gf(d.error_1sigma)
                )
            })
            .collect();
        let mut pooled = Date {
            id: self.ids.next("d"),
            sample_name: name.clone(),
            age_bp: round1(test.mean_bp.unwrap()),
            error_1sigma: round1(test.error_bp.unwrap()),
            depth_cm: None,
            notes: Some(format!(
                "R_Combine of {} dates (Ward and Wilson 1978; {figures}): {}.",
                dates.len(),
                sources.join("; ")
            )),
            outlier_prior: 0.0,
            outlier_model: "general".to_owned(),
            ..dates[0].clone()
        };
        if let Some(outlier) = pooled_outlier {
            self.apply_outlier(outlier, &mut pooled);
        }
        self.result.notes.push(format!(
            "R_Combine(\"{name}\") was imported as one pooled date, {} ± {} BP ({figures}).",
            gf(pooled.age_bp),
            gf(pooled.error_1sigma)
        ));
        (Some(pooled), None)
    }

    /// Ward and Wilson's test over the dates, as `statistics.same_event`.
    fn same_event(&self, dates: &[Date]) -> SameEvent {
        let rows: Vec<statistics::Date> = dates
            .iter()
            .map(|d| statistics::Date {
                age_bp: d.age_bp,
                error_1sigma: d.error_1sigma,
                curve_id: Some(d.curve_id.clone()),
                delta_r: d.delta_r,
                delta_r_error: d.delta_r_error,
                inbuilt_years: d.inbuilt_years,
                inbuilt_shape: d.inbuilt_shape.clone(),
            })
            .collect();
        statistics::same_event(&rows, |id| self.library.curve(id).ok(), Mode::Standard)
            .expect("two or more dates")
    }

    fn simulate_measurement(&mut self, node: &Node, date: &mut Date, year: f64) -> bool {
        let cal_bp = 1950.0 - year;
        let curve = match self.library.curve(&date.curve_id) {
            Ok(curve) => curve,
            Err(exc) => {
                let message = format!("{}(\"{}\"): {exc}", node.command, date.sample_name);
                self.error(node, &message);
                return false;
            }
        };
        let low = curve
            .cal_ages()
            .iter()
            .copied()
            .fold(f64::INFINITY, f64::min);
        let high = curve
            .cal_ages()
            .iter()
            .copied()
            .fold(f64::NEG_INFINITY, f64::max);
        if !(low <= cal_bp && cal_bp <= high) {
            let message = format!(
                "{}(\"{}\") is for the year {} ({} cal BP), outside {}, which covers {}-{} cal BP. There is no curve value there to simulate a measurement from.",
                node.command,
                date.sample_name,
                gf(year),
                gf(cal_bp),
                date.curve_id,
                gf(low),
                gf(high)
            );
            self.error(node, &message);
            return false;
        }
        let c14_age = match curve.interpolate(&[cal_bp]) {
            Ok((c14, _)) => c14[0],
            Err(exc) => {
                let message = format!("{}(\"{}\"): {exc}", node.command, date.sample_name);
                self.error(node, &message);
                return false;
            }
        };
        date.age_bp = round1(c14_age + date.delta_r.unwrap_or(0.0));
        date.notes = Some(format!(
            "Simulated (R_Simulate) for the year {}, {} cal BP.",
            gf(year),
            gf(cal_bp)
        ));
        self.simulated_dates += 1;
        true
    }

    fn apply_date_attribute(&mut self, node: &Node, date: &mut Date, name: &str) {
        let key = node
            .args
            .first()
            .map(|a| a.text().to_lowercase())
            .unwrap_or_default();
        if key == "z" {
            match node.args.get(1).and_then(Arg::float) {
                Some(depth) => date.depth_cm = Some(depth),
                None => self.error(
                    node,
                    &format!("R_Date(\"{name}\") has a z= that is not a number."),
                ),
            }
            return;
        }
        if DISPLAY_ATTRIBUTES.contains(&key.as_str()) {
            self.seen_display_attributes.push(key);
            return;
        }
        let what = if key.is_empty() {
            "An assignment".to_owned()
        } else {
            key
        };
        self.error(
            node,
            &format!(
                "{what}= inside R_Date(\"{name}\") is not something this importer recognises. It has been left out rather than guessed at -- if it does not affect the model, delete the line and import again."
            ),
        );
    }

    fn apply_outlier(&mut self, node: &Node, date: &mut Date) {
        let model_name =
            python::strip(node.name().filter(|n| !n.is_empty()).unwrap_or("General")).to_owned();
        let key = model_name.to_lowercase();
        let prior = node.numbers().first().copied().unwrap_or(0.05);
        if key != "general" && key != "charcoal" {
            self.error(
                node,
                &format!(
                    "Outlier(\"{model_name}\") refers to an outlier model this app does not implement. It has \"General\" (symmetric, for a date wrong in an unknown way) and \"Charcoal\" (one-sided, for old wood); applying the wrong one changes both the date and its outlier probability."
                ),
            );
            return;
        }
        if !self.declared_outlier_models.contains(&key) {
            self.result.warnings.push(format!(
                "Line {}: Outlier(\"{model_name}\") is used but no Outlier_Model(\"{model_name}\") is declared in the file. The app's own definition of that model was applied.",
                node.line
            ));
        }
        date.outlier_model = key;
        date.outlier_prior = prior;
    }

    /// A Gap() outside a wiggle-matched sequence measures nothing.
    fn reject_gaps(&mut self, items: &[Item], place: &str) {
        for item in items {
            if let Item::Gap { line, .. } = item {
                self.result.errors.push(format!(
                    "Line {line}: Gap() inside {place} is not something this app models. A gap between dates is the measured separation of a D_Sequence or V_Sequence; between phases, write two Boundaries with a Hiatus between them."
                ));
            }
        }
    }

    /// `D_Sequence` / `V_Sequence` into a `wiggle_sequence`: each member's
    /// distance back from the youngest, the gap errors added in quadrature.
    fn translate_wiggle_sequence(&mut self, node: &Node, scope: &mut Scope) -> Option<Value> {
        let command = node.lower();
        let name = node.name_or(|| {
            if command == "d_sequence" {
                "Wiggle match"
            } else {
                "Varve sequence"
            }
            .to_owned()
        });
        let items = self.translate_block(node.block(), scope);
        let c = node.command.clone();
        if let Some(Item::Element(e)) = items.iter().find(|i| matches!(i, Item::Element(_))) {
            let etype = e["element_type"].as_str().unwrap_or("None").to_owned();
            self.error(
                node,
                &format!(
                    "{c}(\"{name}\") contains a {etype}. A wiggle-matched series is its dates and the gaps between them; there is no position in it for a phase, a boundary or another sequence."
                ),
            );
            return None;
        }

        let mut entries: Vec<(Date, f64, f64)> = Vec::new();
        let (mut cumulative, mut cumulative_variance) = (0.0, 0.0);
        let (mut pending_value, mut pending_variance) = (0.0, 0.0);
        let mut pending_seen = false;
        let mut has_gap_error = false;
        for item in items {
            match item {
                Item::Gap { value, error, .. } => {
                    let Some(value) = value else {
                        self.error(
                            node,
                            &format!(
                                "{c}(\"{name}\") has a Gap() with no number in it. The gaps are the model: a gap of unknown size cannot be read as zero."
                            ),
                        );
                        return None;
                    };
                    pending_value += value;
                    pending_variance += error.powi(2);
                    has_gap_error = has_gap_error || error != 0.0;
                    pending_seen = true;
                }
                Item::Date(date) => {
                    let date = *date;
                    if !entries.is_empty() {
                        if !pending_seen {
                            self.error(
                                node,
                                &format!(
                                    "{c}(\"{name}\") has two dates with no Gap() between them (at \"{}\"). Every step of the series needs its measured separation, or the two are one event rather than two.",
                                    date.sample_name
                                ),
                            );
                            return None;
                        }
                        cumulative += pending_value;
                        cumulative_variance += pending_variance;
                    } else if pending_seen {
                        self.error(
                            node,
                            &format!(
                                "{c}(\"{name}\") opens with a Gap(). A gap separates two members, so there is nothing for the first one to be a gap from."
                            ),
                        );
                        return None;
                    }
                    entries.push((date, cumulative, cumulative_variance));
                    pending_value = 0.0;
                    pending_variance = 0.0;
                    pending_seen = false;
                }
                Item::Element(_) => unreachable!("refused above"),
            }
        }
        if entries.is_empty() {
            self.error(node, &format!("{c}(\"{name}\") has no dates in it."));
            return None;
        }
        if pending_seen {
            self.result.warnings.push(format!(
                "Line {}: {c}(\"{name}\") ends with a Gap() after its last date. A gap to nothing measures nothing, so it was left out -- if it is the gap to the felling ring, add that ring as a date.",
                node.line
            ));
        }
        let (total, total_variance) = {
            let last = entries.last().unwrap();
            (last.1, last.2)
        };
        let reference_id = entries.last().unwrap().0.id.clone();
        let reference_name = entries.last().unwrap().0.sample_name.clone();
        let count = entries.len();
        let mut element = self.element("wiggle_sequence", &name, Vec::new());
        let element_id = element["id"].as_str().unwrap().to_owned();
        for (mut date, distance, variance) in entries {
            date.ring_offset = Some(total - distance);
            let spread = total_variance - variance;
            // `spread ** 0.5`: pow, not sqrt.
            date.ring_offset_error = (spread > 0.0).then(|| spread.powf(0.5));
            self.attach(date, &element_id, &name);
        }
        element["params"] = json!({"gap_units": "years", "reference_sample_id": reference_id});
        if has_gap_error {
            self.result.warnings.push(format!(
                "Line {}: {c}(\"{name}\") gives its gaps their own uncertainties. This app carries one uncertainty per date -- that date's distance from the reference, here the youngest member -- so the gap errors were added in quadrature along the series. Each date then has the right spread, but this app treats those spreads as independent where OxCal shares each gap between every date beyond it.",
                node.line
            ));
        }
        self.result.notes.push(format!(
            "{c}(\"{name}\") was read as a wiggle match of {count} dates spanning {} years, measured from \"{reference_name}\" as the reference.",
            gf(total)
        ));
        Some(element)
    }

    /// `P_Sequence(name, k, interval, prior)` into a `deposition_sequence`,
    /// z read as depth below the surface.
    fn translate_deposition_sequence(&mut self, node: &Node, scope: &Scope) -> Option<Value> {
        let name = node.name_or(|| "Deposition sequence".to_owned());
        let numbers = node.numbers();
        let k = match numbers.first() {
            Some(&k) if k > 0.0 => k,
            _ => {
                self.error(
                    node,
                    &format!(
                        "P_Sequence(\"{name}\") needs a positive deposition rate k as its first number. k is events per unit depth and decides how rigid the model is, so it cannot be guessed at."
                    ),
                );
                return None;
            }
        };
        if numbers.len() > 1 && numbers[1] != 0.0 {
            self.result.notes.push(format!(
                "Line {}: P_Sequence(\"{name}\") asks OxCal to interpolate every {} units. This app models the levels it is given and reports an age at any depth you add as an undated level, so that argument was not applied.",
                node.line,
                gf(numbers[1])
            ));
        }
        let mut variable = None;
        for argument in &node.args {
            if let Arg::Call(call) = argument
                && call.name.to_lowercase() == "u"
            {
                let bounds: Vec<f64> = call
                    .args
                    .iter()
                    .filter_map(|a| match a {
                        Arg::Number(x) => Some(*x),
                        _ => None,
                    })
                    .collect();
                if bounds.len() >= 2 {
                    variable = Some((bounds[0], bounds[1]));
                }
            }
        }
        let log_k = k.log10();
        let mut element = self.element("deposition_sequence", &name, Vec::new());
        let element_id = element["id"].as_str().unwrap().to_owned();
        element["params"] = json!({
            "k": if variable.is_some() { Value::Null } else { Value::from(k) },
            "k_variable": variable.is_some(),
            "log_k_min": variable.map_or(log_k - 2.0, |v| log_k + v.0),
            "log_k_max": variable.map_or(log_k + 2.0, |v| log_k + v.1),
            "depth_units": "cm",
        });

        let mut inner = scope.clone();
        let mut depths: Vec<f64> = Vec::new();
        let mut dated = 0;
        let mut boundary_levels = 0;
        let mut children = Vec::new();
        for child in node.block() {
            let command = child.lower();
            match command.as_str() {
                "=" => continue,
                "curve" => {
                    self.select_curve(child, &mut inner);
                    continue;
                }
                "delta_r" => {
                    self.apply_delta_r(child, &mut inner);
                    continue;
                }
                c if IGNORABLE.contains(&c) => {
                    self.check_ignorable_holds_dates(child);
                    self.seen_ignorable.push(child.command.clone());
                    continue;
                }
                _ => {}
            }
            let depth = depth_of(child);
            if DATE_COMMANDS.contains(&command.as_str()) {
                let mut date = self.translate_date(child, &inner)?;
                let Some(depth) = depth else {
                    let message = format!(
                        "{}(\"{}\") inside P_Sequence(\"{name}\") has no z=. Depth is the axis the deposition model runs along, so a level without one cannot take part in it.",
                        child.command, date.sample_name
                    );
                    self.error(child, &message);
                    return None;
                };
                date.depth_cm = Some(depth);
                depths.push(depth);
                dated += 1;
                self.attach(date, &element_id, &name);
                continue;
            }
            if command == "date" || command == "boundary" {
                let Some(depth) = depth else {
                    let message = format!(
                        "{}(\"{}\") inside P_Sequence(\"{name}\") has no z=. An undated level is a question about a DEPTH, so there is nothing to ask without one.",
                        child.command,
                        child.name().unwrap_or("")
                    );
                    self.error(child, &message);
                    return None;
                };
                depths.push(depth);
                if command == "boundary" {
                    boundary_levels += 1;
                }
                let label = child.name_or(|| format!("{} cm", gf(depth)));
                let mut level = self.element("interpolated_depth", &label, Vec::new());
                level["params"] = json!({"depth_cm": depth});
                children.push(level);
                continue;
            }
            self.blocking_commands.push(child.command.clone());
            let message = format!(
                "{}() inside P_Sequence(\"{name}\") is not something this app models. A deposition sequence holds dates and undated levels, each with its own z=.",
                child.command
            );
            self.error(child, &message);
            return None;
        }
        element["children"] = Value::Array(children);
        if dated == 0 {
            self.error(
                node,
                &format!(
                    "P_Sequence(\"{name}\") has no R_Date in it. Deposition cannot be modelled against depth with nothing measured at any level."
                ),
            );
            return None;
        }
        if boundary_levels > 0 {
            self.result.warnings.push(format!(
                "Line {}: P_Sequence(\"{name}\") has {boundary_levels} Boundary element(s) with a depth. They were imported as undated levels of the deposition sequence, which is the same parameter under another name here -- but NOT the uniform transition a Boundary is elsewhere in this app.",
                node.line
            ));
        }
        if depths.len() > 1 && depths[0] < depths[depths.len() - 1] {
            self.result.warnings.push(format!(
                "Line {}: P_Sequence(\"{name}\") has z increasing down the file. z has been read as DEPTH BELOW THE SURFACE -- larger is deeper and older. If it is a height above a datum, the sequence is modelled upside down; negate the values and import again.",
                node.line
            ));
        }
        Some(element)
    }

    fn translate_structural(&mut self, node: &Node, scope: &Scope) -> Option<Value> {
        let etype = node.lower();
        let name = node.name_or(|| title(&node.command));
        if etype == "boundary" || etype == "hiatus" {
            if !node.block().is_empty() && count_dates(node.block()) > 0 {
                let message = format!(
                    "{}(\"{name}\") contains dates. A {etype} is a single transition parameter and cannot hold measurements.",
                    node.command
                );
                self.error(node, &message);
                return None;
            }
            let priors: Vec<String> = node
                .args
                .iter()
                .filter_map(|a| match a {
                    Arg::Call(c) => Some(c.text()),
                    _ => None,
                })
                .collect();
            if !priors.is_empty() {
                self.result.warnings.push(format!(
                    "Line {}: {}(\"{name}\") carries a prior ({}), which this app does not model. It was imported as an ordinary uniform {etype} -- set a TPQ or TAQ on it if the limit matters.",
                    node.line,
                    node.command,
                    priors.join(", ")
                ));
            }
            return Some(self.element(&etype, &name, Vec::new()));
        }

        let items = self.translate_block(node.block(), &mut scope.clone());
        self.reject_gaps(&items, &format!("{}(\"{name}\")", node.command));

        if etype == "phase" {
            let mut children = Vec::new();
            let mut dates = Vec::new();
            for item in items {
                match item {
                    Item::Element(e) => children.push(e),
                    Item::Date(d) => dates.push(*d),
                    Item::Gap { .. } => {}
                }
            }
            let element = self.element("phase", &name, children);
            let id = element["id"].as_str().unwrap().to_owned();
            for date in dates {
                self.attach(date, &id, &name);
            }
            return Some(element);
        }

        let mut children = Vec::new();
        for item in items {
            match item {
                Item::Element(e) => children.push(e),
                Item::Date(date) => {
                    let date = *date;
                    let label = date.sample_name.clone();
                    let wrapper = self.element("phase", &label, Vec::new());
                    let id = wrapper["id"].as_str().unwrap().to_owned();
                    self.attach(date, &id, &label);
                    self.wrapped_sequence_dates += 1;
                    children.push(wrapper);
                }
                Item::Gap { .. } => {}
            }
        }
        Some(self.element("sequence", &name, children))
    }

    /// Put a date in a phase and record it, once.
    fn attach(&mut self, mut date: Date, element_id: &str, phase_name: &str) {
        date.element_id = Some(element_id.to_owned());
        date.phase_name = Some(phase_name.to_owned());
        self.result
            .element_assignments
            .push((date.id.clone(), element_id.to_owned()));
        self.result.dates.push(date);
    }

    // -- assembly --------------------------------------------------------

    /// The top level as one rooted tree; several top-level things wrapped in
    /// a Phase, which asserts no order between them.
    fn assemble(&mut self, items: Vec<Item>) {
        self.reject_gaps(&items, "the top level of the file");
        let mut elements = Vec::new();
        let mut loose = Vec::new();
        for item in items {
            match item {
                Item::Element(e) => elements.push(e),
                Item::Date(d) => loose.push(*d),
                Item::Gap { .. } => {}
            }
        }
        if !loose.is_empty() {
            if elements.is_empty() {
                self.result.dates.extend(loose);
                self.result.model_tree = None;
                self.result.element_assignments.clear();
                self.result.notes.push(
                    "The file has no Sequence, Phase or Boundary, so there is no model structure to import -- the dates will be calibrated independently.".to_owned(),
                );
                return;
            }
            let count = loose.len();
            let wrapper = self.element("phase", "Ungrouped dates", Vec::new());
            let id = wrapper["id"].as_str().unwrap().to_owned();
            for date in loose {
                self.attach(date, &id, "Ungrouped dates");
            }
            elements.push(wrapper);
            self.result.warnings.push(format!(
                "{count} date(s) sat outside any Sequence or Phase. They are grouped under \"Ungrouped dates\" so none is lost, unordered with respect to everything else."
            ));
        }
        if elements.is_empty() {
            if self.result.errors.is_empty() {
                self.result
                    .errors
                    .push("No dates or model structure were found in the file.".to_owned());
            }
            return;
        }
        if elements.len() == 1
            && [
                "sequence",
                "phase",
                "wiggle_sequence",
                "deposition_sequence",
            ]
            .contains(&elements[0]["element_type"].as_str().unwrap_or(""))
        {
            self.result.model_tree = elements.pop();
            return;
        }
        let count = elements.len();
        let name = self
            .result
            .analysis_name
            .clone()
            .unwrap_or_else(|| "Imported model".to_owned());
        self.result.model_tree = Some(self.element("phase", &name, elements));
        self.result.notes.push(format!(
            "The file holds {count} top-level elements. They are wrapped in one Phase, which asserts no ordering between them -- the same thing separate top-level items in a Plot assert."
        ));
    }
}

fn bind_to_curve(scope: &mut Scope) {
    scope.curve_offsets.insert(
        scope.curve_id.clone(),
        (
            scope.delta_r,
            scope.delta_r_error,
            scope.delta_r_name.clone(),
        ),
    );
}

fn count_dates(nodes: &[Node]) -> usize {
    nodes
        .iter()
        .map(|n| usize::from(DATE_COMMANDS.contains(&n.lower().as_str())) + count_dates(n.block()))
        .sum()
}

/// The `z=` an element carries, None when it has none or it is not a number.
fn depth_of(node: &Node) -> Option<f64> {
    node.block()
        .iter()
        .find(|c| {
            c.command == "="
                && c.args
                    .first()
                    .is_some_and(|a| a.text().to_lowercase() == "z")
        })
        .and_then(|c| c.args.get(1).and_then(Arg::float))
}

// --------------------------------------------------------------------------
// Entry point
// --------------------------------------------------------------------------

/// Read an OxCal model file into dates plus a model tree. Curve names
/// resolve against `library`.
pub fn parse_oxcal(
    text: &str,
    library: &Library,
    filename: Option<&str>,
    ids: &mut dyn Ids,
) -> ImportResult {
    let mut result = ImportResult::new("oxcal");
    result.analysis_name = date_import::name_from_filename(filename);
    let nodes = match parse_script(text) {
        Ok(nodes) => nodes,
        Err(SyntaxError(message)) => {
            result.errors.push(message);
            return result;
        }
    };
    if nodes.is_empty() {
        result
            .errors
            .push("The file has no OxCal commands in it.".to_owned());
        return result;
    }
    let mut translator = Translator::new(result, library, ids);
    translator.translate(&nodes);
    let blocking = if translator.blocking_commands.is_empty() {
        translator.date_holding_commands.clone()
    } else {
        translator.blocking_commands.clone()
    };
    let mut result = translator.result;

    if result.analysis_name.is_none()
        && let Some(tree) = &result.model_tree
    {
        result.analysis_name = tree["name"].as_str().map(str::to_owned);
    }
    if result.dates.is_empty() && result.errors.is_empty() {
        result
            .errors
            .push("The file has a model structure but no R_Date in it.".to_owned());
    }
    if result.dates.len() > MAX_DATES {
        result.errors.push(format!(
            "The model holds {} dates; the limit is {MAX_DATES} per import.",
            result.dates.len()
        ));
    }
    if !result.errors.is_empty() {
        return offer_dates_only(result.refuse(), &blocking, &nodes, library, ids);
    }
    validate_imported_tree(&mut result);
    if !result.errors.is_empty() {
        return offer_dates_only(result.refuse(), &blocking, &nodes, library, ids);
    }
    result
}

/// A refused model, with the file read as its dates alone attached when that
/// reading succeeds, and the commands that sank the model named.
fn offer_dates_only(
    mut result: ImportResult,
    blocking: &[String],
    nodes: &[Node],
    library: &Library,
    ids: &mut dyn Ids,
) -> ImportResult {
    let mut unique = Vec::new();
    for command in blocking {
        if !unique.contains(command) {
            unique.push(command.clone());
        }
    }
    result.blocking_commands = unique;

    let mut fallback = ImportResult::new("oxcal");
    fallback.analysis_name = result.analysis_name.clone();
    let mut translator = Translator::new(fallback, library, ids);
    translator.translate_dates_only(nodes);
    let mut fallback = translator.result;
    if fallback.dates.len() > MAX_DATES {
        return result;
    }
    if fallback.ok() {
        date_import::validate_dates(&mut fallback);
    }
    if fallback.ok() && !fallback.dates.is_empty() {
        result.dates_only = Some(Box::new(fallback));
    }
    result
}

/// A JSON model element as radical-core's.
pub fn element_from_json(v: &Value) -> Element {
    let param = |v: &Value| match v {
        Value::Bool(b) => Param::Bool(*b),
        Value::Number(n) => Param::Number(n.as_f64().unwrap()),
        Value::String(s) => Param::Text(s.clone()),
        _ => Param::Null,
    };
    Element {
        id: v["id"].as_str().unwrap_or_default().to_owned(),
        element_type: v["element_type"].as_str().unwrap_or_default().to_owned(),
        name: v["name"].as_str().map(str::to_owned),
        tpq_age_bp: v["tpq_age_bp"].as_f64(),
        taq_age_bp: v["taq_age_bp"].as_f64(),
        params: v["params"].as_object().map(|p| {
            p.iter()
                .map(|(k, v)| (k.clone(), param(v)))
                .collect::<Params>()
        }),
        children: v["children"]
            .as_array()
            .map(|c| c.iter().map(element_from_json).collect())
            .unwrap_or_default(),
    }
}

/// The imported tree held to the structural rules the API enforces, so a
/// file that parses cannot still be refused when it is run.
fn validate_imported_tree(result: &mut ImportResult) {
    let Some(tree) = &result.model_tree else {
        return;
    };
    let root = element_from_json(tree);
    let described: Vec<Measurement> = result
        .dates
        .iter()
        .map(|d| Measurement {
            id: d.id.clone(),
            sample_name: Some(d.sample_name.clone()),
            element_id: d.element_id.clone(),
            ring_offset: d.ring_offset,
            ring_offset_error: d.ring_offset_error,
            depth_cm: d.depth_cm,
            outlier_prior: Some(d.outlier_prior),
            ..Measurement::default()
        })
        .collect();
    let checked: Result<(), Error> = model_tree::validate_tree(Some(&root), &described)
        .and_then(|()| model_tree::validate_ordinates(Some(&root), &described));
    if let Err(exc) = checked {
        result.errors.push(format!(
            "The model read from this file is not one this app can run: {exc}"
        ));
        result.model_tree = None;
        result.element_assignments.clear();
        return;
    }
    if !model_tree::elements_of_type(Some(&root), "wiggle_sequence").is_empty()
        && model_tree::standalone_wiggle_match(Some(&root), &described).is_none()
    {
        result.errors.push(
            "This file puts a wiggle-matched sequence inside a larger model, which this app does not yet solve: a wiggle match is one calendar age with the measured gaps added to it, and the surrounding Boundaries and phases would have to model it that way rather than as separate dates. A file holding the D_Sequence or V_Sequence on its own imports and runs.".to_owned(),
        );
        result.model_tree = None;
        result.element_assignments.clear();
        return;
    }
    date_import::validate_dates(result);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::CountingIds;

    fn parse(text: &str) -> ImportResult {
        parse_oxcal(
            text,
            &Library::builtins(),
            Some("t.oxcal"),
            &mut CountingIds::default(),
        )
    }

    /// Where the Python raises IndexError on a truncated file, this reports
    /// it as the syntax error it is.
    #[test]
    fn a_truncated_file_is_a_syntax_error() {
        for text in ["R_Date(", "Options(){ Resolution="] {
            assert_eq!(
                parse(text).errors,
                ["The file ends where a value was expected."],
                "{text}"
            );
        }
    }

    /// Where the Python raises OverflowError, a non-finite resolution is not
    /// a whole number of years.
    #[test]
    fn an_infinite_resolution_is_not_carried_over() {
        let result =
            parse(r#"Options(){ Resolution="inf"; }; Phase("P"){ R_Date("a",3000,25); };"#);
        assert_eq!(result.resolution, None);
        assert!(
            result.notes[0].starts_with("Resolution=inf was not carried over"),
            "{:?}",
            result.notes
        );
    }

    #[test]
    fn a_call_reads_as_python_writes_it() {
        let nodes = parse_script(r#"Outlier_Model("General",T(5),U(0,4),"t");"#).unwrap();
        let texts: Vec<String> = nodes[0].args.iter().map(Arg::text).collect();
        assert_eq!(texts, ["General", "T(5.0)", "U(0.0,4.0)", "t"]);
        let nested = parse_script(r#"X(F("it's", a));"#).unwrap();
        assert_eq!(nested[0].args[0].text(), r#"F("it's",'a')"#);
    }
}
