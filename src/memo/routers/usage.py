import datetime as dt
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from memo.auth import CurrentUser
from memo.db import DbSession
from memo.models import LLMCallRow, User
from memo.services.budget import budget_for

router = APIRouter(prefix="/usage", tags=["usage"])


class StageUsage(BaseModel):
	stage: str
	model: str
	calls: int
	input_tokens: int
	output_tokens: int
	cost_usd: Decimal


class UsageOut(BaseModel):
	window_days: int
	since: dt.datetime
	total_cost_usd: Decimal
	by_stage: list[StageUsage]


@router.get("", response_model=UsageOut)
async def usage(
	session: DbSession,
	user_id: CurrentUser,
	days: Annotated[int, Query(ge=1, le=365)] = 7,
) -> UsageOut:
	"""Answers 'what did last week cost' per stage and model (issue #27)."""
	since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
	rows = (
		await session.execute(
			select(
				LLMCallRow.stage,
				LLMCallRow.model,
				func.count().label("calls"),
				func.coalesce(func.sum(LLMCallRow.input_tokens), 0),
				func.coalesce(func.sum(LLMCallRow.output_tokens), 0),
				func.coalesce(func.sum(LLMCallRow.cost_usd), 0),
			)
			.where(LLMCallRow.user_id == user_id, LLMCallRow.created_at >= since)
			.group_by(LLMCallRow.stage, LLMCallRow.model)
			.order_by(LLMCallRow.stage, LLMCallRow.model)
		)
	).all()
	by_stage = [
		StageUsage(
			stage=stage,
			model=model,
			calls=calls,
			input_tokens=int(inp),
			output_tokens=int(out),
			cost_usd=Decimal(cost),
		)
		for stage, model, calls, inp, out, cost in rows
	]
	return UsageOut(
		window_days=days,
		since=since,
		total_cost_usd=sum((s.cost_usd for s in by_stage), Decimal("0")),
		by_stage=by_stage,
	)


class TodayOut(BaseModel):
	"""Today's AI spend against the daily limit (ADR 0063). `limit_usd` null: none."""

	spent_usd: Decimal
	limit_usd: Decimal | None
	over: bool
	resets_at: dt.datetime  # their next midnight


@router.get("/today", response_model=TodayOut)
async def today(session: DbSession, user_id: CurrentUser) -> TodayOut:
	user = await session.get(User, user_id)
	if user is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
	budget = await budget_for(session, user)
	return TodayOut(
		spent_usd=budget.spent.quantize(Decimal("0.01")),
		limit_usd=budget.limit,
		over=budget.over,
		resets_at=budget.resets_at,
	)
