from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    aws_profile: str | None = None
    aws_region: str = "eu-north-1"
    bedrock_model_id: str = "eu.anthropic.claude-sonnet-5-5"
    database_url: str = "sqlite:///data/kb.db"
    opensanctions_api_key: str | None = None
    http_timeout: float = 20.0
    # offline vyvoj: LLM_PROVIDER=mock (misto Bedrocku), OFFLINE=1 (ukazkova data misto ARES/VIES)
    llm_provider: str = "bedrock"
    offline: bool = False
    samples_dir: str = "data/samples/ares"
    embed_model_id: str = "amazon.titan-embed-text-v2:0"
    embed_dim: int = 512


@lru_cache
def get_settings() -> Settings:
    return Settings()
