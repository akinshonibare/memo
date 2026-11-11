"""Lets the tests run with whichever services this checkout has.

Importing the app builds its settings, so with no .env the suite could not even be
collected. Outside CI, which must always run everything against real services:

- Nothing configured: every required setting gets a placeholder. With the `pgserver`
  dev dependency installed, tests/conftest.py starts an embedded Postgres (with
  pgvector) for the session, and Redis and the object store are fakes: only the tests
  that need the real Redis or object store are skipped. Without `pgserver`, every test
  that needs a service is skipped.
- Only Postgres configured (DATABASE_URL, DATABASE_ADMIN_URL and APP_DB_PASSWORD): the
  object store's settings get placeholders, and Redis and the store are fakes, as above.

Any other partial configuration fails loudly, as it always has.
"""

import os
from importlib.util import find_spec

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
STORE = {"S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"}


def _missing_settings() -> set[str]:
	"""The required settings this environment leaves unset, as variable names."""
	try:
		Settings()
	except ValidationError as exc:
		return {str(error["loc"][0]).upper() for error in exc.errors()}
	return set()


if not os.environ.get("CI"):
	missing = _missing_settings()
	required = {n.upper() for n, f in Settings.model_fields.items() if f.is_required()}
	if missing == required:
		os.environ.update(PLACEHOLDERS)
		if find_spec("pgserver") is not None:
			os.environ["MEMO_TESTS_EMBEDDED_POSTGRES"] = "1"
			os.environ["MEMO_TESTS_DATABASE_ONLY"] = "1"
		else:
			os.environ["MEMO_TESTS_WITHOUT_SERVICES"] = "1"
	elif missing == STORE:
		os.environ.update({name: PLACEHOLDERS[name] for name in STORE})
		os.environ["MEMO_TESTS_DATABASE_ONLY"] = "1"
