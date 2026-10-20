//! Python's own behaviour where the importers lean on it: `float()` on a
//! string, and text decoding as `bytes.decode` does it.

/// Whether Python's `str.isspace` holds for `c`: Rust's whitespace, plus the
/// four ASCII separators 0x1C-0x1F that Python also counts.
pub fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python's `str.strip()`.
pub fn strip(text: &str) -> &str {
    text.trim_matches(is_space)
}

/// Python's `str.splitlines()`: split at every line boundary Python
/// recognises, `\r\n` counting as one, the boundaries dropped.
pub fn splitlines(text: &str) -> Vec<&str> {
    let is_break = |c: char| {
        matches!(
            c,
            '\n' | '\r'
                | '\u{0b}'
                | '\u{0c}'
                | '\u{1c}'
                | '\u{1d}'
                | '\u{1e}'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        )
    };
    let mut out = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        if is_break(c) {
            out.push(&text[start..i]);
            let mut end = i + c.len_utf8();
            if c == '\r' && chars.peek().is_some_and(|&(_, n)| n == '\n') {
                chars.next();
                end += 1;
            }
            start = end;
        }
    }
    if start < text.len() {
        out.push(&text[start..]);
    }
    out
}

/// Python's `float(text)`, or None where it raises ValueError: surrounding
/// whitespace allowed, an optional sign, `inf`/`infinity`/`nan` in any case,
/// and single underscores between digits ("1_000").
pub fn float(text: &str) -> Option<f64> {
    let t = strip(text);
    if t.is_empty() {
        return None;
    }
    let (sign, body) = match t.as_bytes()[0] {
        b'+' => (1.0, &t[1..]),
        b'-' => (-1.0, &t[1..]),
        _ => (1.0, t),
    };
    if body.starts_with(['+', '-']) {
        return None;
    }
    match body.to_ascii_lowercase().as_str() {
        "inf" | "infinity" => return Some(sign * f64::INFINITY),
        "nan" => return Some(f64::NAN),
        _ => {}
    }
    // Underscores only between two digits.
    let bytes = body.as_bytes();
    for (i, &b) in bytes.iter().enumerate() {
        if b == b'_' {
            let before = i > 0 && bytes[i - 1].is_ascii_digit();
            let after = bytes.get(i + 1).is_some_and(u8::is_ascii_digit);
            if !(before && after) {
                return None;
            }
        } else if !(b.is_ascii_digit() || matches!(b, b'.' | b'e' | b'E' | b'+' | b'-')) {
            return None;
        }
    }
    let cleaned: String = body.chars().filter(|&c| c != '_').collect();
    // Rust accepts what Python does for the remaining forms, and nothing
    // more once letters other than e are excluded above.
    cleaned.parse::<f64>().ok().map(|v| sign * v)
}

/// `bytes.decode('cp1252')`: Latin-1 with the 0x80-0x9F block remapped,
/// failing on the five bytes Windows-1252 leaves undefined.
pub fn decode_cp1252(bytes: &[u8]) -> Option<String> {
    const HIGH: [Option<char>; 32] = [
        Some('€'),
        None,
        Some('‚'),
        Some('ƒ'),
        Some('„'),
        Some('…'),
        Some('†'),
        Some('‡'),
        Some('ˆ'),
        Some('‰'),
        Some('Š'),
        Some('‹'),
        Some('Œ'),
        None,
        Some('Ž'),
        None,
        None,
        Some('‘'),
        Some('’'),
        Some('“'),
        Some('”'),
        Some('•'),
        Some('–'),
        Some('—'),
        Some('˜'),
        Some('™'),
        Some('š'),
        Some('›'),
        Some('œ'),
        None,
        Some('ž'),
        Some('Ÿ'),
    ];
    bytes
        .iter()
        .map(|&b| match b {
            0x80..=0x9F => HIGH[(b - 0x80) as usize],
            _ => Some(char::from(b)),
        })
        .collect()
}

