"""The days a question is about (ADR 0045): pure, so every phrase is a table row."""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from memo.services.timewindow import Window, parse, temporal_word

# Wednesday 30 September 2026.
TODAY = dt.date(2026, 9, 30)
D = dt.date


@pytest.mark.parametrize(
	("question", "start", "end"),
	[
		("what's on today?", D(2026, 9, 30), D(2026, 9, 30)),
		("anything tonight", D(2026, 9, 30), D(2026, 9, 30)),
		("what do I have tomorrow", D(2026, 10, 1), D(2026, 10, 1)),
		("what did I do yesterday?", D(2026, 9, 29), D(2026, 9, 29)),
		("the day after tomorrow", D(2026, 10, 2), D(2026, 10, 2)),
		("what's on this week?", D(2026, 9, 28), D(2026, 10, 4)),
		("what's happening next week", D(2026, 10, 5), D(2026, 10, 11)),
		("what did I get done last week", D(2026, 9, 21), D(2026, 9, 27)),
		("plans for this weekend", D(2026, 10, 3), D(2026, 10, 4)),
		("what did I do last weekend", D(2026, 9, 26), D(2026, 9, 27)),
		("how was the weekend", D(2026, 9, 26), D(2026, 9, 27)),
		("what's on the weekend", D(2026, 10, 3), D(2026, 10, 4)),
		("next weekend", D(2026, 10, 10), D(2026, 10, 11)),
		("anything this month", D(2026, 9, 1), D(2026, 9, 30)),
		("what did I write last month", D(2026, 8, 1), D(2026, 8, 31)),
		("next month", D(2026, 10, 1), D(2026, 10, 31)),
		("what's on Friday?", D(2026, 10, 2), D(2026, 10, 2)),
		("what did I do on Monday", D(2026, 9, 28), D(2026, 9, 28)),
		("last friday", D(2026, 9, 25), D(2026, 9, 25)),
		("next wednesday", D(2026, 10, 7), D(2026, 10, 7)),
		("this sunday", D(2026, 10, 4), D(2026, 10, 4)),
		("anything on wednesday", D(2026, 9, 30), D(2026, 9, 30)),
		("what's on the 6th of October", D(2026, 10, 6), D(2026, 10, 6)),
		("october 6", D(2026, 10, 6), D(2026, 10, 6)),
		("6 oct 2027", D(2027, 10, 6), D(2027, 10, 6)),
		("what did I do on 3 march", D(2026, 3, 3), D(2026, 3, 3)),
		("what's on 3 march", D(2027, 3, 3), D(2027, 3, 3)),
		("what was I thinking about in March", D(2026, 3, 1), D(2026, 3, 31)),
		("in december", D(2026, 12, 1), D(2026, 12, 31)),
		("in september", D(2026, 9, 1), D(2026, 9, 30)),
		("the past 10 days", D(2026, 9, 21), D(2026, 9, 30)),
		("last 3 days", D(2026, 9, 28), D(2026, 9, 30)),
		("what was I worried about 3 days ago", D(2026, 9, 27), D(2026, 9, 27)),
		("a week ago", D(2026, 9, 21), D(2026, 9, 27)),
		("two months ago", D(2026, 7, 1), D(2026, 7, 31)),
		("friday and saturday", D(2026, 10, 2), D(2026, 10, 3)),
	],
)
def test_phrases(question: str, start: dt.date, end: dt.date) -> None:
	window = parse(question, TODAY)
	assert window is not None, question
	assert (window.start, window.end) == (start, end), question


@pytest.mark.parametrize(
	"question",
	[
		"when did I last see Tunde?",
		"what did I need to talk to Tunde about",
		"may I ask about the pricing idea",
		"what's the plan for the march on parliament",
		"",
	],
)
def test_no_time_no_window(question: str) -> None:
	assert parse(question, TODAY) is None


def test_the_first_rule_to_claim_words_wins() -> None:
	""" "The 6th of October" is one date, not a date and the whole of October."""
	window = parse("what's on the 6th of October", TODAY)
	assert window is not None and window.start == window.end


def test_an_impossible_date_is_no_window() -> None:
	assert parse("what's on 31 February", TODAY) is None


def test_bounds_are_local_days_as_instants() -> None:
	london = ZoneInfo("Europe/London")
	start, after = Window(D(2026, 9, 28), D(2026, 10, 4), "this week").bounds(london)
	assert start == dt.datetime(2026, 9, 27, 23, 0, tzinfo=dt.UTC)
	assert after == dt.datetime(2026, 10, 4, 23, 0, tzinfo=dt.UTC)


def test_bounds_cross_a_clock_change() -> None:
	london = ZoneInfo("Europe/London")
	start, after = Window(D(2026, 10, 25), D(2026, 10, 25), "Sunday").bounds(london)
	assert after - start == dt.timedelta(hours=25)


def test_temporal_word_catches_what_the_parser_misses() -> None:
	assert parse("what have I been up to recently", TODAY) is None
	assert temporal_word("what have I been up to Recently") == "recently"
	assert temporal_word("what did I need to talk to Tunde about") is None
