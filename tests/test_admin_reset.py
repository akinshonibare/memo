"""The operator's reset (ADR 0059): every user's data goes, accounts, sessions, push
tokens and settings stay, and it refuses rather than guess when the schema has moved
on from what it classifies."""

import asyncio
import io
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import urlsplit

import pytest
from botocore.exceptions import ClientError
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from memo.admin import reset as admin
from memo.admin.reset import KEEP, WIPE, Refused, reset
from memo.config import SEED_USER_ID, Settings
from memo.services.storage import S3ObjectStore
from tests.fakes import FakeObjectStore

# A Redis database of the test's own: the reset deletes every arq key it can see, and a
# local worker keeps its queue in database 0.
REDIS_TEST_DB = 13
VECTOR = "[" + ",".join(["0"] * 1024) + "]"
USER_COLUMNS = (
	"id, email, apple_sub, timezone, display_name, voice, auto_accept, faith_enabled, "
	"bible_translation, bible_reading, habits_enabled, morning_push_at"
)
# Kept by the reset, whatever it finds there.
OTHER_REDIS_KEYS = ("ratelimit:ask:someone:29000000", "memo:health-check")


@pytest.fixture
async def redis(settings: Settings) -> AsyncIterator[Redis]:
	url = urlsplit(settings.redis_url)._replace(path=f"/{REDIS_TEST_DB}").geturl()
	client = Redis.from_url(url)
	await client.flushdb()
	yield client
	await client.flushdb()
	await client.aclose()


@pytest.fixture
async def two_users(admin_engine: AsyncEngine) -> list[uuid.UUID]:
	"""The seeded user and one more, each with a row in every table the reset wipes,
	a session, a phone's push token, and settings of their own."""
	other = uuid.uuid4()
	async with admin_engine.begin() as conn:
		await conn.execute(
			text("INSERT INTO users (id, timezone) VALUES (:id, 'UTC')"), {"id": other}
		)
		for n, user in enumerate((SEED_USER_ID, other)):
			await _fill(conn, user, n)
		await conn.execute(
			text(
				"UPDATE users SET timezone = 'Europe/London', faith_enabled = true, "
				"display_name = 'Akin', bible_translation = 'ESV', voice = 'aura-2', "
				"morning_push_at = '07:30', apple_sub = 'apple-seed', "
				"email = 'seed@example.com', "
				'bible_reading = \'{"book": "John", "chapter": 3}\' '
				"WHERE id = :id"
			),
			{"id": SEED_USER_ID},
		)
		await conn.execute(
			text(
				"UPDATE users SET timezone = 'America/New_York', auto_accept = true, "
				"habits_enabled = false WHERE id = :id"
			),
			{"id": other},
		)
	return [SEED_USER_ID, other]


