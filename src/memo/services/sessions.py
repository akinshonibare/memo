"""Server-issued sessions (ADR 0019). The raw token is random, shown to the client
once, and stored only as a SHA-256 hash - a database read never yields a usable
credential. Resolution happens before the request has a user, so it runs under the
system bypass; revocation happens as the user, under the ordinary tenant policy."""

import datetime as dt
import hashlib
import secrets
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from memo.models import Session

# last_seen_at is a hint, not an audit log: one write per request would be a
# write per read on every table, so it is refreshed at most this often.
TOUCH_EVERY = dt.timedelta(minutes=5)


def hash_token(raw: str) -> str:
	return hashlib.sha256(raw.encode()).hexdigest()


async def create(
	session: AsyncSession,
	user_id: uuid.UUID,
	*,
	ttl: dt.timedelta,
	device_name: str | None = None,
) -> str:
	raw = secrets.token_urlsafe(32)
	now = dt.datetime.now(dt.UTC)
	session.add(
		Session(
			user_id=user_id,
			token_hash=hash_token(raw),
			device_name=device_name,
			expires_at=now + ttl,
			last_seen_at=now,
		)
	)
	await session.flush()
	return raw


async def resolve(session: AsyncSession, raw: str) -> uuid.UUID | None:
	"""The user this token belongs to, or None if unknown, expired, or revoked."""
	now = dt.datetime.now(dt.UTC)
	row = await session.scalar(
		select(Session).where(Session.token_hash == hash_token(raw))
	)
	if row is None or row.revoked_at is not None or row.expires_at <= now:
		return None
	if now - row.last_seen_at > TOUCH_EVERY:
		row.last_seen_at = now
		await session.flush()
	return row.user_id


async def revoke_all(session: AsyncSession, user_id: uuid.UUID) -> int:
	result = await session.execute(
		update(Session)
		.where(Session.user_id == user_id, Session.revoked_at.is_(None))
		.values(revoked_at=dt.datetime.now(dt.UTC))
	)
	return int(result.rowcount)  # type: ignore[attr-defined]


async def revoke(session: AsyncSession, raw: str) -> bool:
	result = await session.execute(
		update(Session)
		.where(Session.token_hash == hash_token(raw), Session.revoked_at.is_(None))
		.values(revoked_at=dt.datetime.now(dt.UTC))
	)
	return bool(result.rowcount)  # type: ignore[attr-defined]
