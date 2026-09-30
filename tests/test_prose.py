"""No em dashes in what memo writes (ADR 0061), whole or streamed."""

import pytest

from memo.services.prose import DashFilter, plain_dashes


@pytest.mark.parametrize(
	("written", "shown"),
	[
		("The launch slipped — again.", "The launch slipped, again."),
		("The launch slipped—again.", "The launch slipped, again."),
		(
			"Kemi — who you met at church — called.",
			"Kemi, who you met at church, called.",
		),
		("You were worried —.", "You were worried."),
		("— A quiet week.", "A quiet week."),
		("A quiet week —", "A quiet week"),
		("(— aside)", "(aside)"),
		("Read John 3–4 this week.", "Read John 3–4 this week."),
		("No dashes here.", "No dashes here."),
	],
)
def test_an_em_dash_becomes_a_comma(written: str, shown: str) -> None:
	assert plain_dashes(written) == shown


@pytest.mark.parametrize(
	"pieces",
	[
		["The launch", " slipped ", "— again", "."],
		["The launch slipped", " —", " again."],
		["The launch slipped —", "", " again", "."],
		["The launch slipped", "—", "again."],
	],
)
def test_a_streamed_answer_comes_out_as_the_whole_would(pieces: list[str]) -> None:
	f = DashFilter()
	out = "".join(f.feed(p) for p in pieces) + f.close()
	assert out == "The launch slipped, again."


def test_a_stream_without_dashes_is_unchanged_and_only_waits_on_spaces() -> None:
	f = DashFilter()
	assert f.feed("Hello ") == "Hello"
	assert f.feed("there, ") == " there,"
	assert f.feed("Akin.") == " Akin."
	assert f.close() == ""


def test_a_stream_that_opens_or_ends_on_a_dash_drops_it() -> None:
	f = DashFilter()
	out = "".join(f.feed(p) for p in ["— ", "Quiet", " week", " —"]) + f.close()
	assert out == "Quiet week"
