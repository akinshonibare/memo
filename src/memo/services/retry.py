"""Failure classification and backoff for the processing pipeline.

Every stage failure is one of two kinds, and the pipeline treats them differently:
a `RetryableError` is transient (the provider was busy, the network blinked) and is
worth another attempt after a delay; a `TerminalError` is deterministic (the input is
bad, the request is malformed) and retrying it only spends money reaching the same
result. Anything else is a bug and propagates.
"""

import random


class RetryableError(Exception):
	"""Transient: 429, 5xx, timeouts, connection errors."""


class TerminalError(Exception):
	"""Deterministic: the same input will fail the same way."""


def backoff_delay(attempt: int, *, base: float = 2.0, cap: float = 300.0) -> float:
	"""Seconds to wait before retry number `attempt` (1-based), with full jitter.

	Without jitter, every worker that failed on the same provider blip retries at the
	same instant and recreates the blip. uniform(0, exponential cap) spreads them out.
	"""
	if attempt < 1:
		raise ValueError("attempt is 1-based")
	ceiling = min(cap, base * 2 ** (attempt - 1))
	return random.uniform(0, ceiling)


def classify_status(status_code: int, detail: str = "") -> Exception:
	"""Map an HTTP status from a provider onto the two failure kinds."""
	message = f"HTTP {status_code}{': ' + detail if detail else ''}"
	if status_code == 429 or status_code >= 500:
		return RetryableError(message)
	return TerminalError(message)
