"""photo caption: your own words with a photo

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-30

ADR 0056. Additive: `captures.caption`, what you wrote alongside a photo. Kept apart
from `text`, which the describe stage rebuilds (your words, then the description), so
reprocessing a photo never loses or doubles them. Only a photo has one (a CHECK).

Downgrade drops the column; the words stay inside `text`, where describe put them.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	op.add_column("captures", sa.Column("caption", sa.Text(), nullable=True))
	op.create_check_constraint(
		"ck_captures_caption_on_photos",
		"captures",
		"caption IS NULL OR kind = 'image'",
	)


def downgrade() -> None:
	op.drop_constraint("ck_captures_caption_on_photos", "captures", type_="check")
	op.drop_column("captures", "caption")
