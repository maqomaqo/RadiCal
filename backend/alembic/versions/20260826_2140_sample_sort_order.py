"""A date's place in its analysis's reading order.

Revision ID: b93c5d0a17e4
Revises: a4f1c7b2e903
Created: 2026-08-26 21:40:00.000000+00:00

The order dates are listed in is not cosmetic: within a phase it is the order
they appear in the results table, the stacked plot, and both exports (see
services/model_tree.flatten_structure). Until now that order was whatever the
rows happened to come back in, since the measurements relationship had no
ORDER BY -- which reads as insertion order on a small table and is not
promised by the database at all. On Postgres it can change once rows are
updated.

Now that data entry offers an explicit sort, the chosen order needs somewhere
to live, and reads need a total order to come back in.

Backfilled by created_at so existing analyses keep the order they are
currently displayed in rather than being silently rearranged by this
migration. Ties within an analysis -- rows written in the same request, which
is most of them -- fall back to id, matching the relationship's own ordering.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b93c5d0a17e4'
down_revision: Union[str, None] = 'a4f1c7b2e903'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('radiocarbon_samples', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0')
        )

    # Number each analysis's dates from 0 in the order they are read in today,
    # so nothing visibly moves the first time this runs. Written as a
    # correlated subquery rather than a window function in UPDATE ... FROM,
    # which SQLite does not support -- the tables here are small enough that
    # the difference does not matter.
    op.execute(
        """
        UPDATE radiocarbon_samples AS target
        SET sort_order = (
            SELECT COUNT(*)
            FROM radiocarbon_samples AS earlier
            WHERE earlier.analysis_id = target.analysis_id
              AND (
                    earlier.created_at < target.created_at
                 OR (earlier.created_at = target.created_at AND earlier.id < target.id)
              )
        )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table('radiocarbon_samples', schema=None) as batch_op:
        batch_op.drop_column('sort_order')
