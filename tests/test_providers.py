"""One model setting, two companies (ADR 0067): a model name picks its provider, the
factories build the matching client, and the privacy page says so."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from anthropic.types import MessageParam
from pydantic import SecretStr

from memo.config import Settings
from memo.pages import model_processors
from memo.services import providers
from memo.services.ask import (
	AnthropicAnswerProvider,
	AnthropicTextProvider,
	UnconfiguredProvider,
	make_providers,
)
from memo.services.extraction import (
	AnthropicExtractionProvider,
	RawCompletion,
	complete_validated,
	make_extractor,
)
from memo.services.faith import make_faith_reader
from memo.services.gemini import GeminiAnswerProvider, GeminiCompletionProvider
from memo.services.habits import make_habits_reader
from memo.services.lately import make_lately_writer
from memo.services.llm import cost_usd
from memo.services.mood import make_mood_reader
from memo.services.people import make_people_reader
from memo.services.reconcile import make_reconciler
from memo.services.vision import PhotoDescriber, make_describer

GEMINI = "gemini-3.8-flash"
CLAUDE = "claude-sonnet-5"

READERS: dict[str, Callable[[Settings], Any]] = {
	"extraction_model": make_extractor,
	"people_model": make_people_reader,
	"habits_model": make_habits_reader,
	"faith_model": make_faith_reader,
	"reconcile_model": make_reconciler,
	"mood_model": make_mood_reader,
	"lately_model": make_lately_writer,
}


def _with(settings: Settings, **update: Any) -> Settings:
	return settings.model_copy(update=update)


@pytest.mark.parametrize(
	("model", "name"),
	[
		("claude-opus-5", "anthropic"),
		("claude-haiku-4-5", "anthropic"),
		("gemini-3.8-flash", "google"),
		("gemma-4-31b-it", "google"),
	],
)
def test_a_model_name_picks_its_provider(model: str, name: str) -> None:
	assert providers.provider_for(model).name == name


def test_an_unknown_model_name_is_refused_loudly() -> None:
	with pytest.raises(ValueError, match="gpt-5"):
		providers.provider_for("gpt-5")


def test_structured_provider_follows_the_model_not_the_keys(settings: Settings) -> None:
	both = _with(
		settings,
		anthropic_api_key=SecretStr("a"),
		gemini_api_key=SecretStr("g"),
	)
	from memo.schemas.people import LatelyWrite

	claude = providers.structured_provider(both, model=CLAUDE, schema=LatelyWrite)
	gemini = providers.structured_provider(both, model=GEMINI, schema=LatelyWrite)
	assert isinstance(claude, AnthropicExtractionProvider)
	assert isinstance(gemini, GeminiCompletionProvider)
	assert (claude.provider, gemini.provider) == ("anthropic", "google")
	# A Claude key does not stand in for a missing Gemini key, or the other way.
	only_anthropic = _with(
		settings, anthropic_api_key=SecretStr("a"), gemini_api_key=None
	)
	assert (
		providers.structured_provider(only_anthropic, model=GEMINI, schema=LatelyWrite)
		is None
	)
	only_gemini = _with(settings, anthropic_api_key=None, gemini_api_key=SecretStr("g"))
	assert (
		providers.structured_provider(only_gemini, model=CLAUDE, schema=LatelyWrite)
		is None
	)


@pytest.mark.parametrize("setting", READERS)
def test_every_reader_routes_by_its_own_model(settings: Settings, setting: str) -> None:
	models = {**{name: CLAUDE for name in READERS}, setting: GEMINI}
	configured = _with(
		settings,
		**models,
		anthropic_api_key=SecretStr("a"),
		gemini_api_key=SecretStr("g"),
	)
	assert READERS[setting](configured).version.endswith(f"/{GEMINI}")
	# Without Google's key that one stage is unconfigured; a Claude key is no help.
	without = _with(configured, gemini_api_key=None)
	assert READERS[setting](without).version.endswith("/unconfigured")


def test_photo_descriptions_follow_the_extraction_model(settings: Settings) -> None:
	configured = _with(settings, extraction_model=GEMINI, gemini_api_key=SecretStr("g"))
	describer = make_describer(configured)
	assert isinstance(describer, PhotoDescriber)
	assert describer.version.endswith(f"/{GEMINI}")


def test_ask_routes_answer_and_rewrite_separately(settings: Settings) -> None:
	configured = _with(
		settings,
		answer_model=GEMINI,
		rewrite_model="claude-haiku-4-5",
		anthropic_api_key=SecretStr("a"),
		gemini_api_key=SecretStr("g"),
	)
	answer, rewrite = make_providers(configured)
	assert isinstance(answer, GeminiAnswerProvider)
	assert isinstance(rewrite, AnthropicTextProvider)
	swapped = _with(configured, answer_model="claude-opus-5", rewrite_model=GEMINI)
	answer, rewrite = make_providers(swapped)
	assert isinstance(answer, AnthropicAnswerProvider)
	assert isinstance(rewrite, GeminiCompletionProvider)
	# One missing key leaves exactly that half unconfigured.
	answer, rewrite = make_providers(_with(swapped, gemini_api_key=None))
	assert isinstance(answer, AnthropicAnswerProvider)
	assert isinstance(rewrite, UnconfiguredProvider)


def test_the_free_tier_never_reads_real_notes(settings: Settings) -> None:
	from memo.schemas.people import LatelyWrite

	live = _with(settings, app_env="production", gemini_api_key=SecretStr("g"))
	with pytest.raises(RuntimeError, match="GEMINI_PAID_TIER"):
		providers.structured_provider(live, model=GEMINI, schema=LatelyWrite)
	with pytest.raises(RuntimeError):
		providers.answer_provider(live, model=GEMINI)
	with pytest.raises(RuntimeError):
		providers.text_provider(live, model=GEMINI)
	paid = _with(live, gemini_paid_tier=True)
	assert (
		providers.structured_provider(paid, model=GEMINI, schema=LatelyWrite)
		is not None
	)
	# The evals run locally against synthetic notes: allowed.
	local = _with(live, app_env="local")
	assert (
		providers.structured_provider(local, model=GEMINI, schema=LatelyWrite)
		is not None
	)
	# Anthropic has no free tier to guard.
	claude = _with(live, anthropic_api_key=SecretStr("a"))
	assert (
		providers.structured_provider(claude, model=CLAUDE, schema=LatelyWrite)
		is not None
	)


def test_in_use_maps_each_company_to_its_jobs(settings: Settings) -> None:
	configured = _with(settings, people_model=GEMINI, lately_model=GEMINI)
	uses = providers.in_use(configured)
	assert uses["google"] == ["who a note is about", "the line on each person"]
	assert "answering your questions" in uses["anthropic"]
	assert len(uses["anthropic"]) + len(uses["google"]) == len(providers.MODEL_SETTINGS)


def test_every_routable_gemini_model_has_a_price() -> None:
	# The probe's live model list, 2026-10-01, text models memo would route to.
	for model in ("gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash"):
		assert cost_usd(model, input_tokens=1_000_000, output_tokens=0) > 0, model
	assert cost_usd(
		"gemini-3.8-flash", input_tokens=1_000_000, output_tokens=1_000_000
	) == Decimal("4.5")
	# Cached input at a tenth, as for Claude.
	assert cost_usd(
		"gemini-3.8-flash", input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000
	) == Decimal("0.075")


class _Scripted:
	model = GEMINI

	async def complete(
		self, *, system: str, messages: list[MessageParam]
	) -> RawCompletion:
		return RawCompletion(
			text='{"line": "x"}',
			model=self.model,
			input_tokens=10,
			output_tokens=5,
			cache_read_tokens=0,
			cache_write_tokens=0,
			latency_ms=1,
			provider="google",
		)


async def test_the_repair_loop_records_who_answered() -> None:
	_, _, calls = await complete_validated(
		_Scripted(),
		system="s",
		first="note",
		parse=str,
		repair=str,
		stage="lately",
		max_repairs=1,
	)
	assert [c.provider for c in calls] == ["google"]
	assert calls[0].cost_usd > 0


def test_the_privacy_page_names_who_reads_what(settings: Settings) -> None:
	claude_only = _with(settings)
	items, training = model_processors(claude_only)
	assert "<b>Anthropic</b>" in items and "Google" not in items
	assert "Anthropic does not train" in training and "Google" not in training

	mixed = _with(settings, people_model=GEMINI, lately_model=GEMINI)
	items, training = model_processors(mixed)
	assert (
		"<b>Google</b> (Gemini): who a note is about and the line on each person."
		in items
	)
	assert "free tier" in training and "reviewers may read it" in training

	paid = _with(mixed, gemini_paid_tier=True)
	_, training = model_processors(paid)
	assert "paid tier" in training and "free tier" not in training

	all_google = _with(settings, **{name: GEMINI for name in providers.MODEL_SETTINGS})
	items, training = model_processors(all_google)
	assert "Anthropic" not in items and "Anthropic" not in training
