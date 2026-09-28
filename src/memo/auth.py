"""Who is this request? (ADR 0019)

Identity is resolved from a server-issued session token, never from the request
body, query or path. The lookup runs before the request has a tenant, so it uses a
short system-bound session of its own; the request's own DbSession then binds the
resolved user for row-level security."""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, WebSocket, status
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


async def resolve_user(
	factory: async_sessionmaker[AsyncSession], token: str
) -> uuid.UUID | None:
	"""The one token lookup, shared by HTTP requests and WebSockets."""
	# memo.db imports CurrentUser from here; importing back at module level would
	# be circular.
	from memo.db import bind_system

	async with factory() as session:
		await bind_system(session)
		user_id = await sessions.resolve(session, token)
		await session.commit()
	return user_id


async def get_current_user(request: Request, credentials: Credentials) -> uuid.UUID:
	token = bearer_token(credentials)
	user_id = await resolve_user(request.app.state.session_factory, token)
	if user_id is None:
		raise _unauthorized()
	return user_id


async def websocket_user(websocket: WebSocket) -> uuid.UUID | None:
	"""A WebSocket's user, from the same Authorization header a request carries.
	Never from the URL: a query string ends up in access logs."""
	header = websocket.headers.get("authorization", "")
	scheme, _, token = header.partition(" ")
	if scheme.lower() != "bearer" or not token:
		return None
	return await resolve_user(websocket.app.state.session_factory, token)


CurrentUser = Annotated[uuid.UUID, Depends(get_current_user)]
BearerToken = Annotated[str, Depends(bearer_token)]
