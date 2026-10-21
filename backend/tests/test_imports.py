"""
Reading dates in from a file -- services/date_import.py (CSV),
services/oxcal_script.py (OxCal's CQL2), and api/imports.py.

The load-bearing tests are in TestOxcalMatchesTheHandBuiltFixtures. The three
Hillside .oxcal files in tests/fixtures/oxcal/ are ALSO transcribed by hand in
tests/oxcal_fixture.model_tree(), and those hand-built trees are what
test_oxcal_agreement.py holds against OxCal's own published output for the same
runs. So checking the parse against them chains the importer onto an oracle
that reaches all the way out to OxCal: if the importer reproduces the tree, it
reproduces a model already known to agree with the reference implementation.

Everything else here is about the two ways an importer can be dangerous:
reading something wrongly, and reading something SILENTLY. The refusal tests
matter as much as the success ones -- an OxCal file using a construct this app
does not model must fail loudly rather than import as the nearest thing we do
have.
"""
import io
import os

import pytest

import oxcal_fixture as ox
from app.services import date_import
from app.services.date_import import template_csv
from app.services import curve_registry
from app.services.date_import import parse_csv as _parse_csv, parse_upload as _parse_upload
from app.services.oxcal_script import parse_oxcal as _parse_oxcal


# The three parsers resolve a file's curve NAMES against one account's curves,
# so each takes an owner id (see services/oxcal_script.parse_oxcal). Almost
# nothing in this file is about that -- these are tests of the readers, and the
# curves they name are the three builtins, which belong to everybody.
#
# So they are wrapped once here with SCOPE_ANY rather than threaded through
# eighty-odd call sites, which would bury what each test is actually about.
# The scoping itself is covered where it belongs, in test_curve_privacy.py.

def parse_csv(text, filename=None):
    return _parse_csv(text, curve_registry.SCOPE_ANY, filename)


def parse_upload(filename, content):
    return _parse_upload(filename, content, curve_registry.SCOPE_ANY)


def parse_oxcal(text, filename=None):
    return _parse_oxcal(text, curve_registry.SCOPE_ANY, filename)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


def read_fixture(*parts):
    with open(os.path.join(FIXTURES, *parts), encoding='utf-8') as fh:
        return fh.read()


def shape(node):
    """A tree reduced to type/name/children, so generated ids don't matter."""
    if node is None:
        return None
    return {
        'type': node['element_type'],
        'name': node['name'],
        'children': [shape(c) for c in node.get('children') or []],
    }


def phase_of(result):
    """{sample name: the name of the phase it landed in}."""
    names = {}
    for element in _walk(result.model_tree):
        names[element['id']] = element['name']
    return {
        d['sample_name']: names.get(d.get('element_id'))
        for d in result.dates
    }


def _walk(node):
    if node is None:
        return
    yield node
    for child in node.get('children') or []:
        yield from _walk(child)


# --------------------------------------------------------------------------
# The oracle
# --------------------------------------------------------------------------

class TestOxcalMatchesTheHandBuiltFixtures:
    """
    Parsing each .oxcal must produce the tree tests/oxcal_fixture.py builds by
    hand for it -- see this module's docstring for why that is the strongest
    check available here.
    """

    @pytest.mark.parametrize('run,filename', [
        ('ordered', 'Hillside_sites_3.oxcal'),
        ('unordered', 'Hillside_sites_3_unordered.oxcal'),
        ('shared_boundary', 'Hillside_sites_3_shared_boundary.oxcal'),
    ])
    def test_the_structure_is_the_one_the_fixture_builds(self, run, filename):
        result = parse_oxcal(read_fixture('oxcal', filename), filename)

        assert result.ok, result.errors
        assert shape(result.model_tree) == shape(ox.model_tree(run))

    @pytest.mark.parametrize('run,filename', [
        ('ordered', 'Hillside_sites_3.oxcal'),
        ('unordered', 'Hillside_sites_3_unordered.oxcal'),
        ('shared_boundary', 'Hillside_sites_3_shared_boundary.oxcal'),
    ])
    def test_every_date_lands_in_the_phase_the_fixture_puts_it_in(self, run, filename):
        result = parse_oxcal(read_fixture('oxcal', filename), filename)

        expected = {}
        for name in ox.FIRE_FEATURE_J:
            expected[name] = 'Fire_Feature_J'
        for name in ox.FIRE_FEATURE_K:
            expected[name] = 'Fire_Feature_K'
        for name in ox.FIRE_FEATURE_A:
            expected[name] = 'Fire_Feature_A'

        assert phase_of(result) == expected

    def test_the_measurements_match_the_c14_csv(self):
        """Ages and errors as OxCal was actually given them."""
        result = parse_oxcal(read_fixture('oxcal', 'Hillside_sites_3.oxcal'),
                             'Hillside_sites_3.oxcal')

        expected = {
            ox.key(m['sample_name']): (m['age_bp'], m['error_1sigma'])
            for m in ox.measurements()
        }
        got = {
            ox.key(d['sample_name']): (d['age_bp'], d['error_1sigma'])
            for d in result.dates
        }
        assert got == expected

    def test_the_curve_is_taken_from_the_file_not_defaulted(self):
        """
        The Hillside models declare Curve("SHCal20") once, at the top. Every
        date must come back on it -- silently defaulting to IntCal20 would
        calibrate a Southern Hemisphere site against the wrong curve and still
        produce a plausible-looking answer.
        """
        result = parse_oxcal(read_fixture('oxcal', 'Hillside_sites_3.oxcal'), 'x.oxcal')

        assert {d['curve_id'] for d in result.dates} == {'shcal20'}


class TestTheCharcoalModel:
    """
    tests/fixtures/oxcal_charcoal/Hillside_Seq.oxcal -- two outlier models,
    declared and applied per date, and a nested Curve("=SHCal20").
    """

    def setup_method(self):
        self.result = parse_oxcal(
            read_fixture('oxcal_charcoal', 'Hillside_Seq.oxcal'), 'Hillside_Seq.oxcal'
        )

    def test_it_imports(self):
        assert self.result.ok, self.result.errors
        assert len(self.result.dates) == 11

    def test_the_declared_outliers_come_through_per_date(self):
        by_name = {d['sample_name']: d for d in self.result.dates}

        # Outlier("Charcoal", 1.00) -- material DECLARED residual, not merely
        # suspected. The prior of 1.0 is the point of the model.
        for name in ('LD10010', 'LD10005', 'LA10002'):
            assert by_name[name]['outlier_model'] == 'charcoal'
            assert by_name[name]['outlier_prior'] == 1.0

        for name in ('LD10012', 'LD10006', 'LA10001'):
            assert by_name[name]['outlier_model'] == 'general'
            assert by_name[name]['outlier_prior'] == 0.05

    def test_a_nested_curve_reference_resolves(self):
        """Curve("=SHCal20") inside a Phase refers back to the declared curve."""
        assert {d['curve_id'] for d in self.result.dates} == {'shcal20'}

    def test_the_declared_outlier_models_raise_nothing(self):
        """
        The file declares General as T(5),U(0,4) and Charcoal as
        Exp(1,-10,0),U(0,3) -- exactly what services/exact_chain implements,
        so there is nothing to warn about.
        """
        assert self.result.warnings == []


class TestTheDuneSectionModel:
    def test_sixteen_dates_in_one_phase(self):
        result = parse_oxcal(
            read_fixture('oxcal_outlier', 'Dune_Section.oxcal'), 'Dune_Section.oxcal'
        )

        assert result.ok, result.errors
        assert len(result.dates) == 16
        assert shape(result.model_tree) == {
            'type': 'sequence', 'name': 'Dune Section', 'children': [
                {'type': 'boundary', 'name': 'Start', 'children': []},
                {'type': 'phase', 'name': 'Start Phase 1', 'children': []},
                {'type': 'boundary', 'name': 'End', 'children': []},
            ],
        }
        assert all(d['outlier_prior'] == 0.05 for d in result.dates)


class TestTheCoastalMiddenModel:
    """
    tests/fixtures/oxcal_delta_r/Coastal_midden.oxcal -- the structure of a
    real coastal model, with its lab codes and ages replaced. Its header
    states the curves, the outlier models and the reservoir offset as named
    definitions and then cites each by name where it applies, which is the
    ordinary shape of a hand-written OxCal file; the citation form
    `Delta_R("=LocalMarine", )` is what the importer used to reject outright
    as a syntax error, taking the whole file with it.

    Marine and terrestrial dates sit in the same phases here, which is what
    makes the scoping of the offset matter: it belongs to Marine20, and the
    SHCal20 dates beside it must not pick it up.
    """

    def setup_method(self):
        self.result = parse_oxcal(
            read_fixture('oxcal_delta_r', 'Coastal_midden.oxcal'),
            'Coastal_midden.oxcal',
        )

    def test_the_whole_model_imports(self):
        assert self.result.ok, self.result.errors
        assert len(self.result.dates) == 22
        assert [c['name'] for c in self.result.model_tree['children']] == [
            'Start L4', 'L4', 'Transition L4/P3', 'P3', 'P3cap',
            'Transition 2/3', 'L2', 'Transition L2/L2u', 'L2u',
            'End Occupation',
        ]

    def test_the_offset_reaches_the_marine_dates_and_only_those(self):
        by_curve = {'marine20': [], 'shcal20': []}
        for date in self.result.dates:
            by_curve[date['curve_id']].append(date)

        assert len(by_curve['marine20']) == 13
        assert len(by_curve['shcal20']) == 9
        assert all(
            (d['delta_r'], d['delta_r_error']) == (-162.0, 28.0)
            for d in by_curve['marine20']
        )
        assert all(d['delta_r'] is None for d in by_curve['shcal20'])

    def test_the_charcoal_outlier_model_survives_the_import(self):
        charcoal = [d for d in self.result.dates if d['outlier_model'] == 'charcoal']
        assert [d['sample_name'].strip() for d in charcoal] == ['LAB-1003']
        assert charcoal[0]['outlier_prior'] == 1.0


