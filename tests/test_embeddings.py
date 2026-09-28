import json
from typing import Any

import httpx
import pytest

from memo.services.embeddings import VoyageEmbedder
from memo.services.retry import RetryableError, TerminalError


def _embedder(handler: Any, dim: int = 4) -> VoyageEmbedder:
	return VoyageEmbedder(
		api_key="k",
		http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
		model="voyage-4",
		dim=dim,
	)


async def test_documents_and_queries_use_the_right_input_type_and_batch() -> None:
	requests: list[dict[str, Any]] = []

	def handler(request: httpx.Request) -> httpx.Response:
		body = json.loads(request.content)
		requests.append(body)
		assert request.headers["Authorization"] == "Bearer k"
		return httpx.Response(
			200,
			json={
				"data": [
					{"embedding": [float(i)] * 4, "index": i}
					for i in range(len(body["input"]))
				][::-1],  # out of order on purpose; must be re-sorted by index
				"usage": {"total_tokens": 7 * len(body["input"])},
			},
		)

	embedder = _embedder(handler)
	result = await embedder.embed_documents([f"t{i}" for i in range(130)])
	assert len(result.vectors) == 130
	assert result.vectors[5] == [5.0] * 4
	assert result.total_tokens == 7 * 130
	assert [r["input_type"] for r in requests] == ["document", "document"]
	assert [len(r["input"]) for r in requests] == [128, 2]
	assert requests[0]["output_dimension"] == 4

	await embedder.embed_query("q")
	assert requests[-1]["input_type"] == "query"


async def test_429_is_retryable_and_400_is_terminal() -> None:
	with pytest.raises(RetryableError):
		await _embedder(lambda r: httpx.Response(429)).embed_query("x")
	with pytest.raises(TerminalError):
		await _embedder(lambda r: httpx.Response(400, text="bad")).embed_query("x")


async def test_wrong_vector_size_is_terminal() -> None:
	def handler(request: httpx.Request) -> httpx.Response:
		return httpx.Response(
			200,
			json={
				"data": [{"embedding": [1.0, 2.0], "index": 0}],
				"usage": {"total_tokens": 1},
			},
		)

	with pytest.raises(TerminalError, match="wrong number or size"):
		await _embedder(handler).embed_query("x")
