from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from memo.models import Capture


class CaptureStatus(StrEnum):
	PENDING = "pending"
	TRANSCRIBING = "transcribing"
	DESCRIBING = "describing"  # a photo becoming text (ADR 0024)
	EXTRACTING = "extracting"
	EMBEDDING = "embedding"
	READY = "ready"
	FAILED = "failed"


# The design doc's `retry_wait` is arq's backoff delay, not a row state: a capture
# stays in its current stage while a retry is scheduled.
_ALLOWED: dict[CaptureStatus, frozenset[CaptureStatus]] = {
	CaptureStatus.PENDING: frozenset(
		{
			CaptureStatus.TRANSCRIBING,
			CaptureStatus.DESCRIBING,
			CaptureStatus.EXTRACTING,
		}
	),
	CaptureStatus.TRANSCRIBING: frozenset(
		{CaptureStatus.EXTRACTING, CaptureStatus.FAILED}
	),
	CaptureStatus.DESCRIBING: frozenset(
		{CaptureStatus.EXTRACTING, CaptureStatus.FAILED}
	),
	CaptureStatus.EXTRACTING: frozenset(
		{CaptureStatus.EMBEDDING, CaptureStatus.FAILED}
	),
	CaptureStatus.EMBEDDING: frozenset({CaptureStatus.READY, CaptureStatus.FAILED}),
	CaptureStatus.READY: frozenset(),
	CaptureStatus.FAILED: frozenset({CaptureStatus.PENDING}),
}


class IllegalTransition(Exception):
	def __init__(self, frm: CaptureStatus, to: CaptureStatus) -> None:
		super().__init__(f"illegal capture transition {frm.value} -> {to.value}")
		self.frm = frm
		self.to = to


def transition(
	capture: Capture, to: CaptureStatus, *, failure_reason: str | None = None
) -> None:
	frm = CaptureStatus(capture.status)
	if to not in _ALLOWED[frm]:
		raise IllegalTransition(frm, to)
	if to is CaptureStatus.FAILED and not failure_reason:
		raise ValueError("a failed capture needs a failure_reason")

	capture.status = to.value
	capture.failure_reason = failure_reason if to is CaptureStatus.FAILED else None
	if to is CaptureStatus.PENDING:
		capture.attempt_count = 0
