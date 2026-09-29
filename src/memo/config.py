from functools import lru_cache
from uuid import UUID

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# The user migration 0001 seeds. The first Sign in with Apple claims it (ADR 0019), so
# data captured before auth existed stays with its owner. Version nibble 7, variant 8.
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

	# Sign in with Apple (ADR 0019): the identity token's audience is the app's bundle
	# id. Sessions the server issues in exchange live this long.
	apple_bundle_id: str = "com.akinshonibare.memo"
	session_ttl_days: int = 90
	# The first Apple sign-in ever attaches to the seeded user rather than creating a
	# new one, so pre-auth captures keep their owner. Off once that has happened.
	claim_seed_user_on_first_login: bool = True

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
	tts_model: str = "aura-2-thalia-en"
	# Shown on the public privacy page; unset shows TestFlight feedback instead.
	support_email: str | None = None
	# Voyage trains on API inputs unless the account opts out, which is a toggle in
	# Voyage's dashboard, not a request parameter. Set true only once it is flipped:
	# the privacy page claims no-training for Voyage exactly when this is true.
	voyage_training_opted_out: bool = False

	# Extraction (ADR 0014/0015). Unset means every capture fails at the extract stage.
	anthropic_api_key: SecretStr | None = None
	extraction_model: str = "claude-opus-5"

	# Embeddings (ADR 0016). Changing the model or dim is a full re-embed.
	voyage_api_key: SecretStr | None = None
	# Keys minted in the Voyage dashboard (`pa-…`) work against api.voyageai.com; keys
	# minted in MongoDB Atlas (`al-…`) only work against ai.mongodb.com. Same request
	# and response shape either way.
	voyage_api_url: str = "https://api.voyageai.com/v1/embeddings"
	embedding_model: str = "voyage-4"
	embedding_dim: int = 1024
	hnsw_ef_search: int = 100

	# Ask. The rewrite is an easy task on the TTFT critical path, so a small model.
	answer_model: str = "claude-opus-5"
	rewrite_model: str = "claude-haiku-4-5"
	ask_top_k: int = 8
	ask_history_turns: int = 6

	# Per-user, per-minute (issue #48). Ask fails closed, capture fails open.
	rate_ask_per_minute: int = 10
	rate_capture_per_minute: int = 60
	rate_voice_per_minute: int = 90
	rate_search_per_minute: int = 60
	# Cosine distance under which two notes count as related. A starting guess for
	# voyage-4 (ADR 0028), not a measured one: calibrate on real notes.
	related_max_distance: float = 0.45


@lru_cache
def get_settings() -> Settings:
	return Settings()
