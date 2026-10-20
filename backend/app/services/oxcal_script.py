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
they query a posterior rather than shape it. Of the Options() values only
Resolution is read, as the step the model's ranges are reported at; the rest
-- kIterations and the like -- configure OxCal's own run length, which this
app does not have.
"""
import math
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.services import curve_registry, statistics
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
    `Sequence("Hillside") { ... };` and `Boundary("Start");` are both this.
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
        if self.at_end():
            raise OxcalSyntaxError('The file ends where a value was expected.')
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

# Sequences whose members are separated by a measured distance rather than by
# stratigraphy. Their contents are read in a walk of their own, because the
# Gap() statements BETWEEN the dates carry the whole model and an ordinary
# block walk would drop them.
#
#   D_Sequence   gaps counted exactly -- tree rings.
#   V_Sequence   gaps measured, each with its own uncertainty -- varves and
#                any sampled series.
ORDINATE_SEQUENCES = {'d_sequence', 'v_sequence'}

# The Poisson-process deposition model. Read by a walk of its own too, for the
# same reason: what makes it a deposition sequence is the `z=` on each
# element, which an ordinary block walk has no use for and drops.
DEPOSITION_SEQUENCE = 'p_sequence'

# Commands that each put one radiocarbon measurement into the model.
# R_Simulate states a calendar year instead of a measurement, and the
# measurement is read off the curve (see _simulate_measurement).
DATE_COMMANDS = {'r_date', 'r_simulate'}
# Measurements pooled before calibration: read as the one date they make,
# exactly as the model editor's "Replace with pooled date" makes it
# (_translate_combine).
COMBINE_COMMAND = 'r_combine'

# Queries and plots: they read a posterior rather than shape one, so leaving
# them out changes nothing.
IGNORABLE = {
    'sum', 'kde_plot', 'kde_model', 'span', 'interval', 'difference', 'shift',
    'first', 'last', 'order', 'correl_matrix', 'correlation', 'mcmc_sample',
    'label', 'axis', 'number', 'mean', 'median', 'sigma', 'prior',
    'posterior', 'display', 'plot_axis', 'sample',
}

# Assignments that set how OxCal DRAWS something rather than what it models:
# `color="red";` inside an R_Date tints that date on the plot. Skipped for the
# same reason Axis() is -- nothing downstream of the likelihood sees them, and
# this app has no colour of its own to carry them into. Kept separate from
# IGNORABLE because those are commands and these are `name=value`.
DISPLAY_ATTRIBUTES = {'color', 'colour'}

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
    # NARROWED rather than lifted. Inside a P_Sequence a Date() with a depth
    # is exactly what an age-depth model is for -- "how old is the level at 47
    # cm" -- and it imports as an undated level. Anywhere else it is still an
    # undated free parameter with nothing bounding it.
    'date': (
        'an undated event parameter outside a deposition sequence. This app '
        'models an undated level only where a P_Sequence gives it a depth to '
        'sit at; elsewhere there is nothing to bound it.'
    ),
    'interpolate': (
        'an interpolated age. Ask for one by putting a Date() with its own '
        'z= inside the P_Sequence, which is what this app imports as an '
        'undated level.'
    ),
    'age': 'a relative-age parameter, which this app does not model.',
    'combine': 'a combination of likelihoods, which this app does not model.',
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
    'u_sequence': (
        'a uniform-deposition sequence. This app models the Poisson-process '
        'deposition of a P_Sequence, which is a different prior over the same '
        'depths -- reading one as the other would change the posterior '
        'without saying so.'
    ),
}

def _normalise_curve_name(raw: str) -> str:
    """A curve name reduced to its comparable core, as the registry compares
    names, so `Curve("=Mixed Diet")` finds `Mix_Curves("mixed diet", ...)`."""
    return re.sub(r'[^a-z0-9]+', '', (raw or '').strip().lstrip('=').lower())


def _curve_id(raw: str, owner_id: str) -> Optional[str]:
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
    return curve_registry.resolve_alias(raw, owner_id)


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
    def __init__(self, result: ImportResult, owner_id: str):
        self.result = result
        # Whose curves this file's Curve()/Mix_Curves() names resolve against.
        # See parse_oxcal: another account's curve must be as unreachable here
        # as one nobody has.
        self.owner_id = owner_id
        self.declared_outlier_models: Dict[str, Call] = {}
        # Reservoir offsets stated with a name, as (delta_r, delta_r_error),
        # for the `Delta_R("=Name")` references that cite them.
        self.declared_delta_r: Dict[str, Tuple[float, float]] = {}
        # How many dates each named offset ended up on, for the note below.
        self.delta_r_usage: Dict[str, int] = {}
        self.wrapped_sequence_dates = 0
        self.simulated_dates = 0
        # Commands the model needs and this app does not have, in file order,
        # for the offer to import the dates without the model.
        self.blocking_commands: List[str] = []
        # Plotting commands that took dates out of the model with them.
        self.date_holding_commands: List[str] = []
        self.seen_ignorable: List[str] = []
        self.seen_display_attributes: List[str] = []
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
        self._report()

    def translate_dates_only(self, nodes: List[Node]) -> None:
        """
        The file's dates with none of its model: every R_Date and R_Simulate
        wherever it sits, each with the curve, reservoir offset and outlier
        setting in force there, to be calibrated independently.

        What a refused model is offered as. Anything wrong with a DATE is
        still an error here -- an unknown curve, an undeclared offset -- since
        those would be wrong without a model too. Only the structure is let go.
        """
        self._collect_declarations(nodes)
        self.result.dates = self._collect_dates(nodes, _Scope(), None)
        self._report()
        if self.result.dates:
            self.result.notes.append(
                f'Imported without the model: the {len(self.result.dates)} '
                'date(s) will be calibrated independently. Each keeps the name '
                'of the Phase or other command it sat in as its context.'
            )
        elif not self.result.errors:
            self.result.errors.append('No dates were found in the file.')

    def _collect_dates(self, nodes: List[Node], scope: _Scope,
                       context: Optional[str]) -> List[Dict[str, Any]]:
        dates: List[Dict[str, Any]] = []
        for node in nodes:
            command = node.command.lower()
            if command == '=':
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
            if command in DATE_COMMANDS:
                date = self._translate_date(node, scope)
                if date is not None:
                    date['phase_name'] = context
                    dates.append(date)
                continue
            if command == COMBINE_COMMAND:
                # Without a model the pool still stands where it can be made;
                # where it cannot, its dates are read one by one, which is
                # what an import without the model offers.
                errors_before = len(self.result.errors)
                date, _reason = self._translate_combine(node, scope.copy())
                if date is not None:
                    date['phase_name'] = context
                    dates.append(date)
                    continue
                del self.result.errors[errors_before:]
                dates.extend(self._collect_dates(node.block or [], scope.copy(), context))
                continue
            # A date written as an argument -- After(R_Date(...)) -- is still
            # a measurement once the constraint around it is gone.
            for argument in node.args:
                if isinstance(argument, Call) and argument.name.lower() in DATE_COMMANDS:
                    date = self._translate_date(
                        Node(argument.name, argument.args, None, node.line), scope)
                    if date is not None:
                        date['phase_name'] = context
                        dates.append(date)
            if node.block:
                inner = context if command in TRANSPARENT else (node.name or context)
                dates.extend(self._collect_dates(node.block, scope.copy(), inner))
        return dates

    def _report(self) -> None:
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

        if self.simulated_dates:
            self.result.notes.append(
                f'{self.simulated_dates} date(s) are R_Simulate. Each was '
                "given its curve's radiocarbon age at the stated year, with the "
                'stated error. OxCal adds random scatter to a simulated '
                'measurement, so its results vary from run to run; these do '
                'not.'
            )
        if self.seen_ignorable:
            unique = sorted(set(self.seen_ignorable))
            self.result.notes.append(
                'Ignored ' + ', '.join(f'{name}()' for name in unique)
                + ' -- these ask OxCal to plot or summarise a result and do '
                'not affect the model, so leaving them out changes nothing.'
            )
        if self.seen_display_attributes:
            unique = sorted(set(self.seen_display_attributes))
            self.result.notes.append(
                'Ignored ' + ', '.join(f'{name}=' for name in unique)
                + ' -- these set how OxCal draws a date and do not affect the '
                'model.'
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
                # An Options() assignment, or a display attribute written on a
                # container -- `Phase("...") { color="red"; ... }`. Neither
                # shapes the model; the display ones are noted so the import
                # says what it passed over.
                if node.args and str(node.args[0]).lower() in DISPLAY_ATTRIBUTES:
                    self.seen_display_attributes.append(str(node.args[0]).lower())
                continue
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
            if command in DATE_COMMANDS:
                date = self._translate_date(node, scope)
                if date is not None:
                    items.append(('date', date))
                continue
            if command == COMBINE_COMMAND:
                date, reason = self._translate_combine(node, scope)
                if date is not None:
                    items.append(('date', date))
                elif reason is not None:
                    self.blocking_commands.append(node.command)
                    self.error(node, reason)
                continue
            if command == 'gap':
                # Emitted rather than acted on here: only a D_Sequence or a
                # V_Sequence knows what to do with one, and every other
                # container reports it (see _reject_gaps).
                items.append(('gap', self._translate_gap(node)))
                continue
            if command in ORDINATE_SEQUENCES:
                element = self._translate_wiggle_sequence(node, scope.copy())
                if element is not None:
                    items.append(('element', element))
                continue
            if command == DEPOSITION_SEQUENCE:
                element = self._translate_deposition_sequence(node, scope.copy())
                if element is not None:
                    items.append(('element', element))
                continue
            if command in STRUCTURAL:
                element = self._translate_structural(node, scope)
                if element is not None:
                    items.append(('element', element))
                continue
            if command in UNSUPPORTED:
                self.blocking_commands.append(node.command)
                self.error(
                    node,
                    f'{node.command}() is {UNSUPPORTED[command]}',
                )
                continue
            if command in IGNORABLE:
                self._check_ignorable_holds_dates(node)
                self.seen_ignorable.append(node.command)
                continue

            self.blocking_commands.append(node.command)
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
            self.date_holding_commands.append(node.command)
            self.result.warnings.append(
                f'Line {node.line}: {node.command}() contains {buried} date(s). '
                f'{node.command}() summarises distributions rather than '
                'modelling them, so those dates were NOT imported. Move them '
                'into a Phase if they belong in the model.'
            )

    def _count_dates(self, nodes: List[Node]) -> int:
        total = 0
        for node in nodes:
            if node.command.lower() in DATE_COMMANDS:
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
        resolved = _curve_id(name, self.owner_id)
        if resolved is None:
            available = ', '.join(
                spec.name for spec in curve_registry.list_specs(
                    self.owner_id, include_archived=True)
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
        a_id = curve_registry.resolve_alias(str(args[1]), self.owner_id)
        b_id = curve_registry.resolve_alias(str(args[2]), self.owner_id)
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

        match = curve_registry.find_mixed(a_id, b_id, percent, error, self.owner_id)
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
        settings = []
        for item in node.block or []:
            if item.command != '=' or not item.args:
                continue
            name = str(item.args[0])
            if name.lower() == 'resolution' and self._take_resolution(item):
                continue
            settings.append(name)
        if settings:
            self.result.notes.append(
                'Options(' + ', '.join(sorted(settings)) + ') was not carried '
                "over: those configure OxCal's own grid and run length, and "
                'this app integrates exactly where it can and extends its own '
                'run until it converges.'
            )

    def _take_resolution(self, item: Node) -> bool:
        """
        Carry Options Resolution over as the step the ranges are reported at,
        or say why not. True when it was taken.

        OxCal's Resolution is its calculation grid as well; here the solver
        picks its own grid and only the reporting follows. The note says so,
        since an index computed at OxCal's coarser grid can differ from this
        app's.
        """
        value = item.args[1] if len(item.args) > 1 else None
        try:
            years = float(value)
        except (TypeError, ValueError):
            return False
        # A non-finite value is not a whole number; int() would raise on it.
        if not math.isfinite(years) or years != int(years) or not 1 <= years <= 100:
            self.result.notes.append(
                f'Resolution={value} was not carried over: the reporting '
                'resolution here is a whole number of years from 1 to 100. '
                'The analysis uses the default of 5.'
            )
            return True
        self.result.resolution = int(years)
        self.result.notes.append(
            f'Resolution={int(years)} was carried over as the step the '
            "modelled ranges are reported at. OxCal also calculates on that "
            "grid; this app's solver picks its own, so agreement indices can "
            'differ slightly from an OxCal run at a coarse resolution.'
        )
        return True

    def _translate_date(self, node: Node, scope: _Scope) -> Optional[Dict[str, Any]]:
        simulated = node.command.lower() == 'r_simulate'
        numbers = node.numbers
        if len(numbers) < 2:
            self.error(
                node,
                f'{node.command} needs '
                + ('a calendar year' if simulated else 'a radiocarbon age')
                + f' and an error; found {len(numbers)} number(s).',
            )
            return None

        name = node.name or f'{node.command} line {node.line}'
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
            elif command == '=':
                self._apply_date_attribute(child, date, name)
            elif command in IGNORABLE:
                self.seen_ignorable.append(child.command)
            else:
                self.error(
                    child,
                    f'{child.command}() inside {node.command}("{name}") is not '
                    'something this importer recognises.',
                )

        # After the block, because a Curve() or Delta_R() inside it decides
        # what the simulated measurement is.
        if simulated and not self._simulate_measurement(node, date, numbers[0]):
            return None

        if date['delta_r'] is not None and inner.delta_r_name:
            self.delta_r_usage[inner.delta_r_name] = (
                self.delta_r_usage.get(inner.delta_r_name, 0) + 1
            )

        return date

    def _translate_combine(self, node: Node, scope: _Scope
                           ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        An R_Combine as the one date it makes, or (None, why not).

        The date is the one the model editor's "Replace with pooled date" makes
        (frontend utils/sameEvent.pooledMeasurement): the error-weighted mean
        and its error, on the dates' shared curve and reservoir offset, with
        the same refusals -- dates on different curves or offsets, and dates
        that fail Ward and Wilson's test. OxCal pools those last ones anyway and
        only reports T; a pool of dates from more than one event is a date of
        nothing, so here it is refused, naming T against its critical value.

        An Outlier() written in the R_Combine's own block applies to the pooled
        date. One on a date inside it is refused: the pool is made before
        calibration, so one of its measurements cannot be an outlier on its
        own, and dropping the setting would change the model without saying so.
        """
        name = node.name or f'R_Combine line {node.line}'
        if node.numbers:
            return None, (
                f'R_Combine("{name}") has a number after its name, which adds to '
                'the pooled error in OxCal. This importer does not apply it; '
                'remove it, or pool the dates in the model editor.')
        inner = scope.copy()
        dates: List[Dict[str, Any]] = []
        pooled_outlier: Optional[Node] = None
        for child in node.block or []:
            command = child.command.lower()
            if command in DATE_COMMANDS:
                date = self._translate_date(child, inner)
                if date is None:
                    return None, None            # the date's own error is recorded
                if date['outlier_prior']:
                    return None, (
                        f'R_Combine("{name}") has an outlier setting on '
                        f'{date["sample_name"]}. Its measurements are pooled before '
                        'calibration, so one of them cannot be an outlier on its own; '
                        'put the Outlier() on the R_Combine instead.')
                dates.append(date)
            elif command == 'curve':
                self._select_curve(child, inner)
            elif command == 'delta_r':
                self._apply_delta_r(child, inner)
            elif command == 'outlier':
                pooled_outlier = child
            elif command == '=' and child.args and str(child.args[0]).lower() in DISPLAY_ATTRIBUTES:
                self.seen_display_attributes.append(str(child.args[0]).lower())
            elif command in IGNORABLE:
                self.seen_ignorable.append(child.command)
            else:
                return None, (
                    f'{child.command}() inside R_Combine("{name}") is not something '
                    'this importer recognises.')
        if len(dates) < 2:
            return None, (
                f'R_Combine("{name}") pools {len(dates)} date; it needs two or more.')

        test = statistics.same_event(dates)
        if test['refused']:
            return None, (
                f'R_Combine("{name}") cannot pool its dates: {test["refused"]} '
                'The model editor refuses the same pool.')
        figures = (f'T = {test["t"]:.1f} · {round(test["level"] * 100)}% critical '
                   f'{test["critical"]:.2f} · df {test["df"]}')
        if not test['consistent']:
            return None, (
                f'R_Combine("{name}") pools dates that fail the same-event test '
                f'({figures}), so they are not one event. OxCal pools them anyway; '
                'this app does not. Put them in a Phase as separate dates, or leave '
                'out the one that does not belong.')

        first = dates[0]
        sources = '; '.join(f'{d["sample_name"]} {d["age_bp"]:g} ± {d["error_1sigma"]:g} BP'
                            for d in dates)
        pooled: Dict[str, Any] = {
            **first,
            'id': f'd-{uuid.uuid4().hex[:12]}',
            'sample_name': name,
            'age_bp': round(test['mean_bp'], 1),
            'error_1sigma': round(test['error_bp'], 1),
            'depth_cm': None,
            'notes': (f'R_Combine of {len(dates)} dates (Ward and Wilson 1978; '
                      f'{figures}): {sources}.'),
            'outlier_prior': 0.0,
            'outlier_model': 'general',
        }
        if pooled_outlier is not None:
            self._apply_outlier(pooled_outlier, pooled)
        self.result.notes.append(
            f'R_Combine("{name}") was imported as one pooled date, '
            f'{pooled["age_bp"]:g} ± {pooled["error_1sigma"]:g} BP ({figures}).')
        return pooled, None

    def _simulate_measurement(self, node: Node, date: Dict[str, Any],
                              year: float) -> bool:
        """
        `R_Simulate(name, year, error)` -- the measurement a sample from that
        calendar year would give, measured with that error.

        The year is OxCal's: AD positive, BC negative, so cal BP is 1950 minus
        it. The measurement is the curve's own radiocarbon age there, plus the
        reservoir offset in force, since a marine sample of calendar age t
        measures curve(t) + delta_r.

        OxCal draws a random measurement around that value, a different one
        every run. This app uses the value itself, as the curve browser's
        Simulate does: the model is then reproducible, and differs from any
        one OxCal run only by that run's scatter. The note in translate() says
        so.
        """
        cal_bp = 1950.0 - year
        try:
            curve = curve_registry.resolve(date['curve_id'])
        except ValueError as exc:
            self.error(node, f'{node.command}("{date["sample_name"]}"): {exc}')
            return False

        low, high = float(curve.cal_ages.min()), float(curve.cal_ages.max())
        if not low <= cal_bp <= high:
            self.error(
                node,
                f'{node.command}("{date["sample_name"]}") is for the year '
                f'{year:g} ({cal_bp:g} cal BP), outside {date["curve_id"]}, '
                f'which covers {low:g}-{high:g} cal BP. There is no curve '
                'value there to simulate a measurement from.',
            )
            return False

        c14_age, _ = curve.interpolate(cal_bp)
        date['age_bp'] = round(float(c14_age) + (date['delta_r'] or 0.0), 1)
        date['notes'] = (
            f'Simulated (R_Simulate) for the year {year:g}, {cal_bp:g} cal BP.'
        )
        self.simulated_dates += 1
        return True

    def _apply_date_attribute(self, node: Node, date: Dict[str, Any],
                              name: str) -> None:
        """
        A `key=value` written inside an R_Date.

        `z=` is the date's place on a P_Sequence's depth axis, read here rather
        than only in _translate_deposition_sequence so the same R_Date reader
        serves both, and so a stray z= outside a deposition sequence is
        recorded as an ordinary depth rather than refused -- depth_cm is a
        column every date has.

        The display attributes are skipped, and anything else is refused: an
        unrecognised key could be setting a likelihood this app would then get
        wrong, and guessing which is which is exactly what this module does
        not do.
        """
        key = str(node.args[0]).lower() if node.args else ''

        if key == 'z':
            try:
                date['depth_cm'] = float(node.args[1])
            except (TypeError, ValueError, IndexError):
                self.error(
                    node,
                    f'R_Date("{name}") has a z= that is not a number.',
                )
            return

        if key in DISPLAY_ATTRIBUTES:
            self.seen_display_attributes.append(key)
            return

        self.error(
            node,
            f'{key or "An assignment"}= inside R_Date("{name}") is not '
            'something this importer recognises. It has been left out rather '
            'than guessed at -- if it does not affect the model, delete the '
            'line and import again.',
        )

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

    def _translate_gap(self, node: Node) -> Dict[str, Any]:
        """
        `Gap(years)` or `Gap(years, error)` -- the distance between two
        adjacent members of a D_Sequence or V_Sequence.

        Read here so the value survives the block walk; whether it is in a
        place that can use it is the container's business.
        """
        numbers = node.numbers
        return {
            'line': node.line,
            'value': numbers[0] if numbers else None,
            'error': numbers[1] if len(numbers) > 1 else 0.0,
        }

    def _reject_gaps(self, items: List[Tuple[str, Dict[str, Any]]],
                     where: str) -> None:
        """A Gap() outside a wiggle-matched sequence measures nothing."""
        for kind, payload in items:
            if kind != 'gap':
                continue
            self.result.errors.append(
                f"Line {payload['line']}: Gap() inside {where} is not "
                'something this app models. A gap between dates is the '
                'measured separation of a D_Sequence or V_Sequence; between '
                'phases, write two Boundaries with a Hiatus between them.'
            )

    def _translate_wiggle_sequence(self, node: Node,
                                   scope: _Scope) -> Optional[Dict[str, Any]]:
        """
        `D_Sequence` / `V_Sequence` -- a series whose members are separated by
        measured gaps -- into this app's `wiggle_sequence` element.

        OxCal writes the series OLDEST FIRST with a `Gap()` between adjacent
        members, and this app records each member's distance from a REFERENCE
        as `ring_offset`: 0 at the reference, increasing into the past. The
        reference chosen here is the YOUNGEST member -- the last one in the
        file, the outermost ring -- which is the conventional one and the one a
        felling date is quoted against. So the offsets are the running total of
        the gaps counted backwards from the end.

        WHERE A V_SEQUENCE IS NOT REPRODUCED EXACTLY, and it is said out loud
        rather than left in the arithmetic: OxCal's gaps form a CHAIN, so two
        members' distances from the reference share every gap between them and
        their uncertainties are correlated. This app carries one uncertainty
        per member and treats them as independent (services/wiggle_match.py),
        which gives each member the right marginal spread but drops that
        correlation. The gap errors are therefore added in quadrature along the
        chain and a warning says so, because a reader comparing this against an
        OxCal run needs to know which model produced which number.
        """
        command = node.command.lower()
        name = node.name or ('Wiggle match' if command == 'd_sequence'
                             else 'Varve sequence')
        items = self._translate_block(node.block or [], scope)

        for kind, payload in items:
            if kind == 'element':
                self.error(
                    node,
                    f'{node.command}("{name}") contains a '
                    f"{payload.get('element_type')}. A wiggle-matched series "
                    'is its dates and the gaps between them; there is no '
                    'position in it for a phase, a boundary or another '
                    'sequence.',
                )
                return None

        entries: List[Tuple[Dict[str, Any], float, float]] = []
        cumulative = 0.0
        cumulative_variance = 0.0
        pending_value = 0.0
        pending_variance = 0.0
        pending_seen = False
        has_gap_error = False

        for kind, payload in items:
            if kind == 'gap':
                if payload['value'] is None:
                    self.error(
                        node,
                        f'{node.command}("{name}") has a Gap() with no '
                        'number in it. The gaps are the model: a gap of '
                        'unknown size cannot be read as zero.',
                    )
                    return None
                pending_value += float(payload['value'])
                pending_variance += float(payload['error'] or 0.0) ** 2
                has_gap_error = has_gap_error or bool(payload['error'])
                pending_seen = True
                continue

            date = payload
            if entries:
                if not pending_seen:
                    self.error(
                        node,
                        f'{node.command}("{name}") has two dates with no '
                        f"Gap() between them (at \"{date['sample_name']}\"). "
                        'Every step of the series needs its measured '
                        'separation, or the two are one event rather than two.',
                    )
                    return None
                cumulative += pending_value
                cumulative_variance += pending_variance
            elif pending_seen:
                self.error(
                    node,
                    f'{node.command}("{name}") opens with a Gap(). A gap '
                    'separates two members, so there is nothing for the first '
                    'one to be a gap from.',
                )
                return None
            entries.append((date, cumulative, cumulative_variance))
            pending_value = pending_variance = 0.0
            pending_seen = False

        if not entries:
            self.error(
                node,
                f'{node.command}("{name}") has no dates in it.',
            )
            return None
        if pending_seen:
            self.result.warnings.append(
                f'Line {node.line}: {node.command}("{name}") ends with a '
                'Gap() after its last date. A gap to nothing measures '
                'nothing, so it was left out -- if it is the gap to the '
                'felling ring, add that ring as a date.'
            )

        total, total_variance = entries[-1][1], entries[-1][2]
        element = self.element('wiggle_sequence', name)
        for date, distance, variance in entries:
            # Distance BACK from the youngest member, which is the reference.
            date['ring_offset'] = float(total - distance)
            spread = total_variance - variance
            date['ring_offset_error'] = (
                float(spread ** 0.5) if spread > 0 else None)
            self._attach(date, element['id'], name)

        element['params'] = {
            # OxCal's Gap() is a number of years however the series was
            # counted, so that is what the axis is labelled -- rather than
            # calling a varve count "rings" and being wrong in a second
            # literature.
            'gap_units': 'years',
            'reference_sample_id': entries[-1][0]['id'],
        }

        if has_gap_error:
            self.result.warnings.append(
                f'Line {node.line}: {node.command}("{name}") gives its gaps '
                'their own uncertainties. This app carries one uncertainty per '
                "date -- that date's distance from the reference, here the "
                'youngest member -- so the gap errors were added in quadrature '
                'along the series. Each date then has the right spread, but '
                'this app treats those spreads as independent where OxCal '
                'shares each gap between every date beyond it.'
            )
        self.result.notes.append(
            f'{node.command}("{name}") was read as a wiggle match of '
            f'{len(entries)} dates spanning {total:g} years, measured from '
            f'"{entries[-1][0]["sample_name"]}" as the reference.'
        )
        return element

    @staticmethod
    def _depth_of(node: Node) -> Optional[float]:
        """
        The `z=` an element carries, which is what puts it on a P_Sequence's
        depth axis. None when it has none.
        """
        for child in node.block or []:
            if (child.command == '=' and child.args
                    and str(child.args[0]).lower() == 'z'):
                try:
                    return float(child.args[1])
                except (TypeError, ValueError, IndexError):
                    return None
        return None

    def _translate_deposition_sequence(self, node: Node,
                                       scope: _Scope) -> Optional[Dict[str, Any]]:
        """
        `P_Sequence(name, k, interval, prior)` -- deposition modelled as a
        Poisson process along a depth axis -- into this app's
        `deposition_sequence`.

        Every member carries its own `z=`, which is what makes the sequence a
        depth axis rather than an ordering, and this app reads z as DEPTH
        BELOW THE SURFACE: larger is deeper and therefore older. OxCal files
        overwhelmingly write it that way, but nothing in the language says so,
        so a file whose z runs the other way is warned about rather than
        silently modelled upside down.

        Three readings worth stating:

          * `Date("...")` with a z is an UNDATED LEVEL -- the "how old is 47
            cm" this whole model exists to answer -- which is why Date() is
            narrowed rather than refused outright (see UNSUPPORTED).
          * `Boundary("...")` with a z is read as an undated level too. In
            this app a deposition sequence's ends are levels of the same
            Poisson process, so the parameter is the same one under another
            name -- but it is not the uniform transition a Boundary is
            everywhere else here, and that is worth a warning rather than a
            note.
          * The INTERPOLATION INTERVAL (OxCal's third argument) inserts extra
            levels every so many units. This app takes the levels it is given,
            so the argument is reported and not applied.
        """
        name = node.name or 'Deposition sequence'
        numbers = node.numbers
        k = numbers[0] if numbers else None
        if k is None or k <= 0:
            self.error(
                node,
                f'P_Sequence("{name}") needs a positive deposition rate k as '
                'its first number. k is events per unit depth and decides how '
                'rigid the model is, so it cannot be guessed at.',
            )
            return None
        if len(numbers) > 1 and numbers[1]:
            self.result.notes.append(
                f'Line {node.line}: P_Sequence("{name}") asks OxCal to '
                f'interpolate every {numbers[1]:g} units. This app models the '
                'levels it is given and reports an age at any depth you add as '
                'an undated level, so that argument was not applied.'
            )

        # A U(a,b) argument makes k variable, a and b being log10 multipliers
        # on the k given -- so the range is centred on it rather than absolute.
        variable = None
        for argument in node.args:
            if isinstance(argument, Call) and argument.name.lower() == 'u':
                bounds = [a for a in argument.args if isinstance(a, (int, float))]
                if len(bounds) >= 2:
                    variable = (float(bounds[0]), float(bounds[1]))
        import math
        log_k = math.log10(k)

        element = self.element('deposition_sequence', name)
        element['params'] = {
            'k': None if variable else float(k),
            'k_variable': variable is not None,
            'log_k_min': log_k + variable[0] if variable else log_k - 2.0,
            'log_k_max': log_k + variable[1] if variable else log_k + 2.0,
            'depth_units': 'cm',
        }

        inner = scope.copy()
        depths: List[float] = []
        dated = 0
        boundary_levels = 0
        for child in node.block or []:
            command = child.command.lower()
            if command == '=':
                continue                      # this sequence's own z, if any
            if command == 'curve':
                self._select_curve(child, inner)
                continue
            if command == 'delta_r':
                self._apply_delta_r(child, inner)
                continue
            if command in IGNORABLE:
                self._check_ignorable_holds_dates(child)
                self.seen_ignorable.append(child.command)
                continue

            depth = self._depth_of(child)
            if command in DATE_COMMANDS:
                date = self._translate_date(child, inner)
                if date is None:
                    return None
                if depth is None:
                    self.error(
                        child,
                        f'{child.command}("{date["sample_name"]}") inside '
                        f'P_Sequence("{name}") has no z=. Depth is the axis '
                        'the deposition model runs along, so a level without '
                        'one cannot take part in it.',
                    )
                    return None
                date['depth_cm'] = depth
                depths.append(depth)
                dated += 1
                self._attach(date, element['id'], name)
                continue

            if command in ('date', 'boundary'):
                if depth is None:
                    self.error(
                        child,
                        f'{child.command}("{child.name or ""}") inside '
                        f'P_Sequence("{name}") has no z=. An undated level is '
                        'a question about a DEPTH, so there is nothing to ask '
                        'without one.',
                    )
                    return None
                depths.append(depth)
                boundary_levels += command == 'boundary'
                element['children'].append({
                    **self.element('interpolated_depth',
                                   child.name or f'{depth:g} cm'),
                    'params': {'depth_cm': depth},
                })
                continue

            self.blocking_commands.append(child.command)
            self.error(
                child,
                f'{child.command}() inside P_Sequence("{name}") is not '
                'something this app models. A deposition sequence holds dates '
                'and undated levels, each with its own z=.',
            )
            return None

        if not dated:
            self.error(
                node,
                f'P_Sequence("{name}") has no R_Date in it. Deposition cannot '
                'be modelled against depth with nothing measured at any level.',
            )
            return None

        if boundary_levels:
            self.result.warnings.append(
                f'Line {node.line}: P_Sequence("{name}") has {boundary_levels} '
                'Boundary element(s) with a depth. They were imported as '
                'undated levels of the deposition sequence, which is the same '
                'parameter under another name here -- but NOT the uniform '
                'transition a Boundary is elsewhere in this app.'
            )
        if len(depths) > 1 and depths[0] < depths[-1]:
            self.result.warnings.append(
                f'Line {node.line}: P_Sequence("{name}") has z increasing down '
                'the file. z has been read as DEPTH BELOW THE SURFACE -- '
                'larger is deeper and older. If it is a height above a datum, '
                'the sequence is modelled upside down; negate the values and '
                'import again.'
            )
        return element

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
        self._reject_gaps(items, f'{node.command}("{name}")')

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
        self._reject_gaps(items, 'the top level of the file')
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

        if len(elements) == 1 and elements[0]['element_type'] in (
                'sequence', 'phase', 'wiggle_sequence', 'deposition_sequence'):
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

