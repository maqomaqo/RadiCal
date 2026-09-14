"""The marine reservoir mirror, and where a looked-up Delta-R came from.

Revision ID: b7c4e0a13d92
Revises: a4d21f7e9c36
Created: 2026-09-14 19:00:00.000000+00:00

Two tables holding a frozen copy of the CHRONO/calib.org marine reservoir
database, and one JSON column on each sample table recording how a date's
Delta-R was derived from it. See docs/SPATIAL_METADATA_AND_DISPERSAL_PLAN.md
§5.

**Why a mirror rather than a query.** Deriving a Delta-R from a date's
coordinates means asking "what reservoir measurements are near HERE". Asking
that of calib.org at analysis time would send a site's position to a third
party on every lookup -- the disclosure the previous revision's
coordinate_precision and map_tiles_enabled exist to control, arriving by the
back door. Mirroring the database is what makes the lookup possible without
it, and it is also what makes two installs of one version agree on what they
compute.

The snapshot tables follow calibration_curves' rules, because they answer the
same question: a published result must not move underneath the person who
published it. A snapshot's numbers never change, a newer table is a new
snapshot rather than an edit, `content_sha256` is verified on load and a
mismatch refuses to serve, and a snapshot any stored `delta_r_source` names
cannot be deleted.

`created_by` takes the split the curves take. The BUNDLED snapshot ships as a
file in calibration_data/ with no row and no owner, so a fresh install has a
working lookup with no network at all. An INGESTED snapshot is a row owned by
whoever ran the ingest and listed for nobody else -- a curve someone is still
working on has nobody vouching for it, and the same is true of a reservoir
table someone pulled down this morning.

`feeding` and `feeding_raw` are two columns for one source field because the
normalisation is a JUDGEMENT: the source holds 25 spellings of about seven
classes, five of which are compound descriptions truncated at sixteen
characters by its column width. The normalised class is what the lookup
filters on; the raw string is what a reader needs to see to check why one of
them became 'ambiguous'.

Everything here is additive, and no existing row changes meaning: an analysis
saved before this migration reruns identically, which is what
tests/test_migrations.py enforces.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c4e0a13d92'
down_revision: Union[str, None] = 'a4d21f7e9c36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'marine_reservoir_snapshots',
        # Source plus its own last_changed, e.g. 'calib-marine20-2026-05-11'.
        # The primary key because a date's delta_r_source names it, and it has
        # to stay resolvable for as long as any analysis cites it.
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('fetched_at', sa.DateTime(), nullable=True),
        # What the SOURCE says about its own currency, as distinct from when
        # we happened to ask. The pair answers "is this stale?" without a
        # re-fetch.
        sa.Column('source_last_changed', sa.DateTime(), nullable=True),
        sa.Column('record_count', sa.Integer(), nullable=False),
        sa.Column('content_sha256', sa.String(), nullable=False),
        sa.Column('citation', sa.String(), nullable=True),
        sa.Column('note', sa.String(), nullable=True),
        # Null for the bundled snapshot, which belongs to the install.
        sa.Column('created_by', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        # Named explicitly: SQLite cannot drop an unnamed constraint, and it
        # is what makes downgrade() below possible at all.
        sa.ForeignKeyConstraint(
            ['created_by'], ['users.id'],
            name='fk_marine_reservoir_snapshots_created_by_users'),
    )
    op.create_index(
        op.f('ix_marine_reservoir_snapshots_created_by'),
        'marine_reservoir_snapshots', ['created_by'], unique=False)

    op.create_table(
        'marine_reservoir_points',
        sa.Column('snapshot_id', sa.String(), nullable=False),
        # The source's own identifier for the point. Kept because it is what
        # calib.org's page is queried by, so a provenance record naming five
        # map numbers can be checked against the site by anyone -- the
        # property that makes a private mirror acceptable.
        sa.Column('map_no', sa.Integer(), nullable=False),
        sa.Column('latitude', sa.Float(), nullable=False),
        sa.Column('longitude', sa.Float(), nullable=False),
        sa.Column('delta_r', sa.Float(), nullable=True),
        # Nullable because that is the source's shape: some rows carry no
        # uncertainty, a weighted mean cannot use them, and the lookup
        # excludes them and says how many it excluded.
        sa.Column('delta_r_error', sa.Float(), nullable=True),
        sa.Column('reference', sa.String(), nullable=True),
        sa.Column('locality', sa.String(), nullable=True),
        sa.Column('subregion', sa.String(), nullable=True),
        sa.Column('notes', sa.String(), nullable=True),
        sa.Column('c14_age', sa.Float(), nullable=True),
        sa.Column('c14_error', sa.Float(), nullable=True),
        sa.Column('lab_id', sa.String(), nullable=True),
        sa.Column('collection_year', sa.Integer(), nullable=True),
        sa.Column('reservoir_age', sa.Float(), nullable=True),
        sa.Column('genus', sa.String(), nullable=True),
        sa.Column('species', sa.String(), nullable=True),
        sa.Column('feeding', sa.String(), nullable=True),
        sa.Column('feeding_raw', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('snapshot_id', 'map_no'),
        sa.ForeignKeyConstraint(
            ['snapshot_id'], ['marine_reservoir_snapshots.id'],
            name='fk_marine_reservoir_points_snapshot_id'),
    )

    for table in ('radiocarbon_samples', 'project_samples'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('delta_r_source', sa.JSON(), nullable=True))


def downgrade() -> None:
    # Reversible, and it drops the mirror rather than anybody's work: every
    # project, analysis and result survives it. What is lost is the provenance
    # of any Delta-R the lookup derived -- the offsets themselves are on
    # delta_r/delta_r_error and stay put -- and the snapshots, which are a
    # copy of a published database and can be ingested again.
    for table in ('project_samples', 'radiocarbon_samples'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('delta_r_source')

    op.drop_table('marine_reservoir_points')
    op.drop_index(
        op.f('ix_marine_reservoir_snapshots_created_by'),
        table_name='marine_reservoir_snapshots')
    op.drop_table('marine_reservoir_snapshots')
