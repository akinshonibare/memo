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
from memo.db import DbSession, bind_user
from memo.models import User
from memo.routers.limits import limit_voice, within_daily_budget
from memo.services.budget import (
	budget_for,
	live_call,
	log_calls,
	speak_call,
	transcribe_call,
)
from memo.services.live_stt import LiveTranscriber, minute_cost, relay
from memo.services.people import keyterms
from memo.services.ratelimit import check
from memo.services.retry import ProviderUnavailable, RetryableError, TerminalError
from memo.services.speech import MAX_CHARS, VOICES, Speaker
from memo.services.transcription import Transcriber

router = APIRouter(prefix="/voice", tags=["voice"])
log = structlog.get_logger("memo.voice")

# Close codes in the application range: the app reads them to decide what to show.
UNAUTHORIZED = 4401
RATE_LIMITED = 4429
BAD_START = 4400
UNAVAILABLE = 4503

MAX_QUESTION_BYTES = 5 * 1024 * 1024  # about five minutes of AAC; a question is seconds
# What a live stream is for, so the cost log can tell dictation (ADR 0051) from Ask.
PURPOSES = frozenset({"ask", "note"})


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
	# Only for previewing a voice in Settings; answers use the saved choice.
	voice: str | None = None


def _upstream(exc: RetryableError | TerminalError) -> HTTPException:
	# 503 tells the app to try again; 422 that this input will never work.
	code = (
		status.HTTP_503_SERVICE_UNAVAILABLE
		if isinstance(exc, RetryableError)
		else status.HTTP_422_UNPROCESSABLE_CONTENT
	)
	detail = exc.for_users if isinstance(exc, ProviderUnavailable) else str(exc)
	return HTTPException(code, detail)


@router.post(
	"/transcribe",
	response_model=TranscriptOut,
	dependencies=[Depends(limit_voice), Depends(within_daily_budget)],
)
async def transcribe_question(
	request: Request,
	user_id: CurrentUser,
	session: DbSession,
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
		transcript = await transcriber.transcribe(
			audio, mime, keyterms=await keyterms(session, user_id)
		)
	except (RetryableError, TerminalError) as exc:
		raise _upstream(exc) from exc
	# Priced into the daily AI budget (ADR 0063).
	seconds = (transcript.duration_ms or 0) / 1000
	log_calls(session, user_id, [transcribe_call(transcript.model, seconds)])
	await session.commit()
	return TranscriptOut(text=transcript.text.strip())


@router.post(
	"/speak",
	response_class=Response,
	responses={200: {"content": {"audio/mpeg": {}}}},
	dependencies=[Depends(limit_voice), Depends(within_daily_budget)],
)
async def speak(
	body: SpeakIn,
	session: DbSession,
	user_id: CurrentUser,
	speaker: Annotated[Speaker, Depends(get_speaker)],
) -> Response:
	if body.voice is not None and body.voice not in VOICES:
		raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown voice")
	user = await session.get(User, user_id)
	voice = body.voice or (user.voice if user is not None else None)
	try:
		audio = await speaker.speak(body.text, voice=voice)
	except (RetryableError, TerminalError) as exc:
		raise _upstream(exc) from exc
	# Priced into the daily AI budget (ADR 0063), by the character.
	log_calls(session, user_id, [speak_call(voice or "aura-2", len(body.text))])
	await session.commit()
	return Response(content=audio, media_type="audio/mpeg")


@router.websocket("/live")
async def live(websocket: WebSocket) -> None:
	"""Step 2 (ADR 0025): the phone streams PCM, Deepgram decides when a turn ends.

	First message from the app: {"type": "start", "sample_rate": N} - the phone's real
	rate is only known once its mic is running - and "purpose": "note" when it shows
	the words of a voice note being recorded (ADR 0051) rather than asking. Then binary
	PCM16 frames, and {"type": "stop"} to end. Down come partial / final /
	speech_started events."""
	# Accept before refusing: a socket closed before accept() fails its handshake with
	# a bare HTTP 403, and the app could not tell "sign in again" from "try later".
	await websocket.accept()
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
	# At the daily AI limit (ADR 0063): refused like a rate limit. A note being
	# recorded still records; only its live words are off.
	async with websocket.app.state.session_factory() as session:
		await bind_user(session, user_id)
		user = await session.get(User, user_id)
		over = user is not None and (await budget_for(session, user)).over
	if over:
		await websocket.close(code=RATE_LIMITED)
		return
	try:
		start = json.loads(await websocket.receive_text())
		sample_rate = int(start.get("sample_rate", 0))
		purpose = start.get("purpose", "ask")
		if (
			start.get("type") != "start"
			or not 8000 <= sample_rate <= 48000
			or purpose not in PURPOSES
		):
			raise ValueError(start)
	except (ValueError, TypeError, WebSocketDisconnect):
		await websocket.close(code=BAD_START)
		return

	transcriber: LiveTranscriber = websocket.app.state.live_transcriber
	# Names from the notes, so Ask hears "Tunde" when it's spoken (ADR 0013).
	async with websocket.app.state.session_factory() as session:
		await bind_user(session, user_id)
		terms = await keyterms(session, user_id)
	seconds = 0.0
	try:
		async with transcriber.open(
			sample_rate=sample_rate, keyterms=terms
		) as upstream:
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
			purpose=purpose,
			model=transcriber.model,
			audio_seconds=round(seconds, 1),
			cost_usd=str(minute_cost(seconds)),
		)
		if seconds > 0:
			# Into the cost log, so live words count towards the daily budget.
			with contextlib.suppress(Exception):
				async with websocket.app.state.session_factory() as session:
					await bind_user(session, user_id)
					log_calls(session, user_id, [live_call(transcriber.model, seconds)])
					await session.commit()
	with contextlib.suppress(Exception):
		await websocket.close()
