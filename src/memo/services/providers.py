"""Which provider a model name belongs to, and the one place that builds a client
for it (ADR 0067).

A stage's model setting names its provider by prefix (`claude-opus-5`,
`gemini-3.8-flash`), so routing a reader to another company is the same one-line
change as routing it to another model (ADR 0065), and every row still records the
provider and model that read it.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from memo.config import Settings
from memo.services.extraction import AnthropicExtractionProvider, ExtractionProvider
from memo.services.gemini import (
	GeminiAnswerProvider,
	GeminiClient,
	GeminiCompletionProvider,
)

if TYPE_CHECKING:
	from memo.services.ask import AnswerProvider, TextProvider


@dataclass(frozen=True)
class ProviderSpec:
	name: str  # what `llm_calls.provider` records
	env: str  # the key's setting, as the operator sees it
	prefixes: tuple[str, ...]


ANTHROPIC = ProviderSpec("anthropic", "ANTHROPIC_API_KEY", ("claude-",))
GOOGLE = ProviderSpec("google", "GEMINI_API_KEY", ("gemini-", "gemma-"))
SPECS = (ANTHROPIC, GOOGLE)

# Every setting that names a model, with what that model does for the person, so the
# privacy page can say who processes what from the configuration rather than from a
# sentence someone has to remember to update.
MODEL_SETTINGS: dict[str, str] = {
	"extraction_model": "pulling items out of notes and describing photos",
	"people_model": "who a note is about",
	"habits_model": "habits",
	"faith_model": "prayers",
	"mood_model": "mood",
	"reconcile_model": "which earlier item a later note answers or carries on",
	"lately_model": "the line on each person",
	"answer_model": "answering your questions",
	"rewrite_model": "rephrasing a follow-up question for search",
}


def provider_for(model: str) -> ProviderSpec:
	for spec in SPECS:
		if model.startswith(spec.prefixes):
			return spec
	known = ", ".join(p for spec in SPECS for p in spec.prefixes)
	raise ValueError(
		f"no provider for model {model!r}: a model setting starts with one of {known}"
	)


def api_key(settings: Settings, spec: ProviderSpec) -> str | None:
	secret = (
		settings.anthropic_api_key if spec is ANTHROPIC else settings.gemini_api_key
	)
	return None if secret is None else secret.get_secret_value()


def in_use(settings: Settings) -> dict[str, list[str]]:
	"""Provider name -> what it does for the person, from the model settings."""
	uses: dict[str, list[str]] = {}
	for setting, job in MODEL_SETTINGS.items():
		spec = provider_for(getattr(settings, setting))
		uses.setdefault(spec.name, []).append(job)
	return uses


def _guard_free_tier(settings: Settings, model: str) -> None:
	"""Google's free tier is for the evals' synthetic notes, never a person's. Its
	terms say not to send personal information, and Google trains on it. A real
	deployment refuses to start a Gemini-routed stage until billing is confirmed."""
	if settings.app_env in ("local", "test") or settings.gemini_paid_tier:
		return
	raise RuntimeError(
		f"{model} is configured for a real deployment without GEMINI_PAID_TIER=true. "
		"Google's free tier uses what it is sent to improve Google's products and its "
		"terms exclude personal information. Link billing in AI Studio and set "
		"GEMINI_PAID_TIER=true, or set this stage back to a Claude model."
	)


def _gemini(settings: Settings, key: str, model: str) -> GeminiClient:
	_guard_free_tier(settings, model)
	return GeminiClient(key)


def structured_provider(
	settings: Settings, *, model: str, schema: type[BaseModel]
) -> ExtractionProvider | None:
	"""A provider constrained to `schema` for `model`, or None when its key is unset
	(the caller returns its Unconfigured stand-in, so the stage fails visibly)."""
	spec = provider_for(model)
	key = api_key(settings, spec)
	if key is None:
		return None
	if spec is GOOGLE:
		return GeminiCompletionProvider(
			_gemini(settings, key, model),
			model=model,
			schema=schema,
			thinking_level=settings.gemini_thinking_level,
		)
	return AnthropicExtractionProvider(
		AsyncAnthropic(api_key=key, max_retries=0), model=model, schema=schema
	)


def text_provider(settings: Settings, *, model: str) -> "TextProvider | None":
	"""A short plain-text completion (Ask's rewrite)."""
	from memo.services.ask import AnthropicTextProvider

	spec = provider_for(model)
	key = api_key(settings, spec)
	if key is None:
		return None
	if spec is GOOGLE:
		# An easy job on the time-to-first-token path: no deliberation, as the
		# Anthropic rewrite has none.
		return GeminiCompletionProvider(
			_gemini(settings, key, model),
			model=model,
			thinking_level="minimal",
			max_output_tokens=300,
		)
	return AnthropicTextProvider(
		AsyncAnthropic(api_key=key, max_retries=0), model=model
	)


def answer_provider(settings: Settings, *, model: str) -> "AnswerProvider | None":
	"""A streamed answer (Ask)."""
	from memo.services.ask import AnthropicAnswerProvider

	spec = provider_for(model)
	key = api_key(settings, spec)
	if key is None:
		return None
	if spec is GOOGLE:
		return GeminiAnswerProvider(
			_gemini(settings, key, model),
			model=model,
			thinking_level=settings.gemini_thinking_level,
		)
	return AnthropicAnswerProvider(
		AsyncAnthropic(api_key=key, max_retries=0), model=model
	)
