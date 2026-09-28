"""users.display_name and users.voice: a profile, for the assistant

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-28

The name the assistant uses for the person (in Ask and spoken answers), and which
Aura-2 voice reads answers aloud. Both nullable: no name means the assistant says
"you"; no voice means the server default. Typed columns rather than a preferences
JSON blob - two fields do not justify giving up constraints.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	op.add_column("users", sa.Column("display_name", sa.String(60), nullable=True))
	op.add_column("users", sa.Column("voice", sa.String(64), nullable=True))


def downgrade() -> None:
	op.drop_column("users", "voice")
	op.drop_column("users", "display_name")
