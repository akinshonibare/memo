"""Who is this request? (ADR 0019)

Identity is resolved from a server-issued session token, never from the request
body, query or path. The lookup runs before the request has a tenant, so it uses a
short system-bound session of its own; the request's own DbSession then binds the
resolved user for row-level security."""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memo.services import sessions

_bearer = HTTPBearer(auto_error=False)

Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def _unauthorized() -> HTTPException:
	return HTTPException(
		status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"}
	)


def bearer_token(credentials: Credentials) -> str:
	if credentials is None or not credentials.credentials:
		raise _unauthorized()
	return credentials.credentials


async def get_current_user(request: Request, credentials: Credentials) -> uuid.UUID:
	# memo.db imports CurrentUser from here; importing back at module level would
	# be circular.
	from memo.db import bind_system

	token = bearer_token(credentials)
	factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
	async with factory() as session:
		await bind_system(session)
		user_id = await sessions.resolve(session, token)
		await session.commit()
	if user_id is None:
		raise _unauthorized()
	return user_id


CurrentUser = Annotated[uuid.UUID, Depends(get_current_user)]
BearerToken = Annotated[str, Depends(bearer_token)]
