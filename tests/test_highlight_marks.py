"""Saying "keep this" marks a passage, deterministically (ADR 0068)."""

import pytest

from memo.services.highlights import marked_passages, overlap, title_for


@pytest.mark.parametrize(
	("text", "expected"),
	[
		# The trigger goes, the thought stays, whichever end it sat at.
		(
			"note to self: the owner role bypasses RLS, so tests "
			"can pass for the wrong reason",
			["The owner role bypasses RLS, so tests can pass for the wrong reason"],
		),
		(
			"people remember how you made them feel, remember this",
			["People remember how you made them feel"],
		),
		(
			"highlight: a journal that reads itself back to you",
			["A journal that reads itself back to you"],
		),
		# "idea" the way people say it, with the filler people say before it.
		(
			"ok random idea, what if the morning view read the top highlight out loud",
			["What if the morning view read the top highlight out loud"],
		),
		(
			"I have an idea for the app: hold to record should show "
			"the transcript live",
			["Hold to record should show the transcript live"],
		),
		(
			"idea: ration resurfacing to one old thought a morning",
			["Ration resurfacing to one old thought a morning"],
		),
		(
			"no todos here just want to remember how good the jollof at Chuku's was",
			["No todos here just how good the jollof at Chuku's was"],
		),
		# Tags mark a short note whole, and a sentence of a long one.
		(
			"#highlight the best review comment I got this year was "
			"about naming not logic",
			["The best review comment I got this year was about naming not logic"],
		),
		# A trigger alone takes the next sentence.
		(
			"Note to self. Buy the domain before someone else does.",
			["Buy the domain before someone else does"],
		),
		# Voice: no punctuation at all, so the passage is the rest of the breath.
		(
			"so yeah note to self the reason the demo broke was the "
			"stale cache not the migration",
			["The reason the demo broke was the stale cache not the migration"],
		),
		# Two marks, two passages, in order.
		(
			"Insight: I only write when I'm anxious. Also, remember "
			"this: Ife starts school in September.",
			["I only write when I'm anxious", "Ife starts school in September"],
		),
	],
)
def test_marks_name_the_passage_without_the_trigger(
	text: str, expected: list[str]
) -> None:
	assert marked_passages(text) == expected


@pytest.mark.parametrize(
	"text",
	[
		"",
		"had a long day, back to back meetings, finally finished the deck",
		"I have no idea when the parcel is coming, maybe tomorrow",
		"Kemi said my pitch needs a clearer first slide, good "
		"idea, will fix it tomorrow",
		"any idea what time the call is",
		"the ideal weather for a run",  # not the word
		"highlights of the trip were the food",  # plural: a noun, not an instruction
		"Note to self.",  # nothing after it to keep
	],
)
def test_ordinary_sentences_mark_nothing(text: str) -> None:
	assert marked_passages(text) == []


def test_a_long_tagged_note_marks_only_its_sentence() -> None:
	long = (
		"The call ran long. " * 20
		+ "#highlight the real blocker is the data model. Then dinner."
	)
	assert marked_passages(long) == ["The real blocker is the data model"]


def test_marks_are_capped_and_clipped() -> None:
	text = ". ".join(
		f"remember this: thought number {i} is worth keeping" for i in range(8)
	)
	passages = marked_passages(text)
	assert len(passages) == 5
	huge = "note to self: " + "word " * 120
	assert len(marked_passages(huge)[0]) <= 300


def test_title_is_the_first_words() -> None:
	assert (
		title_for("Buy the domain before someone else does.")
		== "Buy the domain before someone else does"
	)
	assert len(title_for("a " * 100)) <= 80
	assert title_for("First thought. Second thought.") == "First thought"


def test_overlap_pairs_a_mark_with_the_idea_written_from_it() -> None:
	mark = "the owner role bypasses RLS so tests pass for the wrong reason"
	assert (
		overlap(
			mark, "Table owner bypasses RLS, so tests can pass for the wrong reason"
		)
		> 0.6
	)
	assert overlap(mark, "Resurface one old idea each morning") < 0.2
	assert overlap("", "anything") == 0.0
