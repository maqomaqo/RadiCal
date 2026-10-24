//! The report's figures, drawn as paths rather than pictures: a posterior as
//! the results page draws it, a kernel density estimate, and the phases
//! through time. The palette and the HPD-bar geometry are the page's own
//! (`pdf_report.py`, mirroring the frontend's plot components).

use radical_core::date_format::{self, BC_AD, BCE_CE, CAL_BP, format_age};

use super::{Canvas, Color, hex, text_width};

pub const PRIMARY: &str = "#4a7dc4";
pub const BOUNDARY_INK: &str = "#d23f4a";
pub const PHASE_GREEN: &str = "#1f7a62";
pub const INK: &str = "#2b2320";
const PRIOR_GREY: &str = "#646464";
const POSTERIOR_FILL_ALPHA: f64 = 0.22;
const BOUNDARY_FILL_ALPHA: f64 = 0.13;
const PRIOR_LINE_ALPHA: f64 = 0.5;
const PRIOR_FILL_ALPHA: f64 = 0.15;
const HPD95_ALPHA: f64 = 0.3;
const HPD68_ALPHA: f64 = 0.72;
const GRID_ALPHA: f64 = 0.09;
const HPD95_BAR: (f64, f64) = (-0.11, -0.04);
const HPD68_BAR: (f64, f64) = (-0.22, -0.15);
const AXIS_BOTTOM_MARGIN: f64 = 0.07;
/// A figure's shape, as the backend's ten by 2.8 inches.
pub const FIGURE_ASPECT: f64 = 2.8 / 10.0;
const ACTIVITY_RAMP: [(f64, f64); 6] = [
    (0.0, 0.0),
    (0.02, 0.08),
    (0.1, 0.3),
    (0.3, 0.6),
    (0.6, 0.85),
    (1.0, 1.0),
];
/// One phase strip's share of a figure ten units wide (0.26 in of 10 in).
pub const ACTIVITY_ROW: f64 = 0.026;

fn display_year(cal_bp: f64) -> f64 {
    let signed = date_format::RADIOCARBON_PRESENT - cal_bp;
    if signed > 0.0 {
        signed
    } else {
        -(1.0 - signed)
    }
}

fn cal_bp_from_display_year(year: f64) -> f64 {
    let present = date_format::RADIOCARBON_PRESENT;
    if year > 0.0 {
        present - year
    } else {
        present - 1.0 - year
    }
}

/// A round step (1, 2 or 5 times a power of ten) giving about `target`
/// intervals (`_nice_step`).
pub fn nice_step(span: f64, target: usize) -> f64 {
    if span.is_nan() || span <= 0.0 {
        return 1.0;
    }
    let rough = span / target as f64;
    let magnitude = 10f64.powf(rough.log10().floor());
    for m in [1.0, 2.0, 5.0] {
        if m * magnitude >= rough {
            return m * magnitude;
        }
    }
    10.0 * magnitude
}

/// Ticks at round years in the chosen convention, placed in cal BP, or
/// None for cal BP itself (`_axis_ticks`).
pub fn axis_ticks(
    min_bp: f64,
    max_bp: f64,
    fmt: &str,
    target: usize,
) -> Option<(Vec<f64>, Vec<String>)> {
    if fmt == CAL_BP || !(min_bp.is_finite() && max_bp.is_finite()) || max_bp <= min_bp {
        return None;
    }
    let (start, end) = (display_year(max_bp), display_year(min_bp));
    let step = nice_step(end - start, target);
    let (mut at, mut labels) = (Vec::new(), Vec::new());
    let mut year = (start / step).ceil() * step;
    while year <= end {
        if year != 0.0 {
            let bp = cal_bp_from_display_year(year);
            if (min_bp..=max_bp).contains(&bp) {
                at.push(bp);
                labels.push(format_age(Some(bp), fmt));
            }
        }
        year += step;
    }
    (at.len() >= 2).then_some((at, labels))
}

fn axis_title(fmt: &str) -> &'static str {
    match fmt {
        BC_AD => "Calendar Age (BC / AD)",
        BCE_CE => "Calendar Age (BCE / CE)",
        _ => "Calendar Age (cal BP)",
    }
}

/// Round ticks across a range of plain numbers.
fn plain_ticks(lo: f64, hi: f64, target: usize) -> Vec<f64> {
    let step = nice_step(hi - lo, target);
    let mut out = Vec::new();
    let mut v = (lo / step).ceil() * step;
    while v <= hi + step * 1e-9 {
        out.push(if v.abs() < step * 1e-9 { 0.0 } else { v });
        v += step;
    }
    out
}

