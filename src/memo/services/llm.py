"""Per-call LLM accounting: cost is a first-class metric, not a monthly surprise."""

from dataclasses import dataclass
from decimal import Decimal

import structlog

log = structlog.get_logger("memo.llm")

# USD per million tokens (input, output), first-party API, verified 2026-09-27
# (ADR 0014). Cache reads bill at 0.1x input, cache writes at 1.25x input.
PRICES_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
	"claude-opus-5": (Decimal("5"), Decimal("25")),
	"claude-sonnet-5": (Decimal("2"), Decimal("10")),
	"claude-haiku-4-5": (Decimal("1"), Decimal("5")),
}
CACHE_READ_MULTIPLIER = Decimal("0.1")
CACHE_WRITE_MULTIPLIER = Decimal("1.25")
_MILLION = Decimal(1_000_000)


def cost_usd(
	model: str,
	*,
	input_tokens: int,
	output_tokens: int,
	cache_read_tokens: int = 0,
	cache_write_tokens: int = 0,
) -> Decimal:
	prices = PRICES_PER_MTOK.get(model)
	if prices is None:
		log.warning("llm.unknown_model_price", model=model)
		return Decimal("0")
	in_price, out_price = prices
	total = (
		Decimal(input_tokens) * in_price
		+ Decimal(cache_read_tokens) * in_price * CACHE_READ_MULTIPLIER
		+ Decimal(cache_write_tokens) * in_price * CACHE_WRITE_MULTIPLIER
		+ Decimal(output_tokens) * out_price
	) / _MILLION
	return total.quantize(Decimal("0.000001"))


@dataclass(frozen=True)
class LLMCall:
	provider: str
	model: str
	stage: str
	input_tokens: int
	output_tokens: int
	cache_read_tokens: int
	cache_write_tokens: int
	latency_ms: int
	cost_usd: Decimal

	@classmethod
	def priced(
		cls,
		*,
		provider: str,
		model: str,
		stage: str,
		input_tokens: int,
		output_tokens: int,
		cache_read_tokens: int = 0,
		cache_write_tokens: int = 0,
		latency_ms: int,
	) -> "LLMCall":
		call = cls(
			provider=provider,
			model=model,
			stage=stage,
			input_tokens=input_tokens,
			output_tokens=output_tokens,
			cache_read_tokens=cache_read_tokens,
			cache_write_tokens=cache_write_tokens,
			latency_ms=latency_ms,
			cost_usd=cost_usd(
				model,
				input_tokens=input_tokens,
				output_tokens=output_tokens,
				cache_read_tokens=cache_read_tokens,
				cache_write_tokens=cache_write_tokens,
			),
		)
		log.info(
			"llm.call",
			provider=provider,
			model=model,
			stage=stage,
			input_tokens=input_tokens,
			output_tokens=output_tokens,
			cache_read_tokens=cache_read_tokens,
			cache_write_tokens=cache_write_tokens,
			latency_ms=latency_ms,
			cost_usd=str(call.cost_usd),
		)
		return call
