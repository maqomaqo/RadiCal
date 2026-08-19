"""
Reading dates in from a file -- services/date_import.py (CSV),
services/oxcal_script.py (OxCal's CQL2), and api/imports.py.

The load-bearing tests are in TestOxcalMatchesTheHandBuiltFixtures. The three
Nenthorn .oxcal files in tests/fixtures/oxcal/ are ALSO transcribed by hand in
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
from app.services.date_import import parse_csv, parse_upload, template_csv
from app.services.oxcal_script import parse_oxcal

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
        ('ordered', 'Nenthorn_sites_3.oxcal'),
        ('unordered', 'Nenthorn_sites_3_unordered.oxcal'),
        ('shared_boundary', 'Nenthorn_sites_3_shared_boundary.oxcal'),
    ])
    def test_the_structure_is_the_one_the_fixture_builds(self, run, filename):
        result = parse_oxcal(read_fixture('oxcal', filename), filename)

        assert result.ok, result.errors
        assert shape(result.model_tree) == shape(ox.model_tree(run))

    @pytest.mark.parametrize('run,filename', [
        ('ordered', 'Nenthorn_sites_3.oxcal'),
        ('unordered', 'Nenthorn_sites_3_unordered.oxcal'),
        ('shared_boundary', 'Nenthorn_sites_3_shared_boundary.oxcal'),
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
        result = parse_oxcal(read_fixture('oxcal', 'Nenthorn_sites_3.oxcal'),
                             'Nenthorn_sites_3.oxcal')

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
        The Nenthorn models declare Curve("SHCal20") once, at the top. Every
        date must come back on it -- silently defaulting to IntCal20 would
        calibrate a Southern Hemisphere site against the wrong curve and still
        produce a plausible-looking answer.
        """
        result = parse_oxcal(read_fixture('oxcal', 'Nenthorn_sites_3.oxcal'), 'x.oxcal')

        assert {d['curve_id'] for d in result.dates} == {'shcal20'}


class TestTheCharcoalModel:
    """
    tests/fixtures/oxcal_charcoal/Nenthorn_Seq.oxcal -- two outlier models,
    declared and applied per date, and a nested Curve("=SHCal20").
    """

    def setup_method(self):
        self.result = parse_oxcal(
            read_fixture('oxcal_charcoal', 'Nenthorn_Seq.oxcal'), 'Nenthorn_Seq.oxcal'
        )

    def test_it_imports(self):
        assert self.result.ok, self.result.errors
        assert len(self.result.dates) == 11

    def test_the_declared_outliers_come_through_per_date(self):
        by_name = {d['sample_name']: d for d in self.result.dates}

        # Outlier("Charcoal", 1.00) -- material DECLARED residual, not merely
        # suspected. The prior of 1.0 is the point of the model.
        for name in ('WK50104', 'WK50098', 'ANU55207'):
            assert by_name[name]['outlier_model'] == 'charcoal'
            assert by_name[name]['outlier_prior'] == 1.0

        for name in ('WK51189', 'WK50099', 'ANU55206'):
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


