//! radical-io held to the golden corpus: its `units/` families, case by
//! case, against what the Python implementation answered.
//!
//! Imports are compared as the JSON the API returns, exactly, up to the ids:
//! the Python's are random and the corpus writes them as `<id:N>`, so a run
//! matches when one consistent renaming turns the corpus's placeholders into
//! the ids this run made.

mod curve_import;
mod date_import;
mod oxcal_export;
mod oxcal_script;
mod reservoir_ingest;

use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

use radical_corpus::{Case, Family, load_family, load_manifest};
use regex::Regex;
use serde_json::Value;

/// The families compared here. radical-core's own list names them as
/// compared elsewhere; together the two cover every units/ family.
const COVERED: [&str; 5] = [
    "curve_import",
    "date_import",
    "oxcal_export",
    "oxcal_script",
    "reservoir_ingest",
];

#[test]
fn every_family_listed_here_is_in_the_corpus() {
    let listed: BTreeSet<String> = load_manifest().unwrap().files.into_keys().collect();
    for family in COVERED {
        assert!(
            listed.contains(&format!("data/units/{family}.json")),
            "{family}"
        );
    }
}

pub fn family(name: &str) -> Family {
    load_family(&format!("units/{name}")).unwrap()
}

pub fn outputs(case: &Case) -> Value {
    case.expanded_outputs()
        .unwrap_or_else(|| panic!("{}: expected outputs, found {:?}", case.name, case.error))
}

static PLACEHOLDER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<id:(\d+)>").unwrap());

/// Placeholder number -> the id this run used for it, kept one-to-one.
#[derive(Default)]
pub struct IdMap {
    forward: BTreeMap<String, String>,
    taken: BTreeMap<String, String>,
}

impl IdMap {
    fn bind(&mut self, label: &str, n: &str, id: &str) {
        match self.forward.get(n) {
            Some(bound) => assert_eq!(bound, id, "{label}: <id:{n}> is {bound} elsewhere"),
            None => {
                if let Some(other) = self.taken.get(id) {
                    panic!("{label}: {id} is <id:{other}> elsewhere, not <id:{n}>");
                }
                self.forward.insert(n.to_owned(), id.to_owned());
                self.taken.insert(id.to_owned(), n.to_owned());
            }
        }
    }

    /// A corpus string matched against one from this run, binding the
    /// placeholders it holds.
    fn text(&mut self, label: &str, want: &str, got: &str) {
        if !PLACEHOLDER.is_match(want) {
            assert_eq!(got, want, "{label}");
            return;
        }
        let mut pattern = String::from("^");
        let mut last = 0;
        let mut order = Vec::new();
        for c in PLACEHOLDER.captures_iter(want) {
            let m = c.get(0).unwrap();
            pattern.push_str(&regex::escape(&want[last..m.start()]));
            pattern.push_str("([a-z]-[0-9a-f]{12})");
            order.push(c[1].to_owned());
            last = m.end();
        }
        pattern.push_str(&regex::escape(&want[last..]));
        pattern.push('$');
        let caps = Regex::new(&pattern)
            .unwrap()
            .captures(got)
            .unwrap_or_else(|| panic!("{label}: {got:?} is not {want:?}"));
        for (k, n) in order.iter().enumerate() {
            self.bind(label, n, &caps[k + 1]);
        }
    }

    /// A corpus object key as this run spells it, once its placeholder is
    /// bound.
    fn key(&self, want: &str) -> Option<String> {
        let mut unbound = false;
        let out = PLACEHOLDER.replace_all(want, |c: &regex::Captures| {
            self.forward.get(&c[1]).cloned().unwrap_or_else(|| {
                unbound = true;
                String::new()
            })
        });
        (!unbound).then(|| out.into_owned())
    }
}

/// `got` is `want` exactly, ids aside. Keys that are ids are matched once
/// some value has bound them, so values are walked first.
pub fn same_json(label: &str, got: &Value, want: &Value) {
    let mut ids = IdMap::default();
    walk(label, got, want, &mut ids, false);
    walk(label, got, want, &mut ids, true);
}

fn walk(label: &str, got: &Value, want: &Value, ids: &mut IdMap, keys: bool) {
    match (want, got) {
        (Value::String(w), Value::String(g)) => ids.text(label, w, g),
        (Value::Number(w), Value::Number(g)) => {
            let (w, g) = (w.as_f64().unwrap(), g.as_f64().unwrap());
            assert!(w.to_bits() == g.to_bits(), "{label}: {g} != {w}");
        }
        (Value::Array(w), Value::Array(g)) => {
            assert_eq!(g.len(), w.len(), "{label}: lengths differ");
            for (i, (g, w)) in g.iter().zip(w).enumerate() {
                walk(&format!("{label}[{i}]"), g, w, ids, keys);
            }
        }
        (Value::Object(w), Value::Object(g)) => {
            assert_eq!(
                g.len(),
                w.len(),
                "{label}: keys {:?} vs {:?}",
                g.keys(),
                w.keys()
            );
            for (k, wv) in w {
                let Some(gk) = ids.key(k) else {
                    assert!(!keys, "{label}: key {k} never bound");
                    continue;
                };
                let gv = g
                    .get(&gk)
                    .unwrap_or_else(|| panic!("{label}: no key {gk} (corpus {k})"));
                walk(&format!("{label}.{k}"), gv, wv, ids, keys);
            }
        }
        _ => assert_eq!(got, want, "{label}"),
    }
}
