"""A later note that changes an earlier item (ADR 0048): it answers an open prayer, or
carries on an idea. The brief calls reconciliation the hardest problem in the system;
this is the first slice of it, with the safest policy: memo suggests, the author says
yes or no, and nothing changes until they do.

Two steps, as in entity resolution:

1. **Candidates, cheaply.** Open prayers (only for someone with Faith on, and only when
   the note has an answer-ish word in it) and the ideas nearest the note in meaning,
   read from the embeddings the embed stage just wrote: no extra embedding call.
2. **A decision, by a model.** Similar is not the same as "answers": a note praying
   about the job again is close to the prayer and answers nothing. So a model reads the
   note with the numbered candidates and returns the changes, each with a quote from
   the note that the validation checks is really there.

Most notes stop at step 1 and cost nothing.
"""

import datetime as dt
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy import ColumnElement, and_, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from memo.config import Settings
from memo.ids import uuid7
from memo.models import Capture, Derivation, User
from memo.prompts.reconcile_v1 import (
	RECONCILE_VERSION,
	SYSTEM_PROMPT,
	Listed,
	repair_message,
	user_message,
)
from memo.schemas.derivations import PRAYER, UNREVIEWED, UPDATE
from memo.schemas.reconcile import Reconciliation, UpdateOut, UpdateTarget
from memo.services.extraction import AnthropicExtractionProvider, complete_validated
from memo.services.faith import faith_on, names_for
from memo.services.llm import LLMCall
from memo.services.retry import TerminalError

log = structlog.get_logger("memo.reconcile")

# How many of each the model is shown. Open prayers are few; ideas are the nearest.
PRAYERS_SHOWN = 20
IDEAS_SHOWN = 5
# Cosine distance under which an idea counts as near the note. A starting guess for
# voyage-4, like related notes' 0.45 (ADR 0028): too loose costs a model call and a
# "no"; too tight misses. The eval measures the decision, not this; real notes will.
IDEA_MAX_DISTANCE = 0.45

# An answer is news: something happened, was decided, came through. A note with none of
# these words is not compared with open prayers. A missed answer costs one tap in Faith;
# comparing every note with every prayer costs a model call per note, forever.
_ANSWERISH = re.compile(
	r"\b(answer\w*|thank\w*|grateful|praise\w*|god is good|came through|good news|"
	r"news|got (the|it|in|my|a)|offer\w*|passed|healed|recover\w*|better|well|worked "
	r"out|finally|approved|accepted|result\w*|born|arrived|safe\w*|sorted|done|"
	r"decided|signed|clear\w*|peace|yes|no longer|complet\w*|started|came|won|sold|"
	r"through|over)\b",
	re.IGNORECASE,
)


def might_answer(text: str | None) -> bool:
	return bool(text and _ANSWERISH.search(text))


# --- the reader -----------------------------------------------------------------------


@dataclass(frozen=True)
class ReconcileResult:
	result: Reconciliation
	raw: str
	calls: list[LLMCall]


class ReconcilerLike(Protocol):
	@property
	def model(self) -> str: ...

	@property
	def version(self) -> str: ...

	async def read(
		self,
		text: str,
		listed: list[Listed],
		*,
		captured_at: dt.datetime,
		timezone: str,
	) -> ReconcileResult: ...


class Reconciler:
	def __init__(self, provider: AnthropicExtractionProvider, *, max_repairs: int = 1):
		self._provider = provider
		self._max_repairs = max_repairs

	@property
	def model(self) -> str:
		return self._provider.model

	@property
	def version(self) -> str:
		return f"{RECONCILE_VERSION}/{self.model}"

	async def read(
		self,
		text: str,
		listed: list[Listed],
		*,
		captured_at: dt.datetime,
		timezone: str,
	) -> ReconcileResult:
		context = {"labels": {item.label for item in listed}, "entry": text}
		result, raw, calls = await complete_validated(
			self._provider,
			system=SYSTEM_PROMPT,
			first=user_message(
				text, listed, captured_at=captured_at, timezone=timezone
			),
			parse=lambda raw: Reconciliation.model_validate_json(raw, context=context),
			repair=repair_message,
			stage="reconcile",
			max_repairs=self._max_repairs,
		)
		return ReconcileResult(result=result, raw=raw, calls=calls)


class UnconfiguredReconciler:
	"""Stands in when no Anthropic key is set, so the stage fails visibly."""

	model = "unconfigured"
	version = f"{RECONCILE_VERSION}/unconfigured"

	async def read(
		self,
		text: str,
		listed: list[Listed],
		*,
		captured_at: dt.datetime,
		timezone: str,
	) -> ReconcileResult:
		raise TerminalError(
			"the reconcile stage is not configured (no ANTHROPIC_API_KEY)"
		)


