"""Describe a photo with a vision model, so a photo capture has text like any other.

The describer is provider-neutral: it builds one image-plus-text message in the
shape every stage uses and hands it to a structured provider (ADR 0067), which is
constrained to `_Description` the way the readers are constrained to theirs.
"""

import base64
from dataclasses import dataclass
from typing import Literal, Protocol, cast

import structlog
from anthropic.types import MessageParam
from pydantic import BaseModel, ValidationError

from memo.config import Settings
from memo.prompts.describe_v1 import (
	DESCRIBER_VERSION,
	SYSTEM_PROMPT,
	note_text,
	user_message,
)
from memo.services.extraction import ExtractionProvider
from memo.services.llm import LLMCall
from memo.services.prose import plain_dashes
from memo.services.retry import TerminalError

log = structlog.get_logger("memo.vision")

STAGE = "describe"
# Claude's limit for one base64 image, kept as memo's own rule for every provider.
# The app resizes to 1568px JPEG long before this.
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
		self,
		image: bytes,
		mime: str,
		*,
		caption: str | None = None,
		tone: str | None = None,
	) -> DescribeResult: ...


class PhotoDescriber:
	def __init__(self, provider: ExtractionProvider) -> None:
		self._provider = provider

	@property
	def model(self) -> str:
		return self._provider.model

	@property
	def version(self) -> str:
		return f"{DESCRIBER_VERSION}/{self.model}"

	async def describe(
		self,
		image: bytes,
		mime: str,
		*,
		caption: str | None = None,
		tone: str | None = None,
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
					{"type": "text", "text": user_message(caption, tone)},
				],
			}
		]
		raw = await self._provider.complete(system=SYSTEM_PROMPT, messages=messages)
		try:
			parsed = _Description.model_validate_json(raw.text)
		except ValidationError as exc:
			raise TerminalError(f"description did not validate: {exc}") from exc
		call = LLMCall.priced(
			provider=raw.provider,
			model=raw.model,
			stage=STAGE,
			input_tokens=raw.input_tokens,
			output_tokens=raw.output_tokens,
			cache_read_tokens=raw.cache_read_tokens,
			cache_write_tokens=raw.cache_write_tokens,
			latency_ms=raw.latency_ms,
		)
		# The description is memo's writing, so no em dashes (ADR 0061); the text in
		# the photo is the photo's, verbatim.
		return DescribeResult(
			text=note_text(plain_dashes(parsed.description), parsed.text_in_image),
			call=call,
		)


class UnconfiguredDescriber:
	"""Stands in when the model's API key is unset, so a photo fails visibly."""

	version = f"{DESCRIBER_VERSION}/unconfigured"

	async def describe(
		self,
		image: bytes,
		mime: str,
		*,
		caption: str | None = None,
		tone: str | None = None,
	) -> DescribeResult:
		raise TerminalError("image description is not configured (no API key)")


def make_describer(settings: Settings) -> Describer:
	from memo.services.providers import structured_provider

	provider = structured_provider(
		settings, model=settings.extraction_model, schema=_Description
	)
	if provider is None:
		return UnconfiguredDescriber()
	return PhotoDescriber(provider)
