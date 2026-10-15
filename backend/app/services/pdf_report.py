"""
A completed analysis rendered as a printable report.

The citable form of a run: the same rows the results page and the CSV show, in
the model's own reading order, with each date's posterior drawn beside them and
a method note naming the curves and solver that produced the numbers.

Nothing here touches the database. `api/results.py` owns the assembly and hands
the same structures to the CSV, the JSON and this, so the three cannot
disagree. The one thing this module derives for itself is the unmodelled
calibration series behind each Bayesian date -- a plot input rather than a
table value, computed as `api/analysis.py` computes it for the screen.

Two deliberate duplications, the alternative being to ship the report's
assembly to the browser. The plot palette and HPD-bar geometry are mirrored
from frontend/src/theme/plotColors.ts and components/PosteriorPlot.tsx, so a
figure in the PDF is the figure on screen; and the calendar tick selection is
mirrored from frontend/src/utils/dateFormat.ts, because ticks at round cal BP
values labelled in BC/AD read "1051 BC" where a reader expects "1000 BC".

Figures use matplotlib's object API rather than pyplot, which keeps every
figure in a global registry -- a memory leak in a long-lived server process --
and carries GUI backend selection this has no use for.
"""
from __future__ import annotations

import io
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Table,
    TableStyle,
)

from app.services import date_format as date_format_service
from app.services.calibration import SIGMA_2, get_curve
from app.services import statistics
from app.services.intervals import SHARED_BOUNDARY_TEXT

# --- page geometry -------------------------------------------------------

# Landscape: the results table carries up to twelve columns, several holding a
# multimodal HPD, which portrait fits only by wrapping each onto three lines.
PAGE_SIZE = landscape(A4)
MARGIN = 14 * mm
CONTENT_WIDTH = PAGE_SIZE[0] - 2 * MARGIN

# --- palette, mirrored from frontend/src/theme/plotColors.ts -------------

PRIMARY = '#4a7dc4'          # posterior line; fill is the same at 0.22
BOUNDARY_INK = '#d23f4a'     # a Boundary is coral wherever it is drawn
PHASE_GREEN = '#1f7a62'      # both HPD bars, at two strengths
INK = '#2b2320'              # the app's warm ink
PRIOR_GREY = '#646464'       # the unmodelled shadow, deliberately neutral

POSTERIOR_FILL_ALPHA = 0.22
BOUNDARY_FILL_ALPHA = 0.13
PRIOR_LINE_ALPHA = 0.5
PRIOR_FILL_ALPHA = 0.15
HPD95_ALPHA = 0.3
HPD68_ALPHA = 0.72
GRID_ALPHA = 0.09

# --- figure geometry, mirrored from components/PosteriorPlot.tsx ---------

# The HPD bars sit in a strip BELOW the curve's zero baseline, as fractions of
# the posterior's peak: curve, gap, 95% bar, gap, 68% bar, then clear space
# before the axis. Translucent fills under the curve go muddy three deep.
HPD95_BAR = (-0.11, -0.04)
HPD68_BAR = (-0.22, -0.15)
AXIS_BOTTOM_MARGIN = 0.07

# Sized to the printable width in landscape. 200 dpi over ten inches is about
# 210 effective dpi on the page -- a real figure rather than a screenshot.
FIGURE_SIZE_IN = (10.0, 2.8)
FIGURE_DPI = 200


def _styles() -> Dict[str, ParagraphStyle]:
    """
    The document's text styles: ReportLab's sample sheet for sane metrics,
    recoloured onto the app's warm ink so the report looks like the app it came
    from rather than a library's defaults.
    """
    base = getSampleStyleSheet()
    ink = colors.HexColor(INK)
    return {
        'title': ParagraphStyle(
            'RcTitle', parent=base['Title'], fontSize=17, leading=21,
            alignment=0, textColor=ink, spaceAfter=2,
        ),
        'subtitle': ParagraphStyle(
            'RcSubtitle', parent=base['Normal'], fontSize=9, leading=12,
            textColor=colors.HexColor('#6b625d'), spaceAfter=10,
        ),
        'heading': ParagraphStyle(
            'RcHeading', parent=base['Heading2'], fontSize=12, leading=15,
            textColor=ink, spaceBefore=12, spaceAfter=5,
        ),
        'body': ParagraphStyle(
            'RcBody', parent=base['Normal'], fontSize=8.5, leading=11.5,
            textColor=ink, spaceAfter=4,
        ),
        'note': ParagraphStyle(
            'RcNote', parent=base['Normal'], fontSize=8, leading=10.5,
            textColor=colors.HexColor('#6b625d'), spaceAfter=6,
        ),
        'th': ParagraphStyle(
            'RcTh', parent=base['Normal'], fontSize=7.5, leading=9.5,
            fontName='Helvetica-Bold', textColor=ink,
        ),
        'td': ParagraphStyle(
            'RcTd', parent=base['Normal'], fontSize=7.5, leading=9.5,
            textColor=ink,
        ),
        'caption': ParagraphStyle(
            'RcCaption', parent=base['Normal'], fontSize=9, leading=12,
            fontName='Helvetica-Bold', textColor=ink, spaceBefore=6,
            spaceAfter=1,
        ),
    }


# --- value formatting ----------------------------------------------------

def _hpd_text(
    ranges: Optional[List[Dict]],
    fallback_min: Optional[float],
    fallback_max: Optional[float],
    date_format: str,
) -> str:
    """
    An HPD region as text, in the reader's chosen convention.

    Prefers the per-range breakdown: collapsing "two peaks 200 years apart" to
    one span claims the gap between them as part of the region. The min/max
    envelope is a fallback for rows predating the breakdown, the same one the
    CSV export makes.
    """
    if ranges:
        return date_format_service.format_hpd_ranges(ranges, date_format)
    if fallback_min is not None and fallback_max is not None:
        return date_format_service.format_range(fallback_max, fallback_min, date_format)
    return ''


def _age_text(value: Optional[float], date_format: str) -> str:
    """One calendar age, or empty -- never "0" for a missing value."""
    return date_format_service.format_age(value, date_format) if value is not None else ''


def _number(value: Optional[float], places: int = 0) -> str:
    """A plain number that is not a calendar age (a 14C age, a duration)."""
    if value is None:
        return ''
    return f"{value:.{places}f}"


def _measurement(result: Optional[Dict]) -> str:
    """
    What was measured, as "389 ± 21". Blank for a structural row and for a
    Boundary, which is a modelled parameter with no measurement behind it.
    """
    if not result or result.get('age_bp') is None:
        return ''
    age = _number(result.get('age_bp'))
    error = result.get('error_1sigma')
    return f"{age} ± {_number(error)}" if error is not None else age


# --- calendar axis, mirrored from frontend/src/utils/dateFormat.ts -------

