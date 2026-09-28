"""Voice mode for Ask, step 1 (ADR 0025): hold to talk, hear the answer.

Two small endpoints either side of the ordinary Ask flow, which is unchanged: the
transcribed question is sent as a normal message, streamed, cited and saved like a
typed one. The spoken question is never stored - it is a way of typing, not a note.
"""

import contextlib
import json
from typing import Annotated

import structlog
from fastapi import (
	APIRouter,
	Depends,
	HTTPException,
	Request,
	Response,
	WebSocket,
	status,
)
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from memo.auth import CurrentUser, websocket_user
from memo.routers.limits import limit_voice
from memo.services.live_stt import LiveTranscriber, minute_cost, relay
from memo.services.ratelimit import check
from memo.services.retry import RetryableError, TerminalError
from memo.services.speech import MAX_CHARS, Speaker
from memo.services.transcription import Transcriber

router = APIRouter(prefix="/voice", tags=["voice"])
log = structlog.get_logger("memo.voice")

# Close codes in the application range: the app reads them to decide what to show.
UNAUTHORIZED = 4401
RATE_LIMITED = 4429
BAD_START = 4400
UNAVAILABLE = 4503

MAX_QUESTION_BYTES = 5 * 1024 * 1024  # about five minutes of AAC; a question is seconds


def get_transcriber(request: Request) -> Transcriber:
	transcriber: Transcriber = request.app.state.transcriber
	return transcriber


def get_speaker(request: Request) -> Speaker:
	speaker: Speaker = request.app.state.speaker
	return speaker


class TranscriptOut(BaseModel):
	text: str


class SpeakIn(BaseModel):
	text: str = Field(min_length=1, max_length=MAX_CHARS)


def _upstream(exc: RetryableError | TerminalError) -> HTTPException:
	# 503 tells the app to try again; 422 that this input will never work.
	code = (
		status.HTTP_503_SERVICE_UNAVAILABLE
		if isinstance(exc, RetryableError)
		else status.HTTP_422_UNPROCESSABLE_CONTENT
	)
	return HTTPException(code, str(exc))


@router.post(
	"/transcribe", response_model=TranscriptOut, dependencies=[Depends(limit_voice)]
)
async def transcribe_question(
	request: Request,
	user_id: CurrentUser,
	transcriber: Annotated[Transcriber, Depends(get_transcriber)],
) -> TranscriptOut:
	mime = request.headers.get("content-type", "")
	if not mime.startswith("audio/"):
		raise HTTPException(
			status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "send the audio as audio/*"
		)
	audio = await request.body()
	if not audio:
		raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no audio")
	if len(audio) > MAX_QUESTION_BYTES:
		raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "question too long")
	try:
		transcript = await transcriber.transcribe(audio, mime)
	except (RetryableError, TerminalError) as exc:
		raise _upstream(exc) from exc
	return TranscriptOut(text=transcript.text.strip())


@router.post(
	"/speak",
	response_class=Response,
	responses={200: {"content": {"audio/mpeg": {}}}},
	dependencies=[Depends(limit_voice)],
)
async def speak(
	body: SpeakIn,
	user_id: CurrentUser,
	speaker: Annotated[Speaker, Depends(get_speaker)],
) -> Response:
	try:
		audio = await speaker.speak(body.text)
	except (RetryableError, TerminalError) as exc:
		raise _upstream(exc) from exc
	return Response(content=audio, media_type="audio/mpeg")


@router.websocket("/live")
async def live(websocket: WebSocket) -> None:
	"""Step 2 (ADR 0025): the phone streams PCM, Deepgram decides when a turn ends.

	First message from the app: {"type": "start", "sample_rate": N} - the phone's real
	rate is only known once its mic is running. Then binary PCM16 frames, and
	{"type": "stop"} to end. Down come partial / final / speech_started events."""
	user_id = await websocket_user(websocket)
	if user_id is None:
		await websocket.close(code=UNAUTHORIZED)
		return
	settings = websocket.app.state.settings
	decision = await check(
		websocket.app.state.redis,
		key=f"voice:{user_id}",
		limit=settings.rate_voice_per_minute,
		window_seconds=60,
		on_error="closed",
	)
	if not decision.allowed:
		await websocket.close(code=RATE_LIMITED)
		return
	await websocket.accept()
	try:
		start = json.loads(await websocket.receive_text())
		sample_rate = int(start.get("sample_rate", 0))
		if start.get("type") != "start" or not 8000 <= sample_rate <= 48000:
			raise ValueError(start)
	except (ValueError, TypeError, WebSocketDisconnect):
		await websocket.close(code=BAD_START)
		return

	transcriber: LiveTranscriber = websocket.app.state.live_transcriber
	seconds = 0.0
	try:
		async with transcriber.open(sample_rate=sample_rate) as upstream:
			await websocket.send_json({"type": "ready"})
			seconds = await relay(websocket, upstream, sample_rate=sample_rate)
	except (
		Exception
	) as exc:  # the provider refused or dropped: tell the app, then close
		log.warning("voice.live_failed", user_id=str(user_id), error=repr(exc))
		with contextlib.suppress(Exception):
			await websocket.close(code=UNAVAILABLE)
		return
	finally:
		log.info(
			"voice.live_closed",
			user_id=str(user_id),
			model=transcriber.model,
			audio_seconds=round(seconds, 1),
			cost_usd=str(minute_cost(seconds)),
		)
	with contextlib.suppress(Exception):
		await websocket.close()