/// A tick's label as matplotlib's plain formatter writes it: as few
/// decimals as the step needs.
fn tick_label(v: f64, step: f64) -> String {
    let decimals = if step >= 1.0 {
        0
    } else {
        (-step.log10().floor()) as usize
    };
    format!("{v:.decimals$}")
}

/// A plot area inside a figure's rectangle, mapping data to page.
struct Axes {
    x0: f64,
    y0: f64,
    w: f64,
    h: f64,
    /// Data x at the left and right edges (left the older, so reversed).
    left: f64,
    right: f64,
    bottom: f64,
    top: f64,
}

impl Axes {
    fn px(&self, x: f64) -> f64 {
        self.x0 + (x - self.left) / (self.right - self.left) * self.w
    }

    fn py(&self, y: f64) -> f64 {
        self.y0 + (y - self.bottom) / (self.top - self.bottom) * self.h
    }

    /// Grid, frame, ticks and axis titles.
    fn furniture(
        &self,
        c: &mut Canvas,
        xt: &(Vec<f64>, Vec<String>),
        yt: Option<&[f64]>,
        x_title: &str,
        y_title: Option<&str>,
    ) {
        let ink = hex(INK);
        c.save();
        c.stroke(ink);
        c.line_width(0.6);
        c.alpha(1.0, GRID_ALPHA);
        for &x in &xt.0 {
            c.line(self.px(x), self.y0, self.px(x), self.y0 + self.h);
        }
        for &y in yt.unwrap_or(&[]) {
            c.line(self.x0, self.py(y), self.x0 + self.w, self.py(y));
        }
        c.restore();
        c.save();
        c.stroke(ink);
        c.alpha(1.0, 0.25);
        c.line_width(0.8);
        c.line(self.x0, self.y0, self.x0 + self.w, self.y0);
        c.line(
            self.x0,
            self.y0 + self.h,
            self.x0 + self.w,
            self.y0 + self.h,
        );
        c.line(self.x0, self.y0, self.x0, self.y0 + self.h);
        c.line(
            self.x0 + self.w,
            self.y0,
            self.x0 + self.w,
            self.y0 + self.h,
        );
        c.restore();
        for (x, label) in xt.0.iter().zip(&xt.1) {
            c.stroke(ink);
            c.line_width(0.6);
            c.line(self.px(*x), self.y0, self.px(*x), self.y0 - 2.5);
            c.text_centred(self.px(*x), self.y0 - 10.0, false, 7.0, ink, label);
        }
        c.text_centred(
            self.x0 + self.w / 2.0,
            self.y0 - 21.0,
            false,
            8.0,
            ink,
            x_title,
        );
        if let Some(ys) = yt {
            let step = if ys.len() > 1 { ys[1] - ys[0] } else { 1.0 };
            for &y in ys {
                c.stroke(ink);
                c.line(self.x0, self.py(y), self.x0 - 2.5, self.py(y));
                c.text_right(
                    self.x0 - 4.0,
                    self.py(y) - 2.4,
                    false,
                    7.0,
                    ink,
                    &tick_label(y, step),
                );
            }
        }
        if let Some(t) = y_title {
            let w = text_width(t, false, 8.0);
            c.text_up(self.x0 - 36.0, self.y0 + (self.h - w) / 2.0, 8.0, ink, t);
        }
    }
}

/// One legend entry: a swatch, and what it means.
enum Swatch {
    Line {
        color: Color,
        alpha: f64,
        dotted: bool,
    },
    Fill {
        color: Color,
        alpha: f64,
    },
}

/// The legend, in one row at the top right.
fn legend(c: &mut Canvas, ax: &Axes, entries: &[(Swatch, &str)]) {
    let ink = hex(INK);
    let mut x = ax.x0 + ax.w - 6.0;
    let y = ax.y0 + ax.h - 10.0;
    for (swatch, label) in entries.iter().rev() {
        let w = text_width(label, false, 6.5);
        x -= w;
        c.text(x, y, false, 6.5, ink, label);
        x -= 18.0;
        c.save();
        match swatch {
            Swatch::Line {
                color,
                alpha,
                dotted,
            } => {
                c.stroke(*color);
                c.alpha(1.0, *alpha);
                c.line_width(1.2);
                if *dotted {
                    c.dash(&[1.2, 1.8]);
                }
                c.line(x, y + 2.3, x + 14.0, y + 2.3);
            }
            Swatch::Fill { color, alpha } => {
                c.fill(*color);
                c.alpha(*alpha, 1.0);
                c.rect(x, y - 0.5, 14.0, 6.0);
            }
        }
        c.restore();
        x -= 10.0;
    }
}

