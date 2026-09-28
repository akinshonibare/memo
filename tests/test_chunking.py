from memo.services.chunking import (
	OVERLAP_TOKENS,
	TARGET_TOKENS,
	chunk_text,
	estimate_tokens,
)


def test_a_short_capture_is_exactly_one_chunk() -> None:
	source = "  need to call Tunde about the pricing thing before Thursday  "
	chunks = chunk_text(source)
	assert len(chunks) == 1
	assert chunks[0].text == source.strip()
	assert source[chunks[0].char_start : chunks[0].char_end] == chunks[0].text


def test_empty_text_yields_nothing() -> None:
	assert chunk_text("   \n\n ") == []


def _long_source(paragraphs: int = 12, sentences: int = 6) -> str:
	return "\n\n".join(
		" ".join(
			f"Paragraph {p} sentence {s} says something fairly long about topic {p}."
			for s in range(sentences)
		)
		for p in range(paragraphs)
	)


def test_long_text_splits_on_paragraphs_with_overlap_and_true_offsets() -> None:
	source = _long_source()
	chunks = chunk_text(source)

	assert len(chunks) > 1
	for chunk in chunks:
		assert source[chunk.char_start : chunk.char_end] == chunk.text
		assert estimate_tokens(chunk.text) <= TARGET_TOKENS + OVERLAP_TOKENS
	assert [c.ordinal for c in chunks] == list(range(len(chunks)))
	# Consecutive chunks overlap: the second starts before the first ends.
	assert chunks[1].char_start < chunks[0].char_end
	# Coverage: the first starts at the first character, the last ends at the last.
	assert chunks[0].char_start == 0
	assert chunks[-1].char_end == len(source)


def test_a_paragraph_over_the_target_falls_back_to_sentences() -> None:
	source = " ".join(
		f"Sentence number {i} is here and it is long enough to matter."
		for i in range(80)
	)
	chunks = chunk_text(source)
	assert len(chunks) > 1
	for chunk in chunks:
		assert chunk.text.rstrip().endswith(".")  # sentence boundaries, not mid-word
		assert source[chunk.char_start : chunk.char_end] == chunk.text


def test_a_tiny_tail_is_merged_into_its_predecessor() -> None:
	big = " ".join(f"Word{i}" for i in range(1500))  # ~ 2 chunks' worth
	source = big + "\n\nTiny tail."
	chunks = chunk_text(source)
	assert chunks[-1].text.endswith("Tiny tail.")
	assert estimate_tokens(chunks[-1].text) > 50
