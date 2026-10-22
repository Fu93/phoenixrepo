from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Meterless Nightshift is the primary model provider."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    zetaris_rest_url: str = "http://localhost:8888/api/v1.0"
    zetaris_org_id: str = "1"
    zetaris_api_key: str | None = None
    zetaris_timeout_seconds: float = 30

    meterless_base_url: str = "http://127.0.0.1:8088/v1"
    meterless_runtime_token: str | None = None
    meterless_model: str = "meterless/auto"
    meterless_app: str = "phoenixrepo"
    meterless_timeout_seconds: float = 180

    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = ""
    nvidia_timeout_seconds: float = 90

    llm_provider: str = "meterless"
    sample_mode: bool = True
    log_level: str = "INFO"
    port: int = 8000
    log_dir: str = "logs"
    hmem_store_path: str = "data/hmem/runtime-store.json"
    agent_step_timeout_seconds: float = 120
    run_timeout_seconds: float = 600

    def secret_values(self) -> list[str]:
        values = [
            self.zetaris_api_key,
            self.meterless_runtime_token,
            self.nvidia_api_key,
        ]
        return [value for value in values if value]
