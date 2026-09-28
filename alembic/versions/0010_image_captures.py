"""photo captures: kind 'image', status 'describing'

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-28

ADR 0024. A photo is a capture: uploaded to the bucket through the same recordings
row a voice note uses, turned into text by a `describe` stage, then extracted and
embedded like any other. Only the two CHECK constraints need to know about it.
Downgrade refuses silently-broken rows by deleting photo captures first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES_OLD = "'pending','transcribing','extracting','embedding','ready','failed'"
_STATUSES_NEW = (
	"'pending','transcribing','describing','extracting','embedding','ready','failed'"
)


def upgrade() -> None:
	op.execute("ALTER TABLE captures DROP CONSTRAINT ck_captures_kind")
	op.execute(
		"ALTER TABLE captures ADD CONSTRAINT ck_captures_kind "
		"CHECK (kind IN ('text','audio','image'))"
	)
	op.execute("ALTER TABLE captures DROP CONSTRAINT ck_captures_status")
	op.execute(
		"ALTER TABLE captures ADD CONSTRAINT ck_captures_status "
		f"CHECK (status IN ({_STATUSES_NEW}))"
	)


def downgrade() -> None:
	op.execute("DELETE FROM captures WHERE kind = 'image'")
	op.execute("ALTER TABLE captures DROP CONSTRAINT ck_captures_status")
	op.execute(
		"ALTER TABLE captures ADD CONSTRAINT ck_captures_status "
		f"CHECK (status IN ({_STATUSES_OLD}))"
	)
	op.execute("ALTER TABLE captures DROP CONSTRAINT ck_captures_kind")
	op.execute(
		"ALTER TABLE captures ADD CONSTRAINT ck_captures_kind "
		"CHECK (kind IN ('text','audio'))"
	)
