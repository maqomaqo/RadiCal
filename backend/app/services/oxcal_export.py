"""
An analysis written as an OxCal model file (.oxcal), for a reader who runs
OxCal rather than this app.

The inverse of services/oxcal_script.py, and held to it: every construct
written here is one that importer reads back to the same dates and the same
structure, which test_oxcal_export.py checks by round trip. What this app
models and OxCal's language here cannot say -- a TPQ or TAQ limit, an inbuilt
age, a date outside the model -- is not approximated. It is left out and
listed, in the file's header and in what this returns, so whoever runs the
file knows what it does not carry.

Mapping:

  sequence             Sequence("name") { ... };
  phase                Phase("name") { ... };
  boundary             Boundary("name");
  hiatus               a comment: in OxCal two adjacent Boundaries are the gap
  wiggle_sequence      D_Sequence, or V_Sequence where gaps carry an error
  deposition_sequence  P_Sequence("name", k, 1[, U(a,b)]) with z= per level
  interpolated_depth   Date("name") { z=...; };
  date                 R_Date("name", age, error) { Outlier(...); z=...; };

Options() comes first, above Plot(), since OxCal reads it as a global
setting. Each reservoir offset is declared once by name at the head of Plot()
-- "None" too, where some dates carry no offset -- and cited by name,
Delta_R("=LocalMarine", ), wherever a date needs it. Each curve is loaded once at the head of Plot() -- a file, or a
Mix_Curves of curves loaded before it -- because OxCal refuses a curve name
defined twice. After that a curve is selected by name alone, before each date
whose curve or offset differs from the last one stated, and again after
leaving any block, so the file means the same whether a reader scopes the
selection to blocks (as the importer does) or reads it straight down the file.
"""
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.services import curve_registry

# OxCal's own file names for the shipped curves.
_BUILTIN_FILES = {
    'intcal20': 'intcal20.14c',
    'shcal20': 'shcal20.14c',
    'marine20': 'marine20.14c',
}

# The two outlier models this app implements, as OxCal declares them
# (Bronk Ramsey 2009). The importer checks a file's declarations against
# exactly these.
_OUTLIER_MODELS = {
    'general': 'Outlier_Model("General",T(5),U(0,4),"t");',
    'charcoal': 'Outlier_Model("Charcoal",Exp(1,-10,0),U(0,3),"t");',
}


def _number(value: float) -> str:
    return format(float(value), '.10g')


def _quote(text: Optional[str]) -> str:
    """A name as an OxCal string. A double quote inside one would end it."""
    return '"' + (text or '').replace('"', "'").replace('\n', ' ') + '"'


def _comment(text: str) -> str:
    return text.replace('*/', '* /')


