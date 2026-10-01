"""A daily AI budget per person (ADR 0063): the most memo spends on one person's AI in
one of their days, counted from the cost log (`llm_calls`).

At the limit nothing is lost: a note still saves, and its paid stages wait for that
person's midnight; Ask and voice say they're back then; background rewrites skip them.
The check is made before a paid call, so a call already under way can take the day a
little past the line; it never starts another.

Counted: every Anthropic call (already logged with its price), and Deepgram for voice
notes, live words and spoken answers (priced here). Not counted: Voyage embeddings, a
few thousandths of a cent a note.
"""

import datetime as dt
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from memo.config import get_settings
from memo.ids import uuid7
from memo.models import LLMCallRow, User
from memo.services.llm import LLMCall
from memo.services.morning import tz_or_utc

# Deepgram, pay as you go, read off deepgram.com/pricing on 2026-10-01.
TRANSCRIBE_PER_MINUTE = Decimal("0.0043")  # Nova-3 monolingual, pre-recorded
LIVE_PER_MINUTE = Decimal("0.0048")  # Nova-3 monolingual, streaming
SPEAK_PER_1K_CHARS = Decimal("0.030")  # Aura-2


@dataclass(frozen=True)
class Budget:
	spent: Decimal
	limit: Decimal | None  # None: no limit
	resets_at: dt.datetime  # the person's next midnight, in UTC

	@property
	def over(self) -> bool:
		return self.limit is not None and self.spent >= self.limit

	def seconds_left_today(self, now: dt.datetime | None = None) -> int:
		now = now or dt.datetime.now(dt.UTC)
		return max(1, int((self.resets_at - now).total_seconds()))


class OverBudget(Exception):
	"""Raised where an AI call would start for someone at their limit."""

	def __init__(self, budget: Budget) -> None:
		super().__init__(f"daily AI limit reached ({budget.spent} of {budget.limit})")
		self.budget = budget


def limit_for(user: User) -> Decimal | None:
	own = user.daily_ai_limit_usd
	limit = own if own is not None else get_settings().daily_ai_limit_usd
	return None if limit <= 0 else limit


def _day(user: User, now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
	zone = tz_or_utc(user.timezone)
	local = now.astimezone(zone)
	start = dt.datetime.combine(local.date(), dt.time(), tzinfo=zone)
	end = dt.datetime.combine(
		local.date() + dt.timedelta(days=1), dt.time(), tzinfo=zone
	)
	return start.astimezone(dt.UTC), end.astimezone(dt.UTC)


async def budget_for(
	session: AsyncSession, user: User, *, now: dt.datetime | None = None
) -> Budget:
	"""What this person's AI has cost since their midnight, against their limit."""
	start, end = _day(user, now or dt.datetime.now(dt.UTC))
	spent = await session.scalar(
		select(func.coalesce(func.sum(LLMCallRow.cost_usd), 0)).where(
			LLMCallRow.user_id == user.id, LLMCallRow.created_at >= start
		)
	)
	return Budget(spent=Decimal(spent or 0), limit=limit_for(user), resets_at=end)


async def ensure_within(session: AsyncSession, user: User) -> Budget:
	budget = await budget_for(session, user)
	if budget.over:
		raise OverBudget(budget)
	return budget


# --- pricing what the cost log didn't have ------------------------------------------


def _audio(stage: str, model: str, seconds: float, per_minute: Decimal) -> LLMCall:
	cost = (per_minute * Decimal(str(round(seconds, 1))) / 60).quantize(
		Decimal("0.000001")
	)
	return LLMCall(
		provider="deepgram",
		model=model,
		stage=stage,
		input_tokens=0,
		output_tokens=0,
		cache_read_tokens=0,
		cache_write_tokens=0,
		latency_ms=0,
		cost_usd=cost,
	)


def transcribe_call(model: str, seconds: float) -> LLMCall:
	return _audio("transcribe", model, seconds, TRANSCRIBE_PER_MINUTE)


def live_call(model: str, seconds: float) -> LLMCall:
	return _audio("live_stt", model, seconds, LIVE_PER_MINUTE)


def speak_call(model: str, characters: int) -> LLMCall:
	cost = (SPEAK_PER_1K_CHARS * characters / 1000).quantize(Decimal("0.000001"))
	return LLMCall(
		provider="deepgram",
		model=model,
		stage="speak",
		input_tokens=0,
		output_tokens=0,
		cache_read_tokens=0,
		cache_write_tokens=0,
		latency_ms=0,
		cost_usd=cost,
	)


def log_calls(
	session: AsyncSession,
	user_id: uuid.UUID,
	calls: Iterable[LLMCall],
	*,
	capture_id: uuid.UUID | None = None,
	message_id: uuid.UUID | None = None,
) -> None:
	"""Add calls to the cost log. The caller commits."""
	for call in calls:
		session.add(
			LLMCallRow(
				id=uuid7(),
				user_id=user_id,
				capture_id=capture_id,
				message_id=message_id,
				stage=call.stage,
				provider=call.provider,
				model=call.model[:64],
				input_tokens=call.input_tokens,
				output_tokens=call.output_tokens,
				cache_read_tokens=call.cache_read_tokens,
				cache_write_tokens=call.cache_write_tokens,
				latency_ms=call.latency_ms,
				cost_usd=call.cost_usd,
			)
		)
