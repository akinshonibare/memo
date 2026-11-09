"""Lets the tests that need no services run in a checkout with nothing configured.

Importing the app builds its settings, so with no .env the suite could not even be
collected. When every required setting is absent, and this is not CI (which must
always run everything), fill them with placeholders nothing listens on and tell
tests/conftest.py to skip the tests that need Postgres, Redis or the object store.
"""

import os

from pydantic import ValidationError

from memo.config import Settings

PLACEHOLDERS = {
	"DATABASE_URL": "postgresql+asyncpg://unset@127.0.0.1:1/unset",
	"DATABASE_ADMIN_URL": "postgresql+asyncpg://unset@127.0.0.1:1/unset",
	"APP_DB_PASSWORD": "unset",
	"S3_ENDPOINT": "http://127.0.0.1:1",
	"S3_BUCKET": "unset",
	"S3_ACCESS_KEY_ID": "unset",
	"S3_SECRET_ACCESS_KEY": "unset",
}


def _nothing_configured() -> bool:
	try:
		Settings()
	except ValidationError as exc:
		missing = {error["loc"][0] for error in exc.errors()}
		required = {n for n, f in Settings.model_fields.items() if f.is_required()}
		return missing == required
	return False


if not os.environ.get("CI") and _nothing_configured():
	os.environ.update(PLACEHOLDERS)
	os.environ["MEMO_TESTS_WITHOUT_SERVICES"] = "1"
