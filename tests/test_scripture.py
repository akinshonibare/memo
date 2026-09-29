"""Finding Bible references in notes, and the reading plan (ADR 0042). Pure functions:
no database, no model."""

import pytest

from memo.services.scripture import (
	find_passages,
	label,
	next_reading,
	parse_reading,
	passage_from_payload,
)


def refs(text: str) -> list[str]:
	return [p.ref for p in find_passages(text)]


@pytest.mark.parametrize(
	("text", "expected"),
	[
		("Reading Romans 8:28-30 this morning", ["Romans 8:28–30"]),
		("1 Cor 13:4–7, the love chapter", ["1 Corinthians 13:4–7"]),
		("First Corinthians 13", ["1 Corinthians 13"]),
		("Gen 1-3", ["Genesis 1–3"]),
		("Mark 4:1-5:2", ["Mark 4:1–5:2"]),
		("Jude 5", ["Jude 5"]),
		("psalm 23", ["Psalm 23"]),
		("1st John 4:8", ["1 John 4:8"]),
	],
)
def test_references_come_out_canonical(text: str, expected: list[str]) -> None:
	assert refs(text) == expected


def test_a_name_is_not_a_book() -> None:
	"""The collision ADR 0042 names: "Mark said" is a person, "Mark 4" is a book."""
	assert refs("Mark said we should meet on Thursday") == []
	assert refs("John one of my friends") == []
	assert refs("Philemon was a good read") == []  # a bare book name has no chapter
	assert refs("Read Mark 4 with John") == ["Mark 4"]


def test_the_way_references_are_said_out_loud() -> None:
	assert refs("John chapter 3 verse 16 hit me") == ["John 3:16"]
	assert refs("John chapter 3, verses 16 through 18") == ["John 3:16–18"]
	assert refs("Psalm chapter twenty three") == ["Psalm 23"]
	assert refs("Psalm chapter one hundred nineteen") == ["Psalm 119"]
	assert refs("Luke chapter fifteen verse eleven") == ["Luke 15:11"]
	assert refs("Matthew chapter seventeen") == ["Matthew 17"]


def test_references_that_dont_exist_or_arent_canon_are_ignored() -> None:
	assert refs("Romans 17:3") == []  # Romans has 16 chapters
	assert refs("Tobit 1:1") == []  # the Apocrypha
	assert refs("") == []
	assert refs("   ") == []


def test_a_reference_twice_is_one_passage() -> None:
	assert refs("Psalm 23, and again psalm 23 tonight") == ["Psalm 23"]


def test_several_references_in_order() -> None:
	assert refs("John 3:16 and Rom 8:28") == ["John 3:16", "Romans 8:28"]


def test_passage_payload_carries_the_usfm_code_for_links() -> None:
	(p,) = find_passages("Romans 8:28-30")
	assert p.payload() == {
		"ref": "Romans 8:28–30",
		"book": "ROM",
		"chapter": 8,
		"verse": 28,
		"end_chapter": 8,
		"end_verse": 30,
	}
	(q,) = find_passages("Song of Solomon 2:4")
	assert q.usfm == "SNG"


def test_labels_read_naturally() -> None:
	(single_psalm,) = find_passages("Psalm 23")
	assert label(single_psalm) == "Psalm 23"
	(psalms,) = find_passages("Psalm 1-3")
	assert label(psalms) == "Psalms 1–3"
	(obadiah,) = find_passages("Obadiah 1:3")
	assert label(obadiah) == "Obadiah 3"


def test_what_you_are_reading_can_be_a_whole_book() -> None:
	john = parse_reading("John")
	assert john is not None and label(john) == "John"
	assert (john.chapter, john.end_chapter) == (1, 21)
	psalms = parse_reading("psalms")
	assert psalms is not None and label(psalms) == "Psalms"
	romans = parse_reading("Romans 8")
	assert romans is not None and label(romans) == "Romans 8"
	assert parse_reading("hello there") is None
	assert parse_reading("   ") is None


def test_the_next_chapter_follows_what_you_last_read_in_it() -> None:
	john = parse_reading("John")
	assert label(next_reading(john, None)) == "John 1"  # type: ignore[arg-type]
	assert label(next_reading(john, parse_reading("John 3"))) == "John 4"  # type: ignore[arg-type]
	assert label(next_reading(john, parse_reading("John 3-5"))) == "John 6"  # type: ignore[arg-type]
	assert next_reading(john, parse_reading("John 21")) is None, "read to its end"
	# A chapter set is read once; a few verses set are read as they are.
	romans = parse_reading("Romans 8")
	assert label(next_reading(romans, None)) == "Romans 8"  # type: ignore[arg-type]
	assert next_reading(romans, parse_reading("Romans 8")) is None
	verses = parse_reading("Romans 8:28-39")
	assert next_reading(verses, parse_reading("Romans 8:28-39")) == verses
	assert next_reading(None, None) is None


def test_a_stored_passage_comes_back_the_same() -> None:
	for text in ("Romans 8:28-30", "John", "Jude 3", "Psalm 23"):
		p = parse_reading(text)
		assert p is not None and passage_from_payload(p.payload()) == p
	assert passage_from_payload({"book": "XYZ", "chapter": 1}) is None
