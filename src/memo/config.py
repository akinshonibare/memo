from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
	"""Application configuration loaded from the environment."""

	model_config = SettingsConfigDict(env_file=".env", extra="ignore")

	database_url: str
	app_env: str = "local"

@lru_cache
def get_settings() -> Settings:
	return Settings()  # type: ignore[call-arg]
  