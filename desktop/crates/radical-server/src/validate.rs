//! Request bodies and queries checked as FastAPI and Pydantic v2 check them,
//! and refused in the same words: a 422 listing every problem, field by
//! field in the model's declaration order, then the model's own checks only
//! when every field passed.
//!
//! Pydantic's lax mode is the one a JSON body is validated in here: a
//! numeric string is a number, `1` and `"yes"` are true, but a number is
//! never a string. A handler reads a model's fields in the order the Python
//! model declares them, so the problems come out in the same order.

use axum::body::Bytes;
use serde_json::{Map, Value, json};

use crate::ApiError;

/// The Pydantic release the backend's error URLs name.
const PYDANTIC: &str = "2.13";

/// One problem, as Pydantic's `errors()` lists it and FastAPI sends it.
pub fn problem(kind: &str, loc: &[Value], msg: &str, input: Value, ctx: Option<Value>) -> Value {
    let mut out = json!({"type": kind, "loc": loc, "msg": msg, "input": input});
    if let Some(ctx) = ctx {
        out["ctx"] = ctx;
    }
    out["url"] = Value::from(format!("https://errors.pydantic.dev/{PYDANTIC}/v/{kind}"));
    out
}

/// A request body as JSON: 422 when it is missing or not JSON at all, as
/// FastAPI answers.
pub fn json_body(bytes: &Bytes) -> Result<Value, ApiError> {
    if bytes.iter().all(u8::is_ascii_whitespace) {
        return Err(ApiError::invalid(vec![problem(
            "missing",
            &[json!("body")],
            "Field required",
            Value::Null,
            None,
        )]));
    }
    serde_json::from_slice(bytes).map_err(|e| {
        let position = byte_offset(bytes, e.line(), e.column());
        ApiError::invalid(vec![problem(
            "json_invalid",
            &[json!("body"), json!(position)],
            "JSON decode error",
            json!({}),
            Some(json!({"error": "Expecting value"})),
        )])
    })
}

fn byte_offset(bytes: &[u8], line: usize, column: usize) -> usize {
    let mut offset = 0;
    for (i, l) in bytes.split(|b| *b == b'\n').enumerate() {
        if i + 1 == line {
            return offset + column.saturating_sub(1);
        }
        offset += l.len() + 1;
    }
    offset
}

/// Numeric bounds, in the order Pydantic checks them: le, lt, ge, gt.
#[derive(Debug, Clone, Copy, Default)]
pub struct Bounds {
    pub ge: Option<f64>,
    pub gt: Option<f64>,
    pub le: Option<f64>,
    pub lt: Option<f64>,
}

impl Bounds {
    pub const NONE: Self = Self {
        ge: None,
        gt: None,
        le: None,
        lt: None,
    };

    pub const fn range(ge: f64, le: f64) -> Self {
        Self {
            ge: Some(ge),
            gt: None,
            le: Some(le),
            lt: None,
        }
    }

    pub const fn above(gt: f64, le: f64) -> Self {
        Self {
            ge: None,
            gt: Some(gt),
            le: Some(le),
            lt: None,
        }
    }
}

/// How a bound prints in a message: as a whole number when it is one.
fn bound_text(x: f64) -> String {
    if x.fract() == 0.0 && x.abs() < 1e15 {
        format!("{}", x as i64)
    } else {
        format!("{x}")
    }
}

/// A bound, its Pydantic error type, its words, and the test it sets.
type Check = (
    Option<f64>,
    &'static str,
    &'static str,
    fn(f64, f64) -> bool,
);

/// The problems found so far, for one request.
#[derive(Default)]
pub struct Problems(pub Vec<Value>);

impl Problems {
    pub fn finish(self) -> Result<(), ApiError> {
        if self.0.is_empty() {
            Ok(())
        } else {
            Err(ApiError::invalid(self.0))
        }
    }
}

/// One model's fields, being read.
pub struct Fields<'a, 'p> {
    input: &'a Value,
    map: Option<&'a Map<String, Value>>,
    loc: Vec<Value>,
    problems: &'p mut Problems,
    /// How many problems there were before this model, so its own checks
    /// run only when none of its fields failed.
    start: usize,
}

