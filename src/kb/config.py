from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    aws_profile: str | None = None
    aws_region: str = "eu-central-1"
    bedrock_model_id: str = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
    database_url: str = "sqlite:///data/kb.db"
    opensanctions_api_key: str | None = None
    http_timeout: float = 20.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
