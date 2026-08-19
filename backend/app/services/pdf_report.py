"""
A completed analysis rendered as a printable report.

This is the citable, shareable form of a run: the same rows the results
page and the CSV export show, in the model's own reading order, with each
date's posterior drawn beside them and a method note saying which curves
and which solver actually produced the numbers.

It renders from data the caller has already assembled -- nothing here
touches the database. `api/results.py` owns the assembly (_gather_export_rows
and _structure_rows), and hands the same structures to the CSV, the JSON and
this, so the three cannot disagree about what a run says. The one thing this
module derives for itself is the unmodelled calibration series behind each
Bayesian date, which is a plot input rather than a table value and is
computed the same way `api/analysis.py` computes it for the screen.

Two deliberate duplications, both of the same kind the date_format module
already carries and for the same reason -- the alternative is shipping the
report's assembly to the browser:

  * The plot palette and the HPD-bar geometry are mirrored from
    frontend/src/theme/plotColors.ts and components/PosteriorPlot.tsx, so a
    figure in the PDF is the figure on screen. Both are small sets of
    constants that only move when someone deliberately restyles the plots.

  * The calendar tick selection is mirrored from
    frontend/src/utils/dateFormat.ts, because ticks at round cal BP values
    labelled in BC/AD read "1051 BC" where a reader expects "1000 BC".

Figures are drawn through matplotlib's object API rather than pyplot: pyplot
keeps every figure it makes in a global registry, which in a long-lived
server process is a memory leak waiting for someone to forget a close(), and
it carries GUI backend selection this has no use for.
"""
from __future__ import annotations

import io
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
from app.services.calibration import get_curve

# --- page geometry -------------------------------------------------------

# Landscape, because the results table is the point of the document and it
# carries up to twelve columns, several of them holding a multimodal HPD
# like "3329-3294 cal BP (9.4%); 3253-3103 cal BP (77.6%)". Portrait fits
# that only by wrapping every one of them onto three lines.
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

# The HPD bars sit in their own strip BELOW the curve's zero baseline, as
# fractions of the posterior's peak: curve, gap, 95% bar, gap, 68% bar,
# then clear space before the axis. Stacking translucent fills under the
# curve instead goes muddy three deep, which is what this avoids.
HPD95_BAR = (-0.11, -0.04)
HPD68_BAR = (-0.22, -0.15)
AXIS_BOTTOM_MARGIN = 0.07

# Sized to the printable width in landscape. 200 dpi over ten inches is
# about 210 effective dpi at the size it lands on the page -- a real figure
# rather than the screenshot a 1x export would give.
FIGURE_SIZE_IN = (10.0, 2.8)
FIGURE_DPI = 200


def _styles() -> Dict[str, ParagraphStyle]:
    """
    The document's text styles.

    Built from ReportLab's sample sheet so the metrics stay sane, but
    recoloured onto the app's warm ink: the report should look like the app
    it came from rather than like a library's defaults.
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

    Prefers the per-range breakdown, which is what makes a multimodal
    posterior honest: collapsing "two peaks 200 years apart" to one span
    claims the gap between them as part of the region. The min/max envelope
    is only a fallback for rows written before the breakdown existed --
    the same fallback the CSV export makes, so an old analysis reports the
    same figure in both.
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
    What was measured, as "389 ± 21".

    Blank for a structural row and for a Boundary: a Boundary is a modelled
    parameter with no measurement behind it, and an empty cell says that
    better than a zero would.
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
    One HPD level as solid bars in the strip below the curve.

    Each disjoint range gets its own bar rather than one bar spanning the
    whole envelope, for the same reason the text breakdown keeps them
    separate. Only the first carries the legend label, so a posterior in
    four pieces still produces one legend row.
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
    One posterior drawn the way the results page draws it: time running
    left to right (which means a reversed cal BP axis), the unmodelled
    calibration as a grey shadow behind a modelled date, and the HPD levels
    as nested bars beneath.

    Returns None where there is nothing to draw, so a run with a missing or
    empty distribution loses its figure rather than the whole report.
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

    _draw_hpd_bars(ax, hpd_95_ranges, hpd_95_envelope, peak, HPD95_BAR, HPD95_ALPHA, '95% HPD')
    _draw_hpd_bars(ax, hpd_68_ranges, hpd_68_envelope, peak, HPD68_BAR, HPD68_ALPHA, '68% HPD')

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
    """Whether any exported row actually carries a value for `key`.

    Columns are chosen by what a run produced rather than by its declared
    type: agreement and outlier probabilities exist only where a model was
    fitted, the unmodelled comparison only for a Bayesian run, and an
    analysis old enough to predate a field has none of it either. Deciding
    per-field means the report never prints a column of blanks, and never
    silently drops one that has content.
    """
    return any(row.get(key) is not None for row in rows if row)


def _results_table(
    display_rows: List[Dict],
    columns: List[Tuple[str, str, float]],
    styles: Dict[str, ParagraphStyle],
) -> Table:
    """
    The results table: one row per modelled entity, in the model's own
    reading order, dates and Boundaries reporting through the same columns.

    Structure rows are indented by depth and set in bold, which is what
    carries the model's shape in a document that has no expandable tree --
    the reading order alone is not enough to see that three dates sit
    inside a phase inside a sequence.
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
    header = ['Context', 'Modelled Start (95% HPD)', 'Modelled End (95% HPD)', 'Start Mean', 'End Mean']
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