class TestTheDuneModelWithDisplayAttributes:
    """
    tests/fixtures/oxcal_display/Dune_layers.oxcal -- the structure of a real
    dune stratigraphy, with its lab codes and ages replaced. Every date in it
    carries a `color=` so that its layer comes out in its own colour on
    OxCal's graph, and the file closes with the Interval, Span and Difference
    queries that read the posterior back out.

    None of that shapes the model, and all of it used to take the whole file
    down: a `color="red";` parses to an assignment, the R_Date reader knew only
    `z=`, and every other assignment fell through to the catch-all refusal --
    twelve errors, no dates, for twelve lines that change nothing.

    The colours are dropped rather than stored, because this app has no colour
    of its own to put them in. What it must not do is lose the dates with them.
    """

    def setup_method(self):
        self.result = parse_oxcal(
            read_fixture('oxcal_display', 'Dune_layers.oxcal'),
            'Dune_layers.oxcal',
        )

    def test_the_whole_model_imports(self):
        assert self.result.ok, self.result.errors
        assert self.result.warnings == []
        assert len(self.result.dates) == 12

    def test_the_stratigraphy_survives_in_order(self):
        assert [c['name'] for c in self.result.model_tree['children']] == [
            'Start 11', 'Layer 11', 'End 11',
            'Start 9', 'Layer 9', 'End 9', 'Layer 8',
            'Start 7', 'Layer 7',
            'Layer 6 lower', 'Layer 6 upper', 'Layer 6',
            'Start 5', 'Layer 5',
            'Start 4', 'Layer 4',
            'End 3',
        ]

    def test_the_outlier_model_still_reaches_every_date(self):
        """
        The colour sits in the same block as the Outlier(), so a reader that
        stumbles on one could plausibly drop the other.
        """
        assert all(d['outlier_model'] == 'general' for d in self.result.dates)
        assert all(d['outlier_prior'] == 0.05 for d in self.result.dates)

    def test_the_import_says_it_passed_the_colours_over(self):
        """
        Silence is the failure mode here: a user who wrote colours and got a
        model back is entitled to know the colours are not in it.
        """
        assert any('color=' in note for note in self.result.notes)

    def test_a_colour_on_a_phase_is_passed_over_too(self):
        """OxCal takes color= on a container as well as on a date."""
        result = parse_oxcal(oxcal_model(
            'Phase("P"){ color="red"; R_Date("A",1000,25); }'
        ))

        assert result.ok, result.errors
        assert len(result.dates) == 1
        assert any('color=' in note for note in result.notes)

    def test_an_unrecognised_assignment_is_still_refused(self):
        """
        The point of reading color= is not that assignments are harmless. An
        unknown key could be setting a likelihood, and naming it in the error
        is the difference between a line the user can find and the old `=()`.
        """
        result = parse_oxcal(oxcal_model(
            'Phase("P"){ R_Date("A",1000,25){ resolution=5; }; }'
        ))

        assert not result.ok
        assert 'resolution=' in result.errors[0]
        assert 'R_Date("A")' in result.errors[0]

    def test_a_z_is_still_read_as_a_depth(self):
        """The one assignment that does shape a model, unchanged."""
        result = parse_oxcal(oxcal_model(
            'Phase("P"){ R_Date("A",1000,25){ color="red"; z=47; }; }'
        ))

        assert result.ok, result.errors
        assert result.dates[0]['depth_cm'] == 47.0


# --------------------------------------------------------------------------
# Refusals -- the half that keeps a wrong model out
# --------------------------------------------------------------------------

def oxcal_model(inner):
    return 'Plot(){ ' + inner + ' }'


