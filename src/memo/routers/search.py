"""Search that understands meaning (ADR 0028): Home's search box, backed by the same
hybrid retrieval as Ask - keyword and vector, fused with RRF - instead of substring
match on the client. No model call beyond embedding the query."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from memo.auth import CurrentUser
from memo.db import DbSession
from memo.models import Capture, Derivation
from memo.routers.limits import limit_search
from memo.schemas.captures import CaptureOut
from memo.schemas.derivations import DerivationOut
from memo.services.retrieval import search as retrieve

router = APIRouter(prefix="/search", tags=["search"])


class SearchHit(BaseModel):
	snippet: str
	capture: CaptureOut | None  # the note
	derivation: DerivationOut | None  # or an item whose note was deleted (ADR 0022)


@router.get("", response_model=list[SearchHit], dependencies=[Depends(limit_search)])
async def search(
	request: Request,
	session: DbSession,
	user_id: CurrentUser,
	q: Annotated[str, Query(min_length=2, max_length=300)],
	limit: Annotated[int, Query(ge=1, le=30)] = 15,
) -> list[SearchHit]:
	result = await retrieve(
		session,
		user_id=user_id,
		query=q,
		embedder=request.app.state.embedder,
		k=limit,
		candidates=max(40, 3 * limit),
	)
	capture_ids = [h.capture_id for h in result.hits if h.capture_id is not None]
	orphan_ids = [
		h.derivation_id
		for h in result.hits
		if h.capture_id is None and h.derivation_id is not None
	]
	captures: dict[uuid.UUID, Capture] = {}
	if capture_ids:
		rows = await session.scalars(
			select(Capture).where(
				Capture.id.in_(capture_ids), Capture.user_id == user_id
			)
		)
		captures = {c.id: c for c in rows}
	derivations: dict[uuid.UUID, Derivation] = {}
	if orphan_ids:
		rows_d = await session.scalars(
			select(Derivation).where(
				Derivation.id.in_(orphan_ids),
				Derivation.user_id == user_id,
				Derivation.status != "rejected",
			)
		)
		derivations = {d.id: d for d in rows_d}
	hits: list[SearchHit] = []
	for h in result.hits:  # already in fused rank order
		if h.capture_id is not None and h.capture_id in captures:
			hits.append(
				SearchHit(
					snippet=h.text[:240],
					capture=CaptureOut.model_validate(captures[h.capture_id]),
					derivation=None,
				)
			)
		elif h.derivation_id is not None and h.derivation_id in derivations:
			hits.append(
				SearchHit(
					snippet=h.text[:240],
					capture=None,
					derivation=DerivationOut.model_validate(
						derivations[h.derivation_id]
					),
				)
			)
	return hits