/// `bytes.decode('utf-8-sig')`: strict UTF-8, a leading byte-order mark
/// dropped.
pub fn decode_utf8_sig(bytes: &[u8]) -> Option<String> {
    let text = std::str::from_utf8(bytes).ok()?;
    Some(text.strip_prefix('\u{feff}').unwrap_or(text).to_owned())
}

/// Whether Python's `str.isprintable` holds for `c`: everything but the
/// control, format, separator (bar the space), private-use and surrogate
/// categories. Rust has no category table, so the format and separator
/// characters are listed; unassigned code points, which Python also counts
/// unprintable, are not known here and print as themselves.
pub fn is_printable(c: char) -> bool {
    let u = u32::from(c);
    !(c.is_control()
        || matches!(
            u,
            0xa0 | 0xad | 0x600
                ..=0x605 | 0x61c | 0x6dd | 0x70f | 0x890 | 0x891 | 0x8e2 | 0x1680 | 0x180e
        )
        || matches!(u, 0x2000..=0x200f | 0x2028..=0x202f | 0x205f..=0x2064 | 0x2066..=0x206f | 0x3000)
        || matches!(u, 0xfeff | 0xfff9..=0xfffb | 0x110bd | 0x110cd | 0x13430..=0x1343f)
        || matches!(u, 0x1bca0..=0x1bca3 | 0x1d173..=0x1d17a | 0xe0001 | 0xe0020..=0xe007f)
        || matches!(u, 0xe000..=0xf8ff | 0xf0000..=0xffffd | 0x100000..=0x10fffd))
}

/// Python's `repr(text)` for a string.
pub fn repr_str(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if is_printable(c) => out.push(c),
            c => {
                let u = u32::from(c);
                if u <= 0xff {
                    out.push_str(&format!("\\x{u:02x}"));
                } else if u <= 0xffff {
                    out.push_str(&format!("\\u{u:04x}"));
                } else {
                    out.push_str(&format!("\\U{u:08x}"));
                }
            }
        }
    }
    out.push(quote);
    out
}

/// Python's `repr(None)` or `repr(text)`.
pub fn repr_opt(text: Option<&str>) -> String {
    text.map_or_else(|| "None".to_owned(), repr_str)
}

/// Python's `repr(items)` for a list of strings.
pub fn repr_list(items: &[impl AsRef<str>]) -> String {
    let inner: Vec<String> = items.iter().map(|s| repr_str(s.as_ref())).collect();
    format!("[{}]", inner.join(", "))
}