def _display_year(cal_bp: float) -> float:
    """cal BP as a signed display year: positive AD/CE, negative BC/BCE."""
    signed = date_format_service.RADIOCARBON_PRESENT - cal_bp
    return signed if signed > 0 else -(1 - signed)


def _cal_bp_from_display_year(year: float) -> float:
    """The inverse, honouring the calendar's missing year zero."""
    present = date_format_service.RADIOCARBON_PRESENT
    return present - year if year > 0 else present - 1 - year


def _nice_step(span: float, target: int) -> float:
    """A round step (1/2/5 x 10^n) giving roughly `target` intervals."""
    if not span > 0:
        return 1
    rough = span / target
    magnitude = 10 ** math.floor(math.log10(rough))
    for multiple in (1, 2, 5):
        if multiple * magnitude >= rough:
            return multiple * magnitude
    return 10 * magnitude


def _axis_ticks(
    min_cal_bp: float, max_cal_bp: float, date_format: str, target: int = 7
) -> Optional[Tuple[List[float], List[str]]]:
    """
    Tick positions in cal BP -- where the data is plotted -- with labels in
    the chosen convention.

    None for cal BP: the values already are cal BP, so matplotlib's own
    tick choice beats anything imposed here. For BC/AD and BCE/CE the ticks
    are round years in THAT convention and mapped back to the cal BP
    positions they sit at, which is the difference between an axis reading
    "1000 BC" and one reading "1051 BC".
    """
    if date_format == date_format_service.CAL_BP:
        return None
    if not (math.isfinite(min_cal_bp) and math.isfinite(max_cal_bp)) or max_cal_bp <= min_cal_bp:
        return None

    # Older cal BP is a smaller display year, so the range flips.
    start, end = _display_year(max_cal_bp), _display_year(min_cal_bp)
    step = _nice_step(end - start, target)

    tickvals: List[float] = []
    ticktext: List[str] = []
    year = math.ceil(start / step) * step
    while year <= end:
        if year != 0:  # no year zero
            cal_bp = _cal_bp_from_display_year(year)
            if min_cal_bp <= cal_bp <= max_cal_bp:
                tickvals.append(cal_bp)
                ticktext.append(date_format_service.format_age(cal_bp, date_format))
        year += step
    return (tickvals, ticktext) if len(tickvals) >= 2 else None


def _axis_title(date_format: str) -> str:
    if date_format == date_format_service.BC_AD:
        return 'Calendar Age (BC / AD)'
    if date_format == date_format_service.BCE_CE:
        return 'Calendar Age (BCE / CE)'
    return 'Calendar Age (cal BP)'


# --- figures -------------------------------------------------------------

def _series(distribution: Optional[Sequence]) -> Tuple[List[float], List[float]]:
    """A stored [[cal_age, prob], ...] distribution as two parallel lists."""
    if not distribution:
        return [], []
    ages = [float(point[0]) for point in distribution]
    probs = [float(point[1]) for point in distribution]
    return ages, probs


def _draw_hpd_bars(ax, ranges: Optional[List[Dict]], envelope, peak: float,
                   band: Tuple[float, float], alpha: float, label: str) -> None:
    """
    One HPD level as solid bars in the strip below the curve, each disjoint
    range getting its own rather than one spanning the whole envelope. Only the
    first carries the legend label, so a posterior in four pieces still
    produces one legend row.
    """
    spans: List[Tuple[float, float]] = []
    if ranges:
        spans = [(float(r['min']), float(r['max'])) for r in ranges]
    elif envelope and envelope[0] is not None and envelope[1] is not None:
        spans = [(float(envelope[0]), float(envelope[1]))]

    bottom, top = band[0] * peak, band[1] * peak
    for index, (low, high) in enumerate(spans):
        ax.fill_between(
            [low, high], bottom, top,
            color=PHASE_GREEN, alpha=alpha, linewidth=0,
            label=label if index == 0 else None,
        )


def _posterior_figure(
    distribution: Optional[Sequence],
    hpd_68_ranges: Optional[List[Dict]],
    hpd_95_ranges: Optional[List[Dict]],
    hpd_68_envelope,
    hpd_95_envelope,
    prior_distribution: Optional[Sequence],
    is_boundary: bool,
    date_format: str,
) -> Optional[bytes]:
    """
    One posterior drawn as the results page draws it: time left to right, so a
    reversed cal BP axis, the unmodelled calibration as a grey shadow behind a
    modelled date, and the HPD levels as nested bars beneath.

    None where there is nothing to draw, so a missing distribution loses its
    figure rather than the whole report.
    """
    ages, probs = _series(distribution)
    if not ages or not probs:
        return None
    peak = max(probs)
    if not peak > 0:
        return None

    figure = Figure(figsize=FIGURE_SIZE_IN, dpi=FIGURE_DPI)
    FigureCanvasAgg(figure)
    ax = figure.add_subplot(111)

    line_colour = BOUNDARY_INK if is_boundary else PRIMARY
    fill_alpha = BOUNDARY_FILL_ALPHA if is_boundary else POSTERIOR_FILL_ALPHA

    all_ages = list(ages)

    # The prior goes down first so the posterior draws over it: what shows
    # through is exactly how far the model moved the date.
    prior_ages, prior_probs = _series(prior_distribution)
    if prior_ages:
        all_ages += prior_ages
        ax.fill_between(prior_ages, 0, prior_probs, color=PRIOR_GREY,
                        alpha=PRIOR_FILL_ALPHA, linewidth=0)
        ax.plot(prior_ages, prior_probs, color=PRIOR_GREY, alpha=PRIOR_LINE_ALPHA,
                linewidth=0.9, label='Prior (unmodelled)')

    ax.fill_between(ages, 0, probs, color=line_colour, alpha=fill_alpha, linewidth=0)
    ax.plot(
        ages, probs, color=line_colour, linewidth=1.4,
        linestyle=':' if is_boundary else '-',
        label='Boundary (modelled transition)' if is_boundary else 'Posterior (modelled)',
    )

    _draw_hpd_bars(ax, hpd_95_ranges, hpd_95_envelope, peak, HPD95_BAR, HPD95_ALPHA, '95.4% HPD')
    _draw_hpd_bars(ax, hpd_68_ranges, hpd_68_envelope, peak, HPD68_BAR, HPD68_ALPHA, '68.3% HPD')

    top = peak
    if prior_probs:
        top = max(top, max(prior_probs))
    # An explicit range, not autoscaling: the bars and the margin under them
    # need guaranteed room below zero, which a tight fit would take back.
    ax.set_ylim((HPD68_BAR[0] - AXIS_BOTTOM_MARGIN) * peak, top * 1.08)

    # Time runs left to right, oldest to youngest, as a chronology is read.
    # cal BP counts backwards from the present, so that is a reversed axis.
    ax.set_xlim(max(all_ages), min(all_ages))

    ticks = _axis_ticks(min(all_ages), max(all_ages), date_format)
    if ticks:
        ax.set_xticks(ticks[0])
        ax.set_xticklabels(ticks[1])

    # The bars live below zero, but "-0.02 probability density" is not a
    # thing: label only the axis the curve is actually measured against and
    # leave the bar strip unlabelled, which is how it reads on screen.
    ax.set_yticks([tick for tick in ax.get_yticks() if tick >= 0])

    ax.set_xlabel(_axis_title(date_format), fontsize=9, color=INK)
    ax.set_ylabel('Probability Density', fontsize=9, color=INK)
    ax.tick_params(labelsize=8, colors=INK)
    ax.grid(True, color=INK, alpha=GRID_ALPHA, linewidth=0.6)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(INK)
        spine.set_alpha(0.25)
    ax.legend(loc='upper right', fontsize=7.5, frameon=False, ncol=4)
    figure.tight_layout(pad=0.6)

    buffer = io.BytesIO()
    figure.savefig(buffer, format='png', dpi=FIGURE_DPI)
    buffer.seek(0)
    return buffer.getvalue()


