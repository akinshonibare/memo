import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from memo.auth import CurrentUser
from memo.config import Settings, get_settings
from memo.db import bind_user
from memo.models import Message
from memo.schemas.ask import sse
from memo.services.ask import AskContext, replay, run_turn

router = APIRouter(prefix="/messages", tags=["ask"])


@router.get("/{message_id}/stream")
async def stream_message(
	message_id: uuid.UUID,
	request: Request,
	user_id: CurrentUser,
	settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
	"""SSE: `retrieval` (before any token), `token`*, then `done` or `error`.

	The session is opened inside the generator rather than as a dependency: a
	dependency's session can be closed before a streaming body has finished with it.
	"""
	factory = request.app.state.session_factory

	async with factory() as session:
		await bind_user(session, user_id)
		assistant = await session.scalar(
			select(Message).where(
				Message.id == message_id,
				Message.user_id == user_id,
				Message.role == "assistant",
			)
		)
		if assistant is None:
			raise HTTPException(status.HTTP_404_NOT_FOUND)
		if assistant.status == "streaming":
			raise HTTPException(
				status.HTTP_409_CONFLICT, "this answer is already being streamed"
			)
		initial_status = assistant.status

	async def body() -> AsyncGenerator[str, None]:
		async with factory() as session:
			await bind_user(session, user_id)
			assistant = await session.scalar(
				select(Message).where(
					Message.id == message_id, Message.user_id == user_id
				)
			)
			assert assistant is not None
			if initial_status in ("done", "failed"):
				async for event in replay(session, assistant):
					yield sse(event.name, event.data)
				return
			ctx = AskContext(
				session=session,
				embedder=request.app.state.embedder,
				answer_provider=request.app.state.answer_provider,
				rewrite_provider=request.app.state.rewrite_provider,
				top_k=settings.ask_top_k,
				history_turns=settings.ask_history_turns,
				ef_search=settings.hnsw_ef_search,
			)
			async for event in run_turn(ctx, assistant):
				yield sse(event.name, event.data)

	return StreamingResponse(
		body(),
		media_type="text/event-stream",
		headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
	)
