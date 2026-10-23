//! Flowing a document onto pages, as ReportLab's platypus flows the
//! backend's report: paragraphs wrapped to the frame, tables that break
//! between rows and repeat their header, figures, blocks kept together, and
//! a footer on every page.

use super::{Canvas, Color, MM, PAGE_HEIGHT, PAGE_WIDTH, text_width};

pub const MARGIN: f64 = 14.0 * MM;
pub const CONTENT_WIDTH: f64 = PAGE_WIDTH - 2.0 * MARGIN;
const TOP: f64 = PAGE_HEIGHT - MARGIN;
const BOTTOM: f64 = MARGIN * 1.4;

/// How a paragraph is set.
#[derive(Debug, Clone, Copy)]
pub struct Style {
    pub size: f64,
    pub leading: f64,
    pub bold: bool,
    pub color: Color,
    pub space_before: f64,
    pub space_after: f64,
    pub indent: f64,
}

/// A stretch of text in one face.
#[derive(Debug, Clone, PartialEq)]
struct Run {
    text: String,
    bold: bool,
    sub: bool,
}

/// Paragraph text with the two tags the report uses, `<b>` and `<sub>`.
/// Anything else between angle brackets is text.
fn runs(markup: &str, bold: bool) -> Vec<Run> {
    let mut out = Vec::new();
    let (mut b, mut sub) = (bold, false);
    let mut text = String::new();
    let mut rest = markup;
    while !rest.is_empty() {
        let tag = ["<b>", "</b>", "<sub>", "</sub>"]
            .into_iter()
            .find(|t| rest.starts_with(t));
        match tag {
            Some(t) => {
                if !text.is_empty() {
                    out.push(Run {
                        text: std::mem::take(&mut text),
                        bold: b,
                        sub,
                    });
                }
                match t {
                    "<b>" => b = true,
                    "</b>" => b = bold,
                    "<sub>" => sub = true,
                    _ => sub = false,
                }
                rest = &rest[t.len()..];
            }
            None => {
                let c = rest.chars().next().unwrap();
                text.push(c);
                rest = &rest[c.len_utf8()..];
            }
        }
    }
    if !text.is_empty() {
        out.push(Run { text, bold: b, sub });
    }
    out
}

/// A word: pieces of runs set with no space between them.
type Word = Vec<Run>;

fn size_of(run: &Run, size: f64) -> f64 {
    if run.sub { size * 0.7 } else { size }
}

fn word_width(word: &Word, size: f64) -> f64 {
    word.iter()
        .map(|r| text_width(&r.text, r.bold, size_of(r, size)))
        .sum()
}

/// The words of some runs, whitespace collapsed as a paragraph collapses it.
fn words(runs: &[Run]) -> Vec<Word> {
    let mut out: Vec<Word> = Vec::new();
    let mut current: Word = Vec::new();
    for run in runs {
        let mut piece = String::new();
        for c in run.text.chars() {
            if c.is_whitespace() {
                if !piece.is_empty() {
                    current.push(Run {
                        text: std::mem::take(&mut piece),
                        ..run.clone()
                    });
                }
                if !current.is_empty() {
                    out.push(std::mem::take(&mut current));
                }
            } else {
                piece.push(c);
            }
        }
        if !piece.is_empty() {
            current.push(Run {
                text: piece,
                ..run.clone()
            });
        }
    }
    if !current.is_empty() {
        out.push(current);
    }
    out
}

/// Words broken into lines no wider than `width`; a word wider than a line
/// gets a line of its own.
fn lines(words: &[Word], size: f64, width: f64) -> Vec<Vec<Word>> {
    let space = text_width(" ", false, size);
    let mut out: Vec<Vec<Word>> = Vec::new();
    let mut line: Vec<Word> = Vec::new();
    let mut used = 0.0;
    for w in words {
        let wide = word_width(w, size);
        let needed = if line.is_empty() {
            wide
        } else {
            used + space + wide
        };
        if !line.is_empty() && needed > width {
            out.push(std::mem::take(&mut line));
            used = wide;
        } else {
            used = needed;
        }
        line.push(w.clone());
    }
    if !line.is_empty() {
        out.push(line);
    }
    out
}

/// A paragraph set to a width.
#[derive(Clone)]
pub struct Para {
    lines: Vec<Vec<Word>>,
    style: Style,
}

