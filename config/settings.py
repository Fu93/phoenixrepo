from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    xai_api_key: str | None = None
    xai_base_url: str = "https://api.x.ai/v1"
    xai_model: str = "grok-4.7"
    zetaris_api_key: str | None = None
    zetaris_mcp_url: str | None = None
    meterless_api_key: str | None = None
    sample_mode: bool = True
    log_level: str = "INFO"
    port: int = 8000
    log_dir: str = "logs"
