"""The time a question is about (ADR 0045): "what's on this week?" names no thing a
retriever can match, only a range of days. This reads that range from a closed set of
phrases, deterministically, with no model call on the time-to-first-token budget.

A phrase outside the set gives no window, which is exactly Ask's behaviour before this
existed - never worse. `looks_temporal` lets the caller log those misses, which is the
trigger for replacing this with a model-parsed filter.
"""

import datetime as dt
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

_WEEKDAYS = {
	name: i
	for i, name in enumerate(
		["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
	)
}
# Short forms people type. Not "sat" or "sun": those are ordinary words.
_WEEKDAYS |= {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3}
_WEEKDAYS |= {"thurs": 3, "fri": 4}

_MONTHS = {
	name: i
	for i, name in enumerate(
		[
			"january",
			"february",
			"march",
			"april",
			"may",
			"june",
			"july",
			"august",
			"september",
			"october",
			"november",
			"december",
		],
		start=1,
	)
}
_MONTH_RE = "|".join(
	sorted([*_MONTHS, *(m[:3] for m in _MONTHS if m != "may"), "sept"], key=len)[::-1]
)
_DAY_RE = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))

# Past tense points a bare "Friday" or "March" backwards: "what did I do on Friday"
# is the last one, "what's on Friday" the next.
_PAST = re.compile(
	# Past-tense verbs, not "have I": "what have I got on in November" is the future.
	r"\b(did|was|were|went|had|happened|wrote|written|said|spent|been|ago|done|"
	r"saw|seen|met|talked|spoke|called|felt|how was)\b",
	re.IGNORECASE,
)
# Words that suggest a time the parser didn't catch; only for logging misses.
_TEMPORAL = re.compile(
	rf"\b(today|tonight|tomorrow|yesterday|week|weekend|month|year|day|days|"
	rf"morning|evening|recently|lately|ago|since|{_DAY_RE}|{_MONTH_RE})\b",
	re.IGNORECASE,
)


@dataclass(frozen=True)
class Window:
	"""Whole local days, both ends inclusive."""

	start: dt.date
	end: dt.date
	phrase: str

	def bounds(self, zone: ZoneInfo) -> tuple[dt.datetime, dt.datetime]:
		"""[start of the first day, start of the day after the last) as instants."""
		first = dt.datetime.combine(self.start, dt.time(), zone)
		after = dt.datetime.combine(self.end + dt.timedelta(days=1), dt.time(), zone)
		return first.astimezone(dt.UTC), after.astimezone(dt.UTC)

	def contains(self, day: dt.date) -> bool:
		return self.start <= day <= self.end

	def describe(self) -> str:
		if self.start == self.end:
			return self.start.strftime("%A %-d %B %Y")
		return (
			f"{self.start.strftime('%A %-d %B %Y')} to "
			f"{self.end.strftime('%A %-d %B %Y')}"
		)


def _monday(day: dt.date) -> dt.date:
	return day - dt.timedelta(days=day.weekday())


def _month(year: int, month: int) -> tuple[dt.date, dt.date]:
	first = dt.date(year, month, 1)
	nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
	return first, nxt - dt.timedelta(days=1)


def _shift_month(today: dt.date, by: int) -> tuple[int, int]:
	index = today.year * 12 + today.month - 1 + by
	return index // 12, index % 12 + 1


def _weekday(today: dt.date, target: int, *, which: str, past: bool) -> dt.date:
	ahead = (target - today.weekday()) % 7
	if which == "this":
		return _monday(today) + dt.timedelta(days=target)
	if which == "next":
		return today + dt.timedelta(days=ahead or 7)
	if which == "last":
		return today - dt.timedelta(days=(today.weekday() - target) % 7 or 7)
	# A bare "Friday": today if it is Friday, else the next one, or the last in the
	# past tense.
	if past:
		return today - dt.timedelta(days=(today.weekday() - target) % 7)
	return today + dt.timedelta(days=ahead)


def _weekend(today: dt.date, *, which: str, past: bool) -> tuple[dt.date, dt.date]:
	saturday = _monday(today) + dt.timedelta(days=5)
	if which == "next":
		saturday += dt.timedelta(days=7)
	elif which == "last" or (which == "the" and past and today < saturday):
		# On a weekend, "last weekend" is the one before this.
		saturday -= dt.timedelta(days=7)
	return saturday, saturday + dt.timedelta(days=1)


def _nearest_year(today: dt.date, month: int, day: int | None, *, past: bool) -> int:
	"""A date or month without a year is the nearest one in the direction of the tense:
	"in March" asked in September is this March if past, next March if not."""
	if day is None:
		first, last = _month(today.year, month)
		if first <= today <= last:
			return today.year
		if past:
			return today.year if last < today else today.year - 1
		return today.year if first > today else today.year + 1
	try:
		candidate = dt.date(today.year, month, day)
	except ValueError:
		return today.year  # 30 February: the caller's own date() rejects it
	if past:
		return today.year if candidate <= today else today.year - 1
	return today.year if candidate >= today else today.year + 1


_COUNTS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


