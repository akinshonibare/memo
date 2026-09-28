import asyncio

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def _backend_pid(session: AsyncSession) -> int:
	pid = await session.scalar(text("SELECT pg_backend_pid()"))
	assert pid is not None
	return int(pid)


async def test_concurrent_requests_never_share_a_session(app: FastAPI) -> None:
	factory = app.state.session_factory

	async def one() -> tuple[int, int]:
		async with factory() as session:
			pid = await _backend_pid(session)
			# Hold the connection so the other coroutine must check out a second one.
			await asyncio.sleep(0.05)
			return id(session), pid

	(s1, pid1), (s2, pid2) = await asyncio.gather(one(), one())
	assert s1 != s2
	assert pid1 != pid2
