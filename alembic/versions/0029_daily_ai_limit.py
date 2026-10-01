"""daily ai limit: the most memo spends on one person's AI in a day

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-01

ADR 0063. Additive: `users.daily_ai_limit_usd`, the operator's override of the default
(DAILY_AI_LIMIT_USD) for one person. NULL means the default; 0 means no limit; never
negative (a CHECK).

Downgrade drops it; everyone is back on the default.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	op.add_column(
		"users", sa.Column("daily_ai_limit_usd", sa.Numeric(10, 4), nullable=True)
	)
	op.create_check_constraint(
		"ck_users_daily_ai_limit",
		"users",
		"daily_ai_limit_usd IS NULL OR daily_ai_limit_usd >= 0",
	)


def downgrade() -> None:
	op.drop_constraint("ck_users_daily_ai_limit", "users", type_="check")
	op.drop_column("users", "daily_ai_limit_usd")
