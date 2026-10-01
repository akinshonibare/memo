"""Each reader runs on its own model setting (ADR 0065) and stamps it on its rows."""

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import SecretStr

from memo.config import Settings
from memo.services.faith import make_faith_reader
from memo.services.habits import make_habits_reader
from memo.services.lately import make_lately_writer
from memo.services.mood import make_mood_reader
from memo.services.people import make_people_reader
from memo.services.reconcile import make_reconciler

READERS: dict[str, Callable[[Settings], Any]] = {
	"people_model": make_people_reader,
	"habits_model": make_habits_reader,
	"faith_model": make_faith_reader,
	"reconcile_model": make_reconciler,
	"mood_model": make_mood_reader,
	"lately_model": make_lately_writer,
}


def test_the_defaults_are_the_measured_split() -> None:
	# Changing one of these needs that reader's eval re-run and re-recorded first.
	defaults = {name: Settings.model_fields[name].default for name in READERS}
	assert defaults == {
		"people_model": "claude-sonnet-5",
		"habits_model": "claude-sonnet-5",
		"faith_model": "claude-sonnet-5",
		"reconcile_model": "claude-sonnet-5",
		"mood_model": "claude-opus-5",
		"lately_model": "claude-opus-5",
	}
	assert Settings.model_fields["extraction_model"].default == "claude-opus-5"


@pytest.mark.parametrize("setting", READERS)
def test_a_reader_uses_its_own_setting_and_says_so(
	settings: Settings, setting: str
) -> None:
	# Every other reader is set to something else, so a crossed wire shows.
	others = {name: "claude-other" for name in READERS}
	configured = settings.model_copy(
		update={
			**others,
			setting: "claude-chosen",
			"extraction_model": "claude-other",
			"anthropic_api_key": SecretStr("test-key"),
		}
	)
	reader = READERS[setting](configured)
	assert reader.version.endswith("/claude-chosen")
