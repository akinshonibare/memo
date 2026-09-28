import time
from typing import Protocol

import httpx
import structlog
from pydantic import BaseModel

from memo.config import Settings
from memo.services.retry import RetryableError, TerminalError, classify_status

log = structlog.get_logger("memo.transcription")

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


class Transcript(BaseModel):
	text: str
	duration_ms: int | None
	model: str


class Transcriber(Protocol):
	model: str

	async def transcribe(self, audio: bytes, mime: str) -> Transcript: ...


class DeepgramTranscriber:
	"""Nova-3 over the prerecorded API (ADR 0013)."""

	def __init__(self, *, api_key: str, http: httpx.AsyncClient, model: str) -> None:
		self._api_key = api_key
		self._http = http
		self.model = model

	async def transcribe(self, audio: bytes, mime: str) -> Transcript:
		started = time.perf_counter()
		try:
			response = await self._http.post(
				DEEPGRAM_URL,
				params={"model": self.model, "smart_format": "true"},
				headers={
					"Authorization": f"Token {self._api_key}",
					"Content-Type": mime,
				},
				content=audio,
				timeout=httpx.Timeout(180.0, connect=10.0),
			)
		except (httpx.TimeoutException, httpx.TransportError) as exc:
			raise RetryableError(f"deepgram unreachable: {exc!r}") from exc
		if response.status_code >= 400:
			raise classify_status(response.status_code, response.text[:200])

		data = response.json()
		try:
			text = data["results"]["channels"][0]["alternatives"][0]["transcript"]
		except (KeyError, IndexError, TypeError) as exc:
			raise TerminalError(
				f"deepgram response missing transcript: {exc!r}"
			) from exc
		duration_s = data.get("metadata", {}).get("duration")
		transcript = Transcript(
			text=text,
			duration_ms=int(duration_s * 1000) if duration_s else None,
			model=self.model,
		)
		log.info(
			"transcribed",
			model=self.model,
			audio_bytes=len(audio),
			audio_ms=transcript.duration_ms,
			latency_ms=round((time.perf_counter() - started) * 1000),
			chars=len(text),
		)
		return transcript


class UnconfiguredTranscriber:
	"""Stands in when no STT key is set, so an audio capture fails visibly."""

	model = "unconfigured"

	async def transcribe(self, audio: bytes, mime: str) -> Transcript:
		raise TerminalError(
			"transcription is not configured (DEEPGRAM_API_KEY is unset)"
		)


def make_transcriber(settings: Settings, http: httpx.AsyncClient) -> Transcriber:
	if settings.deepgram_api_key is None:
		return UnconfiguredTranscriber()
	return DeepgramTranscriber(
		api_key=settings.deepgram_api_key.get_secret_value(),
		http=http,
		model=settings.deepgram_model,
	)
