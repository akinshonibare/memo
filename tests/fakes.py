import datetime as dt
import hashlib
import math
import re
import uuid
from dataclasses import dataclass, field

from memo.schemas.derivations import Extraction
from memo.services.embeddings import EmbedResult
from memo.services.extraction import ExtractResult
from memo.services.llm import LLMCall
from memo.services.transcription import Transcript


def fake_llm_call(stage: str = "extract") -> LLMCall:
	return LLMCall.priced(
		provider="fake",
		model="claude-opus-5",
		stage=stage,
		input_tokens=400,
		output_tokens=60,
		latency_ms=10,
	)


class FakeEmbedder:
	"""Deterministic bag-of-words vectors: cosine similarity tracks token overlap, so
	the vector branch behaves like a fuzzy lexical search. Enough to test plumbing,
	ranking and fusion without a network."""

	model = "fake-bow"
	dim = 1024

	def __init__(self, *, fail_with: Exception | None = None) -> None:
		self.fail_with = fail_with
		self.calls = 0

	def _vector(self, text: str) -> list[float]:
		vector = [0.0] * self.dim
		for token in re.findall(r"[a-z0-9']+", text.lower()):
			vector[int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dim] += 1.0
		norm = math.sqrt(sum(v * v for v in vector)) or 1.0
		return [v / norm for v in vector]

	async def embed_documents(self, texts: list[str]) -> EmbedResult:
		self.calls += 1
		if self.fail_with is not None:
			raise self.fail_with
		return EmbedResult(
			vectors=[self._vector(t) for t in texts],
			total_tokens=sum(len(t) // 4 for t in texts),
		)

	async def embed_query(self, text: str) -> list[float]:
		return self._vector(text)


class FakeExtractor:
	model = "fake"
	version = "extract-v1/fake"

	def __init__(
		self,
		extraction: Extraction | None = None,
		*,
		fail_with: Exception | None = None,
	) -> None:
		self.extraction = extraction or Extraction()
		self.fail_with = fail_with
		self.calls = 0
		self.seen: list[tuple[str, dt.datetime, str]] = []

	async def extract(
		self, text: str, *, captured_at: dt.datetime, timezone: str
	) -> ExtractResult:
		self.calls += 1
		self.seen.append((text, captured_at, timezone))
		if self.fail_with is not None:
			raise self.fail_with
		return ExtractResult(
			extraction=self.extraction,
			raw=self.extraction.model_dump_json(),
			calls=[fake_llm_call()],
		)


class FakeObjectStore:
	def __init__(self) -> None:
		self.objects: dict[str, bytes] = {}
		self.deleted: list[str] = []

	async def presign_put(self, key: str, content_type: str, ttl_seconds: int) -> str:
		return f"https://fake-bucket.test/{key}?ttl={ttl_seconds}"

	async def exists(self, key: str) -> bool:
		return key in self.objects

	async def get(self, key: str) -> bytes:
		return self.objects[key]

	async def delete(self, key: str) -> None:
		self.objects.pop(key, None)
		self.deleted.append(key)


class FakeTranscriber:
	def __init__(
		self,
		text: str = "call Tunde about the pricing thing",
		*,
		fail_with: Exception | None = None,
		model: str = "fake-stt-1",
	) -> None:
		self.text = text
		self.fail_with = fail_with
		self.model = model
		self.calls = 0

	async def transcribe(self, audio: bytes, mime: str) -> Transcript:
		self.calls += 1
		if self.fail_with is not None:
			raise self.fail_with
		return Transcript(text=self.text, duration_ms=1500, model=self.model)


@dataclass
class EnqueuedJob:
	capture_id: uuid.UUID
	user_id: uuid.UUID
	request_id: str | None


@dataclass
class FakeQueue:
	jobs: list[EnqueuedJob] = field(default_factory=list)

	async def enqueue_process_capture(
		self, capture_id: uuid.UUID, user_id: uuid.UUID, request_id: str | None
	) -> None:
		self.jobs.append(EnqueuedJob(capture_id, user_id, request_id))
