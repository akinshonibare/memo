import datetime as dt
import hashlib
import math
import re
import uuid
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, field

from anthropic.types import MessageParam

from memo.schemas.derivations import Extraction
from memo.schemas.faith import FaithExtraction
from memo.schemas.habits import HabitsExtraction
from memo.schemas.people import PeopleExtraction
from memo.services.apple import AppleIdentity, InvalidIdentityToken
from memo.services.embeddings import EmbedResult
from memo.services.extraction import ExtractResult, RawCompletion
from memo.services.faith import FaithResult
from memo.services.habits import HabitsResult
from memo.services.llm import LLMCall
from memo.services.people import PeopleResult
from memo.services.transcription import Transcript
from memo.services.vision import DescribeResult


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


class FakeAnswerProvider:
	"""Streams scripted tokens; records whether the stream was closed early."""

	model = "fake-answer"

	def __init__(
		self,
		tokens: list[str] | None = None,
		*,
		fail_with: Exception | None = None,
	) -> None:
		self.tokens = tokens or ["Your ", "notes ", "say ", "[1] ", "call Tunde."]
		self.fail_with = fail_with
		self.closed_early = False
		self.calls = 0
		self.last_messages: list[MessageParam] = []

	async def stream(
		self, *, system: str, messages: list[MessageParam]
	) -> AsyncGenerator[str, None]:
		self.calls += 1
		self.last_messages = list(messages)
		try:
			for token in self.tokens:
				if self.fail_with is not None:
					raise self.fail_with
				yield token
		except GeneratorExit:
			self.closed_early = True
			raise

	def usage(self) -> LLMCall:
		return fake_llm_call("answer")


class FakeRewriteProvider:
	model = "fake-rewrite"

	def __init__(self, rewritten: str = "call Tunde pricing") -> None:
		self.rewritten = rewritten
		self.calls = 0

	async def complete(
		self, *, system: str, messages: list[MessageParam]
	) -> RawCompletion:
		self.calls += 1
		return RawCompletion(
			text=self.rewritten,
			model=self.model,
			input_tokens=120,
			output_tokens=12,
			cache_read_tokens=0,
			cache_write_tokens=0,
			latency_ms=40,
		)


class FakeRedis:
	"""Just enough of redis for the rate limiter, with fresh counters per test."""

	def __init__(self) -> None:
		self.counts: dict[str, int] = {}
		self.ttls: dict[str, int] = {}

	async def incr(self, name: str) -> int:
		self.counts[name] = self.counts.get(name, 0) + 1
		return self.counts[name]

	async def expire(self, name: str, time: int) -> bool:
		self.ttls[name] = time
		return True


class FakeExtractor:
	model = "fake"
	version = "extract-v1.1/fake"

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


class FakePeopleReader:
	"""Scripted people (ADR 0029): a fixed answer, or one per note text."""

	model = "fake"
	version = "people-v1/fake"

	def __init__(
		self,
		extraction: PeopleExtraction | None = None,
		*,
		by_text: dict[str, PeopleExtraction] | None = None,
		fail_with: Exception | None = None,
	) -> None:
		self.extraction = extraction or PeopleExtraction()
		self.by_text = by_text or {}
		self.fail_with = fail_with
		self.calls = 0
		self.seen: list[tuple[str, dt.datetime, str, str | None]] = []

	async def read(
		self,
		text: str,
		*,
		captured_at: dt.datetime,
		timezone: str,
		author: str | None,
	) -> PeopleResult:
		self.calls += 1
		self.seen.append((text, captured_at, timezone, author))
		if self.fail_with is not None:
			raise self.fail_with
		extraction = self.by_text.get(text, self.extraction)
		return PeopleResult(
			extraction=extraction,
			raw=extraction.model_dump_json(),
			calls=[fake_llm_call("people")],
		)


class FakeFaithReader:
	"""Scripted prayers (ADR 0042): a fixed answer for every note it reads."""

	model = "fake"
	version = "faith-v1/fake"

	def __init__(
		self,
		extraction: FaithExtraction | None = None,
		*,
		fail_with: Exception | None = None,
	) -> None:
		self.extraction = extraction or FaithExtraction()
		self.fail_with = fail_with
		self.calls = 0
		self.seen: list[str] = []

	async def read(
		self, text: str, *, captured_at: dt.datetime, timezone: str
	) -> FaithResult:
		self.calls += 1
		self.seen.append(text)
		if self.fail_with is not None:
			raise self.fail_with
		return FaithResult(
			extraction=self.extraction,
			raw=self.extraction.model_dump_json(),
			calls=[fake_llm_call("faith")],
		)