def _kde_figure(kde: Dict, date_format: str) -> Optional[bytes]:
    """
    The kernel density estimate as the results page draws it: the mean curve
    over a band of its ±1σ across draws, on the same reversed calendar axis as
    every posterior.
    """
    points = kde.get('distribution') or []
    if not points:
        return None
    ages = [float(p[0]) for p in points]
    mean = [float(p[1]) for p in points]
    upper = [float(p[1]) + float(p[2]) for p in points]
    lower = [max(float(p[1]) - float(p[2]), 0.0) for p in points]

    figure = Figure(figsize=FIGURE_SIZE_IN, dpi=FIGURE_DPI)
    FigureCanvasAgg(figure)
    ax = figure.add_subplot(111)
    ax.fill_between(ages, lower, upper, color=PRIMARY, alpha=POSTERIOR_FILL_ALPHA,
                    linewidth=0, label='±1σ')
    ax.plot(ages, mean, color=PRIMARY, linewidth=1.4, label='KDE')
    ax.set_ylim(0, max(upper) * 1.08)
    ax.set_xlim(max(ages), min(ages))
    ticks = _axis_ticks(min(ages), max(ages), date_format)
    if ticks:
        ax.set_xticks(ticks[0])
        ax.set_xticklabels(ticks[1])
    ax.set_xlabel(_axis_title(date_format), fontsize=9, color=INK)
    ax.set_ylabel('Probability Density', fontsize=9, color=INK)
    ax.tick_params(labelsize=8, colors=INK)
    ax.grid(True, color=INK, alpha=GRID_ALPHA, linewidth=0.6)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(INK)
        spine.set_alpha(0.25)
    ax.legend(loc='upper right', fontsize=7.5, frameon=False, ncol=2)
    figure.tight_layout(pad=0.6)

    buffer = io.BytesIO()
    figure.savefig(buffer, format='png', dpi=FIGURE_DPI)
    buffer.seek(0)
    return buffer.getvalue()


# How strongly a probability is inked in the phases-through-time figure,
# mirrored from components/PhaseActivityPlot.tsx: steeper than linear at the
# low end, where most of a phase's strip sits.
ACTIVITY_RAMP = ((0.0, 0.0), (0.02, 0.08), (0.1, 0.3), (0.3, 0.6), (0.6, 0.85), (1.0, 1.0))
ACTIVITY_ROW_IN = 0.26


def _rgb(hex_colour: str) -> Tuple[float, float, float]:
    value = hex_colour.lstrip('#')
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _phase_activity_figure(activity: Dict, date_format: str) -> Optional[Tuple[bytes, float]]:
    """
    One strip per phase on a shared calendar axis, shaded by the probability
    the phase was in progress, as the results page draws it. A single date's
    strip is its own relative probability, in the dates' blue. Returns the PNG
    and its height in inches, which grows with the rows.
    """
    rows = activity.get('rows') or []
    if len(rows) < 2 or not rows[0].get('values'):
        return None
    count = len(rows[0]['values'])
    start, step = float(activity['start']), float(activity['step'])
    oldest, youngest = start + step / 2.0, start - (count - 0.5) * step
    at, strength = zip(*ACTIVITY_RAMP)

    height = 0.7 + ACTIVITY_ROW_IN * len(rows)
    figure = Figure(figsize=(FIGURE_SIZE_IN[0], height), dpi=FIGURE_DPI)
    FigureCanvasAgg(figure)
    ax = figure.add_subplot(111)
    for index, row in enumerate(rows):
        alpha = np.interp(np.asarray(row['values'], dtype=float), at, strength)
        image = np.zeros((1, count, 4))
        image[0, :, :3] = _rgb(PRIMARY if row.get('relative') else PHASE_GREEN)
        image[0, :, 3] = alpha
        # Columns run oldest first, so the image's left edge is the oldest year.
        ax.imshow(image, extent=(oldest, youngest, index + 0.4, index - 0.4),
                  aspect='auto', interpolation='bilinear')
    ax.set_xlim(oldest, youngest)
    ax.set_ylim(len(rows) - 0.5, -0.5)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([
        f"{row['name'].strip()} ({'1 date' if row['dates'] == 1 else str(row['dates']) + ' dates'})"
        if row.get('dates') else row['name'].strip()
        for row in rows
    ])
    ticks = _axis_ticks(youngest, oldest, date_format)
    if ticks:
        ax.set_xticks(ticks[0])
        ax.set_xticklabels(ticks[1])
    ax.set_xlabel(_axis_title(date_format), fontsize=9, color=INK)
    ax.tick_params(labelsize=8, colors=INK, length=2)
    ax.grid(True, axis='x', color=INK, alpha=GRID_ALPHA, linewidth=0.6)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(False)
    figure.tight_layout(pad=0.6)

    buffer = io.BytesIO()
    figure.savefig(buffer, format='png', dpi=FIGURE_DPI)
    buffer.seek(0)
    return buffer.getvalue(), height


def _prior_distribution(sample) -> Optional[Sequence]:
    """
    What a date calibrates to on its own, for the shadow behind its
    modelled posterior.

    Derived rather than read from a column, for the reason api/analysis.py
    derives it for the screen: it is a cheap analytic calibration fixed
    entirely by the sample's current age/error/curve, so deriving it cannot
    fall out of step with the measurement the way a stored copy would.
    """
    if sample is None:
        return None
    try:
        curve = get_curve(sample.curve_id)
        cal_ages, probs = curve.calibrate(
            sample.age_bp, sample.error_1sigma, sample.delta_r, sample.delta_r_error,
        )
        return curve.get_display_distribution(cal_ages, probs)
    except Exception:
        # A curve that no longer resolves costs this date its grey shadow,
        # not the report. Every modelled figure on the page is unaffected.
        return None


# --- tables --------------------------------------------------------------

def _scaled_widths(weights: List[float]) -> List[float]:
    """Column weights scaled to fill the printable width exactly."""
    total = sum(weights) or 1
    return [CONTENT_WIDTH * w / total for w in weights]


