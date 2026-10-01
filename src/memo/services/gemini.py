"""Google's Gemini API behind memo's provider seams (ADR 0067).

The same `complete()` and `stream()` shapes as the Anthropic classes, over plain
HTTP. No SDK: google-genai pins websockets below 17 and live transcription needs 17,
and the two calls memo makes (generateContent, streamGenerateContent) are one JSON
body each. Structured output is `responseJsonSchema`, given the same transformed
schema Anthropic constrains with, so one Pydantic model still both constrains and
validates (ADR 0015).

Failures are classified the way ADR 0066 asks: whose fault is it. A per-minute 429
is a blip. A per-day quota 429 lasts until Google's midnight, so it is waited out
without spending a note's attempts, like an empty balance.
"""

import json
import time
from collections.abc import AsyncGenerator, Iterable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, cast

import httpx
import structlog
from anthropic.lib._parse._transform import transform_schema
from anthropic.types import MessageParam
from pydantic import BaseModel

from memo.services.extraction import RawCompletion
from memo.services.llm import LLMCall
from memo.services.retry import (
	ACCOUNT_STATUSES,
	ProviderUnavailable,
	RetryableError,
	TerminalError,
)

log = structlog.get_logger("memo.gemini")

PROVIDER = "google"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
# A reader's call is a few hundred tokens each way; two minutes leaves room for a
# provider having a slow day without holding a worker slot forever.
TIMEOUT = httpx.Timeout(120.0, connect=10.0)
THINKING_LEVELS = ("minimal", "low", "medium", "high")


# --- request and response shapes -----------------------------------------------------


def contents(messages: Iterable[MessageParam]) -> list[dict[str, Any]]:
	"""Anthropic-shaped messages (what every stage builds) as Gemini `contents`."""
	out: list[dict[str, Any]] = []
	for message in messages:
		role = "model" if message["role"] == "assistant" else "user"
		content = message["content"]
		if isinstance(content, str):
			parts = [{"text": content}]
		else:
			parts = [_part(cast(dict[str, Any], block)) for block in content]
		out.append({"role": role, "parts": parts})
	return out


def _part(block: dict[str, Any]) -> dict[str, Any]:
	kind = block.get("type")
	if kind == "text":
		return {"text": block["text"]}
	if kind == "image":
		source = block["source"]
		if source.get("type") != "base64":
			raise TerminalError("gemini: only base64 images are supported")
		return {
			"inlineData": {"mimeType": source["media_type"], "data": source["data"]}
		}
	raise TerminalError(f"gemini: unsupported content block {kind!r}")


def request_body(
	*,
	system: str,
	messages: Iterable[MessageParam],
	schema: dict[str, Any] | None,
	thinking_level: str | None,
	max_output_tokens: int,
) -> dict[str, Any]:
	config: dict[str, Any] = {"maxOutputTokens": max_output_tokens}
	if schema is not None:
		config["responseMimeType"] = "application/json"
		config["responseJsonSchema"] = schema
	if thinking_level is not None:
		config["thinkingConfig"] = {"thinkingLevel": thinking_level}
	return {
		"systemInstruction": {"parts": [{"text": system}]},
		"contents": contents(messages),
		"generationConfig": config,
	}


# The model stopped on its own terms. Anything else (SAFETY, RECITATION,
# PROHIBITED_CONTENT, ...) is a refusal or a block: terminal, like Anthropic's.
_FINISHED = frozenset({"STOP", "FINISH_REASON_UNSPECIFIED"})


def finish_reason(response: dict[str, Any]) -> str | None:
	candidates = response.get("candidates") or []
	if not candidates:
		return None
	return str(candidates[0].get("finishReason") or "STOP")


def candidate_text(response: dict[str, Any], *, truncated_is_error: bool) -> str:
	"""The answer's text, thinking parts left out, or a TerminalError."""
	candidates = response.get("candidates") or []
	if not candidates:
		feedback = response.get("promptFeedback") or {}
		reason = feedback.get("blockReason") or "no candidates"
		raise TerminalError(f"gemini returned no answer: {reason}")
	candidate = candidates[0]
	reason = candidate.get("finishReason") or "STOP"
	if reason == "MAX_TOKENS":
		if truncated_is_error:
			raise TerminalError("gemini output was truncated at maxOutputTokens")
	elif reason not in _FINISHED:
		raise TerminalError(f"gemini declined: {reason}")
	return parts_text(response)


def parts_text(response: dict[str, Any]) -> str:
	candidates = response.get("candidates") or []
	if not candidates:
		return ""
	parts = (candidates[0].get("content") or {}).get("parts") or []
	return "".join(p.get("text", "") for p in parts if not p.get("thought"))


