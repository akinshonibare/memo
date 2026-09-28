"""ADR 0010: if no test fails when the boundary is removed, there is no boundary."""

import uuid
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from memo.config import SEED_USER_ID
from memo.db import bind_user


async def test_the_api_does_not_connect_as_the_owner(
	app: FastAPI, client: AsyncClient
) -> None:
	# RLS silently does not apply to the table owner. If this fails, every other test
	# in this file is passing for the wrong reason.
	async with app.state.session_factory() as session:
		role, superuser = (
			await session.execute(
				text(
					"SELECT current_user, rolsuper FROM pg_roles "
					"WHERE rolname = current_user"
				)
			)
		).one()
	assert role == "memo_app"
	assert superuser is False


async def test_user_b_sees_none_of_user_a(
	client: AsyncClient, as_user: Callable[[uuid.UUID], None], second_user: uuid.UUID
) -> None:
	created = await client.post(
		"/v1/captures",
		json={"text": "A's private thought", "client_token": "tok-a-private"},
	)
	a_capture_id = created.json()["id"]

	as_user(second_user)
	assert (await client.get("/v1/captures")).json()["items"] == []
	assert (await client.get(f"/v1/captures/{a_capture_id}")).status_code == 404


async def test_rls_holds_even_without_the_application_predicate(
	app: FastAPI, client: AsyncClient, second_user: uuid.UUID
) -> None:
	# The route-level WHERE user_id = ? is layer two. This exercises layer one alone:
	# a raw unfiltered SELECT as user B through the app's own connection.
	await client.post(
		"/v1/captures", json={"text": "A only", "client_token": "tok-a-only"}
	)

	async with app.state.session_factory() as session, session.begin():
		await bind_user(session, second_user)
		rows = (await session.execute(text("SELECT id FROM captures"))).all()
	assert rows == []


async def test_a_session_with_no_user_bound_sees_nothing(
	app: FastAPI, client: AsyncClient
) -> None:
	# Fail-closed: a forgotten bind returns zero rows, not everyone's rows.
	await client.post(
		"/v1/captures", json={"text": "exists", "client_token": "tok-exists"}
	)

	async with app.state.session_factory() as session, session.begin():
		rows = (await session.execute(text("SELECT id FROM captures"))).all()
	assert rows == []


async def test_user_b_cannot_insert_as_user_a(
	app: FastAPI, second_user: uuid.UUID
) -> None:
	async with app.state.session_factory() as session:
		await bind_user(session, second_user)
		with pytest.raises(ProgrammingError, match="row-level security"):
			await session.execute(
				text(
					"INSERT INTO captures (id, user_id, kind, text, client_token) "
					"VALUES (:id, :owner, 'text', 'forged', 'tok-forged')"
				),
				{"id": uuid.uuid4(), "owner": SEED_USER_ID},
			)
