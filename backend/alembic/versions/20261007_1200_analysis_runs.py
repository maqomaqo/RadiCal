"""analysis runs

Revision ID: 8c1f4a2e6b90
Revises: 5e2b9c71d4a8
Created: 2026-10-07 12:00:00.000000+00:00

The last few completed runs of each analysis (models.AnalysisRun), so a
re-run after a change to the code can be compared with the run before it
instead of overwriting it. Empty to begin with: an analysis gains its first
entry the next time it runs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8c1f4a2e6b90'
down_revision: Union[str, None] = '5e2b9c71d4a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'analysis_runs',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('analysis_id', sa.String(),
                  sa.ForeignKey('analyses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('app_commit', sa.String(), nullable=True),
        sa.Column('inputs_hash', sa.String(), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
    )
    op.create_index('ix_analysis_runs_analysis_id', 'analysis_runs', ['analysis_id'])


def downgrade() -> None:
    op.drop_index('ix_analysis_runs_analysis_id', table_name='analysis_runs')
    op.drop_table('analysis_runs')
