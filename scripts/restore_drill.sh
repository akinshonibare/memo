#!/usr/bin/env bash
# Restore drill (issue #47). Two modes:
#   SCRATCH_URL=<restored sibling>  scripts/restore_drill.sh   # verify a Railway PITR restore
#   scripts/restore_drill.sh                                    # local: dump dev db, restore into
#                                                               # memo_drill, verify (compose)
# Exits non-zero if the restored database is not at alembic head or has no captures table.
set -euo pipefail

cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a

strip_driver() { echo "$1" | sed 's#postgresql+asyncpg://#postgresql://#'; }

if [ -z "${SCRATCH_URL:-}" ]; then
	# Everything runs inside the db container, so use its own port, not the host's.
	BASE="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5432"
	SOURCE_URL="${BASE}/${POSTGRES_DB}"
	SERVER_URL="${BASE}/postgres"
	SCRATCH_URL="${BASE}/memo_drill"
	echo "local drill: dump ${POSTGRES_DB} -> restore into memo_drill (in-container)"
	docker compose exec -T db psql "$SERVER_URL" -qc "DROP DATABASE IF EXISTS memo_drill;" -c "CREATE DATABASE memo_drill;"
	docker compose exec -T db pg_dump "$SOURCE_URL" --no-owner --no-privileges \
		| docker compose exec -T db psql -q "$SCRATCH_URL" >/dev/null
	PSQL=(docker compose exec -T db psql "$SCRATCH_URL")
else
	command -v psql >/dev/null || { echo "psql is required for a remote drill"; exit 2; }
	PSQL=(psql "$SCRATCH_URL")
fi

q() { "${PSQL[@]}" -tA -c "$1" | tr -d '[:space:]'; }

expected_head="$(basename -a alembic/versions/*.py | sed -E 's/^([0-9]+)_.*/\1/' | sort | tail -1)"
actual_head="$(q "SELECT version_num FROM alembic_version;")"
echo "alembic head: expected $expected_head, restored $actual_head"
[ "$actual_head" = "$expected_head" ] || { echo "FAIL: restored schema is not at head"; exit 1; }

for t in captures derivations chunks messages llm_calls recordings; do
	echo "$t: $(q "SELECT count(*) FROM $t;") rows"
done
echo "newest capture: $(q "SELECT coalesce(max(created_at)::text, 'none') FROM captures;")"
echo "OK: restored database is readable and at head"
