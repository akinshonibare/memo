"""Embeddings via Voyage AI (ADR 0016). Asymmetric on purpose: documents and queries
are embedded with different `input_type`s, so the two functions have different names
rather than one function with a flag that is easy to pass wrongly."""

import time
from dataclasses import dataclass
from typing import Protocol

import httpx
import structlog

from memo.config import Settings
from memo.services.retry import RetryableError, TerminalError, classify_status

log = structlog.get_logger("memo.embeddings")

BATCH = 128


@dataclass(frozen=True)
class EmbedResult:
	vectors: list[list[float]]
	total_tokens: int


class Embedder(Protocol):
	@property
	def model(self) -> str: ...

	@property
	def dim(self) -> int: ...

	async def embed_documents(self, texts: list[str]) -> EmbedResult: ...

	async def embed_query(self, text: str) -> list[float]: ...


class VoyageEmbedder:
	def __init__(
		self,
		*,
		api_key: str,
		http: httpx.AsyncClient,
		model: str,
		dim: int,
		url: str,
	) -> None:
		self._api_key = api_key
		self._http = http
		self._url = url
		self.model = model
		self.dim = dim

	async def _call(self, texts: list[str], input_type: str) -> EmbedResult:
		started = time.perf_counter()
		try:
			response = await self._http.post(
				self._url,
				json={
					"input": texts,
					"model": self.model,
					"input_type": input_type,
					"output_dimension": self.dim,
				},
				headers={"Authorization": f"Bearer {self._api_key}"},
				timeout=httpx.Timeout(60.0, connect=10.0),
			)
		except (httpx.TimeoutException, httpx.TransportError) as exc:
			raise RetryableError(f"voyage unreachable: {exc!r}") from exc
		if response.status_code >= 400:
			raise classify_status(response.status_code, response.text[:200])
		data = response.json()
		try:
			rows = sorted(data["data"], key=lambda r: r["index"])
			vectors = [list(map(float, r["embedding"])) for r in rows]
			total_tokens = int(data["usage"]["total_tokens"])
		except (KeyError, TypeError, ValueError) as exc:
			raise TerminalError(f"voyage response malformed: {exc!r}") from exc
		if len(vectors) != len(texts) or any(len(v) != self.dim for v in vectors):
			raise TerminalError("voyage returned the wrong number or size of vectors")
		log.info(
			"embed.call",
			model=self.model,
			input_type=input_type,
			inputs=len(texts),
			tokens=total_tokens,
			latency_ms=round((time.perf_counter() - started) * 1000),
		)
		return EmbedResult(vectors=vectors, total_tokens=total_tokens)

	async def embed_documents(self, texts: list[str]) -> EmbedResult:
		vectors: list[list[float]] = []
		tokens = 0
		for i in range(0, len(texts), BATCH):
			result = await self._call(texts[i : i + BATCH], "document")
			vectors.extend(result.vectors)
			tokens += result.total_tokens
		return EmbedResult(vectors=vectors, total_tokens=tokens)

	async def embed_query(self, text: str) -> list[float]:
		return (await self._call([text], "query")).vectors[0]


class UnconfiguredEmbedder:
	"""Stands in when no Voyage key is set, so the embed stage fails visibly."""

	model = "unconfigured"
	dim = 0

	async def embed_documents(self, texts: list[str]) -> EmbedResult:
		raise TerminalError("embeddings are not configured (VOYAGE_API_KEY is unset)")

	async def embed_query(self, text: str) -> list[float]:
		raise TerminalError("embeddings are not configured (VOYAGE_API_KEY is unset)")


def make_embedder(settings: Settings, http: httpx.AsyncClient) -> Embedder:
	if settings.voyage_api_key is None:
		return UnconfiguredEmbedder()
	return VoyageEmbedder(
		api_key=settings.voyage_api_key.get_secret_value(),
		http=http,
		model=settings.embedding_model,
		dim=settings.embedding_dim,
		url=settings.voyage_api_url,
	)
