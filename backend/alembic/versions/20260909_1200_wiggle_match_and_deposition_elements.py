"""Ring offsets on a date, and per-element settings on a model element.

Revision ID: f3b81c60d5a7
Revises: c7e2a9f4b108
Created: 2026-09-09 12:00:00.000000+00:00

Two features share one revision because they share one shape: a sequence whose
members are separated by a measured distance on an external axis -- rings for a
wiggle match, depth for an age-depth model. See
docs/AGE_DEPTH_AND_WIGGLE_MATCH_PLAN.md.

`ring_offset` is a NEW column rather than `depth_cm` reused. Rings and depth
are different units running in opposite directions, and overloading one column
would guarantee a sign error in the importers as well as in the solver.
`ring_offset_error` beside it is what makes a V_Sequence -- a gap that was
measured rather than counted -- expressible at all.

`model_elements.params` holds the settings that belong to exactly one kind of
element (a wiggle sequence's gap units, a deposition sequence's rigidity, an
interpolated level's depth). One JSON column rather than five nullable ones
each used by a single element_type, following `analyses.solver` and
`analyses.intervals`: nothing queries across them and they are read whole.

`element_type` is a plain string with no CHECK constraint, so the new element
types need no DDL of their own.

Everything here is additive and nullable, and no existing row changes meaning:
an analysis saved before this migration reruns identically, which is what
tests/test_migrations.py enforces.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3b81c60d5a7'
down_revision: Union[str, None] = 'c7e2a9f4b108'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('radiocarbon_samples', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ring_offset', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('ring_offset_error', sa.Float(), nullable=True))

    with op.batch_alter_table('project_samples', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ring_offset', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('ring_offset_error', sa.Float(), nullable=True))

    with op.batch_alter_table('model_elements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('params', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('model_elements', schema=None) as batch_op:
        batch_op.drop_column('params')

    with op.batch_alter_table('project_samples', schema=None) as batch_op:
        batch_op.drop_column('ring_offset_error')
        batch_op.drop_column('ring_offset')

    with op.batch_alter_table('radiocarbon_samples', schema=None) as batch_op:
        batch_op.drop_column('ring_offset_error')
        batch_op.drop_column('ring_offset')
