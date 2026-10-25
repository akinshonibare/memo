import asyncio
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from memo.config import get_settings
from memo.models import Base

config = context.config
if config.config_file_name is not None:
	fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
	context.configure(
		url=get_settings().database_admin_url,
		target_metadata=target_metadata,
		literal_binds=True,
		dialect_opts={"paramstyle": "named"},
	)
	with context.begin_transaction():
		context.run_migrations()


def _run_migrations(connection: Connection) -> None:
	context.configure(connection=connection, target_metadata=target_metadata)
	with context.begin_transaction():
		# api and worker both migrate before they start (ADR 0046), and deploy at the
		# same time. The second waits here, then reads the version the first committed
		# and has nothing to do. Transaction-scoped: a migration that commits midway
		# (autocommit_block) would release it, so none may, or this becomes a session
		# lock.
		connection.execute(
			text("SELECT pg_advisory_xact_lock(hashtext('memo.alembic'))")
		)
		context.run_migrations()


async def run_migrations_online() -> None:
	# Migrations run as the owner role; the app never does.
	engine = create_async_engine(
		get_settings().database_admin_url, poolclass=pool.NullPool
	)
	async with engine.connect() as connection:
		await connection.run_sync(_run_migrations)
	await engine.dispose()


if context.is_offline_mode():
	run_migrations_offline()
else:
	asyncio.run(run_migrations_online())
