"""drop reservoir age

Revision ID: 9a4e2c7b1d38
Revises: 6e1f3b8d2c47
Created: 2026-10-28 12:00:00.000000+00:00

Removes reservoir_age from a collection's dates and an analysis's copies of
them. It predates delta_r, which replaced it, and no calibration ever read it.
marine_reservoir_points keeps its own reservoir_age: that is the marine
database's field, not a date's.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9a4e2c7b1d38'
down_revision: Union[str, None] = '6e1f3b8d2c47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ('radiocarbon_samples', 'project_samples')


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column('reservoir_age')


def downgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column('reservoir_age', sa.Float(), nullable=True))
