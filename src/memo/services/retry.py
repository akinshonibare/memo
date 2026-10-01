"""Failure classification and backoff for the processing pipeline.

Every stage failure is one of two kinds, and the pipeline treats them differently:
a `RetryableError` is transient (the provider was busy, the network blinked) and is
worth another attempt after a delay; a `TerminalError` is deterministic (the input is
bad, the request is malformed) and retrying it only spends money reaching the same
result. Anything else is a bug and propagates.

One transient kind is singled out: `ProviderUnavailable`, when the provider refuses
our *account* rather than this request (out of credit, key refused). It isn't the
note's fault and it isn't a blip: it lasts until the operator fixes it, which can be
hours. A note's attempts are sized for blips, so it is waited out without spending
them (ADR 0066).
"""

import random

import anthropic


class RetryableError(Exception):
	"""Transient: 429, 5xx, timeouts, connection errors."""


class TerminalError(Exception):
	"""Deterministic: the same input will fail the same way."""


class ProviderUnavailable(RetryableError):
	"""Our account, not this request: out of credit, or the key was refused. Every
	call fails the same way until the operator acts. A RetryableError, so anything
	that already treats those as "not an attempt, try later" stays right."""

	# What a person may see. The provider's own text (billing pages, key names) is
	# for the operator's logs only.
	for_users = (
		"memo's AI is unavailable right now. Your notes still save, and they'll be "
		"read once it's back."
	)


# Unauthorized, payment required, forbidden: about the account, never the input.
ACCOUNT_STATUSES = frozenset({401, 402, 403})


def backoff_delay(attempt: int, *, base: float = 2.0, cap: float = 300.0) -> float:
	"""Seconds to wait before retry number `attempt` (1-based), with full jitter.

	Without jitter, every worker that failed on the same provider blip retries at the
	same instant and recreates the blip. uniform(0, exponential cap) spreads them out.
	"""
	if attempt < 1:
		raise ValueError("attempt is 1-based")
	ceiling = min(cap, base * 2 ** (attempt - 1))
	return random.uniform(0, ceiling)


def provider_wait(tries: int, *, base: float = 60.0, cap: float = 3600.0) -> float:
	"""Seconds before a note waiting on an unavailable provider looks again: about a
	minute, doubling to an hour. Half fixed, half jittered: coming back in seconds
	would find nothing changed, and when the operator tops up, a queue of waiting
	notes shouldn't all arrive at once."""
	if tries < 1:
		raise ValueError("tries is 1-based")
	ceiling = min(cap, base * 2.0 ** (tries - 1))
	return ceiling / 2 + random.uniform(0, ceiling / 2)


def classify_status(status_code: int, detail: str = "") -> Exception:
	"""Map an HTTP status from a provider onto the failure kinds."""
	message = f"HTTP {status_code}{': ' + detail if detail else ''}"
	if status_code in ACCOUNT_STATUSES:
		return ProviderUnavailable(message)
	if status_code == 429 or status_code >= 500:
		return RetryableError(message)
	return TerminalError(message)


def classify_anthropic(exc: anthropic.APIError) -> Exception:
	"""The same mapping for the Claude SDK, which raises its own exception types.

	An empty credit balance comes back as a plain 400 invalid_request_error: the
	message is the only thing that tells it apart from a malformed request. If the
	wording ever changes, this falls back to terminal, which is the old behaviour,
	not a new failure."""
	if isinstance(exc, anthropic.RateLimitError):
		return RetryableError(f"anthropic 429: {exc.message}")
	if isinstance(exc, anthropic.APIStatusError):
		if exc.status_code in ACCOUNT_STATUSES or (
			exc.status_code == 400 and "credit balance" in exc.message.lower()
		):
			return ProviderUnavailable(f"anthropic {exc.status_code}: {exc.message}")
		if exc.status_code >= 500:
			return RetryableError(f"anthropic {exc.status_code}")
		return TerminalError(f"anthropic {exc.status_code}: {exc.message}")
	if isinstance(exc, anthropic.APIConnectionError):
		return RetryableError(f"anthropic unreachable: {exc!r}")
	return exc
