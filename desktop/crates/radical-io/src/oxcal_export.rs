//! An analysis written as an OxCal model file (.oxcal), for a reader who
//! runs OxCal rather than this app. Port of `services/oxcal_export.py`.
//!
//! The inverse of [`crate::oxcal_script`], and held to it: every construct
//! written here is one that importer reads back. What this app models and
//! OxCal's language cannot say -- a TPQ or TAQ limit, an inbuilt age, a date
//! outside the model -- is not approximated. It is left out and listed, in
//! the file's header and in what this returns.

use std::collections::HashMap;

use radical_core::curve_registry::BUILTINS;
use radical_core::measurement::Measurement;
use radical_core::model_tree::{Element, Param};
use radical_core::pyfmt::g;

use crate::library::Library;

/// OxCal's own file names for the shipped curves.
fn builtin_file(curve_id: &str) -> String {
    match curve_id {
        "intcal20" => "intcal20.14c".to_owned(),
        "shcal20" => "shcal20.14c".to_owned(),
        "marine20" => "marine20.14c".to_owned(),
        other => format!("{other}.14c"),
    }
}

/// The two outlier models this app implements, as OxCal declares them.
fn outlier_model_statement(model: &str) -> &'static str {
    match model {
        "general" => r#"Outlier_Model("General",T(5),U(0,4),"t");"#,
        "charcoal" => r#"Outlier_Model("Charcoal",Exp(1,-10,0),U(0,3),"t");"#,
        _ => "",
    }
}

fn number(value: f64) -> String {
    g(value, 10)
}

/// A name as an OxCal string. A double quote inside one would end it.
fn quote(text: Option<&str>) -> String {
    format!(
        "\"{}\"",
        text.unwrap_or("").replace('"', "'").replace('\n', " ")
    )
}

fn comment(text: &str) -> String {
    text.replace("*/", "* /")
}

/// Python's `str.title()`.
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

/// Python's `str.capitalize()`.
fn capitalize(text: &str) -> String {
    let mut chars = text.chars();
    match chars.next() {
        Some(first) => first
            .to_uppercase()
            .chain(chars.flat_map(char::to_lowercase))
            .collect(),
        None => String::new(),
    }
}

/// What a curve is, for writing it.
enum Kind {
    Builtin,
    Imported(Option<String>),
    Mixed {
        a: String,
        b: String,
        percent_a: f64,
        percent_a_error: f64,
    },
}

/// The settings of an analysis the file carries.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Config {
    pub resolution: Option<f64>,
    pub uniform_span_prior: Option<bool>,
}

type State = (String, Option<f64>, Option<f64>);

struct Writer<'m, 'a, 'l> {
    lines: Vec<String>,
    omitted: Vec<String>,
    depth: usize,
    measurements: &'m [Measurement],
    by_element: HashMap<&'m str, Vec<usize>>,
    state: Option<State>,
    offset_names: Vec<((f64, f64), String)>,
    curve_names: Vec<(String, String)>,
    mixed: Vec<(String, (String, String, f64, f64))>,
    library: &'a Library<'l>,
}

impl<'m, 'a, 'l> Writer<'m, 'a, 'l> {
    fn new(measurements: &'m [Measurement], library: &'a Library<'l>) -> Self {
        let mut by_element: HashMap<&str, Vec<usize>> = HashMap::new();
        for (i, m) in measurements.iter().enumerate() {
            if let Some(e) = m.element_id.as_deref().filter(|e| !e.is_empty()) {
                by_element.entry(e).or_default().push(i);
            }
        }
        Self {
            lines: Vec::new(),
            omitted: Vec::new(),
            depth: 0,
            measurements,
            by_element,
            state: None,
            offset_names: Vec::new(),
            curve_names: Vec::new(),
            mixed: Vec::new(),
            library,
        }
    }

    fn line(&mut self, text: &str) {
        self.lines
            .push(format!("{}{text}", "  ".repeat(self.depth)));
    }

    fn open(&mut self, head: &str) {
        self.line(head);
        self.line("{");
        self.depth += 1;
    }

    fn close(&mut self) {
        self.depth -= 1;
        self.line("};");
        self.state = None;
    }

    // -- curves -----------------------------------------------------------