class FakeHabitsReader:
	"""Scripted activities (ADR 0043): a fixed answer for every note it reads."""

	model = "fake"
	version = "habits-v1/fake"

	def __init__(
		self,
		extraction: HabitsExtraction | None = None,
		*,
		fail_with: Exception | None = None,
	) -> None:
		self.extraction = extraction or HabitsExtraction()
		self.fail_with = fail_with
		self.calls = 0
		self.seen: list[str] = []
		self.extra_keys: list[str] | None = None

	async def read(
		self,
		text: str,
		*,
		captured_at: dt.datetime,
		timezone: str,
		extra_keys: list[str] | None = None,
	) -> HabitsResult:
		self.calls += 1
		self.seen.append(text)
		self.extra_keys = extra_keys
		if self.fail_with is not None:
			raise self.fail_with
		return HabitsResult(
			extraction=self.extraction,
			raw=self.extraction.model_dump_json(),
			calls=[fake_llm_call("habits")],
		)


class FakeObjectStore:
	def __init__(self) -> None:
		self.objects: dict[str, bytes] = {}
		self.deleted: list[str] = []

	async def presign_put(self, key: str, content_type: str, ttl_seconds: int) -> str:
		return f"https://fake-bucket.test/{key}?ttl={ttl_seconds}"

	async def presign_get(self, key: str, ttl_seconds: int) -> str:
		return f"https://fake-bucket.test/{key}?get&ttl={ttl_seconds}"

	async def exists(self, key: str) -> bool:
		return key in self.objects

	async def get(self, key: str) -> bytes:
		return self.objects[key]

	async def delete(self, key: str) -> None:
		self.objects.pop(key, None)
		self.deleted.append(key)


class FakeDescriber:
	version = "describe-v1/fake"

	def __init__(
		self,
		text: str = (
			"Photo: A flyer for a jazz night.\n\nText in the photo:\nFri 2 Oct 8pm"
		),
		*,
		fail_with: Exception | None = None,
	) -> None:
		self.text = text
		self.fail_with = fail_with
		self.seen: list[tuple[int, str]] = []

	async def describe(self, image: bytes, mime: str) -> DescribeResult:
		self.seen.append((len(image), mime))
		if self.fail_with is not None:
			raise self.fail_with
		return DescribeResult(text=self.text, call=fake_llm_call("describe"))


class FakeSpeaker:
	model = "fake-tts"

	def __init__(self) -> None:
		self.said: list[str] = []
		self.voices: list[str | None] = []

	async def speak(self, text: str, *, voice: str | None = None) -> bytes:
		self.said.append(text)
		self.voices.append(voice)
		return b"ID3fake-mp3"


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
		self.keyterms: list[str] = []

	async def transcribe(
		self, audio: bytes, mime: str, *, keyterms: Sequence[str] = ()
	) -> Transcript:
		self.calls += 1
		self.keyterms = list(keyterms)
		if self.fail_with is not None:
			raise self.fail_with
		# One word every 400ms, the way Deepgram reports them (ADR 0039).
		words = [(i * 400, i * 400 + 350, w) for i, w in enumerate(self.text.split())]
		return Transcript(
			text=self.text, duration_ms=1500, model=self.model, words=words
		)


@dataclass
class EnqueuedJob:
	capture_id: uuid.UUID
	user_id: uuid.UUID
	request_id: str | None


@dataclass
class FakeQueue:
	jobs: list[EnqueuedJob] = field(default_factory=list)
	embeds: list[tuple[uuid.UUID, uuid.UUID, str]] = field(default_factory=list)

	async def enqueue_process_capture(
		self, capture_id: uuid.UUID, user_id: uuid.UUID, request_id: str | None
	) -> None:
		self.jobs.append(EnqueuedJob(capture_id, user_id, request_id))

	async def enqueue_embed_derivation(
		self,
		derivation_id: uuid.UUID,
		user_id: uuid.UUID,
		fingerprint: str,
		request_id: str | None,
	) -> None:
		self.embeds.append((derivation_id, user_id, fingerprint))


class FakeIdentityVerifier:
	"""Accepts tokens of the form `apple:<sub>[:<email>]`; rejects everything else."""

	async def verify(self, identity_token: str) -> AppleIdentity:
		parts = identity_token.split(":")
		if len(parts) < 2 or parts[0] != "apple" or not parts[1]:
			raise InvalidIdentityToken("not an apple token")
		return AppleIdentity(sub=parts[1], email=parts[2] if len(parts) > 2 else None)
