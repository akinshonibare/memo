"""Reset everyone's data and keep their accounts (ADR 0059).

A one-off for the operator, run from a shell in the api container, the only place with
a route to production Postgres (docs/runbooks/reset-data.md):

	railway ssh --service api
	python -m memo.admin.reset --dry-run
	python -m memo.admin.reset

Every table in the database is kept or wiped by name, below. A table in neither list
stops the reset, so a table added later gets classified on purpose instead of landing
in whichever list a default would have put it.

The order is the database, in one transaction, then the bucket, then the queue. The
database is the record: once it is empty, an object left in the bucket or a job left in
Redis is an orphan that a second run removes, never a row pointing at nothing. The
per-user wipe (DELETE /v1/me/data) goes the other way round because it finds its keys
through the rows; this lists the bucket itself.
"""

import argparse
import asyncio
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from arq.constants import (
	in_progress_key_prefix,
	job_key_prefix,
	result_key_prefix,
	retry_key_prefix,
)
from redis.asyncio import Redis
from sqlalchemy import CursorResult, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import get_settings
from memo.db import bind_system, make_engine, make_session_factory
from memo.services.storage import S3ObjectStore

# Accounts, the sessions that keep phones signed in, and those phones' push tokens:
# everyone opens the app to an empty memo, still signed in, with their settings. And
# the schema's own bookkeeping.
KEEP = ("alembic_version", "push_tokens", "sessions", "users")
# Everything notes produced, directly or downstream, the things you added alongside
# them (people, habits, conversations), and the ledgers about them: what ran
# (job_runs), what it cost (llm_calls), what memo pushed and your thumbs on it (pushes).
WIPE = (
	"captures",
	"chunks",
	"conversations",
	"derivations",
	"habits",
	"lists",
	"job_runs",
	"llm_calls",
	"message_citations",
	"messages",
	"morning_views",
	"people",
	"pushes",
	"recordings",
	"resurfacings",
)

# arq's own per-job keys (arq.constants): a job's arguments, its running marker, its
# try count, its result. The queue itself is the sorted set named arq_queue_name.
# Everything else in Redis (rate-limit windows, the worker's health check) stays.
ARQ_KEY_PREFIXES = (
	job_key_prefix,
	in_progress_key_prefix,
	retry_key_prefix,
	result_key_prefix,
)

# TRUNCATE waits for every open transaction on the tables it empties, and every query
# that arrives after it waits behind it. Give up rather than stall the live app.
LOCK_TIMEOUT = "10s"


class Refused(Exception):
	"""The reset did not start, or was rolled back: nothing changed."""


class Bucket(Protocol):
	bucket: str

	async def list_keys(self) -> list[str]: ...

	async def delete_keys(self, keys: Sequence[str]) -> None: ...


@dataclass(frozen=True)
class Plan:
	database: str
	users: int
	rows: dict[str, int]
	objects: int
	queued: int
	arq_keys: int

	@property
	def phrase(self) -> str:
		"""What the operator types to go ahead. The count ties it to what was just
		shown, so it has to be read off the plan rather than typed from memory."""
		return f"reset {self.users} {'user' if self.users == 1 else 'users'}"


@dataclass(frozen=True)
class Result:
	plan: Plan
	done: bool
	rows_after: dict[str, int] = field(default_factory=dict)
	users_after: int = 0
	readings_cleared: int = 0
	objects_deleted: int = 0
	objects_left: int = 0
	redis_removed: dict[str, int] = field(default_factory=dict)


_FOREIGN_KEYS_INTO_WIPE = text(
	"""
	SELECT c.conname, src_ns.nspname || '.' || src.relname, dst.relname
	FROM pg_constraint c
	JOIN pg_class src ON src.oid = c.conrelid
	JOIN pg_namespace src_ns ON src_ns.oid = src.relnamespace
	JOIN pg_class dst ON dst.oid = c.confrelid
	JOIN pg_namespace dst_ns ON dst_ns.oid = dst.relnamespace
	WHERE c.contype = 'f'
		AND dst_ns.nspname = 'public' AND dst.relname = ANY(:wipe)
		AND NOT (src_ns.nspname = 'public' AND src.relname = ANY(:wipe))
	ORDER BY 2, 1
	"""
)