class _Writer:
    def __init__(self, measurements: List[Dict[str, Any]]):
        self.lines: List[str] = []
        self.omitted: List[str] = []
        self.depth = 0
        self.measurements = measurements
        self.by_element: Dict[str, List[Dict[str, Any]]] = {}
        for m in measurements:
            if m.get('element_id'):
                self.by_element.setdefault(m['element_id'], []).append(m)
        # The curve and offset last stated, or None once it is unknown (after
        # leaving a block). See the module docstring.
        self.state: Optional[Tuple[str, Optional[float], Optional[float]]] = None
        # Each distinct reservoir offset, declared once by name at the head
        # of Plot() and cited by that name before the dates it applies to.
        self.offset_names: Dict[Tuple[float, float], str] = {}
        self.curve_names: Dict[str, str] = {}
        self.mixed: Dict[str, Tuple[str, str, float, float]] = {}

    # -- output -----------------------------------------------------------

    def line(self, text: str) -> None:
        self.lines.append('  ' * self.depth + text)

    def open(self, head: str) -> None:
        self.line(head)
        self.line('{')
        self.depth += 1

    def close(self) -> None:
        self.depth -= 1
        self.line('};')
        self.state = None

    # -- curves -----------------------------------------------------------

    def prepare_curves(self, curve_ids: Iterable[str]) -> None:
        """
        Load every curve used, once: the files first, then each mixture after
        the curves it is made of. OxCal refuses a name defined twice.
        """
        order: List[str] = []
        for curve_id in sorted(set(curve_ids)):
            self._collect(curve_id, order, 0)
        for curve_id in order:
            if curve_id not in self.mixed:
                self.line(self._load_statement(curve_id))
        for curve_id in order:
            if curve_id in self.mixed:
                a, b, percent, error = self.mixed[curve_id]
                self.line(f'Mix_Curves({_quote(self.curve_names[curve_id])},'
                          f'{_quote(self.curve_names[a])},{_quote(self.curve_names[b])},'
                          f'{_number(percent)},{_number(error)});')

    def _collect(self, curve_id: str, order: List[str], depth: int) -> None:
        """Name a curve, and its parents before it, in the order to load them."""
        if curve_id in self.curve_names or depth > 8:
            return
        spec = curve_registry.get_spec(curve_id)
        self.curve_names[curve_id] = spec.name or curve_id
        if spec.kind == 'mixed':
            from app.database import SessionLocal
            from app.models import StoredCalibrationCurve
            with SessionLocal() as db:
                row = db.query(StoredCalibrationCurve).filter(
                    StoredCalibrationCurve.id == curve_id).one()
                recipe = dict(row.recipe or {})
            self._collect(recipe['a'], order, depth + 1)
            self._collect(recipe['b'], order, depth + 1)
            self.mixed[curve_id] = (recipe['a'], recipe['b'], recipe.get('percent_a', 50),
                                    recipe.get('percent_a_error', 0))
        order.append(curve_id)

    def _load_statement(self, curve_id: str) -> str:
        name = self.curve_names[curve_id]
        spec = curve_registry.get_spec(curve_id)
        if spec.kind == 'builtin':
            filename = _BUILTIN_FILES.get(curve_id, curve_id + '.14c')
        else:
            filename = spec.source_filename or f'{curve_id}.14c'
        return f'Curve({_quote(name)},{_quote(filename)});'

    def _curve_statement(self, curve_id: str) -> str:
        """Select a curve already loaded, by its name alone."""
        return f'Curve({_quote(self.curve_names[curve_id])});'

    def select(self, m: Dict[str, Any]) -> None:
        """State the date's curve and offset, if they are not already in force."""
        want = (m.get('curve_id') or 'intcal20', m.get('delta_r'),
                m.get('delta_r_error') if m.get('delta_r') is not None else None)
        if self.state == want:
            return
        self.line(self._curve_statement(want[0]))
        if self.offset_names:
            # Cited every time a curve is selected, "None" included, so no
            # offset stated for an earlier date carries on to this one.
            name = (self.offset_names[(float(want[1]), float(want[2] or 0))]
                    if want[1] is not None else 'None')
            self.line(f'Delta_R("={name}", );')
        self.state = want

    def declare_offsets(self) -> None:
        """
        Name every reservoir offset the dates carry, once, at the head of
        Plot(): "LocalMarine" for one, numbered for several, and "None" when
        some dates carry none. Dates then cite them by name, so no name is
        ever stated twice.
        """
        offsets: List[Tuple[float, float]] = []
        bare = False
        for m in self.measurements:
            if m.get('delta_r') is None:
                bare = True
                continue
            key = (float(m['delta_r']), float(m.get('delta_r_error') or 0))
            if key not in offsets:
                offsets.append(key)
        for index, key in enumerate(offsets, start=1):
            name = 'LocalMarine' if len(offsets) == 1 else f'LocalMarine{index}'
            self.offset_names[key] = name
            self.line(f'Delta_R({_quote(name)},{_number(key[0])},{_number(key[1])});')
        if offsets and bare:
            self.line('Delta_R("None",0,0);')

    # -- dates ------------------------------------------------------------

    def date(self, m: Dict[str, Any], depth: bool = False) -> None:
        self.select(m)
        name = m.get('sample_name')
        head = f'R_Date({_quote(name)},{_number(m["age_bp"])},{_number(m["error_1sigma"])})'
        inner = []
        if m.get('outlier_prior'):
            model = (m.get('outlier_model') or 'general').title()
            inner.append(f'Outlier({_quote(model)},{_number(m["outlier_prior"])});')
        if depth and m.get('depth_cm') is not None:
            inner.append(f'z={_number(m["depth_cm"])};')
        if inner:
            self.line(head)
            self.line('{')
            for text in inner:
                self.line('  ' + text)
            self.line('};')
        else:
            self.line(head + ';')

        limits = []
        if m.get('tpq_age_bp') is not None:
            limits.append(f'TPQ {_number(m["tpq_age_bp"])} cal BP')
        if m.get('taq_age_bp') is not None:
            limits.append(f'TAQ {_number(m["taq_age_bp"])} cal BP')
        if limits:
            self.omitted.append(f'Date {name}: {", ".join(limits)}')
        if m.get('inbuilt_years'):
            self.omitted.append(
                f'Date {name}: inbuilt age up to {_number(m["inbuilt_years"])} years '
                f'({m.get("inbuilt_shape") or "uniform"})')

    # -- structure --------------------------------------------------------

    def element(self, element: Dict[str, Any]) -> None:
        etype = element.get('element_type')
        name = element.get('name') or ''
        limits = []
        if element.get('tpq_age_bp') is not None:
            limits.append(f'TPQ {_number(element["tpq_age_bp"])} cal BP')
        if element.get('taq_age_bp') is not None:
            limits.append(f'TAQ {_number(element["taq_age_bp"])} cal BP')
        if limits:
            self.omitted.append(f'{etype.replace("_", " ").capitalize()} {name}: '
                                + ', '.join(limits))

        if etype == 'boundary':
            self.line(f'Boundary({_quote(name)});')
        elif etype == 'hiatus':
            self.line(f'/* Hiatus {_comment(name)}: in OxCal the gap is the two '
                      'Boundaries either side of it. */')
        elif etype == 'sequence':
            self.open(f'Sequence({_quote(name)})')
            for child in element.get('children') or []:
                self.element(child)
            for m in self.by_element.get(element['id'], []):
                self.date(m)
            self.close()
        elif etype == 'phase':
            self.open(f'Phase({_quote(name)})')
            for m in self.by_element.get(element['id'], []):
                self.date(m)
            for child in element.get('children') or []:
                self.element(child)
            self.close()
        elif etype == 'wiggle_sequence':
            self.wiggle(element)
        elif etype == 'deposition_sequence':
            self.deposition(element)
        else:
            self.omitted.append(f'{etype} {name}: no OxCal equivalent')

    def wiggle(self, element: Dict[str, Any]) -> None:
        """
        A wiggle match, oldest member first with the Gap() between each pair.
        Each member's offset error is its distance from the youngest member,
        which is what the importer builds from a chain of gap errors, so the
        gaps' errors are recovered from the differences of their squares.
        """
        name = element.get('name') or 'Wiggle match'
        members = sorted(self.by_element.get(element['id'], []),
                         key=lambda m: -(m.get('ring_offset') or 0.0))
        errors = [float(m.get('ring_offset_error') or 0.0) for m in members]
        varved = any(errors)
        if varved and errors[-1]:
            self.omitted.append(
                f'Wiggle match {name}: the youngest member has an offset error of '
                f'{_number(errors[-1])} years, which a chain of Gap() errors cannot carry')
        self.open(f'{"V_Sequence" if varved else "D_Sequence"}({_quote(name)})')
        for index, m in enumerate(members):
            if index:
                gap = (members[index - 1].get('ring_offset') or 0.0) - (m.get('ring_offset') or 0.0)
                if varved:
                    variance = errors[index - 1] ** 2 - errors[index] ** 2
                    if variance < 0:
                        self.omitted.append(
                            f'Wiggle match {name}: {m.get("sample_name")} has a larger offset '
                            'error than the member before it, which a chain of gaps cannot '
                            'express; that gap is written without an error')
                    self.line(f'Gap({_number(gap)},{_number(math.sqrt(max(variance, 0.0)))});')
                else:
                    self.line(f'Gap({_number(gap)});')
            self.date(m)
        self.close()

    def deposition(self, element: Dict[str, Any]) -> None:
        """A deposition sequence, deepest level first, each with its z=."""
        name = element.get('name') or 'Deposition sequence'
        params = element.get('params') or {}
        if params.get('k_variable'):
            # k0 = 1, so U(a,b) are log10 k themselves.
            head = (f'P_Sequence({_quote(name)},1,1,'
                    f'U({_number(params.get("log_k_min", -2))},'
                    f'{_number(params.get("log_k_max", 2))}))')
        else:
            head = f'P_Sequence({_quote(name)},{_number(params.get("k") or 1)},1)'
        levels: List[Tuple[float, str, Any]] = []
        for m in self.by_element.get(element['id'], []):
            levels.append((float(m.get('depth_cm') or 0.0), 'date', m))
        for child in element.get('children') or []:
            depth = (child.get('params') or {}).get('depth_cm')
            levels.append((float(depth or 0.0), 'level', child))
        levels.sort(key=lambda item: -item[0])
        self.open(head)
        for depth, kind, payload in levels:
            if kind == 'date':
                self.date(payload, depth=True)
            else:
                self.line(f'Date({_quote(payload.get("name"))})')
                self.line('{')
                self.line(f'  z={_number(depth)};')
                self.line('};')
        self.close()


