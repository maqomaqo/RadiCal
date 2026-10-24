//! `multipart/form-data` bodies, as FastAPI's `File(...)` and `Form(...)`
//! parameters read them: each part by name, with the filename a file part
//! was sent under.

use serde_json::{Value, json};

use crate::{ApiError, problem};

/// One part of a form.
#[derive(Debug, Clone)]
pub struct Part {
    pub name: String,
    pub filename: Option<String>,
    pub data: Vec<u8>,
}

/// A form's parts, in the order sent.
#[derive(Debug, Clone, Default)]
pub struct Form(pub Vec<Part>);

impl Form {
    /// The parts of `body`, or none when it is not a multipart form.
    pub fn parse(content_type: Option<&str>, body: &[u8]) -> Self {
        let Some(boundary) = content_type.and_then(boundary) else {
            return Self::default();
        };
        let delimiter = format!("--{boundary}").into_bytes();
        let mut parts = Vec::new();
        let mut chunks = split(body, &delimiter).into_iter().skip(1);
        for chunk in chunks.by_ref() {
            if chunk.starts_with(b"--") {
                break;
            }
            let chunk = chunk.strip_prefix(b"\r\n").unwrap_or(chunk);
            let Some(split_at) = find(chunk, b"\r\n\r\n") else {
                continue;
            };
            let head = String::from_utf8_lossy(&chunk[..split_at]);
            let mut data = chunk[split_at + 4..].to_vec();
            if data.ends_with(b"\r\n") {
                data.truncate(data.len() - 2);
            }
            let disposition = head
                .lines()
                .find(|l| l.to_ascii_lowercase().starts_with("content-disposition"))
                .unwrap_or("");
            let Some(name) = param(disposition, "name") else {
                continue;
            };
            parts.push(Part {
                name,
                filename: param(disposition, "filename"),
                data,
            });
        }
        Self(parts)
    }

    fn part(&self, name: &str) -> Option<&Part> {
        self.0.iter().find(|p| p.name == name)
    }

    /// A text field, or None when not sent.
    pub fn text(&self, name: &str) -> Option<String> {
        self.part(name)
            .map(|p| String::from_utf8_lossy(&p.data).into_owned())
    }

    /// A file field, or None when not sent.
    pub fn file(&self, name: &str) -> Option<&Part> {
        self.part(name).filter(|p| p.filename.is_some())
    }
}

/// FastAPI's 422 for required form fields that were not sent.
pub fn missing(names: &[&str]) -> ApiError {
    ApiError::invalid(
        names
            .iter()
            .map(|n| {
                problem(
                    "missing",
                    &[json!("body"), json!(n)],
                    "Field required",
                    Value::Null,
                    None,
                )
            })
            .collect(),
    )
}

fn boundary(content_type: &str) -> Option<String> {
    if !content_type
        .to_ascii_lowercase()
        .starts_with("multipart/form-data")
    {
        return None;
    }
    param(content_type, "boundary")
}

/// A `key=value` or `key="value"` parameter of a header.
fn param(header: &str, key: &str) -> Option<String> {
    header.split(';').skip(1).find_map(|p| {
        let (k, v) = p.trim().split_once('=')?;
        if !k.trim().eq_ignore_ascii_case(key) {
            return None;
        }
        let v = v.trim();
        Some(
            v.strip_prefix('"')
                .and_then(|v| v.strip_suffix('"'))
                .unwrap_or(v)
                .to_owned(),
        )
    })
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|w| w == needle)
}

fn split<'a>(body: &'a [u8], delimiter: &[u8]) -> Vec<&'a [u8]> {
    let mut out = Vec::new();
    let mut rest = body;
    while let Some(at) = find(rest, delimiter) {
        out.push(&rest[..at]);
        rest = &rest[at + delimiter.len()..];
    }
    out.push(rest);
    out
}
