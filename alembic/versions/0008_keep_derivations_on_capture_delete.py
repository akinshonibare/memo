"""deleting a capture keeps its derivations; citations follow their chunk

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-28

ADR 0022 (#55, #56). Deleting a note removes the note - its text, audio and text
chunks - but keeps the todos, events and ideas extracted from it, with capture_id set
to NULL. Both foreign keys are composite (capture_id, user_id), so a plain SET NULL
would null user_id as well and drop the row out of its tenant; Postgres 15+'s
column-list form nulls capture_id only. SQLAlchemy's DDL compiler rejects that form,
hence raw SQL.

A chunk must belong to a capture or a derivation: the CHECK makes an orphaned text
chunk (one that would keep answering questions about a deleted note) impossible at
the data layer. A BEFORE DELETE trigger on captures removes the note's text chunks
first, so the SET NULL only ever reaches derivation chunks - on every deletion path,
including the cascade from deleting a user, where the order of the two foreign-key
actions (capture SET NULL vs user CASCADE) is not ours to choose.

Citations now cascade from their chunk: a deleted note's chunk takes its citations
with it, instead of blocking the delete (#55).

Downgrade deletes the derivations and chunks that outlived their capture - there is
no capture to point them back at.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	op.execute("ALTER TABLE derivations ALTER COLUMN capture_id DROP NOT NULL")
	op.execute(
		"ALTER TABLE derivations DROP CONSTRAINT fk_derivations_capture_same_user"
	)
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT fk_derivations_capture_same_user "
		"FOREIGN KEY (capture_id, user_id) REFERENCES captures (id, user_id) "
		"ON DELETE SET NULL (capture_id)"
	)

	op.execute("ALTER TABLE chunks ALTER COLUMN capture_id DROP NOT NULL")
	op.execute("ALTER TABLE chunks DROP CONSTRAINT fk_chunks_capture_same_user")
	op.execute(
		"ALTER TABLE chunks ADD CONSTRAINT fk_chunks_capture_same_user "
		"FOREIGN KEY (capture_id, user_id) REFERENCES captures (id, user_id) "
		"ON DELETE SET NULL (capture_id)"
	)
	op.execute(
		"ALTER TABLE chunks ADD CONSTRAINT ck_chunks_has_owner "
		"CHECK (capture_id IS NOT NULL OR derivation_id IS NOT NULL)"
	)
	# SECURITY INVOKER (the default): runs with the deleting role's rights and under
	# its RLS policy, so it can only ever remove chunks the caller could see anyway.
	op.execute(
		"""
		CREATE FUNCTION delete_capture_text_chunks() RETURNS trigger
		LANGUAGE plpgsql AS $$
		BEGIN
			DELETE FROM chunks
			WHERE capture_id = OLD.id AND user_id = OLD.user_id
				AND derivation_id IS NULL;
			RETURN OLD;
		END;
		$$
		"""
	)
	op.execute(
		"CREATE TRIGGER captures_delete_text_chunks BEFORE DELETE ON captures "
		"FOR EACH ROW EXECUTE FUNCTION delete_capture_text_chunks()"
	)

	op.execute(
		"ALTER TABLE message_citations "
		"DROP CONSTRAINT fk_message_citations_chunk_same_user"
	)
	op.execute(
		"ALTER TABLE message_citations ADD CONSTRAINT "
		"fk_message_citations_chunk_same_user FOREIGN KEY (chunk_id, user_id) "
		"REFERENCES chunks (id, user_id) ON DELETE CASCADE"
	)


def downgrade() -> None:
	op.execute(
		"ALTER TABLE message_citations "
		"DROP CONSTRAINT fk_message_citations_chunk_same_user"
	)
	op.execute("DELETE FROM chunks WHERE capture_id IS NULL")
	op.execute("DELETE FROM derivations WHERE capture_id IS NULL")
	op.execute(
		"ALTER TABLE message_citations ADD CONSTRAINT "
		"fk_message_citations_chunk_same_user FOREIGN KEY (chunk_id, user_id) "
		"REFERENCES chunks (id, user_id)"
	)

	op.execute("DROP TRIGGER captures_delete_text_chunks ON captures")
	op.execute("DROP FUNCTION delete_capture_text_chunks()")
	op.execute("ALTER TABLE chunks DROP CONSTRAINT ck_chunks_has_owner")
	op.execute("ALTER TABLE chunks DROP CONSTRAINT fk_chunks_capture_same_user")
	op.execute(
		"ALTER TABLE chunks ADD CONSTRAINT fk_chunks_capture_same_user "
		"FOREIGN KEY (capture_id, user_id) REFERENCES captures (id, user_id) "
		"ON DELETE CASCADE"
	)
	op.execute("ALTER TABLE chunks ALTER COLUMN capture_id SET NOT NULL")

	op.execute(
		"ALTER TABLE derivations DROP CONSTRAINT fk_derivations_capture_same_user"
	)
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT fk_derivations_capture_same_user "
		"FOREIGN KEY (capture_id, user_id) REFERENCES captures (id, user_id) "
		"ON DELETE CASCADE"
	)
	op.execute("ALTER TABLE derivations ALTER COLUMN capture_id SET NOT NULL")
