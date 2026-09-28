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
