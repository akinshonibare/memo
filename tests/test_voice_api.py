"""Ask voice mode, step 1 (ADR 0025): transcribe a question, speak the answer."""

from fastapi import FastAPI
from httpx import AsyncClient

from memo.services.speech import speakable
from tests.fakes import FakeSpeaker


async def test_a_spoken_question_comes_back_as_text(client: AsyncClient) -> None:
	response = await client.post(
		"/v1/voice/transcribe",
		content=b"\x00\x01fake-m4a",
		headers={"Content-Type": "audio/m4a"},
	)
	assert response.status_code == 200, response.text
	assert response.json() == {"text": "what is on tomorrow"}

	not_audio = await client.post(
		"/v1/voice/transcribe", content=b"{}", headers={"Content-Type": "text/plain"}
	)
	assert not_audio.status_code == 415
	empty = await client.post(
		"/v1/voice/transcribe", content=b"", headers={"Content-Type": "audio/m4a"}
	)
	assert empty.status_code == 422


async def test_an_answer_sentence_is_spoken(app: FastAPI, client: AsyncClient) -> None:
	response = await client.post(
		"/v1/voice/speak", json={"text": "Your dentist is Friday at 2:30 [1]."}
	)
	assert response.status_code == 200, response.text
	assert response.headers["content-type"] == "audio/mpeg"
	assert response.content == b"ID3fake-mp3"
	speaker: FakeSpeaker = app.state.speaker
	assert speaker.said == ["Your dentist is Friday at 2:30 [1]."]
	too_long = await client.post("/v1/voice/speak", json={"text": "a" * 2001})
	assert too_long.status_code == 422


def test_citation_markers_and_markdown_are_not_read_aloud() -> None:
	assert speakable("It's **Friday** [1], and the MOT [2, 3].") == (
		"It's Friday, and the MOT."
	)
