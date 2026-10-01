"""Gemini over plain HTTP (ADR 0067): the request memo sends, what it reads back,
and whose fault each failure is."""

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
from anthropic.types import MessageParam

from memo.schemas.people import LatelyWrite
from memo.services.gemini import (
	GeminiAnswerProvider,
	GeminiClient,
	GeminiCompletionProvider,
	classify_gemini,
	request_body,
)
from memo.services.retry import ProviderUnavailable, RetryableError, TerminalError

MODEL = "gemini-3.8-flash"


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> GeminiClient:
	return GeminiClient(
		"test-key", http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
	)


def _ok(text: str, *, thoughts: int = 50, finish: str = "STOP") -> dict[str, Any]:
	return {
		"candidates": [
			{
				"content": {
					"role": "model",
					"parts": [
						{"text": "let me think", "thought": True},
						{"text": text},
					],
				},
				"finishReason": finish,
			}
		],
		"usageMetadata": {
			"promptTokenCount": 628,
			"cachedContentTokenCount": 100,
			"candidatesTokenCount": 200,
			"thoughtsTokenCount": thoughts,
			"totalTokenCount": 878,
		},
		"modelVersion": f"{MODEL}-001",
	}


def test_request_translates_messages_schema_and_thinking() -> None:
	messages: list[MessageParam] = [
		{"role": "user", "content": "first"},
		{"role": "assistant", "content": '{"line": "x"}'},
		{
			"role": "user",
			"content": [
				{
					"type": "image",
					"source": {
						"type": "base64",
						"media_type": "image/jpeg",
						"data": "QUJD",
					},
				},
				{"type": "text", "text": "what is this"},
			],
		},
	]
	body = request_body(
		system="be brief",
		messages=messages,
		schema={"type": "object"},
		thinking_level="low",
		max_output_tokens=4096,
	)
	assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
	assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
	assert body["contents"][2]["parts"] == [
		{"inlineData": {"mimeType": "image/jpeg", "data": "QUJD"}},
		{"text": "what is this"},
	]
	assert body["generationConfig"] == {
		"maxOutputTokens": 4096,
		"responseMimeType": "application/json",
		"responseJsonSchema": {"type": "object"},
		"thinkingConfig": {"thinkingLevel": "low"},
	}
	plain = request_body(
		system="s", messages=[], schema=None, thinking_level=None, max_output_tokens=300
	)
	assert plain["generationConfig"] == {"maxOutputTokens": 300}


def test_unknown_content_blocks_are_terminal() -> None:
	messages: list[MessageParam] = [
		{"role": "user", "content": [{"type": "document", "source": {}}]}  # type: ignore[list-item]
	]
	with pytest.raises(TerminalError):
		request_body(
			system="s",
			messages=messages,
			schema=None,
			thinking_level=None,
			max_output_tokens=1,
		)


async def test_complete_sends_the_key_and_reads_text_and_usage() -> None:
	seen: list[httpx.Request] = []

	def handler(request: httpx.Request) -> httpx.Response:
		seen.append(request)
		return httpx.Response(200, json=_ok('{"line": "Kemi moved house"}'))

	provider = GeminiCompletionProvider(
		_client(handler), model=MODEL, schema=LatelyWrite
	)
	raw = await provider.complete(
		system="write a line", messages=[{"role": "user", "content": "notes"}]
	)
	request = seen[0]
	assert request.url.path == f"/v1beta/models/{MODEL}:generateContent"
	assert request.headers["x-goog-api-key"] == "test-key"
	sent = json.loads(request.content)
	assert sent["generationConfig"]["responseMimeType"] == "application/json"
	# Every field required, as for Anthropic: no slot the grammar lets the model skip.
	assert set(sent["generationConfig"]["responseJsonSchema"]["required"]) == set(
		LatelyWrite.model_fields
	)
	# Thinking parts are not the answer; the model recorded is the one we asked for.
	assert raw.text == '{"line": "Kemi moved house"}'
	assert raw.provider == "google"
	assert raw.model == MODEL
	assert (raw.input_tokens, raw.cache_read_tokens) == (528, 100)
	assert raw.output_tokens == 250  # thinking bills as output
	assert raw.cache_write_tokens == 0


