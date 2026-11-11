import asyncio
import datetime as dt
import os
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import pytest
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import command
from memo.auth import get_current_user
from memo.config import SEED_USER_ID, Settings
from memo.db import bind_system
from memo.main import create_app
from memo.services import sessions
from tests.fakes import (
	FakeAnswerProvider,
	FakeEmbedder,
	FakeIdentityVerifier,
	FakeObjectStore,
	FakeQueue,
	FakeRedis,
	FakeRewriteProvider,
	FakeSpeaker,
	FakeTranscriber,
)

TEST_DB = "memo_test"

# Set by the root conftest.py when nothing is configured.
WITHOUT_SERVICES = os.environ.get("MEMO_TESTS_WITHOUT_SERVICES") == "1"
# Set by the root conftest.py when only Postgres is configured: the app starts on fakes
# for Redis and the object store, and the tests that need the real ones are skipped.
DATABASE_ONLY = os.environ.get("MEMO_TESTS_DATABASE_ONLY") == "1"
# Set by the root conftest.py when nothing is configured and pgserver is installed:
# the session starts its own Postgres, then runs as DATABASE_ONLY.
EMBEDDED_POSTGRES = os.environ.get("MEMO_TESTS_EMBEDDED_POSTGRES") == "1"
# Every fixture that reaches a service depends on one of these.
SERVICE_FIXTURES = frozenset({"app", "admin_engine"})


def pytest_configure(config: pytest.Config) -> None:
	config.addinivalue_line(
		"markers", "services: needs the real Redis or object store, not only Postgres"
	)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
	if WITHOUT_SERVICES:
		reason = "needs Postgres, Redis and the object store"
	elif DATABASE_ONLY:
		reason = "needs the real Redis or object store"
	else:
		return
	skip = pytest.mark.skip(reason=reason)
	for item in items:
		uses_service = SERVICE_FIXTURES.intersection(getattr(item, "fixturenames", []))
		if item.get_closest_marker("services") or (WITHOUT_SERVICES and uses_service):
			item.add_marker(skip)


def _with_database(url: str, database: str) -> str:
	# str(URL) masks the password as ***; render_as_string is the unmasked form.
	return make_url(url).set(database=database).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def embedded_postgres() -> Iterator[str | None]:
	"""The socket directory of a Postgres started for this session, or None when the
	environment configures its own. pgserver bundles Postgres and pgvector, needs no
	root, and listens only on a Unix socket; its data goes when the session ends."""
	if not EMBEDDED_POSTGRES:
		yield None
		return
	from pgserver.postgres_server import get_server

	server = get_server(tempfile.mkdtemp(prefix="memo-pg-"), cleanup_mode="delete")
	host = make_url(server.get_uri()).query["host"]
	yield host if isinstance(host, str) else host[0]
	server.cleanup()


@pytest.fixture(scope="session")
def settings(embedded_postgres: str | None) -> Settings:
	base = Settings()
	if embedded_postgres is not None:
		socket = f"?host={embedded_postgres}"
		password = base.app_db_password.get_secret_value()
		base = base.model_copy(
			update={
				"database_url": f"postgresql+asyncpg://memo_app:{password}@/memo{socket}",
				"database_admin_url": f"postgresql+asyncpg://postgres@/memo{socket}",
			}
		)
	return base.model_copy(
		update={
			"app_env": "test",
			"database_url": _with_database(base.database_url, TEST_DB),
			"database_admin_url": _with_database(base.database_admin_url, TEST_DB),
		}
	)


async def _ensure_database(admin_url: str) -> None:
	server = create_async_engine(
		_with_database(admin_url, "postgres"), isolation_level="AUTOCOMMIT"
	)
	async with server.connect() as conn:
		exists = await conn.scalar(
			text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DB}
		)
		if not exists:
			await conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
	await server.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database(
	settings: Settings, monkeypatch_session: pytest.MonkeyPatch
) -> None:
	if WITHOUT_SERVICES:
		return
	asyncio.run(_ensure_database(settings.database_admin_url))
	monkeypatch_session.setenv("DATABASE_ADMIN_URL", settings.database_admin_url)
	monkeypatch_session.setenv("DATABASE_URL", settings.database_url)
	# No daily AI limit unless a test sets one on a user (ADR 0063): corpus-heavy tests
	# would otherwise find their notes waiting for midnight.
	monkeypatch_session.setenv("DAILY_AI_LIMIT_USD", "0")
	from memo.config import get_settings

	get_settings.cache_clear()
	command.downgrade(Config("alembic.ini"), "base")
	command.upgrade(Config("alembic.ini"), "head")
	get_settings.cache_clear()


@pytest.fixture(scope="session")
def monkeypatch_session() -> Iterator[pytest.MonkeyPatch]:
	mp = pytest.MonkeyPatch()
	yield mp
	mp.undo()


@pytest.fixture
async def admin_engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
	"""Owner-role engine: bypasses RLS. For test setup and assertions only."""
	engine = create_async_engine(settings.database_admin_url)
	yield engine
	await engine.dispose()