impl Para {
    pub fn new(markup: &str, style: Style, width: f64) -> Self {
        let ws = words(&runs(markup, style.bold));
        Self {
            lines: lines(&ws, style.size, width - style.indent),
            style,
        }
    }

    pub fn height(&self) -> f64 {
        self.lines.len() as f64 * self.style.leading
    }

    /// Drawn with its top at `top`, in `color` unless a row overrides it.
    fn draw(&self, c: &mut Canvas, x: f64, top: f64, color: Option<Color>) {
        let s = &self.style;
        let space = text_width(" ", false, s.size);
        for (i, line) in self.lines.iter().enumerate() {
            let baseline = top - s.size - i as f64 * s.leading;
            let mut cursor = x + s.indent;
            for (j, word) in line.iter().enumerate() {
                if j > 0 {
                    cursor += space;
                }
                for run in word {
                    let size = size_of(run, s.size);
                    let rise = if run.sub { -s.size * 0.2 } else { 0.0 };
                    c.text(
                        cursor,
                        baseline + rise,
                        run.bold,
                        size,
                        color.unwrap_or(s.color),
                        &run.text,
                    );
                    cursor += text_width(&run.text, run.bold, size);
                }
            }
        }
    }
}

/// A table's look.
#[derive(Clone)]
pub struct TableStyle {
    pub pad_left: f64,
    pub pad_right: f64,
    pub pad_top: f64,
    pub pad_bottom: f64,
    pub header_fill: Color,
    pub header_rule: Color,
    pub grid: Color,
}

/// One body row's look: a background, and a colour for all its text.
#[derive(Clone, Default)]
pub struct RowLook {
    pub fill: Option<Color>,
    pub text: Option<Color>,
}

/// A table of paragraphs, its first row repeated at the head of each page
/// it runs onto.
#[derive(Clone)]
pub struct Table {
    pub widths: Vec<f64>,
    pub header: Vec<Para>,
    pub rows: Vec<(Vec<Para>, RowLook)>,
    pub style: TableStyle,
}

impl Table {
    fn row_height(&self, cells: &[Para]) -> f64 {
        cells.iter().map(Para::height).fold(0.0, f64::max)
            + self.style.pad_top
            + self.style.pad_bottom
    }

    fn draw_row(
        &self,
        c: &mut Canvas,
        top: f64,
        cells: &[Para],
        look: &RowLook,
        header: bool,
    ) -> f64 {
        let s = &self.style;
        let h = self.row_height(cells);
        let width: f64 = self.widths.iter().sum();
        let fill = if header {
            Some(s.header_fill)
        } else {
            look.fill
        };
        if let Some(f) = fill {
            c.fill(f);
            c.rect(MARGIN, top - h, width, h);
        }
        c.stroke(s.grid);
        c.line_width(0.25);
        let mut x = MARGIN;
        for (cell, w) in cells.iter().zip(&self.widths) {
            cell.draw(c, x + s.pad_left, top - s.pad_top, look.text);
            c.line(x, top, x, top - h);
            x += w;
        }
        c.line(x, top, x, top - h);
        c.line(MARGIN, top, MARGIN + width, top);
        c.line(MARGIN, top - h, MARGIN + width, top - h);
        if header {
            c.stroke(s.header_rule);
            c.line_width(0.6);
            c.line(MARGIN, top - h, MARGIN + width, top - h);
        }
        h
    }
}

/// What a figure draws into the rectangle (x, y, width, height) it is given.
pub type Draw = Box<dyn Fn(&mut Canvas, f64, f64, f64, f64)>;

/// Something placed on the page.
pub enum Block {
    Para(Para),
    Table(Table),
    Figure {
        height: f64,
        draw: Draw,
    },
    /// Moved whole to the next page rather than split.
    Keep(Vec<Block>),
    PageBreak,
}

impl Block {
    fn height(&self) -> f64 {
        match self {
            Block::Para(p) => p.style.space_before + p.height() + p.style.space_after,
            Block::Table(t) => {
                t.row_height(&t.header)
                    + t.rows.iter().map(|(r, _)| t.row_height(r)).sum::<f64>()
                    + 6.0
            }
            Block::Figure { height, .. } => *height,
            Block::Keep(blocks) => blocks.iter().map(Block::height).sum(),
            Block::PageBreak => 0.0,
        }
    }
}

/// Pages being filled.
struct Flow {
    pages: Vec<Canvas>,
    y: f64,
    footer: String,
}

