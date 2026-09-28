"""The photo prompt (ADR 0024). A photo becomes text here, and everything downstream -
extraction, chunking, retrieval, Ask - works on that text exactly as it does on a
transcript. Versioned like the extractor: stamped on the job ledger, changed only with
a bump, so re-describing old photos is measurable."""

DESCRIBER_VERSION = "describe-v1"

SYSTEM_PROMPT = """\
You turn a photo from one person's private notes app into text. They took or saved it
to remember something; your text is all the rest of the app will ever see of it, both
for searching later and for pulling out todos, events and ideas.

Return two things:
- description: two to four plain sentences on what the photo shows and what about it is
  worth remembering - the thing, the place, the people or names visible, any date, time
  or price shown. Write it as a note to the person ("A flyer for...", "A whiteboard
  listing..."), not as commentary on the photo's quality.
- text_in_image: every piece of legible text, verbatim, one line per line of text, in
  reading order. Empty if there is none. Never guess at text you cannot read; leave it
  out instead.
"""


def note_text(description: str, text_in_image: str) -> str:
	"""What is stored as the capture's text: the same field a transcript fills."""
	body = f"Photo: {description.strip()}"
	if text_in_image.strip():
		body += f"\n\nText in the photo:\n{text_in_image.strip()}"
	return body
