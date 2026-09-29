"""habits always on

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-29

ADR 0054, superseding ADR 0043's Settings switch: Habits is on for everyone. Every
account is switched on (the background sweep then reads the last 60 days of notes of
any that had it off), and new accounts start on. The column stays as the operator's
switch; no setting in the app or the API turns it off.

Downgrade restores the default of off. Accounts keep the value they have.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	op.execute("UPDATE users SET habits_enabled = true WHERE NOT habits_enabled")
	op.alter_column("users", "habits_enabled", server_default=sa.true())


def downgrade() -> None:
	op.alter_column("users", "habits_enabled", server_default=sa.false())
