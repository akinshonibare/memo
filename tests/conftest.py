import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Iterator

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
from memo.main import create_app
from tests.fakes import (
	FakeAnswerProvider,
	FakeEmbedder,
	FakeQueue,
	FakeRewriteProvider,
)

TEST_DB = "memo_test"


def _with_database(url: str, database: str) -> str:
	# str(URL) masks the password as ***; render_as_string is the unmasked form.
	return make_url(url).set(database=database).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def settings() -> Settings:
	base = Settings()
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
	asyncio.run(_ensure_database(settings.database_admin_url))
	monkeypatch_session.setenv("DATABASE_ADMIN_URL", settings.database_admin_url)
	monkeypatch_session.setenv("DATABASE_URL", settings.database_url)
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


@pytest.fixture(autouse=True)
async def clean_tables(admin_engine: AsyncEngine) -> None:
	async with admin_engine.begin() as conn:
		for table in (
			"resurfacings",
			"morning_views",
			"message_citations",
			"messages",
			"conversations",
			"chunks",
			"llm_calls",
			"job_runs",
			"derivations",
			"captures",
			"recordings",
		):
			await conn.execute(text(f"DELETE FROM {table}"))
		await conn.execute(
			text("DELETE FROM users WHERE id <> :seed"), {"seed": str(SEED_USER_ID)}
		)
		await conn.execute(
			text("UPDATE users SET timezone = 'UTC' WHERE id = :seed"),
			{"seed": str(SEED_USER_ID)},
		)


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
	application = create_app(settings)
	async with LifespanManager(application):
		# Jobs are recorded, not sent to Redis; the object store stays real (RustFS).
		# Model calls are scripted: no network in tests.
		application.state.queue = FakeQueue()
		application.state.embedder = FakeEmbedder()
		application.state.answer_provider = FakeAnswerProvider()
		application.state.rewrite_provider = FakeRewriteProvider()
		yield application


@pytest.fixture
def queue(app: FastAPI) -> FakeQueue:
	fake: FakeQueue = app.state.queue
	return fake


@pytest.fixture
async def client(app: FastAPI, settings: Settings) -> AsyncIterator[AsyncClient]:
	token = settings.api_token.get_secret_value()
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
