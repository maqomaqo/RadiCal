"""
Alembic migrations.

The load-bearing test here is test_the_migrations_and_the_models_agree. Every
other kind of schema bug announces itself; a model changed without a migration
does not, because the developer who changed it has a database that already has
the column -- their own, created by an earlier run. It surfaces on someone
else's machine, or in production, as "column does not exist" on every request
to the affected endpoint. That is exactly the failure the hand-maintained
ALTER list in app/database.py existed to paper over, and the reason for
replacing it is not that migrations are tidier but that this test is possible.
"""
import os
import sqlite3
import tempfile

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

import app.models  # noqa: F401  -- registers every model on Base.metadata
from app.database import Base, _alembic_config


@pytest.fixture
def blank_db():
    """A throwaway SQLite file, and an engine on it. Not the suite's own
    database: these tests migrate from nothing, and would otherwise be
    re-creating the schema every other test is using."""
    path = tempfile.mktemp(suffix='.db', prefix='radical_migration_test_')
    engine = create_engine(f'sqlite:///{path}')
    yield engine, path
    engine.dispose()
    if os.path.exists(path):
        os.remove(path)


def _upgrade(engine, revision='head'):
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), revision)


class TestTheMigrationsBuildTheSchema:
    def test_upgrading_from_nothing_creates_every_table(self, blank_db):
        engine, _ = blank_db
        _upgrade(engine)
        tables = set(inspect(engine).get_table_names())
        assert tables >= {t.name for t in Base.metadata.sorted_tables}, (
            "migrations did not create every table the models declare")

    def test_the_migrations_and_the_models_agree(self, blank_db):
        """
        After upgrading to head there must be NOTHING left to autogenerate.

        This is the whole point of adopting Alembic. A column added to a model
        without a migration passes every other test in this suite -- the
        developer's own database already has it -- and fails on a fresh one.
        Here it fails immediately, and the message names the column.
        """
        engine, _ = blank_db
        _upgrade(engine)

        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={'compare_type': True, 'render_as_batch': True})
            diff = compare_metadata(context, Base.metadata)

        assert diff == [], (
            "The models and the migrations disagree. Generate the missing "
            "migration with:\n\n"
            "    alembic revision --autogenerate -m \"what changed\"\n\n"
            f"Outstanding differences: {diff}")

    def test_every_revision_is_reachable_from_one_head(self):
        """
        One linear history, not a fork. Two heads mean two people generated a
        revision from the same parent, and `upgrade head` then fails with an
        error most people meet for the first time in production.
        """
        script = ScriptDirectory.from_config(_alembic_config())
        heads = script.get_heads()
        assert len(heads) == 1, f"the migration history has forked: {heads}"


