"""deleting a note deletes everything taken from it

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-29

ADR 0053, superseding ADR 0022's "keep what the user got out of it". The link from an
item to its note becomes ON DELETE CASCADE: deleting a note deletes its todos, events,
ideas, mentions, prayers, passages, activities and suggestions, and through their own
cascades their chunks, citations and resurfacings. In the database, so every path that
deletes a note - the route, a user's deletion, a raw DELETE - does the same.

Items already detached by an earlier delete (capture_id NULL) are left as they are:
there is no note left to say what they came from. `capture_id` stays nullable for them.

Downgrade restores ON DELETE SET NULL (capture_id). Items deleted meanwhile stay
deleted.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _link(action: str) -> None:
	op.execute(
		"ALTER TABLE derivations DROP CONSTRAINT fk_derivations_capture_same_user"
	)
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT fk_derivations_capture_same_user "
		"FOREIGN KEY (capture_id, user_id) REFERENCES captures (id, user_id) "
		f"ON DELETE {action}"
	)


def upgrade() -> None:
	_link("CASCADE")


def downgrade() -> None:
	_link("SET NULL (capture_id)")