_ORDINAL = r"(\d{1,2})(?:st|nd|rd|th)?"
_RULES: list[tuple[re.Pattern[str], str]] = [
	(re.compile(r"\bday after tomorrow\b"), "after_tomorrow"),
	(re.compile(r"\bday before yesterday\b"), "before_yesterday"),
	(re.compile(r"\b(today|tonight|this (?:morning|afternoon|evening))\b"), "today"),
	(re.compile(r"\btomorrow\b"), "tomorrow"),
	(re.compile(r"\byesterday\b"), "yesterday"),
	(re.compile(r"\b(?:the )?(past|last) (\d{1,3}) days\b"), "past_days"),
	(
		re.compile(
			r"\b(\d{1,3}|a|an|one|two|three|four|five) (day|week|month)s? ago\b"
		),
		"ago",
	),
	(re.compile(r"\b(?:the )?past (week|month)\b"), "past_span"),
	(re.compile(r"\b(this past|past|this|next|last|the) weekend\b"), "weekend"),
	(re.compile(r"\b(this|next|last) week\b"), "week"),
	(re.compile(r"\b(this|next|last) month\b"), "month"),
	(
		re.compile(rf"\b(?:the )?{_ORDINAL} (?:of )?({_MONTH_RE})\b(?: (\d{{4}}))?"),
		"day_month",
	),
	(
		re.compile(rf"\b({_MONTH_RE}) {_ORDINAL}\b(?:,? (\d{{4}}))?"),
		"month_day",
	),
	(re.compile(rf"\b(this|next|last|on)? ?({_DAY_RE})\b"), "weekday"),
	(re.compile(rf"\bin ({_MONTH_RE})\b(?: (\d{{4}}))?"), "in_month"),
]


def _month_number(word: str) -> int:
	word = word.lower()
	if word == "sept":
		return 9
	return next(n for name, n in _MONTHS.items() if name.startswith(word))


def _match(kind: str, m: re.Match[str], today: dt.date, past: bool) -> Window | None:
	phrase = m.group(0).strip()
	one = dt.timedelta(days=1)
	if kind == "today":
		return Window(today, today, phrase)
	if kind == "tomorrow":
		return Window(today + one, today + one, phrase)
	if kind == "yesterday":
		return Window(today - one, today - one, phrase)
	if kind == "after_tomorrow":
		return Window(today + 2 * one, today + 2 * one, phrase)
	if kind == "before_yesterday":
		return Window(today - 2 * one, today - 2 * one, phrase)
	if kind == "past_days":
		n = max(1, min(int(m.group(2)), 366))
		return Window(today - (n - 1) * one, today, phrase)
	if kind == "ago":
		n = _COUNTS.get(m.group(1)) or int(m.group(1))
		unit = m.group(2)
		if unit == "day":
			back = today - n * one
			return Window(back, back, phrase)
		if unit == "week":
			monday = _monday(today - 7 * n * one)
			return Window(monday, monday + 6 * one, phrase)
		return Window(*_month(*_shift_month(today, -n)), phrase)
	if kind == "past_span":
		span = 6 if m.group(1) == "week" else 29
		return Window(today - span * one, today, phrase)
	if kind == "weekend":
		which = {"this past": "last", "past": "last"}.get(m.group(1), m.group(1))
		sat, sun = _weekend(today, which=which, past=past)
		return Window(sat, sun, phrase)
	if kind == "week":
		monday = _monday(today) + {"this": 0, "next": 7, "last": -7}[m.group(1)] * one
		return Window(monday, monday + 6 * one, phrase)
	if kind == "month":
		by = {"this": 0, "next": 1, "last": -1}[m.group(1)]
		year, month = _shift_month(today, by)
		return Window(*_month(year, month), phrase)
	if kind in ("day_month", "month_day"):
		if kind == "day_month":
			day, month_word, year_text = int(m.group(1)), m.group(2), m.group(3)
		else:
			month_word, day, year_text = m.group(1), int(m.group(2)), m.group(3)
		month = _month_number(month_word)
		year = (
			int(year_text) if year_text else _nearest_year(today, month, day, past=past)
		)
		try:
			date = dt.date(year, month, day)
		except ValueError:
			return None
		return Window(date, date, phrase)
	if kind == "weekday":
		which = m.group(1) or ""
		date = _weekday(today, _WEEKDAYS[m.group(2)], which=which, past=past)
		return Window(date, date, phrase)
	if kind == "in_month":
		month = _month_number(m.group(1))
		year = (
			int(m.group(2))
			if m.group(2)
			else _nearest_year(today, month, None, past=past)
		)
		return Window(*_month(year, month), phrase)
	return None


def parse(question: str, today: dt.date) -> Window | None:
	"""The days `question` is about, or None. Several phrases ("between Monday and
	Friday") give the span that covers them all."""
	lowered = question.lower()
	past = bool(_PAST.search(lowered))
	found: list[Window] = []
	taken: list[tuple[int, int]] = []
	for pattern, kind in _RULES:
		for m in pattern.finditer(lowered):
			# The first rule to claim some words wins them: "the 6th of October" is a
			# date, not also a month; "next week" is not also "week".
			if any(m.start() < end and start < m.end() for start, end in taken):
				continue
			window = _match(kind, m, today, past)
			if window is not None:
				found.append(window)
				taken.append((m.start(), m.end()))
	if not found:
		return None
	found.sort(key=lambda w: w.start)
	phrase = " … ".join(w.phrase for w in found)
	return Window(min(w.start for w in found), max(w.end for w in found), phrase)


def temporal_word(question: str) -> str | None:
	"""A word suggesting a time, for logging questions the parser gave no window.
	The word alone is logged, never the question: that is a fragment of someone's
	notes."""
	m = _TEMPORAL.search(question)
	return m.group(0).lower() if m else None
