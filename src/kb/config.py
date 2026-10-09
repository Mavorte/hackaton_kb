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
    # underwriting: rychla cesta klasifikace z znalostni baze (kNN)
    uw_kb_min_sim: float = 0.75
    uw_kb_min_agree: float = 0.8
    uw_kb_k: int = 5
    uw_cases_dir: str = "data/uw"  # MCP smi cist pripady jen odtud
    # vizualni cesta (skeny): levny model na hlavicku/segmentaci/prvni pokus, silnejsi na eskalaci
    uw_model_fast: str = "eu.anthropic.claude-haiku-5-5"
    uw_model_strong: str = "eu.anthropic.claude-sonnet-5-5"
    uw_model_hard: str = "eu.anthropic.claude-opus-5-5"
    uw_scan_dpi: int = 110
    uw_escalate_dpi: int = 160
    uw_min_conf: float = 0.75
    embed_provider: str = ""  # prazdne = podle llm_provider; "mock" vynuti mock embeddingy


@lru_cache
def get_settings() -> Settings:
    return Settings()