    /// What `curve_registry.get_spec` says of a curve: its name and kind.
    fn spec(&self, curve_id: &str) -> Result<(String, Kind), String> {
        if let Some(b) = BUILTINS.iter().find(|b| b.id == curve_id) {
            return Ok((b.name.to_owned(), Kind::Builtin));
        }
        let Some(stored) = self.library.stored(curve_id) else {
            let known: Vec<String> = BUILTINS.iter().map(|b| b.id.to_owned()).collect();
            return Err(format!(
                "Unknown calibration curve '{curve_id}'. Supported curves: {}",
                known.join(", ")
            ));
        };
        let kind = match &stored.recipe {
            Some(r) => Kind::Mixed {
                a: r.a.clone(),
                b: r.b.clone(),
                percent_a: r.percent_a,
                percent_a_error: r.percent_a_error,
            },
            None => Kind::Imported(stored.source_filename.clone()),
        };
        Ok((stored.names.name.clone(), kind))
    }

    fn curve_name(&self, curve_id: &str) -> &str {
        &self
            .curve_names
            .iter()
            .find(|(id, _)| id == curve_id)
            .unwrap()
            .1
    }

    /// Load every curve used, once: the files first, then each mixture after
    /// the curves it is made of. OxCal refuses a name defined twice.
    fn prepare_curves(&mut self, curve_ids: &[String]) -> Result<(), String> {
        let mut ids = curve_ids.to_vec();
        ids.sort();
        ids.dedup();
        let mut order = Vec::new();
        for id in &ids {
            self.collect(id, &mut order, 0)?;
        }
        for id in &order {
            if !self.mixed.iter().any(|(m, _)| m == id) {
                let statement = self.load_statement(id)?;
                self.line(&statement);
            }
        }
        for id in &order {
            if let Some((_, (a, b, percent, error))) =
                self.mixed.iter().find(|(m, _)| m == id).cloned()
            {
                let statement = format!(
                    "Mix_Curves({},{},{},{},{});",
                    quote(Some(self.curve_name(id))),
                    quote(Some(self.curve_name(&a))),
                    quote(Some(self.curve_name(&b))),
                    number(percent),
                    number(error)
                );
                self.line(&statement);
            }
        }
        Ok(())
    }

    fn collect(
        &mut self,
        curve_id: &str,
        order: &mut Vec<String>,
        depth: usize,
    ) -> Result<(), String> {
        if self.curve_names.iter().any(|(id, _)| id == curve_id) || depth > 8 {
            return Ok(());
        }
        let (name, kind) = self.spec(curve_id)?;
        let name = if name.is_empty() {
            curve_id.to_owned()
        } else {
            name
        };
        self.curve_names.push((curve_id.to_owned(), name));
        if let Kind::Mixed {
            a,
            b,
            percent_a,
            percent_a_error,
        } = kind
        {
            self.collect(&a, order, depth + 1)?;
            self.collect(&b, order, depth + 1)?;
            self.mixed
                .push((curve_id.to_owned(), (a, b, percent_a, percent_a_error)));
        }
        order.push(curve_id.to_owned());
        Ok(())
    }

    fn load_statement(&self, curve_id: &str) -> Result<String, String> {
        let filename = match self.spec(curve_id)?.1 {
            Kind::Builtin => builtin_file(curve_id),
            Kind::Imported(Some(f)) if !f.is_empty() => f,
            _ => format!("{curve_id}.14c"),
        };
        Ok(format!(
            "Curve({},{});",
            quote(Some(self.curve_name(curve_id))),
            quote(Some(&filename))
        ))
    }

    /// State the date's curve and offset, if they are not already in force.
    fn select(&mut self, m: &Measurement) {
        let want: State = (
            m.curve_id
                .clone()
                .filter(|c| !c.is_empty())
                .unwrap_or_else(|| "intcal20".to_owned()),
            m.delta_r,
            if m.delta_r.is_some() {
                m.delta_r_error
            } else {
                None
            },
        );
        if self.state.as_ref() == Some(&want) {
            return;
        }
        let statement = format!("Curve({});", quote(Some(self.curve_name(&want.0))));
        self.line(&statement);
        if !self.offset_names.is_empty() {
            let name = match want.1 {
                Some(dr) => {
                    let key = (dr, want.2.unwrap_or(0.0));
                    self.offset_names
                        .iter()
                        .find(|(k, _)| *k == key)
                        .unwrap()
                        .1
                        .clone()
                }
                None => "None".to_owned(),
            };
            self.line(&format!("Delta_R(\"={name}\", );"));
        }
        self.state = Some(want);
    }