# --- narrative -----------------------------------------------------------

_SOLVER_NAMES = {
    'exact': 'exact integration',
    'gibbs': "OxCal's MH/Gibbs sampler",
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
    How the posteriors were actually computed.

    Worth stating in a report for the same reason the results page states
    it: the two solvers answer the same question by different means, and
    which one ran is a property of the model's shape rather than of what
    was asked for.
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
    return sentence


def _page_furniture(canvas, doc) -> None:
    """A footer on every page: what this is, and where you are in it.

    A results table that runs to eight pages is quoted from, photocopied and
    emailed page by page, so each page has to say which analysis it belongs
    to on its own.
    """
    canvas.saveState()
    canvas.setFont('Helvetica', 7)
    canvas.setFillColor(colors.HexColor('#6b625d'))
    canvas.drawString(MARGIN, MARGIN * 0.6, doc.radical_footer)
    canvas.drawRightString(PAGE_SIZE[0] - MARGIN, MARGIN * 0.6, f"Page {doc.page}")
    canvas.restoreState()


# --- assembly ------------------------------------------------------------

def _display_row(row: Dict, date_format: str) -> Dict:
    """
    One structure row reduced to the strings the table prints.

    Every column is derived here, so the table builder only has to choose
    which of them to show -- and a date and a Boundary come out of this
    with the same keys, which is what lets them share a row shape.
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
    }


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
        columns.append(('unmodelled', 'Unmodelled 95% HPD', 2.6))
    if _has_any(result_rows, 'posterior_mean'):
        columns.append(('mean', 'Mean', 1.2))
    if _has_any(result_rows, 'posterior_median'):
        columns.append(('median', 'Median', 1.2))
    columns.append(('hpd68', '68% HPD', 2.6))
    columns.append(('hpd95', '95% HPD', 2.6))
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
    The rows to report, in reading order, numbered.

    Falls back to the flat list of dates when an analysis has no stored
    structure -- a plain calibration, or one old enough to predate model
    trees -- for the same reason the CSV does: an export with no structure
    should still carry the results.
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
    project_name: Optional[str] = None,
    date_format: Optional[str] = None,
) -> bytes:
    """
    A completed analysis as a PDF, returned as bytes.

    `structure` and `rows` are what api/results.py already assembles for
    the CSV and JSON exports (_structure_rows and _gather_export_rows), and
    `context_spans` the modelled start/end stored on each SampleGroup.
    Nothing is re-derived from the database here, so the PDF cannot report
    a different run from the CSV downloaded beside it.

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
        "at 68% and 95%; where a region falls in more than one part, each part is listed with its "
        "own share of the probability rather than being collapsed into a single span.",
        styles['note'],
    ))

    # --- results table
    story.append(Paragraph('Results', styles['heading']))
    has_limiters = any(
        row.get('tpq_age_bp') is not None or row.get('taq_age_bp') is not None
        for row in ordered
    )
    story.append(
        _results_table(display_rows, _columns(result_rows, is_bayesian, has_limiters), styles)
    )

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