def write_oxcal(name: str, analysis_type: str, measurements: List[Dict[str, Any]],
                tree: Optional[Dict[str, Any]], config: Optional[Dict[str, Any]],
                app_commit: Optional[str] = None) -> Tuple[str, List[str]]:
    """
    The analysis as an OxCal model file, and what it could not write.

    `measurements` are the analysis's dates in its own order, as dicts with
    the stored fields and `element_id`; `tree` its model structure (None for
    a calibration). A calibration is written as its dates alone, which OxCal
    calibrates independently.
    """
    config = config or {}
    writer = _Writer(measurements)
    resolution = config.get('resolution')
    if resolution and analysis_type == 'bayesian':
        writer.line('Options()')
        writer.line('{')
        writer.line(f'  Resolution={int(resolution)};')
        if config.get('uniform_span_prior') is False:
            writer.line('  UniformSpanPrior=FALSE;')
        writer.line('};')
    writer.open('Plot()')
    models = sorted({(m.get('outlier_model') or 'general').lower()
                     for m in measurements if m.get('outlier_prior')})
    for model in models:
        writer.line(_OUTLIER_MODELS.get(model, ''))
    writer.prepare_curves(m.get('curve_id') or 'intcal20' for m in measurements)
    writer.declare_offsets()

    if analysis_type == 'bayesian' and tree is not None:
        placed = {id(m) for ms in writer.by_element.values() for m in ms}
        writer.element(tree)
        loose = [m for m in measurements if id(m) not in placed]
        if loose:
            writer.omitted.append(
                f'{len(loose)} date(s) not placed in the model: '
                + ', '.join(str(m.get('sample_name')) for m in loose))
    else:
        for m in measurements:
            writer.date(m)
    writer.close()

    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    header = [
        '/*',
        f'  {_comment(name)}',
        f'  Exported from RadiCal{" (" + app_commit + ")" if app_commit else ""} on {stamp}.',
    ]
    if any(spec_kind(c) != 'builtin' for c in writer.curve_names):
        header.append('  Curves other than IntCal20, SHCal20 and Marine20 must be '
                      'loaded into OxCal under the file names given.')
    if writer.omitted:
        header.append('  Not written, because OxCal\'s language here cannot say it:')
        header += [f'    - {_comment(item)}' for item in writer.omitted]
    header.append('*/')
    return '\n'.join(header + writer.lines) + '\n', writer.omitted


def spec_kind(curve_id: str) -> str:
    try:
        return curve_registry.get_spec(curve_id).kind
    except ValueError:
        return 'unknown'
