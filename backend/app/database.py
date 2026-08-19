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

# Columns added to existing models after their tables were first created.
# Base.metadata.create_all() (called below) only creates tables that don't
# exist yet -- it never ALTERs an existing table to add a new column. On
# any database that predates a given column, that column is simply
# missing, and every query that touches it (which, since these were added
# to the SELECT/INSERT in every relevant endpoint, is *every* request to
# that endpoint) fails with a real "column does not exist" error. There's
# no Alembic in this project, so _ensure_schema_migrations() below is a
# minimal stand-in: for each table that already exists, add any column
# from this list that's missing. New installs never hit this path at all
# (create_all already creates the table with every current column), so
# this only matters for databases created before one of these columns was
# introduced.
#
# Note: this deliberately does not attempt to add a real FOREIGN KEY
# constraint for project_id via ALTER TABLE -- that's awkward to do
# portably across SQLite/Postgres after the fact. The ORM-level
# relationship still works correctly either way; this just means the
# database itself won't reject an invalid project_id at the SQL layer.
_COLUMN_MIGRATIONS = {
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
_STALE_FK_CONSTRAINTS = {
    'stratigraphic_relationships': [
        'stratigraphic_relationships_from_sample_id_fkey',
        'stratigraphic_relationships_to_sample_id_fkey',
    ],
}

def _ensure_schema_migrations():
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table, columns in _COLUMN_MIGRATIONS.items():
            if table not in existing_tables:
                # Brand-new table -- create_all() above already created it
                # with the full current schema, nothing to migrate.
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

        for table, constraint_names in _STALE_FK_CONSTRAINTS.items():
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

def init_db():
    """Initialize database tables"""
    # Import models to register them with Base
    from app.models import (
        Project, Analysis, RadiocarbonSample, AnalysisResult,
        StratigraphicRelationship, SampleGroup, StoredCalibrationCurve,
    )
    Base.metadata.create_all(bind=engine)
    _ensure_schema_migrations()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