def _has_any(rows: List[Dict], key: str) -> bool:
    """Whether any exported row carries a value for `key`.

    Columns are chosen by what a run produced rather than by its declared type,
    so the report never prints a column of blanks and never drops one that has
    content.
    """
    return any(row.get(key) is not None for row in rows if row)


def _results_table(
    display_rows: List[Dict],
    columns: List[Tuple[str, str, float]],
    styles: Dict[str, ParagraphStyle],
) -> Table:
    """
    The results table: one row per modelled entity in the model's own reading
    order, dates and Boundaries reporting through the same columns.

    Structure rows are indented by depth and set in bold, which is what carries
    the model's shape in a document with no expandable tree -- reading order
    alone does not show that three dates sit inside a phase inside a sequence.
    """
    header = [Paragraph(label, styles['th']) for _, label, _ in columns]
    data: List[List[Any]] = [header]

    structural_indices: List[int] = []
    boundary_indices: List[int] = []

    for index, row in enumerate(display_rows, start=1):
        kind = row.get('kind')
        if kind == 'boundary':
            boundary_indices.append(index)
        elif kind not in ('date', None):
            structural_indices.append(index)

        cells: List[Any] = []
        for key, _, _ in columns:
            value = row.get(key, '')
            if key == 'name':
                style = ParagraphStyle(
                    f"RcName{index}", parent=styles['td'],
                    leftIndent=6 * float(row.get('depth') or 0),
                    fontName='Helvetica' if kind == 'date' else 'Helvetica-Bold',
                )
                cells.append(Paragraph(str(value), style))
            else:
                cells.append(Paragraph(str(value), styles['td']))
        data.append(cells)

    table = Table(data, colWidths=_scaled_widths([w for _, _, w in columns]), repeatRows=1)

    style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f2efec')),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, colors.HexColor(INK)),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#ded7d2')),
    ]
    for index in structural_indices:
        style.append(('BACKGROUND', (0, index), (-1, index), colors.HexColor('#f7f5f3')))
    for index in boundary_indices:
        # A Boundary is coral here for the same reason it is coral in the
        # matrix and in its plot: one object, one colour, everywhere.
        style.append(('TEXTCOLOR', (0, index), (-1, index), colors.HexColor(BOUNDARY_INK)))
    table.setStyle(TableStyle(style))
    return table


def _context_spans_table(
    context_spans: List[Dict], date_format: str, styles: Dict[str, ParagraphStyle]
) -> Table:
    """
    Each context's modelled start and end -- the OxCal-style Boundary
    parameters bracketing it.

    Only reached when the model carries no explicit Boundary elements of
    its own, since adjacent contexts SHARE a boundary: with them in the
    results table already, printing this too would state the same
    transition twice, once as one context's end and again as the next
    one's start. The results page suppresses it on the same test.
    """
    header = ['Context', 'Modelled Start (95.4% HPD)', 'Modelled End (95.4% HPD)',
              'Start Mean', 'End Mean']
    data: List[List[Any]] = [[Paragraph(label, styles['th']) for label in header]]

    def span_text(posterior: Dict) -> str:
        envelope = posterior.get('hpd_95') or (None, None)
        return _hpd_text(posterior.get('hpd_95_ranges'), envelope[0], envelope[1], date_format)

    for span in context_spans:
        start, end = span.get('start') or {}, span.get('end') or {}
        data.append([
            Paragraph(str(span.get('name') or ''), styles['td']),
            Paragraph(span_text(start), styles['td']),
            Paragraph(span_text(end), styles['td']),
            Paragraph(_age_text(start.get('posterior_mean'), date_format), styles['td']),
            Paragraph(_age_text(end.get('posterior_mean'), date_format), styles['td']),
        ])

    table = Table(data, colWidths=_scaled_widths([2.2, 3.4, 3.4, 1.6, 1.6]), repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f2efec')),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, colors.HexColor(INK)),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#ded7d2')),
    ]))
    return table


_INTERVAL_LABELS = {
    'sequence_duration': 'Sequence',
    'phase_duration': 'Phase',
    'hiatus_duration': 'Hiatus',
    'sample_duration': 'Dated events',
}


def _years_text(ranges: Optional[List[Dict]]) -> str:
    """A duration's HPD, smallest first -- a length reads upwards, unlike the
    calendar ranges _hpd_text writes oldest first."""
    if not ranges:
        return ''
    return '; '.join(
        f"{r['min']:.0f}-{r['max']:.0f} yr ({r['probability_percent']:.1f}%)"
        for r in sorted(ranges, key=lambda r: r['min'])
    )


def _intervals_table(records: List[Dict], columns: List[Tuple[str, float]],
                     cells, styles: Dict[str, ParagraphStyle]) -> Table:
    """One table for durations, another for differences, sharing a look."""
    data: List[List[Any]] = [[Paragraph(label, styles['th']) for label, _ in columns]]
    for record in records:
        data.append([Paragraph(str(value), styles['td']) for value in cells(record)])
    table = Table(data, colWidths=_scaled_widths([w for _, w in columns]), repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f2efec')),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, colors.HexColor(INK)),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#ded7d2')),
    ]))
    return table


def _durations_table(records: List[Dict], styles: Dict[str, ParagraphStyle]) -> Table:
    columns = [('What', 1.4), ('Name', 2.4), ('Mean (yr)', 1.2),
               ('68.3% (yr)', 2.6), ('95.4% (yr)', 2.6)]

    def cells(record: Dict) -> List[Any]:
        years = record.get('years') or {}
        return [
            _INTERVAL_LABELS.get(record.get('kind'), record.get('kind') or ''),
            record.get('name') or '',
            _number(years.get('mean')),
            _years_text(years.get('hpd_68_ranges')),
            _years_text(years.get('hpd_95_ranges')),
        ]

    return _intervals_table(records, columns, cells, styles)


def _differences_table(records: List[Dict], styles: Dict[str, ParagraphStyle]) -> Table:
    columns = [('', 2.0), ('Compared with', 2.0), ('Mean (yr)', 1.1),
               ('95.4% (yr)', 2.4), ('Overlap', 1.5)]

    def cells(record: Dict) -> List[Any]:
        years = record.get('years') or {}
        overlap = record.get('overlap')
        if record.get('shared_boundary'):
            # Both figures are zero by construction. See
            # intervals.SHARED_BOUNDARY_TEXT.
            return [
                record.get('from_name') or '',
                record.get('to_name') or '',
                '', SHARED_BOUNDARY_TEXT, '',
            ]
        word = record.get('severity') or ''
        if overlap is not None:
            word = f"{word} ({overlap * 100:.0f}%)"
        return [
            record.get('from_name') or '',
            record.get('to_name') or '',
            _number(years.get('mean')),
            _years_text(years.get('hpd_95_ranges')),
            word,
        ]

    return _intervals_table(records, columns, cells, styles)


