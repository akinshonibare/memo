"""captures.words: when each word of a voice note was said

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-29

ADR 0039. Deepgram already returns a start and end time for every word; until now the
transcribe stage kept only the text and threw the timings away. Keeping them lets the
app light up each word as the recording plays, and jump to a word when it's tapped.

Stored on captures next to `text`, as JSONB [[start_ms, end_ms, word], ...], because
the two are one fact: the transcribe stage writes both in the same statement, and a
retry that transcribes again replaces both. A separate words table would make them two
writes that could disagree, and nothing queries into single words.

Nullable with no default and no backfill: NULL means "not a voice note, or transcribed
before words were kept". Filling in old notes would mean paying to transcribe every
recording again for a nicety, so they simply play without following along.

Downgrade drops the column; the text is untouched, so nothing a note says is lost.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	op.add_column(
		"captures",
		sa.Column("words", postgresql.JSONB(), nullable=True),
	)


def downgrade() -> None:
	op.drop_column("captures", "words")