def make_reconciler(settings: Settings) -> ReconcilerLike:
	if settings.anthropic_api_key is None:
		return UnconfiguredReconciler()
	client = AsyncAnthropic(
		api_key=settings.anthropic_api_key.get_secret_value(), max_retries=0
	)
	return Reconciler(
		AnthropicExtractionProvider(
			client, model=settings.extraction_model, schema=Reconciliation
		)
	)


# --- candidates -----------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
	listed: Listed
	target: Derivation


def _written_at() -> ColumnElement[dt.datetime]:
	"""When an item was written: its note's time, or its own for an item with none."""
	return func.coalesce(Capture.created_at, Derivation.created_at)


async def _open_prayers(session: AsyncSession, capture: Capture) -> list[Candidate]:
	rows = (
		await session.execute(
			select(Derivation, _written_at())
			.outerjoin(
				Capture,
				and_(
					Capture.id == Derivation.capture_id,
					Capture.user_id == Derivation.user_id,
				),
			)
			.where(
				Derivation.user_id == capture.user_id,
				Derivation.kind == PRAYER,
				Derivation.status != "rejected",
				Derivation.completed_at.is_(None),
				Derivation.capture_id.is_distinct_from(capture.id),
				_written_at() < capture.created_at,
			)
			.order_by(_written_at().desc())
			.limit(PRAYERS_SHOWN)
		)
	).all()
	names = await names_for(session, capture.user_id, (d.person_id for d, _ in rows))
	return [
		Candidate(
			Listed(
				label=f"P{i}",
				title=str(d.payload.get("title", "")),
				detail=f"for {names[d.person_id]}" if d.person_id in names else None,
				written_on=written.date(),
			),
			d,
		)
		for i, (d, written) in enumerate(rows, start=1)
	]


_NEAR_IDEAS = text(
	"""
	SELECT d.id, min(k.embedding <=> n.embedding) AS distance
	FROM chunks n
	JOIN chunks k
		ON k.user_id = n.user_id AND k.superseded_at IS NULL
		AND k.derivation_id IS NOT NULL
	JOIN derivations d ON d.id = k.derivation_id AND d.user_id = k.user_id
	LEFT JOIN captures c ON c.id = d.capture_id AND c.user_id = d.user_id
	WHERE n.user_id = :user_id AND n.capture_id = :capture_id
		AND n.superseded_at IS NULL
		AND d.kind = 'idea' AND d.status <> 'rejected'
		AND (d.capture_id IS NULL OR d.capture_id <> :capture_id)
		AND coalesce(c.created_at, d.created_at) < :written_at
	GROUP BY d.id
	HAVING min(k.embedding <=> n.embedding) <= :max_distance
	ORDER BY distance
	LIMIT :limit
	"""
)


async def _near_ideas(session: AsyncSession, capture: Capture) -> list[Candidate]:
	"""The ideas nearest this note, by the note's own chunks (its text and what was
	taken from it), from the embeddings already written. Exact, not an index walk: one
	person's ideas are hundreds, not millions."""
	near = (
		await session.execute(
			_NEAR_IDEAS,
			{
				"user_id": capture.user_id,
				"capture_id": capture.id,
				"written_at": capture.created_at,
				"max_distance": IDEA_MAX_DISTANCE,
				"limit": IDEAS_SHOWN,
			},
		)
	).all()
	if not near:
		return []
	ids = [row[0] for row in near]
	rows = {
		d.id: (d, written)
		for d, written in (
			await session.execute(
				select(Derivation, _written_at())
				.outerjoin(
					Capture,
					and_(
						Capture.id == Derivation.capture_id,
						Capture.user_id == Derivation.user_id,
					),
				)
				.where(Derivation.user_id == capture.user_id, Derivation.id.in_(ids))
			)
		).all()
	}
	out = []
	for i, idea_id in enumerate(ids, start=1):
		d, written = rows[idea_id]
		out.append(
			Candidate(
				Listed(
					label=f"I{i}",
					title=str(d.payload.get("title", "")),
					detail=str(d.payload.get("summary") or "") or None,
					written_on=written.date(),
				),
				d,
			)
		)
	return out


async def candidates(
	session: AsyncSession, capture: Capture, user: User | None
) -> list[Candidate]:
	found: list[Candidate] = []
	if faith_on(user) and might_answer(capture.text):
		found += await _open_prayers(session, capture)
	found += await _near_ideas(session, capture)
	return found


# --- suggestions ----------------------------------------------------------------------


