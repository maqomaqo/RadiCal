from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://radiocarbon:password@localhost:5432/radiocarbon_db"
)

# Pool sizing. This used to be left at SQLAlchemy's defaults, filed as
# REMAINING_WORK item 7 and true enough to say it "only bites under
# concurrent load" -- but the background worker IS concurrent load: it holds
# a connection for the whole length of a run (tens of seconds, sometimes
# minutes) while requests continue to arrive and take their own. The request
# threadpool and one job worker want more than the default five, and a
# connection idle for half an hour behind a container network is one the
# database may already have dropped, which pre_ping catches instead of
# handing out a dead socket.
#
# SQLite takes none of these arguments (it has no connection pool worth the
# name), and the test suite runs on SQLite, so they are applied only where
# they mean something.
_POOL_OPTIONS = {} if DATABASE_URL.startswith('sqlite') else {
    'pool_size': 10,
    'max_overflow': 20,
    'pool_pre_ping': True,
    'pool_recycle': 1800,
}

engine = create_engine(DATABASE_URL, echo=False, **_POOL_OPTIONS)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ---------------------------------------------------------------------------
# CLOSED. Everything from here to _ensure_schema_migrations is the stopgap
# Alembic replaced, kept for exactly one job: bringing a database created
# BEFORE Alembic was adopted up to the baseline revision, once, so it can be
# stamped and join the ordinary migration history.
#
# Do not add to these lists. A schema change now means a migration:
#
#     docker compose exec backend alembic revision --autogenerate -m "..."
#
# and tests/test_migrations.py fails if the models and the migrations disagree,
# so a forgotten one does not reach anybody's database.
#
# Why it is not simply deleted. These lists are the only record of what an old
# database is missing. Deleting them would strand anyone upgrading straight
# from a pre-Alembic version: their tables exist, so the baseline migration
# cannot run, and the columns it would have created would never appear. That
# is a silent failure that surfaces as "column does not exist" on every
# request to the affected endpoint. It costs a few dozen lines to keep, and it
# runs at most once in a database's life.
# ---------------------------------------------------------------------------

# Columns added to existing models after their tables were first created.
# create_all() only creates tables that don't exist yet -- it never ALTERs an
# existing table to add a new column. On any database that predates a given
# column, that column is simply missing, and every query that touches it fails
# with a real "column does not exist" error.
#
# Note: this deliberately does not attempt to add a real FOREIGN KEY
# constraint for project_id via ALTER TABLE -- that's awkward to do
# portably across SQLite/Postgres after the fact. The ORM-level
# relationship still works correctly either way; this just means the
# database itself won't reject an invalid project_id at the SQL layer.
_LEGACY_COLUMN_MIGRATIONS = {
    'analyses': [
        ('project_id', 'VARCHAR'),
        # Which solver produced the results, and why it fell back if it did.
        ('solver', 'JSON'),
    ],
    'analysis_results': [
        ('hpd_68_ranges', 'JSON'),
        ('hpd_95_ranges', 'JSON'),
        ('agreement_index', 'FLOAT'),
        ('outlier_probability', 'FLOAT'),
    ],
    'radiocarbon_samples': [
        ('group_id', 'VARCHAR'),
        ('tpq_age_bp', 'FLOAT'),
        ('taq_age_bp', 'FLOAT'),
        # Which model-tree element (a phase) this date sits in; supersedes
        # group_id. Backfilled from group_id when an old analysis's
        # stratigraphy is migrated to a tree.
        ('element_id', 'VARCHAR'),
        # Local marine reservoir offset and its uncertainty.
        ('delta_r', 'FLOAT'),
        ('delta_r_error', 'FLOAT'),
        ('outlier_prior', 'FLOAT'),
        ('outlier_model', 'VARCHAR'),
    ],
    'stratigraphic_relationships': [
        ("from_type", "VARCHAR DEFAULT 'sample'"),
        ("to_type", "VARCHAR DEFAULT 'sample'"),
    ],
    'sample_groups': [
        ('modeled_start', 'JSON'),
        ('modeled_end', 'JSON'),
    ],
}

