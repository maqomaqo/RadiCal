"""
Reading OxCal's own model files (.oxcal, the CQL2 language) into the app's
model tree.

The six files in tests/fixtures are the specification this was written
against: real models covering nested Sequences and Phases, explicit and implied
Boundaries, per-scope Curve selection, named reservoir offsets, and both
outlier models. tests/test_imports.py checks each parse against the hand-built
trees in tests/oxcal_fixture.py, themselves checked against OxCal's published
output, so an imported model is verified two steps out.

THE RULE THIS MODULE IS BUILT AROUND: a construct that would change the
posterior and cannot be honoured is an ERROR, never a warning or a silent
omission. OxCal's language is much larger than what this app models --
Tau_Boundary is an exponential transition, C_Date a calendar likelihood with no
curve behind it, R_Combine pools measurements before calibrating -- and reading
any of those as the nearest thing we do implement would produce a result that
looks right, carries no warning, and is wrong.

Constructs that only affect what OxCal PLOTS are skipped with a note, since
they query a posterior rather than shape it. Options() values are not read at
all: Resolution and kIterations configure OxCal's own grid and run length,
which this app has neither of.
"""
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.services import curve_registry
from app.services.date_import import ImportResult, _name_from_filename


class OxcalSyntaxError(Exception):
    """A file that is not valid CQL2, reported with a line number."""


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"""
    (?P<space>\s+)
  | (?P<line_comment>//[^\n]*)
  | (?P<block_comment>/\*.*?\*/)
  | (?P<string>"(?:[^"\\]|\\.)*")
  | (?P<number>[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<punct>[(){},;=])
""", re.VERBOSE | re.DOTALL)


class Token:
    __slots__ = ('kind', 'value', 'line')

    def __init__(self, kind: str, value: str, line: int):
        self.kind = kind
        self.value = value
        self.line = line

    def __repr__(self):
        return f'Token({self.kind}, {self.value!r}, line {self.line})'


def tokenize(text: str) -> List[Token]:
    tokens: List[Token] = []
    position = 0
    line = 1
    length = len(text)

    while position < length:
        match = _TOKEN_RE.match(text, position)
        if match is None:
            snippet = text[position:position + 20].split('\n')[0]
            raise OxcalSyntaxError(
                f"Line {line}: cannot read '{snippet}' -- this does not look "
                "like an OxCal model file."
            )
        kind = match.lastgroup
        value = match.group()
        if kind in ('string', 'number', 'name', 'punct'):
            tokens.append(Token(kind, value, line))
        line += value.count('\n')
        position = match.end()

    return tokens


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

class Node:
    """
    One CQL2 statement: a command, its arguments, and its block if it has one.
    `Sequence("Nenthorn") { ... };` and `Boundary("Start");` are both this.
    """

    def __init__(self, command: str, args: List[Any], block: Optional[List['Node']], line: int):
        self.command = command
        self.args = args
        self.block = block
        self.line = line

    @property
    def name(self) -> Optional[str]:
        """The first string argument, which is the label in every command that takes one."""
        for arg in self.args:
            if isinstance(arg, str):
                return arg
        return None

    @property
    def numbers(self) -> List[float]:
        return [a for a in self.args if isinstance(a, (int, float))]

    def __repr__(self):
        return f'Node({self.command}, {self.args}, block={self.block is not None})'


class Call:
    """A nested function argument -- T(5), U(0,4), Exp(1,-10,0)."""

    def __init__(self, name: str, args: List[Any]):
        self.name = name
        self.args = args

    def __repr__(self):
        inner = ','.join(repr(a) for a in self.args)
        return f'{self.name}({inner})'


