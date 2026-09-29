"""Suggested changes to earlier items (ADR 0048): a note that may answer a prayer, or
carry on an idea. The app asks; the author says yes or no here."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from memo.auth import CurrentUser
from memo.db import DbSession
from memo.models import Derivation
from memo.schemas.derivations import UPDATE
from memo.schemas.reconcile import UpdateDecision, UpdateOut
from memo.services.reconcile import decide, updates_out

router = APIRouter(prefix="/updates", tags=["updates"])


@router.get("", response_model=list[UpdateOut])
async def list_updates(
	session: DbSession,
	user_id: CurrentUser,
	status_: Annotated[
		Literal["pending", "confirmed"] | None, Query(alias="status")
	] = None,
	kind: Literal["prayer", "idea"] | None = None,
) -> list[UpdateOut]:
	"""Pending: the questions to ask ("Answered?", "Carries on?"). Confirmed: the
	threads, what each idea has been carried on by."""
	return await updates_out(session, user_id, status=status_, target_kind=kind)


@router.patch("/{update_id}", response_model=UpdateOut)
async def answer(
	update_id: uuid.UUID, body: UpdateDecision, session: DbSession, user_id: CurrentUser
) -> UpdateOut:
	update = await session.scalar(
		select(Derivation).where(
			Derivation.id == update_id,
			Derivation.user_id == user_id,
			Derivation.kind == UPDATE,
		)
	)
	if update is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND, "no such suggestion")
	await decide(session, update, body.decision == "yes")
	await session.flush()
	for out in await updates_out(session, user_id):
		if out.id == update.id:
			return out
	raise HTTPException(status.HTTP_404_NOT_FOUND, "its item is gone")
