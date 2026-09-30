"""person lately: a line on what's been going on with each person

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-30

ADR 0062. Additive, all nullable: `people.lately` (the line), and what it was written
from, so the worker's sweep can tell when it's stale: `lately_at` (when), `lately_count`
(live mentions then), `lately_tone`, `lately_version` (the prompt).

Downgrade drops them; the lines are rewritten from the notes if it comes back.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
	("lately", sa.Text()),
	("lately_at", sa.DateTime(timezone=True)),
	("lately_count", sa.Integer()),
	("lately_tone", sa.String(16)),
	("lately_version", sa.String(64)),
)


def upgrade() -> None:
	for name, type_ in COLUMNS:
		op.add_column("people", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
	for name, _ in reversed(COLUMNS):
		op.drop_column("people", name)
