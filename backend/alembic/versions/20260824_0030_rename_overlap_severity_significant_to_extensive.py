"""rename the top overlap severity from significant to extensive

Revision ID: a4f1c7b2e903
Revises: e680738c22ad
Created: 2026-08-24 00:30:00.000000+00:00

The top overlap band names the case where the model can say the LEAST about
which of two dates came first -- and "significant" reads as a finding to anyone
who has met the word in statistics, which is exactly backwards. It is now
"extensive"; see services/intervals.SEVERITY_TOP.

This has to be a data migration rather than a display-only rename because the
word is PERSISTED: analyses.intervals is a JSON column holding one severity
string per difference, and api/results.py and services/pdf_report.py both print
that stored string verbatim into the CSV and the PDF. Renaming only the code
would leave every already-solved analysis reporting "significant" on screen and
in its exports while new runs said "extensive", and would leave the frontend's
severity->shade map with no entry for the old word.

Rewrites the stored JSON in place rather than clearing it: intervals are opt-in
and expensive to recompute (they need the whole model re-solved), so dropping
them would silently cost users a result to fix a spelling.
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa


revision: str = 'a4f1c7b2e903'
down_revision: Union[str, None] = 'e680738c22ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rewrite(old: str, new: str) -> None:
    """Swap one severity word inside every stored intervals blob.

    Done in Python rather than as a JSON-path UPDATE because this app runs on
    both SQLite and Postgres, whose JSON functions do not agree; the number of
    analyses carrying intervals is small (they are opt-in), so reading and
    rewriting them is cheap and portable.
    """
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, intervals FROM analyses WHERE intervals IS NOT NULL")
    ).fetchall()

    for row_id, blob in rows:
        if blob is None:
            continue
        # SQLite hands back a string, Postgres a decoded object.
        data = json.loads(blob) if isinstance(blob, str) else blob
        differences = data.get('differences') or []
        touched = False
        for record in differences:
            if record.get('severity') == old:
                record['severity'] = new
                touched = True
        if not touched:
            continue
        connection.execute(
            sa.text("UPDATE analyses SET intervals = :blob WHERE id = :id"),
            {'blob': json.dumps(data), 'id': row_id},
        )


def upgrade() -> None:
    _rewrite('significant', 'extensive')


def downgrade() -> None:
    _rewrite('extensive', 'significant')