def _same_event_table(tests: List[Dict], date_format: str,
                      styles: Dict[str, ParagraphStyle]) -> Table:
    """One row per same-event test: its figures, the verdict, and the pooled
    age where the dates pass."""
    columns = [('Dates', 0.8), ('Pooled age BP', 1.4), ('T (5% critical)', 1.8),
               ('Result', 2.6), ('Pooled 95.4%', 3.0)]

    def cells(test: Dict) -> List[Any]:
        tested = test.get('t') is not None
        pooled = statistics.pooled_ranges_at(test, SIGMA_2)
        return [
            test['n'],
            f"{test['mean_bp']:.0f} ± {test['error_bp']:.0f}" if tested else '',
            f"{test['t']:.1f} ({test['critical']:.2f}, df {test['df']})" if tested else '',
            statistics.verdict(test),
            _hpd_text(pooled, None, None, date_format) if pooled else '',
        ]

    return _intervals_table(tests, columns, cells, styles)


def _band_text(band: Optional[Sequence[float]], date_format: str) -> str:
    """
    One age-depth band as text.

    Not _hpd_text: a band is a pair of QUANTILES rather than an HPD region, so
    it is one contiguous interval by construction and has no per-range
    breakdown to preserve.
    """
    if not band or len(band) < 2:
        return ''
    return date_format_service.format_range(max(band), min(band), date_format)


def _age_depth_table(record: Dict, date_format: str,
                     styles: Dict[str, ParagraphStyle]) -> Table:
    units = record.get('depth_units') or 'cm'
    columns = [('Level', 2.6), (f'Depth ({units})', 1.2), ('Median', 1.8),
               ('68.3%', 2.2), ('95.4%', 2.2)]

    def cells(level: Dict) -> List[Any]:
        return [
            f"{level.get('name') or ''}{'' if level.get('dated') else ' (undated)'}",
            _number(level.get('depth'), 1),
            _age_text(level.get('median'), date_format),
            _band_text(level.get('band_68'), date_format),
            _band_text(level.get('band_95'), date_format),
        ]

    return _intervals_table(record.get('levels') or [], columns, cells, styles)


def _accumulation_table(record: Dict, styles: Dict[str, ParagraphStyle]) -> Table:
    units = record.get('depth_units') or 'cm'
    columns = [('Between', 3.2), (units, 1.0), ('Years', 1.6),
               (f'Rate (yr/{units})', 1.6), ('68.3% rate', 1.8)]

    def cells(step: Dict) -> List[Any]:
        years = step.get('years') or {}
        band = step.get('rate_band_68') or []
        return [
            f"{step.get('from_name') or ''} to {step.get('to_name') or ''}",
            _number(step.get('depth_gap'), 1),
            _number(years.get('median')),
            _number(step.get('rate_median'), 1),
            f"{min(band):.1f}-{max(band):.1f}" if band else '',
        ]

    return _intervals_table(record.get('accumulation') or [], columns, cells, styles)


# --- narrative -----------------------------------------------------------

_SOLVER_NAMES = {
    'exact': 'exact integration',
    'gibbs': "OxCal's MH/Gibbs sampler",
    'wiggle_match': 'the closed-form wiggle match',
}


def _curve_sentences(curve_ids: Sequence[str]) -> List[str]:
    """
    Each curve the run calibrated against, named and cited.

    Read from the curve registry rather than from a table of names kept
    here, so a report cites an imported curve by the description it was
    given and cannot describe a curve this app no longer has.
    """
    from app.services import curve_registry

    sentences = []
    for curve_id in curve_ids:
        try:
            spec = curve_registry.get_spec(curve_id)
        except Exception:
            # A curve that has since been renamed or removed still gets
            # named by the id the analysis actually used -- which is the
            # provenance that matters -- rather than dropping the line.
            sentences.append(f"<b>{curve_id}</b> (no longer available in this installation).")
            continue
        reference = f" {spec.reference}" if spec.reference else ''
        doi = f" doi:{spec.doi}" if spec.doi else ''
        sentences.append(f"<b>{spec.name}</b> ({spec.id}).{reference}{doi}")
    return sentences


def _solver_sentence(solver: Optional[Dict]) -> Optional[str]:
    """
    How the posteriors were computed. Worth stating because the two solvers
    answer the same question by different means, and which one ran is a
    property of the model's shape rather than of what was asked for.
    """
    if not solver:
        return None
    method = _SOLVER_NAMES.get(solver.get('method'), solver.get('method') or 'unknown method')
    sentence = f"Solved by {method}."
    reason = solver.get('reason')
    if solver.get('method') == 'gibbs' and reason:
        sentence += f" The model could not be integrated exactly: {reason}"
        if not sentence.endswith('.'):
            sentence += '.'
    if solver.get('converged') is False:
        sentence += ' The sampler did not meet its convergence criterion.'
    # The whole model's agreement with its dates, both of OxCal's indices, so a
    # printed report carries the number a model is screened on rather than only
    # the per-date column in the table. See services/agreement.py for what
    # separates them.
    model_agreement = solver.get('model_agreement')
    overall_agreement = solver.get('overall_agreement')
    if model_agreement is not None or overall_agreement is not None:
        quoted = ', '.join(
            f'{label} {value:.0f}%'
            # Paragraph markup: set as OxCal's literature writes them, with
            # the qualifier subscripted, matching the results page.
            for label, value in (('A<sub>model</sub>', model_agreement),
                                 ('A<sub>overall</sub>', overall_agreement))
            if value is not None
        )
        sentence += f' Agreement over the whole model: {quoted}.'
    if solver.get('method') == 'wiggle_match':
        # The check that the match is REAL rather than a diagnostic beside it:
        # the gaps are asserted, so a member at the wrong ring cannot be
        # absorbed. Quoted against OxCal's A_n where the run recorded it;
        # analyses solved before it was reported carry the index alone.
        combination = solver.get('combination_agreement')
        if combination is not None:
            threshold = solver.get('combination_threshold')
            against = ('' if threshold is None
                       else f" (pass mark A<sub>n</sub> {threshold:.0f}%)")
            sentence += (
                f" The series spans {solver.get('series_span', 0):g}"
                f" {solver.get('gap_units') or 'rings'} and its dates agree with"
                f" their own calibrations at a combination index of"
                f" {combination:.0f}%{against}."
            )
    for deposition in solver.get('deposition') or []:
        if deposition.get('k_mean') is None:
            continue
        sentence += (
            f" {deposition.get('name') or 'The deposition sequence'} was modelled at"
            f" a rate of about {deposition['k_mean']:.2g} events per"
            f" {deposition.get('depth_units') or 'cm'}"
            f"{', inferred from the dates' if deposition.get('variable') else ', as set'}."
        )
        if deposition.get('pinned'):
            sentence += (
                ' That rate ran up against the end of the range it was allowed,'
                ' so the prior is shaping the answer as much as the dates are.'
            )
    return sentence


