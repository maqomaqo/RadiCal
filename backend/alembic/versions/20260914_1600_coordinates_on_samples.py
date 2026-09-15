"""Coordinates on a date, and the two per-project settings that govern them.

Revision ID: a4d21f7e9c36
Revises: f3b81c60d5a7
Created: 2026-09-14 16:00:00.000000+00:00

Where a date came from, in WGS84 decimal degrees, on both sample tables --
the project's collection and an analysis's own copy -- so that deploying stays
the field-for-field copy it is today. See
docs/SPATIAL_METADATA_AND_DISPERSAL_PLAN.md §4.

The pair mirrors `delta_r` / `delta_r_error` exactly, and for the same reason:
both halves are one fact about where the sample sat, they are shared by every
date from one site, and half of one is not usable. The schemas refuse a
latitude without a longitude the way they already refuse a ΔR without its
error.

**No `sites` table.** A site is derived, not stored: dates sharing a
coordinate are one site, and inside a modelled analysis a site is the model
element its dates belong to. A first-class Site entity is the tidier model and
stays an open decision, but it would fork the schema much further than this
for something the model tree and the collection already express.

**No elevation, no coordinate uncertainty, no CRS column.** WGS84 decimal
degrees only. A file carrying anything else is refused by name in the importer
rather than assumed -- including degrees-minutes-seconds, where guessing
between 54°30' and 54.30 is a 30-arc-minute error nothing downstream would
catch.

The two `projects` columns are here rather than in a migration of their own
because they exist to govern these four. Site coordinates are restricted data
under many heritage bodies', landowners' and Indigenous data-governance
frameworks, so `coordinate_precision` decides how precisely a coordinate may
LEAVE the app (every export, every rendered figure, and nothing else -- what is
stored stays full precision), and `map_tiles_enabled` gates the one remaining
disclosure a map makes, the raster tile request that tells a tile server which
square of the earth is being looked at. Both carry a server default because
they are NOT NULL on tables that already have rows; both default to the
non-disclosing choice, except precision, where 'full' is the non-surprising
one and rounding is the owner's deliberate act.

Everything here is additive, and no existing row changes meaning: an analysis
saved before this migration reruns identically, which is what
tests/test_migrations.py enforces.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a4d21f7e9c36'
down_revision: Union[str, None] = 'f3b81c60d5a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ('radiocarbon_samples', 'project_samples'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('latitude', sa.Float(), nullable=True))
            batch_op.add_column(sa.Column('longitude', sa.Float(), nullable=True))

    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('coordinate_precision', sa.String(),
                                      nullable=False, server_default='full'))
        batch_op.add_column(sa.Column('map_tiles_enabled', sa.Boolean(),
                                      nullable=False, server_default=sa.false()))


def downgrade() -> None:
    # Reversible, and it drops only the new columns: every project, analysis
    # and result survives it, minus the coordinates entered since the upgrade.
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_column('map_tiles_enabled')
        batch_op.drop_column('coordinate_precision')

    for table in ('project_samples', 'radiocarbon_samples'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('longitude')
            batch_op.drop_column('latitude')
