"""The morning push (ADR 0047): today's events and the todos due today, at a time the
user chose, only on a day that has something in it.

Three rules carry the design:

- **At most once a day.** The day's row in `pushes` is claimed before anything is
  sent, and the claim is unique per user and local date. A crash after the claim loses
  that morning's push rather than sending it twice: a missed push costs one glance at
  the app, a duplicate costs trust in every push after it.
- **Only when there's something to say.** No events today and no todos due today, no
  push. A todo from an earlier day stays on Today in the app and is never pushed again:
  a daily reminder of the same slipped todo is the guilt memo refuses (brief).
- **Only to phones that are signed in.** Tokens are sent to through their session, so
  a revoked session (sign-out, "sign out everywhere") stops at once.

Delivery goes through Expo's push service, which holds the APNs key. Expo answers each
message with a ticket; a receipt for that ticket, fifteen minutes later, says whether
Apple accepted it. Both can say `DeviceNotRegistered` (the app is gone from that phone),
and the token is disabled.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import structlog
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memo.config import Settings
from memo.db import bind_system, bind_user
from memo.ids import uuid7
from memo.models import Push, PushToken, Session, User
from memo.services.morning import (
	MorningViewOut,
	build_morning_view,
	todays_view,
	tz_or_utc,
)
from memo.services.retry import RetryableError, TerminalError, classify_status
from memo.services.sessions import hash_token

log = structlog.get_logger("memo.push")

EXPO_SEND_URL = "https://exp.host/--/api/v2/push/send"
EXPO_RECEIPTS_URL = "https://exp.host/--/api/v2/push/getReceipts"
# The app registers this category with 👍 and 👎 buttons (src/push.ts).
MORNING_CATEGORY = "morning"
# A push due at 07:30 and still unsent at 09:30 (the worker was down) is skipped: a
# morning summary at lunchtime is the wrong message.
LATE_BY = dt.timedelta(hours=2)
# Expo keeps receipts for a day and has them after about fifteen minutes.
RECEIPTS_AFTER = dt.timedelta(minutes=15)
RECEIPTS_UNTIL = dt.timedelta(hours=23)
SHOWN_ITEMS = 4


# --- what the push says ---------------------------------------------------------------


@dataclass(frozen=True)
class Message:
	title: str
	body: str


def clock(moment: dt.datetime) -> str:
	"""10am, 2:30pm: short enough for a lock screen, and not tied to a locale."""
	hour = moment.hour % 12 or 12
	minutes = f":{moment.minute:02d}" if moment.minute else ""
	return f"{hour}{minutes}{'am' if moment.hour < 12 else 'pm'}"


def morning_message(view: MorningViewOut, today: dt.date) -> Message | None:
	"""Events today by time, then todos due today. None when there are neither:
	on an empty day memo doesn't ask for attention."""
	zone = tz_or_utc(view.timezone)
	items = [
		f"{clock(e.starts_at.astimezone(zone))} {e.title}"
		for e in sorted(view.events, key=lambda e: e.starts_at)
	]
	items += [t.title for t in view.todos if t.due == today]
	if not items:
		return None
	first, rest = items[0], items[1:]
	if not rest:
		only_event = view.events[0] if view.events else None
		where = only_event.location if only_event and only_event.location else None
		return Message(f"Today: {first}", where or "Nothing else on.")
	shown = rest[: SHOWN_ITEMS - 1]
	more = len(rest) - len(shown)
	body = " · ".join(shown) + (f" · {more} more" if more else "")
	return Message(f"Today: {first}", body)


# --- sending --------------------------------------------------------------------------


@dataclass(frozen=True)
class Outgoing:
	token_id: uuid.UUID
	token: str


@dataclass(frozen=True)
class Ticket:
	token_id: uuid.UUID
	ticket: str | None  # None when Expo refused the message outright
	error: str | None


class PushSender(Protocol):
	async def send(
		self, to: Sequence[Outgoing], message: Message, data: dict[str, Any]
	) -> list[Ticket]: ...

	async def receipts(self, ticket_ids: Sequence[str]) -> dict[str, str | None]:
		"""ticket id -> the error it ended in, or None for delivered. Missing ids
		aren't ready yet (or have expired)."""
		...


