"""notes with no words, failed only for having no words, are ready

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-29

A voice note where nothing was said used to fail at extraction ("nothing to extract:
the capture has no text") and show as an error with a Retry that could never succeed.
The pipeline now skips the model calls for such a note and marks it ready. This fixes
the ones that already failed that way.

Deliberately narrow: only a failed capture whose text is empty AND whose failure is one
of the three no-text messages. Any other failure is a real one and stays visible.

Downgrade does nothing. Which rows this changed is not recorded, and putting them back
into a failed state would be restoring a bug.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	op.execute(
		"""
		UPDATE captures SET status = 'ready', failure_reason = NULL
		WHERE status = 'failed'
			AND (text IS NULL OR btrim(text) = '')
			AND failure_reason IN (
				'extract: nothing to extract: the capture has no text',
				'people: nothing to read for people: the capture has no text',
				'embed: nothing to embed: the capture has no text'
			)
		"""
	)


def downgrade() -> None:
	pass
