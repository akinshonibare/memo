"""faith: what you're reading is yours to set; readings become passages you read

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-29

ADR 0042, revised. The fixed Genesis-to-Revelation plan goes: Akin sets what he's
reading, and logs what he read.

- users.bible_reading: what you're reading now, a passage (JSONB, the same shape as a
  passage derivation's payload), or NULL.
- derivation kind 'reading': a passage you read, on a day (payload: the passage and
  `read_on`). No note, like a logged habit day. The weekly review and the Bible habit
  count these.
- faith_readings is converted and dropped: each row's plan position is turned back
  into the chapters it stood for (the plan's arithmetic, frozen here so the migration
  never depends on app code), and becomes a 'reading' on the local day it was marked.

Downgrade restores the empty table, and deletes readings and what you set: a reading
of any passage has no position in a plan to go back to.
"""

import json
import uuid
from collections.abc import Sequence
from zoneinfo import ZoneInfo

import pythonbible as bible
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "memo_app"
TENANT = "NULLIF(current_setting('app.user_id', true), '')::uuid"
SYSTEM = "current_setting('app.bypass_rls', true) = 'on'"

KINDS_BEFORE = "'todo', 'event', 'idea', 'mention', 'prayer', 'passage', 'activity'"
KINDS_AFTER = f"{KINDS_BEFORE}, 'reading'"

USFM = (
	"GEN", "EXO", "LEV", "NUM", "DEU", "JOS", "JDG", "RUT", "1SA", "2SA",
	"1KI", "2KI", "1CH", "2CH", "EZR", "NEH", "EST", "JOB", "PSA", "PRO",
	"ECC", "SNG", "ISA", "JER", "LAM", "EZK", "DAN", "HOS", "JOL", "AMO",
	"OBA", "JON", "MIC", "NAM", "HAB", "ZEP", "HAG", "ZEC", "MAL",
	"MAT", "MRK", "LUK", "JHN", "ACT", "ROM", "1CO", "2CO", "GAL", "EPH",
	"PHP", "COL", "1TH", "2TH", "1TI", "2TI", "TIT", "PHM", "HEB", "JAS",
	"1PE", "2PE", "1JN", "2JN", "3JN", "JUD", "REV",
)  # fmt: skip


def _old_plan() -> list[tuple[int, int, int]]:
	"""The plan as 0016 shipped it: (book number, first chapter, last chapter)."""
	books = range(1, 67)
	counts = {b: int(bible.get_number_of_chapters(bible.Book(b))) for b in books}
	per_day = sum(counts.values()) / 365
	out: list[tuple[int, int, int]] = []
	for b in books:
		n = counts[b]
		parts = max(1, round(n / per_day))
		start = 1
		for part in range(parts):
			size = n // parts + (1 if part < n % parts else 0)
			out.append((b, start, start + size - 1))
			start += size
	return out


def _ref(book: int, first: int, last: int) -> str:
	title = str(bible.Book(book).title)
	if int(bible.get_number_of_chapters(bible.Book(book))) == 1:
		return title
	if book == 19 and first == last:
		title = "Psalm"
	return f"{title} {first}" if first == last else f"{title} {first}–{last}"


def upgrade() -> None:
	op.add_column("users", sa.Column("bible_reading", JSONB(), nullable=True))
	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_kind")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_kind "
		f"CHECK (kind IN ({KINDS_AFTER}))"
	)

	bind = op.get_bind()
	plan = _old_plan()
	rows = bind.execute(
		sa.text(
			"SELECT r.user_id, r.position, r.read_at, u.timezone "
			"FROM faith_readings r JOIN users u ON u.id = r.user_id "
			"WHERE r.plan = 'canonical' ORDER BY r.read_at"
		)
	).all()
	for user_id, position, read_at, timezone in rows:
		if not 0 <= position < len(plan):
			continue
		book, first, last = plan[position]
		try:
			zone = ZoneInfo(timezone)
		except (KeyError, ValueError):
			zone = ZoneInfo("UTC")
		payload = {
			"ref": _ref(book, first, last),
			"book": USFM[book - 1],
			"chapter": first,
			"verse": None,
			"end_chapter": last,
			"end_verse": None,
			"read_on": read_at.astimezone(zone).date().isoformat(),
		}
		bind.execute(
			sa.text(
				"INSERT INTO derivations (id, user_id, capture_id, kind, payload, "
				"status, extractor_version, reviewed_at, created_at) VALUES "
				"(:id, :user_id, NULL, 'reading', CAST(:payload AS jsonb), "
				"'user_created', 'user', :at, :at)"
			),
			{
				"id": uuid.uuid4(),
				"user_id": user_id,
				"payload": json.dumps(payload),
				"at": read_at,
			},
		)
	op.drop_table("faith_readings")


def downgrade() -> None:
	op.create_table(
		"faith_readings",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column(
			"user_id",
			sa.Uuid(),
			sa.ForeignKey("users.id", ondelete="CASCADE"),
			nullable=False,
		),
		sa.Column("plan", sa.String(32), nullable=False),
		sa.Column("cycle", sa.Integer(), nullable=False, server_default="1"),
		sa.Column("position", sa.Integer(), nullable=False),
		sa.Column(
			"read_at",
			sa.DateTime(timezone=True),
			nullable=False,
			server_default=sa.func.now(),
		),
		sa.UniqueConstraint(
			"user_id", "plan", "cycle", "position", name="uq_faith_readings_position"
		),
		sa.CheckConstraint("position >= 0", name="ck_faith_readings_position"),
		sa.CheckConstraint("cycle >= 1", name="ck_faith_readings_cycle"),
	)
	op.create_index(
		"ix_faith_readings_user_read", "faith_readings", ["user_id", "read_at"]
	)
	op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON faith_readings TO {APP_ROLE}")
	op.execute("ALTER TABLE faith_readings ENABLE ROW LEVEL SECURITY")
	predicate = f"(user_id = {TENANT} OR {SYSTEM})"
	op.execute(
		f"CREATE POLICY faith_readings_tenant ON faith_readings "
		f"USING {predicate} WITH CHECK {predicate}"
	)
	op.execute("DELETE FROM derivations WHERE kind = 'reading'")
	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_kind")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_kind "
		f"CHECK (kind IN ({KINDS_BEFORE}))"
	)
	op.drop_column("users", "bible_reading")