class _Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.position = 0

    def at_end(self) -> bool:
        return self.position >= len(self.tokens)

    def peek(self) -> Optional[Token]:
        return None if self.at_end() else self.tokens[self.position]

    def next(self) -> Token:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def expect(self, value: str) -> Token:
        token = self.peek()
        if token is None:
            raise OxcalSyntaxError(f"The file ends where '{value}' was expected.")
        if token.value != value:
            raise OxcalSyntaxError(
                f"Line {token.line}: expected '{value}' but found '{token.value}'."
            )
        return self.next()

    def parse_program(self) -> List[Node]:
        nodes: List[Node] = []
        while not self.at_end():
            token = self.peek()
            if token.value == ';':
                self.next()
                continue
            if token.value == '}':
                break
            nodes.append(self.parse_statement())
        return nodes

    def parse_statement(self) -> Node:
        token = self.next()
        if token.kind != 'name':
            raise OxcalSyntaxError(
                f"Line {token.line}: expected a command but found '{token.value}'."
            )
        command = token.value
        line = token.line

        # An assignment, as found inside Options(): `Resolution=1;`
        following = self.peek()
        if following is not None and following.value == '=':
            self.next()
            value = self.parse_value()
            if self.peek() is not None and self.peek().value == ';':
                self.next()
            return Node('=', [command, value], None, line)

        args: List[Any] = []
        if following is not None and following.value == '(':
            args = self.parse_args()

        block: Optional[List[Node]] = None
        if self.peek() is not None and self.peek().value == '{':
            self.next()
            block = self.parse_program()
            self.expect('}')

        if self.peek() is not None and self.peek().value == ';':
            self.next()

        return Node(command, args, block, line)

    def parse_args(self) -> List[Any]:
        self.expect('(')
        args: List[Any] = []
        if self.peek() is not None and self.peek().value == ')':
            self.next()
            return args
        while True:
            args.append(self.parse_value())
            token = self.peek()
            if token is None:
                raise OxcalSyntaxError('The file ends inside an argument list.')
            if token.value == ',':
                self.next()
                # An argument OxCal left out, written as a bare comma before
                # the closing bracket: `Delta_R("=LocalMarine", )` cites a
                # definition and supplies no value of its own.
                if self.peek() is not None and self.peek().value == ')':
                    self.next()
                    return args
                continue
            self.expect(')')
            return args

    def parse_value(self) -> Any:
        token = self.next()
        if token.kind == 'string':
            return _unquote(token.value)
        if token.kind == 'number':
            return float(token.value)
        if token.kind == 'name':
            if self.peek() is not None and self.peek().value == '(':
                return Call(token.value, self.parse_args())
            return token.value
        raise OxcalSyntaxError(
            f"Line {token.line}: expected a value but found '{token.value}'."
        )


def _unquote(raw: str) -> str:
    return raw[1:-1].replace('\\"', '"').replace('\\\\', '\\')


def parse_script(text: str) -> List[Node]:
    """Tokenize and parse, with no interpretation of what the commands mean."""
    return _Parser(tokenize(text)).parse_program()


# --------------------------------------------------------------------------
# Command classification
# --------------------------------------------------------------------------

# Containers contributing no element of their own, so their children are
# lifted into the enclosing scope.
TRANSPARENT = {'plot', 'page'}

STRUCTURAL = {
    'sequence': 'sequence',
    'phase': 'phase',
    'boundary': 'boundary',
    'hiatus': 'hiatus',
}

# Queries and plots: they read a posterior rather than shape one, so leaving
# them out changes nothing.
IGNORABLE = {
    'sum', 'kde_plot', 'kde_model', 'span', 'interval', 'difference', 'shift',
    'first', 'last', 'order', 'correl_matrix', 'correlation', 'mcmc_sample',
    'label', 'axis', 'number', 'mean', 'median', 'sigma', 'prior',
    'posterior', 'display', 'plot_axis', 'sample',
}

# Constructs this app has no equivalent for, each stating what reading it as
# something we DO implement would cost -- which is why it is refused.
UNSUPPORTED = {
    'tau_boundary': (
        'an exponentially-distributed transition. This app models a Boundary '
        'as a uniform transition, and reading a Tau_Boundary as one would '
        'change the posterior without saying so.'
    ),
    'sigma_boundary': (
        'a normally-distributed transition. This app models a Boundary as a '
        'uniform transition.'
    ),
    'zero_boundary': (
        'a transition with a density that falls to zero. This app models a '
        'Boundary as a uniform transition.'
    ),
    'trapezium': 'a trapezoidal transition, which this app does not model.',
    'trapezoid': 'a trapezoidal transition, which this app does not model.',
    'c_date': (
        'a calendar-age likelihood. Every date in this app is a radiocarbon '
        'measurement calibrated through a curve; there is nowhere to put a '
        'date that is already calendrical. A fixed calendar limit can be '
        'entered as a TPQ or TAQ instead.'
    ),
    'date': (
        'an undated event parameter, which this app does not model as a '
        'free parameter of its own.'
    ),
    'age': 'a relative-age parameter, which this app does not model.',
    'r_combine': (
        'several measurements pooled into one before calibration. This app '
        'calibrates each measurement separately; combine them in the lab '
        'report and enter the pooled age and error as a single date.'
    ),
    'combine': 'a combination of likelihoods, which this app does not model.',
    'r_simulate': (
        'a simulated measurement. This app calibrates real measurements; '
        'a simulated date has no equivalent here.'
    ),
    'u_date': 'a uranium-series date, which this app does not calibrate.',
    'tl_date': 'a luminescence date, which this app does not calibrate.',
    'after': (
        'an open-ended ordering constraint. Express the ordering with a '
        'Sequence, or the limit as a TPQ/TAQ.'
    ),
    'before': (
        'an open-ended ordering constraint. Express the ordering with a '
        'Sequence, or the limit as a TPQ/TAQ.'
    ),
    'v_sequence': 'a variable-rate sequence, which this app does not model.',
    'd_sequence': 'a sequence with known gaps, which this app does not model.',
    'p_sequence': 'a Poisson-process deposition model, which this app does not model.',
    'u_sequence': 'a uniform-deposition sequence, which this app does not model.',
}

