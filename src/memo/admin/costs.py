"""What every user's AI has cost (ADR 0063): a read-only report for the operator, from
the cost log (`llm_calls`). Run it in the api container, the only place with a route to
production Postgres:

	railway ssh --service api python -m memo.admin.costs
	railway ssh --service api python -m memo.admin.costs --days 30 --stages

Each user's "today" is their own day (since their midnight), as the daily limit counts
it. Nothing is written.
"""

import argparse
import asyncio
import datetime as dt
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from memo.config import get_settings
from memo.db import bind_system, make_engine, make_session_factory
from memo.models import LLMCallRow, User
from memo.services.budget import budget_for

CENT = Decimal("0.01")


@dataclass(frozen=True)
class UserCosts:
	user_id: uuid.UUID
	who: str  # name, else email, else "-"
	today: Decimal
	limit: Decimal | None
	over: bool
	window: Decimal  # the last `days` days
	calls: int


@dataclass(frozen=True)
class StageCosts:
	stage: str
	provider: str
	calls: int
	cost: Decimal


async def user_costs(
	session: AsyncSession, *, days: int, now: dt.datetime | None = None
) -> list[UserCosts]:
	"""Every user, most expensive over the window first."""
	now = now or dt.datetime.now(dt.UTC)
	since = now - dt.timedelta(days=days)
	window = {
		r[0]: (Decimal(r[1]), int(r[2]))
		for r in (
			await session.execute(
				select(
					LLMCallRow.user_id,
					func.coalesce(func.sum(LLMCallRow.cost_usd), 0),
					func.count(),
				)
				.where(LLMCallRow.created_at >= since)
				.group_by(LLMCallRow.user_id)
			)
		).all()
	}
	rows = []
	for user in (await session.scalars(select(User))).all():
		budget = await budget_for(session, user, now=now)
		spent, calls = window.get(user.id, (Decimal(0), 0))
		rows.append(
			UserCosts(
				user_id=user.id,
				who=user.display_name or user.email or "-",
				today=budget.spent,
				limit=budget.limit,
				over=budget.over,
				window=spent,
				calls=calls,
			)
		)
	return sorted(rows, key=lambda r: (r.window, r.today), reverse=True)


async def stage_costs(
	session: AsyncSession, *, days: int, now: dt.datetime | None = None
) -> list[StageCosts]:
	"""All users together, by stage and provider, most expensive first."""
	since = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(days=days)
	rows = (
		await session.execute(
			select(
				LLMCallRow.stage,
				LLMCallRow.provider,
				func.count(),
				func.coalesce(func.sum(LLMCallRow.cost_usd), 0),
			)
			.where(LLMCallRow.created_at >= since)
			.group_by(LLMCallRow.stage, LLMCallRow.provider)
		)
	).all()
	out = [StageCosts(r[0], r[1], int(r[2]), Decimal(r[3])) for r in rows]
	return sorted(out, key=lambda s: s.cost, reverse=True)


def _usd(value: Decimal) -> str:
	return f"${value.quantize(CENT)}"


def render(
	users: list[UserCosts], stages: list[StageCosts] | None, *, days: int
) -> str:
	lines = [
		f"memo AI costs (USD). Today is each user's own day; the window is the last "
		f"{days} days.",
		"",
		f"  {'user':<36}  {'who':<24}  {'today':>8}  {'limit':>7}  "
		f"{f'{days} days':>9}  {'calls':>6}",
	]
	for u in users:
		limit = _usd(u.limit) if u.limit is not None else "none"
		flag = "  AT LIMIT" if u.over else ""
		lines.append(
			f"  {u.user_id!s:<36}  {u.who[:24]:<24}  {_usd(u.today):>8}  {limit:>7}  "
			f"{_usd(u.window):>9}  {u.calls:>6}{flag}"
		)
	today = _usd(sum((u.today for u in users), Decimal(0)))
	window = _usd(sum((u.window for u in users), Decimal(0)))
	calls = sum(u.calls for u in users)
	lines += [
		"",
		f"  {'total':<36}  {'':<24}  {today:>8}  {'':>7}  {window:>9}  {calls:>6}",
		f"  {len(users)} {'user' if len(users) == 1 else 'users'}, "
		f"{sum(1 for u in users if u.over)} at their limit now",
	]
	if stages is not None:
		lines += ["", f"By stage, last {days} days, all users:"]
		lines += [
			f"  {s.stage:<12} {s.provider:<10} {s.calls:>6} calls  {_usd(s.cost):>9}"
			for s in stages
		]
	return "\n".join(lines)


async def _main(days: int, with_stages: bool) -> str:
	settings = get_settings()
	engine = make_engine(settings.database_admin_url)
	try:
		async with make_session_factory(engine)() as session:
			await bind_system(session)
			users = await user_costs(session, days=days)
			stages = await stage_costs(session, days=days) if with_stages else None
		return render(users, stages, days=days)
	finally:
		await engine.dispose()


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(
		prog="python -m memo.admin.costs",
		description="What every user's AI has cost (read-only, ADR 0063).",
	)
	parser.add_argument("--days", type=int, default=7, help="the window, in days")
	parser.add_argument("--stages", action="store_true", help="add a by-stage table")
	args = parser.parse_args(argv)
	if not 1 <= args.days <= 365:
		print("--days must be 1 to 365", file=sys.stderr)
		return 2
	print(asyncio.run(_main(args.days, args.stages)))
	return 0


if __name__ == "__main__":
	sys.exit(main())
