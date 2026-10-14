"""influence report on an analysis

Revision ID: 4c8d1e6f2a95
Revises: 9b4e2c7a1f30
Created: 2026-10-14 12:00:00.000000+00:00

The last influence run's report (models.Analysis.influence). Null until an
analysis is tested for influence.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '4c8d1e6f2a95'
down_revision: Union[str, None] = '9b4e2c7a1f30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('analyses', sa.Column('influence', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('analyses') as batch:
        batch.drop_column('influence')
