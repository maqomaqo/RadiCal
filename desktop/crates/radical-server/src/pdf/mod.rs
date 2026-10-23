//! A PDF written directly: pages of drawing operators in Flate-compressed
//! content streams, set in the two core Helvetica faces every reader
//! carries, so nothing is embedded. What the analysis report needs and no
//! more -- text, filled and stroked paths, and transparency.
//!
//! Text is laid down as `(...) Tj`, one string per run, in Windows-1252, as
//! ReportLab lays it down; the backend's tests read a report back that way.

pub mod layout;
mod metrics;
pub mod plot;

use std::collections::BTreeSet;
use std::fmt::Write as _;

use miniz_oxide::deflate::compress_to_vec_zlib;

/// Landscape A4, in points.
pub const PAGE_WIDTH: f64 = 841.889_763_779_527_6;
pub const PAGE_HEIGHT: f64 = 595.275_590_551_181_1;
pub const MM: f64 = 72.0 / 25.4;

/// An RGB colour, each channel 0-1.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Color(pub f64, pub f64, pub f64);

/// A colour written as `#rrggbb`.
pub fn hex(s: &str) -> Color {
    let v = s.trim_start_matches('#');
    let c = |i: usize| f64::from(u8::from_str_radix(&v[i..i + 2], 16).unwrap_or(0)) / 255.0;
    Color(c(0), c(2), c(4))
}

/// Text in Windows-1252, the core fonts' encoding. A character they have no
/// glyph for is replaced: sigma by a Latin `s`, as ReportLab substitutes it,
/// anything else by `?`.
pub fn encode(text: &str) -> Vec<u8> {
    text.chars()
        .map(|c| match c {
            'σ' => b's',
            c if (c as u32) < 0x80 => c as u8,
            c => cp1252(c).unwrap_or(b'?'),
        })
        .collect()
}

fn cp1252(c: char) -> Option<u8> {
    const HIGH: [(char, u8); 27] = [
        ('€', 0x80),
        ('‚', 0x82),
        ('ƒ', 0x83),
        ('„', 0x84),
        ('…', 0x85),
        ('†', 0x86),
        ('‡', 0x87),
        ('ˆ', 0x88),
        ('‰', 0x89),
        ('Š', 0x8A),
        ('‹', 0x8B),
        ('Œ', 0x8C),
        ('Ž', 0x8E),
        ('‘', 0x91),
        ('’', 0x92),
        ('“', 0x93),
        ('”', 0x94),
        ('•', 0x95),
        ('–', 0x96),
        ('—', 0x97),
        ('˜', 0x98),
        ('™', 0x99),
        ('š', 0x9A),
        ('›', 0x9B),
        ('œ', 0x9C),
        ('ž', 0x9E),
        ('Ÿ', 0x9F),
    ];
    let code = c as u32;
    if (0xA0..=0xFF).contains(&code) {
        return Some(code as u8);
    }
    HIGH.iter().find(|(h, _)| *h == c).map(|(_, b)| *b)
}

/// How wide a string is set in a core face, in points.
pub fn text_width(text: &str, bold: bool, size: f64) -> f64 {
    let table = if bold {
        &metrics::HELVETICA_BOLD
    } else {
        &metrics::HELVETICA
    };
    let units: u32 = encode(text)
        .iter()
        .map(|&b| u32::from(table[b as usize]))
        .sum();
    f64::from(units) * size / 1000.0
}

fn num(x: f64) -> String {
    let s = format!("{x:.3}");
    let s = s.trim_end_matches('0').trim_end_matches('.');
    if s == "-0" {
        "0".to_owned()
    } else {
        s.to_owned()
    }
}

/// One page's drawing operators.
#[derive(Default)]
pub struct Canvas {
    ops: String,
    /// The transparency states used, as (fill, stroke) alphas in thousandths.
    states: BTreeSet<(u16, u16)>,
}

impl Canvas {
    pub fn save(&mut self) {
        self.ops.push_str("q\n");
    }

    pub fn restore(&mut self) {
        self.ops.push_str("Q\n");
    }

    pub fn fill(&mut self, c: Color) {
        let _ = writeln!(self.ops, "{} {} {} rg", num(c.0), num(c.1), num(c.2));
    }