class TestCoordinatesAreAddedAndCanBeUndone:
    """
    The one revision that adds coordinates, checked in both directions.

    test_the_migrations_and_the_models_agree above already catches a column
    declared on a model and never migrated. What it cannot catch is a
    downgrade that does not undo its own upgrade -- nothing autogenerates
    backwards -- so the round trip is written out here, on a database holding a
    row, to show that going back drops the columns and keeps the work.
    """

    COORDINATE_COLUMNS = {
        'radiocarbon_samples': {'latitude', 'longitude'},
        'project_samples': {'latitude', 'longitude'},
        'projects': {'coordinate_precision', 'map_tiles_enabled'},
    }

    def _columns(self, engine, table):
        return {c['name'] for c in inspect(engine).get_columns(table)}

    def test_head_has_the_coordinate_columns(self, blank_db):
        engine, _ = blank_db
        _upgrade(engine)
        for table, columns in self.COORDINATE_COLUMNS.items():
            assert self._columns(engine, table) >= columns, (
                f"{table} is missing {columns - self._columns(engine, table)}")

    def test_the_project_settings_default_rather_than_null(self, blank_db):
        """
        Both are NOT NULL on a table that already has rows, so the defaults are
        the migration's business and not the model's. A project made before
        this ran must come back with full precision and tiles off -- the latter
        especially: a null read as falsy by accident is not the same as a
        default that says no.
        """
        engine, path = blank_db
        _upgrade(engine, 'f3b81c60d5a7')
        con = sqlite3.connect(path)
        con.execute("INSERT INTO projects (id, name) VALUES ('older', 'Made before coordinates')")
        con.commit()
        con.close()

        _upgrade(engine)

        con = sqlite3.connect(path)
        precision, tiles = con.execute(
            'SELECT coordinate_precision, map_tiles_enabled FROM projects '
            "WHERE id = 'older'").fetchone()
        con.close()
        assert precision == 'full'
        assert not tiles

    def test_downgrading_drops_the_columns_and_keeps_the_row(self, blank_db):
        engine, path = blank_db
        _upgrade(engine)

        con = sqlite3.connect(path)
        con.execute(
            "INSERT INTO projects (id, name, coordinate_precision, map_tiles_enabled) "
            "VALUES ('kept', 'Has a coordinate', '0.01', 1)")
        con.commit()
        con.close()

        with engine.begin() as connection:
            command.downgrade(_alembic_config(connection), 'f3b81c60d5a7')

        for table, columns in self.COORDINATE_COLUMNS.items():
            assert not (self._columns(engine, table) & columns), (
                f"downgrade left {self._columns(engine, table) & columns} on {table}")

        con = sqlite3.connect(path)
        assert con.execute("SELECT name FROM projects WHERE id = 'kept'").fetchone() is not None
        con.close()

    def test_upgrading_again_after_a_downgrade_works(self, blank_db):
        """A downgrade that leaves the table in a state the upgrade cannot run
        against again is a downgrade that only looked reversible."""
        engine, _ = blank_db
        _upgrade(engine)
        with engine.begin() as connection:
            command.downgrade(_alembic_config(connection), 'f3b81c60d5a7')
        _upgrade(engine)
        for table, columns in self.COORDINATE_COLUMNS.items():
            assert self._columns(engine, table) >= columns


class TestTheReservoirMirrorIsAddedAndCanBeUndone:
    """
    The snapshot tables and the provenance column, checked both ways.

    The interesting half is the downgrade, as above: nothing autogenerates
    backwards, and this revision creates TABLES rather than columns, so the
    way back has to drop them in an order the foreign keys allow.
    """

    TABLES = {'marine_reservoir_snapshots', 'marine_reservoir_points'}

    def test_head_has_the_snapshot_tables(self, blank_db):
        engine, _ = blank_db
        _upgrade(engine)
        assert set(inspect(engine).get_table_names()) >= self.TABLES

    def test_both_sample_tables_record_where_a_delta_r_came_from(self, blank_db):
        engine, _ = blank_db
        _upgrade(engine)
        for table in ('radiocarbon_samples', 'project_samples'):
            names = {c['name'] for c in inspect(engine).get_columns(table)}
            assert 'delta_r_source' in names, f'{table} cannot say where a ΔR came from'

    def test_a_point_belongs_to_exactly_one_snapshot(self, blank_db):
        """
        The composite key. map_no is the SOURCE's identifier and is unique only
        within a snapshot, so two snapshots of the database must be able to
        hold the same map number without colliding -- which is the whole point
        of keeping an old snapshot around.
        """
        engine, path = blank_db
        _upgrade(engine)
        con = sqlite3.connect(path)
        for snapshot in ('calib-2026-01-01', 'calib-2026-05-11'):
            con.execute(
                'INSERT INTO marine_reservoir_snapshots '
                '(id, source, record_count, content_sha256) VALUES (?, ?, ?, ?)',
                (snapshot, 'calib.org/marine', 1, 'abc'))
            con.execute(
                'INSERT INTO marine_reservoir_points '
                '(snapshot_id, map_no, latitude, longitude) VALUES (?, ?, ?, ?)',
                (snapshot, 995, 54.5, -5.6))
        con.commit()
        assert con.execute('SELECT count(*) FROM marine_reservoir_points').fetchone()[0] == 2
        con.close()

    def test_downgrading_drops_the_mirror_and_keeps_the_work(self, blank_db):
        engine, path = blank_db
        _upgrade(engine)

        con = sqlite3.connect(path)
        con.execute(
            "INSERT INTO analyses (id, name, analysis_type, status) "
            "VALUES ('kept', 'Has results', 'bayesian', 'completed')")
        con.commit()
        con.close()

        with engine.begin() as connection:
            command.downgrade(_alembic_config(connection), 'a4d21f7e9c36')

        tables = set(inspect(engine).get_table_names())
        assert not (tables & self.TABLES), f'downgrade left {tables & self.TABLES}'
        for table in ('radiocarbon_samples', 'project_samples'):
            names = {c['name'] for c in inspect(engine).get_columns(table)}
            assert 'delta_r_source' not in names

        con = sqlite3.connect(path)
        assert con.execute("SELECT name FROM analyses WHERE id = 'kept'").fetchone() is not None
        con.close()

    def test_upgrading_again_after_a_downgrade_works(self, blank_db):
        engine, _ = blank_db
        _upgrade(engine)
        with engine.begin() as connection:
            command.downgrade(_alembic_config(connection), 'a4d21f7e9c36')
        _upgrade(engine)
        assert set(inspect(engine).get_table_names()) >= self.TABLES


