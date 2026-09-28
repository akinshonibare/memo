import httpx
import pytest

from memo.services.retry import (
	RetryableError,
	TerminalError,
	backoff_delay,
	classify_status,
)
from memo.services.transcription import DeepgramTranscriber


def test_backoff_grows_exponentially_and_caps() -> None:
	for attempt in range(1, 12):
		ceiling = min(300.0, 2.0 * 2 ** (attempt - 1))
		for _ in range(50):
			assert 0 <= backoff_delay(attempt) <= ceiling


def test_backoff_has_jitter() -> None:
	# Identical delays across workers is the thundering herd; jitter is the fix.
	samples = {round(backoff_delay(5), 3) for _ in range(20)}
	assert len(samples) > 1


def test_backoff_rejects_zero_attempt() -> None:
	with pytest.raises(ValueError):
		backoff_delay(0)


@pytest.mark.parametrize(
	("status", "kind"),
	[(429, RetryableError), (500, RetryableError), (503, RetryableError)]
	+ [(400, TerminalError), (401, TerminalError), (413, TerminalError)],
)
def test_http_status_classification(status: int, kind: type[Exception]) -> None:
	assert isinstance(classify_status(status), kind)


def _transcriber(handler: httpx.MockTransport) -> DeepgramTranscriber:
	return DeepgramTranscriber(
		api_key="test-key", http=httpx.AsyncClient(transport=handler), model="nova-3"
	)


async def test_deepgram_parses_a_transcript() -> None:
	seen: dict[str, str] = {}

	def handler(request: httpx.Request) -> httpx.Response:
		seen["auth"] = request.headers["Authorization"]
		seen["content_type"] = request.headers["Content-Type"]
		seen["model"] = request.url.params["model"]
		return httpx.Response(
			200,
			json={
				"metadata": {"duration": 2.5},
				"results": {
					"channels": [{"alternatives": [{"transcript": "hello Tunde"}]}]
				},
			},
		)

	transcript = await _transcriber(httpx.MockTransport(handler)).transcribe(
		b"bytes", "audio/m4a"
	)
	assert transcript.text == "hello Tunde"
	assert transcript.duration_ms == 2500
	assert seen == {
		"auth": "Token test-key",
		"content_type": "audio/m4a",
		"model": "nova-3",
	}


async def test_deepgram_429_is_retryable_and_400_is_terminal() -> None:
	with pytest.raises(RetryableError):
		await _transcriber(
			httpx.MockTransport(lambda r: httpx.Response(429))
		).transcribe(b"x", "audio/m4a")
	with pytest.raises(TerminalError):
		await _transcriber(
			httpx.MockTransport(lambda r: httpx.Response(400, text="bad audio"))
		).transcribe(b"x", "audio/m4a")


async def test_deepgram_timeout_is_retryable() -> None:
	def handler(request: httpx.Request) -> httpx.Response:
		raise httpx.ReadTimeout("slow", request=request)

	with pytest.raises(RetryableError):
		await _transcriber(httpx.MockTransport(handler)).transcribe(b"x", "audio/m4a")