/// The plot area inside a figure's rectangle, leaving room for the labels.
fn plot_area(x: f64, y: f64, w: f64, h: f64) -> (f64, f64, f64, f64) {
    (x + 46.0, y + 26.0, w - 52.0, h - 32.0)
}

fn area(points: &[(f64, f64)], ax: &Axes, floor: &[f64]) -> Vec<(f64, f64)> {
    let mut out: Vec<(f64, f64)> = points.iter().map(|&(x, y)| (ax.px(x), ax.py(y))).collect();
    for (&(x, _), &f) in points.iter().zip(floor).rev() {
        out.push((ax.px(x), ax.py(f)));
    }
    out
}

/// A posterior as the results page draws it (`_posterior_figure`), or None
/// when there is nothing to draw.
#[allow(clippy::too_many_arguments)]
pub fn posterior(
    distribution: &[[f64; 2]],
    hpd_68: &[(f64, f64)],
    hpd_95: &[(f64, f64)],
    prior: &[[f64; 2]],
    is_boundary: bool,
    fmt: &str,
) -> Option<super::layout::Draw> {
    let peak = distribution
        .iter()
        .map(|p| p[1])
        .fold(f64::NEG_INFINITY, f64::max);
    if distribution.is_empty() || peak.is_nan() || peak <= 0.0 {
        return None;
    }
    let (dist, h68, h95, prior) = (
        distribution.to_vec(),
        hpd_68.to_vec(),
        hpd_95.to_vec(),
        prior.to_vec(),
    );
    let fmt = fmt.to_owned();
    Some(Box::new(move |c: &mut Canvas, x, y, w, h| {
        let (line, fill_alpha) = if is_boundary {
            (hex(BOUNDARY_INK), BOUNDARY_FILL_ALPHA)
        } else {
            (hex(PRIMARY), POSTERIOR_FILL_ALPHA)
        };
        let ages = dist.iter().chain(&prior).map(|p| p[0]);
        let (lo, hi) = ages.fold((f64::INFINITY, f64::NEG_INFINITY), |(a, b), v| {
            (a.min(v), b.max(v))
        });
        let top = prior.iter().map(|p| p[1]).fold(peak, f64::max) * 1.08;
        let (px, py, pw, ph) = plot_area(x, y, w, h);
        let ax = Axes {
            x0: px,
            y0: py,
            w: pw,
            h: ph,
            left: hi,
            right: lo,
            bottom: (HPD68_BAR.0 - AXIS_BOTTOM_MARGIN) * peak,
            top,
        };
        let xt = axis_ticks(lo, hi, &fmt, 7).unwrap_or_else(|| {
            let at = plain_ticks(lo, hi, 7);
            let labels = at.iter().map(|v| format!("{v:.0}")).collect();
            (at, labels)
        });
        let yt = plain_ticks(0.0, top, 5);
        ax.furniture(
            c,
            &xt,
            Some(&yt),
            axis_title(&fmt),
            Some("Probability Density"),
        );
        c.save();
        c.clip(px, py, pw, ph);
        if !prior.is_empty() {
            let pts: Vec<(f64, f64)> = prior.iter().map(|p| (p[0], p[1])).collect();
            c.fill(hex(PRIOR_GREY));
            c.alpha(PRIOR_FILL_ALPHA, 1.0);
            c.polygon(&area(&pts, &ax, &vec![0.0; pts.len()]));
            c.stroke(hex(PRIOR_GREY));
            c.alpha(1.0, PRIOR_LINE_ALPHA);
            c.line_width(0.9);
            c.polyline(
                &pts.iter()
                    .map(|&(a, b)| (ax.px(a), ax.py(b)))
                    .collect::<Vec<_>>(),
            );
        }
        let pts: Vec<(f64, f64)> = dist.iter().map(|p| (p[0], p[1])).collect();
        c.fill(line);
        c.alpha(fill_alpha, 1.0);
        c.polygon(&area(&pts, &ax, &vec![0.0; pts.len()]));
        c.stroke(line);
        c.alpha(1.0, 1.0);
        c.line_width(1.4);
        if is_boundary {
            c.dash(&[1.4, 2.3]);
        }
        c.polyline(
            &pts.iter()
                .map(|&(a, b)| (ax.px(a), ax.py(b)))
                .collect::<Vec<_>>(),
        );
        c.dash(&[]);
        for (spans, band, alpha) in [
            (&h95, HPD95_BAR, HPD95_ALPHA),
            (&h68, HPD68_BAR, HPD68_ALPHA),
        ] {
            c.fill(hex(PHASE_GREEN));
            c.alpha(alpha, 1.0);
            for &(a, b) in spans.iter() {
                let (l, r) = (ax.px(b), ax.px(a));
                let (bot, tp) = (ax.py(band.0 * peak), ax.py(band.1 * peak));
                c.rect(l.min(r), bot, (r - l).abs(), tp - bot);
            }
        }
        c.restore();
        let mut entries: Vec<(Swatch, &str)> = Vec::new();
        if !prior.is_empty() {
            entries.push((
                Swatch::Line {
                    color: hex(PRIOR_GREY),
                    alpha: PRIOR_LINE_ALPHA,
                    dotted: false,
                },
                "Prior (unmodelled)",
            ));
        }
        entries.push((
            Swatch::Line {
                color: line,
                alpha: 1.0,
                dotted: is_boundary,
            },
            if is_boundary {
                "Boundary (modelled transition)"
            } else {
                "Posterior (modelled)"
            },
        ));
        if !h95.is_empty() {
            entries.push((
                Swatch::Fill {
                    color: hex(PHASE_GREEN),
                    alpha: HPD95_ALPHA,
                },
                "95.4% HPD",
            ));
        }
        if !h68.is_empty() {
            entries.push((
                Swatch::Fill {
                    color: hex(PHASE_GREEN),
                    alpha: HPD68_ALPHA,
                },
                "68.3% HPD",
            ));
        }
        legend(c, &ax, &entries);
    }))
}

