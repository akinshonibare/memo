"""Live speech to text for Ask's voice mode, step 2 (ADR 0025).

The phone streams 16-bit PCM over a WebSocket to the API; this relays it to Deepgram's
live endpoint and turns Deepgram's results into three events the app acts on:

- `partial`: words so far, for showing what's being heard;
- `final`: the utterance is over (Deepgram's endpointing decided the speaker stopped),
  so the app sends it to Ask exactly as if it had been typed;
- `speech_started`: someone began talking (used to interrupt, when echo allows).

The relay is written against two small protocols rather than concrete sockets, so the
whole loop is testable without a network.
"""

import asyncio
import contextlib
import json
import ssl
import time
from collections.abc import AsyncIterator, MutableMapping
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlencode

import certifi
import structlog
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from memo.config import Settings

log = structlog.get_logger("memo.live_stt")

DEEPGRAM_LIVE_URL = "wss://api.deepgram.com/v1/listen"
PRICE_PER_MINUTE = Decimal("0.0048")  # Nova-3 streaming, monolingual; 2026-09-28
# Deepgram closes a socket that hears nothing for ~10 s. The app mutes the mic while an
# answer plays, which can be longer than that, so the relay keeps it open.
KEEPALIVE_SECONDS = 5.0
# certifi's bundle, the one httpx already uses for every other provider call: the
# system store differs between a laptop and the container, and a missing CA should not
# be how voice fails in one of them.
_TLS = ssl.create_default_context(cafile=certifi.where())


class Upstream(Protocol):
	"""One live transcription session at the provider."""

	async def send_audio(self, pcm: bytes) -> None: ...
	async def keepalive(self) -> None: ...
	async def finish(self) -> None: ...
	def events(self) -> AsyncIterator[dict[str, Any]]: ...


class Client(Protocol):
	"""The app's end of the WebSocket."""

	async def receive(self) -> MutableMapping[str, Any]: ...  # raw ASGI message
	async def send_json(self, data: dict[str, Any]) -> None: ...


class LiveTranscriber(Protocol):
	model: str

	def open(
		self, *, sample_rate: int
	) -> contextlib.AbstractAsyncContextManager[Upstream]: ...


class _DeepgramUpstream:
	def __init__(self, conn: ClientConnection) -> None:
		self._conn = conn

	async def send_audio(self, pcm: bytes) -> None:
		await self._conn.send(pcm)

	async def keepalive(self) -> None:
		await self._conn.send(json.dumps({"type": "KeepAlive"}))

	async def finish(self) -> None:
		with contextlib.suppress(ConnectionClosed):
			await self._conn.send(json.dumps({"type": "CloseStream"}))

	async def events(self) -> AsyncIterator[dict[str, Any]]:
		"""Deepgram's messages, reduced to partial / final / speech_started."""
		finals: list[str] = []
		try:
			async for raw in self._conn:
				if isinstance(raw, bytes):
					continue
				message = json.loads(raw)
				kind = message.get("type")
				if kind == "SpeechStarted":
					yield {"type": "speech_started"}
				elif kind == "Results":
					alts = message.get("channel", {}).get("alternatives") or [{}]
					words = (alts[0].get("transcript") or "").strip()
					if message.get("is_final"):
						if words:
							finals.append(words)
						if message.get("speech_final") and finals:
							yield {"type": "final", "text": " ".join(finals)}
							finals = []
					elif words:
						yield {"type": "partial", "text": " ".join([*finals, words])}
				elif kind == "UtteranceEnd" and finals:
					# The backstop when endpointing never set speech_final (noise).
					yield {"type": "final", "text": " ".join(finals)}
					finals = []
		except ConnectionClosed:
			return


class DeepgramLiveTranscriber:
	def __init__(self, *, api_key: str, model: str) -> None:
		self._api_key = api_key
		self.model = model

	@contextlib.asynccontextmanager
	async def open(self, *, sample_rate: int) -> AsyncIterator[Upstream]:
		params = urlencode(
			{
				"model": self.model,
				# Deepgram trains on API traffic unless each request opts out.
				"mip_opt_out": "true",
				"encoding": "linear16",
				"sample_rate": sample_rate,
				"channels": 1,
				"smart_format": "true",
				"interim_results": "true",
				"endpointing": 400,  # ms of silence that ends a turn
				"utterance_end_ms": 1200,
				"vad_events": "true",
			}
		)
		async with connect(
			f"{DEEPGRAM_LIVE_URL}?{params}",
			additional_headers={"Authorization": f"Token {self._api_key}"},
			open_timeout=5,
			ssl=_TLS,
		) as conn:
			yield _DeepgramUpstream(conn)


async def relay(client: Client, upstream: Upstream, *, sample_rate: int) -> float:
	"""Pump audio up and events down until either side hangs up. Returns the seconds
	of audio relayed, for the cost log. Every task is cancelled on the way out: a
	leaked pump would hold a paid Deepgram socket open."""
	audio_bytes = 0
	last_audio = time.monotonic()

	async def up() -> None:
		nonlocal audio_bytes, last_audio
		while True:
			message = await client.receive()
			if message["type"] == "websocket.disconnect":
				return
			if message.get("bytes"):
				audio_bytes += len(message["bytes"])
				last_audio = time.monotonic()
				await upstream.send_audio(message["bytes"])
			elif message.get("text"):
				if json.loads(message["text"]).get("type") == "stop":
					return

	async def down() -> None:
		async for event in upstream.events():
			await client.send_json(event)

	async def keepalive() -> None:
		while True:
			await asyncio.sleep(KEEPALIVE_SECONDS)
			if time.monotonic() - last_audio >= KEEPALIVE_SECONDS:
				await upstream.keepalive()

	tasks = [asyncio.create_task(f()) for f in (up, down, keepalive)]
	try:
		await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
	finally:
		for task in tasks:
			task.cancel()
		await asyncio.gather(*tasks, return_exceptions=True)
		await upstream.finish()
	return audio_bytes / (2 * sample_rate)


def minute_cost(seconds: float) -> Decimal:
	return PRICE_PER_MINUTE * Decimal(str(round(seconds, 1))) / 60


class UnconfiguredLiveTranscriber:
	model = "unconfigured"

	def open(
		self, *, sample_rate: int
	) -> contextlib.AbstractAsyncContextManager[Upstream]:
		raise RuntimeError("live transcription is not configured (DEEPGRAM_API_KEY)")


def make_live_transcriber(settings: Settings) -> LiveTranscriber:
	if settings.deepgram_api_key is None:
		return UnconfiguredLiveTranscriber()
	return DeepgramLiveTranscriber(
		api_key=settings.deepgram_api_key.get_secret_value(),
		model=settings.deepgram_model,
	)
