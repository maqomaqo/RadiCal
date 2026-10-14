"""simulations

Revision ID: 7e1a5c3b9d24
Revises: 4c8d1e6f2a95
Created: 2026-10-14 18:00:00.000000+00:00

Simulated replicates of an analysis (models.Simulation), and the link from
each replicate analysis to its simulation. Empty to begin with.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7e1a5c3b9d24'
down_revision: Union[str, None] = '4c8d1e6f2a95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'simulations',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('owner_id', sa.String(),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', sa.String(),
                  sa.ForeignKey('projects.id', ondelete='SET NULL'), nullable=True),
        sa.Column('source_analysis_id', sa.String(),
                  sa.ForeignKey('analyses.id', ondelete='SET NULL'), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('seed', sa.Integer(), nullable=False),
        sa.Column('replicates', sa.Integer(), nullable=False),
        sa.Column('scatter', sa.Boolean(), nullable=False),
        sa.Column('inbuilt', sa.Boolean(), nullable=False),
        sa.Column('windows', sa.JSON(), nullable=False),
        sa.Column('truth_key', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_simulations_owner_id', 'simulations', ['owner_id'])
    op.create_index('ix_simulations_source_analysis_id', 'simulations', ['source_analysis_id'])
    with op.batch_alter_table('analyses') as batch:
        batch.add_column(sa.Column('simulation_id', sa.String(), nullable=True))
        batch.add_column(sa.Column('replicate', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_analyses_simulation_id', 'simulations',
                                 ['simulation_id'], ['id'], ondelete='SET NULL')
        batch.create_index('ix_analyses_simulation_id', ['simulation_id'])


def downgrade() -> None:
    with op.batch_alter_table('analyses') as batch:
        batch.drop_index('ix_analyses_simulation_id')
        batch.drop_constraint('fk_analyses_simulation_id', type_='foreignkey')
        batch.drop_column('replicate')
        batch.drop_column('simulation_id')
    op.drop_index('ix_simulations_source_analysis_id', table_name='simulations')
    op.drop_index('ix_simulations_owner_id', table_name='simulations')
    op.drop_table('simulations')
