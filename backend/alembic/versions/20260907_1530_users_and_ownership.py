"""Accounts, and who each project, analysis and job belongs to.

Revision ID: c7e2a9f4b108
Revises: b93c5d0a17e4
Created: 2026-09-07 15:30:00.000000+00:00

Until now the app had no users: anyone reaching it could read, change or
delete anything in it. That was a deliberate choice for a single archaeologist
running it on their own machine, and it stops being one the moment the app is
deployed anywhere more than one person can reach.

This adds the users table and an owner on the three things a user owns:
projects, analyses and jobs. Everything else hangs off one of those --
ProjectSample belongs to a project, RadiocarbonSample and AnalysisResult to an
analysis -- so those are the only three boundaries there are.

**The owner columns are nullable, and that is not laziness.** Every row that
exists when this runs predates the users table, and there is nobody yet to
attribute it to: a migration runs before any account is created. Rather than
inventing a placeholder user here, the columns start null and the FIRST
account registered adopts every ownerless project and analysis
(api/auth._adopt_existing_work). That is the earliest moment the question
"whose is this?" has an answer.

Until that first registration, ownerless rows are visible to nobody -- which
is the safe direction to fail, and is why adoption happens on the very first
account rather than being an administrative step someone has to remember.

Calibration curves get `created_by` rather than an owner. They stay SHARED --
reference data, like the three built-in curves -- and the column exists so
that editing, archiving and deleting can be kept to whoever imported one. See
models.StoredCalibrationCurve.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7e2a9f4b108'
down_revision: Union[str, None] = 'b93c5d0a17e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.String(), nullable=False),
        # Unique, and stored lowercased by the application
        # (services/auth.normalise_email): two accounts differing only in case
        # are two people who each think they are the only one.
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('password_hash', sa.String(), nullable=False),
        sa.Column('display_name', sa.String(), nullable=True),
        sa.Column('is_first_account', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    # Indexed, because every list endpoint now filters on it -- it is in the
    # WHERE clause of the most common query the app makes.
    for table, column in (
        ('projects', 'owner_id'),
        ('analyses', 'owner_id'),
        ('jobs', 'owner_id'),
        ('calibration_curves', 'created_by'),
    ):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column(column, sa.String(), nullable=True))
            batch_op.create_index(op.f(f'ix_{table}_{column}'), [column], unique=False)
            # Named explicitly: SQLite cannot drop an unnamed constraint, and
            # batch_alter_table rebuilds the table to add one, so a name is
            # what makes downgrade() below possible at all.
            batch_op.create_foreign_key(
                f'fk_{table}_{column}_users', 'users', [column], ['id'])


def downgrade() -> None:
    # Reversible, unlike the baseline's downgrade: this drops ownership and the
    # accounts, not anybody's work. Every project, analysis and result survives
    # it -- they simply become visible to everyone again, which is exactly the
    # state the database was in before this ran.
    for table, column in (
        ('calibration_curves', 'created_by'),
        ('jobs', 'owner_id'),
        ('analyses', 'owner_id'),
        ('projects', 'owner_id'),
    ):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(f'fk_{table}_{column}_users', type_='foreignkey')
            batch_op.drop_index(op.f(f'ix_{table}_{column}'))
            batch_op.drop_column(column)

    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