async def _check(session: AsyncSession) -> None:
	"""Refuse unless every table is classified and TRUNCATE ... CASCADE can reach no
	table outside WIPE. CASCADE empties any table with a foreign key into one it
	truncates, silently; a kept table pointing at a wiped one would go with it."""
	tables = set(
		await session.scalars(
			text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
		)
	)
	unclassified = sorted(tables - set(KEEP) - set(WIPE))
	if unclassified:
		raise Refused(
			f"unclassified table(s): {', '.join(unclassified)}. Add each to KEEP or "
			"WIPE in memo/admin/reset.py, deliberately, and deploy that first."
		)
	missing = sorted((set(KEEP) | set(WIPE)) - tables)
	if missing:
		raise Refused(
			"table(s) this command expects are not in the database: "
			f"{', '.join(missing)}. Is it at alembic head?"
		)
	reach = (await session.execute(_FOREIGN_KEYS_INTO_WIPE, {"wipe": list(WIPE)})).all()
	if reach:
		named = "; ".join(f"{src} -> {dst} ({name})" for name, src, dst in reach)
		raise Refused(
			"TRUNCATE ... CASCADE would also empty a table outside WIPE, through "
			f"foreign keys: {named}"
		)


async def _users(session: AsyncSession) -> int:
	return int(await session.scalar(text("SELECT count(*) FROM public.users")) or 0)


async def _rows(session: AsyncSession) -> dict[str, int]:
	rows: dict[str, int] = {}
	for table in WIPE:
		count = await session.scalar(text(f"SELECT count(*) FROM public.{table}"))
		rows[table] = int(count or 0)
	return rows


async def _arq_keys(redis: Redis) -> dict[str, list[Any]]:
	"""arq's per-job keys, by prefix. SCAN, not KEYS: KEYS walks the whole keyspace in
	one command and blocks Redis for the live API and worker while it does."""
	found: dict[str, list[Any]] = {}
	for prefix in ARQ_KEY_PREFIXES:
		found[prefix] = [
			k async for k in redis.scan_iter(match=f"{prefix}*", count=500)
		]
	return found


def _describe(engine: AsyncEngine) -> str:
	url = engine.url
	return f"{url.database} on {url.host}:{url.port or 5432} as {url.username}"


async def make_plan(
	engine: AsyncEngine, bucket: Bucket, redis: Redis, *, queue_name: str
) -> Plan:
	"""What a reset would remove, read without changing anything."""
	async with make_session_factory(engine)() as session:
		await bind_system(session)
		await _check(session)
		users = await _users(session)
		rows = await _rows(session)
	arq_keys = await _arq_keys(redis)
	return Plan(
		database=_describe(engine),
		users=users,
		rows=rows,
		objects=len(await bucket.list_keys()),
		queued=int(await redis.zcard(queue_name)),
		arq_keys=sum(len(keys) for keys in arq_keys.values()),
	)


def render_plan(plan: Plan, *, bucket: str, queue_name: str) -> str:
	width = max(len(t) for t in WIPE) + 2
	lines = [
		"memo reset: wipe every user's data, keep the accounts (ADR 0059)",
		"",
		f"  database  {plan.database}",
		f"  users     {plan.users:,}, kept with their sessions, push tokens and "
		"settings;",
		"            each one's current Bible passage (bible_reading) is cleared",
		"",
		"  wiped, in one transaction (TRUNCATE ... RESTART IDENTITY CASCADE):",
	]
	lines += [f"    {t:<{width}}{plan.rows[t]:>12,}" for t in WIPE]
	lines += [
		f"    {'total':<{width}}{sum(plan.rows.values()):>12,}",
		"",
		f"  bucket    {bucket}: {plan.objects:,} objects, deleted for good "
		"(no versioning)",
		f"  queue     {queue_name!r}: {plan.queued:,} queued jobs and "
		f"{plan.arq_keys:,} arq job keys, removed",
		"",
	]
	return "\n".join(lines)


async def _clear_queue(redis: Redis, queue_name: str) -> dict[str, int]:
	# Count and delete in one MULTI, so a job enqueued in between is counted too.
	async with redis.pipeline(transaction=True) as pipe:
		pipe.zcard(queue_name)
		pipe.delete(queue_name)
		queued, _ = await pipe.execute()
	removed = {"queued jobs": int(queued)}
	for prefix, keys in (await _arq_keys(redis)).items():
		for start in range(0, len(keys), 500):
			await redis.delete(*keys[start : start + 500])
		removed[f"{prefix}* keys"] = len(keys)
	return removed


