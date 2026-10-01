"""Lists (ADR 0069): the named lists you keep, and the items your notes put on them.

Two halves, as for people: **reading** is a model call (ListsReader) that runs only on
notes that sound like a list, and **filing** is deterministic (`apply_list_items`): the
list a name means is decided by a normalised key, so "the shopping list", "grocery" and
"groceries" are one list and a note never makes a second one by spelling. An item is
kept at once (`accepted`): a wrong one is a swipe to remove, and removing it is the
label the eval wants.
"""

import datetime as dt
import re
import uuid
from dataclasses import dataclass
from typing import Protocol

import structlog
from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from memo.config import Settings
from memo.ids import uuid7
from memo.models import Capture, Derivation, UserList
from memo.prompts.lists_v1 import (
	LISTS_VERSION,
	SYSTEM_PROMPT,
	repair_message,
	user_message,
)
from memo.schemas.derivations import LIST_ITEM, UNREVIEWED
from memo.schemas.lists import ListDetail, ListItemOut, ListOut, ListsExtraction
from memo.services.extraction import ExtractionProvider, complete_validated
from memo.services.llm import LLMCall
from memo.services.people import normalize
from memo.services.prose import plain_dashes
from memo.services.providers import structured_provider
from memo.services.retry import TerminalError

log = structlog.get_logger("memo.lists")

STAGE = "lists"

# Words that make a note worth the reader's call. Broad on purpose: a false alarm costs
# one small call, a miss costs an item nobody sees.
_MIGHT = re.compile(
	r"\b(?:lists?|grocer\w*|shopping|wish ?list|watch ?list|to read|to watch|to buy"
	r"|gift idea|gift for|present for|packing|pack|buy|need some|need to get|get some"
	r"|out of|run out|pick up some|add(?:ing)? \w+(?: \w+){0,2} to"
	r"|(?:should|want to|wants to|going to|gonna) (?:read|watch)|recommend\w*)\b",
	re.IGNORECASE,
)


def might_list(text: str | None) -> bool:
	return bool(text) and _MIGHT.search(text or "") is not None


# Names that mean one list, whichever the author said.
CANON: dict[str, str] = {
	"grocery": "groceries",
	"groceries": "groceries",
	"shopping": "groceries",
	"food": "groceries",
	"supermarket": "groceries",
	"book": "books",
	"books": "books",
	"reading": "books",
	"to read": "books",
	"film": "films",
	"films": "films",
	"movie": "films",
	"movies": "films",
	"to watch": "films",
	"watch": "films",
	"watchlist": "films",
	"shows": "films",
	"series": "films",
	"tv": "films",
	"gift": "gifts",
	"gifts": "gifts",
	"present": "gifts",
	"presents": "gifts",
	"wish": "wishlist",
	"wishlist": "wishlist",
	"pack": "packing",
	"packing": "packing",
}


def list_key(name: str) -> str:
	"""The name as matched: case, accents, "the", "my" and a trailing "list" don't
	make a different list, and the common lists have one canonical key."""
	words = normalize(name).split()
	if words and words[0] == "the":
		words = words[1:]
	if len(words) > 1 and words[-1] == "list":
		words = words[:-1]
	key = " ".join(words)
	return CANON.get(key, key)[:80]


def display_name(name: str) -> str:
	"""How the list is shown: the canonical word for a common list, otherwise the
	author's own words without "the", "my" or a trailing "list"."""
	cleaned = re.sub(r"\s+", " ", name).strip(" .,:;")
	cleaned = re.sub(r"^(?:the|my)\s+", "", cleaned, flags=re.IGNORECASE)
	cleaned = re.sub(r"\s+list$", "", cleaned, flags=re.IGNORECASE)
	key = list_key(cleaned)
	return key if key in CANON.values() else (cleaned or key)[:80]


# --- reading --------------------------------------------------------------------------


@dataclass(frozen=True)
class ListsResult:
	extraction: ListsExtraction
	raw: str
	calls: list[LLMCall]


class ListsReaderLike(Protocol):
	@property
	def version(self) -> str: ...

	async def read(
		self,
		text: str,
		*,
		captured_at: dt.datetime,
		timezone: str,
		lists: list[str],
	) -> ListsResult: ...


class ListsReader:
	def __init__(self, provider: ExtractionProvider, *, max_repairs: int = 1) -> None:
		self._provider = provider
		self._max_repairs = max_repairs

	@property
	def version(self) -> str:
		return f"{LISTS_VERSION}/{self._provider.model}"

	async def read(
		self,
		text: str,
		*,
		captured_at: dt.datetime,
		timezone: str,
		lists: list[str],
	) -> ListsResult:
		extraction, raw, calls = await complete_validated(
			self._provider,
			system=SYSTEM_PROMPT,
			first=user_message(
				text, captured_at=captured_at, timezone=timezone, lists=lists
			),
			parse=ListsExtraction.model_validate_json,
			repair=repair_message,
			stage=STAGE,
			max_repairs=self._max_repairs,
		)
		return ListsResult(extraction=extraction, raw=raw, calls=calls)


class UnconfiguredListsReader:
	"""Stands in when the model's API key is unset, so the stage fails visibly."""

	version = f"{LISTS_VERSION}/unconfigured"

	async def read(
		self,
		text: str,
		*,
		captured_at: dt.datetime,
		timezone: str,
		lists: list[str],
	) -> ListsResult:
		raise TerminalError("lists are not configured (no API key for its model)")


def make_lists_reader(settings: Settings) -> ListsReaderLike:
	provider = structured_provider(
		settings, model=settings.lists_model, schema=ListsExtraction
	)
	if provider is None:
		return UnconfiguredListsReader()
	return ListsReader(provider)


