from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Documented stand-ins. A copied .env.example must not look configured.
_PLACEHOLDER_SECRETS = {
    "changeme",
    "placeholder",
    "your-key-here",
    "your-nvidia-key",
    "your-nightshift-runtime-token",
    "your-xai-key-here",
    "your-zetaris-api-key",
}


def usable_secret(value: str | None) -> str | None:
    """Return a secret the process can send, or None for blank and placeholder text.

    ``rt-local`` is left intact so the Meterless adapter can reject it by name.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in _PLACEHOLDER_SECRETS or lowered.startswith("your-"):
        return None
    return text


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
    model_override: str = Field(default="", validation_alias=AliasChoices("MODEL_NAME"))
    meterless_max_tokens: int = 8192
    meterless_app: str = "phoenixrepo"
    meterless_timeout_seconds: float = 180
    meterless_lanes: str = "nvidia/nemotron-3-super-120b-a12b,meterless/auto"

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
    agent_step_timeout_seconds: float = 190
    markovian_max_chunks: int = 24
    run_timeout_seconds: float = 600

    def meterless_lanes_list(self) -> tuple[str, ...]:
        lanes = tuple(part.strip() for part in (self.meterless_lanes or "").split(",") if part.strip())
        return lanes or ("nvidia/nemotron-3-super-120b-a12b", "meterless/auto")

    def chat_timeout_seconds(self) -> float:
        """Chat client budget stays inside the chunk budget so a late reply is not then failed."""

        step = float(self.agent_step_timeout_seconds)
        chat = float(self.meterless_timeout_seconds)
        return min(chat, max(1.0, step - 1.0))

    @field_validator("zetaris_api_key", "meterless_runtime_token", "nvidia_api_key", mode="before")
    @classmethod
    def _drop_placeholder_secrets(cls, value: object) -> str | None:
        if value is None:
            return None
        return usable_secret(str(value))

    def requested_model(self) -> str:
        """Nightshift model id to send. MODEL_NAME overrides METERLESS_MODEL."""

        override = (self.model_override or "").strip()
        if override:
            return override
        configured = (self.meterless_model or "").strip()
        return configured or "meterless/auto"

    def secret_values(self) -> list[str]:
        values = [
            self.zetaris_api_key,
            self.meterless_runtime_token,
            self.nvidia_api_key,
        ]
        return [value for value in values if value]
