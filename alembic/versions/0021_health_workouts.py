"""health workouts: one activity per Apple Health workout

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-29

ADR 0050. Additive: a partial unique index, so a workout read from Apple Health is filed
once per user however many syncs run at the same moment. Workouts are ordinary
`activity` derivations with no note, told apart by `payload.health_id` (the workout's
HealthKit UUID); nothing else changes shape.

Downgrade drops the index only. Workouts already synced stay, as activities.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	op.create_index(
		"uq_derivations_health_workout",
		"derivations",
		["user_id", sa.text("(payload ->> 'health_id')")],
		unique=True,
		postgresql_where=sa.text(
			"kind = 'activity' AND (payload ->> 'health_id') IS NOT NULL"
		),
	)


def downgrade() -> None:
	op.drop_index("uq_derivations_health_workout", table_name="derivations")
