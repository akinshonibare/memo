import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
	AsyncEngine,
	AsyncSession,
	async_sessionmaker,
	create_async_engine,
)

from memo.auth import CurrentUser


def make_engine(url: str) -> AsyncEngine:
	return create_async_engine(url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
	return async_sessionmaker(engine, expire_on_commit=False)


async def bind_user(session: AsyncSession, user_id: uuid.UUID) -> None:
	# is_local=true scopes the setting to the open transaction, which is what the RLS
	# policies read; it is gone the moment the transaction ends, so a pooled connection
	# never carries one user's identity into another request.
	await session.execute(
		text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(user_id)}
	)


async def get_session(
	request: Request, user_id: CurrentUser
) -> AsyncIterator[AsyncSession]:
	factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
	async with factory() as session, session.begin():
		await bind_user(session, user_id)
		yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]
