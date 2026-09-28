from functools import lru_cache
from uuid import UUID

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# The one user V1 ships with. Migration 0001 inserts this row; get_current_user returns
# it until real auth arrives (ADR 0018). Version nibble 7, variant 8 - a valid uuidv7.
SEED_USER_ID = UUID("00000000-0000-7000-8000-000000000001")


class Settings(BaseSettings):
	"""Application configuration loaded from the environment."""

	model_config = SettingsConfigDict(
		env_file=".env", extra="ignore", env_ignore_empty=True
	)

	app_env: str = "local"
	log_level: str = "INFO"

	# The API and worker connect as the non-owner role so row-level security applies.
	database_url: str
	# Migrations connect as the owner. Never used by request-path code.
	database_admin_url: str
	# Password migration 0001 assigns to the memo_app role.
	app_db_password: SecretStr

	api_token: SecretStr
	current_user_id: UUID = SEED_USER_ID

	# arq's broker (ADR 0012: the product never reads job state from here).
	redis_url: str = "redis://localhost:6379/0"
	arq_queue_name: str = "memo"
	# The pipeline's own cap on retries; arq's max_tries is set well above it.
	max_attempts: int = 5
	# A capture left pending this long with no job is re-enqueued by the sweep.
	stale_pending_minutes: int = 10

	# Object storage for recordings (Railway Bucket in prod, MinIO in compose).
	s3_endpoint: str
	s3_bucket: str
	s3_access_key_id: SecretStr
	s3_secret_access_key: SecretStr
	s3_region: str = "auto"
	s3_addressing_style: str = "auto"
	upload_url_ttl_seconds: int = 900
	unclaimed_recording_ttl_hours: int = 24

	# Deepgram Nova-3 (ADR 0013). Unset means audio captures fail visibly, not silently.
	deepgram_api_key: SecretStr | None = None
	deepgram_model: str = "nova-3"

	# Extraction (ADR 0014/0015). Unset means every capture fails at the extract stage.
	anthropic_api_key: SecretStr | None = None
	extraction_model: str = "claude-opus-5"


@lru_cache
def get_settings() -> Settings:
	return Settings()