/// A kernel density estimate: the mean curve over a band of its ±1 sigma
/// (`_kde_figure`).
pub fn kde(points: &[[f64; 3]], fmt: &str) -> Option<super::layout::Draw> {
    if points.is_empty() {
        return None;
    }
    let pts = points.to_vec();
    let fmt = fmt.to_owned();
    Some(Box::new(move |c: &mut Canvas, x, y, w, h| {
        let (lo, hi) = pts
            .iter()
            .fold((f64::INFINITY, f64::NEG_INFINITY), |(a, b), p| {
                (a.min(p[0]), b.max(p[0]))
            });
        let upper: Vec<f64> = pts.iter().map(|p| p[1] + p[2]).collect();
        let lower: Vec<f64> = pts.iter().map(|p| (p[1] - p[2]).max(0.0)).collect();
        let top = upper.iter().copied().fold(0.0, f64::max) * 1.08;
        let (px, py, pw, ph) = plot_area(x, y, w, h);
        let ax = Axes {
            x0: px,
            y0: py,
            w: pw,
            h: ph,
            left: hi,
            right: lo,
            bottom: 0.0,
            top,
        };
        let xt = axis_ticks(lo, hi, &fmt, 7).unwrap_or_else(|| {
            let at = plain_ticks(lo, hi, 7);
            let labels = at.iter().map(|v| format!("{v:.0}")).collect();
            (at, labels)
        });
        let yt = plain_ticks(0.0, top, 5);
        ax.furniture(
            c,
            &xt,
            Some(&yt),
            axis_title(&fmt),
            Some("Probability Density"),
        );
        c.save();
        c.clip(px, py, pw, ph);
        let band: Vec<(f64, f64)> = pts.iter().zip(&upper).map(|(p, u)| (p[0], *u)).collect();
        c.fill(hex(PRIMARY));
        c.alpha(POSTERIOR_FILL_ALPHA, 1.0);
        c.polygon(&area(&band, &ax, &lower));
        c.stroke(hex(PRIMARY));
        c.alpha(1.0, 1.0);
        c.line_width(1.4);
        c.polyline(
            &pts.iter()
                .map(|p| (ax.px(p[0]), ax.py(p[1])))
                .collect::<Vec<_>>(),
        );
        c.restore();
        legend(
            c,
            &ax,
            &[
                (
                    Swatch::Fill {
                        color: hex(PRIMARY),
                        alpha: POSTERIOR_FILL_ALPHA,
                    },
                    "±1σ",
                ),
                (
                    Swatch::Line {
                        color: hex(PRIMARY),
                        alpha: 1.0,
                        dotted: false,
                    },
                    "KDE",
                ),
            ],
        );
    }))
}

/// How strongly a probability is inked, steeper than linear at the low end.
fn ramp(v: f64) -> f64 {
    let v = v.clamp(0.0, 1.0);
    for pair in ACTIVITY_RAMP.windows(2) {
        let ((a, sa), (b, sb)) = (pair[0], pair[1]);
        if v <= b {
            return sa + (v - a) / (b - a) * (sb - sa);
        }
    }
    1.0
}