@pytest.mark.parametrize(
	("response", "said"),
	[
		(_ok("partial", finish="MAX_TOKENS"), "truncated"),
		(_ok("", finish="SAFETY"), "declined"),
		({"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}, "no answer"),
	],
)
async def test_truncation_and_blocks_are_terminal(
	response: dict[str, Any], said: str
) -> None:
	provider = GeminiCompletionProvider(
		_client(lambda _: httpx.Response(200, json=response)), model=MODEL
	)
	with pytest.raises(TerminalError, match=said):
		await provider.complete(system="s", messages=[{"role": "user", "content": "x"}])


def _body(
	code: int, status: str, message: str, details: list[Any] | None = None
) -> str:
	error: dict[str, Any] = {"code": code, "message": message, "status": status}
	if details is not None:
		error["details"] = details
	return json.dumps({"error": error})


DAILY = [
	{
		"@type": "type.googleapis.com/google.rpc.QuotaFailure",
		"violations": [
			{
				"quotaMetric": "generativelanguage.googleapis.com/generate_requests",
				"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
			}
		],
	}
]
MINUTE = [
	{
		"@type": "type.googleapis.com/google.rpc.QuotaFailure",
		"violations": [
			{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}
		],
	}
]


@pytest.mark.parametrize(
	("status", "body", "kind"),
	[
		# The day's quota lasts until Google's midnight: our account, waited out.
		(
			429,
			_body(429, "RESOURCE_EXHAUSTED", "quota exceeded", DAILY),
			ProviderUnavailable,
		),
		(
			429,
			_body(429, "RESOURCE_EXHAUSTED", "You exceeded your daily quota"),
			ProviderUnavailable,
		),
		# The minute's quota is a blip.
		(
			429,
			_body(429, "RESOURCE_EXHAUSTED", "quota exceeded", MINUTE),
			RetryableError,
		),
		(429, "not json at all", RetryableError),
		(503, _body(503, "UNAVAILABLE", "high demand"), RetryableError),
		(500, _body(500, "INTERNAL", "oops"), RetryableError),
		(401, _body(401, "UNAUTHENTICATED", "API key not valid"), ProviderUnavailable),
		(402, _body(402, "PAYMENT_REQUIRED", "credits depleted"), ProviderUnavailable),
		(403, _body(403, "PERMISSION_DENIED", "no"), ProviderUnavailable),
		(
			400,
			_body(400, "FAILED_PRECONDITION", "billing is not enabled"),
			ProviderUnavailable,
		),
		(
			404,
			_body(404, "NOT_FOUND", "no longer available to new users"),
			ProviderUnavailable,
		),
		(400, _body(400, "INVALID_ARGUMENT", "schema too deep"), TerminalError),
		(413, "too large", TerminalError),
	],
)
def test_classification_is_by_whose_fault(status: int, body: str, kind: type) -> None:
	exc = classify_gemini(status, body)
	assert type(exc) is kind, exc
	assert str(exc).startswith(f"gemini {status}")


async def test_http_errors_are_classified_and_transport_errors_retry() -> None:
	provider = GeminiCompletionProvider(
		_client(
			lambda _: httpx.Response(
				429, text=_body(429, "RESOURCE_EXHAUSTED", "x", DAILY)
			)
		),
		model=MODEL,
	)
	with pytest.raises(ProviderUnavailable):
		await provider.complete(system="s", messages=[{"role": "user", "content": "x"}])

	def unreachable(request: httpx.Request) -> httpx.Response:
		raise httpx.ConnectError("refused", request=request)

	provider = GeminiCompletionProvider(_client(unreachable), model=MODEL)
	with pytest.raises(RetryableError):
		await provider.complete(system="s", messages=[{"role": "user", "content": "x"}])


def test_thinking_level_is_checked_up_front() -> None:
	with pytest.raises(ValueError):
		GeminiCompletionProvider(
			_client(lambda _: httpx.Response(500)), model=MODEL, thinking_level="lots"
		)


def _sse(*events: dict[str, Any]) -> str:
	return "".join(f"data: {json.dumps(e)}\r\n\r\n" for e in events)


def _chunk(
	text: str | None,
	*,
	thought: bool = False,
	finish: str | None = None,
	usage: bool = False,
) -> dict[str, Any]:
	parts: list[dict[str, Any]] = []
	if text is not None:
		part: dict[str, Any] = {"text": text}
		if thought:
			part["thought"] = True
		parts.append(part)
	candidate: dict[str, Any] = {"content": {"role": "model", "parts": parts}}
	if finish:
		candidate["finishReason"] = finish
	event: dict[str, Any] = {"candidates": [candidate]}
	if usage:
		event["usageMetadata"] = {
			"promptTokenCount": 1000,
			"candidatesTokenCount": 20,
			"thoughtsTokenCount": 30,
		}
	return event


async def test_stream_yields_text_only_and_prices_the_final_usage() -> None:
	seen: list[httpx.Request] = []

	def handler(request: httpx.Request) -> httpx.Response:
		seen.append(request)
		return httpx.Response(
			200,
			headers={"content-type": "text/event-stream"},
			text=_sse(
				_chunk("planning", thought=True),
				_chunk("Hel"),
				_chunk("lo", finish="STOP", usage=True),
			),
		)

	provider = GeminiAnswerProvider(_client(handler), model=MODEL)
	tokens = [
		t
		async for t in provider.stream(
			system="s", messages=[{"role": "user", "content": "q"}]
		)
	]
	assert tokens == ["Hel", "lo"]
	assert seen[0].url.path == f"/v1beta/models/{MODEL}:streamGenerateContent"
	assert seen[0].url.params["alt"] == "sse"
	call = provider.usage()
	assert (call.provider, call.model, call.stage) == ("google", MODEL, "answer")
	assert (call.input_tokens, call.output_tokens) == (1000, 50)
	# 1000 in at $0.75/M + 50 out at $3.75/M.
	assert call.cost_usd == Decimal("0.000938")


async def test_stream_block_with_nothing_said_is_terminal() -> None:
	provider = GeminiAnswerProvider(
		_client(
			lambda _: httpx.Response(
				200, text=_sse(_chunk(None, finish="SAFETY", usage=True))
			)
		),
		model=MODEL,
	)
	with pytest.raises(TerminalError, match="SAFETY"):
		async for _ in provider.stream(
			system="s", messages=[{"role": "user", "content": "q"}]
		):
			pass


async def test_stream_http_errors_are_classified() -> None:
	provider = GeminiAnswerProvider(
		_client(
			lambda _: httpx.Response(403, text=_body(403, "PERMISSION_DENIED", "x"))
		),
		model=MODEL,
	)
	with pytest.raises(ProviderUnavailable):
		async for _ in provider.stream(
			system="s", messages=[{"role": "user", "content": "q"}]
		):
			pass
	with pytest.raises(RuntimeError):
		provider.usage()