    /// Name every reservoir offset once, at the head of Plot().
    fn declare_offsets(&mut self) {
        let mut offsets: Vec<(f64, f64)> = Vec::new();
        let mut bare = false;
        for m in self.measurements {
            let Some(dr) = m.delta_r else {
                bare = true;
                continue;
            };
            let key = (dr, m.delta_r_error.unwrap_or(0.0));
            if !offsets.contains(&key) {
                offsets.push(key);
            }
        }
        let several = offsets.len() != 1;
        for (index, key) in offsets.iter().enumerate() {
            let name = if several {
                format!("LocalMarine{}", index + 1)
            } else {
                "LocalMarine".to_owned()
            };
            self.line(&format!(
                "Delta_R({},{},{});",
                quote(Some(&name)),
                number(key.0),
                number(key.1)
            ));
            self.offset_names.push((*key, name));
        }
        if !offsets.is_empty() && bare {
            self.line("Delta_R(\"None\",0,0);");
        }
    }

    // -- dates ------------------------------------------------------------

    fn date(&mut self, m: &Measurement, depth: bool) {
        self.select(m);
        let name = m.sample_name.as_deref();
        let head = format!(
            "R_Date({},{},{})",
            quote(name),
            number(m.age_bp),
            number(m.error_1sigma)
        );
        let mut inner = Vec::new();
        if let Some(prior) = m.outlier_prior.filter(|p| *p != 0.0) {
            let model = title(
                m.outlier_model
                    .as_deref()
                    .filter(|s| !s.is_empty())
                    .unwrap_or("general"),
            );
            inner.push(format!(
                "Outlier({},{});",
                quote(Some(&model)),
                number(prior)
            ));
        }
        if depth && let Some(z) = m.depth_cm {
            inner.push(format!("z={};", number(z)));
        }
        if inner.is_empty() {
            self.line(&format!("{head};"));
        } else {
            self.line(&head);
            self.line("{");
            for text in inner {
                self.line(&format!("  {text}"));
            }
            self.line("};");
        }
        // Python writes None for a date with no name.
        let label = name.unwrap_or("None");
        let mut limits = Vec::new();
        if let Some(tpq) = m.tpq_age_bp {
            limits.push(format!("TPQ {} cal BP", number(tpq)));
        }
        if let Some(taq) = m.taq_age_bp {
            limits.push(format!("TAQ {} cal BP", number(taq)));
        }
        if !limits.is_empty() {
            self.omitted
                .push(format!("Date {label}: {}", limits.join(", ")));
        }
        if let Some(years) = m.inbuilt_years.filter(|y| *y != 0.0) {
            self.omitted.push(format!(
                "Date {label}: inbuilt age up to {} years ({})",
                number(years),
                m.inbuilt_shape
                    .as_deref()
                    .filter(|s| !s.is_empty())
                    .unwrap_or("uniform")
            ));
        }
    }

    fn dates_of(&self, element_id: &str) -> Vec<usize> {
        self.by_element.get(element_id).cloned().unwrap_or_default()
    }

    // -- structure --------------------------------------------------------

    fn element(&mut self, element: &Element) {
        let etype = element.element_type.as_str();
        let name = element.name.clone().unwrap_or_default();
        let mut limits = Vec::new();
        if let Some(tpq) = element.tpq_age_bp {
            limits.push(format!("TPQ {} cal BP", number(tpq)));
        }
        if let Some(taq) = element.taq_age_bp {
            limits.push(format!("TAQ {} cal BP", number(taq)));
        }
        if !limits.is_empty() {
            self.omitted.push(format!(
                "{} {name}: {}",
                capitalize(&etype.replace('_', " ")),
                limits.join(", ")
            ));
        }
        match etype {
            "boundary" => self.line(&format!("Boundary({});", quote(Some(&name)))),
            "hiatus" => self.line(&format!(
                "/* Hiatus {}: in OxCal the gap is the two Boundaries either side of it. */",
                comment(&name)
            )),
            "sequence" => {
                self.open(&format!("Sequence({})", quote(Some(&name))));
                for child in &element.children {
                    self.element(child);
                }
                for i in self.dates_of(&element.id) {
                    {
                        let ms = self.measurements;
                        self.date(&ms[i], false);
                    }
                }
                self.close();
            }
            "phase" => {
                self.open(&format!("Phase({})", quote(Some(&name))));
                for i in self.dates_of(&element.id) {
                    {
                        let ms = self.measurements;
                        self.date(&ms[i], false);
                    }
                }
                for child in &element.children {
                    self.element(child);
                }
                self.close();
            }
            "wiggle_sequence" => self.wiggle(element),
            "deposition_sequence" => self.deposition(element),
            _ => self
                .omitted
                .push(format!("{etype} {name}: no OxCal equivalent")),
        }
    }

