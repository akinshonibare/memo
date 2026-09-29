"""Extraction: capture text -> validated Extraction, or a visible failure (ADR 0015).

The API constrains generation to the Extraction JSON schema, so syntax is guaranteed.
The residual failures are semantic - a blank title, an event that ends before it
starts - and those are deterministic, so the policy is one repair with the validation
error appended, then terminal. Never a silent drop.
"""

import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import anthropic
import structlog
from anthropic import AsyncAnthropic
from anthropic.lib._parse._transform import transform_schema
from anthropic.types import MessageParam, OutputConfigParam
from pydantic import BaseModel, ValidationError

from memo.config import Settings
from memo.prompts.extract_v1 import (
	EXTRACTOR_VERSION,
	SYSTEM_PROMPT,
	repair_message,
	user_message,
)
from memo.schemas.derivations import Extraction
from memo.services.llm import LLMCall
from memo.services.retry import RetryableError, TerminalError

log = structlog.get_logger("memo.extraction")

STAGE = "extract"


@dataclass(frozen=True)
class RawCompletion:
	text: str
	model: str
	input_tokens: int
	output_tokens: int
	cache_read_tokens: int
	cache_write_tokens: int
	latency_ms: int


class ExtractionProvider(Protocol):
	model: str

	async def complete(
		self, *, system: str, messages: list[MessageParam]
	) -> RawCompletion: ...


class AnthropicExtractionProvider:
	def __init__(
		self,
		client: AsyncAnthropic,
		*,
		model: str,
		schema: type[BaseModel] = Extraction,
	) -> None:
		self._client = client
		self.model = model
		# The SDK's own schema transform (what messages.parse uses), so the constraint
		# the API enforces is exactly the model we validate with. Private import on
		# purpose: parse() would validate inside the SDK and lose the raw text.
		self._output_config: OutputConfigParam = {
			"format": {
				"type": "json_schema",
				# Serialization mode: every field required, so the grammar leaves the
				# model no field it can skip and then try to write inside a string.
				"schema": transform_schema(
					schema.model_json_schema(mode="serialization")
				),
			},
			"effort": "low",
		}

	async def complete(
		self, *, system: str, messages: list[MessageParam]
	) -> RawCompletion:
		started = time.perf_counter()
		try:
			response = await self._client.messages.create(
				model=self.model,
				max_tokens=4096,
				system=system,
				messages=messages,
				output_config=self._output_config,
			)
		except anthropic.RateLimitError as exc:
			raise RetryableError(f"anthropic 429: {exc.message}") from exc
		except anthropic.APIStatusError as exc:
			if exc.status_code >= 500:
				raise RetryableError(f"anthropic {exc.status_code}") from exc
			raise TerminalError(f"anthropic {exc.status_code}: {exc.message}") from exc
		except anthropic.APIConnectionError as exc:
			raise RetryableError(f"anthropic unreachable: {exc!r}") from exc

		if response.stop_reason == "refusal":
			raise TerminalError("model refused the extraction request")
		if response.stop_reason == "max_tokens":
			raise TerminalError("extraction output was truncated at max_tokens")
		text = next((b.text for b in response.content if b.type == "text"), "")
		usage = response.usage
		return RawCompletion(
			text=text,
			model=response.model,
			input_tokens=usage.input_tokens,
			output_tokens=usage.output_tokens,
			cache_read_tokens=usage.cache_read_input_tokens or 0,
			cache_write_tokens=usage.cache_creation_input_tokens or 0,
			latency_ms=round((time.perf_counter() - started) * 1000),
		)


@dataclass(frozen=True)
class ExtractResult:
	extraction: Extraction
	raw: str
	calls: list[LLMCall]


class ExtractionFailed(TerminalError):
	"""Semantic validation failed after the repair budget. Carries what we paid for."""

	def __init__(self, message: str, *, raw: str, calls: list[LLMCall]) -> None:
		super().__init__(message)
		self.raw = raw
		self.calls = calls