class TestTheShagRiverModel:
    def test_sixteen_dates_in_one_phase(self):
        result = parse_oxcal(
            read_fixture('oxcal_outlier', 'Shag_River.oxcal'), 'Shag_River.oxcal'
        )

        assert result.ok, result.errors
        assert len(result.dates) == 16
        assert shape(result.model_tree) == {
            'type': 'sequence', 'name': 'Shag River Mouth', 'children': [
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
        ('R_Combine', 'Phase("A"){ R_Combine("c"){ R_Date("a",3000,25); }; };'),
        ('P_Sequence', 'P_Sequence("p",1){ Boundary(); Phase("A"){ R_Date("a",3000,25); }; Boundary(); };'),
        ('D_Sequence', 'D_Sequence("d"){ R_Date("a",3000,25); Gap(10); };'),
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

    def test_options_are_noted_rather_than_carried_over(self):
        result = parse_oxcal(oxcal_model(
            'Options(){ Resolution=1; kIterations=30; }; '
            'Sequence(){ Boundary(); Phase("A"){ R_Date("a",3000,25); }; Boundary(); };'
        ), 't.oxcal')

        assert result.ok, result.errors
        assert any('Resolution' in n and 'not carried over' in n for n in result.notes)

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
    tests/fixtures/oxcal/Nenthorn_c14.csv is a real input table for the
    Nenthorn models -- headed 'R_Date,BP,Error,Material,Phase', which is what
    a table written to be fed to OxCal actually looks like. It found a gap:
    'R_Date' is OxCal's own name for the lab-code column and was not among the
    spellings understood.
    """

    def test_it_imports_without_being_edited_first(self):
        result = parse_csv(read_fixture('oxcal', 'Nenthorn_c14.csv'), 'Nenthorn_c14.csv')

        assert result.ok, result.errors
        assert result.warnings == []
        assert len(result.dates) == 8

    def test_it_agrees_with_the_dates_the_oxcal_model_carries(self):
        """The .csv and the .oxcal are two views of one set of measurements,
        so importing either must give the same eight dates."""
        from_csv = parse_csv(read_fixture('oxcal', 'Nenthorn_c14.csv'), 'x.csv')
        from_model = parse_oxcal(
            read_fixture('oxcal', 'Nenthorn_sites_3.oxcal'), 'x.oxcal'
        )

        def summarise(result):
            return {
                ox.key(d['sample_name']): (d['age_bp'], d['error_1sigma'], d['phase_name'])
                for d in result.dates
            }

        assert summarise(from_csv) == summarise(from_model)


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
            '1,sequence,Nenthorn,Nenthorn,,,,\n'
            '2,phase,Nenthorn > Quarry,Quarry,,,,\n'
            '3,date,Nenthorn > Quarry,WK51189,685,25,shcal20,650.5\n'
            '4,date,Nenthorn > Quarry,WK50102,671,21,shcal20,640.1\n'
        )
        result = parse_csv(text)

        assert result.ok, result.errors
        assert len(result.dates) == 2
        assert phase_of(result) == {'WK51189': 'Quarry', 'WK50102': 'Quarry'}
        assert any('structural row' in n for n in result.notes)

    def test_posterior_columns_are_not_mistaken_for_input(self):
        """They are output. Recognised so they do not raise an 'unrecognised
        column' warning, and never read back in as data."""
        result = parse_csv(
            'Name,Age BP,1-Sigma Error,Posterior Mean,95% HPD,Agreement %\n'
            'OxA-1,3000,25,3120.4,3100-3140,98.2\n'
        )

        assert result.ok, result.errors
        assert result.warnings == []
        assert 'posterior_mean' not in result.dates[0]

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
        content = read_fixture('oxcal', 'Nenthorn_sites_3.oxcal').encode()
        response = self._upload(client, 'Nenthorn_sites_3.oxcal', content)

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

    def test_a_bad_file_is_a_200_carrying_its_problems(self):
        """
        The request succeeded; the answer is what is wrong with the file.
        Reserving the error status for actual request failures keeps 'the
        upload broke' distinguishable from 'row 2 has a letter in the age'.
        """
        from fastapi.testclient import TestClient
        from app.main import app

        response = TestClient(app).post(
            '/api/import/dates',
            files={'file': ('dates.csv', io.BytesIO(b'name,age,error\nA,oops,25\n'), 'text/csv')},
        )

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
            read_fixture('oxcal', 'Nenthorn_sites_3.oxcal'), 'Nenthorn_sites_3.oxcal'
        )
        assert result.ok, result.errors

        response = client.post('/api/analysis/bayesian?wait=true', json={
            'name': 'Imported Nenthorn',
            'measurements': [
                {k: v for k, v in d.items() if k != 'phase_name'} for d in result.dates
            ],
            'model_tree': result.model_tree,
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