def _normalise_curve_name(raw: str) -> str:
    """A curve name reduced to its comparable core, as the registry compares
    names, so `Curve("=Mixed Diet")` finds `Mix_Curves("mixed diet", ...)`."""
    return re.sub(r'[^a-z0-9]+', '', (raw or '').strip().lstrip('=').lower())


def _curve_id(raw: str) -> Optional[str]:
    """
    An OxCal curve name as one of the ids the calibration engine loads, or
    None for a curve this app does not have -- which the caller reports as a
    failed import naming the curve, never as a substitution.

    The name map lives in services/curve_registry.py alongside the curves
    themselves, so a curve added there is recognised in an .oxcal file
    without this module being touched. It handles the '=Name' form, which is
    how OxCal refers back to a curve already declared in an enclosing scope
    (`Curve("=SHCal20")`), and a name written as a filename.
    """
    return curve_registry.resolve_alias(raw)


# --------------------------------------------------------------------------
# Translation
# --------------------------------------------------------------------------

class _Scope:
    """
    The curve and reservoir offset in force at a point in the file.

    OxCal's Curve() and Delta_R() apply from where they appear to the end of
    their enclosing block, and are inherited by nested blocks -- so this is
    copied on the way into a block and mutated by statements within it.

    A reservoir offset belongs to the CURVE it was stated against, not to the
    block: OxCal's Delta_R "applies to the current Curve". So the offset
    follows its curve -- switching away leaves it behind, switching back brings
    it with it -- which is what lets one phase calibrate marine dates against
    Marine20 with a local Delta_R and terrestrial ones against SHCal20 with
    none, the ordinary shape of a coastal model.
    """

    def __init__(self, curve_id: str = 'intcal20',
                 delta_r: Optional[float] = None,
                 delta_r_error: Optional[float] = None,
                 delta_r_name: Optional[str] = None,
                 curve_offsets: Optional[Dict[str, Tuple]] = None):
        self.curve_id = curve_id
        self.delta_r = delta_r
        self.delta_r_error = delta_r_error
        # The label the offset was given, carried only so the import can
        # report which named offset reached which dates.
        self.delta_r_name = delta_r_name
        # curve id -> the offset in force for it, so that leaving a curve and
        # coming back to it restores what was stated against it.
        self.curve_offsets: Dict[str, Tuple] = dict(curve_offsets or {})

    def copy(self) -> '_Scope':
        return _Scope(self.curve_id, self.delta_r, self.delta_r_error,
                      self.delta_r_name, self.curve_offsets)