@pytest.fixture
def delete_keeping_items(admin_engine: AsyncEngine) -> Callable[..., Any]:
	"""Delete a note the way memo did before ADR 0053: its rejected items go, the rest
	stay detached (capture_id NULL), with their chunks. Such items are still in real
	data, so every route has to keep coping with them."""

	async def run(capture_id: uuid.UUID | str) -> None:
		ids = {"id": str(capture_id)}
		async with admin_engine.begin() as conn:
			await conn.execute(
				text(
					"DELETE FROM derivations WHERE capture_id = :id "
					"AND status = 'rejected'"
				),
				ids,
			)
			await conn.execute(
				text("UPDATE derivations SET capture_id = NULL WHERE capture_id = :id"),
				ids,
			)
			await conn.execute(
				text(
					"UPDATE chunks SET capture_id = NULL WHERE capture_id = :id "
					"AND derivation_id IS NOT NULL"
				),
				ids,
			)
			await conn.execute(text("DELETE FROM captures WHERE id = :id"), ids)

	return run


@pytest.fixture(autouse=True)
async def clean_tables(settings: Settings) -> None:
	if WITHOUT_SERVICES:
		return
	# Its own engine, not admin_engine: as an autouse fixture, depending on that would
	# put it in every test's fixture closure and hide which tests really need a service.
	engine = create_async_engine(settings.database_admin_url)
	async with engine.begin() as conn:
		for table in (
			"pushes",
			"push_tokens",
			"sessions",
			"resurfacings",
			"morning_views",
			"message_citations",
			"messages",
			"conversations",
			"chunks",
			"llm_calls",
			"job_runs",
			"derivations",
			"lists",
			"people",
			"captures",
			"recordings",
			"habits",
		):
			await conn.execute(text(f"DELETE FROM {table}"))
		await conn.execute(
			text("DELETE FROM users WHERE id <> :seed"), {"seed": str(SEED_USER_ID)}
		)
		# Habits starts switched off here (the operator's switch; ADR 0054 made it on
		# for everyone), so a note's stages are only the ones each test sets up.
		await conn.execute(
			text(
				"UPDATE users SET timezone = 'UTC', apple_sub = NULL, email = NULL, "
				"display_name = NULL, voice = NULL, auto_accept = false, "
				"faith_enabled = false, bible_translation = 'NIV', "
				"bible_reading = NULL, habits_enabled = false, "
				"morning_push_at = NULL, weekly_push_at = NULL, tone = 'plain', "
				"daily_ai_limit_usd = NULL "
				"WHERE id = :seed"
			),
			{"seed": str(SEED_USER_ID)},
		)
	await engine.dispose()


async def _fake_queue(settings: Settings) -> FakeQueue:
	return FakeQueue()


class _FakeStoreFactory:
	"""Stands in for S3ObjectStore at startup when only Postgres is configured."""

	@staticmethod
	def from_settings(settings: Settings) -> FakeObjectStore:
		return FakeObjectStore()


@pytest.fixture
async def app(
	settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[FastAPI]:
	if DATABASE_ONLY:
		# Startup would otherwise connect to Redis (the queue's pool) and to the
		# object store (the bucket check).
		monkeypatch.setattr("memo.main.make_queue", _fake_queue)
		monkeypatch.setattr("memo.main.S3ObjectStore", _FakeStoreFactory)
	application = create_app(settings)
	async with LifespanManager(application):
		# Jobs are recorded, not sent to Redis; the object store stays real (RustFS)
		# unless only Postgres is configured.
		# Model calls are scripted: no network in tests.
		application.state.queue = FakeQueue()
		# Real Redis would carry rate-limit counters across tests within a minute;
		# the real client stays reachable as real_redis for the one test that wants it.
		application.state.real_redis = application.state.redis
		application.state.redis = FakeRedis()
		application.state.embedder = FakeEmbedder()
		application.state.answer_provider = FakeAnswerProvider()
		application.state.rewrite_provider = FakeRewriteProvider()
		application.state.identity_verifier = FakeIdentityVerifier()
		application.state.transcriber = FakeTranscriber("what is on tomorrow")
		application.state.speaker = FakeSpeaker()
		yield application


@pytest.fixture
def queue(app: FastAPI) -> FakeQueue:
	fake: FakeQueue = app.state.queue
	return fake


async def issue_session(app: FastAPI, user_id: uuid.UUID) -> str:
	"""A real session row for user_id, the way /v1/auth/apple would mint one."""
	async with app.state.session_factory() as s:
		await bind_system(s)
		token = await sessions.create(s, user_id, ttl=dt.timedelta(days=1))
		await s.commit()
	return token


@pytest.fixture
async def client(app: FastAPI, settings: Settings) -> AsyncIterator[AsyncClient]:
	token = await issue_session(app, SEED_USER_ID)
	async with AsyncClient(
		transport=ASGITransport(app=app),
		base_url="http://test",
		headers={"Authorization": f"Bearer {token}"},
	) as c:
		yield c


@pytest.fixture
def as_user(app: FastAPI) -> Callable[[uuid.UUID], None]:
	"""Switch the identity the API acts as, the way a real token would."""

	def _switch(user_id: uuid.UUID) -> None:
		app.dependency_overrides[get_current_user] = lambda: user_id

	return _switch


@pytest.fixture
async def second_user(admin_engine: AsyncEngine) -> uuid.UUID:
	user_id = uuid.uuid4()
	async with admin_engine.begin() as conn:
		await conn.execute(
			text("INSERT INTO users (id, timezone) VALUES (:id, 'UTC')"),
			{"id": user_id},
		)
	return user_id