@dataclass(frozen=True)
class Usage:
	input_tokens: int
	output_tokens: int
	cache_read_tokens: int

	@classmethod
	def of(cls, response: dict[str, Any]) -> "Usage":
		"""Gemini counts cached tokens inside the prompt count and bills thinking as
		output; memo's columns and price formula want them apart (services/llm.py)."""
		meta = response.get("usageMetadata") or {}
		prompt = int(meta.get("promptTokenCount") or 0)
		cached = int(meta.get("cachedContentTokenCount") or 0)
		answer = int(meta.get("candidatesTokenCount") or 0)
		thoughts = int(meta.get("thoughtsTokenCount") or 0)
		return cls(
			input_tokens=max(prompt - cached, 0),
			output_tokens=answer + thoughts,
			cache_read_tokens=cached,
		)


# --- failure classification ----------------------------------------------------------


def _error(body: str) -> tuple[str, str, list[dict[str, Any]]]:
	"""(message, status, details) from Google's error envelope, or the raw text."""
	try:
		error = json.loads(body).get("error") or {}
	except (ValueError, AttributeError):
		return body[:200], "", []
	return (
		str(error.get("message") or "")[:200],
		str(error.get("status") or ""),
		list(error.get("details") or []),
	)


def _daily_quota(message: str, details: list[dict[str, Any]]) -> bool:
	"""A 429 for the day's quota rather than the minute's. Google names the quota in
	QuotaFailure details (...PerDay...); the message is the fallback if that moves."""
	for detail in details:
		for violation in detail.get("violations") or []:
			quota = f"{violation.get('quotaId', '')} {violation.get('quotaMetric', '')}"
			if "perday" in quota.replace("_", "").lower():
				return True
	lowered = message.lower()
	return "per day" in lowered or "daily" in lowered


def classify_gemini(status_code: int, body: str) -> Exception:
	"""Whose fault is it (ADR 0066): the note's, the provider's, or our account's."""
	message, status, details = _error(body)
	text = f"gemini {status_code}{' ' + status if status else ''}: {message}"
	if status_code in ACCOUNT_STATUSES or status_code == 402:
		return ProviderUnavailable(text)
	if status_code == 400 and status == "FAILED_PRECONDITION":
		# Billing not enabled, or the free tier isn't offered where we are: ours.
		return ProviderUnavailable(text)
	if status_code == 404:
		# A model Google doesn't serve: retired, or mistyped in a setting. Nothing
		# about the note is wrong, and it ends when the operator acts.
		return ProviderUnavailable(text)
	if status_code == 429:
		# Which quota, for the operator: the per-day one decides what happens next.
		log.warning(
			"gemini.rate_limited",
			message=message,
			quota_ids=[
				v.get("quotaId") for d in details for v in (d.get("violations") or [])
			],
		)
		if _daily_quota(message, details):
			return ProviderUnavailable(text)
		return RetryableError(text)
	if status_code == 408 or status_code >= 500:
		return RetryableError(text)
	return TerminalError(text)


# --- the client ----------------------------------------------------------------------