# --- filing ---------------------------------------------------------------------------


async def list_names(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
	"""The lists a person keeps, for the reader to file into."""
	rows = await session.scalars(
		select(UserList.name)
		.where(UserList.user_id == user_id, UserList.archived_at.is_(None))
		.order_by(UserList.name)
	)
	return list(rows.all())


async def find_or_create(
	session: AsyncSession, user_id: uuid.UUID, name: str
) -> UserList:
	"""The list a name means: the one with its key, brought back if archived, or a
	new one in the author's words."""
	key = list_key(name)
	if not key:
		raise TerminalError("a list needs letters in its name")
	found = await session.scalar(
		select(UserList).where(UserList.user_id == user_id, UserList.key == key)
	)
	if found is not None:
		if found.archived_at is not None:
			found.archived_at = None
		return found
	made = UserList(id=uuid7(), user_id=user_id, name=display_name(name), key=key)
	session.add(made)
	await session.flush()
	return made


def _title_matches(title: str) -> ColumnElement[bool]:
	return func.lower(Derivation.payload["title"].astext) == title.lower()


async def open_item_exists(
	session: AsyncSession, list_id: uuid.UUID, title: str
) -> bool:
	"""Milk said twice is milk once, until it is ticked off."""
	found = await session.scalar(
		select(Derivation.id).where(
			Derivation.list_id == list_id,
			Derivation.kind == LIST_ITEM,
			Derivation.status != "rejected",
			Derivation.completed_at.is_(None),
			_title_matches(title),
		)
	)
	return found is not None


async def apply_list_items(
	session: AsyncSession,
	capture: Capture,
	extraction: ListsExtraction,
	*,
	version: str,
) -> list[Derivation]:
	"""File what a note puts on lists. Idempotent by replacement: a re-run of this
	version drops only items nobody has touched. Each item goes on the list its name
	means, which is created on first use, and is kept at once."""
	await session.execute(
		delete(Derivation).where(
			Derivation.capture_id == capture.id,
			Derivation.user_id == capture.user_id,
			Derivation.kind == LIST_ITEM,
			Derivation.extractor_version == version,
			Derivation.status.in_(UNREVIEWED),
		)
	)
	made: list[Derivation] = []
	for found in extraction.items:
		title = plain_dashes(found.title).strip()
		if not title:
			continue
		target = await find_or_create(session, capture.user_id, found.list)
		if await open_item_exists(session, target.id, title):
			continue
		item = Derivation(
			id=uuid7(),
			user_id=capture.user_id,
			capture_id=capture.id,
			kind=LIST_ITEM,
			payload={"title": title, "quantity": found.quantity},
			# Kept at once: a wrong item is a swipe to remove, and removing it is the
			# "wrong" label.
			status="accepted",
			extractor_version=version,
			confidence=found.confidence,
			list_id=target.id,
		)
		session.add(item)
		made.append(item)
	return made


# --- reading back ---------------------------------------------------------------------


def item_out(d: Derivation) -> ListItemOut:
	return ListItemOut(
		id=d.id,
		title=str(d.payload.get("title", "")),
		quantity=d.payload.get("quantity"),
		done_at=d.completed_at,
		capture_id=d.capture_id,
		added_by_hand=d.status == "user_created",
		created_at=d.created_at,
	)


def _list_out(lst: UserList, open_count: int, done_count: int) -> ListOut:
	return ListOut(
		id=lst.id,
		name=lst.name,
		open_count=open_count,
		done_count=done_count,
		archived=lst.archived_at is not None,
		created_at=lst.created_at,
		updated_at=lst.updated_at,
	)


async def lists_out(
	session: AsyncSession, user_id: uuid.UUID, *, include_archived: bool = False
) -> list[ListOut]:
	"""Every list with its counts, the ones with open items first."""
	stmt = select(UserList).where(UserList.user_id == user_id)
	if not include_archived:
		stmt = stmt.where(UserList.archived_at.is_(None))
	lists = (await session.scalars(stmt.order_by(UserList.name))).all()
	counts = (
		await session.execute(
			select(
				Derivation.list_id,
				func.count().filter(Derivation.completed_at.is_(None)),
				func.count().filter(Derivation.completed_at.is_not(None)),
			)
			.where(
				Derivation.user_id == user_id,
				Derivation.kind == LIST_ITEM,
				Derivation.status != "rejected",
			)
			.group_by(Derivation.list_id)
		)
	).all()
	by_list = {row[0]: (int(row[1]), int(row[2])) for row in counts}
	out = [_list_out(lst, *by_list.get(lst.id, (0, 0))) for lst in lists]
	out.sort(key=lambda o: (o.open_count == 0, o.name.lower()))
	return out


async def list_detail(session: AsyncSession, lst: UserList) -> ListDetail:
	"""One list and its items: open ones newest first, then the ticked ones."""
	items = (
		await session.scalars(
			select(Derivation)
			.where(
				Derivation.user_id == lst.user_id,
				Derivation.list_id == lst.id,
				Derivation.kind == LIST_ITEM,
				Derivation.status != "rejected",
			)
			.order_by(Derivation.created_at.desc(), Derivation.id.desc())
		)
	).all()
	open_items = [item_out(d) for d in items if d.completed_at is None]
	done = sorted(
		(item_out(d) for d in items if d.completed_at is not None),
		key=lambda i: i.done_at or i.created_at,
		reverse=True,
	)
	head = _list_out(lst, len(open_items), len(done))
	return ListDetail(**head.model_dump(), items=open_items + done)
