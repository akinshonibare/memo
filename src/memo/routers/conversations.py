import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select, text

from memo.auth import CurrentUser
from memo.db import DbSession
from memo.ids import uuid7
from memo.models import Conversation, Message
from memo.pagination import decode_cursor, encode_cursor
from memo.routers.limits import limit_ask, within_daily_budget
from memo.schemas.ask import (
	CitationOut,
	ConversationOut,
	ConversationPage,
	MessageAccepted,
	MessageCreate,
	MessageOut,
)
from memo.services.ask import new_turn

router = APIRouter(prefix="/conversations", tags=["ask"])


async def _owned(
	session: DbSession, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> Conversation:
	conversation = await session.scalar(
		select(Conversation).where(
			Conversation.id == conversation_id, Conversation.user_id == user_id
		)
	)
	if conversation is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND)
	return conversation


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ConversationOut)
async def create_conversation(session: DbSession, user_id: CurrentUser) -> Conversation:
	conversation = Conversation(id=uuid7(), user_id=user_id)
	session.add(conversation)
	await session.flush()
	return conversation


@router.get("", response_model=ConversationPage)
async def list_conversations(
	session: DbSession,
	user_id: CurrentUser,
	cursor: str | None = None,
	limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ConversationPage:
	stmt = (
		select(Conversation)
		.where(Conversation.user_id == user_id)
		.order_by(Conversation.created_at.desc(), Conversation.id.desc())
		.limit(limit + 1)
	)
	if cursor is not None:
		after_created, after_id = decode_cursor(cursor)
		stmt = stmt.where(
			or_(
				Conversation.created_at < after_created,
				and_(
					Conversation.created_at == after_created,
					Conversation.id < after_id,
				),
			)
		)
	rows = list((await session.scalars(stmt)).all())
	page = rows[:limit]
	next_cursor = (
		encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None
	)
	return ConversationPage(
		items=[ConversationOut.model_validate(c) for c in page],
		next_cursor=next_cursor,
	)


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
	conversation_id: uuid.UUID, session: DbSession, user_id: CurrentUser
) -> list[MessageOut]:
	"""History with citations resolved to their chunks."""
	await _owned(session, conversation_id, user_id)
	messages = (
		await session.scalars(
			select(Message)
			.where(
				Message.conversation_id == conversation_id, Message.user_id == user_id
			)
			.order_by(Message.created_at, Message.id)
		)
	).all()
	citation_rows = (
		await session.execute(
			text(
				"SELECT c.message_id, c.chunk_id, k.capture_id, k.derivation_id, "
				"c.rank, k.text, k.created_at "
				"FROM message_citations c JOIN chunks k ON k.id = c.chunk_id "
				"JOIN messages m ON m.id = c.message_id "
				"WHERE m.conversation_id = :cid AND c.user_id = :uid "
				"ORDER BY c.message_id, c.rank"
			),
			{"cid": conversation_id, "uid": user_id},
		)
	).all()
	by_message: dict[uuid.UUID, list[CitationOut]] = {}
	for (
		mid,
		chunk_id,
		capture_id,
		derivation_id,
		rank,
		snippet,
		created,
	) in citation_rows:
		by_message.setdefault(mid, []).append(
			CitationOut(
				chunk_id=chunk_id,
				capture_id=capture_id,
				derivation_id=derivation_id,
				rank=rank,
				snippet=snippet[:280],
				created_at=created,
			)
		)
	return [
		MessageOut(
			**MessageOut.model_validate(m).model_dump(exclude={"citations"}),
			citations=by_message.get(m.id, []),
		)
		for m in messages
	]


@router.post(
	"/{conversation_id}/messages",
	status_code=status.HTTP_201_CREATED,
	response_model=MessageAccepted,
	dependencies=[Depends(limit_ask), Depends(within_daily_budget)],
)
async def create_message(
	conversation_id: uuid.UUID,
	body: MessageCreate,
	session: DbSession,
	user_id: CurrentUser,
) -> MessageAccepted:
	"""Create-then-stream: the turn is durable before generation begins, so a client
	that drops mid-answer reconnects to the same stream_url instead of re-paying."""
	conversation = await _owned(session, conversation_id, user_id)
	user_message, assistant = new_turn(conversation.id, user_id, body.text)
	session.add_all([user_message, assistant])
	if conversation.title is None:
		conversation.title = body.text[:80]
	conversation.updated_at = dt.datetime.now(dt.UTC)
	await session.flush()
	return MessageAccepted(
		conversation_id=conversation.id,
		user_message_id=user_message.id,
		message_id=assistant.id,
		stream_url=f"/v1/messages/{assistant.id}/stream",
	)