def parse_oxcal(text: str, owner_id: str, filename: Optional[str] = None) -> ImportResult:
    """
    Read an OxCal model file into dates plus a model tree.

    Every date carries the curve, reservoir offset and outlier model in force
    where it appears in the file, and every element keeps the name OxCal gave
    it -- so the structure that comes back is readable against the original
    rather than being a reconstruction with generated labels.

    `owner_id` is whose curves the file's `Curve(...)` and `Mix_Curves(...)`
    names are resolved against. A file naming a curve that belongs to another
    account fails exactly as one naming a curve nobody has: the alternative
    would let an import confirm what other people have imported, and would
    calibrate one user's dates against a curve they cannot see.
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

    translator = _Translator(result, owner_id)
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
        return _offer_dates_only(result.refuse(), translator, nodes, owner_id)

    _validate_imported_tree(result)
    if result.errors:
        return _offer_dates_only(result.refuse(), translator, nodes, owner_id)
    return result


def _offer_dates_only(result: ImportResult, translator: '_Translator',
                      nodes: List[Node], owner_id: str) -> ImportResult:
    """
    Attach the file read as dates alone to a refused model, when that reading
    succeeds, so the user can be asked whether to take the dates without it.

    Names the commands that sank the model. Where nothing was refused as such
    but a plotting command such as Sum() held the dates, that command is
    named instead, being why there was no model left to import.
    """
    result.blocking_commands = list(dict.fromkeys(
        translator.blocking_commands or translator.date_holding_commands
    ))

    fallback = ImportResult(source_format='oxcal')
    fallback.analysis_name = result.analysis_name
    _Translator(fallback, owner_id).translate_dates_only(nodes)

    from app.services.date_import import MAX_DATES, _validate_dates
    if len(fallback.dates) > MAX_DATES:
        return result
    if fallback.ok:
        _validate_dates(fallback)
    if fallback.ok and fallback.dates:
        result.dates_only = fallback
    return result


def _validate_imported_tree(result: ImportResult) -> None:
    """
    Hold the imported tree to the same structural rules the API enforces, so
    a file that parses cannot still be rejected at the point it is run.
    """
    if result.model_tree is None:
        return
    from app.services.model_tree import (
        elements_of_type, standalone_wiggle_match, validate_ordinates,
        validate_tree,
    )

    described = [
        {'id': d['id'], 'sample_name': d['sample_name'],
         'element_id': d.get('element_id'),
         'ring_offset': d.get('ring_offset'),
         'ring_offset_error': d.get('ring_offset_error'),
         'depth_cm': d.get('depth_cm'),
         'outlier_prior': d.get('outlier_prior')}
        for d in result.dates
    ]
    try:
        validate_tree(result.model_tree, described)
        validate_ordinates(result.model_tree, described)
    except ValueError as exc:
        result.errors.append(
            f'The model read from this file is not one this app can run: {exc}'
        )
        result.model_tree = None
        result.element_assignments = {}
        return

    # A D_Sequence this app can read but cannot run is still a file it cannot
    # import: the rule here is to fail naming the construct rather than to
    # hand back a model that fails later, on a screen with less to say about
    # why.
    if (elements_of_type(result.model_tree, 'wiggle_sequence')
            and standalone_wiggle_match(result.model_tree, described) is None):
        result.errors.append(
            'This file puts a wiggle-matched sequence inside a larger model, '
            'which this app does not yet solve: a wiggle match is one '
            'calendar age with the measured gaps added to it, and the '
            'surrounding Boundaries and phases would have to model it that '
            'way rather than as separate dates. A file holding the '
            'D_Sequence or V_Sequence on its own imports and runs.'
        )
        result.model_tree = None
        result.element_assignments = {}
        return

    from app.services.date_import import _validate_dates
    _validate_dates(result)
