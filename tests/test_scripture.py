"""Finding Bible references in notes, and the reading plan (ADR 0042). Pure functions:
no database, no model."""

import pytest

from memo.services.scripture import find_passages, label, plan


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


def test_the_plan_reads_the_whole_bible_in_about_a_year() -> None:
	readings = plan()
	assert 350 <= len(readings) <= 380
	# Every chapter once, in order, and no reading crosses a book (one link opens it).
	chapters = [
		(r.book.value, c) for r in readings for c in range(r.chapter, r.end_chapter + 1)
	]
	assert len(chapters) == 1189
	assert chapters == sorted(chapters)
	assert len(set(chapters)) == 1189
	assert all(1 <= r.end_chapter - r.chapter + 1 <= 4 for r in readings)
	assert [r.index for r in readings] == list(range(len(readings)))
	assert readings[0].label == "Genesis 1–4"
	assert readings[-1].label.startswith("Revelation")


def test_labels_read_naturally() -> None:
	(single_psalm,) = find_passages("Psalm 23")
	assert label(single_psalm) == "Psalm 23"
	readings = plan()
	psalms = [r for r in readings if r.usfm == "PSA"]
	assert psalms[0].label.startswith("Psalms 1–")
	one_chapter = [r for r in readings if r.usfm == "OBA"]
	assert [r.label for r in one_chapter] == ["Obadiah"]
