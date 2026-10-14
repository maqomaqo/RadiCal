"""model templates

Revision ID: 6e1f3b8d2c47
Revises: 4c8d1e6f2a95
Created: 2026-10-15 12:00:00.000000+00:00

Saved model structures, private to each account (models.ModelTemplate).
Empty to begin with.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '6e1f3b8d2c47'
down_revision: Union[str, None] = '4c8d1e6f2a95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'model_templates',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('owner_id', sa.String(),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('model_tree', sa.JSON(), nullable=False),
        sa.Column('bayesian_config', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_model_templates_owner_id', 'model_templates', ['owner_id'])


def downgrade() -> None:
    op.drop_index('ix_model_templates_owner_id', table_name='model_templates')
    op.drop_table('model_templates')
