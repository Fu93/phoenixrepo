import os

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Gate-1 verdict calls ask Nightshift for this id unless MODEL_NAME is set.
# Nightshift may still answer with a different id. That id is served_model, never rewritten.
DEFAULT_NIGHTSHIFT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
NEMOTRON_FAMILY_PREFIX = "nvidia/nemotron-"
SAMPLE_STANDIN_MODEL = "deterministic-rule-standin"

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


def is_nemotron_family(model: object) -> bool:
    """True only for an nvidia/nemotron-* id. A different Nemotron size still matches."""

    return isinstance(model, str) and model.strip().startswith(NEMOTRON_FAMILY_PREFIX)


def model_mismatch(served: object) -> bool:
    """True when a model reply is missing or not in the nvidia/nemotron-* family.

    An empty served model is a mismatch. The requested id is never copied into it.
    The sample stand-in names itself and is not a Nightshift reply, so it is not a
    routing mismatch.
    """

    if not isinstance(served, str) or not served.strip():
        return True
    text = served.strip()
    if text == SAMPLE_STANDIN_MODEL:
        return False
    return not is_nemotron_family(text)


class Settings(BaseSettings):
    """Runtime settings. Meterless Nightshift is the primary model provider."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    zetaris_rest_url: str = "http://localhost:8888/api/v1.0"
    zetaris_org_id: str = "1"
    zetaris_api_key: str | None = None
    zetaris_timeout_seconds: float = 30

    meterless_base_url: str = "http://127.0.0.1:8088/v1"
    meterless_runtime_token: str | None = None
    meterless_model: str = DEFAULT_NIGHTSHIFT_MODEL
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
        default_lanes = lanes or ("nvidia/nemotron-3-super-120b-a12b", "meterless/auto")
        override = (self.model_override or "").strip()
        if override:
            return (override, *(l for l in default_lanes if l != override))
        return default_lanes

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
        """Nightshift model id to send. MODEL_NAME overrides METERLESS_MODEL.

        With neither set, gate-1 and the revive loop request the Nemotron lane.
        """

        override = (self.model_override or "").strip()
        if override:
            return override
        configured = (self.meterless_model or "").strip()
        return configured or DEFAULT_NIGHTSHIFT_MODEL

    def secret_values(self) -> list[str]:
        values = [
            self.zetaris_api_key,
            self.meterless_runtime_token,
            self.nvidia_api_key,
        ]
        return [value for value in values if value]


ENV_TOKEN_NAMES = ("GITHUB_TOKEN", "GH_TOKEN")


def env_token_values() -> list[str]:
    """GitHub tokens the tools read straight from the process environment (not from Settings)."""
    return [value for value in (usable_secret(os.environ.get(name)) for name in ENV_TOKEN_NAMES) if value]


def trace_secrets(settings: "Settings | None" = None) -> list[str]:
    """Every value a trace must redact: the Settings credentials plus the env GitHub tokens."""
    values: list[str] = []
    try:
        values = (settings or Settings()).secret_values()
    except Exception:  # noqa: BLE001 — a malformed .env must not stop redaction of the env token
        values = []
    return [*values, *(value for value in env_token_values() if value not in values)]