class TestConstructsThatMustBeRefused:
    """
    OxCal's language is much larger than this app's model. Anything that would
    change the posterior and cannot be honoured has to fail the import -- not
    warn, and certainly not import as the nearest thing we do have.
    """

    @pytest.mark.parametrize('construct,fragment', [
        ('Tau_Boundary', 'Sequence(){ Tau_Boundary("t"); Phase("A"){ R_Date("a",3000,25); }; Boundary(); };'),
        ('Sigma_Boundary', 'Sequence(){ Sigma_Boundary("s"); Phase("A"){ R_Date("a",3000,25); }; Boundary(); };'),
        ('Zero_Boundary', 'Sequence(){ Zero_Boundary("z"); Phase("A"){ R_Date("a",3000,25); }; Boundary(); };'),
        ('C_Date', 'Phase("A"){ R_Date("a",3000,25); C_Date("h",1500,10); };'),
        # One date is no pool; see TestRCombine for what is imported.
        ('R_Combine', 'Phase("A"){ R_Combine("c"){ R_Date("a",3000,25); }; };'),
        ('U_Sequence', 'U_Sequence("u",1){ Boundary(); Phase("A"){ R_Date("a",3000,25); }; Boundary(); };'),
        ('After', 'Phase("A"){ After(R_Date("a",3000,25)); };'),
    ])
    def test_a_model_changing_construct_fails_the_import(self, construct, fragment):
        result = parse_oxcal(oxcal_model(fragment), 't.oxcal')

        assert not result.ok
        assert any(construct in e for e in result.errors), result.errors

    def test_an_unrecognised_command_fails_rather_than_being_skipped(self):
        """
        A command this importer has never heard of could be anything,
        including something structural. Guessing that it does not matter is
        the failure mode this whole module is arranged against.
        """
        result = parse_oxcal(oxcal_model(
            'Sequence(){ Boundary(); Wiggle_Match("w"); '
            'Phase("A"){ R_Date("a",3000,25); }; Boundary(); };'
        ), 't.oxcal')

        assert not result.ok
        assert any('Wiggle_Match' in e for e in result.errors)

    def test_an_unknown_curve_fails_rather_than_falling_back(self):
        result = parse_oxcal(oxcal_model(
            'Curve("IntCal13","intcal13.14c"); Phase("A"){ R_Date("a",3000,25); };'
        ), 't.oxcal')

        assert not result.ok
        assert any('IntCal13' in e for e in result.errors)

    def test_a_custom_outlier_model_cannot_be_applied_to_a_date(self):
        result = parse_oxcal(oxcal_model(
            'Outlier_Model("SSimple",N(0,10),0,"s"); '
            'Sequence(){ Boundary(); Phase("A"){ R_Date("a",3000,25)'
            '{ Outlier("SSimple",0.05); }; }; Boundary(); };'
        ), 't.oxcal')

        assert not result.ok
        assert any('SSimple' in e for e in result.errors)

    def test_a_refused_file_hands_back_nothing_at_all(self):
        """
        All-or-nothing. A refused result that still carried the rows it
        managed to read would invite exactly the partial import the rule
        exists to prevent.
        """
        result = parse_oxcal(oxcal_model(
            'Sequence(){ Tau_Boundary("t"); Phase("A"){ R_Date("a",3000,25); }; };'
        ), 't.oxcal')

        assert not result.ok
        assert result.dates == []
        assert result.model_tree is None
        assert result.element_assignments == {}

    def test_a_redefined_standard_outlier_model_warns(self):
        """
        Importing is right -- the model IS General -- but the parameters in
        the file are not the ones applied, and the scale range is not
        cosmetic: it is what puts a third of the charcoal prior below ten
        years.
        """
        result = parse_oxcal(oxcal_model(
            'Outlier_Model("General",T(3),U(0,2),"t"); '
            'Sequence(){ Boundary(); Phase("A"){ R_Date("a",3000,25)'
            '{ Outlier("General",0.05); }; }; Boundary(); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        assert any('cannot vary it' in w for w in result.warnings), result.warnings

    def test_dates_hidden_inside_a_plotting_command_are_reported(self):
        """
        Sum() summarises distributions rather than modelling them, so its
        dates are genuinely not part of the model -- but a quietly shorter
        date list is what nobody notices.
        """
        result = parse_oxcal(oxcal_model(
            'Sequence(){ Boundary(); Phase("A"){ R_Date("a",3000,25); }; Boundary(); }; '
            'Sum("s"){ R_Date("b",3100,25); R_Date("c",3200,25); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        assert len(result.dates) == 1
        assert any('Sum() contains 2 date(s)' in w for w in result.warnings), result.warnings


class TestDatesWithoutTheModel:
    """
    A refused model comes back with its dates read on their own, for the user
    to take or leave. Nothing about the refusal itself changes.
    """

    def test_a_refused_model_offers_its_dates_and_names_the_command(self):
        result = parse_oxcal(oxcal_model(
            'Sequence(){ Tau_Boundary("t"); Phase("A"){ R_Date("a",3000,25); '
            'After(R_Date("b",3100,25)); }; Boundary(); };'
        ), 't.oxcal')

        assert not result.ok
        assert result.dates == [] and result.model_tree is None
        assert result.blocking_commands == ['Tau_Boundary', 'After']
        offered = result.dates_only
        assert offered.ok and offered.model_tree is None
        assert [(d['sample_name'], d['phase_name']) for d in offered.dates] == [
            ('a', 'A'), ('b', 'A')]
        assert offered.suggested_analysis_type == 'group_calibration'

    def test_dates_hidden_in_a_sum_are_offered_naming_the_sum(self):
        result = parse_oxcal(oxcal_model(
            'Curve("Marine20","marine20.14c"); Delta_R("local",-162,28); '
            'Sum("S"){ R_Date("a",3000,25); R_Date("b",3100,25); };'
        ), 't.oxcal')

        assert not result.ok
        assert result.blocking_commands == ['Sum']
        a, b = result.dates_only.dates
        assert (a['curve_id'], a['delta_r'], a['phase_name']) == ('marine20', -162.0, 'S')

    def test_a_date_that_is_itself_wrong_offers_nothing(self):
        """Letting go of the model does not make a bad date good."""
        result = parse_oxcal(oxcal_model(
            'Phase("A"){ Tau_Boundary("t"); '
            'R_Date("a",3000,25){ Outlier("Mine",0.1); }; };'
        ), 't.oxcal')

        assert not result.ok
        assert result.dates_only is None

    def test_an_importable_model_offers_nothing(self):
        result = parse_oxcal(oxcal_model('Phase("A"){ R_Date("a",3000,25); };'), 't.oxcal')

        assert result.ok
        assert result.dates_only is None and result.as_dict()['dates_only'] is None


class TestTranslationChoices:
    """Where the file's shape and the app's differ, and what gets reported."""

    def test_dates_directly_in_a_sequence_keep_their_order(self):
        """
        A Sequence orders its children, so a date in one holds a position.
        This app attaches dates to Phases, so each becomes a Phase of one --
        in place, which preserves the ordering exactly.
        """
        result = parse_oxcal(oxcal_model(
            'Sequence("Core"){ Boundary("s"); R_Date("deep",3300,25); '
            'R_Date("mid",3100,25); R_Date("top",2900,25); Boundary("e"); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        assert [(c['element_type'], c['name']) for c in result.model_tree['children']] == [
            ('boundary', 's'),
            ('phase', 'deep'),
            ('phase', 'mid'),
            ('phase', 'top'),
            ('boundary', 'e'),
        ]
        assert any('Phase of its own' in n for n in result.notes)

    def test_a_date_between_two_elements_keeps_its_position(self):
        """The ordering a Sequence expresses is positional, so a loose date
        cannot be shuffled to either end of the children."""
        result = parse_oxcal(oxcal_model(
            'Sequence("S"){ R_Date("first",3300,25); Phase("Middle")'
            '{ R_Date("m",3100,25); }; R_Date("last",2900,25); };'
        ), 't.oxcal')

        assert [c['name'] for c in result.model_tree['children']] == [
            'first', 'Middle', 'last'
        ]

    def test_a_file_of_bare_dates_is_not_a_model(self):
        result = parse_oxcal('Plot(){ R_Date("a",3000,25); R_Date("b",3100,30); }', 't.oxcal')

        assert result.ok, result.errors
        assert len(result.dates) == 2
        assert result.model_tree is None
        assert result.suggested_analysis_type == 'group_calibration'

    def test_several_top_level_models_are_wrapped_unordered(self):
        result = parse_oxcal(oxcal_model(
            'Sequence("S1"){ Boundary(); Phase("A"){ R_Date("a",3000,25); }; Boundary(); }; '
            'Sequence("S2"){ Boundary(); Phase("B"){ R_Date("b",3100,25); }; Boundary(); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        assert result.model_tree['element_type'] == 'phase'
        assert [c['name'] for c in result.model_tree['children']] == ['S1', 'S2']
        assert any('no ordering between them' in n for n in result.notes)

    def test_resolution_is_carried_over_and_other_options_noted(self):
        result = parse_oxcal(oxcal_model(
            'Options(){ Resolution=1; kIterations=30; }; '
            'Sequence(){ Boundary(); Phase("A"){ R_Date("a",3000,25); }; Boundary(); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        assert result.resolution == 1
        assert result.as_dict()['resolution'] == 1
        assert any('Resolution=1 was carried over' in n for n in result.notes)
        assert any('kIterations' in n and 'not carried over' in n for n in result.notes)
        assert not any('Resolution' in n and 'not carried over' in n for n in result.notes)

    def test_an_unusable_resolution_is_noted_and_left_at_the_default(self):
        result = parse_oxcal(oxcal_model(
            'Options(){ Resolution=0.5; }; '
            'Sequence(){ Boundary(); Phase("A"){ R_Date("a",3000,25); }; Boundary(); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        assert result.resolution is None
        assert any('Resolution=0.5 was not carried over' in n for n in result.notes)

    @pytest.mark.parametrize('value', ['"inf"', '"-inf"', '"nan"'])
    def test_a_resolution_that_is_not_a_number_is_noted(self, value):
        result = parse_oxcal(oxcal_model(
            f'Options(){{ Resolution={value}; }}; '
            'Sequence(){ Boundary(); Phase("A"){ R_Date("a",3000,25); }; Boundary(); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        assert result.resolution is None
        assert any('was not carried over' in n and 'whole number' in n for n in result.notes)

    @pytest.mark.parametrize('text', ['R_Date(', 'Options(){ Resolution='])
    def test_a_file_cut_off_mid_value_is_a_syntax_error(self, text):
        result = parse_oxcal(text, 't.oxcal')

        assert not result.ok
        assert result.errors == ['The file ends where a value was expected.']

    def test_a_file_without_options_sets_no_resolution(self):
        result = parse_oxcal(oxcal_model(
            'Sequence(){ Boundary(); Phase("A"){ R_Date("a",3000,25); }; Boundary(); };'
        ), 't.oxcal')

        assert result.resolution is None

    def test_delta_r_applies_to_the_dates_in_its_scope(self):
        result = parse_oxcal(oxcal_model(
            'Curve("Marine20","marine20.14c"); Delta_R("local",-7,45); '
            'Sequence(){ Boundary(); Phase("Midden"){ R_Date("shell",1200,30); }; Boundary(); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        date = result.dates[0]
        assert date['curve_id'] == 'marine20'
        assert date['sample_type'] == 'marine'
        assert (date['delta_r'], date['delta_r_error']) == (-7.0, 45.0)

    def test_a_named_delta_r_is_applied_where_it_is_cited(self):
        """
        OxCal states an offset once, with a name, and cites it by that name
        wherever it applies: `Delta_R("=LocalMarine", )`. The citation
        carries no numbers -- they are in the declaration -- so reading it as
        an offset with no value refuses a file OxCal runs.
        """
        result = parse_oxcal(oxcal_model(
            'Curve("SHCal20","SHCal20.14c"); Curve("Marine20","Marine20.14c"); '
            'Delta_R("LocalMarine",-162,28); '
            'Phase("Midden"){ Curve("=Marine20"); Delta_R("=LocalMarine", ); '
            'R_Date("shell",900,20); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        date = result.dates[0]
        assert date['curve_id'] == 'marine20'
        assert (date['delta_r'], date['delta_r_error']) == (-162.0, 28.0)

    def test_an_offset_stays_with_the_curve_it_was_stated_for(self):
        """
        OxCal defines Delta_R as applying to the current Curve, so switching
        curve leaves it behind. This is the shape every coastal site has --
        marine and terrestrial dates in one phase -- and carrying the offset
        across would shift the terrestrial dates by the marine reservoir.
        """
        result = parse_oxcal(oxcal_model(
            'Curve("SHCal20","SHCal20.14c"); Curve("Marine20","Marine20.14c"); '
            'Delta_R("LocalMarine",-162,28); '
            'Phase("L4"){ '
            'Curve("=SHCal20"); R_Date("charcoal",473,19); '
            'Curve("=Marine20"); Delta_R("=LocalMarine", ); R_Date("shell",886,22); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        charcoal, shell = result.dates
        assert charcoal['curve_id'] == 'shcal20'
        assert (charcoal['delta_r'], charcoal['delta_r_error']) == (None, None)
        assert shell['curve_id'] == 'marine20'
        assert (shell['delta_r'], shell['delta_r_error']) == (-162.0, 28.0)

    def test_returning_to_a_curve_brings_its_offset_back(self):
        """
        The offset is a property of the curve, not of the point in the file
        where it was written, so it does not have to be restated after every
        excursion to another curve -- and dropping it there would understate
        the uncertainty of the dates that follow.
        """
        result = parse_oxcal(oxcal_model(
            'Curve("SHCal20","SHCal20.14c"); Curve("Marine20","Marine20.14c"); '
            'Delta_R("LocalMarine",-162,28); '
            'Phase("L4"){ R_Date("shell",886,22); '
            'Curve("=SHCal20"); R_Date("charcoal",473,19); '
            'Curve("=Marine20"); R_Date("shell2",900,20); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        shell, charcoal, shell2 = result.dates
        assert (shell['delta_r'], shell['delta_r_error']) == (-162.0, 28.0)
        assert charcoal['delta_r'] is None
        assert (shell2['delta_r'], shell2['delta_r_error']) == (-162.0, 28.0)

    def test_an_offset_stated_inside_a_phase_stays_there(self):
        """
        Curve and Delta_R are scoped to their block, so one phase's local
        offset must not reach the next phase's dates on the same curve.
        """
        result = parse_oxcal(oxcal_model(
            'Curve("Marine20","Marine20.14c"); '
            'Phase("A"){ Delta_R("Bay",-162,28); R_Date("a",900,20); }; '
            'Phase("B"){ R_Date("b",900,20); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        inside, outside = result.dates
        assert (inside['delta_r'], inside['delta_r_error']) == (-162.0, 28.0)
        assert outside['delta_r'] is None

    def test_which_dates_a_named_offset_reached_is_reported(self):
        result = parse_oxcal(oxcal_model(
            'Curve("Marine20","Marine20.14c"); Delta_R("LocalMarine",-162,28); '
            'Phase("M"){ R_Date("a",900,20); R_Date("b",910,20); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        assert any('LocalMarine' in n and '2 date(s)' in n for n in result.notes)

    def test_citing_an_offset_the_file_never_states_is_refused(self):
        """
        The alternative is importing those dates with no offset at all, which
        calibrates them against the global marine curve and looks like a
        successful import.
        """
        result = parse_oxcal(oxcal_model(
            'Curve("Marine20","Marine20.14c"); '
            'Phase("M"){ Delta_R("=LocalMarine", ); R_Date("shell",900,20); };'
        ), 't.oxcal')

        assert not result.ok
        assert any('LocalMarine' in e for e in result.errors)

    def test_delta_r_without_its_error_is_refused(self):
        """
        The two are combined in quadrature, so an offset alone would be
        treated as exactly known and report a date more precise than the
        evidence supports -- the same rule MeasurementInput enforces.
        """
        result = parse_oxcal(oxcal_model(
            'Curve("Marine20","marine20.14c"); Delta_R("local",-7); '
            'Phase("M"){ R_Date("shell",1200,30); };'
        ), 't.oxcal')

        assert not result.ok
        assert any('uncertainty' in e for e in result.errors)

    def test_a_boundary_carrying_a_prior_says_so(self):
        """
        A named boundary can carry one too, so this cannot key on the label
        being absent -- dropping the prior silently would narrow the
        transition without saying so.
        """
        result = parse_oxcal(oxcal_model(
            'Sequence(){ Boundary("Start", U(1000,1200)); '
            'Phase("A"){ R_Date("a",3000,25); }; Boundary(); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        assert any('carries a prior' in w for w in result.warnings), result.warnings

    def test_more_dates_than_the_endpoint_accepts_is_refused(self):
        dates = ' '.join(
            f'R_Date("d{i}",3000,25);' for i in range(date_import.MAX_DATES + 1)
        )
        result = parse_oxcal(oxcal_model(
            'Sequence(){ Boundary(); Phase("A"){ ' + dates + ' }; Boundary(); };'
        ), 't.oxcal')

        assert not result.ok
        assert str(date_import.MAX_DATES) in result.errors[0]

    def test_a_simulated_date_takes_the_curves_value_at_its_year(self):
        """
        R_Simulate states a calendar year, not a measurement. The measurement
        is the curve's own radiocarbon age there -- without OxCal's random
        scatter, so the import is the same every time -- and a note says so.
        """
        result = parse_oxcal(oxcal_model(
            'Sequence(){ Boundary(); Phase("A"){ R_Simulate("s",-1000,25); '
            'R_Date("a",2900,25); }; Boundary(); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        simulated = next(d for d in result.dates if d['sample_name'] == 's')
        c14_age, _ = curve_registry.resolve('intcal20').interpolate(2950.0)
        assert simulated['age_bp'] == pytest.approx(float(c14_age), abs=0.1)
        assert simulated['error_1sigma'] == 25.0
        assert 'R_Simulate' in simulated['notes']
        assert any('R_Simulate' in n for n in result.notes), result.notes

    def test_a_simulated_marine_date_carries_its_offset(self):
        """A marine sample of calendar age t measures curve(t) + delta_r."""
        result = parse_oxcal(oxcal_model(
            'Curve("Marine20","marine20.14c"); Delta_R("local",-162,28); '
            'Phase("M"){ R_Simulate("shell",-1000,20); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        shell, = result.dates
        c14_age, _ = curve_registry.resolve('marine20').interpolate(2950.0)
        assert shell['age_bp'] == pytest.approx(float(c14_age) - 162, abs=0.1)
        assert (shell['delta_r'], shell['delta_r_error']) == (-162.0, 28.0)

    def test_a_simulated_date_off_the_end_of_its_curve_is_refused(self):
        result = parse_oxcal(oxcal_model(
            'Phase("A"){ R_Simulate("s",-80000,25); R_Date("a",2900,25); };'
        ), 't.oxcal')

        assert not result.ok
        assert any('outside intcal20' in e for e in result.errors), result.errors

    def test_comments_are_ignored(self):
        result = parse_oxcal(
            '// a leading comment\n'
            '/* and a block\n   one */\n'
            + oxcal_model('Phase("A"){ R_Date("a",3000,25); };'),
            't.oxcal',
        )
        assert result.ok, result.errors
        assert len(result.dates) == 1


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

class TestCsvColumns:
    def test_the_minimum_is_name_age_error(self):
        result = parse_csv('sample_name,age_bp,error_1sigma\nOxA-1,3000,25\n')

        assert result.ok, result.errors
        assert result.dates[0]['sample_name'] == 'OxA-1'
        assert result.dates[0]['age_bp'] == 3000
        assert result.dates[0]['curve_id'] == 'intcal20'

    def test_columns_can_be_in_any_order(self):
        result = parse_csv('error_1sigma,sample_name,age_bp\n25,OxA-1,3000\n')

        assert result.ok, result.errors
        assert (result.dates[0]['age_bp'], result.dates[0]['error_1sigma']) == (3000, 25)

    @pytest.mark.parametrize('header', [
        'Lab Code,Age BP,1-Sigma Error',
        'name,age,sd',
        'Sample,C14 age,uncertainty',
        'LabCode,BP,± 1σ',
    ])
    def test_the_spellings_a_lab_report_actually_uses(self, header):
        result = parse_csv(header + '\nOxA-1,3000,25\n')

        assert result.ok, result.errors
        assert (result.dates[0]['sample_name'], result.dates[0]['age_bp']) == ('OxA-1', 3000)

    def test_a_missing_required_column_names_what_was_found(self):
        result = parse_csv('name,age\nOxA-1,3000\n')

        assert not result.ok
        assert 'error_1sigma' in result.errors[0]
        assert "'name'" in result.errors[0]

    def test_an_unrecognised_column_warns_rather_than_passing_unnoticed(self):
        """A misspelled 'eror' column would otherwise look like a file with
        no errors in it, and every date would import with the wrong sigma."""
        result = parse_csv('name,age,error,eror\nOxA-1,3000,25,99\n')

        assert result.ok, result.errors
        assert any('eror' in w for w in result.warnings)

    def test_two_columns_meaning_the_same_thing_is_an_error(self):
        result = parse_csv('name,sample_name,age,error\nA,B,3000,25\n')

        assert not result.ok
        assert any('both mean' in e for e in result.errors)

    @pytest.mark.parametrize('delimiter', [',', '\t', ';'])
    def test_comma_tab_and_semicolon(self, delimiter):
        header = delimiter.join(['name', 'age', 'error'])
        row = delimiter.join(['OxA-1', '3000', '25'])
        result = parse_csv(f'{header}\n{row}\n')

        assert result.ok, result.errors
        assert result.dates[0]['age_bp'] == 3000

    def test_a_byte_order_mark_does_not_break_the_first_header(self):
        result = parse_upload('x.csv', '﻿name,age,error\nOxA-1,3000,25\n'.encode('utf-8'))

        assert result.ok, result.errors

    def test_blank_lines_are_skipped(self):
        result = parse_csv('name,age,error\nOxA-1,3000,25\n\n\nOxA-2,3100,30\n')

        assert result.ok, result.errors
        assert len(result.dates) == 2

    @pytest.mark.parametrize('newline', ['\r\n', '\r'])
    def test_windows_and_old_mac_line_endings(self, newline):
        """A lone CR is how an old Mac spreadsheet ends its lines; it is read
        as a line break, not handed to the csv reader to raise on."""
        text = newline.join(['name,age,error', 'OxA-1,3000,25', 'OxA-2,3100,30', ''])
        result = parse_csv(text)

        assert result.ok, result.errors
        assert [d['age_bp'] for d in result.dates] == [3000, 3100]

    def test_a_quote_that_never_closes_is_refused_not_raised(self):
        """An unclosed quote swallows the rest of the file into one cell, and
        past the csv reader's field limit that is an error to report -- naming
        where reading stopped and the likely cause -- not a 500."""
        text = 'name,age,error,notes\nOxA-1,3000,25,"see below\nOxA-2,3100,30,' + 'x' * 131072 + '\n'
        result = parse_csv(text)

        assert not result.ok
        assert result.dates == []
        assert result.errors[0].startswith('Reading stopped at line 3. A single cell runs past 131,072')
        assert 'never closes' in result.errors[0]


class TestCsvValues:
    def test_every_bad_row_is_reported_not_just_the_first(self):
        result = parse_csv(
            'name,age,error\n'
            'OxA-1,three thousand,25\n'
            'OxA-2,3100,\n'
            'OxA-3,3200,30\n'
        )

        assert not result.ok
        assert len(result.errors) == 2
        assert any('Row 2' in e for e in result.errors)
        assert any('Row 3' in e for e in result.errors)

    def test_nothing_is_imported_when_a_row_fails(self):
        result = parse_csv('name,age,error\nOxA-1,3000,25\nOxA-2,oops,30\n')

        assert not result.ok
        assert result.dates == []

    def test_spreadsheet_debris_in_a_number_is_tolerated(self):
        """Thousands separators and a stray ± are formatting, not data."""
        result = parse_csv('name,age,error\nOxA-1,"3,000",± 25\n')

        assert result.ok, result.errors
        assert (result.dates[0]['age_bp'], result.dates[0]['error_1sigma']) == (3000, 25)

    def test_an_unknown_curve_is_refused(self):
        result = parse_csv('name,age,error,curve\nOxA-1,3000,25,intcal13\n')

        assert not result.ok
        assert any('intcal13' in e for e in result.errors)

    @pytest.mark.parametrize('written,expected', [
        ('intcal20', 'intcal20'), ('IntCal20', 'intcal20'), ('SHCal20', 'shcal20'),
        ('Marine20', 'marine20'), ('marine', 'marine20'),
    ])
    def test_curve_spellings(self, written, expected):
        result = parse_csv(f'name,age,error,curve\nOxA-1,3000,25,{written}\n')

        assert result.ok, result.errors
        assert result.dates[0]['curve_id'] == expected

    def test_the_measurement_schema_is_enforced_on_uploads_too(self):
        """
        An uploaded date is held to the same bounds a typed one is. A
        negative error is not a measurement uncertainty however it arrived.
        """
        result = parse_csv('name,age,error\nOxA-1,3000,-25\n')

        assert not result.ok
        assert any('error_1sigma' in e for e in result.errors)

    def test_a_reservoir_offset_needs_its_uncertainty(self):
        result = parse_csv('name,age,error,curve,delta_r\nS1,1200,30,marine20,-7\n')

        assert not result.ok
        assert any('delta_r_error' in e for e in result.errors)

    def test_an_outlier_prior_can_be_written_as_a_percentage(self):
        result = parse_csv(
            'name,age,error,outlier_model,outlier_prior\n'
            'A,3000,25,charcoal,100%\n'
            'B,3100,25,general,0.05\n'
        )

        assert result.ok, result.errors
        assert result.dates[0]['outlier_prior'] == 1.0
        assert result.dates[0]['outlier_model'] == 'charcoal'
        assert result.dates[1]['outlier_prior'] == 0.05

    def test_a_repeated_sample_name_warns_but_imports(self):
        """A site really can have two dates from one sample -- but this is
        also the shape of a copy-paste accident, and the two look alike."""
        result = parse_csv('name,age,error\nOxA-1,3000,25\nOxA-1,3100,30\n')

        assert result.ok, result.errors
        assert len(result.dates) == 2
        assert any('more than once' in w for w in result.warnings)

    def test_an_empty_file_is_refused(self):
        assert not parse_csv('').ok
        assert not parse_csv('name,age,error\n').ok


class TestCsvStructure:
    def test_a_phase_column_builds_an_unordered_starting_model(self):
        result = parse_csv(
            'name,age,error,phase\n'
            'A,3000,25,Pit 12\nB,3050,25,Pit 12\nC,2900,25,Layer 3\n'
        )

        assert result.ok, result.errors
        assert shape(result.model_tree) == {
            'type': 'sequence', 'name': 'Imported model', 'children': [
                {'type': 'boundary', 'name': 'Start', 'children': []},
                {'type': 'phase', 'name': 'Contexts', 'children': [
                    {'type': 'phase', 'name': 'Pit 12', 'children': []},
                    {'type': 'phase', 'name': 'Layer 3', 'children': []},
                ]},
                {'type': 'boundary', 'name': 'End', 'children': []},
            ],
        }
        assert result.suggested_analysis_type == 'bayesian'
        assert any('UNORDERED' in n for n in result.notes)

    def test_no_phase_column_means_no_model(self):
        result = parse_csv('name,age,error\nA,3000,25\nB,3100,25\n')

        assert result.ok, result.errors
        assert result.model_tree is None
        assert result.suggested_analysis_type == 'group_calibration'

    def test_dates_with_a_blank_phase_are_not_dropped(self):
        result = parse_csv('name,age,error,phase\nA,3000,25,Pit 12\nB,3050,25,\n')

        assert result.ok, result.errors
        assert len(result.dates) == 2
        assert phase_of(result) == {'A': 'Pit 12', 'B': 'Unassigned dates'}
        assert any('Unassigned dates' in w for w in result.warnings)

    def test_a_context_really_called_unassigned_dates_does_not_collide(self):
        result = parse_csv(
            'name,age,error,phase\nA,3000,25,Unassigned dates\nB,3050,25,\n'
        )

        assert result.ok, result.errors
        contexts = result.model_tree['children'][1]['children']
        assert len(contexts) == 2
        assert len({c['id'] for c in contexts}) == 2


class TestARealLabTable:
    """
    tests/fixtures/oxcal/Hillside_c14.csv is a real input table for the
    Hillside models -- headed 'R_Date,BP,Error,Material,Phase', which is what
    a table written to be fed to OxCal actually looks like. It found a gap:
    'R_Date' is OxCal's own name for the lab-code column and was not among the
    spellings understood.
    """

    def test_it_imports_without_being_edited_first(self):
        result = parse_csv(read_fixture('oxcal', 'Hillside_c14.csv'), 'Hillside_c14.csv')

        assert result.ok, result.errors
        assert result.warnings == []
        assert len(result.dates) == 8

    def test_it_agrees_with_the_dates_the_oxcal_model_carries(self):
        """The .csv and the .oxcal are two views of one set of measurements,
        so importing either must give the same eight dates."""
        from_csv = parse_csv(read_fixture('oxcal', 'Hillside_c14.csv'), 'x.csv')
        from_model = parse_oxcal(
            read_fixture('oxcal', 'Hillside_sites_3.oxcal'), 'x.oxcal'
        )

        def summarise(result):
            return {
                ox.key(d['sample_name']): (d['age_bp'], d['error_1sigma'], d['phase_name'])
                for d in result.dates
            }

        assert summarise(from_csv) == summarise(from_model)


class TestCsvCoordinates:
    """
    Where a date came from, read in WGS84 decimal degrees and in nothing else.

    The refusal is the point of this class. Degrees-and-minutes is the one bad
    cell nothing downstream catches: read 54°30' as 54.30 and the result is in
    range, plots on the map, and is 55 km from the site. So the file fails,
    naming the cell and the format wanted, rather than the importer reading a
    construct as the nearest thing it implements.
    """

    def test_a_coordinate_is_read(self):
        result = parse_csv(
            'name,age,error,latitude,longitude\nOxA-1,3000,25,54.5,-5.6\n')

        assert result.ok, result.errors
        assert (result.dates[0]['latitude'], result.dates[0]['longitude']) == (54.5, -5.6)

    @pytest.mark.parametrize("header", [
        'name,age,error,latitude,longitude',
        'name,age,error,lat,lon',
        'name,age,error,Lat,Long',
        'name,age,error,decimal_latitude,decimal_longitude',
        'name,age,error,lat_dd,lon_dd',
        'name,age,error,y,x',
    ])
    def test_the_spellings_a_site_gazetteer_actually_uses(self, header):
        result = parse_csv(f'{header}\nOxA-1,3000,25,54.5,-5.6\n')

        assert result.ok, result.errors
        assert result.dates[0]['latitude'] == 54.5

    def test_the_columns_can_be_in_any_order(self):
        result = parse_csv(
            'longitude,name,latitude,error,age\n-5.6,OxA-1,54.5,25,3000\n')

        assert result.ok, result.errors
        assert (result.dates[0]['latitude'], result.dates[0]['longitude']) == (54.5, -5.6)

    @pytest.mark.parametrize("cell", [
        "54°30'N",
        "54 30 00 N",
        "N54.5",
        "54 deg 30 min",
        '54°30\'15"',
        "54 30.5",
    ])
    def test_degrees_and_minutes_fails_the_file(self, cell):
        result = parse_csv(
            f'name,age,error,latitude,longitude\nOxA-1,3000,25,"{cell}",-5.6\n')

        assert not result.ok, f"{cell!r} was parsed rather than refused"
        assert any(cell in e for e in result.errors), (
            "the error must show the offending cell")
        assert any('decimal degrees' in e for e in result.errors), (
            "the error must name the format wanted")

    def test_a_refused_coordinate_imports_nothing_at_all(self):
        """An import is all-or-nothing: a file with one bad coordinate must
        not hand back the rows it managed to read."""
        result = parse_csv(
            'name,age,error,latitude,longitude\n'
            'OxA-1,3000,25,54.5,-5.6\n'
            "OxA-2,3100,30,54°30'N,-5.6\n"
        ).refuse()

        assert not result.ok
        assert result.dates == []

    def test_a_decimal_comma_is_refused_rather_than_read_as_thousands(self):
        """
        _number strips thousands separators, so '5,6' would arrive as 56 --
        in range for a longitude, fifty degrees out, and silent. The same
        class of error as the DMS one, and refused the same way.
        """
        result = parse_csv(
            'name,age,error,latitude,longitude\nOxA-1,3000,25,"54,5","-5,6"\n')

        assert not result.ok
        assert any('comma' in e for e in result.errors)

    def test_half_a_coordinate_in_a_file_is_refused(self):
        result = parse_csv('name,age,error,latitude\nOxA-1,3000,25,54.5\n')

        assert not result.ok
        assert any('longitude' in e for e in result.errors)

    def test_no_coordinate_columns_at_all_is_normal(self):
        result = parse_csv('name,age,error\nOxA-1,3000,25\n')

        assert result.ok, result.errors
        assert result.dates[0]['latitude'] is None

    def test_null_island_warns_but_imports(self):
        """
        (0, 0) is a real position in the Gulf of Guinea and someone has to be
        able to say so -- but it is far more often what a spreadsheet leaves
        when a column is filled down over empty cells, so it is a warning
        rather than a refusal.
        """
        result = parse_csv(
            'name,age,error,latitude,longitude\n'
            'OxA-1,3000,25,0,0\n'
            'OxA-2,3100,30,54.5,-5.6\n'
        )

        assert result.ok, result.errors
        assert len(result.dates) == 2
        assert any('Gulf of Guinea' in w for w in result.warnings)
        assert any('OxA-1' in w for w in result.warnings)
        assert not any('OxA-2' in w for w in result.warnings)

    def test_a_position_off_the_earth_is_refused_by_the_shared_schema(self):
        """Row-level parsing lets it through; schemas/sample.py's bounds are
        what stop it, which is the same check a typed date meets."""
        result = parse_csv(
            'name,age,error,latitude,longitude\nOxA-1,3000,25,95,-5.6\n')

        assert not result.ok

    def test_the_template_carries_a_readable_coordinate(self):
        """The marine row is the one a reader copies when they have a placed
        date, so it has to demonstrate the accepted format."""
        result = parse_csv(template_csv())

        assert result.ok, result.errors
        placed = [d for d in result.dates if d['latitude'] is not None]
        assert len(placed) == 1
        assert placed[0]['sample_name'] == 'LB-10002'
        assert placed[0]['longitude'] == 170.5036


class TestReImportingOurOwnExports:
    """
    Export a results table, correct it in a spreadsheet, import it back. A
    path that nearly works is worse than one that doesn't, so it is tested.
    """

    def test_the_template_round_trips(self):
        result = parse_csv(template_csv(), 'radical-dates-template.csv')

        assert result.ok, result.errors
        assert len(result.dates) == len(date_import.TEMPLATE_ROWS)

    def test_structural_rows_are_skipped_and_the_path_gives_the_phase(self):
        """
        The results export has a row per model element as well as per date,
        and writes a date's position as a full path.
        """
        text = (
            'Order,Row Type,Structure Path,Name,Age BP,1-Sigma Error,Curve,Posterior Mean\n'
            '1,sequence,Hillside,Hillside,,,,\n'
            '2,phase,Hillside > North,North,,,,\n'
            '3,date,Hillside > North,LD10012,685,25,shcal20,650.5\n'
            '4,date,Hillside > North,LD10009,671,21,shcal20,640.1\n'
        )
        result = parse_csv(text)

        assert result.ok, result.errors
        assert len(result.dates) == 2
        assert phase_of(result) == {'LD10012': 'North', 'LD10009': 'North'}
        assert any('structural row' in n for n in result.notes)

    def test_posterior_columns_are_not_mistaken_for_input(self):
        """They are output. Recognised so they do not raise an 'unrecognised
        column' warning, and never read back in as data."""
        result = parse_csv(
            'Name,Age BP,1-Sigma Error,Posterior Mean,95.4% HPD,Agreement %\n'
            'OxA-1,3000,25,3120.4,3100-3140,98.2\n'
        )

        assert result.ok, result.errors
        assert result.warnings == []
        assert 'posterior_mean' not in result.dates[0]

    def test_an_hpd_column_from_an_older_export_is_still_recognised(self):
        """
        The HPD columns were headed '68% HPD' / '95% HPD' until the reporting
        levels were corrected to the exact sigma coverages; they now say
        '68.3%' / '95.4%'. A file exported before that has to keep importing,
        or re-importing an older results table would read its posterior back in
        as data.
        """
        for header in ('68% HPD', '68.3% HPD', '95% HPD', '95.4% HPD'):
            result = parse_csv(
                f'Name,Age BP,1-Sigma Error,{header}\n'
                'OxA-1,3000,25,3100-3140\n'
            )
            assert result.ok, (header, result.errors)
            assert result.warnings == [], (header, result.warnings)

    def test_the_era_formatted_duplicate_columns_are_ignored(self):
        """
        Every calendar column is exported again in the chosen era. Reading
        'TPQ BP (BC/AD)' back would both double-count and be wrong by 1950 --
        the duplicate is in AD where tpq_age_bp is in cal BP.
        """
        result = parse_csv(
            'Name,Age BP,1-Sigma Error,TPQ BP,TPQ BP (BC/AD),Posterior Mean (BCE/CE)\n'
            'OxA-1,3000,25,3500,-1550,1100\n'
        )

        assert result.ok, result.errors
        assert result.warnings == []
        assert result.dates[0]['tpq_age_bp'] == 3500

    def test_depth_means_centimetres_in_a_lab_report(self):
        result = parse_csv('name,age,error,depth\nOxA-1,3000,25,45\n')

        assert result.ok, result.errors
        assert result.dates[0]['depth_cm'] == 45

    def test_depth_means_tree_nesting_in_our_own_export(self):
        """
        Same header, two meanings, told apart by the Row Type column only our
        own export writes. Reading a nesting depth as centimetres would put
        silent nonsense in the record.
        """
        result = parse_csv(
            'Row Type,Depth,Name,Age BP,1-Sigma Error\n'
            'date,3,OxA-1,3000,25\n'
        )

        assert result.ok, result.errors
        assert result.warnings == []
        assert result.dates[0]['depth_cm'] is None


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

class TestFormatDetection:
    def test_an_oxcal_extension_goes_to_the_oxcal_reader(self):
        result = parse_upload('model.oxcal', b'Plot(){ R_Date("a",3000,25); }')

        assert result.source_format == 'oxcal'

    def test_a_model_saved_as_txt_is_still_read_as_a_model(self):
        """`.txt` is an ordinary name for both an exported table and a saved
        OxCal model, so the content decides."""
        result = parse_upload('model.txt', b'Plot(){ R_Date("a",3000,25); }')

        assert result.source_format == 'oxcal'
        assert result.ok, result.errors

    def test_a_csv_with_a_phase_column_is_not_mistaken_for_a_model(self):
        result = parse_upload('dates.csv', b'name,age,error,phase\nA,3000,25,Sequence\n')

        assert result.source_format == 'csv'
        assert result.ok, result.errors

    def test_a_csv_extension_is_taken_at_its_word(self):
        """
        A notes cell can say anything. Sniffing a .csv would send a perfectly
        good table to the model parser over a stray 'Sequence(' in prose, and
        it would come back refused as broken CQL2 -- a confusing failure with
        no obvious cause.
        """
        result = parse_upload(
            'dates.csv',
            b'name,age,error,notes\nA,3000,25,"see Sequence(3) and Phase(2) in the report"\n',
        )

        assert result.source_format == 'csv'
        assert result.ok, result.errors

    def test_a_file_that_is_neither_fails_clearly(self):
        result = parse_upload('notes.txt', b'Just some prose about the site.\n')

        assert not result.ok

    def test_windows_encoded_text_is_read(self):
        result = parse_upload('x.csv', 'name,age,error,notes\nA,3000,25,café\n'.encode('cp1252'))

        assert result.ok, result.errors
        assert result.dates[0]['notes'] == 'café'

    def test_an_oversized_file_is_refused_before_parsing(self):
        result = parse_upload('big.csv', b'x' * (date_import.MAX_CONTENT_BYTES + 1))

        assert not result.ok
        assert 'limit' in result.errors[0]

    def test_more_dates_than_the_collection_endpoint_accepts_is_refused(self):
        rows = '\n'.join(f'S{i},3000,25' for i in range(date_import.MAX_DATES + 1))
        result = parse_csv('name,age,error\n' + rows + '\n')

        assert not result.ok
        assert str(date_import.MAX_DATES) in result.errors[0]


# --------------------------------------------------------------------------
# The endpoints
# --------------------------------------------------------------------------

class TestImportApi:
    def _upload(self, client, name, content):
        return client.post(
            '/api/import/dates',
            files={'file': (name, io.BytesIO(content), 'text/plain')},
        )

    def test_uploading_an_oxcal_model(self, client):
        content = read_fixture('oxcal', 'Hillside_sites_3.oxcal').encode()
        response = self._upload(client, 'Hillside_sites_3.oxcal', content)

        assert response.status_code == 200, response.text
        data = response.json()['data']
        assert data['ok'] is True
        assert data['date_count'] == 8
        assert data['source_format'] == 'oxcal'
        assert data['suggested_analysis_type'] == 'bayesian'
        assert shape(data['model_tree']) == shape(ox.model_tree('ordered'))

    def test_uploading_a_csv(self, client):
        response = self._upload(client, 'dates.csv', b'name,age,error\nOxA-1,3000,25\n')

        assert response.status_code == 200, response.text
        data = response.json()['data']
        assert data['ok'] is True
        assert data['dates'][0]['sample_name'] == 'OxA-1'

    def test_a_bad_file_is_a_200_carrying_its_problems(self, client):
        """
        The request succeeded; the answer is what is wrong with the file.
        Reserving the error status for actual request failures keeps 'the
        upload broke' distinguishable from 'row 2 has a letter in the age'.

        Uses the shared `client` rather than building a bare TestClient, which
        is what it did before the endpoints required an account -- a bare one
        now answers 401 and never reaches the parser this is about.
        """
        response = self._upload(client, 'dates.csv', b'name,age,error\nA,oops,25\n')

        assert response.status_code == 200
        data = response.json()['data']
        assert data['ok'] is False
        assert data['dates'] == []
        assert data['errors']

    def test_an_empty_upload_is_a_400(self, client):
        response = self._upload(client, 'empty.csv', b'')

        assert response.status_code == 400

    def test_the_template_downloads_and_parses(self, client):
        response = client.get('/api/import/csv-template')

        assert response.status_code == 200
        assert 'attachment' in response.headers['content-disposition']
        assert parse_csv(response.text).ok

    def test_the_documented_columns_are_the_ones_the_parser_reads(self, client):
        """
        The dialog's format help is served from the parser's own alias table,
        so a documented column the importer does not actually read cannot
        exist.
        """
        response = client.get('/api/import/csv-columns')

        assert response.status_code == 200
        data = response.json()['data']
        names = {c['name'] for c in data['columns']}
        assert set(data['required']) <= names
        for column in data['columns']:
            assert column['name'] in date_import.COLUMN_ALIASES


class TestImportedDatesRunAsAnAnalysis:
    """
    The end of the road: what the importer produces has to be something the
    Bayesian endpoint accepts and can actually solve. Parsing into a shape the
    solver then rejects would be a working importer and a broken feature.
    """

    def test_an_imported_oxcal_model_runs(self, client):
        result = parse_oxcal(
            read_fixture('oxcal', 'Hillside_sites_3.oxcal'), 'Hillside_sites_3.oxcal'
        )
        assert result.ok, result.errors

        response = client.post('/api/analysis/bayesian?wait=true', json={
            'name': 'Imported Hillside',
            'measurements': [
                {k: v for k, v in d.items() if k != 'phase_name'} for d in result.dates
            ],
            'model_tree': result.model_tree,
            # Sampled, the model not being a chain; untempered, since what
            # is tested is that the import runs at all.
            'bayesian_config': {'mcmc_temperatures': 1},
        })

        assert response.status_code == 200, response.text
        data = response.json()['data']
        assert data['status'] == 'completed'
        assert len(data['results']) == 8

    def test_an_imported_csv_with_phases_runs(self, client):
        result = parse_csv(
            'name,age,error,curve,phase\n'
            'A,3000,25,shcal20,Pit 12\n'
            'B,3050,25,shcal20,Pit 12\n'
            'C,2900,25,shcal20,Layer 3\n',
            'site.csv',
        )
        assert result.ok, result.errors

        response = client.post('/api/analysis/bayesian?wait=true', json={
            'name': 'Imported CSV',
            'measurements': [
                {k: v for k, v in d.items() if k != 'phase_name'} for d in result.dates
            ],
            'model_tree': result.model_tree,
        })

        assert response.status_code == 200, response.text
        assert response.json()['data']['status'] == 'completed'

    def test_imported_dates_go_into_a_project_collection(self, client):
        """The collection path: same dates, through the endpoint hand-entered
        ones already use."""
        project_id = client.post('/api/projects', json={'name': 'Import Site'}).json()['data']['id']
        result = parse_csv('name,age,error,phase\nIMP-1,3000,25,Pit 9\n')

        response = client.post(f'/api/projects/{project_id}/samples', json={
            'samples': [{
                'sample_name': d['sample_name'],
                'age_bp': d['age_bp'],
                'error_1sigma': d['error_1sigma'],
                'material_type': d['material_type'],
                'curve_id': d['curve_id'],
                'default_context_name': d['phase_name'],
            } for d in result.dates],
        })

        assert response.status_code == 200, response.text
        items = response.json()['data']['items']
        assert items[0]['sample_name'] == 'IMP-1'
        assert items[0]['default_context_name'] == 'Pit 9'


class TestWiggleMatchedSequences:
    """
    D_Sequence and V_Sequence -- a series whose members are separated by
    measured gaps rather than by stratigraphy (services/wiggle_match.py).

    The whole risk here is the ORDINATE. OxCal writes the series oldest first
    with a Gap() between adjacent members; this app records each member's
    distance from a reference, 0 at the reference and increasing INTO THE
    PAST. Getting that conversion backwards produces a tight answer in the
    wrong place, so it is asserted member by member rather than in aggregate.
    """

    def test_a_d_sequence_becomes_a_wiggle_match_with_offsets_from_the_youngest(self):
        result = parse_oxcal(
            read_fixture('oxcal_wiggle', 'Timber_D_Sequence.oxcal'),
            'Timber_D_Sequence.oxcal')
        assert result.ok, result.errors

        tree = result.model_tree
        assert tree['element_type'] == 'wiggle_sequence'
        assert tree['name'] == 'Timber 1'
        assert len(result.dates) == 8

        by_name = {d['sample_name']: d for d in result.dates}
        # OAK-008 is the last date in the file, so it is the youngest, the
        # reference, and offset zero. OAK-001 is 250 rings older.
        assert by_name['OAK-008']['ring_offset'] == 0.0
        assert by_name['OAK-001']['ring_offset'] == 250.0
        assert by_name['OAK-002']['ring_offset'] == 214.0
        assert tree['params']['reference_sample_id'] == by_name['OAK-008']['id']
        # Exact gaps: no uncertainty is invented for a D_Sequence.
        assert all(d['ring_offset_error'] is None for d in result.dates)

    def test_the_imported_series_recovers_the_age_it_was_built_at(self):
        """
        The fixture's ages were read off IntCal20 for an outermost ring in
        2500 cal BP, so the import is checked all the way through the solver
        rather than only as a tree -- which is what would catch a sign error
        surviving into the offsets.
        """
        from app.services import wiggle_match
        from app.services.model_tree import plan_model

        result = parse_oxcal(
            read_fixture('oxcal_wiggle', 'Timber_D_Sequence.oxcal'),
            'Timber_D_Sequence.oxcal')
        assert result.ok, result.errors

        plan = plan_model(result.model_tree, result.dates)
        solved = wiggle_match.solve(result.dates, plan.wiggle_groups[0],
                                    root=result.model_tree)
        assert abs(solved['reference']['posterior_median'] - 2500.0) < 15.0

    def test_a_v_sequence_accumulates_its_gap_errors_and_says_it_did(self):
        result = parse_oxcal(
            read_fixture('oxcal_wiggle', 'Varves_V_Sequence.oxcal'),
            'Varves_V_Sequence.oxcal')
        assert result.ok, result.errors

        by_name = {d['sample_name']: d for d in result.dates}
        assert by_name['VAR-003']['ring_offset'] == 0.0
        assert by_name['VAR-002']['ring_offset'] == 130.0
        assert by_name['VAR-001']['ring_offset'] == 250.0
        # The reference has no distance and so no uncertainty; VAR-002 is one
        # gap away and VAR-001 two, in quadrature.
        assert by_name['VAR-003']['ring_offset_error'] is None
        assert by_name['VAR-002']['ring_offset_error'] == pytest.approx(6.0)
        assert by_name['VAR-001']['ring_offset_error'] == pytest.approx(10.0)
        assert any('independent' in w for w in result.warnings), result.warnings

    def test_two_dates_with_no_gap_between_them_fail(self):
        """
        A missing Gap() is not a gap of zero. Read as one it would put two
        samples on the same ring, which is a different claim and a tighter
        answer than the file makes.
        """
        result = parse_oxcal(oxcal_model(
            'D_Sequence("d"){ R_Date("a",2500,25); R_Date("b",2520,25); };'
        ), 't.oxcal')
        assert not result.ok
        assert any('no Gap()' in e for e in result.errors), result.errors

    def test_a_gap_outside_a_wiggle_match_is_refused(self):
        result = parse_oxcal(oxcal_model(
            'Phase("A"){ R_Date("a",3000,25); Gap(10); R_Date("b",3010,25); };'
        ), 't.oxcal')
        assert not result.ok
        assert any('Gap()' in e for e in result.errors), result.errors

    def test_a_boundary_inside_a_wiggle_match_is_refused(self):
        """
        OxCal lets a D_Sequence end with a Boundary standing for the felling
        ring. That is a parameter beyond the series, and this app has nowhere
        to put it -- so it is named rather than dropped.
        """
        result = parse_oxcal(oxcal_model(
            'D_Sequence("d"){ R_Date("a",2500,25); Gap(10); '
            'R_Date("b",2520,25); Boundary("Felling"); };'
        ), 't.oxcal')
        assert not result.ok
        assert any('boundary' in e.lower() for e in result.errors), result.errors

    def test_a_wiggle_match_inside_a_larger_model_fails_the_import(self):
        """
        Importable as a tree, but not runnable -- and this importer's rule is
        to fail naming the construct rather than to hand back a model that
        fails later on a screen with less to say about why.
        """
        result = parse_oxcal(oxcal_model(
            'Sequence(){ Boundary("Start"); '
            'D_Sequence("d"){ R_Date("a",2500,25); Gap(10); R_Date("b",2520,25); }; '
            'Boundary("End"); };'
        ), 't.oxcal')
        assert not result.ok
        assert any('inside a larger model' in e for e in result.errors), result.errors

    def test_a_trailing_gap_is_reported_rather_than_counted(self):
        result = parse_oxcal(oxcal_model(
            'D_Sequence("d"){ R_Date("a",2500,25); Gap(10); '
            'R_Date("b",2520,25); Gap(12); };'
        ), 't.oxcal')
        assert result.ok, result.errors
        assert any('ends with a Gap()' in w for w in result.warnings), result.warnings
        by_name = {d['sample_name']: d for d in result.dates}
        assert by_name['b']['ring_offset'] == 0.0
        assert by_name['a']['ring_offset'] == 10.0


class TestDepositionSequences:
    """
    P_Sequence -- deposition modelled as a Poisson process down a depth axis
    (services/gibbs.py). The ordinate here is the `z=` each element carries,
    and the two readings worth pinning are what happens to a Date() (an
    undated level, the thing an age-depth model exists to answer) and to a
    Boundary() (the same parameter under another name, which is a warning
    rather than a note).
    """

    def test_a_p_sequence_becomes_a_deposition_sequence_with_depths(self):
        result = parse_oxcal(
            read_fixture('oxcal_wiggle', 'Core_P_Sequence.oxcal'),
            'Core_P_Sequence.oxcal')
        assert result.ok, result.errors

        tree = result.model_tree
        assert tree['element_type'] == 'deposition_sequence'
        assert tree['name'] == 'Peat core'
        assert {d['sample_name']: d['depth_cm'] for d in result.dates} == {
            'PEAT-100': 100.0, 'PEAT-80': 80.0, 'PEAT-60': 60.0,
            'PEAT-40': 40.0, 'PEAT-20': 20.0,
        }

    def test_the_rigidity_prior_is_centred_on_the_k_the_file_gives(self):
        """
        OxCal's U(a,b) argument makes k variable, a and b being log10
        MULTIPLIERS on the k written beside them rather than absolute bounds.
        """
        result = parse_oxcal(oxcal_model(
            'P_Sequence("c",10,0,U(-1,1)){ R_Date("a",2500,25){z=20;}; '
            'R_Date("b",2400,25){z=10;}; };'
        ), 't.oxcal')
        assert result.ok, result.errors
        params = result.model_tree['params']
        assert params['k_variable'] is True
        assert params['log_k_min'] == pytest.approx(0.0)     # log10(10) - 1
        assert params['log_k_max'] == pytest.approx(2.0)     # log10(10) + 1

    def test_a_k_with_no_prior_is_fixed(self):
        result = parse_oxcal(oxcal_model(
            'P_Sequence("c",1){ R_Date("a",2500,25){z=20;}; '
            'R_Date("b",2400,25){z=10;}; };'
        ), 't.oxcal')
        assert result.ok, result.errors
        assert result.model_tree['params'] == {
            'k': 1.0, 'k_variable': False, 'log_k_min': -2.0,
            'log_k_max': 2.0, 'depth_units': 'cm',
        }

    def test_a_dated_level_with_no_depth_fails(self):
        result = parse_oxcal(oxcal_model(
            'P_Sequence("c",1){ R_Date("a",2500,25){z=20;}; '
            'R_Date("b",2400,25); };'
        ), 't.oxcal')
        assert not result.ok
        assert any('no z=' in e for e in result.errors), result.errors

    def test_an_undated_level_is_imported_as_a_question_about_a_depth(self):
        result = parse_oxcal(oxcal_model(
            'P_Sequence("c",1){ R_Date("a",2500,25){z=20;}; '
            'Date("tephra"){z=15;}; R_Date("b",2400,25){z=10;}; };'
        ), 't.oxcal')
        assert result.ok, result.errors
        children = result.model_tree['children']
        assert [c['element_type'] for c in children] == ['interpolated_depth']
        assert children[0]['params']['depth_cm'] == 15.0
        # ...and only there. Date() elsewhere is still an undated free
        # parameter with nothing bounding it.
        elsewhere = parse_oxcal(oxcal_model(
            'Phase("A"){ R_Date("a",3000,25); Date("d"); };'), 't.oxcal')
        assert not elsewhere.ok
        assert any('Date' in e for e in elsewhere.errors)

    def test_a_boundary_with_a_depth_is_imported_but_warned_about(self):
        """
        In this app a deposition sequence's ends are levels of the same
        Poisson process, so the parameter is the same one -- but it is not the
        uniform transition a Boundary is everywhere else here, and reading it
        silently as one would be exactly the substitution this importer
        refuses to make quietly.
        """
        result = parse_oxcal(oxcal_model(
            'P_Sequence("c",1){ Boundary("Base"){z=30;}; '
            'R_Date("a",2500,25){z=20;}; R_Date("b",2400,25){z=10;}; '
            'Boundary("Top"){z=0;}; };'
        ), 't.oxcal')
        assert result.ok, result.errors
        assert [c['element_type'] for c in result.model_tree['children']] == [
            'interpolated_depth', 'interpolated_depth']
        assert any('under another name' in w for w in result.warnings), result.warnings

    def test_z_running_the_wrong_way_is_warned_about_rather_than_guessed_at(self):
        result = parse_oxcal(oxcal_model(
            'P_Sequence("c",1){ R_Date("a",2400,25){z=10;}; '
            'R_Date("b",2500,25){z=20;}; };'
        ), 't.oxcal')
        assert result.ok, result.errors
        assert any('upside down' in w for w in result.warnings), result.warnings

    def test_a_p_sequence_with_no_dates_fails(self):
        result = parse_oxcal(oxcal_model(
            'P_Sequence("c",1){ Date("a"){z=20;}; Date("b"){z=10;}; };'
        ), 't.oxcal')
        assert not result.ok
        assert any('no R_Date' in e for e in result.errors), result.errors

    def test_the_imported_core_recovers_the_ages_it_was_built_at(self):
        """
        The fixture's ages were read off IntCal20 at a constant ten years per
        centimetre, so the import is checked through the solver rather than
        only as a tree.
        """
        from app.services import gibbs

        result = parse_oxcal(
            read_fixture('oxcal_wiggle', 'Core_P_Sequence.oxcal'),
            'Core_P_Sequence.oxcal')
        assert result.ok, result.errors

        solved = gibbs.solve(result.dates, result.model_tree, sweeps=6000,
                             num_samples=300, max_sweeps=40000, seed=31,
                             temperatures=1)
        for date in result.dates:
            expected = 1000.0 + 10.0 * date['depth_cm']
            got = solved['measurements'][date['id']]['posterior_median']
            assert abs(got - expected) < 60.0, (
                f"{date['sample_name']} came back at {got:.0f}, not {expected:.0f}")


class TestRCombine:
    """
    R_Combine imports as the one date it makes -- the date the model editor's
    "Replace with pooled date" makes -- with the editor's refusals.
    """

    def test_it_imports_as_the_pooled_date(self):
        result = parse_oxcal(oxcal_model(
            'Phase("A"){ R_Combine("pooled"){ R_Date("a",3000,25); R_Date("b",3020,25); }; '
            'R_Date("c",3100,30); };'), 't.oxcal')

        assert result.ok, result.errors
        assert [d['sample_name'] for d in result.dates] == ['pooled', 'c']
        pooled = result.dates[0]
        # The error-weighted mean of two equal errors, and its error.
        assert (pooled['age_bp'], pooled['error_1sigma']) == (3010.0, 17.7)
        assert pooled['notes'].startswith('R_Combine of 2 dates (Ward and Wilson 1978; T = 0.3')
        assert 'a 3000 ± 25 BP; b 3020 ± 25 BP' in pooled['notes']
        assert any('R_Combine("pooled") was imported as one pooled date' in n for n in result.notes)

    def test_it_matches_the_same_event_test(self):
        from app.services import statistics
        result = parse_oxcal(oxcal_model(
            'Phase("A"){ R_Combine("p"){ R_Date("a",3000,20); R_Date("b",3040,30); '
            'R_Date("c",3010,25); }; };'), 't.oxcal')
        test = statistics.same_event([
            {'age_bp': a, 'error_1sigma': e, 'curve_id': 'intcal20'}
            for a, e in ((3000, 20), (3040, 30), (3010, 25))])
        pooled = result.dates[0]
        assert pooled['age_bp'] == round(test['mean_bp'], 1)
        assert pooled['error_1sigma'] == round(test['error_bp'], 1)

    def test_the_curve_and_offset_in_force_apply_to_the_pool(self):
        result = parse_oxcal(oxcal_model(
            'Phase("A"){ R_Combine("shells"){ Curve("Marine20","marine20.14c"); '
            'Delta_R("local",-162,28); R_Date("a",3000,25); R_Date("b",3010,25); }; };'
        ), 't.oxcal')

        assert result.ok, result.errors
        pooled = result.dates[0]
        assert (pooled['curve_id'], pooled['delta_r'], pooled['delta_r_error']) == (
            'marine20', -162.0, 28.0)

    def test_dates_that_fail_the_test_are_refused_naming_t(self):
        result = parse_oxcal(oxcal_model(
            'Phase("A"){ R_Combine("mixed"){ R_Date("a",3000,25); R_Date("b",3400,25); }; };'
        ), 't.oxcal')

        assert not result.ok
        assert result.blocking_commands == ['R_Combine']
        assert any('R_Combine("mixed") pools dates that fail the same-event test' in e
                   and 'T = ' in e and '5% critical 3.84' in e for e in result.errors)
        # Offered without the model, its dates are read one by one.
        assert [d['sample_name'] for d in result.dates_only.dates] == ['a', 'b']

    def test_dates_on_different_offsets_are_refused(self):
        result = parse_oxcal(oxcal_model(
            'Curve("Marine20","marine20.14c"); Phase("A"){ R_Combine("p"){ '
            'R_Date("a",3000,25){ Delta_R("x",-100,20); }; '
            'R_Date("b",3010,25){ Delta_R("y",-150,20); }; }; };'), 't.oxcal')

        assert not result.ok
        assert any('cannot pool its dates' in e and 'ΔR' in e for e in result.errors)

    def test_an_outlier_on_the_pool_applies_to_it(self):
        result = parse_oxcal(oxcal_model(
            'Phase("A"){ R_Combine("p"){ Outlier(0.1); R_Date("a",3000,25); '
            'R_Date("b",3010,25); }; };'), 't.oxcal')

        assert result.ok, result.errors
        assert (result.dates[0]['outlier_prior'], result.dates[0]['outlier_model']) == (
            0.1, 'general')

    def test_an_outlier_on_one_pooled_date_is_refused(self):
        result = parse_oxcal(oxcal_model(
            'Phase("A"){ R_Combine("p"){ R_Date("a",3000,25){ Outlier(0.1); }; '
            'R_Date("b",3010,25); }; };'), 't.oxcal')

        assert not result.ok
        assert any('outlier setting on a' in e for e in result.errors)

    def test_an_added_error_is_refused(self):
        result = parse_oxcal(oxcal_model(
            'Phase("A"){ R_Combine("p", 10){ R_Date("a",3000,25); R_Date("b",3010,25); }; };'
        ), 't.oxcal')

        assert not result.ok
        assert any('number after its name' in e for e in result.errors)

    def test_in_a_sequence_the_pool_holds_a_position(self):
        result = parse_oxcal(oxcal_model(
            'Sequence(){ Boundary("S"); R_Combine("p"){ R_Date("a",3000,25); '
            'R_Date("b",3010,25); }; R_Date("c",2900,25); Boundary("E"); };'), 't.oxcal')

        assert result.ok, result.errors
        assert [d['sample_name'] for d in result.dates] == ['p', 'c']