class ExpoPushSender:
	def __init__(self, http: httpx.AsyncClient, access_token: str | None) -> None:
		self._http = http
		self._headers = {"accept": "application/json"}
		if access_token:
			self._headers["authorization"] = f"Bearer {access_token}"

	async def _post(self, url: str, payload: Any) -> dict[str, Any]:
		try:
			response = await self._http.post(
				url, json=payload, headers=self._headers, timeout=20.0
			)
		except (httpx.TimeoutException, httpx.TransportError) as exc:
			raise RetryableError(f"expo push unreachable: {exc!r}") from exc
		if response.status_code >= 400:
			raise classify_status(response.status_code, response.text[:200])
		body: dict[str, Any] = response.json()
		if body.get("errors"):
			raise TerminalError(f"expo push refused the request: {body['errors']}")
		return body

	async def send(
		self, to: Sequence[Outgoing], message: Message, data: dict[str, Any]
	) -> list[Ticket]:
		# Expo takes up to 100 messages a request; one person has a phone or two.
		messages = [
			{
				"to": o.token,
				"title": message.title,
				"body": message.body,
				"data": data,
				"sound": "default",
				"categoryId": MORNING_CATEGORY,
			}
			for o in to
		]
		body = await self._post(EXPO_SEND_URL, messages)
		tickets = []
		for o, t in zip(to, body.get("data", []), strict=False):
			if t.get("status") == "ok":
				tickets.append(Ticket(o.token_id, str(t.get("id")), None))
			else:
				error = (t.get("details") or {}).get("error") or t.get("message")
				tickets.append(Ticket(o.token_id, None, str(error)))
		return tickets

	async def receipts(self, ticket_ids: Sequence[str]) -> dict[str, str | None]:
		body = await self._post(EXPO_RECEIPTS_URL, {"ids": list(ticket_ids)[:1000]})
		out: dict[str, str | None] = {}
		for ticket, receipt in (body.get("data") or {}).items():
			if receipt.get("status") == "ok":
				out[ticket] = None
			else:
				details = receipt.get("details") or {}
				out[ticket] = str(details.get("error") or receipt.get("message"))
		return out


def make_push_sender(settings: Settings, http: httpx.AsyncClient) -> PushSender:
	token = settings.expo_access_token
	return ExpoPushSender(http, token.get_secret_value() if token else None)


class NotSignedIn(Exception):
	"""The request's token isn't a session of the user it resolved to."""


# --- the phones -----------------------------------------------------------------------


async def register_token(
	factory: async_sessionmaker[AsyncSession],
	user_id: uuid.UUID,
	raw_session: str,
	token: str,
) -> None:
	"""This phone's token, under the session it is signed in with. A token another
	account held moves to this one: RLS can't see the other account's row, and a
	unique index would still refuse the insert, so the move runs as the system."""
	now = dt.datetime.now(dt.UTC)
	async with factory() as session:
		await bind_system(session)
		session_id = await session.scalar(
			select(Session.id).where(
				Session.token_hash == hash_token(raw_session),
				Session.user_id == user_id,
			)
		)
		if session_id is None:
			raise NotSignedIn
		await session.execute(
			insert(PushToken)
			.values(id=uuid7(), user_id=user_id, session_id=session_id, token=token)
			.on_conflict_do_update(
				index_elements=[PushToken.token],
				set_={
					"user_id": user_id,
					"session_id": session_id,
					"last_seen_at": now,
					"disabled_at": None,
				},
			)
		)
		await session.commit()


async def forget_token(session: AsyncSession, user_id: uuid.UUID, token: str) -> None:
	await session.execute(
		text("DELETE FROM push_tokens WHERE user_id = :u AND token = :t"),
		{"u": user_id, "t": token},
	)


async def live_tokens(session: AsyncSession, user_id: uuid.UUID) -> list[Outgoing]:
	"""Tokens on phones that are signed in right now."""
	now = dt.datetime.now(dt.UTC)
	rows = await session.execute(
		select(PushToken.id, PushToken.token)
		.join(Session, Session.id == PushToken.session_id)
		.where(
			PushToken.user_id == user_id,
			PushToken.disabled_at.is_(None),
			Session.revoked_at.is_(None),
			Session.expires_at > now,
		)
	)
	return [Outgoing(tid, tok) for tid, tok in rows.all()]


async def _disable(session: AsyncSession, token_ids: Sequence[uuid.UUID]) -> None:
	if token_ids:
		await session.execute(
			update(PushToken)
			.where(PushToken.id.in_(token_ids))
			.values(disabled_at=dt.datetime.now(dt.UTC))
		)


# --- the morning ----------------------------------------------------------------------


def is_due(now: dt.datetime, timezone: str, at: dt.time) -> tuple[bool, dt.date]:
	"""Whether the user's push time has come today, and not so long ago that the
	moment has passed. Asked per user like the 04:00 build, so DST needs nothing."""
	local = now.astimezone(tz_or_utc(timezone))
	due = dt.datetime.combine(local.date(), at, local.tzinfo)
	return due <= local < due + LATE_BY, local.date()


