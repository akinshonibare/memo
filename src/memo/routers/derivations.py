import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import and_, or_, select, update

from memo.auth import CurrentUser
from memo.db import DbSession
from memo.models import Chunk, Derivation
from memo.pagination import decode_cursor, encode_cursor
from memo.schemas.derivations import (
	DERIVATION_STATUSES,
	KINDS,
	DerivationOut,
	DerivationPage,
	DerivationPatch,
	validate_payload,
	validation_detail,
)

router = APIRouter(prefix="/derivations", tags=["derivations"])


@router.get("", response_model=DerivationPage)
async def list_derivations(
	session: DbSession,
	user_id: CurrentUser,
	status_: Annotated[str | None, Query(alias="status")] = None,
	kind: str | None = None,
	cursor: str | None = None,
	limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DerivationPage:
	if status_ is not None and status_ not in DERIVATION_STATUSES:
		raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown status")
	if kind is not None and kind not in KINDS:
		raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown kind")

	stmt = (
		select(Derivation)
		.where(Derivation.user_id == user_id)
		.order_by(Derivation.created_at.desc(), Derivation.id.desc())
		.limit(limit + 1)
	)
	if status_ is not None:
		stmt = stmt.where(Derivation.status == status_)
	if kind is not None:
		stmt = stmt.where(Derivation.kind == kind)
	if cursor is not None:
		after_created, after_id = decode_cursor(cursor)
		stmt = stmt.where(
			or_(
				Derivation.created_at < after_created,
				and_(Derivation.created_at == after_created, Derivation.id < after_id),
			)
		)
	rows = list((await session.scalars(stmt)).all())
	page = rows[:limit]
	next_cursor = (
		encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None
	)
	return DerivationPage(
		items=[DerivationOut.model_validate(d) for d in page], next_cursor=next_cursor
	)


@router.patch("/{derivation_id}", response_model=DerivationOut)
async def review_derivation(
	derivation_id: uuid.UUID,
	body: DerivationPatch,
	session: DbSession,
	user_id: CurrentUser,
) -> Derivation:
	"""Confirm, reject, or edit. Every call here is a labelled example arriving free."""
	derivation = await session.scalar(
		select(Derivation).where(
			Derivation.id == derivation_id, Derivation.user_id == user_id
		)
	)
	if derivation is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND)

	if body.payload is not None:
		try:
			derivation.payload = validate_payload(derivation.kind, body.payload)
		except ValidationError as exc:
			raise HTTPException(
				status.HTTP_422_UNPROCESSABLE_CONTENT, validation_detail(exc)
			) from exc
	# An edit is a confirmation only of something awaiting review. One the user added
	# stays `user_created` (the recall signal), one already confirmed stays confirmed.
	if body.status is not None:
		derivation.status = body.status
	elif derivation.status == "pending_review":
		derivation.status = "confirmed"
	derivation.reviewed_at = dt.datetime.now(dt.UTC)
	if derivation.status == "rejected":
		# A rejected item must stop answering questions (ADR 0021). Superseded, not
		# deleted: a past answer's citation still resolves to what it was shown.
		await session.execute(
			update(Chunk)
			.where(
				Chunk.derivation_id == derivation.id,
				Chunk.user_id == user_id,
				Chunk.superseded_at.is_(None),
			)
			.values(superseded_at=derivation.reviewed_at)
		)
	await session.flush()
	return derivation