    pub fn stroke(&mut self, c: Color) {
        let _ = writeln!(self.ops, "{} {} {} RG", num(c.0), num(c.1), num(c.2));
    }

    /// Transparency for what follows, fill and stroke.
    pub fn alpha(&mut self, fill: f64, stroke: f64) {
        let key = (
            (fill.clamp(0.0, 1.0) * 1000.0).round() as u16,
            (stroke.clamp(0.0, 1.0) * 1000.0).round() as u16,
        );
        self.states.insert(key);
        let _ = writeln!(self.ops, "/GS{}_{} gs", key.0, key.1);
    }

    pub fn line_width(&mut self, w: f64) {
        let _ = writeln!(self.ops, "{} w", num(w));
    }

    /// A dash pattern, or a solid line for an empty one.
    pub fn dash(&mut self, pattern: &[f64]) {
        let parts: Vec<String> = pattern.iter().map(|&x| num(x)).collect();
        let _ = writeln!(self.ops, "[{}] 0 d", parts.join(" "));
    }

    pub fn rect(&mut self, x: f64, y: f64, w: f64, h: f64) {
        let _ = writeln!(self.ops, "{} {} {} {} re f", num(x), num(y), num(w), num(h));
    }

    pub fn line(&mut self, x1: f64, y1: f64, x2: f64, y2: f64) {
        let _ = writeln!(
            self.ops,
            "{} {} m {} {} l S",
            num(x1),
            num(y1),
            num(x2),
            num(y2)
        );
    }

    fn path(&mut self, points: &[(f64, f64)]) {
        for (i, (x, y)) in points.iter().enumerate() {
            let _ = writeln!(
                self.ops,
                "{} {} {}",
                num(*x),
                num(*y),
                if i == 0 { "m" } else { "l" }
            );
        }
    }

    pub fn polyline(&mut self, points: &[(f64, f64)]) {
        if points.len() < 2 {
            return;
        }
        self.path(points);
        self.ops.push_str("S\n");
    }

    pub fn polygon(&mut self, points: &[(f64, f64)]) {
        if points.len() < 3 {
            return;
        }
        self.path(points);
        self.ops.push_str("h f\n");
    }

    /// A clipping rectangle for what follows, until the next restore.
    pub fn clip(&mut self, x: f64, y: f64, w: f64, h: f64) {
        let _ = writeln!(
            self.ops,
            "{} {} {} {} re W n",
            num(x),
            num(y),
            num(w),
            num(h)
        );
    }

    /// One run of text with its baseline starting at (x, y).
    pub fn text(&mut self, x: f64, y: f64, bold: bool, size: f64, color: Color, text: &str) {
        let mut escaped = Vec::new();
        for b in encode(text) {
            if matches!(b, b'(' | b')' | b'\\') {
                escaped.push(b'\\');
            }
            escaped.push(b);
        }
        // Latin-1 into the stream: these bytes are the font's codes.
        let shown: String = escaped.iter().map(|&b| char::from(b)).collect();
        let _ = writeln!(
            self.ops,
            "BT /{} {} Tf {} {} {} rg {} {} Td ({}) Tj ET",
            if bold { "F2" } else { "F1" },
            num(size),
            num(color.0),
            num(color.1),
            num(color.2),
            num(x),
            num(y),
            shown
        );
    }

    /// Text set so it ends at `x`.
    pub fn text_right(&mut self, x: f64, y: f64, bold: bool, size: f64, color: Color, text: &str) {
        let w = text_width(text, bold, size);
        self.text(x - w, y, bold, size, color, text);
    }

    /// Text centred on `x`.
    pub fn text_centred(
        &mut self,
        x: f64,
        y: f64,
        bold: bool,
        size: f64,
        color: Color,
        text: &str,
    ) {
        let w = text_width(text, bold, size);
        self.text(x - w / 2.0, y, bold, size, color, text);
    }

    /// Text turned a quarter left, reading upwards, starting at (x, y).
    pub fn text_up(&mut self, x: f64, y: f64, size: f64, color: Color, text: &str) {
        let shown: String = encode(text)
            .iter()
            .flat_map(|&b| {
                if matches!(b, b'(' | b')' | b'\\') {
                    vec!['\\', char::from(b)]
                } else {
                    vec![char::from(b)]
                }
            })
            .collect();
        let _ = writeln!(
            self.ops,
            "BT /F1 {} Tf {} {} {} rg 0 1 -1 0 {} {} Tm ({}) Tj ET",
            num(size),
            num(color.0),
            num(color.1),
            num(color.2),
            num(x),
            num(y),
            shown
        );
    }
}

