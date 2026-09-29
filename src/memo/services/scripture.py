"""Bible references and the reading plan (ADR 0042).

Finding a reference is deterministic, not a model call. References have a regular
shape (a book, a chapter, maybe verses), `pythonbible` already parses and validates
them against real chapter and verse counts, and a model that is usually right about
"1 Cor 13:4" is still wrong often enough to matter. Two rules on top of the library:

- A reference must name a chapter. "Mark said we should meet" parses as the whole
  book of Mark; requiring a chapter is what tells the person from the book.
- Voice notes say "John chapter 3 verse 16". That is rewritten to "John 3:16" first,
  including small spelled-out numbers ("chapter twenty three").

memo never produces Scripture text. What it stores is a reference; reading happens in
the user's Bible app, in the translation they chose.
"""

import re
from dataclasses import dataclass
from functools import cache

import pythonbible as bible

# USFM book codes, the ones Bible apps and bible.com links use, in canonical order.
# pythonbible numbers the 66 books 1..66 in the same order.
USFM: tuple[str, ...] = (
	"GEN", "EXO", "LEV", "NUM", "DEU", "JOS", "JDG", "RUT", "1SA", "2SA",
	"1KI", "2KI", "1CH", "2CH", "EZR", "NEH", "EST", "JOB", "PSA", "PRO",
	"ECC", "SNG", "ISA", "JER", "LAM", "EZK", "DAN", "HOS", "JOL", "AMO",
	"OBA", "JON", "MIC", "NAM", "HAB", "ZEP", "HAG", "ZEC", "MAL",
	"MAT", "MRK", "LUK", "JHN", "ACT", "ROM", "1CO", "2CO", "GAL", "EPH",
	"PHP", "COL", "1TH", "2TH", "1TI", "2TI", "TIT", "PHM", "HEB", "JAS",
	"1PE", "2PE", "1JN", "2JN", "3JN", "JUD", "REV",
)  # fmt: skip
BOOKS: tuple[bible.Book, ...] = tuple(bible.Book(n) for n in range(1, 67))
assert len(USFM) == len(BOOKS) == 66


@dataclass(frozen=True)
class Passage:
	book: bible.Book
	chapter: int
	verse: int | None
	end_chapter: int
	end_verse: int | None

	@property
	def usfm(self) -> str:
		return USFM[int(self.book.value) - 1]

	@property
	def ref(self) -> str:
		return label(self)

	def payload(self) -> dict[str, object]:
		return {
			"ref": self.ref,
			"book": self.usfm,
			"chapter": self.chapter,
			"verse": self.verse,
			"end_chapter": self.end_chapter,
			"end_verse": self.end_verse,
		}


def _one_chapter(book: bible.Book) -> bool:
	return bool(bible.get_number_of_chapters(book) == 1)


def book_title(book: bible.Book, *, single_chapter: bool = False) -> str:
	# "Psalm 23", but "Psalms 1–41".
	if book == bible.Book.PSALMS and single_chapter:
		return "Psalm"
	return str(book.title)


def label(p: Passage) -> str:
	"""How a reference reads: "Romans 8:28–30", "Genesis 1–3", "Psalm 23", "Jude 5"."""
	one_chapter_book = _one_chapter(p.book)
	name = book_title(p.book, single_chapter=p.chapter == p.end_chapter)
	if p.verse is None:
		if one_chapter_book:
			return name
		if p.end_chapter == p.chapter:
			return f"{name} {p.chapter}"
		return f"{name} {p.chapter}–{p.end_chapter}"
	start = f"{p.verse}" if one_chapter_book else f"{p.chapter}:{p.verse}"
	if p.end_chapter == p.chapter:
		if p.end_verse is None or p.end_verse == p.verse:
			return f"{name} {start}"
		return f"{name} {start}–{p.end_verse}"
	return f"{name} {start}–{p.end_chapter}:{p.end_verse}"


# --- finding references in a note -----------------------------------------------------