async def apply_changes(
	session: AsyncSession,
	capture: Capture,
	result: Reconciliation,
	listed: Sequence[Candidate],
	*,
	version: str,
) -> list[Derivation]:
	"""Record what the model found as suggestions. Idempotent by replacement: a re-run
	of this version drops only suggestions nobody has answered, and never suggests again
	what the author already said yes or no to for this note."""
	await session.execute(
		delete(Derivation).where(
			Derivation.capture_id == capture.id,
			Derivation.user_id == capture.user_id,
			Derivation.kind == UPDATE,
			Derivation.extractor_version == version,
			Derivation.status.in_(UNREVIEWED),
		)
	)
	decided = set(
		(
			await session.scalars(
				select(Derivation.target_id).where(
					Derivation.capture_id == capture.id,
					Derivation.user_id == capture.user_id,
					Derivation.kind == UPDATE,
				)
			)
		).all()
	)
	by_label = {c.listed.label: c for c in listed}
	made = []
	for change in result.changes:
		candidate = by_label.get(change.candidate)
		if candidate is None or candidate.target.id in decided:
			continue
		update = Derivation(
			id=uuid7(),
			user_id=capture.user_id,
			capture_id=capture.id,
			kind=UPDATE,
			target_id=candidate.target.id,
			payload={
				"relation": change.relation,
				"quote": change.quote.strip().strip("\"'"),
				"target_kind": candidate.target.kind,
			},
			# Never kept without asking, auto-accept or not (ADR 0030): this changes
			# something the author already has.
			status="pending_review",
			extractor_version=version,
			confidence=change.confidence,
		)
		session.add(update)
		made.append(update)
	return made


async def decide(session: AsyncSession, update: Derivation, yes: bool) -> None:
	"""Yes or no. A yes to "answers" marks the prayer answered, on the day the note was
	written, with the note's words as how (unless it already says how). A yes to
	"continues" changes nothing on the idea: the thread is read from the updates."""
	now = dt.datetime.now(dt.UTC)
	update.status = "confirmed" if yes else "rejected"
	update.reviewed_at = now
	if not yes or update.payload.get("relation") != "answers":
		return
	prayer = await session.scalar(
		select(Derivation).where(
			Derivation.id == update.target_id,
			Derivation.user_id == update.user_id,
			Derivation.kind == PRAYER,
		)
	)
	if prayer is None or prayer.completed_at is not None:
		return  # gone, or already marked answered by hand: that answer stands
	note = await session.get(Capture, update.capture_id) if update.capture_id else None
	prayer.completed_at = note.created_at if note is not None else update.created_at
	if not prayer.payload.get("answer"):
		prayer.payload = {**prayer.payload, "answer": update.payload.get("quote")}


# --- reading them back ----------------------------------------------------------------


async def updates_out(
	session: AsyncSession,
	user_id: uuid.UUID,
	*,
	status: str | None = None,
	target_kind: str | None = None,
	limit: int = 200,
) -> list[UpdateOut]:
	"""Suggestions and the ones said yes to, newest first. A pending "answers" for a
	prayer that has since been marked answered is left out: the question is settled."""
	target = aliased(Derivation)
	query = (
		select(Derivation, target, Capture.created_at)
		.join(
			target,
			and_(
				target.id == Derivation.target_id, target.user_id == Derivation.user_id
			),
		)
		.outerjoin(
			Capture,
			and_(
				Capture.id == Derivation.capture_id,
				Capture.user_id == Derivation.user_id,
			),
		)
		.where(
			Derivation.user_id == user_id,
			Derivation.kind == UPDATE,
			target.status != "rejected",
		)
		.order_by(Derivation.created_at.desc())
		.limit(limit)
	)
	if status == "pending":
		query = query.where(Derivation.status.in_(UNREVIEWED))
	elif status is not None:
		query = query.where(Derivation.status == status)
	if target_kind is not None:
		query = query.where(target.kind == target_kind)
	rows = (await session.execute(query)).all()
	names = await names_for(session, user_id, (t.person_id for _, t, _ in rows))
	out = []
	for update, item, written in rows:
		if (
			update.status in UNREVIEWED
			and item.kind == PRAYER
			and item.completed_at is not None
		):
			continue
		out.append(
			UpdateOut(
				id=update.id,
				relation=update.payload.get("relation", "continues"),
				quote=str(update.payload.get("quote", "")),
				status=update.status,
				capture_id=update.capture_id,
				written_at=written or update.created_at,
				target=UpdateTarget(
					id=item.id,
					kind=item.kind,
					title=str(item.payload.get("title", "")),
					summary=item.payload.get("summary"),
					person_name=names.get(item.person_id) if item.person_id else None,
					answered_at=item.completed_at,
				),
			)
		)
	return out
