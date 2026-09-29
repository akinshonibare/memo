from typing import Any

from evals.extraction.run import load_golden
from evals.extraction.scoring import Report, score_example, titles_match
from memo.schemas.derivations import Extraction


def test_title_matching_survives_rephrasing_but_not_different_tasks() -> None:
	assert titles_match("Call Tunde about the pricing thing", "call Tunde re pricing")
	assert titles_match("Buy milk", "buy some milk")
	assert not titles_match("Call Tunde", "Call the dentist")
	assert not titles_match("Buy milk", "Return the library books")
	# Word forms are one word (stemming); the golden set's g28 needed this.
	assert titles_match(
		"RRF beats weighted fusion because it never normalises scores",
		"Why RRF beats weighted score fusion",
	)


def test_scores_count_hits_misses_and_inventions_per_kind() -> None:
	expected = Extraction.model_validate(
		{
			"todos": [{"title": "Buy milk"}, {"title": "Email Sarah the invoice"}],
			"events": [{"title": "Gym", "starts_at": "2026-09-29T07:00:00Z"}],
		}
	)
	predicted = Extraction.model_validate(
		{
			"todos": [{"title": "buy milk"}, {"title": "Call the dentist"}],
			"events": [],
			"ideas": [{"title": "made up", "summary": "x"}],
		}
	)
	scores = score_example(expected, predicted)
	assert (scores["todo"].tp, scores["todo"].fp, scores["todo"].fn) == (1, 1, 1)
	assert (scores["event"].tp, scores["event"].fn) == (0, 1)
	assert scores["idea"].fp == 1


def test_report_flags_threshold_failures() -> None:
	report = Report()
	perfect = Extraction.model_validate({"todos": [{"title": "Buy milk"}]})
	report.add("a", score_example(perfect, perfect))
	assert report.failures() == []

	missed = Extraction()
	report.add("b", score_example(perfect, missed))
	assert any("todo recall" in f for f in report.failures())
	assert report.misses == ["b: todo fn=1 fp=0"]


def test_golden_set_loads_and_has_enough_hard_cases() -> None:
	examples = load_golden()
	assert len(examples) >= 40
	assert len({e.id for e in examples}) == len(examples)
	empties = [e for e in examples if not e.expected.items()]
	assert len(empties) >= 6, "the set must include captures with nothing to extract"


def test_recorded_pairs_score_paraphrases_the_lexical_matcher_misses() -> None:
	expected = Extraction.model_validate(
		{"todos": [{"title": "Get the eval set done"}]}
	)
	predicted = Extraction.model_validate({"todos": [{"title": "Finish the eval set"}]})
	lexical = score_example(expected, predicted)["todo"]
	semantic = score_example(expected, predicted, {"todo": [(0, 0)]})["todo"]
	assert (lexical.tp, lexical.fp, lexical.fn) == (0, 1, 1)
	assert (semantic.tp, semantic.fp, semantic.fn) == (1, 0, 0)


def test_a_model_verdict_cannot_inflate_the_score() -> None:
	from evals.extraction.matching import _Pair, _valid

	pairs = [_Pair(expected=0, predicted=0), _Pair(expected=0, predicted=1)]
	pairs += [_Pair(expected=5, predicted=0), _Pair(expected=1, predicted=0)]
	assert _valid(pairs, n_expected=2, n_predicted=2) == [(0, 0)]


def test_a_stale_verdict_is_not_applied() -> None:
	from evals.extraction.matching import fingerprint

	expected = Extraction.model_validate({"todos": [{"title": "Buy milk"}]})
	before = Extraction.model_validate({"todos": [{"title": "buy milk"}]})
	after = Extraction.model_validate({"todos": [{"title": "Call the dentist"}]})
	assert fingerprint(expected, before) != fingerprint(expected, after)


def test_harvest_labels_only_finished_reviews() -> None:
	from evals.extraction.harvest import candidate

	def capture(*derivations: dict[str, Any]) -> dict[str, Any]:
		return {
			"id": "01a0e9eb-d082-7de2-ab24-bc0055bd3844",
			"text": "call Tunde, and the dentist moved",
			"status": "ready",
			"created_at": "2026-09-28T07:40:00+01:00",
			"derivations": list(derivations),
		}

	confirmed = {
		"kind": "todo",
		"status": "confirmed",
		"payload": {"title": "Call Tunde"},
	}
	added = {
		"kind": "event",
		"status": "user_created",
		"payload": {"title": "Dentist", "starts_at": "2026-10-02T14:30:00+01:00"},
	}
	rejected = {
		"kind": "idea",
		"status": "rejected",
		"payload": {"title": "x", "summary": "y"},
	}
	pending = {"kind": "todo", "status": "pending_review", "payload": {"title": "z"}}

	case = candidate(capture(confirmed, added, rejected), "Europe/London")
	assert case is not None
	assert case["expected"] == {
		"todos": [{"title": "Call Tunde"}],
		"events": [{"title": "Dentist", "starts_at": "2026-10-02T14:30:00+01:00"}],
	}
	assert candidate(capture(confirmed, pending), "UTC") is None, "review unfinished"
	assert candidate(capture(), "UTC") is None, "nothing reviewed"
	nothing = candidate(capture(rejected), "UTC")
	assert nothing is not None and nothing["expected"] == {}


def test_habits_scoring_counts_days_and_pairs_each_day_with_its_own() -> None:
	import datetime as dt

	from evals.habits.run import load_golden as load_habits
	from evals.habits.scoring import Expected
	from evals.habits.scoring import score_example as score_habits
	from memo.schemas.habits import ActivityFound, HabitsExtraction

	def did(*found: tuple[str, str]) -> HabitsExtraction:
		return HabitsExtraction(
			activities=[
				ActivityFound(key=k, label=k, occurred_on=dt.date.fromisoformat(on))
				for k, on in found
			]
		)

	week = [
		Expected.model_validate({"key": "gym", "on": d})
		for d in ("2026-09-21", "2026-09-23")
	]
	# The same day twice is one day, as the app counts it; order doesn't matter.
	right = score_habits(
		week, did(("gym", "2026-09-23"), ("gym", "2026-09-21"), ("gym", "2026-09-21"))
	)
	assert (right.tp, right.fp, right.fn, right.dates_right) == (2, 0, 0, 2)
	off = score_habits(week[:1], did(("gym", "2026-09-22")))
	assert (off.tp, off.dates_right) == (1, 0), "right activity, wrong day"
	plan = score_habits([], did(("gym", "2026-09-28")))
	assert plan.fp == 1
	either = Expected.model_validate(
		{"key": "sport", "on": "2026-09-26", "or": ["football"]}
	)
	assert score_habits([either], did(("football", "2026-09-26"))).tp == 1
	golden = load_habits()
	assert len(golden) >= 30
	assert sum(1 for e in golden if not e.expected) >= 10, "plenty of traps"
