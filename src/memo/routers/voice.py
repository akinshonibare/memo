"""Voice mode for Ask, step 1 (ADR 0025): hold to talk, hear the answer.

Two small endpoints either side of the ordinary Ask flow, which is unchanged: the
transcribed question is sent as a normal message, streamed, cited and saved like a
typed one. The spoken question is never stored - it is a way of typing, not a note.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from memo.auth import CurrentUser
from memo.routers.limits import limit_voice
from memo.services.retry import RetryableError, TerminalError
from memo.services.speech import MAX_CHARS, Speaker
from memo.services.transcription import Transcriber

router = APIRouter(prefix="/voice", tags=["voice"])

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