_UNITS = {
	"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
	"eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
	"fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
	"nineteen": 19,
}  # fmt: skip
_TENS = {
	"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
	"seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
# Longest first, and each a whole word: otherwise "nine" wins inside "nineteen".
_NUMBER_WORD = "|".join(
	sorted([*_UNITS, *_TENS, "hundred", "and"], key=len, reverse=True)
)


def _words_to_int(phrase: str) -> int | None:
	"""'twenty three' -> 23, 'one hundred nineteen' -> 119; None if not a number."""
	total, seen = 0, False
	for word in re.split(r"[\s-]+", phrase.lower().strip()):
		if not word or word == "and":
			continue
		if word in _UNITS:
			total += _UNITS[word]
		elif word in _TENS:
			total += _TENS[word]
		elif word == "hundred":
			total = (total or 1) * 100
		else:
			return None
		seen = True
	return total if seen and total > 0 else None


def _spell_out_after(keyword: str, text: str) -> str:
	"""Numbers written as words, but only right after "chapter" or "verse(s)": elsewhere
	"John one of my friends" would become John 1."""

	def replace(match: re.Match[str]) -> str:
		words = match.group(2)
		value = _words_to_int(words)
		if value is None:
			return match.group(0)
		trailing = words[len(words.rstrip(" -\t")) :]  # keep the space before "verse"
		return f"{match.group(1)}{value}{trailing}"

	pattern = re.compile(
		rf"(\b{keyword}\s+)((?:(?:{_NUMBER_WORD})\b[\s-]*)+)", re.IGNORECASE
	)
	return pattern.sub(replace, text)


_VERSE_RANGE = re.compile(
	r"\bchapter\s+(\d+)[,\s]+verses?\s+(\d+)(?:\s*(?:-|to|through|thru)\s*(\d+))?",
	re.IGNORECASE,
)
_CHAPTER_ONLY = re.compile(r"\bchapter\s+(\d+)\b", re.IGNORECASE)
_DASH = re.compile(r"(?<=\d)\s*[–—]\s*(?=\d)")


def normalise(text: str) -> str:
	"""The way people say references, rewritten the way pythonbible reads them."""
	out = _DASH.sub("-", text)
	for keyword in ("chapter", "verses?"):
		out = _spell_out_after(keyword, out)
	out = _VERSE_RANGE.sub(
		lambda m: (
			f"{m.group(1)}:{m.group(2)}" + (f"-{m.group(3)}" if m.group(3) else "")
		),
		out,
	)
	return _CHAPTER_ONLY.sub(lambda m: m.group(1), out)


def find_passages(text: str) -> list[Passage]:
	"""Every distinct reference in `text` that names a chapter, in order found."""
	if not text or not text.strip():
		return []
	try:
		refs = bible.get_references(normalise(text))
	except Exception:  # noqa: BLE001 - a parser bug must never cost a note
		return []
	found: list[Passage] = []
	for r in refs:
		if r.book.value > 66 or r.start_chapter is None:
			continue  # the Apocrypha, or a bare book name ("Mark said...")
		if r.end_book is not None and r.end_book != r.book:
			continue  # a range across books: rare, and no single link can open it
		passage = Passage(
			book=r.book,
			chapter=r.start_chapter,
			verse=r.start_verse,
			end_chapter=r.end_chapter or r.start_chapter,
			end_verse=r.end_verse,
		)
		if passage not in found:
			found.append(passage)
	return found


# --- the reading plan -----------------------------------------------------------------

CANONICAL = "canonical"
PLAN_TITLES = {CANONICAL: "Genesis to Revelation"}
# 1,189 chapters over a year is about 3.26 a day.
DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class Reading:
	index: int
	book: bible.Book
	chapter: int
	end_chapter: int

	@property
	def usfm(self) -> str:
		return USFM[int(self.book.value) - 1]

	@property
	def label(self) -> str:
		return str(
			label(Passage(self.book, self.chapter, None, self.end_chapter, None))
		)


@cache
def plan(name: str = CANONICAL) -> tuple[Reading, ...]:
	"""Genesis to Revelation in about a year. A reading never crosses a book, so each
	opens with one link; each book is split into near-equal readings of 3-4 chapters."""
	if name != CANONICAL:
		raise KeyError(name)
	total = sum(bible.get_number_of_chapters(b) for b in BOOKS)
	per_day = total / DAYS_PER_YEAR
	readings: list[Reading] = []
	for book in BOOKS:
		chapters = int(bible.get_number_of_chapters(book))
		parts = max(1, round(chapters / per_day))
		start = 1
		for part in range(parts):
			size = chapters // parts + (1 if part < chapters % parts else 0)
			readings.append(Reading(len(readings), book, start, start + size - 1))
			start += size
	return tuple(readings)
