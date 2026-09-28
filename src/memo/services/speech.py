"""Text to speech for Ask's voice mode (ADR 0025): Deepgram Aura-2 over REST.

Same vendor and key as transcription (ADR 0013), so voice adds a model, not an account.
The app speaks an answer a sentence at a time as it streams, so each call is short and
the first sentence is audible while the rest is still being generated.
"""

import re
import time
from decimal import Decimal
from typing import Protocol

import httpx
import structlog

from memo.config import Settings
from memo.services.retry import RetryableError, TerminalError, classify_status

log = structlog.get_logger("memo.speech")

DEEPGRAM_SPEAK_URL = "https://api.deepgram.com/v1/speak"
MAX_CHARS = 2000  # Aura's per-request limit

# The voices offered in the app, verified against Deepgram's model list 2026-09-28.
# A short, deliberate set: a picker with forty voices is a worse product.
VOICES: dict[str, str] = {
	"aura-2-thalia-en": "Thalia · clear, energetic (US)",
	"aura-2-helena-en": "Helena · warm, friendly (US)",
	"aura-2-pandora-en": "Pandora · calm, smooth (UK)",
	"aura-2-apollo-en": "Apollo · relaxed, casual (US)",
	"aura-2-draco-en": "Draco · warm baritone (UK)",
	"aura-2-hyperion-en": "Hyperion · gentle, warm (AU)",
}
PRICE_PER_1K_CHARS = Decimal("0.030")  # Aura-2, pay as you go; verified 2026-09-28

# "[2]" citation markers and markdown emphasis read aloud as noise.
_MARKERS = re.compile(r"\s*\[\d+(?:,\s*\d+)*\]")
_EMPHASIS = re.compile(r"[*_`#]+")


def speakable(text: str) -> str:
	return _EMPHASIS.sub("", _MARKERS.sub("", text)).strip()


class Speaker(Protocol):
	model: str

	async def speak(self, text: str, *, voice: str | None = None) -> bytes: ...


class DeepgramSpeaker:
	def __init__(self, *, api_key: str, http: httpx.AsyncClient, model: str) -> None:
		self._api_key = api_key
		self._http = http
		self.model = model

	async def speak(self, text: str, *, voice: str | None = None) -> bytes:
		model = voice if voice in VOICES else self.model
		body = speakable(text)
		if not body:
			raise TerminalError("nothing to say")
		if len(body) > MAX_CHARS:
			raise TerminalError(f"{len(body)} characters; the limit is {MAX_CHARS}")
		started = time.perf_counter()
		try:
			response = await self._http.post(
				DEEPGRAM_SPEAK_URL,
				params={"model": model, "encoding": "mp3"},
				headers={
					"Authorization": f"Token {self._api_key}",
					"Content-Type": "application/json",
				},
				json={"text": body},
				timeout=httpx.Timeout(30.0, connect=5.0),
			)
		except (httpx.TimeoutException, httpx.TransportError) as exc:
			raise RetryableError(f"deepgram unreachable: {exc!r}") from exc
		if response.status_code >= 400:
			raise classify_status(response.status_code, response.text[:200])
		log.info(
			"spoken",
			model=model,
			chars=len(body),
			cost_usd=str(PRICE_PER_1K_CHARS * len(body) / 1000),
			audio_bytes=len(response.content),
			latency_ms=round((time.perf_counter() - started) * 1000),
		)
		return response.content


class UnconfiguredSpeaker:
	model = "unconfigured"

	async def speak(self, text: str, *, voice: str | None = None) -> bytes:
		raise TerminalError("text to speech is not configured (DEEPGRAM_API_KEY unset)")


def make_speaker(settings: Settings, http: httpx.AsyncClient) -> Speaker:
	if settings.deepgram_api_key is None:
		return UnconfiguredSpeaker()
	return DeepgramSpeaker(
		api_key=settings.deepgram_api_key.get_secret_value(),
		http=http,
		model=settings.tts_model,
	)
