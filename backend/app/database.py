from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://radiocarbon:password@localhost:5432/radiocarbon_db"
)

# The job worker holds a connection for a whole run (tens of seconds) while
# requests keep taking their own, so the default pool of five is too small;
# pre_ping catches connections the database dropped while idle. SQLite takes
# none of these arguments.
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
# CLOSED. Everything down to _ensure_schema_migrations exists for one job:
# topping a pre-Alembic database up to the baseline revision so it can be
# stamped and join the migration history. Do not add to these lists -- a
# schema change means a migration (`alembic revision --autogenerate`), and
# tests/test_migrations.py fails if the models and migrations disagree.
# ---------------------------------------------------------------------------

# Columns added after their tables were first created. No FOREIGN KEY is added
# for project_id: that is awkward to do portably across SQLite/Postgres after
# the fact, and the ORM-level relationship works either way.
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

# from_sample_id/to_sample_id are polymorphic (a sample id or a group id, see
# from_type/to_type), so models.py declares no FOREIGN KEY on them. A stale FK
# on an old database rejects every context-to-context relationship with a 400
# ForeignKeyViolation, so it is dropped by name if present.
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
                # Absent, so the baseline migration creates it in full.
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

    The ALTER list runs once to supply columns the baseline would have created
    (the baseline itself cannot run, as its tables already exist), then the
    database is STAMPED at the baseline rather than upgraded to it.
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

    Migrations rather than create_all(), which only ever creates a table and
    never alters one. Run at startup because this is a single-process app run
    locally; set RADICAL_SKIP_MIGRATIONS=1 for a deployment that would rather
    run `alembic upgrade head` deliberately.
    """
    # Importing registers every model on Base.metadata, which autogenerate
    # needs to see anything.
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