async def _fill(conn: Any, user: uuid.UUID, n: int) -> None:
	ids = {name: uuid.uuid4() for name in ("rec", "cap", "der", "per", "men", "chk")}
	ids |= {name: uuid.uuid4() for name in ("con", "msg", "ses")}
	values = {
		"u": user,
		"v": VECTOR,
		"key": f"{user}/{ids['rec']}.m4a",
		"tok": f"tok-reset-{n}",
		"hash": uuid.uuid4().hex * 2,
		"push": f"ExponentPushToken[{user}]",
		**ids,
	}
	for statement in (
		"INSERT INTO recordings (id, user_id, storage_key, mime) "
		"VALUES (:rec, :u, :key, 'audio/mp4')",
		"INSERT INTO captures (id, user_id, kind, text, status, client_token, "
		"recording_id) VALUES (:cap, :u, 'audio', 'call Tunde', 'ready', :tok, :rec)",
		"INSERT INTO job_runs (id, capture_id, user_id, stage, version, status, "
		"started_at) VALUES (gen_random_uuid(), :cap, :u, 'extract', 'v1', "
		"'succeeded', now())",
		"INSERT INTO people (id, user_id, name) VALUES (:per, :u, 'Tunde')",
		"INSERT INTO derivations (id, user_id, capture_id, kind, payload, status, "
		"extractor_version) VALUES (:der, :u, :cap, 'todo', "
		"'{\"title\": \"Call Tunde\"}', 'confirmed', 'extract-v1')",
		"INSERT INTO derivations (id, user_id, capture_id, kind, payload, status, "
		"extractor_version, person_id) VALUES (:men, :u, :cap, 'mention', "
		"'{\"name\": \"Tunde\"}', 'accepted', 'people-v1', :per)",
		"INSERT INTO chunks (id, user_id, capture_id, ordinal, text, char_start, "
		"char_end, embedding, embedding_model, embedding_dim) VALUES (:chk, :u, "
		":cap, 0, 'call Tunde', 0, 10, CAST(:v AS vector), 'voyage-4', 1024)",
		"INSERT INTO conversations (id, user_id) VALUES (:con, :u)",
		"INSERT INTO messages (id, conversation_id, user_id, role, status) "
		"VALUES (:msg, :con, :u, 'assistant', 'done')",
		"INSERT INTO message_citations (message_id, chunk_id, user_id, rank) "
		"VALUES (:msg, :chk, :u, 1)",
		"INSERT INTO morning_views (id, user_id, local_date, payload) "
		"VALUES (gen_random_uuid(), :u, current_date, '{}')",
		"INSERT INTO resurfacings (id, user_id, derivation_id, shown_on) "
		"VALUES (gen_random_uuid(), :u, :der, current_date)",
		"INSERT INTO habits (id, user_id, key, name, source) "
		"VALUES (gen_random_uuid(), :u, 'run', 'Running', 'activity')",
		"INSERT INTO llm_calls (id, user_id, stage, provider, model, input_tokens, "
		"output_tokens, latency_ms, cost_usd) VALUES (gen_random_uuid(), :u, "
		"'extract', 'anthropic', 'claude-opus-5', 400, 60, 900, 0.01)",
		"INSERT INTO pushes (id, user_id, kind, local_date, status) "
		"VALUES (gen_random_uuid(), :u, 'morning', current_date, 'sent')",
		"INSERT INTO sessions (id, user_id, token_hash, expires_at) "
		"VALUES (:ses, :u, :hash, now() + interval '90 days')",
		"INSERT INTO push_tokens (id, user_id, session_id, token) "
		"VALUES (gen_random_uuid(), :u, :ses, :push)",
	):
		await conn.execute(text(statement), values)


def _bucket(users: list[uuid.UUID]) -> FakeObjectStore:
	store = FakeObjectStore()
	for user in users:
		store.objects[f"{user}/{uuid.uuid4()}.m4a"] = b"audio"
	store.objects["orphan-with-no-row.jpg"] = b"photo"
	return store


async def _queue(redis: Redis, queue_name: str) -> None:
	await redis.zadd(queue_name, {"process_capture:a": 1, "embed_derivation:b": 2})
	await redis.set("arq:job:process_capture:a", b"job")
	await redis.set("arq:job:embed_derivation:b", b"job")
	await redis.set("arq:in-progress:process_capture:a", b"1")
	await redis.set("arq:retry:process_capture:a", b"3")
	await redis.set("arq:result:cron:build_morning_views_task:1", b"done")
	for key in OTHER_REDIS_KEYS:
		await redis.set(key, b"stays")


async def _snapshot(
	engine: AsyncEngine, store: FakeObjectStore, redis: Redis
) -> dict[str, Any]:
	"""Everything the reset could change, to show that a refusal changed none of it."""
	async with engine.connect() as conn:
		counts = {
			t: await conn.scalar(text(f"SELECT count(*) FROM {t}"))
			for t in (*KEEP, *WIPE)
		}
		users = (
			await conn.execute(text(f"SELECT {USER_COLUMNS} FROM users ORDER BY id"))
		).all()
	return {
		"counts": counts,
		"users": users,
		"objects": sorted(store.objects),
		"redis": sorted(await redis.keys("*")),
	}


def _typed(answer: str) -> Callable[[str], str]:
	def confirm(prompt: str) -> str:
		return answer

	return confirm