/// Document information.
pub struct Info {
    pub title: String,
    pub author: String,
    pub subject: String,
}

fn pdf_string(s: &str) -> String {
    let mut out = String::from("(");
    for b in encode(s) {
        match b {
            b'(' | b')' | b'\\' => {
                out.push('\\');
                out.push(char::from(b));
            }
            b if !(0x20..0x7F).contains(&b) => {
                let _ = write!(out, "\\{b:03o}");
            }
            b => out.push(char::from(b)),
        }
    }
    out.push(')');
    out
}

/// The pages as a PDF file.
pub fn write(pages: &[Canvas], info: &Info) -> Vec<u8> {
    let mut out: Vec<u8> = b"%PDF-1.4\n%\xE2\xE3\xCF\xD3\n".to_vec();
    let mut offsets: Vec<usize> = Vec::new();
    let object = |out: &mut Vec<u8>, offsets: &mut Vec<usize>, body: &[u8]| {
        offsets.push(out.len());
        out.extend(format!("{} 0 obj\n", offsets.len()).as_bytes());
        out.extend(body);
        out.extend(b"\nendobj\n");
    };
    // 1 catalog, 2 pages, 3 and 4 fonts, 5 info; then a page and its
    // content per page.
    let page_ids: Vec<usize> = (0..pages.len()).map(|i| 6 + 2 * i).collect();
    object(&mut out, &mut offsets, b"<< /Type /Catalog /Pages 2 0 R >>");
    let kids: Vec<String> = page_ids.iter().map(|id| format!("{id} 0 R")).collect();
    object(
        &mut out,
        &mut offsets,
        format!(
            "<< /Type /Pages /Kids [{}] /Count {} >>",
            kids.join(" "),
            pages.len()
        )
        .as_bytes(),
    );
    for face in ["Helvetica", "Helvetica-Bold"] {
        object(
            &mut out,
            &mut offsets,
            format!(
                "<< /Type /Font /Subtype /Type1 /BaseFont /{face} /Encoding /WinAnsiEncoding >>"
            )
            .as_bytes(),
        );
    }
    object(
        &mut out,
        &mut offsets,
        format!(
            "<< /Title {} /Author {} /Subject {} /Producer (RadiCal) >>",
            pdf_string(&info.title),
            pdf_string(&info.author),
            pdf_string(&info.subject)
        )
        .as_bytes(),
    );
    for (page, id) in pages.iter().zip(&page_ids) {
        let states: Vec<String> = page
            .states
            .iter()
            .map(|(f, s)| {
                format!(
                    "/GS{f}_{s} << /ca {} /CA {} >>",
                    num(f64::from(*f) / 1000.0),
                    num(f64::from(*s) / 1000.0)
                )
            })
            .collect();
        object(
            &mut out,
            &mut offsets,
            format!(
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {} {}] /Contents {} 0 R /Resources << /Font << /F1 3 0 R /F2 4 0 R >> /ExtGState << {} >> >> >>",
                num(PAGE_WIDTH),
                num(PAGE_HEIGHT),
                id + 1,
                states.join(" ")
            )
            .as_bytes(),
        );
        let raw: Vec<u8> = page.ops.chars().map(|c| c as u32 as u8).collect();
        let packed = compress_to_vec_zlib(&raw, 6);
        let mut body = format!(
            "<< /Length {} /Filter /FlateDecode >>\nstream\n",
            packed.len()
        )
        .into_bytes();
        body.extend(&packed);
        body.extend(b"\nendstream");
        object(&mut out, &mut offsets, &body);
    }
    let xref = out.len();
    out.extend(format!("xref\n0 {}\n0000000000 65535 f \n", offsets.len() + 1).as_bytes());
    for o in &offsets {
        out.extend(format!("{o:010} 00000 n \n").as_bytes());
    }
    out.extend(
        format!(
            "trailer\n<< /Size {} /Root 1 0 R /Info 5 0 R >>\nstartxref\n{xref}\n%%EOF\n",
            offsets.len() + 1
        )
        .as_bytes(),
    );
    out
}