/// One phase strip: its label, its colour, and its values oldest first.
pub struct Strip {
    pub label: String,
    pub relative: bool,
    pub values: Vec<f64>,
}

/// One strip per phase on a shared calendar axis, shaded by the probability
/// the phase was in progress (`_phase_activity_figure`).
pub fn phase_activity(
    strips: Vec<Strip>,
    start: f64,
    step: f64,
    fmt: &str,
) -> Option<super::layout::Draw> {
    if strips.len() < 2 || strips[0].values.is_empty() {
        return None;
    }
    let fmt = fmt.to_owned();
    Some(Box::new(move |c: &mut Canvas, x, y, w, h| {
        let ink = hex(INK);
        let count = strips[0].values.len();
        let (oldest, youngest) = (start + step / 2.0, start - (count as f64 - 0.5) * step);
        let label_w = strips
            .iter()
            .map(|s| text_width(&s.label, false, 7.0))
            .fold(0.0, f64::max)
            + 10.0;
        let (px, py, pw, ph) = (x + label_w, y + 26.0, w - label_w - 6.0, h - 32.0);
        let ax = Axes {
            x0: px,
            y0: py,
            w: pw,
            h: ph,
            left: oldest,
            right: youngest,
            bottom: strips.len() as f64 - 0.5,
            top: -0.5,
        };
        let xt = axis_ticks(youngest, oldest, &fmt, 7).unwrap_or_else(|| {
            let at = plain_ticks(youngest, oldest, 7);
            let labels = at.iter().map(|v| format!("{v:.0}")).collect();
            (at, labels)
        });
        c.save();
        c.stroke(ink);
        c.line_width(0.6);
        c.alpha(1.0, GRID_ALPHA);
        for &t in &xt.0 {
            c.line(ax.px(t), py, ax.px(t), py + ph);
        }
        c.restore();
        for (i, strip) in strips.iter().enumerate() {
            let colour = hex(if strip.relative { PRIMARY } else { PHASE_GREEN });
            let (top, bottom) = (ax.py(i as f64 - 0.4), ax.py(i as f64 + 0.4));
            let cell = pw / count as f64;
            c.save();
            c.fill(colour);
            for (j, v) in strip.values.iter().enumerate() {
                let a = (ramp(*v) * 50.0).round() / 50.0;
                if a <= 0.0 {
                    continue;
                }
                c.alpha(a, 1.0);
                c.rect(px + j as f64 * cell, bottom, cell + 0.05, top - bottom);
            }
            c.restore();
            c.text_right(
                px - 4.0,
                ax.py(i as f64) - 2.4,
                false,
                7.0,
                ink,
                &strip.label,
            );
        }
        for (t, label) in xt.0.iter().zip(&xt.1) {
            c.stroke(ink);
            c.line_width(0.6);
            c.line(ax.px(*t), py, ax.px(*t), py - 2.0);
            c.text_centred(ax.px(*t), py - 10.0, false, 7.0, ink, label);
        }
        c.text_centred(px + pw / 2.0, py - 21.0, false, 8.0, ink, axis_title(&fmt));
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cal_bp_leaves_the_ticks_alone() {
        assert!(axis_ticks(2000.0, 3000.0, CAL_BP, 7).is_none());
    }

    #[test]
    fn bc_ad_ticks_are_round_years_with_no_year_zero() {
        let (_, labels) = axis_ticks(2000.0, 3000.0, BC_AD, 7).unwrap();
        for l in &labels {
            let year: i64 = l.split_whitespace().find_map(|p| p.parse().ok()).unwrap();
            assert_eq!(year % 100, 0, "{labels:?}");
        }
        let (_, labels) = axis_ticks(1800.0, 2100.0, BC_AD, 7).unwrap();
        assert!(
            labels
                .iter()
                .all(|l| l.trim() != "0 BC" && l.trim() != "AD 0")
        );
        assert!(axis_ticks(3000.0, 3000.0, BC_AD, 7).is_none());
    }

    #[test]
    fn nothing_to_draw_yields_no_figure() {
        assert!(posterior(&[], &[], &[], &[], false, CAL_BP).is_none());
        assert!(posterior(&[[1.0, 0.0], [2.0, 0.0]], &[], &[], &[], false, CAL_BP).is_none());
        assert!(posterior(&[[1.0, 0.1], [2.0, 0.3]], &[], &[], &[], false, CAL_BP).is_some());
    }
}
