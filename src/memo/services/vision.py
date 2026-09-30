"""Describe a photo with Claude vision, so a photo capture has text like any other."""

import base64
import time
from dataclasses import dataclass
from typing import Literal, Protocol, cast

import anthropic
import structlog
from anthropic import AsyncAnthropic
from anthropic.lib._parse._transform import transform_schema
from anthropic.types import MessageParam, OutputConfigParam
from pydantic import BaseModel, ValidationError

from memo.config import Settings
from memo.prompts.describe_v1 import (
	DESCRIBER_VERSION,
	SYSTEM_PROMPT,
	note_text,
	user_message,
)
from memo.services.llm import LLMCall
from memo.services.retry import RetryableError, TerminalError

log = structlog.get_logger("memo.vision")

STAGE = "describe"
# Claude's limit for one base64 image. The app resizes to 1568px JPEG long before this.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
_VisionMime = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
VISION_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class _Description(BaseModel):
	description: str
	text_in_image: str


@dataclass(frozen=True)
class DescribeResult:
	text: str
	call: LLMCall


class Describer(Protocol):
	@property
	def version(self) -> str: ...

	async def describe(
		self, image: bytes, mime: str, *, caption: str | None = None
	) -> DescribeResult: ...


class AnthropicDescriber:
	def __init__(self, client: AsyncAnthropic, *, model: str) -> None:
		self._client = client
		self.model = model
		self._output_config: OutputConfigParam = {
			"format": {"type": "json_schema", "schema": transform_schema(_Description)},
			"effort": "low",
		}

	@property
	def version(self) -> str:
		return f"{DESCRIBER_VERSION}/{self.model}"

	async def describe(
		self, image: bytes, mime: str, *, caption: str | None = None
	) -> DescribeResult:
		if mime not in VISION_MIME:
			raise TerminalError(f"unsupported image type {mime}")
		if len(image) > MAX_IMAGE_BYTES:
			raise TerminalError(f"image is {len(image)} bytes; the limit is 5 MB")
		media_type = cast(_VisionMime, mime)
		messages: list[MessageParam] = [
			{
				"role": "user",
				"content": [
					{
						"type": "image",
						"source": {
							"type": "base64",
							"media_type": media_type,
							"data": base64.standard_b64encode(image).decode(),
						},
					},
					{"type": "text", "text": user_message(caption)},
				],
			}
		]
		started = time.perf_counter()
		try:
			response = await self._client.messages.create(
				model=self.model,
				max_tokens=4096,
				system=SYSTEM_PROMPT,
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
			raise TerminalError("model declined to describe this image")
		if response.stop_reason == "max_tokens":
			raise TerminalError("image description was truncated at max_tokens")
		raw = next((b.text for b in response.content if b.type == "text"), "")
		try:
			parsed = _Description.model_validate_json(raw)
		except ValidationError as exc:
			raise TerminalError(f"description did not validate: {exc}") from exc
		usage = response.usage
		call = LLMCall.priced(
			provider="anthropic",
			model=response.model,
			stage=STAGE,
			input_tokens=usage.input_tokens,
			output_tokens=usage.output_tokens,
			cache_read_tokens=usage.cache_read_input_tokens or 0,
			cache_write_tokens=usage.cache_creation_input_tokens or 0,
			latency_ms=round((time.perf_counter() - started) * 1000),
		)
		return DescribeResult(
			text=note_text(parsed.description, parsed.text_in_image), call=call
		)


class UnconfiguredDescriber:
	"""Stands in when no Anthropic key is set, so a photo fails visibly."""

	version = f"{DESCRIBER_VERSION}/unconfigured"

	async def describe(
		self, image: bytes, mime: str, *, caption: str | None = None
	) -> DescribeResult:
		raise TerminalError("image description is not configured (no Anthropic key)")


def make_describer(settings: Settings) -> Describer:
	if settings.anthropic_api_key is None:
		return UnconfiguredDescriber()
	client = AsyncAnthropic(
		api_key=settings.anthropic_api_key.get_secret_value(), max_retries=0
	)
	return AnthropicDescriber(client, model=settings.extraction_model)