impl<'a, 'p> Fields<'a, 'p> {
    /// The model at `loc` (e.g. `["body"]`); a model_type problem when it is
    /// not an object.
    pub fn new(
        input: &'a Value,
        loc: Vec<Value>,
        class_name: &str,
        problems: &'p mut Problems,
    ) -> Self {
        let start = problems.0.len();
        let map = input.as_object();
        if map.is_none() {
            problems.0.push(problem(
                "model_type",
                &loc,
                &format!("Input should be a valid dictionary or instance of {class_name}"),
                input.clone(),
                Some(json!({"class_name": class_name})),
            ));
        }
        Self {
            input,
            map,
            loc,
            problems,
            start,
        }
    }

    fn at(&self, name: &str) -> Vec<Value> {
        let mut loc = self.loc.clone();
        loc.push(Value::from(name));
        loc
    }

    fn push(&mut self, kind: &str, name: &str, msg: &str, input: &Value, ctx: Option<Value>) {
        let loc = self.at(name);
        self.problems
            .0
            .push(problem(kind, &loc, msg, input.clone(), ctx));
    }

    /// Whether the body sent this field at all (`model_fields_set`).
    pub fn sent(&self, name: &str) -> bool {
        self.map.is_some_and(|m| m.contains_key(name))
    }

