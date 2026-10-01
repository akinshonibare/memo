"""A live provider for an eval run, from the same factory the services use, so an
eval measures exactly the client production would run (ADR 0067)."""

from pydantic import BaseModel

from memo.config import get_settings
from memo.services.extraction import ExtractionProvider
from memo.services.providers import provider_for, structured_provider


def live_provider(model: str, schema: type[BaseModel]) -> ExtractionProvider | None:
	"""The provider for `model`, or None (with a message) when its key is unset."""
	provider = structured_provider(get_settings(), model=model, schema=schema)
	if provider is None:
		print(f"{provider_for(model).env} is unset; use --replay for an offline run")
	return provider