class _Translator:
    def __init__(self, result: ImportResult):
        self.result = result
        self.declared_outlier_models: Dict[str, Call] = {}
        # Reservoir offsets stated with a name, as (delta_r, delta_r_error),
        # for the `Delta_R("=Name")` references that cite them.
        self.declared_delta_r: Dict[str, Tuple[float, float]] = {}
        # How many dates each named offset ended up on, for the note below.
        self.delta_r_usage: Dict[str, int] = {}
        self.wrapped_sequence_dates = 0
        self.seen_ignorable: List[str] = []
        # Curve names this file defined with Mix_Curves, mapped to the stored
        # curve each was matched to. Scoped to the file, as OxCal scopes them.
        self.mixed_curves: Dict[str, str] = {}

    # -- helpers ---------------------------------------------------------

    def element(self, etype: str, name: str, children=None) -> Dict[str, Any]:
        return {
            'id': f'e-{uuid.uuid4().hex[:12]}',
            'element_type': etype,
            'name': name,
            'tpq_age_bp': None,
            'taq_age_bp': None,
            'children': children if children is not None else [],
        }

    def error(self, node: Node, message: str) -> None:
        self.result.errors.append(f'Line {node.line}: {message}')

    # -- top level -------------------------------------------------------

    def translate(self, nodes: List[Node]) -> None:
        scope = _Scope()
        # A first pass for declarations that apply file-wide wherever they
        # sit: OxCal's Outlier_Model is referenced by name from anywhere,
        # including from above its own declaration.
        self._collect_declarations(nodes)

        self._assemble(self._translate_block(nodes, scope))

        for label, count in sorted(self.delta_r_usage.items()):
            offset, uncertainty = self.declared_delta_r.get(
                label.lower(), (None, None)
            )
            if offset is None:
                continue
            self.result.notes.append(
                f'Delta_R("{label}") = {offset:g} +/- {uncertainty:g} was '
                f'applied to {count} date(s) -- every date calibrated against '
                'the curve it was stated for. Dates on another curve carry no '
                'offset; the table shows which.'
            )

        if self.seen_ignorable:
            unique = sorted(set(self.seen_ignorable))
            self.result.notes.append(
                'Ignored ' + ', '.join(f'{name}()' for name in unique)
                + ' -- these ask OxCal to plot or summarise a result and do '
                'not affect the model, so leaving them out changes nothing.'
            )
        if self.wrapped_sequence_dates:
            self.result.notes.append(
                f'{self.wrapped_sequence_dates} date(s) sat directly inside a '
                'Sequence. Each is now in a Phase of its own, which keeps the '
                'ordering exactly as written -- this app attaches dates to '
                'Phases.'
            )

    def _collect_declarations(self, nodes: List[Node]) -> None:
        for node in nodes:
            command = node.command.lower()
            if command == 'outlier_model':
                self._declare_outlier_model(node)
            if command == 'delta_r':
                self._declare_delta_r(node)
            if node.block:
                self._collect_declarations(node.block)

    def _declare_delta_r(self, node: Node) -> None:
        """
        Record a reservoir offset stated with a name, so that a later
        `Delta_R("=Name")` can cite it.

        Collected in the same first pass as the outlier models, and for the
        same reason: a name is a name wherever it was written, and resolving
        references against file order would refuse a model OxCal accepts. A
        declaration that is short of its uncertainty is not recorded -- the
        error for that belongs to the point of use, where the date it would
        have applied to can be named.
        """
        name = (node.name or '').strip()
        if not name or name.startswith('='):
            return                            # a reference, not a declaration
        numbers = node.numbers
        if len(numbers) >= 2:
            self.declared_delta_r[name.lower()] = (numbers[0], numbers[1])

    def _declare_outlier_model(self, node: Node) -> None:
        name = (node.name or '').strip()
        key = name.lower()
        self.declared_outlier_models[key] = node

        if key in ('general', 'charcoal'):
            # The two we implement, with the parameters we implement them at.
            # A file redefining General with a different offset distribution
            # or scale range is asking for a model we do not have, and the
            # difference is not cosmetic -- the scale range is what makes a
            # third of the charcoal prior sit at ten years or less.
            expected = {
                'general': ('T(5.0)', 'U(0.0,4.0)'),
                'charcoal': ('Exp(1.0,-10.0,0.0)', 'U(0.0,3.0)'),
            }[key]
            found = tuple(str(a) for a in node.args if isinstance(a, Call))[:2]
            if found and tuple(found) != expected:
                self.result.warnings.append(
                    f"Line {node.line}: Outlier_Model(\"{name}\") is declared as "
                    f"{', '.join(found)}, but this app implements the "
                    f"{key} model as {', '.join(expected)} (Bronk Ramsey 2009) "
                    'and cannot vary it. Dates using it are calibrated against '
                    "the app's own parameters, not the file's."
                )
        else:
            self.result.warnings.append(
                f"Line {node.line}: Outlier_Model(\"{name}\") is a custom "
                'outlier model. This app implements only "General" and '
                '"Charcoal"; any date referring to this one will be refused.'
            )

    # -- blocks ----------------------------------------------------------

    def _translate_block(
        self, nodes: List[Node], scope: _Scope
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """
        One block's contents IN FILE ORDER, as ('element'|'date', payload).

        Order is the whole meaning of a Sequence:
        `Sequence(){R_Date(a); Phase(){...}; R_Date(b);}` says a is older than
        the phase, which is older than b. Separate lists would lose that.

        What a loose date means depends on the enclosing element, so the caller
        decides: in a Phase it simply belongs to it, in a Sequence it holds a
        position and needs a Phase of its own.
        """
        items: List[Tuple[str, Dict[str, Any]]] = []

        for node in nodes:
            command = node.command.lower()

            if command == '=':
                continue                      # an Options() assignment
            if command in TRANSPARENT:
                items.extend(self._translate_block(node.block or [], scope.copy()))
                continue
            if command == 'curve':
                self._select_curve(node, scope)
                continue
            if command == 'delta_r':
                self._apply_delta_r(node, scope)
                continue
            if command == 'mix_curves':
                self._define_mixed_curve(node)
                continue
            if command == 'outlier_model':
                continue                      # already collected
            if command == 'options':
                self._note_options(node)
                continue
            if command == 'r_date':
                date = self._translate_date(node, scope)
                if date is not None:
                    items.append(('date', date))
                continue
            if command in STRUCTURAL:
                element = self._translate_structural(node, scope)
                if element is not None:
                    items.append(('element', element))
                continue
            if command in UNSUPPORTED:
                self.error(
                    node,
                    f'{node.command}() is {UNSUPPORTED[command]}',
                )
                continue
            if command in IGNORABLE:
                self._check_ignorable_holds_dates(node)
                self.seen_ignorable.append(node.command)
                continue

            self.error(
                node,
                f"{node.command}() is not a command this importer recognises. "
                'It has been left out rather than guessed at -- if it does '
                'not affect the model, delete the line and import again.',
            )

        return items

    def _check_ignorable_holds_dates(self, node: Node) -> None:
        """
        A plotting command containing dates takes them out of the import with
        it. Sum(){R_Date(...)} is the usual case: the dates are real, they are
        simply not part of a model. Worth saying, since a silently shorter
        date list is exactly what nobody notices.
        """
        buried = self._count_dates(node.block or [])
        if buried:
            self.result.warnings.append(
                f'Line {node.line}: {node.command}() contains {buried} date(s). '
                f'{node.command}() summarises distributions rather than '
                'modelling them, so those dates were NOT imported. Move them '
                'into a Phase if they belong in the model.'
            )

    def _count_dates(self, nodes: List[Node]) -> int:
        total = 0
        for node in nodes:
            if node.command.lower() == 'r_date':
                total += 1
            if node.block:
                total += self._count_dates(node.block)
        return total

    # -- individual commands ---------------------------------------------

    def _select_curve(self, node: Node, scope: _Scope) -> Optional[str]:
        """
        Make a curve the one in force, bringing whatever reservoir offset
        was stated against THAT curve with it.

        OxCal applies Delta_R to the current curve, so switching from
        Marine20 to SHCal20 does not carry the marine offset across -- a file
        calibrating part of a phase against each, which is the usual shape
        for a coastal site, depends on exactly that, and carrying it would
        shift every terrestrial date by the marine reservoir. Switching back
        restores it, because the offset is a property of the curve rather
        than of the point in the file where it was written.
        """
        resolved = self._resolve_curve(node)
        if resolved is None:
            return None
        if resolved != scope.curve_id:
            scope.delta_r, scope.delta_r_error, scope.delta_r_name = (
                scope.curve_offsets.get(resolved, (None, None, None))
            )
        scope.curve_id = resolved
        return resolved

    def _resolve_curve(self, node: Node) -> Optional[str]:
        name = node.name
        if not name:
            return None
        # A name this file defined with Mix_Curves wins over the global
        # registry, exactly as it does in OxCal: the file is talking about its
        # own mixture, not about a stored curve that happens to share a name.
        local = self.mixed_curves.get(_normalise_curve_name(name))
        if local is not None:
            return local
        resolved = _curve_id(name)
        if resolved is None:
            available = ', '.join(
                spec.name for spec in curve_registry.list_specs(include_archived=True)
            )
            self.error(
                node,
                f"Curve(\"{name}\") is not a curve this app has. It calibrates "
                f'against {available}; substituting one of those would produce '
                'a real result against the wrong curve. Import the curve on '
                'the Curves page and try again.',
            )
            return None
        return resolved

    def _define_mixed_curve(self, node: Node) -> None:
        """
        Mix_Curves("name", A, B, percent, error) -- a curve made of two others,
        for a sample whose carbon came from more than one reservoir.

        Matched against the mixed curves already stored rather than created
        here. Importing a file must not write curves: parsing and storing are
        separate steps throughout this app (see api/imports.py), and a curve
        quietly created by opening a file is a curve nobody chose to add. When
        nothing matches, the error says exactly what to create, so one visit
        to the Curves page makes the file importable.
        """
        args = [a for a in (node.args or []) if a is not None]
        if len(args) < 4:
            self.error(
                node,
                'Mix_Curves() needs a name, two curves and a percentage, '
                'e.g. Mix_Curves("diet","IntCal20","Marine20",30,10).',
            )
            return

        local_name = str(args[0]).strip().strip('"')
        a_id = curve_registry.resolve_alias(str(args[1]))
        b_id = curve_registry.resolve_alias(str(args[2]))
        if a_id is None or b_id is None:
            missing = str(args[1]) if a_id is None else str(args[2])
            self.error(node, f"Mix_Curves() names a curve this app does not have: '{missing}'.")
            return

        try:
            percent = float(args[3])
            error = float(args[4]) if len(args) > 4 else 0.0
        except (TypeError, ValueError):
            self.error(node, 'Mix_Curves() needs its percentage as a number.')
            return

        match = curve_registry.find_mixed(a_id, b_id, percent, error)
        if match is None:
            self.error(
                node,
                f'Mix_Curves("{local_name}", {a_id}, {b_id}, {percent:g}, '
                f'{error:g}) has no matching curve in this app. Create it on '
                'the Curves page -- New mixed curve, '
                f'{percent:g}% {a_id} with {b_id}, uncertainty {error:g}% -- '
                'and import this file again.',
            )
            return

        self.mixed_curves[_normalise_curve_name(local_name)] = match
        self.result.notes.append(
            f'Mix_Curves("{local_name}") was matched to the stored curve '
            f"'{match}'."
        )

    def _apply_delta_r(self, node: Node, scope: _Scope) -> None:
        """
        Put a reservoir offset in force for the curve currently selected.

        Two forms, both OxCal's. Written out --
        `Delta_R("LocalMarine", -162, 28)` -- states the offset and makes its
        name citable. Cited -- `Delta_R("=LocalMarine", )` -- refers to one
        already stated, which is how a file gives an offset once in its header
        and applies it at each marine group.
        """
        numbers = node.numbers
        name = (node.name or '').strip()
        if len(numbers) >= 2:
            scope.delta_r = numbers[0]
            scope.delta_r_error = numbers[1]
            scope.delta_r_name = name.lstrip('=').strip() or None
            self._bind_to_curve(scope)
            return

        if name.startswith('='):
            declared = self.declared_delta_r.get(name.lstrip('=').strip().lower())
            if declared is None:
                self.error(
                    node,
                    f'Delta_R("{name}") cites a reservoir offset that the file '
                    'never states. Add the offset and its uncertainty where it '
                    'is defined -- importing the dates without it would '
                    'calibrate them against the global marine curve alone.',
                )
                return
            scope.delta_r, scope.delta_r_error = declared
            scope.delta_r_name = name.lstrip('=').strip()
            self._bind_to_curve(scope)
            return

        self.error(
            node,
            'Delta_R needs a value and its uncertainty. An offset without '
            'one would understate the uncertainty of every date using it.',
        )

    @staticmethod
    def _bind_to_curve(scope: _Scope) -> None:
        """Remember an offset against the curve it was stated for."""
        scope.curve_offsets[scope.curve_id] = (
            scope.delta_r, scope.delta_r_error, scope.delta_r_name
        )

    def _note_options(self, node: Node) -> None:
        settings = [
            str(item.args[0]) for item in (node.block or [])
            if item.command == '=' and item.args
        ]
        if settings:
            self.result.notes.append(
                'Options(' + ', '.join(sorted(settings)) + ') was not carried '
                "over: those configure OxCal's own grid and run length, and "
                'this app integrates exactly where it can and extends its own '
                'run until it converges.'
            )

    def _translate_date(self, node: Node, scope: _Scope) -> Optional[Dict[str, Any]]:
        numbers = node.numbers
        if len(numbers) < 2:
            self.error(
                node,
                f'R_Date needs a radiocarbon age and an error; found '
                f'{len(numbers)} number(s).',
            )
            return None

        name = node.name or f'R_Date line {node.line}'
        date: Dict[str, Any] = {
            'id': f'd-{uuid.uuid4().hex[:12]}',
            'sample_name': name,
            'age_bp': numbers[0],
            'error_1sigma': numbers[1],
            'material_type': 'unknown',
            'curve_id': scope.curve_id,
            'sample_type': 'marine' if scope.curve_id == 'marine20' else 'terrestrial',
            'delta_r': scope.delta_r,
            'delta_r_error': scope.delta_r_error,
            'tpq_age_bp': None,
            'taq_age_bp': None,
            'depth_cm': None,
            'location': None,
            'notes': None,
            'outlier_prior': 0.0,
            'outlier_model': 'general',
            'phase_name': None,
        }

        inner = scope.copy()
        for child in node.block or []:
            command = child.command.lower()
            if command == 'outlier':
                self._apply_outlier(child, date)
            elif command == 'curve':
                # Through the scope, so that a Curve inside the date drops an
                # inherited offset the same way one in a block does.
                resolved = self._select_curve(child, inner)
                if resolved:
                    date['curve_id'] = resolved
                    date['sample_type'] = (
                        'marine' if resolved == 'marine20' else 'terrestrial'
                    )
                    date['delta_r'] = inner.delta_r
                    date['delta_r_error'] = inner.delta_r_error
            elif command == 'delta_r':
                self._apply_delta_r(child, inner)
                date['delta_r'] = inner.delta_r
                date['delta_r_error'] = inner.delta_r_error
            elif command in IGNORABLE:
                self.seen_ignorable.append(child.command)
            else:
                self.error(
                    child,
                    f'{child.command}() inside R_Date("{name}") is not '
                    'something this importer recognises.',
                )

        if date['delta_r'] is not None and inner.delta_r_name:
            self.delta_r_usage[inner.delta_r_name] = (
                self.delta_r_usage.get(inner.delta_r_name, 0) + 1
            )

        return date

    def _apply_outlier(self, node: Node, date: Dict[str, Any]) -> None:
        model_name = (node.name or 'General').strip()
        key = model_name.lower()
        numbers = node.numbers
        prior = numbers[0] if numbers else 0.05

        if key not in ('general', 'charcoal'):
            self.error(
                node,
                f'Outlier("{model_name}") refers to an outlier model this app '
                'does not implement. It has "General" (symmetric, for a date '
                'wrong in an unknown way) and "Charcoal" (one-sided, for old '
                'wood); applying the wrong one changes both the date and its '
                'outlier probability.',
            )
            return

        if key not in self.declared_outlier_models:
            self.result.warnings.append(
                f'Line {node.line}: Outlier("{model_name}") is used but no '
                f'Outlier_Model("{model_name}") is declared in the file. '
                "The app's own definition of that model was applied."
            )

        date['outlier_model'] = key
        date['outlier_prior'] = prior

    def _translate_structural(self, node: Node, scope: _Scope) -> Optional[Dict[str, Any]]:
        command = node.command.lower()
        etype = STRUCTURAL[command]
        name = node.name or node.command.title()

        if etype in ('boundary', 'hiatus'):
            if node.block and self._count_dates(node.block):
                self.error(
                    node,
                    f'{node.command}("{name}") contains dates. A '
                    f'{etype} is a single transition parameter and cannot '
                    'hold measurements.',
                )
                return None
            # Boundary(U(1000,1200)) / Boundary("Start", U(1000,1200)) -- a
            # prior on the transition, which this app does not offer. Keyed on
            # a Call argument rather than on the label being absent: a named
            # boundary can carry one too, and dropping it silently would
            # narrow the transition without saying so.
            priors = [str(a) for a in node.args if isinstance(a, Call)]
            if priors:
                self.result.warnings.append(
                    f'Line {node.line}: {node.command}("{name}") carries a prior '
                    f'({", ".join(priors)}), which this app does not model. It '
                    f'was imported as an ordinary uniform {etype} -- set a TPQ '
                    'or TAQ on it if the limit matters.'
                )
            return self.element(etype, name)

        items = self._translate_block(node.block or [], scope.copy())

        if etype == 'phase':
            element = self.element(
                'phase', name, [payload for kind, payload in items if kind == 'element']
            )
            for kind, payload in items:
                if kind == 'date':
                    self._attach(payload, element['id'], name)
            return element

        # A Sequence orders its children, so a date sitting directly in one
        # holds a POSITION in that order. This app attaches dates to Phases,
        # so each such date becomes a Phase of one, IN PLACE -- which
        # preserves the ordering exactly: model_tree derives a sequence's
        # constraints from every age-bearing term in each child subtree, and a
        # one-date Phase contributes precisely that date.
        children: List[Dict[str, Any]] = []
        for kind, payload in items:
            if kind == 'element':
                children.append(payload)
                continue
            wrapper = self.element('phase', payload['sample_name'])
            self._attach(payload, wrapper['id'], payload['sample_name'])
            self.wrapped_sequence_dates += 1
            children.append(wrapper)

        return self.element('sequence', name, children)

    def _attach(self, date: Dict[str, Any], element_id: str, phase_name: str) -> None:
        """Put a date in a phase and record it, once."""
        date['element_id'] = element_id
        date['phase_name'] = phase_name
        self.result.dates.append(date)
        self.result.element_assignments[date['id']] = element_id

    # -- assembly --------------------------------------------------------

    def _assemble(self, items: List[Tuple[str, Dict[str, Any]]]) -> None:
        """
        The file's top-level contents as one rooted tree.

        A model tree has a single root, where a Plot can hold several
        independent things. Wrapping them in a Phase is the faithful reading:
        a Phase asserts no order between its children, which is exactly what
        two unrelated top-level models assert about each other.
        """
        elements = [payload for kind, payload in items if kind == 'element']
        loose_dates = [payload for kind, payload in items if kind == 'date']

        if loose_dates:
            if elements:
                wrapper = self.element('phase', 'Ungrouped dates')
                for date in loose_dates:
                    self._attach(date, wrapper['id'], 'Ungrouped dates')
                elements = elements + [wrapper]
                self.result.warnings.append(
                    f'{len(loose_dates)} date(s) sat outside any Sequence or '
                    'Phase. They are grouped under "Ungrouped dates" so none '
                    'is lost, unordered with respect to everything else.'
                )
            else:
                # Nothing but dates: not a model at all, just a list. They
                # still have to be recorded, without a phase to belong to.
                for date in loose_dates:
                    self.result.dates.append(date)
                self.result.model_tree = None
                self.result.element_assignments = {}
                self.result.notes.append(
                    'The file has no Sequence, Phase or Boundary, so there is '
                    'no model structure to import -- the dates will be '
                    'calibrated independently.'
                )
                return

        if not elements:
            if not self.result.errors:
                self.result.errors.append(
                    'No dates or model structure were found in the file.'
                )
            return

        if len(elements) == 1 and elements[0]['element_type'] in ('sequence', 'phase'):
            self.result.model_tree = elements[0]
            return

        self.result.model_tree = self.element(
            'phase', self.result.analysis_name or 'Imported model', elements
        )
        self.result.notes.append(
            f'The file holds {len(elements)} top-level elements. They are '
            'wrapped in one Phase, which asserts no ordering between them -- '
            'the same thing separate top-level items in a Plot assert.'
        )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def parse_oxcal(text: str, filename: Optional[str] = None) -> ImportResult:
    """
    Read an OxCal model file into dates plus a model tree.

    Every date carries the curve, reservoir offset and outlier model in force
    where it appears in the file, and every element keeps the name OxCal gave
    it -- so the structure that comes back is readable against the original
    rather than being a reconstruction with generated labels.
    """
    result = ImportResult(source_format='oxcal')
    result.analysis_name = _name_from_filename(filename)

    try:
        nodes = parse_script(text)
    except OxcalSyntaxError as exc:
        result.errors.append(str(exc))
        return result

    if not nodes:
        result.errors.append('The file has no OxCal commands in it.')
        return result

    translator = _Translator(result)
    translator.translate(nodes)

    if result.model_tree is not None and result.analysis_name is None:
        result.analysis_name = result.model_tree.get('name')

    if not result.dates and not result.errors:
        result.errors.append('The file has a model structure but no R_Date in it.')

    from app.services.date_import import MAX_DATES
    if len(result.dates) > MAX_DATES:
        result.errors.append(
            f'The model holds {len(result.dates)} dates; the limit is '
            f'{MAX_DATES} per import.'
        )

    if result.errors:
        # A partially-read model is more dangerous than none: the tree would
        # look complete while missing whatever was refused.
        return result.refuse()

    _validate_imported_tree(result)
    return result.refuse()


def _validate_imported_tree(result: ImportResult) -> None:
    """
    Hold the imported tree to the same structural rules the API enforces, so
    a file that parses cannot still be rejected at the point it is run.
    """
    if result.model_tree is None:
        return
    from app.services.model_tree import validate_tree

    try:
        validate_tree(
            result.model_tree,
            [{'id': d['id'], 'sample_name': d['sample_name'],
              'element_id': d.get('element_id')} for d in result.dates],
        )
    except ValueError as exc:
        result.errors.append(
            f'The model read from this file is not one this app can run: {exc}'
        )
        result.model_tree = None
        result.element_assignments = {}
        return

    from app.services.date_import import _validate_dates
    _validate_dates(result)
