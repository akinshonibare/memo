import time
from typing import Protocol

import httpx
import structlog
from pydantic import BaseModel

from memo.config import Settings
from memo.services.retry import RetryableError, TerminalError, classify_status

log = structlog.get_logger("memo.transcription")

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


# When each word was said: (start_ms, end_ms, word as written in the transcript).
Word = tuple[int, int, str]


class Transcript(BaseModel):
	text: str
	duration_ms: int | None
	model: str
	# Kept so the app can follow the transcript as the recording plays (ADR 0039).
	words: list[Word] = []


def _words(alternative: dict[str, object]) -> list[Word]:
	"""Deepgram's per-word timings, in milliseconds, with smart_format's punctuation
	and capitals. A malformed entry is skipped rather than failing the transcript: the
	words are a nicety on top of the text, never a reason to lose it."""
	out: list[Word] = []
	raw = alternative.get("words")
	if not isinstance(raw, list):
		return out
	for w in raw:
		if not isinstance(w, dict):
			continue
		word = w.get("punctuated_word") or w.get("word")
		start, end = w.get("start"), w.get("end")
		if not isinstance(word, str) or not isinstance(start, int | float):
			continue
		end_s = end if isinstance(end, int | float) else start
		out.append((round(start * 1000), round(end_s * 1000), word))
	return out


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
				# Deepgram trains on API traffic unless each request opts out.
				params={
					"model": self.model,
					"smart_format": "true",
					"mip_opt_out": "true",
				},
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
			alternative = data["results"]["channels"][0]["alternatives"][0]
			text = alternative["transcript"]
		except (KeyError, IndexError, TypeError) as exc:
			raise TerminalError(
				f"deepgram response missing transcript: {exc!r}"
			) from exc
		duration_s = data.get("metadata", {}).get("duration")
		transcript = Transcript(
			text=text,
			duration_ms=int(duration_s * 1000) if duration_s else None,
			model=self.model,
			words=_words(alternative),
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