def _page_furniture(canvas, doc) -> None:
    """A footer on every page: which analysis this is, and where you are in it.
    An eight-page results table gets quoted from and emailed page by page."""
    canvas.saveState()
    canvas.setFont('Helvetica', 7)
    canvas.setFillColor(colors.HexColor('#6b625d'))
    canvas.drawString(MARGIN, MARGIN * 0.6, doc.radical_footer)
    canvas.drawRightString(PAGE_SIZE[0] - MARGIN, MARGIN * 0.6, f"Page {doc.page}")
    canvas.restoreState()


# --- assembly ------------------------------------------------------------

def _display_row(row: Dict, date_format: str) -> Dict:
    """
    One structure row reduced to the strings the table prints. Every column is
    derived here, so the table builder only chooses which to show, and a date
    and a Boundary come out with the same keys.
    """
    result = row.get('result') or {}
    return {
        'kind': row.get('kind'),
        'depth': row.get('depth') or 0,
        'order': str(row.get('order') or ''),
        'name': row.get('name') or '',
        'age': _measurement(result),
        'curve': (result.get('curve_id') or '') if result else '',
        'unmodelled': _hpd_text(result.get('unmodelled_hpd_95_ranges'), None, None, date_format),
        'mean': _age_text(result.get('posterior_mean'), date_format),
        'median': _age_text(result.get('posterior_median'), date_format),
        'hpd68': _hpd_text(result.get('hpd_68_ranges'), result.get('hpd_68_min'),
                           result.get('hpd_68_max'), date_format),
        'hpd95': _hpd_text(result.get('hpd_95_ranges'), result.get('hpd_95_min'),
                           result.get('hpd_95_max'), date_format),
        # Blank rather than zero where there is none: a Boundary has no
        # unmodelled counterpart to agree with, and a plain calibration has
        # no model. Neither is a score of zero.
        'agreement': _number(result.get('agreement_index'), 1),
        'outlier': (_number(100 * result['outlier_probability'], 1)
                    if result.get('outlier_probability') is not None else ''),
        'tpq': _age_text(row.get('tpq_age_bp'), date_format),
        'taq': _age_text(row.get('taq_age_bp'), date_format),
        # Already rounded to the project's declared precision by
        # api/results._gather_export_rows -- printed here, never rounded here,
        # so there is one place the rule lives and one place to check it.
        'position': _position_text(result),
    }


def _position_text(result: Optional[Dict]) -> str:
    """
    A date's position as one cell, "54.5, -5.6", which is how a coordinate is
    written when it is read rather than computed -- and one column narrower
    than splitting it, on a page that is already spending its width on HPDs.

    Printed as the number itself, so the figure in the report is the figure in
    the CSV beside it; what precision it was rounded to is stated once, in the
    method note, rather than implied by trailing zeros here.
    """
    if not result:
        return ''
    latitude, longitude = result.get('latitude'), result.get('longitude')
    if latitude is None or longitude is None:
        return ''
    return f"{latitude}, {longitude}"


def _columns(
    result_rows: List[Dict], is_bayesian: bool, has_limiters: bool
) -> List[Tuple[str, str, float]]:
    """
    Which columns this run has something to say in, as (key, label, width
    weight).

    See _has_any: a column earns its width by having content. The
    alternative -- a fixed twelve-column layout -- spends a fifth of a
    landscape page on two columns of blanks for every plain calibration.
    """
    columns: List[Tuple[str, str, float]] = [
        ('order', '#', 0.5),
        ('name', 'Structure / Sample', 3.2),
        # Measurement and error in one column, as "389 ± 21" -- the way a
        # published radiocarbon table sets them, and one column narrower
        # than splitting them. The error is 1-sigma, which the method note
        # states: the symbol itself is unavailable, since the report's
        # Helvetica has no sigma and ReportLab quietly substitutes a Latin
        # "s" for it, which reads as a unit rather than as what it replaced.
        ('age', 'Radiocarbon Age', 1.4),
        ('curve', 'Curve', 1.1),
    ]
    if is_bayesian and _has_any(result_rows, 'unmodelled_hpd_95_ranges'):
        columns.append(('unmodelled', 'Unmodelled 95.4% HPD', 2.6))
    if _has_any(result_rows, 'posterior_mean'):
        columns.append(('mean', 'Mean', 1.2))
    if _has_any(result_rows, 'posterior_median'):
        columns.append(('median', 'Median', 1.2))
    columns.append(('hpd68', '68.3% HPD', 2.6))
    columns.append(('hpd95', '95.4% HPD', 2.6))
    # Only where the run has positions to print, by the same rule as every
    # other optional column here -- and a report of unplaced dates should not
    # advertise a column for something the project has chosen not to record.
    if _has_any(result_rows, 'latitude'):
        columns.append(('position', 'Position', 1.8))
    if _has_any(result_rows, 'agreement_index'):
        columns.append(('agreement', 'Agree %', 1.2))
    if _has_any(result_rows, 'outlier_probability'):
        columns.append(('outlier', 'Outlier %', 1.2))
    # The age limiters are inputs rather than results, but they are part of
    # the model that produced these numbers, so a report that omits them
    # cannot be checked against the run it describes.
    if has_limiters:
        columns.append(('tpq', 'TPQ', 1.2))
        columns.append(('taq', 'TAQ', 1.2))
    return columns


def _ordered_rows(structure: List[Dict], rows: List[Dict]) -> List[Dict]:
    """
    The rows to report, in reading order, numbered. Falls back to the flat
    list of dates when an analysis has no stored structure, as the CSV does:
    an export with no structure should still carry the results.
    """
    if structure:
        ordered = []
        for order, row in enumerate(structure, start=1):
            entry = dict(row)
            entry['order'] = order
            ordered.append(entry)
        return ordered

    return [
        {
            'kind': 'date',
            'depth': 0,
            'order': order,
            'name': row.get('sample_name') or row.get('sample_id') or '',
            'result': row,
            'sample': {'id': row.get('sample_id')},
            'tpq_age_bp': None,
            'taq_age_bp': None,
        }
        for order, row in enumerate(rows, start=1)
    ]


def _boundary_result(posterior: Dict) -> Dict:
    """A Boundary's posterior reshaped like a result row, so it reports
    through the same cells a date does."""
    hpd_68 = posterior.get('hpd_68') or (None, None)
    hpd_95 = posterior.get('hpd_95') or (None, None)
    return {
        'posterior_mean': posterior.get('posterior_mean'),
        'posterior_median': posterior.get('posterior_median'),
        'posterior_std': posterior.get('posterior_std'),
        'hpd_68_ranges': posterior.get('hpd_68_ranges'),
        'hpd_95_ranges': posterior.get('hpd_95_ranges'),
        'hpd_68_min': hpd_68[0],
        'hpd_68_max': hpd_68[1],
        'hpd_95_min': hpd_95[0],
        'hpd_95_max': hpd_95[1],
        'probability_distribution': posterior.get('probability_distribution'),
        'agreement_index': None,
        'outlier_probability': None,
    }