def _never(prompt: str) -> str:
	raise AssertionError("asked to confirm when it should have stopped first")


async def test_reset_wipes_every_users_data_and_keeps_accounts(
	admin_engine: AsyncEngine,
	redis: Redis,
	settings: Settings,
	two_users: list[uuid.UUID],
	capsys: pytest.CaptureFixture[str],
) -> None:
	store = _bucket(two_users)
	await _queue(redis, settings.arq_queue_name)
	before = await _snapshot(admin_engine, store, redis)
	assert all(before["counts"][t] >= 2 for t in WIPE), before["counts"]

	result = await reset(
		admin_engine,
		store,
		redis,
		queue_name=settings.arq_queue_name,
		confirm=_typed("reset 2 users"),
	)

	assert result.done
	assert result.plan.users == 2 and result.plan.objects == 3
	assert result.plan.queued == 2 and result.plan.arq_keys == 5
	after = await _snapshot(admin_engine, store, redis)
	assert {t: after["counts"][t] for t in WIPE} == dict.fromkeys(WIPE, 0)
	assert result.rows_after == dict.fromkeys(WIPE, 0)
	for kept in ("users", "sessions", "push_tokens", "alembic_version"):
		assert after["counts"][kept] == before["counts"][kept], kept
	assert after["counts"]["sessions"] == 2 and after["counts"]["push_tokens"] == 2

	# Every setting survives; only the passage, which came from using memo, is gone.
	settings_before = {u.id: u._asdict() for u in before["users"]}
	for user in after["users"]:
		row = user._asdict()
		assert row["bible_reading"] is None
		assert row == settings_before[user.id] | {"bible_reading": None}
	seed = next(u for u in after["users"] if u.id == SEED_USER_ID)
	assert (seed.timezone, seed.faith_enabled, seed.bible_translation) == (
		"Europe/London",
		True,
		"ESV",
	)
	assert result.readings_cleared == 1

	assert store.objects == {} and result.objects_deleted == 3
	assert result.objects_left == 0
	assert sorted(await redis.keys("*")) == sorted(k.encode() for k in OTHER_REDIS_KEYS)
	assert result.redis_removed == {
		"queued jobs": 2,
		"arq:job:* keys": 2,
		"arq:in-progress:* keys": 1,
		"arq:retry:* keys": 1,
		"arq:result:* keys": 1,
	}
	out = capsys.readouterr().out
	assert "memo_test on" in out and "password" not in out
	assert "Reset done" in out and "next sync" in out


async def test_a_second_run_finds_nothing_left_and_still_asks(
	admin_engine: AsyncEngine,
	redis: Redis,
	settings: Settings,
	two_users: list[uuid.UUID],
) -> None:
	store = _bucket(two_users)
	confirm = _typed("reset 2 users")
	await reset(
		admin_engine, store, redis, queue_name=settings.arq_queue_name, confirm=confirm
	)
	again = await reset(
		admin_engine, store, redis, queue_name=settings.arq_queue_name, confirm=confirm
	)
	assert again.done
	assert again.plan.rows == dict.fromkeys(WIPE, 0) and again.plan.objects == 0


@pytest.mark.parametrize(
	"typed", ["", "yes", "reset 3 users", "reset 2 users ", "RESET 2 USERS"]
)
async def test_anything_but_the_exact_phrase_changes_nothing(
	admin_engine: AsyncEngine,
	redis: Redis,
	settings: Settings,
	two_users: list[uuid.UUID],
	typed: str,
) -> None:
	store = _bucket(two_users)
	await _queue(redis, settings.arq_queue_name)
	before = await _snapshot(admin_engine, store, redis)

	result = await reset(
		admin_engine,
		store,
		redis,
		queue_name=settings.arq_queue_name,
		confirm=_typed(typed),
	)

	assert not result.done
	assert await _snapshot(admin_engine, store, redis) == before


