import uuid
from dataclasses import dataclass, field

from memo.services.transcription import Transcript


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
