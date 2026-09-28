from functools import lru_cache
from uuid import UUID

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# The one user V1 ships with. Migration 0001 inserts this row; get_current_user returns
# it until real auth arrives (ADR 0018). Version nibble 7, variant 8 - a valid uuidv7.
SEED_USER_ID = UUID("00000000-0000-7000-8000-000000000001")


class Settings(BaseSettings):
	"""Application configuration loaded from the environment."""

	model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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


@lru_cache
def get_settings() -> Settings:
	return Settings()
