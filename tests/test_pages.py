"""The public privacy page TestFlight and the App Store link to."""

import inspect

import httpx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from memo.services.live_stt import DeepgramLiveTranscriber
from memo.services.speech import DeepgramSpeaker
from memo.services.transcription import DeepgramTranscriber


async def test_privacy_is_public_and_names_every_processor(app: FastAPI) -> None:
	async with AsyncClient(
		transport=ASGITransport(app=app), base_url="http://t"
	) as anon:
		page = await anon.get("/privacy")
	assert page.status_code == 200
	assert page.headers["content-type"].startswith("text/html")
	for processor in ("Anthropic", "Deepgram", "Voyage", "Railway", "Apple"):
		assert processor in page.text, processor
	assert "Delete account" in page.text


async def test_every_deepgram_call_opts_out_of_training() -> None:
	# The privacy page promises this; these check the requests actually sent.
	seen: list[httpx.URL] = []

	def handler(request: httpx.Request) -> httpx.Response:
		seen.append(request.url)
		if "listen" in request.url.path:
			return httpx.Response(
				200,
				json={
					"results": {"channels": [{"alternatives": [{"transcript": "hi"}]}]}
				},
			)
		return httpx.Response(200, content=b"ID3")

	async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
		await DeepgramTranscriber(api_key="k", http=http, model="nova-3").transcribe(
			b"audio", "audio/m4a"
		)
		await DeepgramSpeaker(api_key="k", http=http, model="aura-2-thalia-en").speak(
			"Hello there."
		)
	assert [u.params.get("mip_opt_out") for u in seen] == ["true", "true"]

	# The live socket's URL is built inside open(); check the source it builds from.
	source = inspect.getsource(DeepgramLiveTranscriber.open)
	assert '"mip_opt_out": "true"' in source