class GeminiClient:
	"""One API key and one HTTP client; the providers below share it."""

	def __init__(
		self,
		api_key: str,
		*,
		http: httpx.AsyncClient | None = None,
		base_url: str = BASE_URL,
	) -> None:
		self._api_key = api_key
		self._http = http or httpx.AsyncClient(timeout=TIMEOUT)
		self._base_url = base_url.rstrip("/")

	def _url(self, model: str, method: str) -> str:
		return f"{self._base_url}/models/{model}:{method}"

	@property
	def _headers(self) -> dict[str, str]:
		return {"x-goog-api-key": self._api_key}

	async def generate(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
		try:
			response = await self._http.post(
				self._url(model, "generateContent"),
				json=body,
				headers=self._headers,
				timeout=TIMEOUT,
			)
		except (httpx.TimeoutException, httpx.TransportError) as exc:
			raise RetryableError(f"gemini unreachable: {exc!r}") from exc
		if response.status_code >= 400:
			raise classify_gemini(response.status_code, response.text)
		try:
			return cast(dict[str, Any], response.json())
		except ValueError as exc:
			raise TerminalError("gemini response was not JSON") from exc

	async def stream(
		self, model: str, body: dict[str, Any]
	) -> AsyncGenerator[dict[str, Any], None]:
		"""Each server-sent event's JSON, in order. Leaving the generator closes the
		connection, which is what stops the bill when the client has gone."""
		try:
			async with self._http.stream(
				"POST",
				self._url(model, "streamGenerateContent"),
				params={"alt": "sse"},
				json=body,
				headers=self._headers,
				timeout=TIMEOUT,
			) as response:
				if response.status_code >= 400:
					await response.aread()
					raise classify_gemini(response.status_code, response.text)
				async for line in response.aiter_lines():
					if not line.startswith("data:"):
						continue
					payload = line[5:].strip()
					if payload:
						yield cast(dict[str, Any], json.loads(payload))
		except (httpx.TimeoutException, httpx.TransportError) as exc:
			raise RetryableError(f"gemini unreachable: {exc!r}") from exc


# --- the providers -------------------------------------------------------------------


class GeminiCompletionProvider:
	"""One call, with or without a schema: the ExtractionProvider and TextProvider
	shapes over Gemini."""

	provider = PROVIDER

	def __init__(
		self,
		client: GeminiClient,
		*,
		model: str,
		schema: type[BaseModel] | None = None,
		thinking_level: str | None = "low",
		max_output_tokens: int = 4096,
	) -> None:
		if thinking_level is not None and thinking_level not in THINKING_LEVELS:
			raise ValueError(
				f"gemini thinking level {thinking_level!r}: expected one of "
				f"{', '.join(THINKING_LEVELS)}"
			)
		self._client = client
		self.model = model
		# Serialization mode with every field required, exactly as for Anthropic
		# (see AnthropicExtractionProvider): the grammar leaves no field to skip.
		self._schema = (
			transform_schema(schema.model_json_schema(mode="serialization"))
			if schema is not None
			else None
		)
		self._thinking_level = thinking_level
		self._max_output_tokens = max_output_tokens

	async def complete(
		self, *, system: str, messages: list[MessageParam]
	) -> RawCompletion:
		started = time.perf_counter()
		response = await self._client.generate(
			self.model,
			request_body(
				system=system,
				messages=messages,
				schema=self._schema,
				thinking_level=self._thinking_level,
				max_output_tokens=self._max_output_tokens,
			),
		)
		text = candidate_text(response, truncated_is_error=True)
		usage = Usage.of(response)
		return RawCompletion(
			text=text,
			# The model we asked for, which is the one the price table knows. Google's
			# `modelVersion` may carry a build suffix the table wouldn't match.
			model=self.model,
			input_tokens=usage.input_tokens,
			output_tokens=usage.output_tokens,
			cache_read_tokens=usage.cache_read_tokens,
			cache_write_tokens=0,
			latency_ms=round((time.perf_counter() - started) * 1000),
			provider=PROVIDER,
		)


class GeminiAnswerProvider:
	"""A streamed answer: the AnswerProvider shape in services/ask.py."""

	provider = PROVIDER

	def __init__(
		self,
		client: GeminiClient,
		*,
		model: str,
		thinking_level: str | None = "low",
		max_output_tokens: int = 2048,
	) -> None:
		self._client = client
		self.model = model
		self._thinking_level = thinking_level
		self._max_output_tokens = max_output_tokens
		self._last: LLMCall | None = None

	async def stream(
		self, *, system: str, messages: list[MessageParam]
	) -> AsyncGenerator[str, None]:
		started = time.perf_counter()
		body = request_body(
			system=system,
			messages=messages,
			schema=None,
			thinking_level=self._thinking_level,
			max_output_tokens=self._max_output_tokens,
		)
		said = False
		last: dict[str, Any] | None = None
		usage_from: dict[str, Any] | None = None
		async with aclosing(self._client.stream(self.model, body)) as events:
			async for event in events:
				last = event
				if "usageMetadata" in event:
					usage_from = event
				text = parts_text(event)
				if text:
					said = True
					yield text
		if last is None:
			raise TerminalError("gemini returned no answer: empty stream")
		reason = finish_reason(last)
		if not said and reason is not None and reason not in _FINISHED:
			# A block with nothing said is a refusal; a cut-off answer stays an answer.
			raise TerminalError(f"gemini declined: {reason}")
		usage = Usage.of(usage_from or last)
		self._last = LLMCall.priced(
			provider=PROVIDER,
			model=self.model,
			stage="answer",
			input_tokens=usage.input_tokens,
			output_tokens=usage.output_tokens,
			cache_read_tokens=usage.cache_read_tokens,
			cache_write_tokens=0,
			latency_ms=round((time.perf_counter() - started) * 1000),
		)

	def usage(self) -> LLMCall:
		if self._last is None:
			raise RuntimeError("usage() before the stream completed")
		return self._last