def render_report(
    analysis,
    structure: List[Dict],
    rows: List[Dict],
    samples_by_id: Dict[str, Any],
    distributions: Dict[str, Any],
    context_spans: List[Dict],
    intervals: Optional[Dict] = None,
    summaries: Optional[Dict] = None,
    same_event: Optional[List[Dict]] = None,
    p_before: Optional[Dict] = None,
    project_name: Optional[str] = None,
    date_format: Optional[str] = None,
    coordinate_precision: str = 'full',
) -> bytes:
    """
    A completed analysis as a PDF, returned as bytes.

    `structure` and `rows` are what api/results.py assembles for the CSV and
    JSON exports, and `context_spans` the modelled start/end on each
    SampleGroup. Nothing is re-derived from the database, so the PDF cannot
    report a different run from the CSV downloaded beside it.

    `intervals` is the durations-and-differences block stored on the analysis
    (services/intervals.py), passed in rather than read here for the same
    reason as everything else: the report describes the run the CSV beside it
    describes. None when the run was not asked for them, in which case the
    report simply has no such section -- an empty one would read as "nothing
    lasted any time", which is not what a missing option means.

    `summaries` is the analysis's summaries block (a kernel density
    estimate, services/kde.py), passed in on the same terms as `intervals`.

    `p_before` is the results page's "P before" column when it was showing
    ({cal_bp, samples, elements}, from api/results._p_before), which adds it
    to the results table.

    `same_event` is a group calibration's same-event test
    (services/statistics.py), computed by the same call as the other exports.

    `coordinate_precision` is the project's setting, and is used only to SAY
    what it is: the positions in `rows` have already been rounded to it by
    api/results._gather_export_rows, which is deliberately the one place that
    can be done. A report that printed a rounded position without saying so
    would be read as the site's recorded location.

    `distributions` maps sample id to that date's posterior series, and is
    passed separately because the shared assembly deliberately leaves it
    out: it is the one field a spreadsheet has no use for, and adding it to
    those rows to reach it here would put a few hundred numbers per date
    into the JSON export as a side effect. A Boundary needs no such help --
    its series travels on the posterior stored with the element.

    An analysis with no results still produces a valid PDF saying so, in
    the same spirit as the CSV's "No results" row: a caller asking for a
    report of a pending or failed run should get an answer, not a download
    that fails.
    """
    date_format = date_format_service.normalise(date_format)
    styles = _styles()
    is_bayesian = analysis.analysis_type == 'bayesian'

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=PAGE_SIZE,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN * 1.4,
        title=f"RadiCal report: {analysis.name}",
        author='RadiCal',
        subject=f"Radiocarbon analysis results ({analysis.analysis_type})",
    )
    doc.radical_footer = f"RadiCal — {analysis.name} ({analysis.id})"

    story: List[Any] = []
    story.append(Paragraph(str(analysis.name), styles['title']))

    created = analysis.created_at.strftime('%Y-%m-%d') if analysis.created_at else 'unknown date'
    descriptors = [
        {'bayesian': 'Bayesian chronological model',
         'group_calibration': 'Group calibration',
         'calibration': 'Calibration'}.get(analysis.analysis_type, analysis.analysis_type),
        f"status: {analysis.status}",
        f"created {created}",
    ]
    if project_name:
        descriptors.insert(1, f"project: {project_name}")
    story.append(Paragraph(' · '.join(descriptors), styles['subtitle']))

    ordered = _ordered_rows(structure, rows)

    if not ordered:
        story.append(Paragraph(
            f"This analysis has no results to report (status: {analysis.status}). "
            "Run it, then export the report again.",
            styles['body'],
        ))
        doc.build(story, onFirstPage=_page_furniture, onLaterPages=_page_furniture)
        return buffer.getvalue()

    # Attach each Boundary's posterior in result shape before anything reads
    # a row, so the table and the figures agree on what a Boundary carries.
    for row in ordered:
        if row.get('kind') != 'date' and row.get('posterior'):
            row['result'] = _boundary_result(row['posterior'])

    result_rows = [row.get('result') for row in ordered if row.get('result')]
    display_rows = [_display_row(row, date_format) for row in ordered]
    if p_before:
        for row, shown in zip(ordered, display_rows):
            if row.get('kind') == 'date':
                p = p_before['samples'].get((row.get('sample') or {}).get('id'))
            else:
                p = p_before['elements'].get(row.get('element_id'))
            shown['p_before'] = statistics.format_probability(p)

    # --- method note
    story.append(Paragraph('Method', styles['heading']))
    curve_ids = []
    for row in rows:
        curve_id = row.get('curve_id')
        if curve_id and curve_id not in curve_ids:
            curve_ids.append(curve_id)
    for sentence in _curve_sentences(curve_ids):
        story.append(Paragraph(f"Calibrated against {sentence}", styles['body']))

    solver_sentence = _solver_sentence(analysis.solver)
    if solver_sentence:
        story.append(Paragraph(solver_sentence, styles['body']))

    era = date_format_service.ERA_LABELS.get(date_format, 'cal BP')
    story.append(Paragraph(
        "Radiocarbon ages are conventional ages BP with their 1-sigma measurement error. "
        f"Calendar ages are reported in {era}. Highest Posterior Density (HPD) regions are given "
        "at 68.3% and 95.4% -- the coverages of one and two standard deviations; where a region "
        "falls in more than one part, each part is listed with its "
        "own share of the probability rather than being collapsed into a single span.",
        styles['note'],
    ))

    if _has_any(result_rows, 'latitude'):
        rounded = coordinate_precision != 'full'
        story.append(Paragraph(
            "Positions are WGS84 decimal degrees. "
            + (f"They have been rounded to the nearest {coordinate_precision}"
               " degree for this report, at the project's setting, and are not "
               "the recorded site locations."
               if rounded else
               "They are given at the precision they were recorded."),
            styles['note'],
        ))

    # --- results table
    story.append(Paragraph('Results', styles['heading']))
    has_limiters = any(
        row.get('tpq_age_bp') is not None or row.get('taq_age_bp') is not None
        for row in ordered
    )
    columns = _columns(result_rows, is_bayesian, has_limiters)
    if p_before:
        threshold = date_format_service.format_age(p_before['cal_bp'], date_format)
        columns.append(('p_before', f'P before {threshold}', 1.3))
    story.append(_results_table(display_rows, columns, styles))

    # --- context spans, where no explicit Boundary already reports them
    has_boundaries = any(row.get('kind') == 'boundary' for row in ordered)
    if context_spans and not has_boundaries:
        story.append(Paragraph('Context Spans', styles['heading']))
        story.append(Paragraph(
            "Modelled start and end of each context (OxCal-style Boundary parameters). These may "
            "extend beyond the oldest and youngest dated sample, since the dated samples are "
            "treated as a partial sample of the context's true duration.",
            styles['note'],
        ))
        story.append(_context_spans_table(context_spans, date_format, styles))

    # --- durations and differences, when the run was asked for them
    durations = (intervals or {}).get('durations') or []
    differences = (intervals or {}).get('differences') or []
    if durations or differences:
        story.append(Paragraph('Durations and Differences', styles['heading']))
        story.append(Paragraph(
            "Lengths of time, in years, rather than calendar dates -- so these are not "
            "converted to the reporting era. A phase's duration is the interval its "
            "Boundaries enclose and is longer than the span of its dated events, since "
            "those events are a sample of the phase rather than the whole of it. "
            f"Estimated from {(intervals or {}).get('draws') or 0:,} joint draws, so unlike "
            "every other figure in this report they carry a Monte Carlo error.",
            styles['note'],
        ))
        if durations:
            story.append(_durations_table(durations, styles))
        drawn = (_phase_activity_figure(intervals['phase_activity'], date_format)
                 if (intervals or {}).get('phase_activity') else None)
        if drawn:
            png, height = drawn
            story.append(KeepTogether([
                Paragraph('Phases through time', styles['caption']),
                Paragraph(
                    "Shading is the probability each phase was in progress in each "
                    "year. A phase represented by a single date shows that date's own "
                    "probability, relative to its peak.",
                    styles['note'],
                ),
                Image(io.BytesIO(png), width=CONTENT_WIDTH,
                      height=CONTENT_WIDTH * height / FIGURE_SIZE_IN[0]),
            ]))
        if differences:
            story.append(Paragraph(
                "Differences are positive for a gap and negative for an overlap. "
                "\"Overlap\" is the probability that the two cannot be separated in "
                "time -- for two phases, that their modelled intervals intersect; for "
                "two dates, that the model cannot say which came first -- graded at the "
                "same 68.3% and 95.4% levels the HPD regions use. Phases are compared "
                "only where the model leaves their order open, as in parallel branches."
                # Only an analysis stored before ordered pairs were dropped has these.
                + (f" Two phases either side of one shared Boundary read "
                   f"\"{SHARED_BOUNDARY_TEXT}\" instead: one phase's end is the next "
                   "one's start, so the model gives them no gap and no overlap to measure."
                   if any(d.get('shared_boundary') for d in differences) else ""),
                styles['note'],
            ))
            story.append(_differences_table(differences, styles))
        if (intervals or {}).get('note'):
            story.append(Paragraph(str(intervals['note']), styles['note']))

    # --- age against depth, when the model has a deposition sequence in it
    for record in ((intervals or {}).get('age_depth') or []):
        story.append(Paragraph(f"Age and Depth: {record.get('name') or ''}",
                               styles['heading']))
        story.append(Paragraph(
            "Age against depth, and how fast the deposit accumulated between one "
            "level and the next. The bands are quantiles rather than HPD regions, "
            "so they nest; the rates are read off the same joint draws as the ages "
            "above them, since a rate is a function of two ages at once and pairing "
            "their separate posteriors would give an answer wrong in shape.",
            styles['note'],
        ))
        story.append(_age_depth_table(record, date_format, styles))
        if record.get('accumulation'):
            story.append(_accumulation_table(record, styles))

    # --- the kernel density estimates, when the run was asked for them
    kde_blocks: List[Any] = []
    for kde in ((summaries or {}).get('kde') or []):
        png = _kde_figure(kde, date_format)
        if png is None:
            continue
        width = kde['bandwidth']
        block = []
        if kde.get('name'):
            block.append(Paragraph(f"Phase: {kde['name']}", styles['caption']))
        block += [
            Paragraph(
                f"{kde['dates']} dates, {kde['draws']:,} draws. Kernel width "
                f"{width['median']:.0f} years (68.3%: {width['range_68'][0]:.0f}"
                f"–{width['range_68'][1]:.0f})"
                + (", reflected at the phase's Boundaries." if kde.get('reflected') else "."),
                styles['note'],
            ),
            Image(io.BytesIO(png), width=CONTENT_WIDTH,
                  height=CONTENT_WIDTH * FIGURE_SIZE_IN[1] / FIGURE_SIZE_IN[0]),
        ]
        kde_blocks.append(KeepTogether(block))
    if kde_blocks:
        story.append(Paragraph('Kernel Density Estimates', styles['heading']))
        story.append(Paragraph(
            "How the dates are spread through time, as OxCal's KDE_Plot (Bronk Ramsey "
            "2017): the mean of many kernel density estimates, each of one joint draw "
            "of the dates, with their ±1σ as a band.",
            styles['note'],
        ))
        story.extend(kde_blocks)

    # --- whether a group calibration's dates could be one event
    if same_event:
        story.append(Paragraph('Same-Event Test', styles['heading']))
        story.append(Paragraph(
            "Ward and Wilson's (1978) test of whether a set of radiocarbon ages could "
            "date one event, as OxCal's R_Combine: T against the chi-squared critical "
            "value at 5%. The pooled age is the error-weighted mean, calibrated only "
            "where the dates pass.",
            styles['note'],
        ))
        story.append(_same_event_table(same_event, date_format, styles))

    # --- one figure per modelled parameter
    figures: List[Any] = []
    for row in ordered:
        result = row.get('result')
        if not result:
            continue
        is_boundary = row.get('kind') == 'boundary'
        sample_id = (row.get('sample') or {}).get('id')
        sample = samples_by_id.get(sample_id) if sample_id else None
        distribution = (result.get('probability_distribution') if is_boundary
                        else distributions.get(sample_id))
        png = _posterior_figure(
            distribution,
            result.get('hpd_68_ranges'),
            result.get('hpd_95_ranges'),
            (result.get('hpd_68_min'), result.get('hpd_68_max')),
            (result.get('hpd_95_min'), result.get('hpd_95_max')),
            _prior_distribution(sample) if is_bayesian and not is_boundary else None,
            is_boundary,
            date_format,
        )
        if png is None:
            continue

        caption = row.get('name') or ''
        path = row.get('path')
        image = Image(io.BytesIO(png), width=CONTENT_WIDTH,
                      height=CONTENT_WIDTH * FIGURE_SIZE_IN[1] / FIGURE_SIZE_IN[0])
        block = [Paragraph(str(caption), styles['caption'])]
        if path:
            block.append(Paragraph(str(path), styles['note']))
        block.append(image)
        # Kept together so a caption never sits at the foot of one page with
        # its figure at the head of the next.
        figures.append(KeepTogether(block))

    if figures:
        story.append(PageBreak())
        story.append(Paragraph('Posterior Distributions', styles['heading']))
        story.extend(figures)

    doc.build(story, onFirstPage=_page_furniture, onLaterPages=_page_furniture)
    return buffer.getvalue()