class TestAdoptingAnExistingDatabase:
    """
    A database created before Alembic existed has the tables but no
    alembic_version, so the baseline cannot simply run -- everything it creates
    is already there. app.database._adopt_alembic tops it up from the legacy
    column list and stamps it instead.
    """

    def _pre_alembic_database(self, engine, drop_columns=()):
        Base.metadata.create_all(bind=engine)
        path = str(engine.url).replace('sqlite:///', '')
        con = sqlite3.connect(path)
        for table, column in drop_columns:
            con.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
        con.execute(
            "INSERT INTO analyses (id, name, analysis_type, status) "
            "VALUES ('legacy-row', 'Made before Alembic', 'bayesian', 'completed')")
        con.commit()
        con.close()
        return path

    def _adopt(self, engine, monkeypatch):
        # _adopt_alembic reads the module-level engine, which is the suite's
        # own; point it at the throwaway one for the duration.
        import app.database as database
        monkeypatch.setattr(database, 'engine', engine)
        with engine.begin() as connection:
            return database._adopt_alembic(connection)

    def test_an_old_database_is_stamped_rather_than_rebuilt(self, blank_db, monkeypatch):
        engine, path = blank_db
        self._pre_alembic_database(engine)

        assert self._adopt(engine, monkeypatch) is True

        con = sqlite3.connect(path)
        version = con.execute('SELECT version_num FROM alembic_version').fetchone()
        assert version is not None, "the database was not stamped"
        # The row it already held is still there: adoption must not be a
        # rebuild, whatever else it does.
        assert con.execute(
            "SELECT name FROM analyses WHERE id = 'legacy-row'").fetchone() is not None
        con.close()

    def test_columns_an_old_database_is_missing_are_added(self, blank_db, monkeypatch):
        """
        The legacy ALTER list is closed to new entries but still has to run
        once, or a database old enough to be missing these columns joins the
        migration history still missing them -- and every later migration
        assumes the baseline is complete.
        """
        engine, path = blank_db
        self._pre_alembic_database(
            engine, drop_columns=[('analyses', 'solver'),
                                  ('radiocarbon_samples', 'delta_r')])

        self._adopt(engine, monkeypatch)

        con = sqlite3.connect(path)
        analyses = {r[1] for r in con.execute('PRAGMA table_info(analyses)')}
        samples = {r[1] for r in con.execute('PRAGMA table_info(radiocarbon_samples)')}
        con.close()
        assert 'solver' in analyses
        assert 'delta_r' in samples

    def test_a_new_database_is_not_treated_as_an_old_one(self, blank_db, monkeypatch):
        """Nothing there at all: adoption declines, and the caller runs the
        migrations normally."""
        engine, _ = blank_db
        assert self._adopt(engine, monkeypatch) is False

    def test_a_database_already_under_alembic_is_left_alone(self, blank_db, monkeypatch):
        engine, _ = blank_db
        _upgrade(engine)
        assert self._adopt(engine, monkeypatch) is False


class TestTheBaselineRefusesToBeUndone:
    def test_downgrading_past_the_baseline_raises(self, blank_db):
        """
        Alembic generated a downgrade that drops every table, which here means
        deleting every project and analysis in the database. It is replaced by
        a refusal: an irreversible loss of a user's work should not be one
        mistyped command away.
        """
        engine, _ = blank_db
        _upgrade(engine)
        with pytest.raises(Exception) as raised:
            with engine.begin() as connection:
                command.downgrade(_alembic_config(connection), 'base')
        assert 'not reversible' in str(raised.value)