impl Flow {
    fn page(&mut self) -> &mut Canvas {
        self.pages.last_mut().expect("a page")
    }

    fn new_page(&mut self) {
        self.pages.push(Canvas::default());
        self.y = TOP;
    }

    fn at_top(&self) -> bool {
        (self.y - TOP).abs() < 1e-9
    }

    fn place(&mut self, block: &Block) {
        match block {
            Block::PageBreak => {
                if !self.at_top() {
                    self.new_page();
                }
            }
            Block::Para(p) => {
                let before = if self.at_top() {
                    0.0
                } else {
                    p.style.space_before
                };
                if self.y - before - p.height() < BOTTOM && !self.at_top() {
                    self.new_page();
                    return self.place(block);
                }
                self.y -= before;
                let y = self.y;
                p.draw(self.page(), MARGIN, y, None);
                self.y -= p.height() + p.style.space_after;
            }
            Block::Figure { height, draw } => {
                if self.y - height < BOTTOM && !self.at_top() {
                    self.new_page();
                }
                let y = self.y;
                draw(self.page(), MARGIN, y - height, CONTENT_WIDTH, *height);
                self.y -= height;
            }
            Block::Keep(blocks) => {
                let h = block.height();
                if self.y - h < BOTTOM && !self.at_top() && h <= TOP - BOTTOM {
                    self.new_page();
                }
                for b in blocks {
                    self.place(b);
                }
            }
            Block::Table(t) => {
                let header_h = t.row_height(&t.header);
                let mut started = false;
                for (cells, look) in &t.rows {
                    let h = t.row_height(cells);
                    let need = if started { h } else { header_h + h };
                    if self.y - need < BOTTOM && !(self.at_top() && !started) {
                        self.new_page();
                        started = false;
                    }
                    if !started {
                        let y = self.y;
                        let page = self.pages.last_mut().unwrap();
                        let drawn = t.draw_row(page, y, &t.header, &RowLook::default(), true);
                        self.y -= drawn;
                        started = true;
                    }
                    let y = self.y;
                    let page = self.pages.last_mut().unwrap();
                    self.y -= t.draw_row(page, y, cells, look, false);
                }
                if t.rows.is_empty() {
                    let y = self.y;
                    let page = self.pages.last_mut().unwrap();
                    self.y -= t.draw_row(page, y, &t.header, &RowLook::default(), true);
                }
                self.y -= 6.0;
            }
        }
    }

    /// The footer on every page: which analysis, and which page.
    fn finish(mut self) -> Vec<Canvas> {
        let grey = super::hex("#6b625d");
        let footer = self.footer.clone();
        for (i, page) in self.pages.iter_mut().enumerate() {
            page.text(MARGIN, MARGIN * 0.6, false, 7.0, grey, &footer);
            page.text_right(
                PAGE_WIDTH - MARGIN,
                MARGIN * 0.6,
                false,
                7.0,
                grey,
                &format!("Page {}", i + 1),
            );
        }
        self.pages
    }
}

/// The blocks flowed onto as many pages as they take.
pub fn flow(blocks: &[Block], footer: &str) -> Vec<Canvas> {
    let mut f = Flow {
        pages: vec![Canvas::default()],
        y: TOP,
        footer: footer.to_owned(),
    };
    for b in blocks {
        f.place(b);
    }
    f.finish()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn markup_splits_into_runs_and_words() {
        let r = runs("A<sub>model</sub> 64%, <b>IntCal20</b> (x)", false);
        assert_eq!(r.len(), 5);
        assert!(r[1].sub && r[3].bold);
        let w = words(&r);
        assert_eq!(w.len(), 4);
        assert_eq!(w[0].len(), 2, "A and its subscript are one word");
    }

    #[test]
    fn lines_wrap_to_the_width() {
        let style = Style {
            size: 10.0,
            leading: 12.0,
            bold: false,
            color: Color(0.0, 0.0, 0.0),
            space_before: 0.0,
            space_after: 0.0,
            indent: 0.0,
        };
        let p = Para::new(&"word ".repeat(100), style, 200.0);
        assert!(p.lines.len() > 5);
        for line in &p.lines {
            let w: f64 = line.iter().map(|w| word_width(w, 10.0)).sum::<f64>()
                + (line.len() - 1) as f64 * text_width(" ", false, 10.0);
            assert!(w <= 200.0 + 1e-9);
        }
    }
}