    /// A wiggle match, oldest member first with the Gap() between each pair,
    /// the gaps' errors recovered from the differences of their squares.
    fn wiggle(&mut self, element: &Element) {
        let name = element.given_name().unwrap_or("Wiggle match").to_owned();
        let mut members = self.dates_of(&element.id);
        let ms = self.measurements;
        let offset = |i: usize| ms[i].ring_offset.unwrap_or(0.0);
        members.sort_by(|&x, &y| {
            (-offset(x))
                .partial_cmp(&-offset(y))
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let errors: Vec<f64> = members
            .iter()
            .map(|&i| self.measurements[i].ring_offset_error.unwrap_or(0.0))
            .collect();
        let varved = errors.iter().any(|e| *e != 0.0);
        if varved && errors[errors.len() - 1] != 0.0 {
            self.omitted.push(format!(
                "Wiggle match {name}: the youngest member has an offset error of {} years, which a chain of Gap() errors cannot carry",
                number(errors[errors.len() - 1])
            ));
        }
        self.open(&format!(
            "{}({})",
            if varved { "V_Sequence" } else { "D_Sequence" },
            quote(Some(&name))
        ));
        for (index, &i) in members.iter().enumerate() {
            if index > 0 {
                let gap = offset(members[index - 1]) - offset(i);
                if varved {
                    let variance = errors[index - 1].powi(2) - errors[index].powi(2);
                    if variance < 0.0 {
                        self.omitted.push(format!(
                            "Wiggle match {name}: {} has a larger offset error than the member before it, which a chain of gaps cannot express; that gap is written without an error",
                            self.measurements[i].sample_name.as_deref().unwrap_or("None")
                        ));
                    }
                    self.line(&format!(
                        "Gap({},{});",
                        number(gap),
                        number(variance.max(0.0).sqrt())
                    ));
                } else {
                    self.line(&format!("Gap({});", number(gap)));
                }
            }
            {
                let ms = self.measurements;
                self.date(&ms[i], false);
            }
        }
        self.close();
    }

    /// A deposition sequence, deepest level first, each with its z=.
    fn deposition(&mut self, element: &Element) {
        let name = element
            .given_name()
            .unwrap_or("Deposition sequence")
            .to_owned();
        let params = element.params.clone().unwrap_or_default();
        let get = |key: &str| params.get(key).cloned().unwrap_or(Param::Null);
        let head = if get("k_variable").truthy() {
            let bound =
                |key: &str, default: f64| params.get(key).map_or(Some(default), Param::as_f64);
            format!(
                "P_Sequence({},1,1,U({},{}))",
                quote(Some(&name)),
                number(bound("log_k_min", -2.0).unwrap_or(f64::NAN)),
                number(bound("log_k_max", 2.0).unwrap_or(f64::NAN))
            )
        } else {
            let k = if get("k").truthy() {
                get("k").as_f64().unwrap_or(1.0)
            } else {
                1.0
            };
            format!("P_Sequence({},{},1)", quote(Some(&name)), number(k))
        };
        enum Level<'e> {
            Date(usize),
            Undated(&'e Element),
        }
        let mut levels: Vec<(f64, Level)> = Vec::new();
        for i in self.dates_of(&element.id) {
            levels.push((self.measurements[i].depth_cm.unwrap_or(0.0), Level::Date(i)));
        }
        for child in &element.children {
            let depth = child
                .params
                .as_ref()
                .and_then(|p| p.get("depth_cm"))
                .filter(|p| p.truthy())
                .and_then(Param::as_f64)
                .unwrap_or(0.0);
            levels.push((depth, Level::Undated(child)));
        }
        levels.sort_by(|a, b| {
            (-a.0)
                .partial_cmp(&-b.0)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        self.open(&head);
        for (depth, level) in levels {
            match level {
                Level::Date(i) => {
                    let ms = self.measurements;
                    self.date(&ms[i], true);
                }
                Level::Undated(child) => {
                    self.line(&format!("Date({})", quote(child.name.as_deref())));
                    self.line("{");
                    self.line(&format!("  z={};", number(depth)));
                    self.line("};");
                }
            }
        }
        self.close();
    }
}

/// The analysis as an OxCal model file, and what it could not write.
///
/// `measurements` are the analysis's dates in its own order; `tree` its model
/// structure (None for a calibration). `stamp` is the export date as
/// YYYY-MM-DD. `Err` names a curve the library does not have.
// The Python's own arguments, plus the date and the curves it reads from
// globals.
#[allow(clippy::too_many_arguments)]
pub fn write_oxcal(
    name: &str,
    analysis_type: &str,
    measurements: &[Measurement],
    tree: Option<&Element>,
    config: &Config,
    app_commit: Option<&str>,
    stamp: &str,
    library: &Library,
) -> Result<(String, Vec<String>), String> {
    let mut writer = Writer::new(measurements, library);
    if let Some(resolution) = config.resolution.filter(|r| *r != 0.0)
        && analysis_type == "bayesian"
    {
        writer.line("Options()");
        writer.line("{");
        writer.line(&format!("  Resolution={};", resolution.trunc()));
        if config.uniform_span_prior == Some(false) {
            writer.line("  UniformSpanPrior=FALSE;");
        }
        writer.line("};");
    }
    writer.open("Plot()");
    let mut models: Vec<String> = measurements
        .iter()
        .filter(|m| m.outlier_prior.is_some_and(|p| p != 0.0))
        .map(|m| {
            m.outlier_model
                .as_deref()
                .filter(|s| !s.is_empty())
                .unwrap_or("general")
                .to_lowercase()
        })
        .collect();
    models.sort();
    models.dedup();
    for model in models {
        writer.line(outlier_model_statement(&model));
    }
    let curve_ids: Vec<String> = measurements
        .iter()
        .map(|m| {
            m.curve_id
                .clone()
                .filter(|c| !c.is_empty())
                .unwrap_or_else(|| "intcal20".to_owned())
        })
        .collect();
    writer.prepare_curves(&curve_ids)?;
    writer.declare_offsets();

    match tree {
        Some(tree) if analysis_type == "bayesian" => {
            writer.element(tree);
            let loose: Vec<&str> = measurements
                .iter()
                .filter(|m| m.element_id.as_deref().is_none_or(str::is_empty))
                .map(|m| m.sample_name.as_deref().unwrap_or("None"))
                .collect();
            if !loose.is_empty() {
                writer.omitted.push(format!(
                    "{} date(s) not placed in the model: {}",
                    loose.len(),
                    loose.join(", ")
                ));
            }
        }
        _ => {
            for m in measurements {
                writer.date(m, false);
            }
        }
    }
    writer.close();

    let mut header = vec![
        "/*".to_owned(),
        format!("  {}", comment(name)),
        format!(
            "  Exported from RadiCal{} on {stamp}.",
            app_commit.map_or_else(String::new, |c| format!(" ({c})"))
        ),
    ];
    if writer
        .curve_names
        .iter()
        .any(|(id, _)| !BUILTINS.iter().any(|b| b.id == id))
    {
        header.push(
            "  Curves other than IntCal20, SHCal20 and Marine20 must be loaded into OxCal under the file names given."
                .to_owned(),
        );
    }
    if !writer.omitted.is_empty() {
        header.push("  Not written, because OxCal's language here cannot say it:".to_owned());
        header.extend(
            writer
                .omitted
                .iter()
                .map(|item| format!("    - {}", comment(item))),
        );
    }
    header.push("*/".to_owned());
    header.extend(writer.lines);
    Ok((format!("{}\n", header.join("\n")), writer.omitted))
}
