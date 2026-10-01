"""summaries on an analysis

Revision ID: 5e2b9c71d4a8
Revises: b7c4e0a13d92
Created: 2026-10-01 12:00:00.000000+00:00

Summaries of an analysis's dates taken as a set: for now a kernel density
estimate of how the dated events are spread through time (services/kde.py).
A function of every date at once, so it has nowhere to sit on an
analysis_results row.

Nullable and with no backfill, as `intervals` was: NULL means the run was
never asked for one (the option is opt-in), and producing one for an existing
analysis is what its Re-run button is for.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5e2b9c71d4a8'
down_revision: Union[str, None] = 'b7c4e0a13d92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('analyses', sa.Column('summaries', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('analyses', 'summaries')