async def reset(
	engine: AsyncEngine,
	bucket: Bucket,
	redis: Redis,
	*,
	queue_name: str,
	confirm: Callable[[str], str],
	dry_run: bool = False,
) -> Result:
	"""Plan, show, ask, then wipe. Raises Refused, with nothing changed, when a table
	is unclassified, when a foreign key would carry the TRUNCATE into a kept table, or
	when the transaction fails and is rolled back."""
	plan = await make_plan(engine, bucket, redis, queue_name=queue_name)
	print(render_plan(plan, bucket=bucket.bucket, queue_name=queue_name))
	if dry_run:
		print("Dry run: nothing changed.")
		return Result(plan, done=False)
	try:
		typed = confirm(f'Type "{plan.phrase}" to go ahead; anything else stops: ')
	except EOFError:
		typed = ""
	if typed != plan.phrase:
		print("Aborted: nothing changed.")
		return Result(plan, done=False)

	factory = make_session_factory(engine)
	async with factory() as session:
		try:
			await bind_system(session)
			await session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
			# Again, inside the transaction: a deploy may have migrated, or someone
			# signed up, while the operator read the plan.
			await _check(session)
			if await _users(session) != plan.users:
				raise Refused("the number of users changed since the plan; run again")
			tables = ", ".join(f"public.{t}" for t in WIPE)
			await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
			cleared = cast(
				CursorResult[Any],
				await session.execute(
					text(
						"UPDATE users SET bible_reading = NULL "
						"WHERE bible_reading IS NOT NULL"
					)
				),
			)
		except DBAPIError as exc:
			await session.rollback()
			raise Refused(
				f"the database stopped the reset and it was rolled back: {exc.orig}"
			) from exc
		await session.commit()
	readings_cleared = cleared.rowcount
	print(
		f"Database: committed. {len(WIPE)} tables emptied; the current Bible passage "
		f"cleared on {readings_cleared:,} accounts."
	)

	try:
		keys = await bucket.list_keys()
		await bucket.delete_keys(keys)
		print(f"Bucket: {len(keys):,} objects deleted.")
		removed = await _clear_queue(redis, queue_name)
		print(
			"Queue: removed "
			+ ", ".join(f"{n:,} {what}" for what, n in removed.items())
			+ "."
		)
	except Exception:
		print(
			"The database reset is committed, but clearing the bucket or the queue "
			"failed (below). Run this again to finish: the plan will show 0 rows.",
			file=sys.stderr,
		)
		raise

	async with factory() as session:
		await bind_system(session)
		rows_after = await _rows(session)
		users_after = await _users(session)
	result = Result(
		plan,
		done=True,
		rows_after=rows_after,
		users_after=users_after,
		readings_cleared=readings_cleared,
		objects_deleted=len(keys),
		objects_left=len(await bucket.list_keys()),
		redis_removed=removed,
	)
	print(render_result(result))
	return result


def render_result(result: Result) -> str:
	plan = result.plan
	width = max(len(t) for t in WIPE) + 2
	lines = ["", "Reset done. Now:", ""]
	lines.append(
		f"    {'users':<{width}}{result.users_after:>12,}  (was {plan.users:,})"
	)
	lines += [
		f"    {t:<{width}}{result.rows_after[t]:>12,}  (was {plan.rows[t]:,})"
		for t in WIPE
	]
	lines.append(
		f"    {'bucket objects':<{width}}{result.objects_left:>12,}  "
		f"(was {plan.objects:,})"
	)
	left = {t: n for t, n in result.rows_after.items() if n}
	if left or result.objects_left:
		lines += [
			"",
			"Not everything is empty: "
			+ ", ".join(
				[f"{t} {n:,}" for t, n in left.items()]
				+ ([f"bucket {result.objects_left:,}"] if result.objects_left else [])
			)
			+ ". Something wrote after the reset (a worker job, a phone's outbox,"
			" the morning build). Look before running it again.",
		]
	lines += [
		"",
		"Phones remove memo's Calendar events, Reminders todos and event",
		"notifications on their next sync, when the app next comes to the front.",
	]
	return "\n".join(lines)


async def _main(*, dry_run: bool) -> Result:
	settings = get_settings()
	engine = make_engine(settings.database_admin_url)
	redis = Redis.from_url(settings.redis_url)
	try:
		print(f"APP_ENV={settings.app_env}\n")
		return await reset(
			engine,
			S3ObjectStore.from_settings(settings),
			redis,
			queue_name=settings.arq_queue_name,
			confirm=input,
			dry_run=dry_run,
		)
	finally:
		await redis.aclose()
		await engine.dispose()


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(
		prog="python -m memo.admin.reset",
		description="Wipe every user's data, keep their accounts (ADR 0059).",
	)
	parser.add_argument(
		"--dry-run", action="store_true", help="show the plan and change nothing"
	)
	args = parser.parse_args(argv)
	# The phrase is typed, never piped, passed as a flag or read from the environment:
	# a reset that can run unattended is one that can run by accident.
	if not args.dry_run and not sys.stdin.isatty():
		print(
			"Refused: the confirmation has to be typed at a terminal, and stdin is "
			"not one. Nothing changed.",
			file=sys.stderr,
		)
		return 2
	try:
		result = asyncio.run(_main(dry_run=args.dry_run))
	except Refused as exc:
		print(f"Refused: {exc}\nNothing changed.", file=sys.stderr)
		return 2
	return 0 if result.done or args.dry_run else 1


if __name__ == "__main__":
	sys.exit(main())
