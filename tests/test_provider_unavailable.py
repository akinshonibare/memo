"""A provider that refuses our account (out of credit, key refused) is waited out,
not failed note by note, and its own text never reaches a person (ADR 0066)."""

import uuid
from collections.abc import AsyncIterator

import anthropic
import httpx2
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.jobs import ARQ_MAX_TRIES
from memo.routers.voice import _upstream
from memo.services.ask import _for_the_user
from memo.services.pipeline import PipelineContext, process_capture
from memo.services.retry import (
	ProviderUnavailable,
	RetryableError,
	TerminalError,
	classify_anthropic,
	classify_status,
	provider_wait,
)
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
)

# The real response, 2026-09-30, when memo's balance ran out mid-eval.
CREDIT = (
	"Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
	"'message': 'Your credit balance is too low to access the Anthropic API. Please "
	"go to Plans & Billing to upgrade or purchase credits.'}}"
)


def _anthropic(
	cls: type[anthropic.APIStatusError], code: int, message: str
) -> Exception:
	# The SDK's own HTTP types (it ships httpx2), so these are its real errors.
	request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
	response = httpx2.Response(code, request=request)
	return classify_anthropic(cls(message, response=response, body=None))


def test_an_empty_balance_is_the_account_not_the_request() -> None:
	assert isinstance(
		_anthropic(anthropic.BadRequestError, 400, CREDIT), ProviderUnavailable
	)
	# Still a RetryableError: every sweep that stops on those stops on this.
	assert isinstance(
		_anthropic(anthropic.BadRequestError, 400, CREDIT), RetryableError
	)
	bad = _anthropic(
		anthropic.BadRequestError, 400, "Error code: 400 - prompt too long"
	)
	assert type(bad) is TerminalError


@pytest.mark.parametrize(
	("cls", "code"),
	[
		(anthropic.AuthenticationError, 401),
		(anthropic.PermissionDeniedError, 403),
	],
)
def test_a_refused_key_is_the_account_too(
	cls: type[anthropic.APIStatusError], code: int
) -> None:
	assert isinstance(_anthropic(cls, code, "refused"), ProviderUnavailable)


def test_blips_stay_ordinary_retries() -> None:
	for cls, code in [
		(anthropic.RateLimitError, 429),
		(anthropic.InternalServerError, 500),
	]:
		assert type(_anthropic(cls, code, "busy")) is RetryableError


@pytest.mark.parametrize("code", [401, 402, 403])
def test_http_providers_out_of_credit_wait_too(code: int) -> None:
	# Deepgram says 402 when its balance runs out; Voyage and Expo refuse with 401/403.
	assert isinstance(
		classify_status(code, "insufficient credits"), ProviderUnavailable
	)


def test_nobody_is_shown_the_billing_page() -> None:
	exc = ProviderUnavailable(f"anthropic 400: {CREDIT}")
	for shown in (_for_the_user(exc), _upstream(exc).detail):
		assert shown == ProviderUnavailable.for_users
		assert "credit" not in shown.lower() and "billing" not in shown.lower()
	assert _upstream(exc).status_code == 503


def test_the_wait_grows_to_an_hour_and_outlasts_a_night() -> None:
	for _ in range(50):
		assert 30 <= provider_wait(1) <= 60
		assert 1800 <= provider_wait(40) <= 3600
	# The shortest a note can wait before its last try fails it.
	least = sum(min(3600, 60 * 2 ** (n - 1)) / 2 for n in range(1, ARQ_MAX_TRIES))
	assert least >= 22 * 3600


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		yield s


def _ctx(
	session: AsyncSession, extractor: FakeExtractor, *, may_wait: bool = True
) -> PipelineContext:
	return PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=extractor,
		embedder=FakeEmbedder(),
		people=FakePeopleReader(),
		may_wait=may_wait,
	)


async def _note(client: AsyncClient, token: str) -> uuid.UUID:
	created = await client.post(
		"/v1/captures", json={"text": "call the accountant", "client_token": token}
	)
	assert created.status_code == 202, created.text
	return uuid.UUID(created.json()["id"])


async def _row(admin_engine: AsyncEngine, capture_id: uuid.UUID) -> dict[str, object]:
	async with admin_engine.connect() as conn:
		row = (
			(
				await conn.execute(
					text(
						"SELECT status, attempt_count, failure_reason, "
						"(SELECT count(*) FROM job_runs r WHERE r.capture_id = c.id "
						" AND r.stage = 'extract') AS extract_runs "
						"FROM captures c WHERE id = :id"
					),
					{"id": capture_id},
				)
			)
			.mappings()
			.one()
		)
	return dict(row)


async def test_a_note_waits_out_an_empty_balance_and_then_reads(
	client: AsyncClient, admin_engine: AsyncEngine, session: AsyncSession
) -> None:
	capture_id = await _note(client, "tok-unavailable-1")
	broke = FakeExtractor(fail_with=ProviderUnavailable(f"anthropic 400: {CREDIT}"))

	# Far more waits than a note's five attempts: none of them is an attempt.
	for _ in range(8):
		outcome = await process_capture(_ctx(session, broke), capture_id, SEED_USER_ID)
		assert outcome.waiting_on_provider and outcome.retry_after is None
	row = await _row(admin_engine, capture_id)
	assert row["status"] == "extracting"
	assert row["attempt_count"] == 0 and row["failure_reason"] is None
	assert row["extract_runs"] == 0  # the claim was let go each time

	topped_up = FakeExtractor()
	outcome = await process_capture(_ctx(session, topped_up), capture_id, SEED_USER_ID)
	assert outcome.status == "ready" and topped_up.calls == 1


async def test_on_the_last_try_the_note_fails_where_it_can_be_seen(
	client: AsyncClient, admin_engine: AsyncEngine, session: AsyncSession
) -> None:
	capture_id = await _note(client, "tok-unavailable-2")
	broke = FakeExtractor(fail_with=ProviderUnavailable(f"anthropic 400: {CREDIT}"))

	outcome = await process_capture(
		_ctx(session, broke, may_wait=False), capture_id, SEED_USER_ID
	)

	assert outcome.status == "failed" and not outcome.waiting_on_provider
	row = await _row(admin_engine, capture_id)
	assert row["failure_reason"] == (
		"extract: the AI provider was unavailable for too long"
	)
	# Failed, so the app's retry button brings it back.
	assert (await client.post(f"/v1/captures/{capture_id}/retry")).status_code == 202