def _format_errors(exc: ValidationError) -> str:
	lines = []
	for err in exc.errors():
		location = ".".join(str(p) for p in err["loc"]) or "<root>"
		lines.append(f"- {location}: {err['msg']}")
	return "\n".join(lines)


class Extractor:
	def __init__(self, provider: ExtractionProvider, *, max_repairs: int = 1) -> None:
		self._provider = provider
		self._max_repairs = max_repairs

	@property
	def model(self) -> str:
		return self._provider.model

	@property
	def version(self) -> str:
		return f"{EXTRACTOR_VERSION}/{self.model}"

	async def extract(
		self, text: str, *, captured_at: dt.datetime, timezone: str
	) -> ExtractResult:
		extraction, raw, calls = await complete_validated(
			self._provider,
			system=SYSTEM_PROMPT,
			first=user_message(text, captured_at=captured_at, timezone=timezone),
			parse=Extraction.model_validate_json,
			repair=repair_message,
			stage=STAGE,
			max_repairs=self._max_repairs,
		)
		return ExtractResult(extraction=extraction, raw=raw, calls=calls)


async def complete_validated[T](
	provider: ExtractionProvider,
	*,
	system: str,
	first: str,
	parse: Callable[[str], T],
	repair: Callable[[str], str],
	stage: str,
	max_repairs: int,
) -> tuple[T, str, list[LLMCall]]:
	"""One call, validated; on a validation error, the error goes back to the model
	for a repair, at most `max_repairs` times, then ExtractionFailed (ADR 0015).
	Shared by every stage that asks a model for a schema: extraction and people."""
	messages: list[MessageParam] = [{"role": "user", "content": first}]
	calls: list[LLMCall] = []
	last_raw = ""
	last_errors = ""
	for attempt in range(1, max_repairs + 2):
		raw = await provider.complete(system=system, messages=messages)
		calls.append(
			LLMCall.priced(
				provider="anthropic",
				model=raw.model,
				stage=stage,
				input_tokens=raw.input_tokens,
				output_tokens=raw.output_tokens,
				cache_read_tokens=raw.cache_read_tokens,
				cache_write_tokens=raw.cache_write_tokens,
				latency_ms=raw.latency_ms,
			)
		)
		try:
			parsed = parse(raw.text)
		except ValidationError as exc:
			last_raw, last_errors = raw.text, _format_errors(exc)
			log.info(
				"extraction.repair", stage=stage, attempt=attempt, errors=last_errors
			)
			messages = [
				*messages,
				{"role": "assistant", "content": raw.text},
				{"role": "user", "content": repair(last_errors)},
			]
			continue
		return parsed, raw.text, calls

	raise ExtractionFailed(
		f"schema_validation after {max_repairs + 1} attempts:\n{last_errors}",
		raw=last_raw,
		calls=calls,
	)


class ExtractorLike(Protocol):
	@property
	def model(self) -> str: ...

	@property
	def version(self) -> str: ...

	async def extract(
		self, text: str, *, captured_at: dt.datetime, timezone: str
	) -> ExtractResult: ...


class UnconfiguredExtractor:
	"""Stands in when no Anthropic key is set, so extraction fails visibly."""

	model = "unconfigured"
	version = f"{EXTRACTOR_VERSION}/unconfigured"

	async def extract(
		self, text: str, *, captured_at: dt.datetime, timezone: str
	) -> ExtractResult:
		raise TerminalError("extraction is not configured (ANTHROPIC_API_KEY is unset)")


def make_extractor(settings: Settings) -> ExtractorLike:
	if settings.anthropic_api_key is None:
		return UnconfiguredExtractor()
	client = AsyncAnthropic(
		api_key=settings.anthropic_api_key.get_secret_value(), max_retries=0
	)
	return Extractor(
		AnthropicExtractionProvider(client, model=settings.extraction_model)
	)