# stratigraphic_relationships.from_sample_id/to_sample_id are polymorphic --
# each can hold either a RadiocarbonSample.id or a SampleGroup.id (see
# from_type/to_type) -- so models.py deliberately declares no FOREIGN KEY on
# them; a plain FK could only ever point at one table. Databases created
# before that polymorphism was introduced still have the old FK (pointing
# only at radiocarbon_samples), left behind because create_all() never
# drops or alters an existing constraint. That stale constraint silently
# rejects every relationship whose from_sample_id/to_sample_id is actually
# a group id -- i.e. every context-to-context relationship, which is the
# only kind the Stratigraphy UI creates -- with a 400 ForeignKeyViolation.
# Dropped here, by name, only if present.
_LEGACY_STALE_FK_CONSTRAINTS = {
    'stratigraphic_relationships': [
        'stratigraphic_relationships_from_sample_id_fkey',
        'stratigraphic_relationships_to_sample_id_fkey',
    ],
}

def _ensure_schema_migrations():
    """Top an old database up to the baseline. See the CLOSED note above."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table, columns in _LEGACY_COLUMN_MIGRATIONS.items():
            if table not in existing_tables:
                # Not in this database at all, so there is no old version of
                # it to top up; the baseline migration creates it in full.
                continue
            existing_columns = {c['name'] for c in inspector.get_columns(table)}
            for column_name, column_type in columns:
                if column_name in existing_columns:
                    continue
                try:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column_name} {column_type}'))
                    print(f"✓ Migrated: added column {table}.{column_name}")
                except Exception as e:
                    print(f"⚠ Warning: could not add column {table}.{column_name}: {e}")

        for table, constraint_names in _LEGACY_STALE_FK_CONSTRAINTS.items():
            if table not in existing_tables:
                continue
            existing_fks = {fk['name'] for fk in inspector.get_foreign_keys(table)}
            for constraint_name in constraint_names:
                if constraint_name not in existing_fks:
                    continue
                try:
                    conn.execute(text(f'ALTER TABLE {table} DROP CONSTRAINT {constraint_name}'))
                    print(f"✓ Migrated: dropped stale constraint {table}.{constraint_name}")
                except Exception as e:
                    print(f"⚠ Warning: could not drop constraint {table}.{constraint_name}: {e}")

def _alembic_config(connection=None):
    """
    Alembic's config, pointed at this package's alembic/ directory.

    Located relative to this file rather than to the working directory, so
    that migrations run the same whether the app was started by uvicorn from
    /app, by pytest from anywhere, or by `alembic` on the command line.
    """
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = Config(os.path.join(root, 'alembic.ini'))
    config.set_main_option('script_location', os.path.join(root, 'alembic'))
    if connection is not None:
        # env.py picks this up and migrates on the app's own connection
        # instead of opening a second one.
        config.attributes['connection'] = connection
    return config


def _adopt_alembic(connection) -> bool:
    """
    Bring a database that predates Alembic into the migration history.

    Returns True if this was such a database. Recognised by having the app's
    tables but no alembic_version: a new database has neither, and one already
    under Alembic has both.

    The old ALTER list runs one last time -- an old database may be missing
    columns the baseline revision would have created, and the baseline cannot
    run because the tables it creates already exist -- and then the database is
    STAMPED at the baseline rather than upgraded to it. From the next
    migration onwards it is indistinguishable from a database created today.
    """
    from alembic import command
    from alembic.runtime.migration import MigrationContext

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if 'alembic_version' in tables or 'analyses' not in tables:
        return False

    _ensure_schema_migrations()

    context = MigrationContext.configure(connection)
    if context.get_current_revision() is None:
        command.stamp(_alembic_config(connection), 'head')
        print("✓ Adopted an existing database into Alembic at the baseline")
    return True


def init_db():
    """
    Bring the database up to the current schema.

    Migrations rather than create_all(): a table is created once and then
    changed many times, and create_all() only ever does the first of those.
    Renames, type changes and backfills -- none of which the ALTER list it used
    to be paired with could express -- are ordinary revisions now.

    Run at startup because this is a single-process app someone runs locally,
    where "the schema is up to date" should not be a separate step to remember.
    Set RADICAL_SKIP_MIGRATIONS=1 to take the decision back, for a deployment
    that would rather run `alembic upgrade head` deliberately.
    """
    # Registers every model on Base.metadata -- importing the module is what
    # does it, so the names here are not the point. env.py depends on this
    # having happened for autogenerate to see anything.
    import app.models  # noqa: F401

    if os.getenv('RADICAL_SKIP_MIGRATIONS') == '1':
        print("• Skipping migrations (RADICAL_SKIP_MIGRATIONS=1)")
        return

    from alembic import command

    with engine.begin() as connection:
        adopted = _adopt_alembic(connection)
        if not adopted:
            command.upgrade(_alembic_config(connection), 'head')

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