/// Python's `repr(data)` for bytes, without the `b'` and `'` around it.
pub fn repr_bytes_inner(data: &[u8]) -> String {
    let quote = if data.contains(&b'\'') && !data.contains(&b'"') {
        b'"'
    } else {
        b'\''
    };
    let mut out = String::new();
    for &b in data {
        match b {
            b'\\' => out.push_str("\\\\"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            b'\t' => out.push_str("\\t"),
            b if b == quote => {
                out.push('\\');
                out.push(char::from(b));
            }
            0x20..=0x7e => out.push(char::from(b)),
            b => out.push_str(&format!("\\x{b:02x}")),
        }
    }
    out
}

/// Python's `int(text)` in base 10, or the ValueError's message.
pub fn int(text: &str) -> Result<i64, String> {
    let fail = || format!("invalid literal for int() with base 10: {}", repr_str(text));
    let t = strip(text);
    let (negative, digits) = match t.as_bytes().first() {
        Some(b'-') => (true, &t[1..]),
        Some(b'+') => (false, &t[1..]),
        _ => (false, t),
    };
    let bytes = digits.as_bytes();
    if bytes.is_empty()
        || bytes[0] == b'_'
        || bytes[bytes.len() - 1] == b'_'
        || digits.contains("__")
    {
        return Err(fail());
    }
    let mut value: i64 = 0;
    for &b in bytes {
        match b {
            b'_' => {}
            b'0'..=b'9' => {
                value = value
                    .checked_mul(10)
                    .and_then(|v| v.checked_add(i64::from(b - b'0')))
                    .ok_or_else(fail)?;
            }
            _ => return Err(fail()),
        }
    }
    Ok(if negative { -value } else { value })
}

/// Python's `float(text)`, or the ValueError's message.
pub fn float_or_error(text: &str) -> Result<f64, String> {
    float(text).ok_or_else(|| format!("could not convert string to float: {}", repr_str(text)))
}

/// Python's `html.unescape`.
pub fn html_unescape(text: &str) -> String {
    use crate::html_entities::{INVALID_CHARREFS, INVALID_CODEPOINTS, NAMED};
    use std::sync::LazyLock;
    static CHARREF: LazyLock<regex::Regex> = LazyLock::new(|| {
        regex::Regex::new(r"&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\x0c <&#;]{1,32};?)").unwrap()
    });
    let named = |name: &str| {
        NAMED
            .binary_search_by(|(k, _)| (*k).cmp(name))
            .ok()
            .map(|i| NAMED[i].1)
    };
    if !text.contains('&') {
        return text.to_owned();
    }
    CHARREF
        .replace_all(text, |caps: &regex::Captures| {
            let s = &caps[1];
            if let Some(number) = s.strip_prefix('#') {
                let (digits, radix) = match number.strip_prefix(['x', 'X']) {
                    Some(hex) => (hex, 16),
                    None => (number, 10),
                };
                let digits = digits.trim_end_matches(';');
                // Saturating: anything past 0x10FFFF reads the same.
                let num = digits.chars().fold(0u64, |acc, d| {
                    acc.saturating_mul(radix)
                        .saturating_add(u64::from(d.to_digit(radix as u32).unwrap()))
                });
                if let Ok(small) = u32::try_from(num)
                    && let Some((_, replacement)) =
                        INVALID_CHARREFS.iter().find(|(k, _)| *k == small)
                {
                    return (*replacement).to_owned();
                }
                if (0xd800..=0xdfff).contains(&num) || num > 0x10ffff {
                    return "\u{fffd}".to_owned();
                }
                let num = num as u32;
                if INVALID_CODEPOINTS.contains(&num) {
                    return String::new();
                }
                return char::from_u32(num).map(String::from).unwrap_or_default();
            }
            if let Some(replacement) = named(s) {
                return replacement.to_owned();
            }
            // The longest name that prefixes it, as the standard defines.
            let bounds: Vec<usize> = s.char_indices().map(|(i, _)| i).skip(2).collect();
            for &end in bounds.iter().rev() {
                if let Some(replacement) = named(&s[..end]) {
                    return format!("{replacement}{}", &s[end..]);
                }
            }
            format!("&{s}")
        })
        .into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn float_is_pythons() {
        for (text, want) in [
            ("3000", Some(3000.0)),
            (" 12.5 ", Some(12.5)),
            ("1_000", Some(1000.0)),
            ("1__0", None),
            ("_1", None),
            ("1e3", Some(1000.0)),
            ("-.5", Some(-0.5)),
            ("5.", Some(5.0)),
            ("+7", Some(7.0)),
            ("Infinity", Some(f64::INFINITY)),
            ("-inf", Some(f64::NEG_INFINITY)),
            ("three", None),
            ("1,000", None),
            ("", None),
            ("0x10", None),
            ("+-5", None),
        ] {
            assert_eq!(float(text), want, "{text:?}");
        }
        assert!(float("NaN").unwrap().is_nan());
    }

    #[test]
    fn cp1252_is_windows() {
        assert_eq!(decode_cp1252(b"caf\xe9 \x80").as_deref(), Some("café €"));
        assert_eq!(decode_cp1252(b"\x81"), None);
    }
}
