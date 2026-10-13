"""personal access tokens

Revision ID: 9b4e2c7a1f30
Revises: 3d7a91c5e2f4
Created: 2026-10-13 12:00:00.000000+00:00

Long-lived tokens for scripts (models.PersonalAccessToken). Empty to begin
with: a user makes their first from the Account page.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9b4e2c7a1f30'
down_revision: Union[str, None] = '3d7a91c5e2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'personal_access_tokens',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('token_hash', sa.String(), nullable=False),
        sa.Column('prefix', sa.String(), nullable=False),
        sa.Column('scope', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_personal_access_tokens_user_id', 'personal_access_tokens',
                    ['user_id'])
    op.create_index('ix_personal_access_tokens_token_hash', 'personal_access_tokens',
                    ['token_hash'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_personal_access_tokens_token_hash', table_name='personal_access_tokens')
    op.drop_index('ix_personal_access_tokens_user_id', table_name='personal_access_tokens')
    op.drop_table('personal_access_tokens')