async def test_end_of_input_at_the_prompt_changes_nothing(
	admin_engine: AsyncEngine,
	redis: Redis,
	settings: Settings,
	two_users: list[uuid.UUID],
) -> None:
	def closed(prompt: str) -> str:
		raise EOFError

	store = _bucket(two_users)
	before = await _snapshot(admin_engine, store, redis)
	result = await reset(
		admin_engine, store, redis, queue_name=settings.arq_queue_name, confirm=closed
	)
	assert not result.done
	assert await _snapshot(admin_engine, store, redis) == before


async def test_a_dry_run_shows_the_plan_and_changes_nothing(
	admin_engine: AsyncEngine,
	redis: Redis,
	settings: Settings,
	two_users: list[uuid.UUID],
	capsys: pytest.CaptureFixture[str],
) -> None:
	store = _bucket(two_users)
	await _queue(redis, settings.arq_queue_name)
	before = await _snapshot(admin_engine, store, redis)

	result = await reset(
		admin_engine,
		store,
		redis,
		queue_name=settings.arq_queue_name,
		confirm=_never,
		dry_run=True,
	)

	assert not result.done
	assert result.plan.rows["captures"] == 2 and result.plan.users == 2
	assert await _snapshot(admin_engine, store, redis) == before
	out = capsys.readouterr().out
	assert "captures" in out and "3 objects" in out and "Dry run" in out


async def test_an_unclassified_table_refuses_with_nothing_changed(
	admin_engine: AsyncEngine,
	redis: Redis,
	settings: Settings,
	two_users: list[uuid.UUID],
) -> None:
	store = _bucket(two_users)
	await _queue(redis, settings.arq_queue_name)
	async with admin_engine.begin() as conn:
		await conn.execute(text("CREATE TABLE reset_probe (id int)"))
	try:
		before = await _snapshot(admin_engine, store, redis)
		with pytest.raises(Refused, match="reset_probe.*KEEP or WIPE"):
			await reset(
				admin_engine,
				store,
				redis,
				queue_name=settings.arq_queue_name,
				confirm=_never,
			)
		assert await _snapshot(admin_engine, store, redis) == before
	finally:
		async with admin_engine.begin() as conn:
			await conn.execute(text("DROP TABLE reset_probe"))


async def test_a_kept_table_that_a_cascade_would_reach_refuses(
	admin_engine: AsyncEngine,
	redis: Redis,
	settings: Settings,
	two_users: list[uuid.UUID],
) -> None:
	# TRUNCATE captures CASCADE would empty sessions too, signing everyone out.
	store = _bucket(two_users)
	async with admin_engine.begin() as conn:
		await conn.execute(
			text(
				"ALTER TABLE sessions ADD COLUMN probe_capture_id uuid "
				"CONSTRAINT fk_sessions_probe REFERENCES captures (id)"
			)
		)
	try:
		before = await _snapshot(admin_engine, store, redis)
		with pytest.raises(Refused, match=r"public\.sessions -> captures"):
			await reset(
				admin_engine,
				store,
				redis,
				queue_name=settings.arq_queue_name,
				confirm=_never,
			)
		assert await _snapshot(admin_engine, store, redis) == before
	finally:
		async with admin_engine.begin() as conn:
			await conn.execute(
				text("ALTER TABLE sessions DROP COLUMN probe_capture_id")
			)


async def test_a_table_created_while_the_operator_reads_refuses_and_rolls_back(
	admin_engine: AsyncEngine,
	redis: Redis,
	settings: Settings,
	two_users: list[uuid.UUID],
) -> None:
	"""The checks run again inside the transaction: a deploy can migrate between the
	plan and the phrase."""
	store = _bucket(two_users)

	async def migrate() -> None:
		engine = create_async_engine(settings.database_admin_url)
		async with engine.begin() as conn:
			await conn.execute(text("CREATE TABLE reset_probe_late (id int)"))
		await engine.dispose()

	def deploy_then_confirm(prompt: str) -> str:
		# confirm is called synchronously on the running loop, so the "deploy" runs
		# on a thread with a loop of its own, the way another process would.
		thread = threading.Thread(target=asyncio.run, args=(migrate(),))
		thread.start()
		thread.join()
		return "reset 2 users"

	before = await _snapshot(admin_engine, store, redis)
	try:
		with pytest.raises(Refused, match="reset_probe_late"):
			await reset(
				admin_engine,
				store,
				redis,
				queue_name=settings.arq_queue_name,
				confirm=deploy_then_confirm,
			)
		after = await _snapshot(admin_engine, store, redis)
		assert after["counts"] == before["counts"] and after["users"] == before["users"]
		assert after["objects"] == before["objects"]
	finally:
		async with admin_engine.begin() as conn:
			await conn.execute(text("DROP TABLE IF EXISTS reset_probe_late"))


