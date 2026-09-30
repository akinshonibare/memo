"""titled names: people memo met in notes, named as a page should show them

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-30

ADR 0060. Data only: every person's name and aliases are titled the way the people
stage now names someone it meets in a note ("tunde bakare" -> "Tunde Bakare"). A word
with a capital in it is left alone ("McKenzie", "DJ"), as is a particle inside a name
("van", "de la").

The function is copied here, frozen, rather than imported: a migration must do tomorrow
what it did today, whatever services/people.py becomes.

Downgrade changes nothing: the lower case it replaced was how a transcript happened
to spell the name, not something anyone chose, and nothing records it to go back to.
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PARTICLES = frozenset(
	{"al", "bin", "da", "de", "del", "della", "der", "di", "du", "ibn", "la", "le"}
	| {"van", "von"}
)


def _capitalise(word: str) -> str:
	done = []
	for part in re.split(r"(-)", word):
		head, sep, tail = part.partition("'")
		if sep and len(head) == 1 and tail:
			done.append(head.upper() + sep + tail[:1].upper() + tail[1:])
		else:
			done.append(part[:1].upper() + part[1:])
	return "".join(done)


def _titled(name: str) -> str:
	out = []
	for i, word in enumerate(name.split()):
		if word != word.lower() or (i > 0 and word in _PARTICLES):
			out.append(word)
		else:
			out.append(_capitalise(word))
	return " ".join(out)


def upgrade() -> None:
	bind = op.get_bind()
	# The table owner runs migrations, and RLS isn't forced on people: it sees them all.
	rows = bind.execute(sa.text("SELECT id, name, aliases FROM people")).all()
	for person_id, name, aliases in rows:
		new_name = _titled(name)
		new_aliases = [_titled(a) for a in aliases or []]
		if new_name == name and new_aliases == list(aliases or []):
			continue
		bind.execute(
			sa.text("UPDATE people SET name = :n, aliases = :a WHERE id = :id"),
			{"n": new_name, "a": new_aliases, "id": person_id},
		)


def downgrade() -> None:
	pass
