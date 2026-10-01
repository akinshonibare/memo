"""Lists (ADR 0069): the Lists screen, one list and its items, and the taps that add,
tick, untick, rename, archive or remove.

Items are derivations of kind 'list_item', but they live here rather than under
/v1/derivations: the generic routes serve the Planner, the phone sync and apps built
before Lists, none of which should ever meet one.
"""

import datetime as dt
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from memo.auth import CurrentUser
from memo.db import DbSession, commit_and_rebind
from memo.ids import uuid7
from memo.models import Derivation, UserList
from memo.schemas.derivations import LIST_ITEM
from memo.schemas.lists import (
	ListCreate,
	ListDetail,
	ListItemCreate,
	ListItemOut,
	ListItemPatch,
	ListOut,
	ListPatch,
	ListsOut,
)
from memo.services.lists import (
	display_name,
	find_or_create,
	item_out,
	list_detail,
	list_key,
	lists_out,
	open_item_exists,
)
from memo.services.prose import plain_dashes

router = APIRouter(prefix="/lists", tags=["lists"])


async def _list(session: DbSession, user_id: uuid.UUID, list_id: uuid.UUID) -> UserList:
	found = await session.scalar(
		select(UserList).where(UserList.id == list_id, UserList.user_id == user_id)
	)
	if found is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND, "list not found")
	return found


async def _item(
	session: DbSession, user_id: uuid.UUID, list_id: uuid.UUID, item_id: uuid.UUID
) -> Derivation:
	found = await session.scalar(
		select(Derivation).where(
			Derivation.id == item_id,
			Derivation.user_id == user_id,
			Derivation.list_id == list_id,
			Derivation.kind == LIST_ITEM,
			Derivation.status != "rejected",
		)
	)
	if found is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")
	return found


async def _out(session: DbSession, lst: UserList) -> ListOut:
	detail = await list_detail(session, lst)
	return ListOut(**{k: v for k, v in detail.model_dump().items() if k != "items"})


@router.get("", response_model=ListsOut)
async def lists(
	session: DbSession, user_id: CurrentUser, archived: bool = False
) -> ListsOut:
	"""Your lists with their counts; archived ones only when asked for."""
	return ListsOut(lists=await lists_out(session, user_id, include_archived=archived))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ListOut)
async def create_list(
	body: ListCreate, session: DbSession, user_id: CurrentUser
) -> ListOut:
	"""A list you name. A name that means an existing list (the shopping list is
	groceries) gives you that list back, unarchived if it was."""
	if not list_key(body.name):
		raise HTTPException(
			status.HTTP_422_UNPROCESSABLE_CONTENT, "a list needs letters in its name"
		)
	lst = await find_or_create(session, user_id, body.name)
	await commit_and_rebind(session, user_id)
	return await _out(session, lst)


@router.get("/{list_id}", response_model=ListDetail)
async def one_list(
	list_id: uuid.UUID, session: DbSession, user_id: CurrentUser
) -> ListDetail:
	return await list_detail(session, await _list(session, user_id, list_id))


@router.patch("/{list_id}", response_model=ListOut)
async def patch_list(
	list_id: uuid.UUID, body: ListPatch, session: DbSession, user_id: CurrentUser
) -> ListOut:
	"""Rename, archive, or bring back. A rename onto another list's name is refused:
	merging is a decision, not a side effect of a typo."""
	lst = await _list(session, user_id, list_id)
	if body.name is not None:
		key = list_key(body.name)
		if not key:
			raise HTTPException(
				status.HTTP_422_UNPROCESSABLE_CONTENT,
				"a list needs letters in its name",
			)
		other = await session.scalar(
			select(UserList.id).where(
				UserList.user_id == user_id, UserList.key == key, UserList.id != lst.id
			)
		)
		if other is not None:
			raise HTTPException(
				status.HTTP_409_CONFLICT, "you already have a list by that name"
			)
		lst.name = display_name(body.name)
		lst.key = key
	if body.archived is not None:
		lst.archived_at = dt.datetime.now(dt.UTC) if body.archived else None
	await commit_and_rebind(session, user_id)
	return await _out(session, lst)


@router.delete("/{list_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_list(
	list_id: uuid.UUID, session: DbSession, user_id: CurrentUser
) -> None:
	"""The list and everything on it (the items cascade with it)."""
	lst = await _list(session, user_id, list_id)
	await session.delete(lst)
	await commit_and_rebind(session, user_id)


@router.post(
	"/{list_id}/items", status_code=status.HTTP_201_CREATED, response_model=ListItemOut
)
async def add_item(
	list_id: uuid.UUID,
	body: ListItemCreate,
	session: DbSession,
	user_id: CurrentUser,
) -> ListItemOut:
	"""An item you type. The same open item already on the list is given back rather
	than doubled."""
	lst = await _list(session, user_id, list_id)
	title = plain_dashes(body.title).strip()
	if await open_item_exists(session, lst.id, title):
		existing = await session.scalar(
			select(Derivation).where(
				Derivation.list_id == lst.id,
				Derivation.kind == LIST_ITEM,
				Derivation.status != "rejected",
				Derivation.completed_at.is_(None),
			)
		)
		assert existing is not None
		return item_out(existing)
	item = Derivation(
		id=uuid7(),
		user_id=user_id,
		capture_id=None,
		kind=LIST_ITEM,
		payload={"title": title, "quantity": body.quantity},
		status="user_created",
		extractor_version="user",
		confidence=None,
		list_id=lst.id,
	)
	session.add(item)
	await commit_and_rebind(session, user_id)
	return item_out(item)


@router.patch("/{list_id}/items/{item_id}", response_model=ListItemOut)
async def patch_item(
	list_id: uuid.UUID,
	item_id: uuid.UUID,
	body: ListItemPatch,
	session: DbSession,
	user_id: CurrentUser,
) -> ListItemOut:
	"""Tick, untick, or reword."""
	item = await _item(session, user_id, list_id, item_id)
	payload = dict(item.payload)
	if body.title is not None:
		payload["title"] = plain_dashes(body.title).strip()
	if body.quantity is not None:
		payload["quantity"] = body.quantity or None
	item.payload = payload
	if body.done is not None:
		item.completed_at = dt.datetime.now(dt.UTC) if body.done else None
	await commit_and_rebind(session, user_id)
	return item_out(item)


@router.delete("/{list_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_item(
	list_id: uuid.UUID, item_id: uuid.UUID, session: DbSession, user_id: CurrentUser
) -> None:
	"""Off the list. One memo filed is marked rejected, the label the eval wants; one
	you typed is simply gone."""
	item = await _item(session, user_id, list_id, item_id)
	if item.status == "user_created":
		await session.delete(item)
	else:
		item.status = "rejected"
		item.reviewed_at = dt.datetime.now(dt.UTC)
	await commit_and_rebind(session, user_id)
