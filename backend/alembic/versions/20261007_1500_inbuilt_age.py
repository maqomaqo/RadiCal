"""inbuilt age

Revision ID: 3d7a91c5e2f4
Revises: 8c1f4a2e6b90
Created: 2026-10-07 15:00:00.000000+00:00

Each date's inbuilt-age allowance (feature spec M1): how much older than its
event the dated material may be, and the shape of that allowance -- uniform up
to a maximum, or exponential about a mean. On a collection's date and on an
analysis's copy of it, as ring offsets are, since it is a fact about the
sample. Nullable with no backfill: no allowance is what every existing date
was run with.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3d7a91c5e2f4'
down_revision: Union[str, None] = '8c1f4a2e6b90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ('radiocarbon_samples', 'project_samples')


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column('inbuilt_years', sa.Float(), nullable=True))
        op.add_column(table, sa.Column('inbuilt_shape', sa.String(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column('inbuilt_shape')
            batch.drop_column('inbuilt_years')