async def test_every_table_at_head_is_either_kept_or_wiped(
	admin_engine: AsyncEngine,
) -> None:
	"""Fails when a migration adds a table nobody classified: decide whether a reset
	keeps it, and say so in memo/admin/reset.py."""
	async with admin_engine.connect() as conn:
		tables = set(
			await conn.scalars(
				text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
			)
		)
	assert not set(KEEP) & set(WIPE), "a table cannot be both kept and wiped"
	unclassified = sorted(tables - set(KEEP) - set(WIPE))
	assert not unclassified, (
		f"table(s) {unclassified} exist at alembic head but are in neither list: add "
		"each to KEEP or WIPE in memo/admin/reset.py (ADR 0059)"
	)
	gone = sorted((set(KEEP) | set(WIPE)) - tables)
	assert not gone, (
		f"table(s) {gone} are listed in memo/admin/reset.py but no longer exist at "
		"alembic head: remove them from KEEP or WIPE"
	)


def test_the_phrase_is_never_piped(
	monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
	monkeypatch.setattr("sys.stdin", io.StringIO("reset 2 users\n"))
	assert admin.main([]) == 2
	assert "typed at a terminal" in capsys.readouterr().err


def test_the_command_runs_a_dry_run_end_to_end(
	settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
	"""The real entry point, wired from settings (the test database, the compose
	Redis and bucket), reading only."""
	asyncio.run(S3ObjectStore.from_settings(settings).ensure_bucket())
	assert admin.main(["--dry-run"]) == 0
	out = capsys.readouterr().out
	assert "APP_ENV=" in out and "memo_test on" in out and "Dry run" in out


async def test_the_bucket_is_listed_and_deleted_a_page_at_a_time(
	settings: Settings,
) -> None:
	store = S3ObjectStore.from_settings(settings)
	await store.ensure_bucket()
	prefix = f"reset-test-{uuid.uuid4()}/"
	keys = [f"{prefix}{n}" for n in range(5)]
	async with store._client() as s3:
		for key in keys:
			await s3.put_object(Bucket=store.bucket, Key=key, Body=b"x")

	assert await store.list_keys(prefix, page_size=2) == keys
	await store.delete_keys([*keys, f"{prefix}already-gone"], batch_size=2)
	assert await store.list_keys(prefix) == []


class _NoBatchDelete:
	"""An S3 client whose store turns DeleteObjects down."""

	def __init__(self, inner: Any) -> None:
		self._inner = inner

	async def __aenter__(self) -> "_NoBatchDelete":
		self._s3 = await self._inner.__aenter__()
		return self

	async def __aexit__(self, *exc: Any) -> None:
		await self._inner.__aexit__(*exc)

	async def delete_objects(self, **kwargs: Any) -> Any:
		raise ClientError(
			{"Error": {"Code": "NotImplemented", "Message": "no"}}, "DeleteObjects"
		)

	def __getattr__(self, name: str) -> Any:
		return getattr(self._s3, name)


async def test_a_store_without_batch_delete_deletes_one_by_one(
	settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
	store = S3ObjectStore.from_settings(settings)
	await store.ensure_bucket()
	prefix = f"reset-test-{uuid.uuid4()}/"
	keys = [f"{prefix}{n}" for n in range(3)]
	async with store._client() as s3:
		for key in keys:
			await s3.put_object(Bucket=store.bucket, Key=key, Body=b"x")
	real = store._client
	monkeypatch.setattr(store, "_client", lambda: _NoBatchDelete(real()))

	await store.delete_keys(keys, batch_size=2)
	assert await store.list_keys(prefix) == []