    /// The raw value sent, if any.
    pub fn raw(&self, name: &str) -> Option<&'a Value> {
        self.map.and_then(|m| m.get(name))
    }

    /// Whether every field read so far passed.
    pub fn clean(&self) -> bool {
        self.problems.0.len() == self.start
    }

    fn missing(&mut self, name: &str) {
        let input = self.input.clone();
        self.push("missing", name, "Field required", &input, None);
    }

    /// A field validator's or a model validator's refusal: `Value error,
    /// ...`. `name` None is the model's own.
    pub fn value_error(&mut self, name: Option<&str>, message: &str) {
        let (loc, input) = match name {
            Some(n) => (self.at(n), self.raw(n).cloned().unwrap_or(Value::Null)),
            None => (self.loc.clone(), self.input.clone()),
        };
        self.problems.0.push(problem(
            "value_error",
            &loc,
            &format!("Value error, {message}"),
            input,
            Some(json!({"error": {}})),
        ));
    }

    // -- numbers ---------------------------------------------------------

    fn number(&mut self, name: &str, v: &Value, integer: bool) -> Option<f64> {
        let parsed = match v {
            Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
            Value::Number(n) => n.as_f64(),
            Value::String(s) => {
                let t = s.trim();
                let x = if integer {
                    t.parse::<i64>()
                        .map(|i| i as f64)
                        .ok()
                        .or_else(|| t.parse::<f64>().ok().filter(|f| f.fract() == 0.0))
                } else {
                    t.parse::<f64>().ok()
                };
                if x.is_none() {
                    let (kind, msg) = if integer {
                        (
                            "int_parsing",
                            "Input should be a valid integer, unable to parse string as an integer",
                        )
                    } else {
                        (
                            "float_parsing",
                            "Input should be a valid number, unable to parse string as a number",
                        )
                    };
                    self.push(kind, name, msg, v, None);
                    return None;
                }
                x
            }
            _ => None,
        };
        let Some(x) = parsed else {
            let (kind, msg) = if integer {
                ("int_type", "Input should be a valid integer")
            } else {
                ("float_type", "Input should be a valid number")
            };
            self.push(kind, name, msg, v, None);
            return None;
        };
        if integer && x.fract() != 0.0 {
            self.push(
                "int_from_float",
                name,
                "Input should be a valid integer, got a number with a fractional part",
                v,
                None,
            );
            return None;
        }
        Some(x)
    }

    fn bounded(&mut self, name: &str, v: &Value, x: f64, b: Bounds, integer: bool) -> Option<f64> {
        let ctx = |key: &str, bound: f64| {
            if integer {
                json!({key: bound as i64})
            } else {
                json!({key: bound})
            }
        };
        let checks: [Check; 4] = [
            (b.le, "less_than_equal", "less than or equal to", |x, y| {
                x <= y
            }),
            (b.lt, "less_than", "less than", |x, y| x < y),
            (
                b.ge,
                "greater_than_equal",
                "greater than or equal to",
                |x, y| x >= y,
            ),
            (b.gt, "greater_than", "greater than", |x, y| x > y),
        ];
        for (bound, kind, words, holds) in checks {
            if let Some(bound) = bound
                && !holds(x, bound)
            {
                let key = match kind {
                    "less_than_equal" => "le",
                    "less_than" => "lt",
                    "greater_than_equal" => "ge",
                    _ => "gt",
                };
                self.push(
                    kind,
                    name,
                    &format!("Input should be {words} {}", bound_text(bound)),
                    v,
                    Some(ctx(key, bound)),
                );
                return None;
            }
        }
        Some(x)
    }

    /// An `Optional[float]` with bounds: None when absent or null.
    pub fn opt_float(&mut self, name: &str, b: Bounds) -> Option<f64> {
        let v = self.raw(name).filter(|v| !v.is_null())?;
        let x = self.number(name, v, false)?;
        self.bounded(name, v, x, b, false)
    }

    /// A required `float`.
    pub fn float(&mut self, name: &str, b: Bounds) -> Option<f64> {
        let Some(v) = self.raw(name) else {
            self.missing(name);
            return None;
        };
        let x = self.number(name, v, false)?;
        self.bounded(name, v, x, b, false)
    }

    /// A `float` with a default.
    pub fn float_or(&mut self, name: &str, default: f64, b: Bounds) -> Option<f64> {
        let Some(v) = self.raw(name) else {
            return Some(default);
        };
        let x = self.number(name, v, false)?;
        self.bounded(name, v, x, b, false)
    }

    /// An `int` with a default.
    pub fn int_or(&mut self, name: &str, default: i64, b: Bounds) -> Option<i64> {
        let Some(v) = self.raw(name) else {
            return Some(default);
        };
        let x = self.number(name, v, true)?;
        self.bounded(name, v, x, b, true).map(|x| x as i64)
    }

    /// An `Optional[int]`.
    pub fn opt_int(&mut self, name: &str, b: Bounds) -> Option<i64> {
        let v = self.raw(name).filter(|v| !v.is_null())?;
        let x = self.number(name, v, true)?;
        self.bounded(name, v, x, b, true).map(|x| x as i64)
    }

    // -- text ------------------------------------------------------------

    fn text(
        &mut self,
        name: &str,
        v: &Value,
        max: Option<usize>,
        min: Option<usize>,
    ) -> Option<String> {
        let Value::String(s) = v else {
            self.push(
                "string_type",
                name,
                "Input should be a valid string",
                v,
                None,
            );
            return None;
        };
        let n = s.chars().count();
        if let Some(min) = min
            && n < min
        {
            let msg = format!(
                "String should have at least {min} character{}",
                if min == 1 { "" } else { "s" }
            );
            self.push(
                "string_too_short",
                name,
                &msg,
                v,
                Some(json!({"min_length": min})),
            );
            return None;
        }
        if let Some(max) = max
            && n > max
        {
            let msg = format!(
                "String should have at most {max} character{}",
                if max == 1 { "" } else { "s" }
            );
            self.push(
                "string_too_long",
                name,
                &msg,
                v,
                Some(json!({"max_length": max})),
            );
            return None;
        }
        Some(s.clone())
    }

    /// An `Optional[str]` with a max length.
    pub fn opt_str(&mut self, name: &str, max: Option<usize>) -> Option<String> {
        let v = self.raw(name).filter(|v| !v.is_null())?;
        self.text(name, v, max, None)
    }

    /// A `str` with a default.
    pub fn str_or(&mut self, name: &str, default: &str, max: Option<usize>) -> Option<String> {
        match self.raw(name) {
            None => Some(default.to_owned()),
            Some(v) => self.text(name, v, max, None),
        }
    }

    /// A required `str`, with optional length limits.
    pub fn string(&mut self, name: &str, min: Option<usize>, max: Option<usize>) -> Option<String> {
        let Some(v) = self.raw(name) else {
            self.missing(name);
            return None;
        };
        self.text(name, v, max, min)
    }

    // -- choices ---------------------------------------------------------

    fn literal(&mut self, name: &str, v: &Value, allowed: &[&str]) -> Option<String> {
        if let Some(s) = v.as_str().filter(|s| allowed.contains(s)) {
            return Some(s.to_owned());
        }
        let expected = match allowed {
            [one] => format!("'{one}'"),
            [init @ .., last] => format!(
                "{} or '{last}'",
                init.iter()
                    .map(|a| format!("'{a}'"))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
            [] => String::new(),
        };
        self.push(
            "literal_error",
            name,
            &format!("Input should be {expected}"),
            v,
            Some(json!({"expected": expected})),
        );
        None
    }

    /// An `Optional[Literal[...]]`.
    pub fn opt_literal(&mut self, name: &str, allowed: &[&str]) -> Option<String> {
        let v = self.raw(name).filter(|v| !v.is_null())?;
        self.literal(name, v, allowed)
    }

    /// A required `Literal[...]`.
    pub fn literal_req(&mut self, name: &str, allowed: &[&str]) -> Option<String> {
        let Some(v) = self.raw(name) else {
            self.missing(name);
            return None;
        };
        self.literal(name, v, allowed)
    }

    /// A `Literal[...]` with a default.
    pub fn literal_or(&mut self, name: &str, allowed: &[&str], default: &str) -> Option<String> {
        match self.raw(name) {
            None => Some(default.to_owned()),
            Some(v) => self.literal(name, v, allowed),
        }
    }

    fn boolean(&mut self, name: &str, v: &Value) -> Option<bool> {
        match v {
            Value::Bool(b) => Some(*b),
            Value::Number(n) if n.as_f64() == Some(1.0) => Some(true),
            Value::Number(n) if n.as_f64() == Some(0.0) => Some(false),
            Value::String(s) => match s.trim().to_lowercase().as_str() {
                "1" | "on" | "t" | "true" | "y" | "yes" => Some(true),
                "0" | "off" | "f" | "false" | "n" | "no" => Some(false),
                _ => {
                    self.push(
                        "bool_parsing",
                        name,
                        "Input should be a valid boolean, unable to interpret input",
                        v,
                        None,
                    );
                    None
                }
            },
            Value::Number(_) => {
                self.push(
                    "bool_parsing",
                    name,
                    "Input should be a valid boolean, unable to interpret input",
                    v,
                    None,
                );
                None
            }
            _ => {
                self.push(
                    "bool_type",
                    name,
                    "Input should be a valid boolean",
                    v,
                    None,
                );
                None
            }
        }
    }

    /// An `Optional[bool]`.
    pub fn opt_bool(&mut self, name: &str) -> Option<bool> {
        let v = self.raw(name).filter(|v| !v.is_null())?;
        self.boolean(name, v)
    }

    /// A `bool` with a default.
    pub fn bool_or(&mut self, name: &str, default: bool) -> Option<bool> {
        match self.raw(name) {
            None => Some(default),
            Some(v) => self.boolean(name, v),
        }
    }

    // -- containers ------------------------------------------------------

    /// An `Optional[Dict[str, Any]]`.
    pub fn opt_dict(&mut self, name: &str) -> Option<Map<String, Value>> {
        let v = self.raw(name).filter(|v| !v.is_null())?;
        match v {
            Value::Object(m) => Some(m.clone()),
            _ => {
                self.push(
                    "dict_type",
                    name,
                    "Input should be a valid dictionary",
                    v,
                    None,
                );
                None
            }
        }
    }

    fn list_length(
        &mut self,
        name: &str,
        v: &Value,
        len: usize,
        min: Option<usize>,
        max: Option<usize>,
    ) -> bool {
        if let Some(min) = min
            && len < min
        {
            let msg = format!(
                "List should have at least {min} item{} after validation, not {len}",
                if min == 1 { "" } else { "s" }
            );
            self.push(
                "too_short",
                name,
                &msg,
                v,
                Some(json!({"field_type": "List", "min_length": min, "actual_length": len})),
            );
            return false;
        }
        if let Some(max) = max
            && len > max
        {
            let msg = format!(
                "List should have at most {max} item{} after validation, not {len}",
                if max == 1 { "" } else { "s" }
            );
            self.push(
                "too_long",
                name,
                &msg,
                v,
                Some(json!({"field_type": "List", "max_length": max, "actual_length": len})),
            );
            return false;
        }
        true
    }

    /// A required `List[str]` with length limits.
    pub fn str_list(
        &mut self,
        name: &str,
        min: Option<usize>,
        max: Option<usize>,
    ) -> Option<Vec<String>> {
        let Some(v) = self.raw(name) else {
            self.missing(name);
            return None;
        };
        let Value::Array(items) = v else {
            self.push("list_type", name, "Input should be a valid list", v, None);
            return None;
        };
        let mut out = Vec::new();
        let mut ok = true;
        for (i, item) in items.iter().enumerate() {
            match item.as_str() {
                Some(s) => out.push(s.to_owned()),
                None => {
                    ok = false;
                    let mut loc = self.at(name);
                    loc.push(Value::from(i));
                    self.problems.0.push(problem(
                        "string_type",
                        &loc,
                        "Input should be a valid string",
                        item.clone(),
                        None,
                    ));
                }
            }
        }
        if !ok || !self.list_length(name, v, items.len(), min, max) {
            return None;
        }
        Some(out)
    }

    /// A required list of models, each read by `item`, with length limits.
    pub fn models<T>(
        &mut self,
        name: &str,
        class_name: &str,
        min: Option<usize>,
        max: Option<usize>,
        mut item: impl FnMut(&mut Fields) -> Option<T>,
    ) -> Option<Vec<T>> {
        let Some(v) = self.raw(name) else {
            self.missing(name);
            return None;
        };
        let Value::Array(items) = v else {
            self.push("list_type", name, "Input should be a valid list", v, None);
            return None;
        };
        let mut out = Vec::new();
        let mut ok = true;
        for (i, element) in items.iter().enumerate() {
            let mut loc = self.at(name);
            loc.push(Value::from(i));
            let mut fields = Fields::new(element, loc, class_name, self.problems);
            if fields.map.is_none() {
                ok = false;
                continue;
            }
            match item(&mut fields) {
                Some(t) if fields.clean() => out.push(t),
                _ => ok = false,
            }
        }
        if !ok || !self.list_length(name, v, items.len(), min, max) {
            return None;
        }
        Some(out)
    }

    /// A list of models with an empty default (`default_factory=list`).
    pub fn models_or<T>(
        &mut self,
        name: &str,
        class_name: &str,
        item: impl FnMut(&mut Fields) -> Option<T>,
    ) -> Option<Vec<T>> {
        if self.raw(name).is_none() {
            return Some(Vec::new());
        }
        self.models(name, class_name, None, None, item)
    }

    /// A nested model: `Some(None)` when absent (or null, where
    /// `nullable`), None when it failed.
    pub fn model<T>(
        &mut self,
        name: &str,
        class_name: &str,
        nullable: bool,
        item: impl FnOnce(&mut Fields) -> Option<T>,
    ) -> Option<Option<T>> {
        let Some(v) = self.raw(name).filter(|v| !(nullable && v.is_null())) else {
            return Some(None);
        };
        let loc = self.at(name);
        let mut fields = Fields::new(v, loc, class_name, self.problems);
        fields.map?;
        match item(&mut fields) {
            Some(t) if fields.clean() => Some(Some(t)),
            _ => None,
        }
    }

    /// Whether this model's input was an object at all.
    pub fn is_object(&self) -> bool {
        self.map.is_some()
    }
}

/// The body's model: `Fields` at `["body"]`.
pub fn body<'a, 'p>(
    input: &'a Value,
    class_name: &str,
    problems: &'p mut Problems,
) -> Fields<'a, 'p> {
    Fields::new(input, vec![json!("body")], class_name, problems)
}