async def _claim(
	session: AsyncSession, user_id: uuid.UUID, today: dt.date
) -> uuid.UUID | None:
	"""Today's row, or None when another run already has it."""
	return await session.scalar(
		insert(Push)
		.values(
			id=uuid7(),
			user_id=user_id,
			kind="morning",
			local_date=today,
			status="sending",
		)
		.on_conflict_do_nothing(constraint="uq_pushes_user_day")
		.returning(Push.id)
	)


async def send_morning(
	factory: async_sessionmaker[AsyncSession],
	sender: PushSender,
	user_id: uuid.UUID,
	*,
	now: dt.datetime,
	today: dt.date,
) -> str:
	"""Claim, compose, send, record. Returns what happened, for the sweep's log."""
	async with factory() as session:
		await bind_user(session, user_id)
		push_id = await _claim(session, user_id, today)
		await session.commit()
	if push_id is None:
		return "already"

	async with factory() as session:
		await bind_user(session, user_id)
		push = await session.get(Push, push_id)
		assert push is not None
		view = await todays_view(session, user_id, now=now)
		if view is None:  # the 04:00 build hasn't run (a new user, a missed hour)
			view = await build_morning_view(session, user_id, now=now)
		message = morning_message(view, today)
		to = await live_tokens(session, user_id)
		if message is None or not to:
			push.status = "skipped"
			push.reason = "nothing today" if message is None else "no phone"
			await session.commit()
			return push.status
		push.title, push.body = message.title, message.body
		try:
			tickets = await sender.send(
				to, message, {"kind": "morning", "push_id": str(push_id)}
			)
		except (RetryableError, TerminalError) as exc:
			# Not retried: a morning push that arrives an hour late is a different
			# message. The row says what went wrong.
			push.status, push.reason = "failed", str(exc)[:500]
			await session.commit()
			log.warning("push.failed", push_id=str(push_id), error=str(exc))
			return "failed"
		push.tickets = [
			{"token_id": str(t.token_id), "ticket": t.ticket, "error": t.error}
			for t in tickets
		]
		gone = [t.token_id for t in tickets if t.error == "DeviceNotRegistered"]
		await _disable(session, gone)
		delivered = any(t.ticket for t in tickets)
		push.status = "sent" if delivered else "failed"
		push.reason = None if delivered else "; ".join(str(t.error) for t in tickets)
		push.sent_at = dt.datetime.now(dt.UTC)
		await session.commit()
	log.info("push.sent", push_id=str(push_id), phones=len(to), status=push.status)
	return push.status


async def send_due_mornings(
	factory: async_sessionmaker[AsyncSession],
	sender: PushSender,
	*,
	now: dt.datetime | None = None,
) -> dict[str, int]:
	now = now or dt.datetime.now(dt.UTC)
	async with factory() as session:
		await bind_system(session)
		users = (
			await session.execute(
				select(User.id, User.timezone, User.morning_push_at).where(
					User.morning_push_at.is_not(None)
				)
			)
		).all()
	outcomes: dict[str, int] = {}
	for user_id, timezone, at in users:
		if at is None:
			continue
		due, today = is_due(now, timezone, at)
		if not due:
			continue
		outcome = await send_morning(factory, sender, user_id, now=now, today=today)
		outcomes[outcome] = outcomes.get(outcome, 0) + 1
	return outcomes


async def check_receipts(
	factory: async_sessionmaker[AsyncSession],
	sender: PushSender,
	*,
	now: dt.datetime | None = None,
) -> int:
	"""Read Expo's receipts for pushes sent 15 minutes to a day ago, once each, and
	disable the tokens Apple says are gone. Other errors are logged, and kept on the
	push, where "why didn't I get it?" can be answered."""
	now = now or dt.datetime.now(dt.UTC)
	async with factory() as session:
		await bind_system(session)
		pushes = (
			await session.scalars(
				select(Push).where(
					Push.status == "sent",
					Push.receipts_checked_at.is_(None),
					Push.sent_at <= now - RECEIPTS_AFTER,
					Push.sent_at > now - RECEIPTS_UNTIL,
				)
			)
		).all()
		ids = [t["ticket"] for p in pushes for t in p.tickets if t.get("ticket")]
		if not ids:
			return 0
		receipts = await sender.receipts(ids)
		gone: list[uuid.UUID] = []
		for push in pushes:
			updated = []
			for t in push.tickets:
				error = receipts.get(t.get("ticket") or "")
				if error:
					log.warning("push.receipt_error", push_id=str(push.id), error=error)
				if error == "DeviceNotRegistered":
					gone.append(uuid.UUID(t["token_id"]))
				updated.append(
					{**t, "receipt": error or "ok"}
					if t.get("ticket") in receipts
					else t
				)
			push.tickets = updated
			push.receipts_checked_at = now
		await _disable(session, gone)
		await session.commit()
	return len(pushes)
