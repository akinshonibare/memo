"""Export and deletion (issue #49). A product owes its users their data and its
removal; the schema was built multi-tenant, so both are one user's rows."""

import datetime as dt
import uuid
import zoneinfo
from typing import Annotated, Any, cast

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select, text
from sqlalchemy.engine import CursorResult

from memo.auth import CurrentUser
from memo.config import Settings, get_settings
from memo.db import DbSession
from memo.models import (
	Capture,
	Conversation,
	Derivation,
	Message,
	Person,
	Recording,
	User,
)
from memo.routers.recordings import get_object_store
from memo.schemas.ask import MessageOut
from memo.schemas.captures import CaptureOut
from memo.schemas.derivations import DerivationOut
from memo.services.speech import VOICES
from memo.services.storage import ObjectStore

router = APIRouter(prefix="/me", tags=["me"])
log = structlog.get_logger("memo.me")


class MeOut(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: uuid.UUID
	timezone: str
	name: str | None = Field(validation_alias="display_name")
	email: str | None  # from Apple; may be a private relay address
	voice: str | None
	created_at: dt.datetime


class MePatch(BaseModel):
	"""Any of: the name the assistant uses, the voice it speaks in, the timezone.

	The timezone drives the morning view's 04:00 and how extraction reads "noon", so it
	is the user's to set - from the device, not from a config file. An empty name
	clears it (the assistant goes back to "you")."""

	model_config = ConfigDict(extra="forbid")

	timezone: str | None = Field(default=None, min_length=1, max_length=64)
	name: str | None = Field(default=None, max_length=60)
	voice: str | None = None

	@field_validator("timezone")
	@classmethod
	def _is_a_real_zone(cls, value: str | None) -> str | None:
		if value is None:
			return value
		try:
			zoneinfo.ZoneInfo(value)
		except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
			raise ValueError(f"unknown IANA timezone: {value!r}") from exc
		return value

	@field_validator("name")
	@classmethod
	def _clean_name(cls, value: str | None) -> str | None:
		if value is None:
			return value
		# One line, no control characters: this string is placed in a prompt.
		cleaned = " ".join("".join(ch for ch in value if ch.isprintable()).split())
		return cleaned

	@field_validator("voice")
	@classmethod
	def _known_voice(cls, value: str | None) -> str | None:
		if value is not None and value not in VOICES:
			raise ValueError(f"unknown voice {value!r}")
		return value

	@model_validator(mode="after")
	def _something(self) -> "MePatch":
		if not self.model_fields_set:
			raise ValueError("provide timezone, name or voice")
		return self


async def _load_user(session: DbSession, user_id: uuid.UUID) -> User:
	user = await session.get(User, user_id)
	if user is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
	return user


@router.get("", response_model=MeOut)
async def read_me(session: DbSession, user_id: CurrentUser) -> User:
	return await _load_user(session, user_id)


@router.patch("", response_model=MeOut)
async def update_me(body: MePatch, session: DbSession, user_id: CurrentUser) -> User:
	user = await _load_user(session, user_id)
	fields = body.model_fields_set
	if "timezone" in fields and body.timezone is not None:
		user.timezone = body.timezone
	if "name" in fields:
		user.display_name = body.name or None
	if "voice" in fields:
		user.voice = body.voice
	await session.flush()
	return user


class VoiceOut(BaseModel):
	id: str
	label: str


@router.get("/voices", response_model=list[VoiceOut])
async def list_voices(user_id: CurrentUser) -> list[VoiceOut]:
	"""The voices the app offers for spoken answers (ADR 0025)."""
	return [VoiceOut(id=k, label=v) for k, v in VOICES.items()]


class RecordingExport(BaseModel):
	id: uuid.UUID
	mime: str
	bytes: int | None
	duration_ms: int | None
	storage_key: str
	download_url: str | None


class ConversationExport(BaseModel):
	id: uuid.UUID
	title: str | None
	created_at: dt.datetime
	messages: list[MessageOut]


class CaptureExport(CaptureOut):
	derivations: list[DerivationOut]


class PersonExport(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: uuid.UUID
	name: str
	aliases: list[str]
	note: str | None
	reach_out_every_days: int | None
	snoozed_until: dt.date | None
	created_at: dt.datetime


class Export(BaseModel):
	exported_at: dt.datetime
	user_id: uuid.UUID
	captures: list[CaptureExport]
	# Derivations kept after their note was deleted (ADR 0022), and people contact
	# you logged by hand (ADR 0029), which never had a note.
	derivations_without_note: list[DerivationOut]
	recordings: list[RecordingExport]
	conversations: list[ConversationExport]
	# Who your notes are about; each mention (a derivation) carries its person_id.
	people: list[PersonExport] = []


@router.get("/export", response_model=Export)
async def export_everything(
	session: DbSession,
	user_id: CurrentUser,
	settings: Annotated[Settings, Depends(get_settings)],
	store: Annotated[ObjectStore, Depends(get_object_store)],
) -> Export:
	"""Everything the user owns, as JSON. Recording bytes come via time-limited
	download URLs rather than inline: audio does not belong in a JSON body."""
	captures = (
		await session.scalars(
			select(Capture)
			.where(Capture.user_id == user_id)
			.order_by(Capture.created_at, Capture.id)
		)
	).all()
	derivations = (
		await session.scalars(
			select(Derivation)
			.where(Derivation.user_id == user_id)
			.order_by(Derivation.created_at, Derivation.id)
		)
	).all()
	by_capture: dict[uuid.UUID, list[DerivationOut]] = {}
	unattached: list[DerivationOut] = []
	for d in derivations:
		out = DerivationOut.model_validate(d)
		if d.capture_id is None:
			unattached.append(out)
		else:
			by_capture.setdefault(d.capture_id, []).append(out)

	recordings = []
	for r in (
		await session.scalars(select(Recording).where(Recording.user_id == user_id))
	).all():
		url: str | None = None
		if await store.exists(r.storage_key):
			url = await store.presign_get(
				r.storage_key, settings.upload_url_ttl_seconds
			)
		recordings.append(
			RecordingExport(
				id=r.id,
				mime=r.mime,
				bytes=r.bytes,
				duration_ms=r.duration_ms,
				storage_key=r.storage_key,
				download_url=url,
			)
		)

	conversations = []
	for c in (
		await session.scalars(
			select(Conversation)
			.where(Conversation.user_id == user_id)
			.order_by(Conversation.created_at)
		)
	).all():
		messages = (
			await session.scalars(
				select(Message)
				.where(Message.conversation_id == c.id, Message.user_id == user_id)
				.order_by(Message.created_at, Message.id)
			)
		).all()
		conversations.append(
			ConversationExport(
				id=c.id,
				title=c.title,
				created_at=c.created_at,
				messages=[MessageOut.model_validate(m) for m in messages],
			)
		)

	people = (
		await session.scalars(
			select(Person).where(Person.user_id == user_id).order_by(Person.name)
		)
	).all()

	return Export(
		exported_at=dt.datetime.now(dt.UTC),
		user_id=user_id,
		captures=[
			CaptureExport(
				**CaptureOut.model_validate(c).model_dump(),
				derivations=by_capture.get(c.id, []),
			)
			for c in captures
		],
		derivations_without_note=unattached,
		recordings=recordings,
		conversations=conversations,
		people=[PersonExport.model_validate(p) for p in people],
	)


@router.delete("/data", status_code=status.HTTP_200_OK)
async def delete_everything(
	session: DbSession,
	user_id: CurrentUser,
	confirm: Annotated[str | None, Query()] = None,
	store: Annotated[ObjectStore, Depends(get_object_store)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
	"""Hard delete of everything the user owns; the account row and its sessions stay,
	so the app remains signed in to an empty account (DELETE /v1/me removes those)."""
	if confirm != str(user_id):
		raise HTTPException(
			status.HTTP_400_BAD_REQUEST,
			"pass confirm=<your user id> to delete everything you own",
		)
	return {"deleted": await _purge(session, user_id, store)}


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
	session: DbSession,
	user_id: CurrentUser,
	confirm: Annotated[str | None, Query()] = None,
	store: Annotated[ObjectStore, Depends(get_object_store)] = None,  # type: ignore[assignment]
) -> None:
	"""Delete the account: every row the user owns, every session, then the user.

	Required in-app for any app that offers Sign in with Apple (App Store guideline
	5.1.1(v)). Not yet done: revoking the Apple token with Apple's REST API, which needs
	the team's Sign in with Apple key - until then, a new sign-in makes a new account.
	"""
	if confirm != str(user_id):
		raise HTTPException(
			status.HTTP_400_BAD_REQUEST,
			"pass confirm=<your user id> to delete your account",
		)
	counts = await _purge(session, user_id, store)
	await session.execute(
		text("DELETE FROM sessions WHERE user_id = :uid"), {"uid": user_id}
	)
	await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
	log.info("account.deleted", user_id=str(user_id), **counts)


async def _purge(
	session: DbSession, user_id: uuid.UUID, store: ObjectStore
) -> dict[str, Any]:
	"""Every row and object the user owns, except the account and its sessions.

	Hard rather than soft: an orphaned embedding still answers questions, so a
	soft-deleted chunk would have to be filtered out of every retrieval path forever.
	Recording bytes are removed from the bucket first, because a row that is gone
	cannot tell us its key afterwards."""
	keys = (
		await session.scalars(
			select(Recording.storage_key).where(Recording.user_id == user_id)
		)
	).all()
	for key in keys:
		await store.delete(key)
	counts: dict[str, Any] = {"recording_objects": len(keys)}
	# Chunks and derivations explicitly, before captures: deleting a capture keeps its
	# derivations (ADR 0022), which is right for one note and wrong for "everything".
	# Chunks go first because a text chunk cannot outlive its capture (the CHECK).
	for table in (
		"conversations",
		"chunks",
		"derivations",
		"people",
		"captures",
		"recordings",
		"morning_views",
		"llm_calls",
	):
		result = cast(
			CursorResult[Any],
			await session.execute(
				text(f"DELETE FROM {table} WHERE user_id = :uid"), {"uid": user_id}
			),
		)
		counts[table] = result.rowcount
	return counts
