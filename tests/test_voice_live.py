"""Voice step 2 (ADR 0025): the live relay between the app's socket and Deepgram."""

import asyncio
import json
from collections.abc import AsyncIterator, MutableMapping
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import ClientConnection

from memo.auth import websocket_user
from memo.config import Settings
from memo.main import create_app
from memo.services import live_stt
from memo.services.live_stt import _DeepgramUpstream, relay


class FakeUpstream:
	def __init__(self, script: list[dict[str, Any]], *, hold_open: bool = False):
		self.script = script
		self.hold_open = hold_open
		self.audio: list[bytes] = []
		self.keepalives = 0
		self.finished = False

	async def send_audio(self, pcm: bytes) -> None:
		self.audio.append(pcm)

	async def keepalive(self) -> None:
		self.keepalives += 1

	async def finish(self) -> None:
		self.finished = True

	async def events(self) -> AsyncIterator[dict[str, Any]]:
		for event in self.script:
			await asyncio.sleep(0)
			yield event
		if self.hold_open:
			await asyncio.sleep(3600)


class FakeClient:
	def __init__(
		self, messages: list[MutableMapping[str, Any]], *, then_wait: float = 0
	):
		self.messages = messages
		self.then_wait = then_wait
		self.sent: list[dict[str, Any]] = []

	async def receive(self) -> MutableMapping[str, Any]:
		if self.messages:
			await asyncio.sleep(0.01)
			return self.messages.pop(0)
		await asyncio.sleep(self.then_wait or 3600)
		return {"type": "websocket.disconnect"}

	async def send_json(self, data: dict[str, Any]) -> None:
		self.sent.append(data)


async def test_audio_goes_up_events_come_down_and_stop_ends_it() -> None:
	pcm = b"\x00\x01" * 8000  # half a second at 16 kHz, 16-bit mono
	client = FakeClient(
		[
			{"type": "websocket.receive", "bytes": pcm},
			{"type": "websocket.receive", "bytes": pcm},
			{"type": "websocket.receive", "text": json.dumps({"type": "stop"})},
		]
	)
	upstream = FakeUpstream(
		[
			{"type": "partial", "text": "what's on"},
			{"type": "final", "text": "what's on"},
		],
		hold_open=True,
	)

	seconds = await asyncio.wait_for(relay(client, upstream, sample_rate=16000), 5)

	assert upstream.audio == [pcm, pcm]
	assert client.sent[:2] == [
		{"type": "partial", "text": "what's on"},
		{"type": "final", "text": "what's on"},
	]
	assert upstream.finished, "the paid upstream socket is always closed"
	assert seconds == pytest.approx(1.0)


async def test_a_muted_mic_still_keeps_the_upstream_alive(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	monkeypatch.setattr(live_stt, "KEEPALIVE_SECONDS", 0.05)
	client = FakeClient([], then_wait=0.3)  # silent, then hangs up
	upstream = FakeUpstream([], hold_open=True)
	await asyncio.wait_for(relay(client, upstream, sample_rate=16000), 5)
	assert upstream.keepalives >= 2
	assert upstream.finished


class _FakeConn:
	def __init__(self, messages: list[dict[str, Any]]):
		self.messages = [json.dumps(m) for m in messages]

	def __aiter__(self) -> "_FakeConn":
		return self

	async def __anext__(self) -> str:
		if not self.messages:
			raise StopAsyncIteration
		return self.messages.pop(0)


def _results(text: str, *, final: bool, speech_final: bool = False) -> dict[str, Any]:
	return {
		"type": "Results",
		"is_final": final,
		"speech_final": speech_final,
		"channel": {"alternatives": [{"transcript": text}]},
	}


async def test_deepgram_results_become_partial_final_and_speech_started() -> None:
	conn = _FakeConn(
		[
			{"type": "SpeechStarted"},
			_results("what's", final=False),
			_results("what's on", final=True),
			_results("tomorrow", final=False),
			_results("tomorrow", final=True, speech_final=True),
			_results("and friday", final=True),
			{"type": "UtteranceEnd"},  # backstop: no speech_final came
		]
	)
	upstream = _DeepgramUpstream(cast(ClientConnection, conn))
	events = [e async for e in upstream.events()]
	assert events == [
		{"type": "speech_started"},
		{"type": "partial", "text": "what's"},
		{"type": "partial", "text": "what's on tomorrow"},
		{"type": "final", "text": "what's on tomorrow"},
		{"type": "final", "text": "and friday"},
	]


async def test_a_socket_without_a_bearer_header_has_no_user(app: FastAPI) -> None:
	class _WS:
		def __init__(self, headers: dict[str, str]):
			self.headers = headers
			self.app = app

	assert await websocket_user(cast(Any, _WS({}))) is None
	assert (
		await websocket_user(cast(Any, _WS({"authorization": "Bearer nope"}))) is None
	)
	assert await websocket_user(cast(Any, _WS({"authorization": "Basic x"}))) is None


def test_a_refused_socket_gets_a_close_code_the_app_can_read(
	settings: Settings,
) -> None:
	# Found in production: closing before accept() is an HTTP 403 at the handshake,
	# so 4401 never reached the app. Real app, real lifespan, no token.
	with TestClient(create_app(settings)) as client:
		with client.websocket_connect("/v1/voice/live") as ws:
			with pytest.raises(WebSocketDisconnect) as closed:
				ws.receive_text()
	assert closed.value.code == 4401
